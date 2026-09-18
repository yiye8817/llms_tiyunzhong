"""Private log persistence, bounded payloads and optional metadata-only diagnostics."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.diagnostics import LOGGER, PAYLOAD_CHUNK_CHARS, PAYLOAD_MAX_CHARS, configure_logging, error_payload, event, shutdown_logging


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "logs"
        self.terminal = io.StringIO()

    def tearDown(self):
        shutdown_logging()
        self.temporary.cleanup()

    def configure(self, **kwargs):
        return configure_logging(self.directory, self.terminal, **kwargs)

    def test_terminal_and_file_have_identical_json_without_duplicate_handlers(self):
        path = self.configure()
        self.configure()
        event("model.completed", request_id="a-test-id", provider="chatgpt", output_chars=42, duration_ms=12)
        self.assertEqual(path.read_text(), self.terminal.getvalue())
        records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["component"], "backend")
        self.assertEqual(records[0]["output_chars"], 42)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)

    def test_credentials_and_unapproved_payload_fields_never_reach_either_sink(self):
        known = "configured-secret-value"
        path = self.configure(secrets=[known])
        event("request.failed", code=known, path="/tmp/Bearer bearer-secret/token=rawsecret/sk-testsecret012345",
              prompt="private question", messages=[{"content": "private body"}], content="private answer",
              markdown="# private answer", html="<secret>", token="token-secret", cookie="cookie-secret", headers={"Authorization": known})
        contents = path.read_text()
        self.assertEqual(contents, self.terminal.getvalue())
        for forbidden in (known, "bearer-secret", "rawsecret", "sk-testsecret012345", "private question", "private body", "private answer", "cookie-secret", "token-secret", "<secret>"):
            self.assertNotIn(forbidden, contents)
        self.assertIn("[REDACTED]", contents)
        data = json.loads(contents)
        self.assertNotIn("messages", data)
        self.assertNotIn("headers", data)

    def test_explicit_payload_defaults_to_full_content_in_both_sinks(self):
        path = self.configure()
        payload = {"messages": [{"role": "user", "content": "完整问题\n" + "a" * 5000}],
                   "response": {"content": "完整回答", "steps": list(range(150))}}
        with patch.dict(os.environ, {"FUSION_LOG_CONTENT": "1"}):
            event("request.completed", request_id="trace-1", payload=payload)
        self.assertEqual(path.read_text(), self.terminal.getvalue())
        record = json.loads(path.read_text())
        self.assertEqual(json.loads(record["payload"]), payload)
        self.assertEqual(record["request_id"], "trace-1")
        self.assertEqual(record["part"], 1)
        self.assertFalse(record["truncated"])

    def test_metadata_only_opt_out_keeps_steps_and_omits_payload(self):
        path = self.configure()
        with patch.dict(os.environ, {"FUSION_LOG_CONTENT": "0"}):
            event("bridge.dispatch", request_id="trace-1", input_chars=50, payload={"prompt": "PRIVATE INPUT"})
        record = json.loads(path.read_text())
        self.assertEqual(record["event"], "bridge.dispatch")
        self.assertEqual(record["input_chars"], 50)
        self.assertNotIn("payload", record)
        self.assertNotIn("payload_id", record)
        self.assertNotIn("PRIVATE INPUT", path.read_text())
        self.assertEqual(path.read_text(), self.terminal.getvalue())

    def test_payload_redacts_nested_secrets_headers_login_environment_and_urls(self):
        known = "known-secret/+value"
        path = self.configure(secrets=[known])
        payload = {"prompt": 'visible ' + known + ' Bearer bearer-value\npassword="space secret"\n'
                   'https://alice:url-pass@example.com/path?token=query-secret&auth=auth-secret\n'
                   'Cookie: session=raw-cookie; another=other-cookie\n'
                   '{"api_key":"json-secret","message":"still visible"}',
                   "response": {"headers": {"Authorization": "unregistered-header-secret"},
                                "password": "nested-secret", "environment": {"USER": "private-username"},
                                "login": {"name": "private-login"}, "content": "allowed answer"}}
        event("model.completed", payload=payload)
        contents = path.read_text()
        self.assertEqual(contents, self.terminal.getvalue())
        for forbidden in (known, "bearer-value", "space secret", "alice", "url-pass", "query-secret", "auth-secret",
                          "raw-cookie", "other-cookie", "json-secret", "unregistered-header-secret", "nested-secret",
                          "private-username", "private-login"):
            self.assertNotIn(forbidden, contents)
        restored = json.loads(json.loads(contents)["payload"])
        self.assertEqual(restored["response"]["content"], "allowed answer")
        self.assertIn("visible", restored["prompt"])
        self.assertIn("[REDACTED]", restored["prompt"])

    def test_payload_chunks_reassemble_without_loss_or_split_secret_leakage(self):
        path = self.configure(secrets=["split-boundary-secret"])
        payload = {"content": "中\\\n" * 14_000 + "split-boundary-secret" + "z" * 18_000}
        event("bridge.result", request_id="chunked-request", job_id="chunked-job", payload=payload)
        records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertGreater(len(records), 2)
        self.assertEqual(path.read_text(), self.terminal.getvalue())
        self.assertEqual(len({record["payload_id"] for record in records}), 1)
        self.assertEqual([record["part"] for record in records], list(range(1, len(records) + 1)))
        self.assertTrue(all(record["parts"] == len(records) and len(record["payload"]) <= PAYLOAD_CHUNK_CHARS for record in records))
        restored = json.loads("".join(record["payload"] for record in records))
        self.assertEqual(restored["content"], payload["content"].replace("split-boundary-secret", "[REDACTED]"))
        self.assertTrue(all(not record["truncated"] for record in records))

    def test_json_strings_redact_entire_credentials_with_escaped_quotes(self):
        path = self.configure()
        prompt = json.dumps({"password": 'before"PRIVATE_PASSWORD_SUFFIX', "api_key": 'before\\"PRIVATE_KEY_SUFFIX',
                             "content": "keep this answer"})
        event("bridge.dispatch", payload={"prompt": prompt})
        contents = path.read_text()
        self.assertNotIn("PRIVATE_PASSWORD_SUFFIX", contents)
        self.assertNotIn("PRIVATE_KEY_SUFFIX", contents)
        self.assertIn("keep this answer", contents)

    def test_payload_cap_is_explicit_and_bounds_record_count(self):
        path = self.configure(max_bytes=8 * 1024 * 1024)
        event("oversized.result", payload={"content": "x" * (PAYLOAD_MAX_CHARS + 100)})
        records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(sum(len(record["payload"]) for record in records), PAYLOAD_MAX_CHARS)
        self.assertTrue(all(record["truncated"] and record["chars"] > PAYLOAD_MAX_CHARS for record in records))
        self.assertEqual(len(records), 125)
        self.assertEqual(path.read_text(), self.terminal.getvalue())

    def test_nested_exception_cause_retains_message_but_redacts_credentials(self):
        path = self.configure()
        try:
            try:
                raise ValueError("provider invalid_protocol token=never-print")
            except ValueError as cause:
                raise RuntimeError("dispatch failed") from cause
        except RuntimeError as exc:
            event("request.failed", payload=error_payload(exc))
        contents = path.read_text()
        payload = json.loads(json.loads(contents)["payload"])
        self.assertEqual(payload["cause"]["error_type"], "ValueError")
        self.assertIn("invalid_protocol", payload["cause"]["message"])
        self.assertNotIn("never-print", contents)

    def test_unstructured_exception_messages_and_tracebacks_are_omitted(self):
        path = self.configure()
        try:
            raise ValueError("sensitive message from remote website")
        except ValueError:
            LOGGER.exception("request body %s", "private body")
        result = json.loads(path.read_text())
        self.assertEqual(result["event"], "unstructured_log_omitted")
        self.assertNotIn("sensitive", path.read_text())
        self.assertNotIn("private", path.read_text())
        self.assertNotIn("Traceback", path.read_text())

    def test_rotation_has_bounded_files_and_private_permissions(self):
        self.configure(max_bytes=500, backup_count=2)
        for number in range(35):
            event("model.completed", request_id=f"request-{number}", output_chars=500)
        files = sorted(self.directory.glob("backend.log*"))
        self.assertEqual([path.name for path in files], ["backend.log", "backend.log.1", "backend.log.2"])
        for path in files:
            self.assertLessEqual(path.stat().st_size, 500)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for line in path.read_text().splitlines():
                self.assertEqual(json.loads(line)["component"], "backend")
        self.assertIn("request-34", files[0].read_text())

    def test_env_override_is_honored(self):
        with patch.dict(os.environ, {"FUSION_LOG_DIR": str(self.directory)}):
            path = configure_logging(stream=self.terminal)
        self.assertEqual(path, self.directory / "backend.log")

    def test_reconfiguration_closes_old_sink_without_cross_writes(self):
        first = self.configure()
        event("first.event")
        second = configure_logging(Path(self.temporary.name) / "other", self.terminal)
        event("second.event")
        self.assertNotIn("second.event", first.read_text())
        self.assertNotIn("first.event", second.read_text())

    def test_log_symlink_is_refused_without_touching_destination(self):
        self.directory.mkdir()
        target = Path(self.temporary.name) / "target"
        target.write_text("untouched")
        (self.directory / "backend.log").symlink_to(target)
        self.configure()
        self.assertEqual(target.read_text(), "untouched")
        self.assertEqual(json.loads(self.terminal.getvalue())["event"], "log.file_unavailable")

    def test_unwritable_directory_keeps_terminal_logging_and_retries_later(self):
        with patch("backend.diagnostics.Path.mkdir", side_effect=PermissionError("secret error detail")):
            path = self.configure()
        event("backend.ready")
        self.assertFalse(path.exists())
        records = [json.loads(line) for line in self.terminal.getvalue().splitlines()]
        self.assertEqual([record["event"] for record in records], ["log.file_unavailable", "backend.ready"])
        self.assertNotIn("secret error detail", self.terminal.getvalue())
        self.configure()
        event("file.recovered")
        self.assertEqual(json.loads(path.read_text())["event"], "file.recovered")

    def test_unwritable_file_keeps_terminal_logging(self):
        with patch("backend.diagnostics.PrivateRotatingHandler._open", side_effect=PermissionError("not writable")):
            self.configure()
        event("request.completed", output_chars=42)
        records = [json.loads(line) for line in self.terminal.getvalue().splitlines()]
        self.assertEqual([record["event"] for record in records], ["log.file_unavailable", "request.completed"])

    def test_invalid_metadata_values_do_not_break_json_or_leak_messages(self):
        path = self.configure()
        event("model.failed", code="unexpected private website body", state="arbitrary page text", duration_ms=float("nan"))
        data = json.loads(path.read_text())
        self.assertEqual(data["code"], "unrecognized")
        self.assertEqual(data["state"], "unrecognized")
        self.assertNotIn("duration_ms", data)


if __name__ == "__main__":
    unittest.main()
