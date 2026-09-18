"""Deterministic integration tests; no paid API or website account is used.

Run: .venv/bin/python -m unittest discover -s tests -p 'test_backend.py' -v
"""

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from backend.app import app
from backend.engine import Bridge, Engine, FusionError, synthesize_api
from backend.models import AppConfig, ChatRequest
from backend.storage import Store

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "test-only-token-at-least-32-characters"
AUTH = {"Authorization": "Bearer " + TOKEN}


def configuration():
    return json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))


def chat(**overrides):
    return dict({"model": "web-fusion", "messages": [{"role": "user", "content": "比较 Android 内存泄漏定位方法，保留代码。"}]}, **overrides)


@contextmanager
def website_bridge(client, *, fail=()):
    packets, thread_errors = [], []
    with client.websocket_connect("/internal/bridge", headers=AUTH) as ws:
        config = ws.receive_json()
        assert config["type"] == "config"
        ws.send_json({"type": "ready"})

        def respond():
            try:
                while True:
                    packet = ws.receive_json()
                    packets.append(packet)
                    if packet["type"] == "config":
                        ws.send_json({"type": "ready"})
                    elif packet["type"] == "generate":
                        provider = packet["provider_id"]
                        if provider in fail:
                            ws.send_json({"type": "error", "job_id": packet["job_id"], "provider_id": provider,
                                          "error": {"code": "login_required", "message": "Please log in manually."}})
                        else:
                            content = ("# 整合结论\n\n先用 PSS 趋势分类，再分别定位 Java 与 native 分配。\n\n```sh\nadb shell dumpsys meminfo\n```"
                                       if packet["purpose"] == "fusion" else f"# {provider}\n\n原始候选回答\n\n|类别|工具|\n|---|---|\n|native|heapprofd|")
                            ws.send_json({"type": "result", "job_id": packet["job_id"], "provider_id": provider, "markdown": content})
            except (WebSocketDisconnect, RuntimeError):
                pass
            except BaseException as exc:
                # Context shutdown can cancel portal futures; preserve unexpected errors for diagnosis.
                thread_errors.append(exc)

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        yield packets
    thread.join(timeout=2)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environ = patch.dict(os.environ, {"FUSION_DATA_DIR": self.temporary.name, "FUSION_TOKEN": TOKEN,
                                               "FUSION_LOG_DIR": str(Path(self.temporary.name) / "logs")})
        self.environ.start()
        self.client_context = TestClient(app, base_url="http://127.0.0.1:8765")
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.environ.stop()
        self.temporary.cleanup()

    def test_send1177_old_configuration_gets_non_destructive_transport_defaults(self):
        config = configuration()
        for key in ("input_chunk_chars", "input_chunk_delay_ms", "submit_settle_seconds"):
            config["generation"].pop(key, None)
        settings = AppConfig.model_validate(config).generation
        self.assertEqual(settings.input_chunk_chars, 4096)
        self.assertEqual(settings.input_chunk_delay_ms, 35)
        self.assertEqual(settings.submit_settle_seconds, 2)

    def test_send1177_rejects_unsafe_transport_setting_values(self):
        for key, invalid in (("input_chunk_chars", [0, 255, 16385, 3.5, True]),
                             ("input_chunk_delay_ms", [-1, 1001, False]),
                             ("submit_settle_seconds", [-1, 11])):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    config = configuration()
                    config["generation"][key] = value
                    with self.assertRaises(ValidationError):
                        AppConfig.model_validate(config)

    def test_send1177_settings_persist_and_are_forwarded_to_browser_jobs(self):
        config = configuration()
        expected = {"input_chunk_chars": 2048, "input_chunk_delay_ms": 80, "submit_settle_seconds": 3}
        config["generation"].update(expected)
        response = self.client.put("/internal/config", headers=AUTH, json=config)
        self.assertEqual(response.status_code, 200, response.text)
        saved = json.loads((Path(self.temporary.name) / "config.json").read_text())
        self.assertTrue(all(saved["generation"][key] == value for key, value in expected.items()))
        with website_bridge(self.client) as packets:
            response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(model="chatgpt"))
        self.assertEqual(response.status_code, 200, response.text)
        packet = next(row for row in packets if row["type"] == "generate")
        for key, value in expected.items():
            self.assertEqual(packet[key], value)

    def test_send1177_agent_qwen_and_chat_glm_receive_identical_complete_context(self):
        config = configuration()
        for provider in config["providers"]:
            provider["enabled"] = provider["id"] in ("qwen", "glm")
        config["fusion"]["provider"] = "qwen"
        response = self.client.put("/internal/config", headers=AUTH, json=config)
        self.assertEqual(response.status_code, 200, response.text)
        messages = [{"role": "system", "content": '工具 schema 必须保留。"引号"\n' * 1800},
                    {"role": "user", "content": "历史原任务"},
                    {"role": "assistant", "content": '{"type":"final","answer":"已读上下文"}'},
                    {"role": "user", "content": "读取本地文件后继续，不可删减原任务。"}]
        with website_bridge(self.client) as packets:
            direct = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(model="qwen", messages=messages))
            paired = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(messages=messages))
        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertEqual(paired.status_code, 200, paired.text)
        generated = [row for row in packets if row["type"] == "generate"]
        self.assertEqual([(row["provider_id"], row["purpose"]) for row in generated],
                         [("qwen", "candidate"), ("qwen", "candidate"), ("glm", "candidate"), ("qwen", "fusion")])
        self.assertEqual(generated[0]["request_source"], "api")
        for packet in generated[1:3]:
            self.assertEqual(packet["request_source"], "fusion_chat")
            self.assertTrue(packet["qwen_retry_stages"])
            self.assertEqual(packet["qwen_manual_retry_wait_seconds"], 20)
            self.assertTrue(0 < packet["total_timeout_seconds"] <= 60)
        prompt = generated[0]["prompt"]
        self.assertEqual(prompt, generated[1]["prompt"])
        self.assertEqual(prompt, generated[2]["prompt"])
        for message in messages:
            self.assertIn(message["content"], prompt)
        self.assertEqual(len(paired.json()["fusion"]["sources"]), 2)
        self.assertEqual(self.client.get("/internal/status", headers=AUTH).json()["version"], "1.17.10")

    def test_auth_host_and_strict_api(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/internal/config").status_code, 401)
        self.assertEqual(self.client.get("/health", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/health", headers={"Host": "[evil]"}).status_code, 403)
        response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(tools=[]))
        self.assertEqual(response.status_code, 422)
        self.assertIn("tools", response.json()["error"]["message"])
        response = self.client.post("/v1/chat/completions", headers=AUTH,
                                    json=chat(messages=[{"role": "user", "content": [{"type": "image_url"}]}]))
        self.assertEqual(response.status_code, 422)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/internal/bridge", headers=dict(AUTH, Origin="https://example.org")):
                pass

    def test_real_pipeline_contract_and_persistence(self):
        with website_bridge(self.client) as packets:
            response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat())
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["choices"][0]["message"]["role"], "assistant")
        self.assertTrue(data["choices"][0]["message"]["content"].startswith("# 整合结论"))
        generated = [p for p in packets if p["type"] == "generate"]
        self.assertEqual([p["purpose"] for p in generated], ["candidate", "candidate", "fusion"])
        self.assertEqual(generated[0]["submission_timeout_seconds"], 120)
        self.assertEqual(generated[0]["timeout_seconds"], 600)
        self.assertEqual(generated[0]["access_interval_seconds"], 0)
        self.assertIn("untrusted_candidate", generated[-1]["prompt"])
        self.assertIn("原始候选回答", generated[-1]["prompt"])
        fusion = data["fusion"]
        self.assertEqual(len(fusion["sources"]), 2)
        self.assertEqual(Path(fusion["merged_path"]).read_text(), data["choices"][0]["message"]["content"])
        for source in fusion["sources"]:
            self.assertEqual(Path(source["path"]).read_text(), source["markdown"])
        history = self.client.get("/internal/history/" + fusion["conversation_id"], headers=AUTH).json()
        self.assertEqual(history["runs"][0]["sources"], fusion["sources"])
        self.assertEqual(history["messages"][-1], data["choices"][0]["message"])
        self.assertEqual((Path(self.temporary.name) / "config.json").stat().st_mode & 0o777, 0o600)

    def test_partial_response_explicitly_attributes_missing_provider(self):
        self.client.app.state.engine.store.config.allow_partial = True
        with website_bridge(self.client, fail=("deepseek",)):
            response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat())
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["fusion"]["errors"][0]["provider"], "deepseek")
        self.assertIn("未成功返回：deepseek", data["choices"][0]["message"]["content"])

    def test_all_failed_and_fusion_failed_never_concatenate(self):
        with website_bridge(self.client, fail=("chatgpt", "deepseek")):
            response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "candidate_generation_failed")
        # ChatGPT also performs synthesis: its failure must not be replaced by raw DeepSeek text.
        with website_bridge(self.client, fail=("chatgpt",)):
            response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(response.json()["error"]["details"]["sources"]), 1)
        self.assertEqual(self.client.get("/internal/history", headers=AUTH).json()["conversations"], [])

    def test_http_502_retains_request_ids_provider_cause_and_logged_payload(self):
        with website_bridge(self.client, fail=("chatgpt",)) as packets:
            response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(model="chatgpt"))
        self.assertEqual(response.status_code, 502)
        error = response.json()["error"]
        self.assertEqual(error["code"], "candidate_generation_failed")
        self.assertEqual(error["details"]["errors"][0]["code"], "login_required")
        request_id, http_id = response.headers["X-Request-ID"], response.headers["X-HTTP-ID"]
        self.assertEqual(request_id, error["details"]["request_id"])
        generated = [packet for packet in packets if packet["type"] == "generate"]
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["request_id"], request_id)
        self.assertEqual(generated[0]["http_id"], http_id)
        contents = (Path(self.temporary.name) / "logs" / "backend.log").read_text()
        records = [json.loads(line) for line in contents.splitlines()]
        rejected = next(record for record in records if record["event"] == "http.rejected" and record["request_id"] == request_id)
        self.assertEqual(rejected["status"], 502)
        self.assertEqual(rejected["http_id"], http_id)
        self.assertEqual(json.loads(rejected["payload"])["details"]["errors"][0]["code"], "login_required")
        self.assertNotIn(TOKEN, contents)

    def test_early_http_error_has_http_id_without_inventing_generation_id(self):
        response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(model="not-a-model"))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.headers["X-HTTP-ID"])
        self.assertNotIn("X-Request-ID", response.headers)

    def test_single_provider_and_buffered_sse(self):
        with website_bridge(self.client) as packets:
            response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(model="web-deepseek", stream=True))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        records = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(records[-1], "[DONE]")
        chunks = [json.loads(line) for line in records[:-1]]
        content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        self.assertTrue(content.startswith("# deepseek"))
        self.assertEqual(chunks[-1]["fusion"]["mode"], "single")
        self.assertEqual(len([p for p in packets if p["type"] == "generate"]), 1)

    def test_glm_and_kimi_are_openai_compatible_single_model_aliases_when_enabled(self):
        config = self.client.app.state.engine.store.config.model_dump()
        for provider in config["providers"]:
            provider["enabled"] = provider["id"] in ("glm", "kimi")
        config["fusion"]["provider"] = "glm"
        self.client.app.state.engine.store.write_config(AppConfig.model_validate(config))
        catalog = [row["id"] for row in self.client.get("/v1/models", headers=AUTH).json()["data"]]
        self.assertEqual(catalog, ["web-fusion", "web-glm", "web-kimi", "glm", "kimi"])
        for model, provider in (("glm", "glm"), ("web-kimi", "kimi")):
            with website_bridge(self.client) as packets:
                response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(model=model))
            self.assertEqual(response.status_code, 200, response.text)
            generated = [packet for packet in packets if packet["type"] == "generate"]
            self.assertEqual([(packet["provider_id"], packet["purpose"]) for packet in generated], [(provider, "candidate")])
            self.assertEqual(response.json()["fusion"]["mode"], "single")

    def test_sse_terminal_error(self):
        with website_bridge(self.client, fail=("chatgpt", "deepseek")):
            response = self.client.post("/v1/chat/completions", headers=AUTH, json=chat(stream=True))
        records = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(json.loads(records[0])["error"]["code"], "candidate_generation_failed")
        self.assertEqual(records[-1], "[DONE]")

    def test_configuration_rejects_self_loop_and_missing_selectors(self):
        config = configuration()
        config["fusion"].update(mode="api", model="web-fusion", base_url="http://127.0.0.1:8765/v1")
        self.assertEqual(self.client.put("/internal/config", headers=AUTH, json=config).status_code, 422)
        config = configuration()
        config["providers"][0]["selectors"]["assistant"] = []
        self.assertEqual(self.client.put("/internal/config", headers=AUTH, json=config).status_code, 422)


class APITransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_synthesis_uses_model_response_and_rejects_truncation(self):
        config_data = configuration()
        config_data["fusion"].update(mode="api", base_url="http://127.0.0.1:11434/v1", model="test-synthesizer", api_key="secret-test-key")
        config = AppConfig.model_validate(config_data)
        seen = []

        def api(request):
            seen.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "# Semantically synthesized"}, "finish_reason": "stop"}]})

        original_client = httpx.AsyncClient
        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(api), **kwargs)):
            result = await synthesize_api("combined source material", config)
        self.assertEqual(result, "# Semantically synthesized")
        self.assertEqual(seen[0].url.path, "/v1/chat/completions")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer secret-test-key")
        self.assertEqual(json.loads(seen[0].content)["model"], "test-synthesizer")
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}))
        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original_client(transport=transport, **kwargs)):
            with self.assertRaises(FusionError) as caught:
                await synthesize_api("sources", config)
        self.assertEqual(caught.exception.code, "fusion_truncated")

    async def test_api_http_error_preserves_nested_provider_code_without_retry(self):
        config_data = configuration()
        config_data["fusion"].update(mode="api", base_url="http://127.0.0.1:11434/v1", model="test-synthesizer")
        config = AppConfig.model_validate(config_data)
        seen = []

        def api(request):
            seen.append(request)
            return httpx.Response(502, json={"error": {"code": "upstream_unavailable", "message": "Provider connection closed",
                                                       "details": {"cause": {"code": "socket_reset"}}}})

        original_client = httpx.AsyncClient
        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(api), **kwargs)):
            with self.assertRaises(FusionError) as caught:
                await synthesize_api("source material", config)
        self.assertEqual(len(seen), 1)
        self.assertEqual(caught.exception.code, "fusion_api_http_error")
        self.assertEqual(caught.exception.details["status"], 502)
        self.assertEqual(caught.exception.details["cause"]["error"]["details"]["cause"]["code"], "socket_reset")

    async def test_api_error_echoed_credentials_and_html_never_reach_error_payload(self):
        config_data = configuration()
        config_data["fusion"].update(mode="api", base_url="http://127.0.0.1:11434/v1", model="test-synthesizer", api_key="fixture-api-key")
        config = AppConfig.model_validate(config_data)
        bodies = [httpx.Response(401, json={"error": {"code": "authentication_failed", "message": "Authorization: Bearer fixture-api-key",
                                                     "api_key": "fixture-api-key", "headers": {"Authorization": "Bearer unknown-header"},
                                                     "details": {"cause": {"code": "proxy_rejected", "message": "token=fixture-api-key"}}}}),
                  httpx.Response(502, text='<html><form><input value="fixture-api-key"></form>private proxy body</html>')]
        original_client = httpx.AsyncClient
        for response in bodies:
            with self.subTest(status=response.status_code):
                with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(lambda request: response), **kwargs)):
                    with self.assertRaises(FusionError) as caught:
                        await synthesize_api("sources", config)
                payload = json.dumps(caught.exception.payload())
                self.assertNotIn("fixture-api-key", payload)
                self.assertNotIn("unknown-header", payload)
                self.assertNotIn("private proxy body", payload)
                self.assertNotIn("<html>", payload)
                self.assertEqual(caught.exception.details["status"], response.status_code)
                if response.status_code == 401:
                    cause = caught.exception.details["cause"]["error"]
                    self.assertEqual(cause["code"], "authentication_failed")
                    self.assertEqual(cause["details"]["cause"]["code"], "proxy_rejected")


if __name__ == "__main__":
    unittest.main()
