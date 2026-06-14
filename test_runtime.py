import json
import os
import tempfile
import unittest
from pathlib import Path

from dictation_runtime import (
    RuntimeState,
    cleanup_stale_files,
    is_process_alive,
    make_session_id,
    write_json_atomic,
)


class RuntimeStateTests(unittest.TestCase):
    def test_make_session_id_has_prefix_and_unique_suffix(self):
        first = make_session_id("translate")
        second = make_session_id("translate")
        self.assertTrue(first.startswith("translate-"))
        self.assertTrue(second.startswith("translate-"))
        self.assertNotEqual(first, second)

    def test_runtime_state_snapshot_is_json_serializable(self):
        state = RuntimeState()
        session_id = state.start_recording(mode="translate")
        state.stop_recording()
        state.mark_queued(queue_size=1)
        state.mark_transcribing()
        state.mark_done()

        snapshot = state.snapshot()
        json.dumps(snapshot)

        self.assertEqual(snapshot["state"], "done")
        self.assertEqual(snapshot["mode"], "translate")
        self.assertEqual(snapshot["session_id"], session_id)
        self.assertGreaterEqual(snapshot["record_seconds"], 0)
        self.assertEqual(snapshot["queue_size"], 1)

    def test_runtime_state_rejects_start_while_recording(self):
        state = RuntimeState()
        state.start_recording(mode="dictation")
        with self.assertRaises(RuntimeError):
            state.start_recording(mode="translate")

    def test_write_json_atomic_writes_complete_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            write_json_atomic(path, {"ok": True, "value": "тест"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True, "value": "тест"})
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_cleanup_stale_files_removes_socket_state_and_orphan_results_but_keeps_live_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            socket_path = root / "whisper.sock"
            state_path = root / "state"
            stale_pid_path = root / "stale.pid"
            live_pid_path = root / "live.pid"
            result_path = root / "result.txt"
            trigger_path = root / "trigger"
            session_result = root / "whisper_result_old.json"
            session_trigger = root / "whisper_result_old.trigger"

            for path in (socket_path, state_path, result_path, trigger_path, session_result, session_trigger):
                path.write_text("x")
            stale_pid_path.write_text("999999")
            live_pid_path.write_text(str(os.getpid()))

            removed = cleanup_stale_files(
                socket_path=socket_path,
                state_path=state_path,
                pid_path=stale_pid_path,
                result_path=result_path,
                trigger_path=trigger_path,
                glob_root=root,
            )

            self.assertFalse(socket_path.exists())
            self.assertFalse(state_path.exists())
            self.assertFalse(stale_pid_path.exists())
            self.assertFalse(result_path.exists())
            self.assertFalse(trigger_path.exists())
            self.assertFalse(session_result.exists())
            self.assertFalse(session_trigger.exists())
            self.assertTrue(any("stale.pid" in item for item in removed))

            removed_live = cleanup_stale_files(
                socket_path=socket_path,
                state_path=state_path,
                pid_path=live_pid_path,
                result_path=result_path,
                trigger_path=trigger_path,
                glob_root=root,
            )
            self.assertTrue(live_pid_path.exists())
            self.assertFalse(any("live.pid" in item for item in removed_live))

    def test_is_process_alive(self):
        self.assertTrue(is_process_alive(os.getpid()))
        self.assertFalse(is_process_alive(999999))


if __name__ == "__main__":
    unittest.main()
