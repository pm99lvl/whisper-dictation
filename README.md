# whisper-dictation

Local macOS voice dictation using [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), Apple Silicon, and Hammerspoon.

## Current workflow

- **Hold Left Alt** → speak Russian → release → Russian text is pasted into the active field.
- **Hold Right Alt** → speak Russian → release → text is transcribed in Russian, translated to English, and pasted into the active field.

The active runtime path is:

1. `dictate_daemon.py` keeps the Whisper model loaded in memory and accepts commands over `/tmp/whisper_daemon.sock`. Recording and transcription are decoupled: new audio can be recorded while the previous phrase is still being transcribed/translated, and jobs are processed through a single transcription queue.
2. `hammerspoon_init.lua` handles hotkeys, sends daemon commands, watches `/tmp/whisper_paste.trigger`, and pastes `/tmp/whisper_result.txt` into the saved active app.

## Performance settings

```python
MODEL = "mlx-community/whisper-small-mlx"
SILENCE_AFTER = 10.0
MAX_SECONDS = 60
```

Why:

- `whisper-small-mlx` is roughly 2× faster than `whisper-large-v3-turbo` on this setup.
- `SILENCE_AFTER = 10.0` allows natural pauses during long dictation.
- Releasing Alt still stops recording immediately, so the 10s timeout is only a safety fallback.

## Screenshot-safe status alerts

Hammerspoon status alerts are hidden before macOS screenshots (`Cmd+Shift+3/4/5`) and restored after 0.7s with the correct state (`dictating`, `transcribing`, `translate_dictating`, `translate_transcribing`).

## Requirements

- macOS on Apple Silicon
- Hammerspoon with Accessibility permission
- Python 3.11+
- Python packages: `mlx-whisper`, `sounddevice`, `scipy`, `numpy`, `deep-translator`

```bash
pip install mlx-whisper sounddevice scipy numpy deep-translator
```

## Setup

```bash
# daemon code
mkdir -p ~/.whisper-dictation
cp dictate_daemon.py ~/.whisper-dictation/dictate_daemon.py

# Hammerspoon config
cp hammerspoon_init.lua ~/.hammerspoon/init.lua
killall Hammerspoon && open -a Hammerspoon
```

The daemon is started by Hammerspoon if `/tmp/whisper_daemon.sock` is not alive.

## Debugging

```bash
# Check daemon process without stopping it
ps -o pid,etime,%cpu,%mem,args -p $(pgrep -f dictate_daemon.py | head -1)

# Watch logs
tail -f /tmp/whisper_dictation.log

# Check socket health
echo ping | nc -U -w1 /tmp/whisper_daemon.sock

# Compile-check Python files
python3 -m py_compile dictate_daemon.py dictate.py dictate_standalone.py
```

Useful log markers:

- `⏱ transcribe_ru: ...s` — local Whisper transcription time.
- `⏱ translate_ru_en: ...s` — network translation time.
- `⏱ handoff_to_hammerspoon: ...s` — file handoff time.
- `📥 Queued transcription ...` — a completed recording was queued for transcription.
- `Still transcribing — recording next phrase concurrently` — a new recording started while the previous phrase was still being processed; it is no longer dropped.
- `⏱ transcription_job_total: ...s` — total time for one queued transcription/translation job.

## Voice punctuation commands

| Say | Result |
| --- | --- |
| вопросительный знак | `?` |
| восклицательный знак | `!` |
| точка с запятой | `;` |
| двоеточие | `:` |
| многоточие | `…` |
| новая строка | newline |
| новый абзац | blank line |

## Legacy files

These files are kept for history/backward compatibility, but are not the preferred runtime path:

- `dictate.py` — older CLI `start|stop|toggle` flow.
- `dictate_standalone.py` — older standalone event-monitor flow.
- `WhisperDictation.app/` and `com.whisper-dictation.plist` — LaunchAgent/app wrapper for the standalone flow.

Prefer `dictate_daemon.py` + Hammerspoon IPC for active development.
