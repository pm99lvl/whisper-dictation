#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dictation_modes import CONFIG_PATH, PRESETS, get_active_mode, get_active_preset, write_mode
from dictation_runtime import is_process_alive

HOST = "127.0.0.1"
PORT = 8787
PYTHON = sys.executable
LOG_PATH = Path("/tmp/whisper_dictation.log")
PID_FILE = Path("/tmp/whisper_daemon.pid")
SOCKET_PATH = Path("/tmp/whisper_daemon.sock")


def project_script(name: str) -> Path:
    live = Path.home() / ".whisper-dictation" / name
    if live.exists():
        return live
    return Path(__file__).resolve().with_name(name)


DAEMON_SCRIPT = project_script("dictate_daemon.py")
DAEMON_CWD = DAEMON_SCRIPT.parent


def read_socket_json(command: str) -> dict:
    if not SOCKET_PATH.exists():
        return {"ok": False, "error": "socket_missing"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(SOCKET_PATH))
            client.sendall(command.encode("utf-8"))
            client.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        payload = b"".join(chunks).decode("utf-8", errors="replace").strip()
        return json.loads(payload) if payload else {"ok": False, "error": "empty_response"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def daemon_status() -> dict:
    return read_socket_json("status")


def stop_daemon() -> None:
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        pid = None

    if pid and is_process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(40):
            if not is_process_alive(pid):
                break
            time.sleep(0.1)
        if is_process_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def start_daemon() -> None:
    mode, preset = get_active_preset()
    env = os.environ.copy()
    env.setdefault("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    env["WHISPER_PRESET"] = mode
    env["WHISPER_MODEL"] = preset["model"]
    env["WHISPER_SILENCE_AFTER"] = str(preset["silence_after"])
    env["WHISPER_MAX_SECONDS"] = str(preset["max_seconds"])
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("ab") as log_file:
        subprocess.Popen(
            [PYTHON, str(DAEMON_SCRIPT)],
            cwd=str(DAEMON_CWD),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def ensure_daemon_running() -> None:
    status = daemon_status()
    if status.get("ok"):
        return
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pid = None
        if pid and is_process_alive(pid):
            return
    start_daemon()


def wait_for_daemon(timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    status = daemon_status()
    while time.time() < deadline:
        if status.get("ok"):
            return status
        time.sleep(1.0)
        status = daemon_status()
    return status


def restart_daemon_for_mode(mode: str) -> dict:
    normalized = write_mode(mode)
    stop_daemon()
    start_daemon()
    return {
        "ok": True,
        "mode": normalized,
        "preset": PRESETS[normalized],
        "daemon": wait_for_daemon(),
    }


HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Whisper Dictation Modes</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b1020;
      --panel: rgba(17, 24, 39, 0.9);
      --panel-2: rgba(30, 41, 59, 0.8);
      --text: #e5eefb;
      --muted: #9fb0c7;
      --line: rgba(148, 163, 184, 0.18);
      --accent: #7dd3fc;
      --accent-2: #a78bfa;
      --good: #4ade80;
      --warn: #fbbf24;
      --shadow: 0 24px 80px rgba(0,0,0,0.45);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(125, 211, 252, 0.18), transparent 30%),
        radial-gradient(circle at 80% 20%, rgba(167, 139, 250, 0.16), transparent 25%),
        linear-gradient(180deg, #060816, #0b1020 48%, #0a1224);
    }
    .wrap {
      max-width: 960px;
      margin: 0 auto;
      padding: 40px 20px 56px;
    }
    .hero {
      display: flex;
      gap: 20px;
      align-items: end;
      justify-content: space-between;
      margin-bottom: 22px;
    }
    h1 {
      margin: 0;
      font-size: clamp(28px, 5vw, 44px);
      letter-spacing: -0.03em;
    }
    .subtitle {
      color: var(--muted);
      margin-top: 10px;
      max-width: 62ch;
      line-height: 1.5;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 10px 14px;
      background: rgba(255,255,255,0.04);
      color: var(--muted);
      white-space: nowrap;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-top: 22px;
    }
    .card {
      position: relative;
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 22px;
      background: linear-gradient(180deg, var(--panel), var(--panel-2));
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .card.active {
      border-color: rgba(125, 211, 252, 0.55);
      box-shadow: 0 0 0 1px rgba(125, 211, 252, 0.15), var(--shadow);
    }
    .card h2 {
      margin: 0 0 8px;
      font-size: 24px;
    }
    .desc {
      margin: 0 0 18px;
      color: var(--muted);
      line-height: 1.55;
      min-height: 3lh;
    }
    .meta {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 18px;
    }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
      color: var(--text);
      background: rgba(255,255,255,0.03);
    }
    .pill.muted { color: var(--muted); }
    button {
      appearance: none;
      border: none;
      border-radius: 14px;
      padding: 14px 18px;
      font-size: 15px;
      font-weight: 700;
      color: #07111f;
      background: linear-gradient(135deg, var(--accent), #c4b5fd);
      cursor: pointer;
      transition: transform 0.15s ease, filter 0.15s ease;
    }
    button:hover { transform: translateY(-1px); filter: brightness(1.02); }
    button:disabled { cursor: wait; opacity: 0.7; transform: none; }
    .ghost {
      background: transparent;
      color: var(--text);
      border: 1px solid var(--line);
      font-weight: 600;
    }
    .toolbar {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 20px;
    }
    .status {
      margin-top: 22px;
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px 20px;
      background: rgba(255,255,255,0.04);
    }
    .status h3 {
      margin: 0 0 10px;
      font-size: 15px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }
    .status-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .stat {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
      background: rgba(8, 15, 32, 0.52);
    }
    .stat .label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .stat .value {
      font-size: 14px;
      line-height: 1.35;
      word-break: break-word;
    }
    .error {
      color: #fecaca;
    }
    @media (max-width: 760px) {
      .hero { flex-direction: column; align-items: stretch; }
      .grid, .status-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div>
        <h1>Whisper Dictation</h1>
        <div class="subtitle">
          Переключайся между быстрым и качественным режимами без правки кода.
          Режим выбирается здесь, daemon перезапускается автоматически.
        </div>
      </div>
      <div class="chip" id="chip">Сейчас загружаюсь…</div>
    </div>

    <div class="grid" id="cards"></div>

    <div class="status">
      <h3>Live status</h3>
      <div class="status-grid">
        <div class="stat"><span class="label">Mode</span><span class="value" id="stat-mode">—</span></div>
        <div class="stat"><span class="label">Model</span><span class="value" id="stat-model">—</span></div>
        <div class="stat"><span class="label">State</span><span class="value" id="stat-state">—</span></div>
        <div class="stat"><span class="label">Record</span><span class="value" id="stat-record">—</span></div>
      </div>
      <div class="toolbar">
        <button class="ghost" id="restart-btn">Restart current mode</button>
        <button class="ghost" id="open-log-btn">Open log file</button>
      </div>
    </div>
  </div>

  <script>
    const PRESETS = %PRESETS_JSON%;
    let busy = false;

    function prettySeconds(value) {
      if (value === null || value === undefined) return "—";
      return `${Number(value).toFixed(1)}s`;
    }

    function renderCards(activeMode) {
      const cards = document.getElementById("cards");
      cards.innerHTML = "";
      Object.entries(PRESETS).forEach(([mode, preset]) => {
        const el = document.createElement("div");
        el.className = `card ${mode === activeMode ? "active" : ""}`;
        el.innerHTML = `
          <h2>${preset.name}</h2>
          <p class="desc">${preset.description}</p>
          <div class="meta">
            <span class="pill">model: ${preset.model}</span>
            <span class="pill">silence: ${preset.silence_after}s</span>
            <span class="pill muted">max: ${preset.max_seconds}s</span>
          </div>
          <button data-mode="${mode}">${mode === activeMode ? "Active" : "Switch to " + preset.name}</button>
        `;
        cards.appendChild(el);
      });
      cards.querySelectorAll("button[data-mode]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (busy) return;
          busy = true;
          btn.disabled = true;
          try {
            await fetch("/api/mode", {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({mode: btn.dataset.mode}),
            });
            await refresh();
          } finally {
            busy = false;
            btn.disabled = false;
          }
        });
      });
    }

    function renderState(payload) {
      const mode = payload.mode || "—";
      const preset = payload.preset || {};
      const status = payload.daemon || {};
      document.getElementById("chip").textContent = `${mode} mode · ${preset.model || "unknown model"}`;
      document.getElementById("stat-mode").textContent = `${mode}${preset.name ? " · " + preset.name : ""}`;
      document.getElementById("stat-model").textContent = preset.model || "—";
      document.getElementById("stat-state").textContent = status.state || "unknown";
      document.getElementById("stat-record").textContent = prettySeconds(status.record_seconds);
      renderCards(mode);
    }

    async function refresh() {
      try {
        const res = await fetch("/api/state");
        const payload = await res.json();
        renderState(payload);
      } catch (err) {
        document.getElementById("chip").innerHTML = `<span class="error">Cannot reach daemon</span>`;
      }
    }

    document.getElementById("restart-btn").addEventListener("click", async () => {
      if (busy) return;
      busy = true;
      try {
        await fetch("/api/restart", {method: "POST"});
        await refresh();
      } finally {
        busy = false;
      }
    });

    document.getElementById("open-log-btn").addEventListener("click", async () => {
      await fetch("/api/open-log", {method: "POST"});
    });

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = HTML.replace("%PRESETS_JSON%", json.dumps(PRESETS, ensure_ascii=False))
            self._send(200, "text/html; charset=utf-8", body.encode("utf-8"))
            return
        if path == "/api/state":
            mode = get_active_mode()
            self._json(
                {
                    "ok": True,
                    "mode": mode,
                    "preset": PRESETS[mode],
                    "config_path": str(CONFIG_PATH),
                    "daemon": daemon_status(),
                }
            )
            return
        if path == "/api/presets":
            self._json({"ok": True, "presets": PRESETS, "active_mode": get_active_mode()})
            return
        self._json({"ok": False, "error": "not_found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            payload = {}

        if path == "/api/mode":
            mode = payload.get("mode")
            if mode not in PRESETS:
                self._json({"ok": False, "error": "unknown_mode"}, status=400)
                return
            data = restart_daemon_for_mode(mode)
            self._json({**data, "daemon": daemon_status(), "config_path": str(CONFIG_PATH)})
            return

        if path == "/api/restart":
            mode = get_active_mode()
            stop_daemon()
            start_daemon()
            self._json({"ok": True, "mode": mode, "daemon": wait_for_daemon()})
            return

        if path == "/api/open-log":
            try:
                subprocess.Popen(["open", str(LOG_PATH)])
            except Exception:
                pass
            self._json({"ok": True})
            return

        self._json({"ok": False, "error": "not_found"}, status=404)


def main() -> None:
    ensure_daemon_running()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Whisper UI listening on http://{HOST}:{PORT}", flush=True)
    print(f"Preset file: {CONFIG_PATH}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
