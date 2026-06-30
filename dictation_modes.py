from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

MODELS_DIR = Path(__file__).parent / "models"

PRESETS: dict[str, dict[str, Any]] = {
    "fast": {
        "name": "Fast",
        "description": "whisper.cpp small — Metal GPU, fastest.",
        "engine": "whispercpp",
        "model": str(MODELS_DIR / "ggml-small.bin"),
        "silence_after": 2.0,
        "max_seconds": 45,
    },
    "quality": {
        "name": "Quality",
        "description": "whisper.cpp large-v3-turbo — Metal GPU, best accuracy.",
        "engine": "whispercpp",
        "model": str(MODELS_DIR / "ggml-large-v3-turbo.bin"),
        "silence_after": 6.0,
        "max_seconds": 90,
    },
}

DEFAULT_MODE = "fast"
CONFIG_PATH = Path.home() / ".config" / "whisper-dictation" / "mode.json"


def normalize_mode(mode: str | None) -> str:
    if mode and mode in PRESETS:
        return mode
    return DEFAULT_MODE


def read_mode_from_disk() -> str:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return normalize_mode(payload.get("mode"))
    except FileNotFoundError:
        return DEFAULT_MODE
    except Exception:
        return DEFAULT_MODE


def get_active_mode() -> str:
    return normalize_mode(os.getenv("WHISPER_PRESET") or read_mode_from_disk())


def get_active_preset() -> tuple[str, dict[str, Any]]:
    mode = get_active_mode()
    return mode, PRESETS[mode]


def write_mode(mode: str) -> str:
    normalized = normalize_mode(mode)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": normalized,
        "updated_at": time.time(),
    }
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized
