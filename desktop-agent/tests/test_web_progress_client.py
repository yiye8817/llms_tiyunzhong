import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

from fusion_agent.client import ClientError, FusionClient, _WebProgress


class ProgressAPI:
    def __init__(self, *, unsupported=False, invalid=False, post_status=200):
        self.requests = []
        self.records = {}
        self.lock = threading.Lock()
        self.unsupported, self.invalid = unsupported, invalid
        self.post_status = post_status
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.loads(raw)
                key = self.headers.get("X-Fusion-Progress-ID")
                with owner.lock:
                    owner.requests.append(("POST", self.path, key, body))
                    record = {"request_id": str(uuid4()), "done": False, "sequence": 3,
                              "events": [{"sequence": i + 1, "stage": stage, "provider": body["model"], "http_status": 200,
                                          "payload": "PRIVATE BODY", "url": "https://PRIVATE"}
                                         for i, stage in enumerate(["send_dispatched", "accepted", "server_responded"])],
                              "observed": threading.Event()}
                    owner.records[key] = record
                if key and not owner.unsupported and not owner.invalid:
                    record["observed"].wait(timeout=2)
                with owner.lock:
                    record["done"] = True
                    record["sequence"] = 4
                    record["events"].append({"sequence": 4, "stage": "completed" if owner.post_status == 200 else "failed"})
                if owner.post_status == 200:
                    self.respond({"choices": [{"message": {"role": "assistant", "content": "好的"}, "finish_reason": "stop"}]})
                else:
                    self.respond({"error": {"code": "provider_failed", "message": "Website request failed"}}, owner.post_status)

            def do_GET(self):
                parsed = urlsplit(self.path)
                key = parsed.path.rsplit("/", 1)[-1]
                cursor = int(parse_qs(parsed.query)["after"][0])
                with owner.lock:
                    owner.requests.append(("GET", self.path, self.headers.get("Authorization")))
                    record = owner.records.get(key)
                    report = ({"request_id": record["request_id"], "done": record["done"], "sequence": record["sequence"],
                               "events": [dict(row) for row in record["events"] if row["sequence"] > cursor]}
                              if record else {"request_id": None, "done": False, "sequence": 0, "events": [], "pending": True})
                    if record:
                        record["observed"].set()
                if owner.unsupported:
                    self.respond({"error": "not found"}, 404)
                elif owner.invalid:
                    self.respond({"events": "PRIVATE invalid body"})
                else:
                    self.respond(report)

            def respond(self, value, status=200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ProgressClientTests(unittest.TestCase):
    def test_real_request_reports_stages_and_final_fetch_without_resubmission(self):
        with ProgressAPI() as api:
            events = []
            client = FusionClient(api.url, "secret", model="qwen", web_progress=True,
                                  event=lambda name, fields: events.append((name, fields)))
            self.assertEqual(client.complete([{"role": "user", "content": "task"}]), "好的")
            rows = [fields for name, fields in events if name == "model.web_progress"]
            self.assertEqual([row["stage"] for row in rows], ["send_dispatched", "accepted", "server_responded", "completed"])
            self.assertEqual(len({row["request_id"] for row in rows}), 1)
            self.assertNotIn("PRIVATE", json.dumps(rows))
            posts = [item for item in api.requests if item[0] == "POST"]
            self.assertEqual(len(posts), 1)
            self.assertEqual(set(posts[0][3]), {"model", "messages", "stream"})
            self.assertTrue(all(item[2] == "Bearer secret" for item in api.requests if item[0] == "GET"))

    def test_concurrent_calls_have_independent_progress_and_headers(self):
        with ProgressAPI() as api:
            callbacks = {"qwen": [], "deepseek": []}
            clients = {name: FusionClient(api.url, "secret", model=name, web_progress=True,
                                          event=lambda event, fields, target=name: callbacks[target].append((event, fields)))
                       for name in callbacks}
            threads = [threading.Thread(target=lambda value=client: value.complete([{"role": "user", "content": "task"}]))
                       for client in clients.values()]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
            request_ids = []
            for name, events in callbacks.items():
                rows = [fields for event, fields in events if event == "model.web_progress"]
                self.assertEqual({row["provider"] for row in rows if "provider" in row}, {name})
                self.assertEqual(rows[-1]["stage"], "completed")
                request_ids.append(rows[0]["request_id"])
            self.assertEqual(len(set(request_ids)), 2)
            self.assertEqual(len([item for item in api.requests if item[0] == "POST"]), 2)

    def test_old_or_invalid_progress_endpoint_degrades_without_affecting_answer(self):
        for options in ({"unsupported": True}, {"invalid": True}):
            with self.subTest(options=options), ProgressAPI(**options) as api:
                events = []
                client = FusionClient(api.url, "secret", web_progress=True, event=lambda *args: events.append(args))
                self.assertEqual(client.complete([{"role": "user", "content": "task"}]), "好的")
                self.assertEqual(len([item for item in api.requests if item[0] == "POST"]), 1)
                self.assertEqual(len([name for name, _ in events if name == "model.web_progress_unavailable"]), 1)
                self.assertFalse(any(name == "model.web_progress" for name, _ in events))

    def test_generic_api_has_no_progress_probe_or_custom_header(self):
        with ProgressAPI() as api:
            self.assertEqual(FusionClient(api.url, "secret").complete([{"role": "user", "content": "task"}]), "好的")
            self.assertEqual(len(api.requests), 1)
            self.assertIsNone(api.requests[0][2])

    def test_failed_completion_collects_terminal_progress_without_post_retry(self):
        with ProgressAPI(post_status=502) as api:
            events = []
            client = FusionClient(api.url, "secret", web_progress=True, event=lambda *args: events.append(args))
            with self.assertRaises(ClientError) as caught:
                client.complete([{"role": "user", "content": "task"}])
            self.assertEqual(caught.exception.status, 502)
            self.assertEqual([fields["stage"] for name, fields in events if name == "model.web_progress"][-1], "failed")
            self.assertEqual(len([item for item in api.requests if item[0] == "POST"]), 1)

    def test_progress_thread_start_failure_leaves_the_single_post_available(self):
        with ProgressAPI() as api:
            events = []
            client = FusionClient(api.url, "secret", web_progress=True, event=lambda *args: events.append(args))
            with patch.object(_WebProgress, "start", side_effect=RuntimeError("cannot start thread")):
                self.assertEqual(client.complete([{"role": "user", "content": "task"}]), "好的")
            self.assertEqual(len(api.requests), 1)
            self.assertIsNone(api.requests[0][2])
            self.assertTrue(any(name == "model.web_progress_unavailable" for name, _ in events))

    def test_stalled_observer_close_is_bounded_and_never_emits_late_updates(self):
        events = []
        observer = _WebProgress(FusionClient("http://localhost:8765", "secret", event=lambda *args: events.append(args)))
        observer.HTTP_TIMEOUT = .01
        entered, release = threading.Event(), threading.Event()
        def stalled_read():
            entered.set()
            release.wait(timeout=2)
            observer._emit("model.web_progress", stage="generating")
            observer.done = True
        observer._read = stalled_read
        observer.start()
        self.assertTrue(entered.wait(timeout=1))
        started = time.monotonic()
        observer.close()
        self.assertLess(time.monotonic() - started, .5)
        release.set()
        observer.thread.join(timeout=1)
        self.assertFalse(observer.thread.is_alive())
        self.assertEqual(events, [])
