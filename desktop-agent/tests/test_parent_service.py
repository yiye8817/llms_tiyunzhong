import concurrent.futures
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fusion_agent.config import Settings, api_key
from fusion_agent import config
from fusion_agent import parent_service as service


@contextmanager
def api_fixture(*, health=None, status=None, status_code=200, health_code=200):
    calls = []
    state = {"bridge_connected": True, "providers": {}, "busy": False} if status is None else status
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            code = health_code if self.path == "/health" else status_code
            self.send_response(code)
            self.end_headers()
            self.wfile.write(json.dumps(({"status": "ok"} if health is None else health)
                                        if self.path == "/health" else state).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {"FUSION_PORT": str(server.server_port), "FUSION_AGENT_API_KEY": "fixture-private-key"}):
            yield Settings(base_url=f"http://127.0.0.1:{server.server_port}/v1"), calls, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class ParentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "parent 中文 $(literal)"
        (self.project / "electron").mkdir(parents=True)
        (self.project / "electron/main.cjs").write_text("fixture marker")
        self.processes = []
        self.addCleanup(self.clean_processes)
        self.environment = patch.dict(os.environ, {"FUSION_LOG_DIR": str(self.project / "logs"),
                                                   "FUSION_AGENT_API_KEY": "fixture-private-key"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root_patch = patch.object(service, "_project_root", return_value=self.project)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        original_launch = service._launch
        def recorded_launch(*arguments):
            process = original_launch(*arguments)
            self.processes.append(process)
            return process
        self.launch_patch = patch.object(service, "_launch", side_effect=recorded_launch)
        self.launch = self.launch_patch.start()
        self.addCleanup(self.launch_patch.stop)
        # The hosted test executor virtualizes PIDs while exposing its host's
        # /proc. Exercise real processes/sockets and use their Popen liveness
        # here instead of accidentally reading an unrelated host PID's stat.
        def fixture_identity(pid):
            process = next((item for item in self.processes if item.pid == pid), None)
            return f"fixture-start-{pid}" if process and process.poll() is None else None
        identity = patch.object(service, "_process_identity", side_effect=fixture_identity)
        identity.start()
        self.addCleanup(identity.stop)

    def clean_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def launcher(self, body):
        script = self.project / "fixture.py"
        script.write_text(body)
        (self.project / "run.sh").write_text('#!/usr/bin/env bash\nexec "$PARENT_TEST_PYTHON" "$PARENT_TEST_SCRIPT" "$@"\n')
        os.environ["PARENT_TEST_PYTHON"] = sys.executable
        os.environ["PARENT_TEST_SCRIPT"] = str(script)

    def free_endpoint(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        os.environ["FUSION_PORT"] = str(port)
        return Settings(base_url=f"http://127.0.0.1:{port}/v1")

    def server_launcher(self):
        self.launcher('''import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
assert sys.argv[1:] == ["--skip-install"]
with Path("launch-count.txt").open("a") as output:
    output.write("started\\n")
print("private parent startup detail", flush=True)
time.sleep(0.2)
start = time.monotonic()
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path == "/health":
            body = {"status": "ok"}
        else:
            assert self.headers.get("Authorization") == "Bearer fixture-private-key"
            body = {"bridge_connected": time.monotonic() - start > 0.2, "providers": {}, "busy": False}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
HTTPServer(("127.0.0.1", int(os.environ["FUSION_PORT"])), Handler).serve_forever()
''')

    def test_ready_service_is_reused_only_after_authenticated_bridge_probe(self):
        with api_fixture() as (settings, calls, _):
            report = service.ensure_parent(settings)
        self.assertEqual(report["state"], "ready")
        self.assertFalse(report["web_progress_supported"])
        self.assertEqual(calls, [("/health", None), ("/internal/status", "Bearer fixture-private-key")])
        self.launch.assert_not_called()

    def test_web_progress_requires_explicit_parent_capability(self):
        state = {"bridge_connected": True, "providers": {}, "busy": False,
                 "service": "multillm-fusion", "capabilities": {"web_progress": 1}}
        with api_fixture(status=state) as (settings, _, _):
            self.assertTrue(service.ensure_parent(settings)["web_progress_supported"])
            state["service"] = "another-app"
            self.assertFalse(service.ensure_parent(settings)["web_progress_supported"])

    def test_real_detached_launcher_waits_for_bridge_and_preserves_private_logs(self):
        settings = self.free_endpoint()
        self.server_launcher()
        progress = []
        events = []
        report = service.ensure_parent(settings, timeout=5, progress=progress.append,
                                       event=lambda name, fields: events.append(name))
        self.assertEqual(report["state"], "ready")
        self.assertTrue(report["started"])
        self.assertIn("parent.ready", events)
        self.assertNotIn("private parent startup detail", "\n".join(progress))
        log = Path(report["log_path"])
        self.assertNotIn(str(log), "\n".join(progress))
        self.assertIn("private parent startup detail", log.read_text())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        self.assertEqual(service.ensure_parent(settings)["state"], "ready")
        self.launch.assert_called_once()
        self.assertEqual((self.project / "launch-count.txt").read_text(), "started\n")

    def test_concurrent_agents_share_one_actual_launcher(self):
        settings = self.free_endpoint()
        self.server_launcher()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(service.ensure_parent, settings, timeout=5) for _ in range(2)]
            reports = [future.result(timeout=6) for future in futures]
        self.assertTrue(all(report["state"] == "ready" for report in reports))
        self.launch.assert_called_once()
        self.assertEqual((self.project / "launch-count.txt").read_text(), "started\n")

    def test_api_without_bridge_starts_desktop_instead_of_accepting_health_alone(self):
        marker = self.project / "bridge-ready"
        self.launcher('import pathlib, time\npathlib.Path("bridge-ready").write_text("ready")\ntime.sleep(10)\n')
        with api_fixture(status={"bridge_connected": False, "providers": {}, "busy": False}) as (settings, _, state):
            def ready_when_launched():
                for _ in range(100):
                    if marker.exists():
                        state["bridge_connected"] = True
                        return
                    time.sleep(0.01)
            thread = threading.Thread(target=ready_when_launched)
            thread.start()
            report = service.ensure_parent(settings, timeout=3)
            thread.join(2)
        self.assertEqual(report["state"], "ready")
        self.launch.assert_called_once()

    def test_authentication_error_does_not_spawn_or_retry_a_task(self):
        with api_fixture(status_code=401) as (settings, calls, _):
            with self.assertRaises(service.ParentServiceError) as raised:
                service.ensure_parent(settings)
            self.assertEqual(raised.exception.code, "parent_auth_error")
            self.assertEqual(len(calls), 2)
        self.launch.assert_not_called()

    def test_unknown_service_and_http_errors_never_start_another_process(self):
        cases = [{"health_code": 503}, {"health_code": 302}, {"health": {"status": "another-app"}},
                 {"status": {"ready": True}}, {"status_code": 404}]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), api_fixture(**kwargs) as (settings, _, _):
                with self.assertRaises(service.ParentServiceError) as raised:
                    service.ensure_parent(settings)
                self.assertEqual(raised.exception.code, "parent_unexpected_service")
        self.launch.assert_not_called()

    def test_remote_and_custom_local_endpoints_are_not_probed_or_started(self):
        with patch.dict(os.environ, {"FUSION_PORT": "8765"}), patch.object(service, "_json_get") as get:
            for base in ("https://example.test/v1", "http://127.0.0.1:9000/v1", "http://127.0.0.1:8765/other/v1"):
                self.assertEqual(service.ensure_parent(Settings(base_url=base))["state"], "external")
            get.assert_not_called()
        self.launch.assert_not_called()

    def test_no_auto_start_reports_offline_without_creating_launch_files(self):
        with self.assertRaises(service.ParentServiceError) as raised:
            service.ensure_parent(self.free_endpoint(), auto_start=False)
        self.assertEqual(raised.exception.code, "parent_not_ready")
        self.assertFalse((self.project / "logs").exists())
        self.launch.assert_not_called()

    def test_read_only_status_never_launches_or_creates_files(self):
        self.assertEqual(service.parent_status(self.free_endpoint())["state"], "offline")
        self.assertFalse((self.project / "logs").exists())
        self.launch.assert_not_called()

    def test_unknown_connection_failure_is_not_treated_as_connection_refused(self):
        with patch.object(service, "_json_get", return_value={"kind": "unreachable"}):
            with self.assertRaises(service.ParentServiceError) as raised:
                service.ensure_parent(self.free_endpoint())
        self.assertEqual(raised.exception.code, "parent_unreachable")
        self.launch.assert_not_called()

    def test_launcher_failure_is_reported_with_log_path_and_return_code(self):
        self.launcher('import sys\nprint("fixture launch error", file=sys.stderr)\nsys.exit(7)\n')
        with self.assertRaises(service.ParentServiceError) as raised:
            service.ensure_parent(self.free_endpoint(), timeout=3)
        self.assertEqual(raised.exception.code, "parent_start_failed")
        self.assertEqual(raised.exception.details["returncode"], 7)
        self.assertIn("agent-parent-start.log", str(raised.exception))
        self.assertIn("fixture launch error", Path(raised.exception.details["log_path"]).read_text())

    def test_timeout_keeps_known_process_and_next_call_does_not_spawn_again(self):
        self.launcher('import time\ntime.sleep(10)\n')
        settings = self.free_endpoint()
        for _ in range(2):
            with self.assertRaises(service.ParentServiceError) as raised:
                service.ensure_parent(settings, timeout=0.15)
            self.assertEqual(raised.exception.code, "parent_start_timeout")
        self.launch.assert_called_once()
        self.assertIsNone(self.processes[0].poll())

    def test_symlink_launch_log_cannot_overwrite_another_file(self):
        settings = self.free_endpoint()
        self.server_launcher()
        directory = self.project / "logs"
        directory.mkdir()
        victim = self.project / "untouched"
        victim.write_text("private")
        (directory / "agent-parent-start.log").symlink_to(victim)
        with self.assertRaises(service.ParentServiceError):
            service.ensure_parent(settings, timeout=1)
        self.assertEqual(victim.read_text(), "private")
        self.assertEqual(self.processes, [])

    def test_unreadable_process_identity_does_not_cause_a_duplicate_after_timeout(self):
        self.launcher('import time\ntime.sleep(10)\n')
        settings = self.free_endpoint()
        with patch.object(service, "_process_identity", return_value=None):
            for _ in range(2):
                with self.assertRaises(service.ParentServiceError) as raised:
                    service.ensure_parent(settings, timeout=0.1)
                self.assertEqual(raised.exception.code, "parent_start_timeout")
        self.launch.assert_called_once()
        self.assertIsNone(self.processes[0].poll())

    def test_stale_pid_record_is_not_treated_as_the_current_launcher(self):
        settings = self.free_endpoint()
        self.server_launcher()
        directory = self.project / "logs"
        directory.mkdir()
        (directory / f"agent-parent-{os.environ['FUSION_PORT']}.lock").write_text(
            json.dumps({"pid": 123456789, "identity": "old-process-start", "log_path": "unused"}))
        report = service.ensure_parent(settings, timeout=5)
        self.assertEqual(report["state"], "ready")
        self.launch.assert_called_once()

    def test_large_previous_start_log_is_rotated_before_new_process(self):
        settings = self.free_endpoint()
        self.launcher('import sys\nprint("new startup output")\nsys.exit(9)\n')
        directory = self.project / "logs"
        directory.mkdir()
        log = directory / "agent-parent-start.log"
        log.write_text("x" * (5 * 1024 * 1024))
        with self.assertRaises(service.ParentServiceError):
            service.ensure_parent(settings, timeout=2)
        self.assertIn("new startup output", log.read_text())
        previous = directory / "agent-parent-start.log.1"
        self.assertEqual(previous.stat().st_size, 5 * 1024 * 1024)
        self.assertEqual(previous.stat().st_mode & 0o777, 0o600)

    def test_single_instance_handoff_waits_for_existing_desktop_bridge(self):
        self.launcher('print("Existing application owns the instance lock")\n')
        with api_fixture(status={"bridge_connected": False, "providers": {}, "busy": False}) as (settings, _, state):
            def connect_existing():
                time.sleep(0.6)
                state["bridge_connected"] = True
            thread = threading.Thread(target=connect_existing)
            thread.start()
            messages = []
            report = service.ensure_parent(settings, timeout=3, progress=messages.append)
            thread.join(2)
        self.assertEqual(report["state"], "ready")
        self.assertTrue(any("继续等待现有父应用" in message for message in messages))
        self.launch.assert_called_once()

    def test_relative_parent_log_directory_is_anchored_to_parent_and_passed_as_absolute(self):
        self.launcher('import os, pathlib, sys\npathlib.Path("effective-log-dir").write_text(os.environ["FUSION_LOG_DIR"])\npathlib.Path("effective-data-dir").write_text(os.environ["FUSION_DATA_DIR"])\nsys.exit(8)\n')
        settings = self.free_endpoint()
        with patch.dict(os.environ, {"FUSION_LOG_DIR": "relative-logs", "FUSION_DATA_DIR": "relative-data"}):
            with self.assertRaises(service.ParentServiceError) as raised:
                service.ensure_parent(settings, timeout=2)
        expected = self.project / "relative-logs"
        self.assertEqual((self.project / "effective-log-dir").read_text(), str(expected))
        self.assertEqual((self.project / "effective-data-dir").read_text(), str(self.project / "relative-data"))
        self.assertEqual(Path(raised.exception.details["log_path"]).parent, expected)
        self.assertTrue((expected / "agent-parent-start.log").is_file())

    def test_fifo_log_is_rejected_without_blocking_on_open(self):
        settings = self.free_endpoint()
        self.server_launcher()
        directory = self.project / "logs"
        directory.mkdir()
        os.mkfifo(directory / "agent-parent-start.log")
        started = time.monotonic()
        with self.assertRaises(service.ParentServiceError):
            service.ensure_parent(settings, timeout=1)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.processes, [])

    def test_relative_parent_data_directory_finds_key_and_explicit_key_file_keeps_priority(self):
        directory = self.project / "relative-data"
        directory.mkdir()
        (directory / "api-key.txt").write_text("relative-parent-key\n")
        explicit = self.project / "explicit-key.txt"
        explicit.write_text("explicit-key\n")
        with patch.dict(os.environ, {"FUSION_DATA_DIR": "relative-data", "FUSION_TOKEN": ""}), \
                patch.object(config, "ROOT", self.project / "desktop-agent"):
            self.assertEqual(api_key(Settings(api_key_env="")), "relative-parent-key")
            self.assertEqual(api_key(Settings(api_key_env="", api_key_file=str(explicit))), "explicit-key")


if __name__ == "__main__":
    unittest.main()
