#!/usr/bin/env python3
"""
Whisper Dictation Daemon
Держит модель в памяти — нет overhead на загрузку при каждой диктовке.
Принимает команды start/stop через Unix socket.
"""

import sys, os, socket, threading, tempfile, subprocess, time, signal

os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

# Пишем PID сразу — до загрузки модели — чтобы Hammerspoon не убивал нас во время загрузки
_PID_FILE_EARLY = "/tmp/whisper_daemon.pid"
with open(_PID_FILE_EARLY, "w") as _f:
    _f.write(str(os.getpid()))

MODEL             = "mlx-community/whisper-small-mlx"
SAMPLE_RATE       = 16000
MAX_SECONDS       = 60     # максимум 60 секунд — защита от зависания
SPEECH_THRESHOLD  = 0.0008
SILENCE_AFTER     = 10.0   # 10с тишины после речи → авто-стоп (для длинных текстов с паузами)
MIN_RECORD_TIME   = 1.0

SOCKET_PATH   = "/tmp/whisper_daemon.sock"
STATE_FILE    = "/tmp/whisper_dictation_state"
PID_FILE      = "/tmp/whisper_daemon.pid"
RESULT_FILE   = "/tmp/whisper_result.txt"
TRIGGER_FILE  = "/tmp/whisper_paste.trigger"
FOCUS_FILE    = "/tmp/whisper_focus_app.txt"

import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wf

# ── Preload model (один раз при старте) ───────────────────────────
print("⏳ Loading model...", flush=True)
import mlx_whisper

_warmup = tempfile.mktemp(suffix=".wav")
wf.write(_warmup, SAMPLE_RATE, np.zeros(SAMPLE_RATE, dtype=np.int16))
mlx_whisper.transcribe(_warmup, path_or_hf_repo=MODEL, language=None, word_timestamps=False)
os.unlink(_warmup)
print("✅ Model ready.", flush=True)

# ── State ─────────────────────────────────────────────────────────
_recording    = False
_transcribing = False
_stop_event   = threading.Event()
_lock         = threading.Lock()


def notify(title, msg):
    subprocess.run(["osascript", "-e",
        f'display notification "{msg}" with title "{title}"'], capture_output=True)


# Голосовые команды → знаки препинания
PUNCT_COMMANDS = [
    (r'(?i)\bвопросительный знак\b',  '?'),
    (r'(?i)\bвосклицательный знак\b', '!'),
    (r'(?i)\bточка с запятой\b',      ';'),
    (r'(?i)\bдвоеточие\b',            ':'),
    (r'(?i)\bмноготочие\b',           '…'),
    (r'(?i)\bновая строка\b',         '\n'),
    (r'(?i)\bновый абзац\b',          '\n\n'),
]

def apply_punct_commands(text: str) -> str:
    import re
    for pattern, replacement in PUNCT_COMMANDS:
        text = re.sub(pattern, replacement, text)
    # Убираем пробел перед знаком и лишнюю точку перед ?!
    text = re.sub(r'\s+([?!;:…])', r'\1', text)
    text = re.sub(r'[.,]\s*([?!])', r'\1', text)
    return text.strip()


def transcribe_and_paste(audio: np.ndarray, translate: bool = False):
    tmp = tempfile.mktemp(suffix=".wav")
    wf.write(tmp, SAMPLE_RATE, (audio * 32767).astype(np.int16))
    try:
        if translate:
            print("⚙️  Transcribing (ru)...", flush=True)
            result = mlx_whisper.transcribe(
                tmp, path_or_hf_repo=MODEL, language="ru", word_timestamps=False,
                condition_on_previous_text=False)
            text = result["text"].strip()
            if text:
                print(f"   RU: {text}", flush=True)
                from deep_translator import GoogleTranslator
                text = GoogleTranslator(source="ru", target="en").translate(text)
                print(f"⚙️  → EN: {text}", flush=True)
        else:
            print("⚙️  Transcribing...", flush=True)
            result = mlx_whisper.transcribe(
                tmp, path_or_hf_repo=MODEL, language=None, word_timestamps=False,
                condition_on_previous_text=False)
            text = result["text"].strip()
        if not text:
            notify("Whisper", "Текст не распознан")
            return
        # Детектор галлюцинаций: если >80% слов одинаковые — выбрасываем
        words = text.split()
        if len(words) > 10 and len(set(words)) / len(words) < 0.2:
            print(f"⚠️  Hallucination loop detected ({len(words)} words), discarding", flush=True)
            return
        text = apply_punct_commands(text)
        print(f"✅ {text}", flush=True)
        # Пишем результат в файл — Hammerspoon (с Accessibility) сделает вставку
        with open(RESULT_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        with open(TRIGGER_FILE, "w") as f:
            f.write("paste")
    finally:
        try: os.unlink(tmp)
        except: pass


def record_thread(translate: bool = False):
    global _recording, _transcribing
    print(f"🎙 Recording... (translate={translate})", flush=True)

    with open(STATE_FILE, "w") as f:
        f.write("recording")

    frames = []
    chunk  = int(SAMPLE_RATE * 0.1)
    speech_detected = False
    silent_chunks   = 0
    elapsed         = 0.0
    max_rms         = 0.0

    # Выбираем устройство: предпочитаем встроенный мик MacBook
    def find_builtin_mic():
        for i, d in enumerate(sd.query_devices()):
            if d['max_input_channels'] > 0 and 'MacBook' in d['name']:
                return i
        return None

    device = find_builtin_mic()
    if device is not None:
        print(f"🎤 Using: {sd.query_devices(device)['name']}", flush=True)

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            blocksize=int(SAMPLE_RATE * 0.1),
                            latency="high", device=device) as stream:
            for _ in range(int(MAX_SECONDS / 0.1)):
                if _stop_event.is_set():
                    print("⏹  Stop by hotkey", flush=True)
                    break
                data, _ = stream.read(chunk)
                frames.append(data.copy())
                elapsed += 0.1
                rms = float(np.sqrt(np.mean(data ** 2)))
                if rms > max_rms: max_rms = rms
                if rms >= SPEECH_THRESHOLD:
                    speech_detected = True
                    silent_chunks = 0
                elif speech_detected and elapsed >= MIN_RECORD_TIME:
                    silent_chunks += 1
                    if silent_chunks >= int(SILENCE_AFTER / 0.1):
                        print("🔇 Silence stop", flush=True)
                        break
    except Exception as e:
        print(f"⚠️  InputStream error: {e}", flush=True)
        notify("Whisper", f"Ошибка микрофона: {e}")
    finally:
        try: os.remove(STATE_FILE)
        except: pass
        # Сбрасываем флаг ВСЕГДА — даже если InputStream упал
        with _lock:
            _recording = False

    if not frames:
        return

    with _lock:
        _transcribing = True

    try:
        if len(frames) < 5:
            print("⚠️  Too short", flush=True)
            return

        audio = np.concatenate(frames).flatten()
        if not speech_detected:
            print(f"⚠️  No speech (max RMS={max_rms:.4f}, threshold={SPEECH_THRESHOLD})", flush=True)
            notify("Whisper", "Речь не обнаружена")
            return

        transcribe_and_paste(audio, translate=translate)
    finally:
        with _lock:
            _transcribing = False


def handle_start(translate: bool = False):
    global _recording
    with _lock:
        if _recording:
            print("Already recording — ignoring start", flush=True)
            return
        if _transcribing:
            print("Still transcribing — ignoring start", flush=True)
            return
        _recording = True
        _stop_event.clear()
    threading.Thread(target=record_thread, args=(translate,), daemon=True).start()


def handle_stop():
    _stop_event.set()
    try: os.remove(STATE_FILE)
    except: pass
    print("⏹  Stop requested", flush=True)


# ── Socket server ─────────────────────────────────────────────────
def cleanup(sig=None, frame=None):
    for p in (SOCKET_PATH, PID_FILE, STATE_FILE):
        try: os.unlink(p)
        except: pass
    sys.exit(0)

signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)

try: os.unlink(SOCKET_PATH)
except: pass

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET_PATH)
server.listen(5)
os.chmod(SOCKET_PATH, 0o600)

with open(PID_FILE, "w") as f:
    f.write(str(os.getpid()))

print(f"🟢 Daemon listening on {SOCKET_PATH}", flush=True)

while True:
    try:
        conn, _ = server.accept()
        try:
            cmd = conn.recv(32).decode().strip()
        finally:
            conn.close()
        print(f"CMD: {cmd}", flush=True)
        if cmd == "start":
            handle_start(translate=False)
        elif cmd == "stop":
            handle_stop()
        elif cmd == "start_translate":
            handle_start(translate=True)
        elif cmd == "stop_translate":
            handle_stop()
        elif cmd == "quit":
            cleanup()
    except Exception as e:
        print(f"Error: {e}", flush=True)
