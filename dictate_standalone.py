#!/usr/bin/env python3
"""
Whisper Dictation — standalone, без Hammerspoon
Зажми Left Alt → говори → отпусти → текст вставится в активное поле

Права: System Settings → Privacy & Security:
  - Input Monitoring → добавить python3
  - Accessibility    → добавить python3
"""

import sys, os, threading, tempfile, time, signal, subprocess, re
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wf
from dictation_modes import get_active_preset

os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

PID_FILE = "/tmp/whisper_daemon.pid"
with open(PID_FILE, "w") as f:
    f.write(str(os.getpid()))

ACTIVE_MODE, ACTIVE_PRESET = get_active_preset()
MODEL            = os.getenv("WHISPER_MODEL", ACTIVE_PRESET["model"])
SAMPLE_RATE      = 16000
MAX_SECONDS      = int(os.getenv("WHISPER_MAX_SECONDS", str(ACTIVE_PRESET["max_seconds"])))
SPEECH_THRESHOLD = 0.0008
SILENCE_AFTER    = float(os.getenv("WHISPER_SILENCE_AFTER", str(ACTIVE_PRESET["silence_after"])))
MIN_RECORD_TIME  = 1.0

# ── Загружаем модель один раз ──────────────────────────────────────
print("⏳ Loading model...", flush=True)
import mlx_whisper

_warmup = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
_warmup.close()
try:
    wf.write(_warmup.name, SAMPLE_RATE, np.zeros(SAMPLE_RATE, dtype=np.int16))
    mlx_whisper.transcribe(_warmup.name, path_or_hf_repo=MODEL, language="ru", word_timestamps=False, temperature=0)
finally:
    try: os.unlink(_warmup.name)
    except FileNotFoundError: pass
print("✅ Model ready. Hold Left Alt to dictate.", flush=True)

# ── Состояние ─────────────────────────────────────────────────────
_recording    = False
_transcribing = False
_stop_event   = threading.Event()
_lock         = threading.Lock()
_alt_held     = False


# ── Звук / уведомления ────────────────────────────────────────────
def play_sound(name="Tink"):
    paths = {"Tink": "/System/Library/Sounds/Tink.aiff",
             "Pop":  "/System/Library/Sounds/Pop.aiff"}
    subprocess.Popen(["afplay", paths.get(name, paths["Tink"])],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def notify(title, msg):
    subprocess.Popen(["osascript", "-e",
        f'display notification "{msg}" with title "{title}"'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── Буфер + вставка ───────────────────────────────────────────────
def set_clipboard(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)

def paste_via_quartz():
    """Cmd+V через Quartz CGEvent (требует Accessibility)"""
    try:
        import Quartz
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        V = 9  # kVK_ANSI_V
        kd = Quartz.CGEventCreateKeyboardEvent(src, V, True)
        ku = Quartz.CGEventCreateKeyboardEvent(src, V, False)
        Quartz.CGEventSetFlags(kd, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(ku, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, kd)
        time.sleep(0.03)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ku)
        print("📋 Pasted via Quartz", flush=True)
    except Exception as e:
        print(f"⚠️  Quartz paste failed: {e} — trying osascript", flush=True)
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down'],
            capture_output=True)


# ── Фокус приложения ──────────────────────────────────────────────
def get_frontmost_app():
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.localizedName() if app else None
    except Exception as e:
        print(f"⚠️  get_frontmost_app: {e}", flush=True)
        return None

def activate_app(name):
    try:
        from AppKit import NSWorkspace
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.localizedName() == name:
                app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
                return True
    except Exception as e:
        print(f"⚠️  activate_app: {e}", flush=True)
    return False


# ── Голосовые команды пунктуации ──────────────────────────────────
PUNCT_COMMANDS = [
    (r'(?i)\bвопросительный знак\b',  '?'),
    (r'(?i)\bвосклицательный знак\b', '!'),
    (r'(?i)\bточка с запятой\b',      ';'),
    (r'(?i)\bдвоеточие\b',            ':'),
    (r'(?i)\bмноготочие\b',           '…'),
    (r'(?i)\bновая строка\b',         '\n'),
    (r'(?i)\bновый абзац\b',          '\n\n'),
]

def apply_punct_commands(text):
    for pattern, replacement in PUNCT_COMMANDS:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r'\s+([?!;:…])', r'\1', text)
    text = re.sub(r'[.,]\s*([?!])', r'\1', text)
    return text.strip()


# ── Транскрипция + вставка ────────────────────────────────────────
def transcribe_and_paste(audio, focus_app):
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    wf.write(tmp.name, SAMPLE_RATE, (audio * 32767).astype(np.int16))
    try:
        print("⚙️  Transcribing...", flush=True)
        result = mlx_whisper.transcribe(
            tmp.name,
            path_or_hf_repo=MODEL,
            language="ru",
            word_timestamps=False,
            condition_on_previous_text=False,
            temperature=0)
        text = result["text"].strip()
        if not text:
            notify("Whisper", "Текст не распознан")
            return
        words = text.split()
        if len(words) > 10 and len(set(words)) / len(words) < 0.2:
            print(f"⚠️  Hallucination detected, discarding", flush=True)
            return
        text = apply_punct_commands(text)
        print(f"✅ {text}", flush=True)

        set_clipboard(text)
        if focus_app:
            activate_app(focus_app)
            time.sleep(0.15)
        paste_via_quartz()
    finally:
        try: os.unlink(tmp.name)
        except FileNotFoundError: pass


# ── Запись ────────────────────────────────────────────────────────
def record_thread(focus_app):
    global _recording, _transcribing
    print("🎙 Recording...", flush=True)

    frames = []
    chunk  = int(SAMPLE_RATE * 0.1)
    speech_detected = False
    silent_chunks   = 0
    elapsed         = 0.0
    max_rms         = 0.0

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            blocksize=chunk, latency="high") as stream:
            for _ in range(int(MAX_SECONDS / 0.1)):
                if _stop_event.is_set():
                    print("⏹  Stop by key release", flush=True)
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
    finally:
        with _lock:
            _recording = False
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
        transcribe_and_paste(audio, focus_app)
    finally:
        with _lock:
            _transcribing = False


def start_recording():
    global _recording
    with _lock:
        if _recording or _transcribing:
            print("⏭  Busy, ignoring start", flush=True)
            return
        _recording = True
        _stop_event.clear()
    focus_app = get_frontmost_app()
    print(f"🎯 Focus: {focus_app}", flush=True)
    play_sound("Tink")
    threading.Thread(target=record_thread, args=(focus_app,), daemon=True).start()

def stop_recording():
    _stop_event.set()
    print("⏹  Stop requested", flush=True)


# ── Слушатель клавиатуры ──────────────────────────────────────────
from pynput import keyboard as kb
_alt_held = False

def on_press(key):
    global _alt_held
    if key == kb.Key.alt_l and not _alt_held:
        _alt_held = True
        start_recording()

def on_release(key):
    global _alt_held
    if key == kb.Key.alt_l:
        _alt_held = False
        stop_recording()


# ── Завершение ────────────────────────────────────────────────────
def cleanup(sig=None, frame=None):
    try: os.unlink(PID_FILE)
    except: pass
    sys.exit(0)

signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)

print("🟢 Listening for Left Alt... (Ctrl+C to quit)", flush=True)

import AppKit, objc

LEFT_ALT_VK = 0x3A  # kVK_Option

# Инициализируем NSApplication (нужен для NSEvent global monitor)
app = AppKit.NSApplication.sharedApplication()

def flags_handler(event):
    try:
        if event.keyCode() == LEFT_ALT_VK:
            flags = event.modifierFlags()
            alt_pressed = bool(flags & AppKit.NSAlternateKeyMask)
            if alt_pressed:
                on_press(kb.Key.alt_l)
            else:
                on_release(kb.Key.alt_l)
    except Exception as e:
        print(f"event error: {e}", flush=True)

monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
    AppKit.NSFlagsChangedMask,
    flags_handler
)

if monitor is None:
    print("❌ NSEvent monitor failed — check Accessibility permission", flush=True)
    sys.exit(1)

print("✅ NSEvent global monitor active", flush=True)

from PyObjCTools import AppHelper
AppHelper.runConsoleEventLoop(installInterrupt=True)
