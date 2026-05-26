# whisper-dictation

Free macOS voice dictation using [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) (Apple Silicon) + Hammerspoon.

**Hold Left Alt** → speak → **release** → text is pasted into the active field.

## How it works

- `dictate_daemon.py` — persistent Python daemon that keeps the Whisper model loaded in RAM. Accepts `start` / `stop` commands over a Unix socket (`/tmp/whisper_daemon.sock`). After transcription writes the result to `/tmp/whisper_result.txt` and touches `/tmp/whisper_paste.trigger`.
- `~/.hammerspoon/init.lua` — Hammerspoon config that handles the hotkey (Left Alt hold), watches for the trigger file, and pastes via `hs.eventtap.keyStroke({"cmd"}, "v")` (requires Accessibility permission).

## Requirements

- macOS (Apple Silicon)
- [Hammerspoon](https://www.hammerspoon.org/)
- Python 3.11+ via pyenv (or any Python 3.11+)
- `mlx-whisper`, `sounddevice`, `scipy`, `numpy`

```bash
pip install mlx-whisper sounddevice scipy numpy
```

## Setup

1. Copy `dictate_daemon.py` to `~/.whisper-dictation/dictate_daemon.py`
2. Copy `init.lua` to `~/.hammerspoon/init.lua`
3. Grant Hammerspoon **Accessibility** permission in System Settings → Privacy & Security
4. Reload Hammerspoon config

The daemon starts automatically when Hammerspoon loads. First start downloads the model (~1.5 GB, `mlx-community/whisper-large-v3-turbo`).

## Voice punctuation commands (Russian)

| Say | Result |
|-----|--------|
| вопросительный знак | ? |
| восклицательный знак | ! |
| точка с запятой | ; |
| двоеточие | : |
| многоточие | … |
| новая строка | ↵ |
| новый абзац | ↵↵ |

## Key settings in `dictate_daemon.py`

```python
MODEL            = "mlx-community/whisper-large-v3-turbo"
SPEECH_THRESHOLD = 0.0008   # RMS threshold — tune for your mic
SILENCE_AFTER    = 5.0      # seconds of silence before auto-stop
MAX_SECONDS      = 300      # max recording length
```
