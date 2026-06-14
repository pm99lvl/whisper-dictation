#!/usr/bin/env python3
"""
Whisper Dictation Daemon

Persistent local daemon for macOS dictation:
- keeps the MLX Whisper model loaded in memory;
- accepts start/stop commands over a Unix socket;
- writes recognized text to /tmp for Hammerspoon to paste.
"""

from __future__ import annotations

import os
import json
import queue
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path

from dictation_runtime import RuntimeState, cleanup_stale_files, is_process_alive, write_json_atomic

os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

MODEL = "mlx-community/whisper-small-mlx"
SAMPLE_RATE = 16000
MAX_SECONDS = 60          # safety guard for a stuck hotkey
SPEECH_THRESHOLD = 0.0008
SILENCE_AFTER = 10.0      # allows natural pauses; release Alt for instant stop
MIN_RECORD_TIME = 1.0
BLOCK_SECONDS = 0.1
EDGE_SILENCE_KEEP_SECONDS = 0.2
INNER_SILENCE_KEEP_SECONDS = 0.6

SOCKET_PATH = Path("/tmp/whisper_daemon.sock")
STATE_FILE = Path("/tmp/whisper_dictation_state")
PID_FILE = Path("/tmp/whisper_daemon.pid")
RESULT_FILE = Path("/tmp/whisper_result.txt")
TRIGGER_FILE = Path("/tmp/whisper_paste.trigger")
STATUS_FILE = Path("/tmp/whisper_status.json")

if PID_FILE.exists():
    existing_pid = PID_FILE.read_text().strip()
    if existing_pid and existing_pid != str(os.getpid()) and is_process_alive(existing_pid):
        print(f"🟡 Existing daemon already alive (pid={existing_pid}); exiting duplicate before model load", flush=True)
        sys.exit(0)

removed_startup_artifacts = cleanup_stale_files(
    socket_path=SOCKET_PATH,
    state_path=STATE_FILE,
    pid_path=PID_FILE,
    result_path=RESULT_FILE,
    trigger_path=TRIGGER_FILE,
    glob_root=Path("/tmp"),
)
if removed_startup_artifacts:
    print(f"🧹 Removed stale artifacts: {removed_startup_artifacts}", flush=True)

# Write PID before model preload so Hammerspoon does not kill us while loading.
PID_FILE.write_text(str(os.getpid()))

import numpy as np
import scipy.io.wavfile as wf
import sounddevice as sd
from deep_translator import GoogleTranslator
import mlx_whisper

# ── Preload model once ──────────────────────────────────────────────
print(f"⏳ Loading model: {MODEL}", flush=True)
_warmup = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
_warmup.close()
try:
    wf.write(_warmup.name, SAMPLE_RATE, np.zeros(SAMPLE_RATE, dtype=np.int16))
    mlx_whisper.transcribe(_warmup.name, path_or_hf_repo=MODEL, language=None, word_timestamps=False)
finally:
    with suppress(FileNotFoundError):
        os.unlink(_warmup.name)
print("✅ Model ready.", flush=True)

# ── State ───────────────────────────────────────────────────────────
_recording = False
_transcribing = False
_stop_event = threading.Event()
_lock = threading.Lock()
_runtime_state = RuntimeState()
_transcription_queue: queue.Queue[tuple[str, np.ndarray, bool]] = queue.Queue()
_translator_ru_en: GoogleTranslator | None = None
_input_device: int | None = None
_input_device_name: str | None = None

# Voice punctuation commands → symbols.
PUNCT_COMMANDS = [
    (re.compile(r"(?i)\bвопросительный знак\b"), "?"),
    (re.compile(r"(?i)\bвосклицательный знак\b"), "!"),
    (re.compile(r"(?i)\bточка с запятой\b"), ";"),
    (re.compile(r"(?i)\bдвоеточие\b"), ":"),
    (re.compile(r"(?i)\bмноготочие\b"), "…"),
    (re.compile(r"(?i)\bновая строка\b"), "\n"),
    (re.compile(r"(?i)\bновый абзац\b"), "\n\n"),
]
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([?!;:…])")
DUPLICATE_TERMINATOR_RE = re.compile(r"[.,]\s*([?!])")


def log_timing(label: str, start: float) -> None:
    print(f"⏱ {label}: {time.perf_counter() - start:.2f}s", flush=True)


def emit_event(event: str, *, session_id: str | None = None, **payload) -> None:
    fields = {
        "event": event,
        "pid": os.getpid(),
        "session_id": session_id or _runtime_state.session_id,
        "state": _runtime_state.state,
        "queue_size": _transcription_queue.qsize(),
        **payload,
    }
    parts = [f"{key}={value}" for key, value in fields.items() if value is not None]
    print("🧾 " + " ".join(parts), flush=True)
    write_status()


def status_payload() -> dict:
    snapshot = _runtime_state.snapshot()
    snapshot.update(
        {
            "ok": True,
            "pid": os.getpid(),
            "model": MODEL,
            "recording": _recording,
            "transcribing": _transcribing,
            "queue_size": _transcription_queue.qsize(),
            "socket_path": str(SOCKET_PATH),
            "status_file": str(STATUS_FILE),
        }
    )
    return snapshot


def write_status() -> None:
    with suppress(Exception):
        write_json_atomic(STATUS_FILE, status_payload())


def notify(title: str, msg: str) -> None:
    subprocess.run(
        ["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
        capture_output=True,
        check=False,
    )


def get_translator() -> GoogleTranslator:
    """Create the translator once; constructing it per request adds avoidable latency."""
    global _translator_ru_en
    if _translator_ru_en is None:
        _translator_ru_en = GoogleTranslator(source="ru", target="en")
    return _translator_ru_en


def find_input_device() -> tuple[int | None, str | None]:
    """Prefer the built-in MacBook microphone; cache result for subsequent recordings."""
    global _input_device, _input_device_name
    if _input_device_name is not None:
        return _input_device, _input_device_name

    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and "MacBook" in device["name"]:
            _input_device = index
            _input_device_name = str(device["name"])
            return _input_device, _input_device_name

    _input_device = None
    _input_device_name = "default input"
    return _input_device, _input_device_name


def apply_punct_commands(text: str) -> str:
    for pattern, replacement in PUNCT_COMMANDS:
        text = pattern.sub(replacement, text)
    text = SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = DUPLICATE_TERMINATOR_RE.sub(r"\1", text)
    return text.strip()


def is_hallucination_loop(text: str) -> bool:
    words = text.split()
    return len(words) > 10 and len(set(words)) / len(words) < 0.2


def write_result_for_hammerspoon(text: str) -> None:
    """Write result atomically, then touch trigger so Hammerspoon never reads a partial file."""
    tmp_result = RESULT_FILE.with_suffix(".txt.tmp")
    tmp_trigger = TRIGGER_FILE.with_suffix(".trigger.tmp")
    tmp_result.write_text(text, encoding="utf-8")
    os.replace(tmp_result, RESULT_FILE)
    tmp_trigger.write_text("paste")
    os.replace(tmp_trigger, TRIGGER_FILE)


def compact_silence(audio: np.ndarray) -> np.ndarray:
    """Remove silence that helps recording UX but slows Whisper.

    Users can hold Alt and think for up to SILENCE_AFTER seconds. That is good UX,
    but sending those silent spans to Whisper is wasted work. Keep speech chunks and
    short silence padding so phrases remain separated, but drop long empty stretches.
    """
    chunk_size = int(SAMPLE_RATE * BLOCK_SECONDS)
    if len(audio) < chunk_size:
        return audio

    chunks = [audio[i : i + chunk_size] for i in range(0, len(audio), chunk_size)]
    rms_values = [float(np.sqrt(np.mean(chunk**2))) if len(chunk) else 0.0 for chunk in chunks]
    speech_indexes = [i for i, rms in enumerate(rms_values) if rms >= SPEECH_THRESHOLD]
    if not speech_indexes:
        return audio

    edge_keep = max(1, int(EDGE_SILENCE_KEEP_SECONDS / BLOCK_SECONDS))
    inner_keep = max(edge_keep, int(INNER_SILENCE_KEEP_SECONDS / BLOCK_SECONDS))
    first_speech = max(0, speech_indexes[0] - edge_keep)
    last_speech = min(len(chunks) - 1, speech_indexes[-1] + edge_keep)

    kept: list[np.ndarray] = []
    silence_run: list[np.ndarray] = []
    seen_speech = False

    for idx in range(first_speech, last_speech + 1):
        chunk = chunks[idx]
        if rms_values[idx] >= SPEECH_THRESHOLD:
            if silence_run:
                kept.extend(silence_run[:inner_keep])
                silence_run = []
            kept.append(chunk)
            seen_speech = True
        elif seen_speech:
            silence_run.append(chunk)
        else:
            kept.append(chunk)

    if silence_run:
        kept.extend(silence_run[:edge_keep])

    compacted = np.concatenate(kept).flatten() if kept else audio
    original_s = len(audio) / SAMPLE_RATE
    compacted_s = len(compacted) / SAMPLE_RATE
    if original_s - compacted_s >= 0.2:
        print(f"✂️  audio_compact: {original_s:.2f}s → {compacted_s:.2f}s", flush=True)
    else:
        print(f"✂️  audio_compact: {original_s:.2f}s unchanged", flush=True)
    return compacted


def transcribe_and_paste(session_id: str, audio: np.ndarray, translate: bool = False) -> None:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        audio = compact_silence(audio)
        wf.write(tmp.name, SAMPLE_RATE, (audio * 32767).astype(np.int16))

        if translate:
            _runtime_state.mark_transcribing()
            emit_event("transcribe_start", session_id=session_id, mode="translate")
            print("⚙️  Transcribing (ru)...", flush=True)
            started = time.perf_counter()
            result = mlx_whisper.transcribe(
                tmp.name,
                path_or_hf_repo=MODEL,
                language="ru",
                word_timestamps=False,
                condition_on_previous_text=False,
            )
            log_timing("transcribe_ru", started)
            text = result["text"].strip()
            if text:
                print(f"   RU: {text}", flush=True)
                _runtime_state.mark_translating()
                emit_event("translate_start", session_id=session_id)
                started = time.perf_counter()
                text = get_translator().translate(text)
                log_timing("translate_ru_en", started)
                print(f"⚙️  → EN: {text}", flush=True)
        else:
            _runtime_state.mark_transcribing()
            emit_event("transcribe_start", session_id=session_id, mode="dictation")
            print("⚙️  Transcribing...", flush=True)
            started = time.perf_counter()
            result = mlx_whisper.transcribe(
                tmp.name,
                path_or_hf_repo=MODEL,
                language=None,
                word_timestamps=False,
                condition_on_previous_text=False,
            )
            log_timing("transcribe", started)
            text = result["text"].strip()

        if not text:
            notify("Whisper", "Текст не распознан")
            return
        if is_hallucination_loop(text):
            print(f"⚠️  Hallucination loop detected ({len(text.split())} words), discarding", flush=True)
            return

        text = apply_punct_commands(text)
        print(f"✅ {text}", flush=True)
        started = time.perf_counter()
        _runtime_state.mark_ready_to_paste()
        write_result_for_hammerspoon(text)
        log_timing("handoff_to_hammerspoon", started)
        emit_event("result_ready", session_id=session_id, chars=len(text))
    finally:
        with suppress(FileNotFoundError):
            os.unlink(tmp.name)


def enqueue_transcription(session_id: str, audio: np.ndarray, translate: bool) -> None:
    _transcription_queue.put((session_id, audio, translate))
    _runtime_state.mark_queued(_transcription_queue.qsize())
    print(f"📥 Queued transcription (session={session_id}, translate={translate}, queue={_transcription_queue.qsize()})", flush=True)
    emit_event("queued", session_id=session_id, translate=translate)


def transcription_worker() -> None:
    global _transcribing
    while True:
        session_id, audio, translate = _transcription_queue.get()
        with _lock:
            _transcribing = True
        started = time.perf_counter()
        try:
            transcribe_and_paste(session_id, audio, translate=translate)
        except Exception as exc:
            _runtime_state.mark_error(str(exc))
            emit_event("transcription_error", session_id=session_id, error=exc)
            print(f"⚠️  Transcription job failed: {exc}", flush=True)
            notify("Whisper", f"Ошибка транскрипции: {exc}")
        finally:
            log_timing("transcription_job_total", started)
            _transcription_queue.task_done()
            with _lock:
                _transcribing = not _transcription_queue.empty()
                recording_now = _recording
            if not _transcribing and not recording_now:
                _runtime_state.mark_done()
            write_status()


def record_thread(session_id: str, translate: bool = False) -> None:
    global _recording
    print(f"🎙 Recording... (session={session_id}, translate={translate})", flush=True)
    STATE_FILE.write_text(session_id)
    emit_event("recording_started", session_id=session_id, translate=translate)

    frames: list[np.ndarray] = []
    chunk = int(SAMPLE_RATE * BLOCK_SECONDS)
    speech_detected = False
    silent_chunks = 0
    elapsed = 0.0
    max_rms = 0.0

    device, device_name = find_input_device()
    print(f"🎤 Using: {device_name}", flush=True)

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=chunk,
            latency="high",
            device=device,
        ) as stream:
            for _ in range(int(MAX_SECONDS / BLOCK_SECONDS)):
                if _stop_event.is_set():
                    print("⏹  Stop by hotkey", flush=True)
                    break
                data, _ = stream.read(chunk)
                frames.append(data.copy())
                elapsed += BLOCK_SECONDS
                rms = float(np.sqrt(np.mean(data**2)))
                max_rms = max(max_rms, rms)
                if rms >= SPEECH_THRESHOLD:
                    speech_detected = True
                    silent_chunks = 0
                elif speech_detected and elapsed >= MIN_RECORD_TIME:
                    silent_chunks += 1
                    if silent_chunks >= int(SILENCE_AFTER / BLOCK_SECONDS):
                        print("🔇 Silence stop", flush=True)
                        break
    except Exception as exc:
        _runtime_state.mark_error(str(exc))
        emit_event("input_error", session_id=session_id, error=exc)
        print(f"⚠️  InputStream error: {exc}", flush=True)
        notify("Whisper", f"Ошибка микрофона: {exc}")
    finally:
        with suppress(FileNotFoundError):
            STATE_FILE.unlink()
        with _lock:
            _recording = False

    if not frames:
        emit_event("recording_empty", session_id=session_id)
        return

    if len(frames) < 5:
        emit_event("recording_too_short", session_id=session_id, frames=len(frames))
        print("⚠️  Too short", flush=True)
        return

    audio = np.concatenate(frames).flatten()
    if not speech_detected:
        emit_event("no_speech", session_id=session_id, max_rms=f"{max_rms:.4f}")
        print(f"⚠️  No speech (max RMS={max_rms:.4f}, threshold={SPEECH_THRESHOLD})", flush=True)
        notify("Whisper", "Речь не обнаружена")
        return

    enqueue_transcription(session_id, audio, translate=translate)


def handle_start(translate: bool = False) -> None:
    global _recording
    mode = "translate" if translate else "dictation"
    with _lock:
        if _recording:
            print("Already recording — ignoring start", flush=True)
            emit_event("start_rejected", reason="already_recording", mode=mode)
            return
        if _transcribing:
            print("Still transcribing — recording next phrase concurrently", flush=True)
            emit_event("start_during_transcription", mode=mode)
        session_id = _runtime_state.start_recording(mode=mode)
        _recording = True
        _stop_event.clear()
    threading.Thread(target=record_thread, args=(session_id, translate), daemon=True).start()


def handle_stop() -> None:
    _stop_event.set()
    _runtime_state.stop_recording()
    with suppress(FileNotFoundError):
        STATE_FILE.unlink()
    print("⏹  Stop requested", flush=True)
    emit_event("stop_requested")


def cleanup(sig=None, frame=None) -> None:  # noqa: ARG001 - signal handler signature
    emit_event("shutdown")
    for path in (SOCKET_PATH, PID_FILE, STATE_FILE):
        with suppress(FileNotFoundError):
            path.unlink()
    sys.exit(0)


def serve() -> None:
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    with suppress(FileNotFoundError):
        SOCKET_PATH.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    server.listen(5)
    os.chmod(SOCKET_PATH, 0o600)
    PID_FILE.write_text(str(os.getpid()))
    threading.Thread(target=transcription_worker, daemon=True).start()
    write_status()

    print(f"🟢 Daemon listening on {SOCKET_PATH}", flush=True)
    emit_event("ready")
    while True:
        try:
            conn, _ = server.accept()
            try:
                cmd = conn.recv(32).decode().strip()
                print(f"CMD: {cmd}", flush=True)
                response = "OK\n"
                if cmd == "start":
                    handle_start(translate=False)
                elif cmd == "stop":
                    handle_stop()
                elif cmd == "start_translate":
                    handle_start(translate=True)
                elif cmd == "stop_translate":
                    handle_stop()
                elif cmd == "ping":
                    response = "pong\n"
                elif cmd == "status":
                    response = json.dumps(status_payload(), ensure_ascii=False, sort_keys=True) + "\n"
                elif cmd == "quit":
                    response = "quitting\n"
                    conn.sendall(response.encode())
                    cleanup()
                else:
                    response = "ERR unknown command\n"
                    print(f"⚠️  Unknown command: {cmd}", flush=True)
                conn.sendall(response.encode())
            finally:
                conn.close()
        except Exception as exc:
            print(f"Error: {exc}", flush=True)


if __name__ == "__main__":
    serve()
