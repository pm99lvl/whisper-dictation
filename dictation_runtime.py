from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATES = {
    "idle",
    "recording",
    "stopping",
    "queued",
    "transcribing",
    "translating",
    "ready_to_paste",
    "done",
    "error",
}


def now() -> float:
    return time.time()


def make_session_id(mode: str) -> str:
    safe_mode = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in mode).strip("-") or "session"
    return f"{safe_mode}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def is_process_alive(pid: int | str | None) -> bool:
    try:
        pid_int = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _remove(path: Path, removed: list[str]) -> None:
    try:
        path.unlink()
        removed.append(str(path))
    except FileNotFoundError:
        pass


def cleanup_stale_files(
    *,
    socket_path: Path,
    state_path: Path,
    pid_path: Path,
    result_path: Path,
    trigger_path: Path,
    glob_root: Path,
) -> list[str]:
    """Clean stale IPC files and orphan result/trigger payloads.

    The PID file is removed only when the process is no longer alive. Socket/state
    files are runtime artifacts and are always safe to remove during daemon boot.
    """
    removed: list[str] = []
    for path in (socket_path, state_path, result_path, trigger_path):
        _remove(path, removed)

    if pid_path.exists() and not is_process_alive(pid_path.read_text().strip()):
        _remove(pid_path, removed)

    for pattern in ("whisper_result_*.json", "whisper_result_*.trigger", "whisper_result_*.tmp"):
        for path in glob_root.glob(pattern):
            _remove(path, removed)
    return removed


@dataclass
class RuntimeState:
    state: str = "idle"
    mode: str | None = None
    session_id: str | None = None
    queue_size: int = 0
    error: str | None = None
    started_at: float | None = None
    stopped_at: float | None = None
    updated_at: float = field(default_factory=now)

    def _set(self, state: str, **updates: Any) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state: {state}")
        self.state = state
        for key, value in updates.items():
            setattr(self, key, value)
        self.updated_at = now()

    def start_recording(self, mode: str) -> str:
        if self.state == "recording":
            raise RuntimeError("already recording")
        session_id = make_session_id(mode)
        self._set(
            "recording",
            mode=mode,
            session_id=session_id,
            error=None,
            started_at=now(),
            stopped_at=None,
        )
        return session_id

    def stop_recording(self) -> None:
        if self.state == "recording":
            self._set("stopping", stopped_at=now())

    def mark_queued(self, queue_size: int) -> None:
        self._set("queued", queue_size=queue_size)

    def mark_transcribing(self) -> None:
        self._set("transcribing")

    def mark_translating(self) -> None:
        self._set("translating")

    def mark_ready_to_paste(self) -> None:
        self._set("ready_to_paste")

    def mark_done(self) -> None:
        self._set("done", error=None)

    def mark_error(self, error: str) -> None:
        self._set("error", error=error)

    def snapshot(self) -> dict[str, Any]:
        record_seconds = None
        if self.started_at is not None:
            end = self.stopped_at if self.stopped_at is not None else now()
            record_seconds = max(0.0, end - self.started_at)
        return {
            "state": self.state,
            "mode": self.mode,
            "session_id": self.session_id,
            "queue_size": self.queue_size,
            "error": self.error,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "updated_at": self.updated_at,
            "record_seconds": record_seconds,
        }
