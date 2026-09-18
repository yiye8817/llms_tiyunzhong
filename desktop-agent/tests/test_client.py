import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from fusion_agent.client import ClientError, FusionClient, MAX_REQUEST_BYTES, encode_request_payload


class FakeAPI:
    def __init__(self):
        self.requests = []
        self.raw_bodies = []
        self.status = 200
        self.headers = {}
        self.body = {"choices": [{"message": {"role": "assistant", "content": "好的"}, "finish_reason": "stop"}]}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.respond()

            def do_GET(self):
                self.respond()

            def respond(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.raw_bodies.append(raw)
                owner.requests.append({"method": self.command, "path": self.path,
                                       "authorization": self.headers.get("Authorization"),
                                       "body": json.loads(raw) if raw else None})
                self.send_response(owner.status)
                for key, value in owner.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                body = owner.body if isinstance(owner.body, bytes) else json.dumps(owner.body, ensure_ascii=False).encode()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ClientTests(unittest.TestCase):
    def test_real_http_text_only_request_and_single_model(self):
        with FakeAPI() as api:
            client = FusionClient(api.url, "test-key", model="qwen")
            messages = [{"role": "system", "content": "只输出 JSON"}, {"role": "user", "content": "你好"}]
            self.assertEqual(client.complete(messages), "好的")
            self.assertEqual(api.requests, [{"method": "POST", "path": "/v1/chat/completions",
                                           "authorization": "Bearer test-key",
                                           "body": {"model": "qwen", "messages": messages, "stream": False}}])

    def test_models_real_http(self):
        with FakeAPI() as api:
            api.body = {"data": [{"id": "deepseek", "object": "model", "owned_by": "web"}]}
            client = FusionClient(api.url + "/v1/", "test-key")
            self.assertEqual(client.models(), [{"id": "deepseek", "object": "model"}])
            self.assertEqual(api.requests[0]["path"], "/v1/models")

    def test_http_failure_not_retried_and_secrets_not_exposed(self):
        with FakeAPI() as api:
            api.status = 503
            api.body = {"error": {"message": "secret-key https://private.example"}}
            client = FusionClient(api.url, "secret-key")
            with self.assertRaises(ClientError) as context:
                client.complete([{"role": "user", "content": "go"}])
            self.assertEqual(context.exception.status, 503)
            self.assertNotIn("secret-key", str(context.exception))
            self.assertNotIn("private.example", str(context.exception))
            self.assertEqual(len(api.requests), 1)

    def test_redirect_does_not_forward_bearer_or_repeat_submission(self):
        with FakeAPI() as source, FakeAPI() as destination:
            source.status = 307
            source.headers = {"Location": destination.url + "/v1/chat/completions"}
            client = FusionClient(source.url, "secret-key")
            with self.assertRaises(ClientError):
                client.complete([{"role": "user", "content": "go"}])
            self.assertEqual(len(source.requests), 1)
            self.assertEqual(destination.requests, [])

    def test_response_size_limit(self):
        with FakeAPI() as api:
            api.body = b"x" * 2048
            client = FusionClient(api.url, "key", max_response_bytes=1024)
            with self.assertRaises(ClientError) as context:
                client.models()
            self.assertEqual(context.exception.code, "response_too_large")

    def test_invalid_or_incomplete_api_responses(self):
        responses = [b"not json", [], {"error": {"message": "secret"}}, {"choices": []},
                     {"choices": [{"message": {"content": None}}]},
                     {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]},
                     {"choices": [{"message": {"content": "text", "tool_calls": [{"id": "x"}]}}]}]
        with FakeAPI() as api:
            client = FusionClient(api.url, "key")
            for response in responses:
                with self.subTest(response=response):
                    api.body = response
                    with self.assertRaises(ClientError):
                        client.complete([{"role": "user", "content": "go"}])

    def test_native_tool_or_image_messages_rejected_before_sending(self):
        messages = [[{"role": "tool", "content": "tool"}],
                    [{"role": "user", "content": [{"type": "image_url", "image_url": "file:///tmp/x"}]}],
                    [{"role": "assistant", "content": "x", "tool_calls": []}]]
        with FakeAPI() as api:
            client = FusionClient(api.url, "key")
            for message in messages:
                with self.assertRaises(ClientError):
                    client.complete(message)
            self.assertEqual(api.requests, [])

    def test_server_message_limits_at_exact_boundaries(self):
        boundary_cases = [
            ([{"role": "user", "content": "x"}] * 100,
             [{"role": "user", "content": "x"}] * 101, "message_count_limit"),
            ([{"role": "user", "content": "x" * 200000}],
             [{"role": "user", "content": "x" * 200001}], "message_length_limit"),
            ([{"role": "user", "content": "x" * length} for length in (200000, 200000, 100000)],
             [{"role": "user", "content": "x" * length} for length in (200000, 200000, 100001)], "message_total_limit"),
        ]
        with FakeAPI() as api:
            client = FusionClient(api.url, "key")
            for valid, invalid, code in boundary_cases:
                with self.subTest(code=code):
                    self.assertEqual(client.complete(valid), "好的")
                    count = len(api.requests)
                    with self.assertRaises(ClientError) as error:
                        client.complete(invalid)
                    self.assertEqual(error.exception.code, code)
                    self.assertEqual(len(api.requests), count)

    def test_utf8_content_counted_as_characters_and_body_as_bytes(self):
        with FakeAPI() as api:
            client = FusionClient(api.url, "key")
            content = "中" * 200000
            self.assertEqual(client.complete([{"role": "user", "content": content}]), "好的")
            self.assertGreater(len(api.raw_bodies[0]), 600000)
            self.assertLess(len(api.raw_bodies[0]), 600500)
            self.assertIn("中".encode("utf-8"), api.raw_bodies[0])

    def test_utf8_body_exact_limit_and_oversize_not_sent(self):
        # Test the byte encoder separately: valid messages normally hit 500k characters first.
        overhead = len(encode_request_payload({"x": ""}))
        count, remaining = divmod(MAX_REQUEST_BYTES - overhead, 3)
        value = "中" * count + "x" * remaining
        self.assertEqual(len(encode_request_payload({"x": value})), MAX_REQUEST_BYTES)
        with FakeAPI() as api:
            client = FusionClient(api.url, "key")
            with self.assertRaises(ClientError) as error:
                client._request("/chat/completions", {"x": value + "x"})
            self.assertEqual(error.exception.code, "request_too_large")
            self.assertEqual(api.requests, [])

    def test_empty_user_or_invalid_unicode_rejected_before_sending(self):
        with FakeAPI() as api:
            client = FusionClient(api.url, "key")
            for messages in ([{"role": "system", "content": "instructions"}],
                             [{"role": "user", "content": "   "}],
                             [{"role": "user", "content": "\ud800"}]):
                with self.assertRaises(ClientError):
                    client.complete(messages)
            self.assertEqual(api.requests, [])

    def test_base_url_validation(self):
        for url in ("file:///tmp/key", "http://user:pass@localhost:8765", "http://localhost:8765?token=key",
                    "http://localhost:8765/#frag", "http://localhost:99999", "http://localhost\n.evil", None):
            with self.subTest(url=url), self.assertRaises(ClientError):
                FusionClient(url, "key")
        for host in ("127.0.0.1", "localhost", "[::1]"):
            self.assertTrue(FusionClient(f"http://{host}:8765", "key").base_url.endswith("/v1"))
        with self.assertRaises(ClientError):
            FusionClient("http://192.168.1.2:8765/v1", "key")
        self.assertEqual(FusionClient("http://192.168.1.2:8765", "key", allow_remote_http=True).base_url,
                         "http://192.168.1.2:8765/v1")
        self.assertEqual(FusionClient("https://example.com/api/v1", "key").base_url,
                         "https://example.com/api/v1")

    def test_http_502_provider_details_and_correlation_survive(self):
        events = []
        with FakeAPI() as api:
            api.status = 502
            api.headers = {"X-Request-ID": "turn-abc123"}
            api.body = {"error": {"code": "candidate_generation_failed", "message": "网页生成失败",
                       "details": {"request_id": "body-id", "sources": [{"markdown": "PRIVATE SOURCE"}],
                                   "errors": [{"provider": "qwen", "code": "input_not_accepted",
                                               "message": "按钮不可用", "traceback": "PRIVATE TRACE"}]}}}
            client = FusionClient(api.url, "api-secret", event=lambda *items: events.append(items))
            with self.assertRaises(ClientError) as caught:
                client.complete([{"role": "user", "content": "任务"}])
            failure = caught.exception
            self.assertEqual((failure.code, failure.status, failure.server_code, failure.request_id),
                             ("http_error", 502, "candidate_generation_failed", "turn-abc123"))
            self.assertIn("qwen / input_not_accepted / 按钮不可用", str(failure))
            self.assertEqual(failure.details["errors"], [{"provider": "qwen", "code": "input_not_accepted", "message": "按钮不可用"}])
            logged = json.dumps(events)
            self.assertNotIn("PRIVATE", logged)
            self.assertEqual(events[-1][0], "model.http_error")
            self.assertIsInstance(events[-1][1]["duration_ms"], int)
            self.assertEqual(len(api.requests), 1)

    def test_http_503_body_request_id_and_secret_redaction(self):
        events = []
        with FakeAPI() as api:
            api.status = 503
            api.body = {"error": {"code": "bridge_disconnected", "message": "lost api-secret Bearer OTHER-TOKEN password=LETMEIN",
                       "details": {"request_id": "turn-from-body", "errors": [
                           {"provider": "qwen", "code": "not_ready", "message": "Cookie: session=COOKIESECRET; other=MORESECRET"}]}}}
            client = FusionClient(api.url, "api-secret", event=lambda *items: events.append(items))
            with self.assertRaises(ClientError) as caught:
                client.complete([{"role": "user", "content": "check"}])
            failure = caught.exception
            self.assertEqual(failure.request_id, "turn-from-body")
            self.assertEqual(failure.server_code, "bridge_disconnected")
            log = json.dumps(events) + str(failure) + json.dumps(failure.details)
            for secret in ("api-secret", "OTHER-TOKEN", "LETMEIN", "COOKIESECRET", "MORESECRET"):
                self.assertNotIn(secret, log)

    def test_html_and_large_error_body_never_echoed(self):
        with FakeAPI() as api:
            api.status = 502
            api.headers = {"X-Request-ID": "proxy-turn"}
            api.body = b'<html><script>password="PRIVATE"</script><title>gateway' + b"x" * 70000
            with self.assertRaises(ClientError) as caught:
                FusionClient(api.url, "key").complete([{"role": "user", "content": "go"}])
            failure = caught.exception
            self.assertEqual(failure.details["body_format"], "non_json")
            self.assertTrue(failure.details["body_truncated"])
            self.assertLess(len(str(failure)), 500)
            self.assertNotIn("PRIVATE", str(failure))
            self.assertEqual(failure.request_id, "proxy-turn")

    def test_full_request_response_events_redact_without_altering_request(self):
        events = []
        with FakeAPI() as api:
            api.headers = {"X-Request-ID": "accepted-123", "X-HTTP-ID": "http-accepted-123"}
            api.body = {"choices": [{"message": {"role": "assistant", "content": "reply api-secret password=HIDDEN"}, "finish_reason": "stop"}]}
            messages = [{"role": "user", "content": "任务 api-secret Bearer TOKEN password=PRIVATE"}]
            client = FusionClient(api.url, "api-secret", event=lambda *items: events.append(items))
            self.assertEqual(client.complete(messages), "reply api-secret password=HIDDEN")
            self.assertEqual(api.requests[0]["body"]["messages"], messages)
            self.assertEqual([name for name, _ in events], ["model.http_request", "model.http_response", "model.response"])
            self.assertEqual(events[1][1]["status"], 200)
            self.assertEqual(events[1][1]["request_id"], "accepted-123")
            self.assertEqual(events[1][1]["http_id"], "http-accepted-123")
            self.assertIn("payload", events[0][1])
            self.assertIn("payload", events[2][1])
            logged = json.dumps(events)
            for secret in ("api-secret", "TOKEN", "PRIVATE", "HIDDEN"):
                self.assertNotIn(secret, logged)

    def test_error_diagnostics_field_and_array_limits(self):
        with FakeAPI() as api:
            api.status = 502
            api.body = {"error": {"code": "z" * 200, "message": "m" * 2000,
                                  "details": {"errors": [{"provider": "p", "message": "x" * 2000}] * 20}}}
            with self.assertRaises(ClientError) as caught:
                FusionClient(api.url, "key").complete([{"role": "user", "content": "go"}])
            self.assertEqual(len(caught.exception.server_code), 80)
            self.assertEqual(len(caught.exception.details["message"]), 1000)
            self.assertEqual(len(caught.exception.details["errors"]), 12)
            self.assertTrue(all(len(row["message"]) <= 1000 for row in caught.exception.details["errors"]))

    def test_bare_token_and_escaped_quote_secrets_fully_redacted(self):
        events = []
        diagnostics = r'''token=BARETOKEN password="PASSWORD\"ESCAPED_SUFFIX" secret='SINGLE\'QUOTE_SUFFIX' api_key="KEY\\PATH_SUFFIX"'''
        with FakeAPI() as api:
            api.status = 502
            api.body = {"error": {"code": "input_failed", "message": diagnostics}}
            client = FusionClient(api.url, "local-key", event=lambda *items: events.append(items))
            with self.assertRaises(ClientError) as caught:
                client.complete([{"role": "user", "content": diagnostics}])
            logged = json.dumps(events) + str(caught.exception) + json.dumps(caught.exception.details)
            for secret in ("BARETOKEN", "PASSWORD", "ESCAPED_SUFFIX", "SINGLE", "QUOTE_SUFFIX", "KEY", "PATH_SUFFIX"):
                self.assertNotIn(secret, logged)

    def test_nested_bare_token_key_and_json_escaped_password_are_redacted(self):
        from fusion_agent.client import _redact
        result = _redact({"token": "PLAIN", "nested": {"password": "NESTED"},
                          "body": r'''{"token":"TOKEN\"SUFFIX", "password":"PASS\"TAIL"}'''}, "api-key")
        logged = json.dumps(result)
        for secret in ("PLAIN", "NESTED", "TOKEN", "SUFFIX", "PASS", "TAIL"):
            self.assertNotIn(secret, logged)

    def test_early_error_http_id_preserved_without_fabricating_generation_id(self):
        events = []
        with FakeAPI() as api:
            api.status = 503
            api.headers = {"X-HTTP-ID": "early-http-123"}
            api.body = {"error": {"code": "bridge_disconnected", "message": "Browser bridge is unavailable"}}
            with self.assertRaises(ClientError) as caught:
                FusionClient(api.url, "local-key", event=lambda *items: events.append(items)).complete(
                    [{"role": "user", "content": "task"}])
            failure = caught.exception
            self.assertEqual(failure.http_id, "early-http-123")
            self.assertEqual(failure.details["http_id"], "early-http-123")
            self.assertIsNone(failure.request_id)
            self.assertNotIn("request_id", failure.details)
            self.assertIn("HTTP 请求编号：early-http-123", str(failure))
            self.assertEqual(events[-1][1]["http_id"], "early-http-123")


if __name__ == "__main__":
    unittest.main()
