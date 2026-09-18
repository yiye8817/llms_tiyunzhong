import io
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fusion_agent.audit import Audit


class AuditRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "logs" / "agent.log"
        self.run_path = self.root / "runs" / "example-run" / "events.jsonl"
        self.stream = io.StringIO()

    def make_audit(self, **kwargs):
        audit = Audit(self.path, stream=self.stream, run_path=self.run_path, **kwargs)
        self.addCleanup(audit.close)
        return audit

    def assert_fallback_records(self, expected_events):
        terminal = self.stream.getvalue()
        self.assertNotIn('"event":', terminal)
        records = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["event"] for item in records], expected_events)
        return records

    def test_same_sanitized_records_are_immediately_readable_in_both_files_and_terminal_is_quiet(self):
        self.run_path.parent.mkdir(parents=True, mode=0o755)
        audit = self.make_audit(secrets=("sample-api-key",), chunk_chars=32)
        self.assertEqual(audit.run_path, self.run_path)
        self.assertTrue(audit.run_log_available)
        self.assertEqual(stat.S_IMODE(self.run_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.run_path.parent.stat().st_mode), 0o700)
        self.assertEqual(audit.handler.maxBytes, 5 * 1024 * 1024)
        self.assertEqual(audit.handler.backupCount, 3)
        payload = {"content": "逐步响应 " * 20, "api_key": "sample-api-key"}
        audit("model.response", {"run_id": "example-run", "payload": payload})
        # Read before close: every emitted chunk must already have been flushed.
        self.assertEqual(self.stream.getvalue(), "")
        saved = self.path.read_text(encoding="utf-8")
        self.assertEqual(saved, self.run_path.read_text(encoding="utf-8"))
        self.assertNotIn("sample-api-key", saved)
        records = [json.loads(line) for line in saved.splitlines()]
        self.assertGreater(len(records), 1)
        restored = json.loads("".join(item["payload"] for item in records))
        self.assertEqual(restored["content"], payload["content"])
        self.assertEqual(restored["api_key"], "[REDACTED]")

    def test_existing_run_file_is_preserved_and_failure_warns_in_both_other_sinks(self):
        self.run_path.parent.mkdir(parents=True)
        self.run_path.write_text("original run data", encoding="utf-8")
        audit = self.make_audit()
        self.assertFalse(audit.run_log_available)
        audit("run.started", {"step": 0})
        records = self.assert_fallback_records(["audit.run_log_failed", "run.started"])
        self.assertEqual(self.run_path.read_text(), "original run data")
        details = json.loads(records[0]["payload"])
        self.assertEqual(details["path"], str(self.run_path))
        self.assertIn("不可写", details["message"])
        self.assertEqual(self.stream.getvalue().count("不可写"), 1)

    def test_leaf_symlink_target_is_never_modified(self):
        self.run_path.parent.mkdir(parents=True)
        target = self.root / "original.txt"
        target.write_text("preserve me", encoding="utf-8")
        self.run_path.symlink_to(target)
        audit = self.make_audit()
        self.assertFalse(audit.run_log_available)
        self.assertTrue(self.run_path.is_symlink())
        self.assertEqual(target.read_text(), "preserve me")
        self.assert_fallback_records(["audit.run_log_failed"])

    def test_parent_symlink_does_not_create_file_in_target(self):
        self.run_path.parent.parent.mkdir(parents=True)
        target = self.root / "target-dir"
        target.mkdir()
        self.run_path.parent.symlink_to(target, target_is_directory=True)
        audit = self.make_audit()
        self.assertFalse(audit.run_log_available)
        self.assertFalse((target / "events.jsonl").exists())
        self.assert_fallback_records(["audit.run_log_failed"])

    def test_unusable_parent_falls_back_without_interrupting_events(self):
        self.run_path.parent.parent.mkdir(parents=True)
        self.run_path.parent.write_text("not a directory", encoding="utf-8")
        audit = self.make_audit()
        self.assertFalse(audit.run_log_available)
        audit("model.response", {"payload": {"content": "response remains available"}})
        self.assert_fallback_records(["audit.run_log_failed", "model.response"])
        self.assertEqual(self.run_path.parent.read_text(), "not a directory")

    def test_write_failure_disables_only_run_sink_and_warns_once(self):
        audit = self.make_audit()
        audit._run_handle.close()
        broken = Mock()
        broken.write.side_effect = OSError("disk full")
        audit._run_handle = broken
        audit("tool.started", {"tool": "browser.open"})
        self.assertFalse(audit.run_log_available)
        audit("tool.completed", {"ok": True})
        audit("agent.finished", {"status": "completed"})
        self.assert_fallback_records(["tool.started", "audit.run_log_failed", "tool.completed", "agent.finished"])
        broken.write.assert_called_once()
        broken.close.assert_called_once()
        self.assertEqual(self.stream.getvalue().count("不可写"), 1)

    def test_flush_failure_falls_back_with_same_warning(self):
        audit = self.make_audit()
        handle = audit._run_handle
        broken = Mock(wraps=handle)
        broken.flush.side_effect = OSError("flush failed")
        audit._run_handle = broken
        audit("model.response", {"response_chars": 4})
        self.assertFalse(audit.run_log_available)
        self.assert_fallback_records(["model.response", "audit.run_log_failed"])
        self.assertTrue(handle.closed)

    def test_metadata_only_warning_remains_clear(self):
        self.run_path.parent.mkdir(parents=True)
        self.run_path.write_text("original", encoding="utf-8")
        audit = self.make_audit(content=False)
        self.assertFalse(audit.run_log_available)
        records = self.assert_fallback_records(["audit.run_log_failed"])
        self.assertIn("不可写", records[0]["reason"])
        self.assertTrue(records[0]["payload_omitted"])

    def test_optional_run_path_keeps_legacy_constructor_working(self):
        audit = Audit(self.path, (), self.stream)
        self.addCleanup(audit.close)
        self.assertIsNone(audit.run_path)
        self.assertFalse(audit.run_log_available)
        audit("agent.finished", {"status": "completed"})
        self.assert_fallback_records(["agent.finished"])
        self.assertEqual(self.stream.getvalue(), "")

    def test_global_write_error_warns_once_without_logging_traceback_or_payload(self):
        audit = self.make_audit()
        original = audit.handler.stream
        broken = Mock(wraps=original)
        broken.write.side_effect = OSError("secret disk payload must not be printed")
        audit.handler.stream = broken
        for _ in range(2):
            audit("model.raw_reply", {"payload": {"reply": "private model answer"}})
        self.assertFalse(audit.global_log_available)
        self.assertTrue(audit.run_log_available)
        warning = self.stream.getvalue()
        self.assertEqual(warning.count("不可写"), 1)
        for private in ("Traceback", "private model answer", "secret disk payload", '"event"'):
            self.assertNotIn(private, warning)
        self.assertEqual(len(self.run_path.read_text().splitlines()), 2)
        self.assertIn("private model answer", self.run_path.read_text())

    def test_closed_terminal_never_prevents_file_logging(self):
        self.stream.close()
        audit = self.make_audit()
        audit("model.response", {"payload": {"content": "saved despite closed terminal"}})
        self.assertEqual(self.path.read_text(), self.run_path.read_text())
        self.assertIn("saved despite closed terminal", self.path.read_text())

    def test_concurrent_events_keep_complete_chunk_groups_identical_in_both_files(self):
        audit = self.make_audit(chunk_chars=32)
        first_global_chunk = threading.Event()
        second_attempted = threading.Event()
        second_finished = threading.Event()
        failures = []
        emit = audit.handler.emit

        def allow_competing_writer(record):
            emit(record)
            row = json.loads(record.getMessage())
            if row["step"] == 1 and row["part"] == 1:
                first_global_chunk.set()
                if not second_attempted.wait(2):
                    raise AssertionError("second writer did not start")
                # Without an event-wide lock the other writer can finish here,
                # between this event's global and per-run first chunk writes.
                second_finished.wait(.05)

        def write(step):
            try:
                if step == 2:
                    if not first_global_chunk.wait(2):
                        raise AssertionError("first writer did not start")
                    second_attempted.set()
                audit("model.response", {"step": step, "payload": {"content": f"response-{step} " * 30}})
            except BaseException as exc:
                failures.append(exc)
            finally:
                if step == 2:
                    second_finished.set()

        threads = [threading.Thread(target=write, args=(step,), daemon=True) for step in (1, 2)]
        with patch.object(audit.handler, "emit", side_effect=allow_competing_writer):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
                self.assertFalse(thread.is_alive(), "concurrent log write did not finish")
        self.assertEqual(failures, [])
        saved = self.path.read_text(encoding="utf-8")
        self.assertEqual(saved, self.run_path.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in saved.splitlines()]
        self.assertEqual([row["step"] for row in rows], sorted(row["step"] for row in rows))
        for step in (1, 2):
            group = [row for row in rows if row["step"] == step]
            self.assertGreater(len(group), 1)
            self.assertEqual(len({row["payload_id"] for row in group}), 1)
            self.assertEqual([row["part"] for row in group], list(range(1, group[0]["parts"] + 1)))
            self.assertEqual(json.loads("".join(row["payload"] for row in group)),
                             {"content": f"response-{step} " * 30})
        self.assertEqual(self.stream.getvalue(), "")

    def test_delayed_progress_after_close_does_not_reopen_or_change_either_log(self):
        audit = self.make_audit()
        audit("agent.finished", {"status": "completed", "steps": 1})
        before = self.path.read_bytes()
        release = threading.Event()
        failures = []

        def delayed_callback():
            release.wait(2)
            try:
                audit("model.web_progress", {"stage": "completed", "payload": {"content": "late body"}})
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=delayed_callback, daemon=True)
        thread.start()
        audit.close()
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.run_path.read_bytes(), before)
        self.assertIsNone(audit.handler.stream)
        self.assertEqual(self.stream.getvalue(), "")

    def test_web_progress_and_alias_metadata_survive_without_content_logging(self):
        audit = self.make_audit(content=False)
        progress = {"run_id": "example-run", "request_id": "request-one", "progress_id": "progress-one",
                    "stage": "server_responded", "provider": "qwen", "purpose": "candidate",
                    "sequence": 4, "http_status": 503}
        audit("model.web_progress", {**progress, "payload": {"content": "private response"}})
        audit("tool.alias_resolved", {"original_tool": "file.read", "resolved_tool": "files.read",
                                      "tool": "files.read", "payload": {"arguments": {"path": "private path"}}})
        saved = self.path.read_text(encoding="utf-8")
        self.assertEqual(saved, self.run_path.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in saved.splitlines()]
        self.assertEqual({key: rows[0].get(key) for key in progress}, progress)
        self.assertEqual(rows[1]["original_tool"], "file.read")
        self.assertEqual(rows[1]["resolved_tool"], "files.read")
        self.assertTrue(all(row["payload_omitted"] for row in rows))
        self.assertNotIn("private", saved)
        self.assertEqual(self.stream.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
