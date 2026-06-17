#!/usr/bin/env python3
"""
Whisper Dictation — замена Whisper Flow
Double-Alt → запись → Double-Alt (или 3 сек тишины ПОСЛЕ речи) → текст
"""

import sys
import os
import tempfile
import subprocess
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wavfile
from dictation_modes import get_active_preset

# Прописываем PATH чтобы ffmpeg находился из Homebrew
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

# ── Настройки ──────────────────────────────────────────────────────────────
ACTIVE_MODE, ACTIVE_PRESET = get_active_preset()
MODEL             = os.getenv("WHISPER_MODEL", ACTIVE_PRESET["model"])
SAMPLE_RATE       = 16000
MAX_SECONDS       = int(os.getenv("WHISPER_MAX_SECONDS", str(ACTIVE_PRESET["max_seconds"])))
SPEECH_THRESHOLD  = 0.005   # RMS выше этого = речь
SILENCE_AFTER     = float(os.getenv("WHISPER_SILENCE_AFTER", str(ACTIVE_PRESET["silence_after"])))
MIN_RECORD_TIME   = 1.5     # минимальное время записи (не стопать раньше)

STATE_FILE = "/tmp/whisper_dictation_state"
PID_FILE   = "/tmp/whisper_dictation.pid"


def write_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def clear_pid():
    for p in (PID_FILE, STATE_FILE):
        try: os.remove(p)
        except: pass

def kill_existing():
    """Убиваем предыдущий процесс если он ещё жив."""
    if not os.path.exists(PID_FILE):
        return
    try:
        pid = int(open(PID_FILE).read().strip())
        if pid != os.getpid():
            import signal
            os.kill(pid, signal.SIGTERM)
            import time; time.sleep(0.3)
    except Exception:
        pass
    clear_pid()


def is_recording():
    return os.path.exists(STATE_FILE)


def start_recording():
    kill_existing()          # убиваем старый процесс если есть
    write_pid()              # записываем свой PID
    with open(STATE_FILE, "w") as f:
        f.write(str(os.getpid()))

    print("🎙  Запись...", flush=True)

    frames = []
    chunk  = int(SAMPLE_RATE * 0.1)   # 100 мс чанки
    max_ch = int(MAX_SECONDS / 0.1)

    speech_detected  = False
    silent_after_ch  = 0
    elapsed          = 0.0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        for _ in range(max_ch):
            # Стоп по второму нажатию Double-Alt
            if not os.path.exists(STATE_FILE):
                print("⏹  Стоп по хоткею", flush=True)
                break

            data, _ = stream.read(chunk)
            frames.append(data.copy())
            elapsed += 0.1

            rms = float(np.sqrt(np.mean(data ** 2)))

            if rms >= SPEECH_THRESHOLD:
                speech_detected = True
                silent_after_ch = 0
            elif speech_detected and elapsed >= MIN_RECORD_TIME:
                silent_after_ch += 1
                if silent_after_ch >= int(SILENCE_AFTER / 0.1):
                    print("🔇  Тишина после речи — стоп", flush=True)
                    break

    clear_pid()

    if len(frames) < 5:
        print("⚠️  Слишком короткая запись", flush=True)
        return

    audio = np.concatenate(frames, axis=0).flatten()

    if not speech_detected:
        print("⚠️  Речь не обнаружена", flush=True)
        notify("Whisper Dictation", "Речь не обнаружена")
        return

    transcribe_and_paste(audio)


def stop_recording():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        print("⏹  Стоп", flush=True)
    else:
        print("⚠️  Запись не активна", flush=True)


def transcribe_and_paste(audio: np.ndarray):
    import mlx_whisper

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    wavfile.write(tmp_path, SAMPLE_RATE, (audio * 32767).astype(np.int16))

    try:
        print("⚙️  Транскрипция...", flush=True)
        result = mlx_whisper.transcribe(
            tmp_path,
            path_or_hf_repo=MODEL,
            language=None,
            word_timestamps=False,
        )
        text = result["text"].strip()

        if not text:
            print("⚠️  Пусто", flush=True)
            notify("Whisper Dictation", "Текст не распознан")
            return

        print(f"✅  {text}", flush=True)
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)

        # Вставляем в активное поле
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "v" using {command down}'
        ], check=True)

        notify("Whisper Dictation", f"✅ {text[:80]}{'…' if len(text) > 80 else ''}")

    finally:
        os.unlink(tmp_path)


def notify(title: str, message: str):
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    if cmd == "start":
        start_recording()
    elif cmd == "stop":
        stop_recording()
    elif cmd == "toggle":
        if is_recording():
            stop_recording()
        else:
            start_recording()
    else:
        print(f"Использование: {sys.argv[0]} [start|stop|toggle]")
