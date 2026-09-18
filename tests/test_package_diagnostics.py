"""Synthetic profiles only: verify diagnostic archives do not leak login data."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/package_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("package_diagnostics", SCRIPT)
diagnostics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostics)


class DiagnosticPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "project"
        self.data = self.base / "private"
        self.logs = self.root / "logs"
        for directory in (self.root, self.data, self.logs):
            directory.mkdir(parents=True, exist_ok=True)
        self.write(self.root / "package.json", {"name": "fixture", "version": "1.1.2"})
        self.write(self.root / "run.sh", "#!/bin/bash\ntrue\n")
        self.write(self.root / "backend/app.py", "print('fixture')\n")
        self.write(self.data / "config.json", {
            "schema_version": 1,
            "providers": [{"id": "chatgpt", "enabled": True, "url": "https://chatgpt.com/?token=query-secret",
                           "proxy": "http://proxyuser:proxy-password@127.0.0.1:8080", "selectors": {"input": ["#prompt-textarea"]}}],
            "fusion": {"mode": "web", "provider": "chatgpt", "api_key": "fixture-private-api-key"},
            "unexpected_password": "do-not-export-password", "messages": [{"content": "private question"}],
        })
        self.write(self.data / "api-key.txt", "fixture-private-api-token\n")
        environment = patch.dict(os.environ, {"FUSION_TOKEN": "fixture-env-token"})
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content) if isinstance(content, (dict, list)) else content)

    def package(self, include_markdown=False, **kwargs):
        output = kwargs.pop("output", self.root / "diagnostics/report.tar.gz")
        collector = diagnostics.Collector(self.root, self.data, self.logs, include_markdown, output, **kwargs)
        collector.collect().write(output)
        with tarfile.open(output) as archive:
            members = archive.getmembers()
            payload = {member.name.removeprefix("multillm-fusion-diagnostics/"): archive.extractfile(member).read()
                       for member in members}
            self.assertTrue(all(member.isfile() for member in members))
            self.assertTrue(all(member.uid == member.gid == 0 for member in members))
        return output, collector, payload

    def test_archive_contains_allowlisted_code_current_and_legacy_logs_only(self):
        self.write(self.logs / "electron.log", '{"event":"adapter.send","provider":"chatgpt"}\n')
        self.write(self.logs / "electron.log.1", "old diagnostic\n")
        self.write(self.logs / "backend.log", "backend diagnostic\n")
        self.write(self.logs / "launcher.log", "launcher diagnostic\n")
        self.write(self.logs / "login.json", '{"cookies": "never-export-this"}')
        self.write(self.data / "electron.log", "legacy diagnostic\n")
        self.write(self.data / "history.sqlite3", "private conversations")
        self.write(self.root / ".env", "SECRET=env-secret")
        self.write(self.root / "config.json", "private custom config")
        self.write(self.root / "node_modules/pkg/index.js", "dependency")
        self.write(self.root / "backend/__pycache__/bad.py", "compiled cache")
        self.write(self.root / "backend/cookies.py", "private unexpected cookie dump")
        self.write(self.root / "backend/custom.json", '{"cookie": "cookie-secret"}')
        self.write(self.root / "browser-extension/chromium/manifest.json", {"manifest_version": 3})
        output, _, payload = self.package()
        for name in ("code/run.sh", "code/backend/app.py", "code/browser-extension/chromium/manifest.json",
                     "logs/current/electron.log", "logs/current/electron.log.1", "logs/current/backend.log",
                     "logs/current/launcher.log", "logs/legacy/electron.log", "config-summary.json", "environment.json"):
            self.assertIn(name, payload)
        all_bytes = b"\n".join(payload.values())
        for secret in (b"never-export-this", b"private conversations", b"env-secret", b"private custom config", b"cookie-secret"):
            self.assertNotIn(secret, all_bytes)
        self.assertFalse(any("node_modules" in name or "__pycache__" in name or "cookies.py" in name for name in payload))
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)

    def test_runtime_secrets_url_credentials_and_structured_conversation_are_redacted(self):
        entries = [
            {"event": "failure", "api_key": "unknown-key", "cookie": "session=secret-cookie", "messages": [{"content": "private dialogue"}],
             "nested": {"markdown": "private answer", "prompt": "private prompt"}, "code": "timeout", "count": 3},
            {"event": "send", "details": "fixture-private-api-key fixture-private-api-token fixture-env-token proxy-password query-secret"},
        ]
        raw = "\n".join(json.dumps(entry) for entry in entries) + "\n"
        raw += 'Bearer unknown-bearer\nCookie: session=unknown-session; second=value\n'
        raw += 'url=https://otheruser:otherpass@example.com/path?session=unregistered-secret&x=abc#private-fragment\n'
        raw += 'api_key="plain-api-secret"\nprompt: plain-private-prompt\n'
        self.write(self.logs / "backend.log", raw)
        _, _, payload = self.package()
        runtime = b"\n".join(value for key, value in payload.items() if not key.startswith("code/"))
        for secret in (b"unknown-key", b"secret-cookie", b"private dialogue", b"private answer", b"private prompt",
                       b"fixture-private-api-key", b"fixture-private-api-token", b"fixture-env-token", b"proxy-password",
                       b"query-secret", b"unknown-bearer", b"unknown-session", b"otheruser", b"otherpass", b"unregistered-secret",
                       b"private-fragment", b"plain-api-secret", b"plain-private-prompt", b"do-not-export-password"):
            self.assertNotIn(secret, runtime)
        self.assertIn(b'"code": "timeout"', payload["logs/current/backend.log"])
        summary = json.loads(payload["config-summary.json"])
        self.assertTrue(summary["fusion"]["api_key_configured"])
        self.assertTrue(summary["providers"][0]["proxy_configured"])
        self.assertEqual(summary["providers"][0]["selectors"]["input"], ["#prompt-textarea"])
        self.assertNotIn("api_key", summary["fusion"])

    def test_auto_started_parent_logs_are_collected_and_redacted_without_process_lock(self):
        self.write(self.logs / "agent-parent-start.log", json.dumps({
            "event": "adapter.complete", "payload": {"markdown": "MODEL-PRIVATE-ANSWER"},
            "api_key": "fixture-private-api-token"}) + "\n")
        self.write(self.logs / "agent-parent-start.log.1", "startup failure: no display\n")
        self.write(self.logs / "agent-parent-8765.lock", "PROCESS-PRIVATE-IDENTITY")
        _, _, payload = self.package()
        log = payload["logs/current/agent-parent-start.log"]
        self.assertIn(b"adapter.complete", log)
        self.assertNotIn(b"MODEL-PRIVATE-ANSWER", log)
        self.assertNotIn(b"fixture-private-api-token", log)
        self.assertIn(b"startup failure", payload["logs/current/agent-parent-start.log.1"])
        self.assertNotIn(b"PROCESS-PRIVATE-IDENTITY", b"\n".join(payload.values()))
        _, _, full = self.package(include_content=True, output=self.root / "diagnostics/parent-full.tar.gz")
        self.assertIn(b"MODEL-PRIVATE-ANSWER", full["logs/current/agent-parent-start.log"])
        self.assertNotIn(b"fixture-private-api-token", full["logs/current/agent-parent-start.log"])

    def test_agent_code_and_audit_are_collected_without_sessions_workspace_or_credentials(self):
        root = self.root / "desktop-agent"
        self.write(root / "src/fusion_agent/runtime.py", "# agent fixture\n")
        self.write(root / "skills/system-report/SKILL.md", "---\nname: system-report\ndescription: fixture\n---\n")
        self.write(root / "requirements-browser.txt", "playwright>=1.50,<2\n")
        self.write(root / "config.example.json", {"model": "chatgpt"})
        self.write(root / ".runtime/logs/agent.log", '{"event":"tool.completed","tool":"files.read"}\n')
        self.write(root / "agent.config.json", {"workspace": "workspace", "api_key_file": "AGENT_PRIVATE_CONTENT"})
        for name in (".runtime/runs/task/transcript.json", ".runtime/browser-profile/Cookies", "workspace/private.md", "artifacts/secret.md"):
            self.write(root / name, "AGENT_PRIVATE_CONTENT")
        _, _, payload = self.package()
        for name in ("code/desktop-agent/src/fusion_agent/runtime.py", "code/desktop-agent/skills/system-report/SKILL.md", "code/desktop-agent/requirements-browser.txt", "code/desktop-agent/config.example.json", "logs/agent/agent.log"):
            self.assertIn(name, payload)
        self.assertNotIn(b"AGENT_PRIVATE_CONTENT", b"\n".join(payload.values()))

    def test_custom_agent_private_directories_are_never_treated_as_source(self):
        root = self.root / "desktop-agent"
        self.write(root / "src/fusion_agent/runtime.py", "# expected source")
        self.write(root / "agent.config.json", {"workspace": "docs/personal", "runtime_dir": "src/history"})
        for name in ("tasks/private.md", "runtime/runs/test/final.md", "docs/personal/notes.md", "src/history/runs/test/final.md", "skills/custom-runtime/final.md"):
            self.write(root / name, "CUSTOM_AGENT_PRIVATE_DATA")
        self.write(root / "skills/custom-runtime/logs/agent.log", '{"event":"agent.started"}\n')
        _, _, payload = self.package(agent_runtime_dir=root / "skills/custom-runtime")
        self.assertIn("code/desktop-agent/src/fusion_agent/runtime.py", payload)
        self.assertIn("logs/agent/agent.log", payload)
        self.assertNotIn(b"CUSTOM_AGENT_PRIVATE_DATA", b"\n".join(payload.values()))

    def test_invalid_agent_config_skips_agent_sources_without_exposing_contents(self):
        root = self.root / "desktop-agent"
        self.write(root / "agent.config.json", "{malformed")
        self.write(root / "src/private.py", "PRIVATE_UNKNOWN_PATH")
        _, collector, payload = self.package()
        self.assertNotIn(b"PRIVATE_UNKNOWN_PATH", b"\n".join(payload.values()))
        self.assertIn("private_paths_unknown_agent_sources_skipped", [item["reason"] for item in collector.warnings])

    def test_markdown_requires_explicit_flag_and_never_includes_database(self):
        self.write(self.data / "runs/request/merged.md", "# private user content\nBearer markdown-token\n")
        self.write(self.data / "runs/request/history.sqlite3", "database")
        _, _, default = self.package(output=self.root / "default.tar.gz")
        self.assertFalse(any(name.startswith("markdown/") for name in default))
        _, _, selected = self.package(True, output=self.root / "selected.tar.gz")
        self.assertIn("markdown/request/merged.md", selected)
        self.assertIn(b"private user content", selected["markdown/request/merged.md"])
        self.assertNotIn(b"markdown-token", selected["markdown/request/merged.md"])
        self.assertFalse(any("sqlite" in name for name in selected))

    def test_symlink_files_directories_and_output_ancestors_are_not_followed(self):
        outside = self.base / "outside"
        self.write(outside / "hidden.py", "outside-secret")
        (self.root / "backend/leak.py").symlink_to(outside / "hidden.py")
        (self.root / "ui").symlink_to(outside, target_is_directory=True)
        (self.logs / "electron.log").symlink_to(outside / "hidden.py")
        (self.data / "runs").symlink_to(outside, target_is_directory=True)
        _, collector, payload = self.package(True)
        self.assertNotIn(b"outside-secret", b"\n".join(payload.values()))
        self.assertTrue(any("symlink" in warning["reason"] for warning in collector.warnings))
        (self.root / "linked-output").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            collector.write(self.root / "linked-output/report.tar.gz")

    def test_existing_output_is_never_overwritten_or_packed_even_under_source_directory(self):
        output = self.root / "scripts/report.py"
        self.write(output, "existing diagnostic archive bytes")
        collector = diagnostics.Collector(self.root, self.data, self.logs, output=output).collect()
        self.assertFalse(any(name == "code/scripts/report.py" for name, *_ in collector.entries))
        with self.assertRaises(FileExistsError):
            collector.write(output)
        self.assertEqual(output.read_text(), "existing diagnostic archive bytes")

    def test_empty_logs_and_missing_or_corrupt_config_still_produce_manifest(self):
        (self.data / "config.json").write_text("{broken")
        _, collector, payload = self.package()
        summary = json.loads(payload["config-summary.json"])
        self.assertFalse(summary["available"])
        self.assertIn("no_logs_found", [item["reason"] for item in collector.warnings])
        self.assertIn("invalid_config", [item["reason"] for item in collector.warnings])
        manifest = json.loads(payload["manifest.json"])
        for item in manifest["files"]:
            self.assertEqual(item["sha256"], hashlib.sha256(payload[item["path"]]).hexdigest())
            self.assertEqual(item["bytes"], len(payload[item["path"]]))

    def test_read_limits_skip_large_sources_and_take_complete_log_tail(self):
        collector = diagnostics.Collector(self.root, self.data, self.logs)
        huge = self.root / "backend/big.py"
        self.write(huge, "1234567890\n" * 10)
        self.assertIsNone(collector.read(huge, "code/backend/big.py", limit=20))
        tail = collector.read(huge, "logs/current/backend.log", limit=25, tail=True)
        self.assertEqual(tail, b"1234567890\n1234567890\n")
        reasons = {item["reason"] for item in collector.warnings}
        self.assertIn("file_size_limit_skipped", reasons)
        self.assertIn("log_tail_only", reasons)

    def test_known_secret_literals_are_removed_from_code(self):
        self.write(self.root / "backend/custom.py", 'api_key = "literal-secret-in-source"\nprint("fixture-private-api-key")\n')
        _, _, payload = self.package()
        self.assertNotIn(b"literal-secret-in-source", payload["code/backend/custom.py"])
        self.assertNotIn(b"fixture-private-api-key", payload["code/backend/custom.py"])

    def test_content_chunks_are_removed_by_default_and_can_be_included_explicitly(self):
        original = json.dumps({"prompt": "USER TASK TO DEBUG", "reply": "MODEL INVALID JSON", "arguments": {"command": "python3 --version"}}, ensure_ascii=False)
        records = [{"event": "model.raw_reply", "payload_id": "p1", "part": i+1, "parts": 2, "payload": chunk}
                   for i, chunk in enumerate((original[:40], original[40:]))]
        self.write(self.root / "desktop-agent/.runtime/logs/agent.log", '\n'.join(json.dumps(row) for row in records) + '\n')
        _, _, ordinary = self.package(output=self.root / "ordinary.tar.gz")
        self.assertNotIn(b"USER TASK", ordinary["logs/agent/agent.log"])
        self.assertNotIn(b"INVALID JSON", ordinary["logs/agent/agent.log"])
        _, _, included = self.package(output=self.root / "content.tar.gz", include_content=True)
        rows = [json.loads(line) for line in included["logs/agent/agent.log"].decode().splitlines()]
        self.assertEqual(json.loads(''.join(row["payload"] for row in rows)), json.loads(original))
        self.assertTrue(json.loads(included["manifest.json"])["include_content"])

    def test_content_option_still_removes_known_tokens_and_never_collects_agent_sessions(self):
        self.write(self.root / "desktop-agent/.runtime/logs/agent.log", json.dumps({"event": "model.reply", "payload": 'task fixture-private-api-token', "authorization": 'Bearer other-token'}) + '\n')
        self.write(self.root / "desktop-agent/.runtime/runs/private/transcript.json", "DO NOT COLLECT PRIVATE RUN")
        _, _, payload = self.package(include_content=True)
        logs = payload["logs/agent/agent.log"]
        self.assertIn(b"task", logs)
        self.assertNotIn(b"fixture-private-api-token", logs)
        self.assertNotIn(b"other-token", logs)
        self.assertNotIn(b"DO NOT COLLECT PRIVATE RUN", b'\n'.join(payload.values()))

    def test_selected_agent_run_events_are_opt_in_and_follow_content_redaction(self):
        runtime = self.base / "agent-runtime"
        run_id = "20260909T032443Z-69d70e4c"
        self.write(runtime / "runs" / run_id / "events.jsonl", json.dumps({
            "event": "model.raw_reply", "run_id": run_id,
            "payload": "MODEL REPLY fixture-private-api-token"}) + "\n")
        for name in (f"runs/{run_id}/transcript.json", f"runs/{run_id}/result.md", "runs/other/events.jsonl"):
            self.write(runtime / name, "UNSELECTED PRIVATE FILE")
        name = f"logs/agent/runs/{run_id}/events.jsonl"
        _, _, ordinary = self.package(agent_runtime_dir=runtime, output=self.root / "ordinary.tar.gz")
        self.assertNotIn(name, ordinary)
        _, _, selected = self.package(agent_runtime_dir=runtime, agent_run_ids=[run_id], output=self.root / "selected.tar.gz")
        self.assertIn(b"model.raw_reply", selected[name])
        self.assertNotIn(b"MODEL REPLY", selected[name])
        _, _, included = self.package(agent_runtime_dir=runtime, agent_run_ids=[run_id, run_id], include_content=True)
        self.assertIn(b"MODEL REPLY", included[name])
        self.assertNotIn(b"fixture-private-api-token", included[name])
        self.assertNotIn(b"UNSELECTED PRIVATE FILE", b"\n".join(included.values()))
        self.assertEqual(json.loads(included["manifest.json"])["agent_run_ids"], [run_id])

    def test_agent_run_id_cannot_escape_runtime_and_symlink_run_is_skipped(self):
        for value in ("../outside", "/tmp/run", "a/b", "..", "a" * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                diagnostics.Collector(self.root, self.data, self.logs, agent_run_ids=[value])
        run_root = self.root / "desktop-agent/.runtime/runs"
        outside = self.base / "outside"
        self.write(outside / "events.jsonl", "NEVER EXPORT")
        run_root.mkdir(parents=True)
        (run_root / "linked").symlink_to(outside, target_is_directory=True)
        _, collector, payload = self.package(agent_run_ids=["linked", "missing"])
        self.assertNotIn(b"NEVER EXPORT", b"\n".join(payload.values()))
        self.assertIn("symlink_skipped", [warning["reason"] for warning in collector.warnings])
        self.assertIn("file_not_found", [warning["reason"] for warning in collector.warnings])


if __name__ == "__main__":
    unittest.main()
