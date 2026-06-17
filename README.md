# whisper-dictation

Local macOS voice dictation using [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), Apple Silicon, and Hammerspoon.

## Current workflow

- **Hold Left Alt** → speak Russian → release → Russian text is pasted into the active field.
- **Hold Right Alt** → speak Russian → release → text is transcribed in Russian, translated to English, and pasted into the active field.

The active runtime path is:

1. `dictate_daemon.py` keeps the Whisper model loaded in memory and accepts commands over `/tmp/whisper_daemon.sock`. Recording and transcription are decoupled: new audio can be recorded while the previous phrase is still being transcribed/translated, and jobs are processed through a single transcription queue.
2. `hammerspoon_init.lua` handles hotkeys, sends daemon commands, watches `/tmp/whisper_paste.trigger`, and pastes `/tmp/whisper_result.txt` into the saved active app.

## Local UI

Run this from the project root to open the localhost control panel:

```bash
python3 dictation_ui.py
```

The panel lives at `http://127.0.0.1:8787` and lets you switch between:

- `Fast` - smaller model, quicker turnaround, lower quality.
- `Quality` - larger model, slower turnaround, better Russian recognition.

Changing the mode writes `~/.whisper-dictation/dictation_mode.json` and restarts the daemon with that preset.

## Quality mode

This checkout now defaults to a quality-first preset:

```python
MODEL = "mlx-community/whisper-large-v3-turbo"
SILENCE_AFTER = 6.0
MAX_SECONDS = 90
```

Why:

- `whisper-large-v3-turbo` should give noticeably better Russian recognition than `whisper-small-mlx`, while staying more practical than the full `large-v3-mlx` model.
- `SILENCE_AFTER = 6.0` leaves more room for natural pauses, which helps long phrases stay intact.
- `MAX_SECONDS = 90` gives long dictation sessions more room before the safety cutoff.
- Releasing Alt still stops recording immediately, so the timeout remains a fallback.

You can still override the preset with env vars if you want to trade quality back for speed:

```bash
export WHISPER_MODEL=mlx-community/whisper-small-mlx
export WHISPER_SILENCE_AFTER=2
export WHISPER_MAX_SECONDS=45
```

If you want to try an even faster model later, you can point `WHISPER_MODEL` at a `tiny` or `small` variant, but expect a noticeable quality drop and a shorter first-download time.

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

# Runtime status as JSON
printf status | nc -U -w1 /tmp/whisper_daemon.sock | python3 -m json.tool

# Last persisted status snapshot
python3 -m json.tool /tmp/whisper_status.json

# Compile-check Python files
python3 -m py_compile dictation_runtime.py dictate_daemon.py dictate.py dictate_standalone.py
```

Useful log markers:

- `🧾 event=... session_id=... state=...` — structured runtime event.
- `state: idle|recording|queued|transcribing|translating|ready_to_paste|done|error` — state-machine snapshot in `status`.
- `⏱ transcribe_ru: ...s` — local Whisper transcription time.
- `⏱ translate_ru_en: ...s` — network translation time.
- `⏱ handoff_to_hammerspoon: ...s` — file handoff time.
- `📥 Queued transcription ...` — a completed recording was queued for transcription.
- `Still transcribing — recording next phrase concurrently` — a new recording started while the previous phrase was still being processed; it is no longer dropped.
- `⏱ transcription_job_total: ...s` — total time for one queued transcription/translation job.

## Resiliency

See the full operational playbook: [`RESILIENCY_GUIDE.md`](./RESILIENCY_GUIDE.md).

- `dictation_runtime.py` contains testable runtime helpers: session IDs, state snapshots, atomic JSON writes, stale-file cleanup, and process liveness checks.
- Daemon startup refuses duplicate instances before model load if `/tmp/whisper_daemon.pid` points to a live process.
- Startup removes stale socket/state/result/trigger artifacts when no live daemon owns them.
- `status` socket command and `/tmp/whisper_status.json` expose the current runtime state for Hammerspoon/watchdogs.
- Transcription jobs are queued instead of dropping new recordings while the previous job is still finishing.

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
