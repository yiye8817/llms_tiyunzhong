import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, quote

import fusion_agent.web_search as web_search_module
from fusion_agent.audit import Audit
from fusion_agent.cli import components, doctor_report
from fusion_agent.config import Settings
from fusion_agent.registry import ToolRegistry
from fusion_agent.terminal import TerminalProgress
from fusion_agent.web_search import (
    BackendFailure,
    BrowserUseBackend,
    DDGBackend,
    OpenCLIBackend,
    PlaywrightBackend,
    SearchResult,
    WebSearchTools,
    _run_bounded_process,
    _safe_result_url,
)


class StubBackend:
    def __init__(self, name, outcome, calls=None, barrier=None):
        self.name = name
        self.outcome = outcome
        self.calls = calls if calls is not None else []
        self.barrier = barrier

    def search(self, query, maximum, timeout):
        self.calls.append((self.name, query, maximum, timeout))
        if self.barrier:
            self.barrier.wait(timeout=1)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if callable(self.outcome):
            return self.outcome(query, maximum, timeout)
        return self.outcome


def result(source, url="https://example.test/path", title="Example", snippet="Summary"):
    return SearchResult(title, url, snippet, source)


class DDGBackendTests(unittest.TestCase):
    def test_html_search_parses_redirects_nested_text_and_snippet(self):
        seen = {}
        html = b"""
        <div class="result">
          <h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2FExample.COM%2Fnews%3Futm_source%3Dddg%26id%3D7">A <b>current</b> result</a></h2>
          <div class="result__snippet">Small <b>trusted-looking</b> snippet</div>
        </div>
        <div class="result"><a class="result__a" href="javascript:alert(1)">Unsafe</a></div>
        <div class="result"><a class="result__a" href="http://127.0.0.1/private">Local</a></div>
        """

        def fetch(url, data, headers, timeout):
            seen.update(url=url, data=data, headers=headers, timeout=timeout)
            return html

        rows = DDGBackend(fetch).search("最新 AI 消息 & more", 5, 4.5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "A current result")
        self.assertEqual(rows[0].snippet, "Small trusted-looking snippet")
        self.assertEqual(rows[0].url, "https://example.com/news?id=7")
        self.assertEqual(rows[0].source, "ddgo")
        self.assertEqual(parse_qs(seen["data"].decode())["q"], ["最新 AI 消息 & more"])
        self.assertEqual(seen["timeout"], 4.5)
        self.assertEqual(seen["headers"]["Content-Type"], "application/x-www-form-urlencoded")

    def test_fetch_timeout_and_invalid_fetch_output_have_safe_codes(self):
        def timed_out(*_):
            raise TimeoutError("private transport details")

        with self.assertRaises(BackendFailure) as error:
            DDGBackend(timed_out).search("query", 3, 1)
        self.assertEqual(error.exception.code, "timeout")
        self.assertNotIn("private", str(error.exception))

        with self.assertRaises(BackendFailure) as error:
            DDGBackend(lambda *_: "not bytes").search("query", 3, 1)
        self.assertEqual(error.exception.code, "invalid_output")

    def test_url_safety_rejects_credentials_local_and_non_http(self):
        for value in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "https://user:password@example.com/",
            "http://localhost/admin",
            "http://[::1]/",
            "http://10.0.0.8/",
            "http://2130706433/",
            "http://0x7f000001/",
            "http://0177.0.0.1/",
            "http://127.000.000.001/",
            "http://localhost./",
            "https://example.com:bad/",
        ):
            with self.subTest(value=value):
                self.assertIsNone(_safe_result_url(value))
        self.assertEqual(_safe_result_url("HTTPS://Example.COM:443/a#secret"), "https://example.com/a")
        self.assertEqual(
            _safe_result_url(
                "https://example.com/a?id=7&token=PRIVATE&utm_medium=x&"
                "api%252Dkey=DOUBLE&X-Amz-Signature=AWS&sig=SIGNED&"
                "X-Goog-Credential=GOOGLE&Key-Pair-Id=CLOUDFRONT&"
                "token_extra=EXTRA&token%2500x=NULL"),
            "https://example.com/a?id=7",
        )
        deeply_encoded = "api%2Dkey"
        for _ in range(12):
            deeply_encoded = quote(deeply_encoded, safe="")
        self.assertEqual(
            _safe_result_url(f"https://example.com/a?{deeply_encoded}=PRIVATE&id=8"),
            "https://example.com/a?id=8",
        )
        self.assertEqual(_safe_result_url("https://8.8.8.8/a"), "https://8.8.8.8/a")


class ProductionProcessTests(unittest.TestCase):
    def test_bounded_process_stops_before_stdout_can_exceed_limit(self):
        with self.assertRaises(BackendFailure) as error:
            _run_bounded_process(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 4096)"],
                env=os.environ.copy(), timeout=2, output_limit=128, backend_label="Test CLI",
            )
        self.assertEqual(error.exception.code, "output_too_large")
        self.assertNotIn("x" * 32, str(error.exception))

    @unittest.skipUnless(os.name == "posix", "process-group cleanup is POSIX-specific")
    def test_bounded_process_timeout_kills_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            started = Path(temporary) / "child-started"
            survived = Path(temporary) / "child-survived"
            child = (
                "from pathlib import Path; import time; "
                f"Path({str(started)!r}).write_text('started'); "
                "time.sleep(1.0); "
                f"Path({str(survived)!r}).write_text('survived'); "
                "time.sleep(5)"
            )
            parent = (
                "from pathlib import Path; import subprocess, sys, time; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
                f"Path({str(started)!r} + '.parent').write_text('spawned'); "
                "time.sleep(10)"
            )
            began = time.monotonic()
            with self.assertRaises(BackendFailure) as error:
                _run_bounded_process(
                    [sys.executable, "-c", parent], env=os.environ.copy(), timeout=0.5,
                    output_limit=1024, backend_label="Test CLI",
                )
            self.assertEqual(error.exception.code, "timeout")
            self.assertLess(time.monotonic() - began, 1.5)
            self.assertTrue(Path(str(started) + ".parent").exists())
            time.sleep(0.8)
            self.assertFalse(survived.exists())


class AggregateTests(unittest.TestCase):
    def test_tool_contract_is_strict_read_only_and_browser_scoped(self):
        spec = WebSearchTools([StubBackend("ddgo", [])]).specs()[0]
        self.assertEqual(spec.name, "web.search")
        self.assertEqual(spec.capability, "web")
        self.assertFalse(spec.mutating)
        self.assertFalse(spec.parameters["additionalProperties"])
        self.assertEqual(spec.parameters["properties"]["mode"]["enum"], ["sequential", "parallel"])

    def test_sequential_uses_priority_order_and_stops_on_first_results(self):
        calls = []
        backends = [
            StubBackend("playwright", [result("playwright")], calls),
            StubBackend("opencli", [result("opencli", "https://opencli.test/")], calls),
            StubBackend("browser_use", BackendFailure("down", "Browser unavailable."), calls),
            StubBackend("ddgo", [], calls),
        ]
        output = WebSearchTools(backends).search({"query": "current query", "max_results": 4})
        self.assertEqual([item[0] for item in calls], ["ddgo", "browser_use", "opencli"])
        self.assertEqual(output["results"][0]["source"], "opencli")
        self.assertEqual([item["status"] for item in output["attempts"]], ["empty", "failed", "ok"])
        self.assertNotIn("ok", output)  # ToolRegistry adds success; handler only overrides failures.

    def test_all_backend_failures_are_structured_and_raw_exception_is_hidden(self):
        backends = [
            StubBackend("ddgo", RuntimeError("token=PRIVATE backend explosion")),
            StubBackend("browser_use", BackendFailure("dependency_missing", "Optional support is absent.", status="unavailable", retryable=False)),
            StubBackend("opencli", BackendFailure("timeout", "OpenCLI timed out.", status="timeout")),
            StubBackend("playwright", []),
        ]
        output = WebSearchTools(backends).search({"query": "query"})
        self.assertFalse(output["ok"])
        self.assertEqual(output["error"]["code"], "search_failed")
        self.assertEqual([item["status"] for item in output["attempts"]],
                         ["failed", "unavailable", "timeout", "empty"])
        self.assertNotIn("PRIVATE", json.dumps(output))

    def test_parallel_starts_all_routes_and_deduplicates_in_priority_order(self):
        barrier = threading.Barrier(4)
        backends = [
            StubBackend("playwright", [result("playwright", "https://third.test/")], barrier=barrier),
            StubBackend("opencli", [result("opencli", "https://same.test/article/", snippet="secondary")], barrier=barrier),
            StubBackend("browser_use", [result("browser_use", "https://second.test/")], barrier=barrier),
            StubBackend("ddgo", [result("ddgo", "https://same.test/article", snippet="primary")], barrier=barrier),
        ]
        output = WebSearchTools(backends).search({"query": "parallel", "mode": "parallel", "max_results": 8})
        self.assertEqual(output["result_count"], 3)
        self.assertEqual([row["url"] for row in output["results"]],
                         ["https://same.test/article", "https://second.test/", "https://third.test/"])
        self.assertEqual(output["results"][0]["sources"], ["ddgo", "opencli"])
        self.assertEqual(output["results"][0]["snippet"], "primary")
        self.assertEqual([item["backend"] for item in output["attempts"]],
                         ["ddgo", "browser_use", "opencli", "playwright"])

    def test_parallel_preserves_success_when_other_routes_fail(self):
        output = WebSearchTools([
            StubBackend("ddgo", BackendFailure("network_error", "DDG unavailable.")),
            StubBackend("browser_use", [result("browser_use")]),
        ]).search({"query": "parallel", "mode": "parallel"})
        self.assertEqual(output["result_count"], 1)
        self.assertEqual(output["results"][0]["source"], "browser_use")
        self.assertEqual([item["status"] for item in output["attempts"]], ["failed", "ok"])

    def test_direct_handler_rejects_malformed_arguments_without_backends(self):
        calls = []
        tools = WebSearchTools([StubBackend("ddgo", [], calls)])
        for args in (None, {}, {"query": ""}, {"query": "q", "mode": "race"},
                     {"query": "q", "max_results": True}, {"query": "q", "timeout_seconds": 0}):
            with self.subTest(args=args):
                output = tools.search(args)
                self.assertFalse(output["ok"])
                self.assertEqual(output["error"]["code"], "invalid_arguments")
        self.assertEqual(calls, [])

    def test_events_contain_only_progress_metadata(self):
        events = []
        WebSearchTools([StubBackend("ddgo", [result("ddgo")])], event=lambda *args: events.append(args)).search(
            {"query": "PRIVATE SEARCH QUERY"}
        )
        self.assertEqual([event for event, _ in events],
                         ["web.search_started", "web.search_backend", "web.search_completed"])
        self.assertEqual(events[1][1]["result_count"], 1)
        self.assertIsNone(events[1][1]["code"])
        self.assertEqual(events[2][1]["result_count"], 1)
        self.assertEqual(events[2][1]["attempt"], 1)
        self.assertNotIn("PRIVATE SEARCH QUERY", json.dumps(events))

    def test_registry_audit_keeps_details_while_terminal_only_shows_status(self):
        terminal = io.StringIO()
        progress = TerminalProgress(terminal)
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "events.jsonl"
            audit = Audit(log_path, stream=io.StringIO())

            def event(name, fields):
                audit(name, fields)
                progress(name, fields)

            tools = WebSearchTools([StubBackend("ddgo", [result("ddgo")])], event=event)
            registry = ToolRegistry(tools.specs(), allowed=["web"], event=event)
            output = registry.invoke("web.search", {"query": "PRIVATE SEARCH QUERY", "max_results": 2})
            audit.close()
            self.assertTrue(output["ok"])
            saved = log_path.read_text()
            self.assertIn("PRIVATE SEARCH QUERY", saved)
            self.assertIn('"event": "web.search_backend"', saved)
            self.assertIn('"result_count": 1', saved)
            self.assertNotIn("PRIVATE SEARCH QUERY", terminal.getvalue())
            self.assertIn("正在搜索实时网页信息", terminal.getvalue())
            self.assertIn("已整理 1 条结果", terminal.getvalue())


class MainIntegrationTests(unittest.TestCase):
    def test_components_register_web_search_and_doctor_reports_optional_clis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(workspace=str(root / "workspace"), runtime_dir=str(root / "runtime"),
                                skills_dir=str(root / "skills")).validate()
            owners, _, specs = components(settings)
            try:
                names = [spec.name for spec in specs]
                self.assertEqual(names.count("web.search"), 1)
                selected = next(spec for spec in specs if spec.name == "web.search")
                self.assertEqual(selected.capability, "web")
                self.assertFalse(selected.mutating)
                self.assertTrue(any(isinstance(owner, WebSearchTools) for owner in owners))
                self.assertTrue({"browser-use", "opencli"} <= set(doctor_report(settings)["optional_cli"]))
            finally:
                for owner in reversed(owners):
                    close = getattr(owner, "close", None)
                    if close:
                        close()


class OptionalBackendTests(unittest.TestCase):
    def test_browser_use_cli3_runs_fixed_stdin_template_without_model_or_secret_env(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            payload = {"results": [
                {"title": "Good", "url": "https://example.test/x", "snippet": "Found"},
                {"title": "Bad", "url": "file:///etc/passwd", "snippet": "Unsafe"},
            ]}
            stdout = "browser startup noise\n__FUSION_WEB_SEARCH_RESULT__:" + json.dumps(payload)
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        query = "q; __import__('os').system('touch /tmp/not-run')"
        rows = BrowserUseBackend(
            runner=runner,
            executable="/opt/browser-use",
            environ={"PATH": "/bin", "HOME": "/home/test", "BROWSER_USE_API_KEY": "PRIVATE-KEY",
                     "UNRELATED_SECRET": "PRIVATE-ENV"},
        ).search(query, 4, 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "browser_use")
        argv, kwargs = calls[0]
        self.assertEqual(argv, ["/opt/browser-use"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["env"]["FUSION_SEARCH_QUERY"], query)
        self.assertEqual(kwargs["env"]["FUSION_SEARCH_MAX_RESULTS"], "4")
        self.assertEqual(kwargs["env"]["PATH"], "/bin")
        self.assertNotIn("BROWSER_USE_API_KEY", kwargs["env"])
        self.assertNotIn("UNRELATED_SECRET", kwargs["env"])
        self.assertNotIn(query, kwargs["input"])
        self.assertNotIn("Agent", kwargs["input"])
        self.assertNotIn("ChatBrowserUse", kwargs["input"])
        for helper in ("new_tab(", "wait_for_load(", "js(", "close_tab("):
            self.assertIn(helper, kwargs["input"])

    def test_browser_use_invalid_output_has_stable_error(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "private non-json output", "")

        with self.assertRaises(BackendFailure) as error:
            BrowserUseBackend(runner=runner, executable="browser-use").search("q", 4, 3)
        self.assertEqual(error.exception.code, "invalid_output")
        self.assertNotIn("private", str(error.exception))

    def test_browser_use_nonzero_exit_does_not_leak_stderr(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 2, "", "PRIVATE CLI traceback")

        with self.assertRaises(BackendFailure) as error:
            BrowserUseBackend(runner=runner, executable="browser-use").search("q", 4, 3)
        self.assertEqual(error.exception.code, "backend_error")
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_opencli_uses_one_official_machine_command_without_shell(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            stdout = json.dumps([{"title": "CLI", "url": "https://cli.test/r", "snippet": "Found"}])
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        rows = OpenCLIBackend(
            runner=runner,
            executable="/opt/opencli",
            environ={"PATH": "/bin", "HOME": "/home/test", "DISPLAY": ":9",
                     "OPENCLI_PROFILE": "work", "OPENAI_API_KEY": "PRIVATE-KEY",
                     "FUSION_AGENT_API_KEY": "PRIVATE-FUSION-KEY",
                     "FUSION_TOKEN": "PRIVATE-FUSION-TOKEN",
                     "NODE_OPTIONS": "--require=/tmp/private-code.js",
                     "UNRELATED_SECRET": "PRIVATE-ENV"},
        ).search(
            "x; touch /tmp/not-run & more", 3, 5
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(kwargs["shell"] is False for _, kwargs in calls))
        self.assertTrue(all(isinstance(argv, list) for argv, _ in calls))
        self.assertEqual(calls[0][0], [
            "/opt/opencli", "duckduckgo", "search", "x; touch /tmp/not-run & more",
            "--limit", "3", "-f", "json",
        ])
        self.assertEqual(calls[0][1]["env"]["PATH"], "/bin")
        self.assertEqual(calls[0][1]["env"]["HOME"], "/home/test")
        self.assertEqual(calls[0][1]["env"]["DISPLAY"], ":9")
        self.assertEqual(calls[0][1]["env"]["OPENCLI_PROFILE"], "work")
        for key in ("OPENAI_API_KEY", "FUSION_AGENT_API_KEY", "FUSION_TOKEN",
                    "NODE_OPTIONS", "UNRELATED_SECRET"):
            self.assertNotIn(key, calls[0][1]["env"])
        self.assertEqual(rows[0].source, "opencli")

    def test_opencli_clamps_adapter_limit_to_ten_while_public_tool_allows_twenty(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "[]", "")

        backend = OpenCLIBackend(runner=runner, executable="opencli", environ={"PATH": "/bin"})
        self.assertEqual(backend.search("q", 20, 3), [])
        self.assertEqual(calls[0][calls[0].index("--limit") + 1], "10")
        maximum = WebSearchTools([StubBackend("ddgo", [])]).specs()[0].parameters["properties"]["max_results"]["maximum"]
        self.assertEqual(maximum, 20)

    def test_opencli_leading_dash_query_remains_a_positional_value(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 66, "", "")

        backend = OpenCLIBackend(runner=runner, executable="opencli")
        self.assertEqual(backend.search("--help", 2, 3), [])
        self.assertEqual(calls[0][3], " --help")

    def test_opencli_exit_codes_are_mapped_without_leaking_stderr(self):
        cases = {
            66: (None, None),
            69: ("backend_unavailable", "unavailable"),
            75: ("timeout", "timeout"),
            77: ("authentication_required", "unavailable"),
            78: ("configuration_error", "unavailable"),
            9: ("backend_error", "failed"),
        }
        for returncode, (code, status) in cases.items():
            with self.subTest(returncode=returncode):
                def runner(argv, **kwargs):
                    return subprocess.CompletedProcess(argv, returncode, "", "PRIVATE stderr token")
                backend = OpenCLIBackend(runner=runner, executable="opencli")
                if returncode == 66:
                    self.assertEqual(backend.search("q", 2, 3), [])
                else:
                    with self.assertRaises(BackendFailure) as error:
                        backend.search("q", 2, 3)
                    self.assertEqual(error.exception.code, code)
                    self.assertEqual(error.exception.status, status)
                    self.assertNotIn("PRIVATE", str(error.exception))

    def test_playwright_callback_results_are_validated(self):
        seen = []
        backend = PlaywrightBackend(lambda query, maximum, timeout: seen.append((query, maximum, timeout)) or [
            {"title": "Browser", "url": "https://browser.test/", "snippet": "Result"},
            {"title": "Unsafe", "url": "http://192.168.1.2/", "snippet": "Local"},
        ])
        rows = backend.search("q", 5, 7)
        self.assertEqual(seen, [("q", 5, 7)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "playwright")

    def test_playwright_steps_share_one_deadline(self):
        timeouts = {}

        class Page:
            def goto(self, _url, **kwargs):
                timeouts["goto"] = kwargs["timeout"]

            def wait_for_selector(self, _selector, **kwargs):
                timeouts["selector"] = kwargs["timeout"]

            def eval_on_selector_all(self, *_args):
                return [{"title": "Result", "url": "https://example.test/", "snippet": "ok"}]

        class Browser:
            def new_page(self):
                return Page()

            def close(self):
                pass

        class Chromium:
            def launch(self, **kwargs):
                timeouts["launch"] = kwargs["timeout"]
                return Browser()

        class Manager:
            chromium = Chromium()

            def stop(self):
                pass

        module = SimpleNamespace(
            sync_playwright=lambda: SimpleNamespace(start=lambda: Manager()))
        ticks = iter((100.0, 100.1, 100.35, 100.7))
        with patch.object(web_search_module.time, "monotonic", side_effect=lambda: next(ticks)), \
                patch.object(web_search_module.importlib, "import_module", return_value=module):
            rows = PlaywrightBackend().search("q", 2, 1)

        self.assertEqual(len(rows), 1)
        self.assertGreater(timeouts["launch"], timeouts["goto"])
        self.assertGreater(timeouts["goto"], timeouts["selector"])
        self.assertLessEqual(timeouts["launch"], 900)
        self.assertLessEqual(timeouts["selector"], 300)


if __name__ == "__main__":
    unittest.main()
