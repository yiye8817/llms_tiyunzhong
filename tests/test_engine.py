"""Core scheduler tests requiring only Pydantic and the Python standard library."""

import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.engine import Bridge, Engine, FusionError, available_models, conversation_prompt, synthesis_prompt, json_artifact
from backend.diagnostics import configure_logging, shutdown_logging
from backend.models import AppConfig, ChatRequest
from backend.storage import Store

TOKEN = "test-only-token-at-least-32-characters"

def chat(**overrides):
    return dict({"model": "web-fusion", "messages": [{"role": "user", "content": "比较 Android 内存泄漏定位方法"}]}, **overrides)


class JSONArtifactTests(unittest.TestCase):
    def test_only_unique_object_or_array_json_gets_canonical_artifact(self):
        self.assertEqual(json.loads(json_artifact('{"b":[1],"a":"中文"}')), {"b": [1], "a": "中文"})
        self.assertEqual(json.loads(json_artifact('[{"ok":true}]')), [{"ok": True}])
        self.assertIsNone(json_artifact('说明文字'))
        self.assertIsNone(json_artifact('{"a":1,"a":2}'))
        self.assertIsNone(json_artifact('{"a":NaN}'))


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environ = patch.dict(os.environ, {"FUSION_TOKEN": TOKEN})
        self.environ.start()
        self.store = Store(Path(self.temporary.name))
        self.engine = Engine(self.store)

    async def asyncTearDown(self):
        await self.engine.close()
        self.store.close()
        self.environ.stop()
        self.temporary.cleanup()

    async def test_global_serialization_and_parallel_candidates(self):
        gate = asyncio.Event()
        calls = []

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                calls.append((provider, purpose, prompt))
                if purpose == "candidate":
                    await gate.wait()
                return "# semantic result" if purpose == "fusion" else provider + " original"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        first = self.engine.submit(ChatRequest.model_validate(chat()))
        second = self.engine.submit(ChatRequest.model_validate(chat(messages=[{"role": "user", "content": "SECOND TURN"}])))
        for _ in range(50):
            if len(calls) == 2:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(purpose == "candidate" for _, purpose, _ in calls))
        self.assertFalse(any("SECOND TURN" in prompt for _, _, prompt in calls))
        self.assertEqual(len(self.engine.queue), 1)
        gate.set()
        await asyncio.wait_for(asyncio.gather(first.future, second.future), 3)
        self.assertEqual([c[1] for c in calls], ["candidate", "candidate", "fusion", "candidate", "candidate", "fusion"])

    async def test_original_save_failure_is_logged_as_failed_and_blocks_fusion(self):
        calls, records = [], []
        save = self.store.write_markdown

        def fail_one_save(request_id, filename, content):
            if filename == "chatgpt.md":
                raise OSError("fixture storage error")
            return save(request_id, filename, content)

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                calls.append((provider, purpose))
                return provider + " original"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        with patch.object(self.store, "write_markdown", side_effect=fail_one_save), patch("backend.engine.event", side_effect=lambda name, **fields: records.append((name, fields))):
            turn = self.engine.submit(ChatRequest.model_validate(chat()))
            with self.assertRaises(FusionError):
                await asyncio.wait_for(turn.future, 2)
        last = [fields["payload"] for name, fields in records if name == "models.progress"][-1]
        self.assertEqual(last["pending"], [])
        self.assertEqual(last["failed"], ["chatgpt"])
        self.assertEqual(last["completed"], ["deepseek"])
        self.assertTrue(any(name == "model.failed" and fields["provider"] == "chatgpt" for name, fields in records))
        self.assertFalse(any(name == "model.completed" and fields["provider"] == "chatgpt" for name, fields in records))
        self.assertTrue(all(purpose == "candidate" for _, purpose in calls))

    async def test_slow_last_candidate_is_required_before_fusion_and_progress_lists_pending(self):
        slow_started, release_slow = asyncio.Event(), asyncio.Event()
        calls = []
        records = []

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                calls.append((provider, purpose))
                if purpose == "candidate" and provider == "deepseek":
                    slow_started.set()
                    await release_slow.wait()
                return provider + " complete response"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        with patch("backend.engine.event", side_effect=lambda name, **fields: records.append((name, fields))):
            turn = self.engine.submit(ChatRequest.model_validate(chat()))
            await asyncio.wait_for(slow_started.wait(), 2)
            self.assertFalse(turn.future.done())
            self.assertEqual(calls, [("chatgpt", "candidate"), ("deepseek", "candidate")])
            self.assertTrue((self.store.root / "runs" / turn.request_id / "chatgpt.md").exists())
            self.assertFalse((self.store.root / "runs" / turn.request_id / "merged.md").exists())
            self.assertTrue(any(name == "models.progress" and fields["payload"]["pending"] == ["deepseek"] for name, fields in records))
            release_slow.set()
            result = await asyncio.wait_for(turn.future, 2)
        self.assertEqual(len(result["fusion"]["sources"]), 2)
        self.assertEqual(calls[-1], ("chatgpt", "fusion"))
        events = [name for name, _ in records]
        terminal_index = next(i for i, (name, fields) in enumerate(records) if name == "models.progress" and fields["state"] == "all_terminal")
        self.assertLess(terminal_index, events.index("fusion.started"))

    async def test_default_strict_mode_waits_remaining_candidate_after_peer_failure_and_blocks_fusion(self):
        slow_started, release_slow = asyncio.Event(), asyncio.Event()
        calls = []
        self.assertFalse(self.store.config.allow_partial)

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                calls.append((provider, purpose))
                if provider == "chatgpt":
                    raise FusionError("submission_unconfirmed", "Website did not accept the prompt")
                slow_started.set()
                await release_slow.wait()
                return "slow but complete response"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat()))
        await asyncio.wait_for(slow_started.wait(), 2)
        self.assertFalse(turn.future.done(), "An early peer error must not cancel the remaining response")
        release_slow.set()
        with self.assertRaises(FusionError) as caught:
            await asyncio.wait_for(turn.future, 2)
        self.assertEqual(caught.exception.code, "candidate_generation_failed")
        self.assertEqual(caught.exception.details["sources"][0]["markdown"], "slow but complete response")
        self.assertTrue((self.store.root / "runs" / turn.request_id / "deepseek.md").exists())
        self.assertFalse((self.store.root / "runs" / turn.request_id / "merged.md").exists())
        self.assertEqual(calls, [("chatgpt", "candidate"), ("deepseek", "candidate")])

    async def test_login_import_lease_blocks_submission_and_releases(self):
        self.engine.bridge.websocket, self.engine.bridge.ready = object(), True
        try:
            lease = self.engine.acquire_browser_maintenance()
            self.assertTrue(self.engine.busy)
            with self.assertRaises(FusionError) as error:
                self.engine.submit(ChatRequest.model_validate(chat()))
            self.assertEqual(error.exception.code, "browser_maintenance_busy")
            with self.assertRaises(FusionError):
                self.engine.acquire_browser_maintenance()
            self.engine.release_browser_maintenance(lease)
            self.assertFalse(self.engine.busy)
            turn = self.engine.submit(ChatRequest.model_validate(chat()))
            self.engine.cancel(turn.request_id, disconnected=True)
        finally:
            self.engine.bridge.websocket = None

    async def test_login_import_rejects_wrong_lease_without_unlocking(self):
        self.engine.bridge.websocket, self.engine.bridge.ready = object(), True
        try:
            lease = self.engine.acquire_browser_maintenance()
            with self.assertRaises(FusionError):
                self.engine.release_browser_maintenance("wrong-lease")
            self.assertEqual(self.engine.maintenance_lease, lease)
            self.engine.release_browser_maintenance(lease)
        finally:
            self.engine.bridge.websocket = None

    async def test_login_import_cannot_interrupt_queued_chat(self):
        self.engine.bridge.websocket, self.engine.bridge.ready = object(), True
        try:
            turn = self.engine.submit(ChatRequest.model_validate(chat()))
            with self.assertRaises(FusionError) as error:
                self.engine.acquire_browser_maintenance()
            self.assertEqual(error.exception.status, 409)
            self.assertIsNone(self.engine.maintenance_lease)
            self.engine.cancel(turn.request_id, disconnected=True)
        finally:
            self.engine.bridge.websocket = None

    async def test_bridge_disconnect_revokes_import_lease_but_stale_close_does_not(self):
        socket = object()
        self.engine.bridge.websocket, self.engine.bridge.ready = socket, True
        lease = self.engine.acquire_browser_maintenance()
        self.engine.on_bridge_disconnect(object())
        self.assertEqual(self.engine.maintenance_lease, lease)
        self.engine.on_bridge_disconnect(socket)
        self.assertIsNone(self.engine.maintenance_lease)
        self.assertFalse(self.engine.busy)
        self.assertFalse(self.engine.bridge.connected)

    async def test_queue_capacity_and_cancel_release_capacity(self):
        self.engine.bridge.ready = True
        self.engine.bridge.websocket = object()
        self.engine.MAX_QUEUE = 1
        first = self.engine.submit(ChatRequest.model_validate(chat()))
        with self.assertRaises(FusionError) as caught:
            self.engine.submit(ChatRequest.model_validate(chat()))
        self.assertEqual(caught.exception.status, 429)
        self.assertTrue(self.engine.cancel(first.request_id, disconnected=True))
        self.assertEqual(len(self.engine.queue), 0)
        second = self.engine.submit(ChatRequest.model_validate(chat()))
        self.engine.cancel(second.request_id, disconnected=True)
        self.engine.bridge.websocket = None

    async def test_bridge_disconnect_unblocks_pending_request(self):
        class Socket:
            async def send_json(self, _packet):
                pass
        bridge = Bridge()
        socket = Socket()
        bridge.websocket, bridge.ready = socket, True
        pending = asyncio.create_task(bridge.generate("chatgpt", "test", "candidate", self.store.config))
        await asyncio.sleep(0)
        self.assertEqual(len(bridge.pending), 1)
        bridge.disconnect(socket)
        with self.assertRaises(FusionError) as caught:
            await pending
        self.assertEqual(caught.exception.code, "bridge_disconnected")
        self.assertFalse(bridge.pending)

    async def test_partial_failure_synthesis_attribution_and_original_files(self):
        self.store.config.allow_partial = True  # Deliberate opt-in remains supported.
        calls = []

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                calls.append((provider, purpose, prompt))
                if provider == "deepseek":
                    raise FusionError("login_required", "Log in manually")
                return "# Synthesized answer" if purpose == "fusion" else "# Original\n\n```c\nfree(ptr);\n```"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat()))
        result = await asyncio.wait_for(turn.future, 2)
        self.assertIn("未成功返回：deepseek", result["choices"][0]["message"]["content"])
        source = result["fusion"]["sources"][0]
        self.assertEqual(Path(source["path"]).read_text(), source["markdown"])
        self.assertIn("缺席模型", calls[-1][2])
        self.assertEqual(result["fusion"]["errors"][0]["code"], "login_required")
        self.assertEqual(self.store.conversation(turn.conversation_id)["runs"][0]["sources"], result["fusion"]["sources"])

    async def test_fusion_failure_does_not_return_concatenated_candidates(self):
        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                if purpose == "fusion":
                    raise FusionError("provider_timeout", "No synthesis output", 504)
                return provider + " original"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat()))
        with self.assertRaises(FusionError) as caught:
            await asyncio.wait_for(turn.future, 2)
        self.assertEqual(caught.exception.code, "provider_timeout")
        self.assertEqual(len(caught.exception.details["sources"]), 2)
        run = Path(self.temporary.name) / "runs" / turn.request_id
        self.assertTrue((run / "chatgpt.md").is_file())
        self.assertFalse((run / "merged.md").exists())
        self.assertEqual(self.store.history(), [])

    async def test_active_cancel_saves_completed_original_and_next_request_runs(self):
        waiting = asyncio.Event()
        never = asyncio.Event()

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                if provider == "deepseek" and "CANCEL ME" in prompt:
                    waiting.set()
                    await never.wait()
                return "finished original"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        first = self.engine.submit(ChatRequest.model_validate(chat(messages=[{"role": "user", "content": "CANCEL ME"}])))
        second = self.engine.submit(ChatRequest.model_validate(chat(model="web-chatgpt")))
        await asyncio.wait_for(waiting.wait(), 2)
        self.engine.cancel(first.request_id)
        with self.assertRaises(FusionError) as caught:
            await first.future
        self.assertEqual(caught.exception.code, "cancelled")
        result = await asyncio.wait_for(second.future, 2)
        self.assertEqual(result["fusion"]["mode"], "single")
        self.assertTrue((Path(self.temporary.name) / "runs" / first.request_id / "chatgpt.md").is_file())

    async def test_expired_queue_never_submits_to_website(self):
        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, *args):
                raise AssertionError("expired request must not reach a website")

        self.engine.bridge = FakeBridge()
        turn = self.engine.submit(ChatRequest.model_validate(chat()))
        turn.queued_at -= 301
        self.engine.start()
        with self.assertRaises(FusionError) as caught:
            await asyncio.wait_for(turn.future, 2)
        self.assertEqual(caught.exception.code, "queue_timeout")

    async def test_bridge_timeout_cancels_once_without_retry(self):
        packets = []

        class Socket:
            async def send_json(self, packet):
                packets.append(packet)

        bridge = Bridge()
        bridge.websocket, bridge.ready = Socket(), True
        original_wait_for = asyncio.wait_for

        async def timeout_generation(awaitable, timeout):
            if timeout == 10:
                return await original_wait_for(awaitable, timeout)
            raise asyncio.TimeoutError

        with patch("backend.engine.asyncio.wait_for", side_effect=timeout_generation):
            with self.assertRaises(FusionError) as caught:
                await bridge.generate("chatgpt", "test", "candidate", self.store.config)
        self.assertEqual(caught.exception.code, "provider_timeout")
        self.assertEqual([p["type"] for p in packets], ["generate", "cancel"])
        self.assertFalse(bridge.pending)

    async def test_all_provider_aliases_select_exactly_one_website_without_synthesis(self):
        calls = []

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                calls.append((provider, purpose))
                return "# Single answer\n\n```sh\necho ok\n```"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        with patch("backend.engine.synthesize_api", side_effect=AssertionError("single mode must never synthesize")):
            for provider in ("chatgpt", "deepseek", "qwen", "claude", "grok", "glm", "kimi"):
                config = self.store.config.model_dump()
                for item in config["providers"]:
                    item["enabled"] = item["id"] == provider
                config["fusion"]["provider"] = provider
                self.store.write_config(AppConfig.model_validate(config))
                for model in (provider, "web-" + provider):
                    for stream in (False, True):
                        calls.clear()
                        turn = self.engine.submit(ChatRequest.model_validate(chat(model=model, stream=stream)))
                        response = await asyncio.wait_for(turn.future, 2)
                        self.assertEqual(calls, [(provider, "candidate")])
                        self.assertEqual(response["model"], model)
                        self.assertEqual(response["fusion"]["mode"], "single")
                        self.assertEqual(response["choices"][0]["message"]["content"], "# Single answer\n\n```sh\necho ok\n```")
                        self.assertEqual([source["provider"] for source in response["fusion"]["sources"]], [provider])

    async def test_disabled_and_unknown_models_fail_before_queuing_or_dispatch(self):
        for model in ("qwen", "web-qwen", "claude", "grok", "glm", "web-glm", "kimi", "web-kimi"):
            with self.assertRaises(FusionError) as error:
                self.engine.submit(ChatRequest.model_validate(chat(model=model)))
            self.assertEqual(error.exception.code, "model_not_enabled")
            self.assertEqual(error.exception.status, 404)
        for model in ("unknown", "web-unknown", "web-fusion-typo"):
            with self.assertRaises(FusionError) as error:
                self.engine.submit(ChatRequest.model_validate(chat(model=model)))
            self.assertEqual(error.exception.code, "model_not_found")
        self.assertEqual(len(self.engine.queue), 0)
        self.assertFalse(self.engine.bridge.pending)

    async def test_model_catalog_contains_enabled_canonical_and_alias_ids_only(self):
        self.assertEqual(available_models(self.store.config), ["web-fusion", "web-chatgpt", "web-deepseek", "chatgpt", "deepseek"])

    async def test_custom_provider_keeps_canonical_web_id(self):
        calls = []

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                calls.append((provider, purpose))
                return "custom answer"

        config = self.store.config.model_dump()
        config["providers"][1]["id"] = "custom-site"
        self.store.write_config(AppConfig.model_validate(config))
        self.assertIn("web-custom-site", available_models(self.store.config))
        self.assertNotIn("custom-site", available_models(self.store.config))
        self.engine.bridge = FakeBridge()
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat(model="web-custom-site")))
        response = await asyncio.wait_for(turn.future, 2)
        self.assertEqual(calls, [("custom-site", "candidate")])
        self.assertEqual(response["fusion"]["mode"], "single")

    async def test_generated_packet_and_full_logs_correlate_request_http_and_job_ids(self):
        packets = []
        terminal = io.StringIO()
        path = configure_logging(Path(self.temporary.name) / "logs", terminal)

        class Socket:
            async def send_json(_self, packet):
                packets.append(packet)
                if packet["type"] == "generate":
                    self.engine.bridge.receive({"type": "result", "job_id": packet["job_id"],
                                                "provider_id": packet["provider_id"], "markdown": "one answer"})

            async def close(_self, **kwargs):
                pass

        try:
            self.engine.bridge.websocket, self.engine.bridge.ready = Socket(), True
            self.engine.start()
            turn = self.engine.submit(ChatRequest.model_validate(chat(model="chatgpt")), http_id="test-http-id")
            await asyncio.wait_for(turn.future, 2)
            self.assertEqual(len(packets), 1)
            self.assertEqual(packets[0]["request_id"], turn.request_id)
            self.assertEqual(packets[0]["http_id"], "test-http-id")
            contents = path.read_text()
            self.assertEqual(contents, terminal.getvalue())
            records = [json.loads(line) for line in contents.splitlines()]
            by_name = {record["event"]: record for record in records}
            self.assertEqual(json.loads(by_name["bridge.dispatch"]["payload"]), packets[0])
            self.assertEqual(json.loads(by_name["bridge.result"]["payload"])["content"], "one answer")
            self.assertEqual(json.loads(by_name["request.completed"]["payload"])["response"]["choices"][0]["message"]["content"], "one answer")
            for name in ("bridge.dispatch", "bridge.dispatched", "bridge.result"):
                self.assertEqual(by_name[name]["job_id"], packets[0]["job_id"])
                self.assertEqual(by_name[name]["request_id"], turn.request_id)
                self.assertEqual(by_name[name]["http_id"], "test-http-id")
        finally:
            shutdown_logging()

    async def test_single_model_failure_never_falls_back_to_other_provider(self):
        calls = []

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                calls.append((provider, purpose))
                raise FusionError("submit_not_confirmed", "Website submission could not be confirmed")

        self.engine.bridge = FakeBridge()
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat(model="chatgpt")))
        with self.assertRaises(FusionError) as error:
            await asyncio.wait_for(turn.future, 2)
        self.assertEqual(calls, [("chatgpt", "candidate")])
        self.assertEqual(error.exception.code, "candidate_generation_failed")
        self.assertEqual(error.exception.details["errors"][0]["code"], "submit_not_confirmed")

    @patch.dict(os.environ, {"FUSION_LOG_CONTENT": "0"})
    async def test_request_lifecycle_metadata_opt_out_omits_conversation_and_output(self):
        terminal = io.StringIO()
        path = configure_logging(Path(self.temporary.name) / "logs", terminal)

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                return "PRIVATE MARKDOWN body"

        try:
            self.engine.bridge = FakeBridge()
            self.engine.start()
            turn = self.engine.submit(ChatRequest.model_validate(chat(model="chatgpt", messages=[{"role": "user", "content": "PRIVATE USER message"}])))
            await asyncio.wait_for(turn.future, 2)
            contents = path.read_text()
            self.assertEqual(contents, terminal.getvalue())
            self.assertNotIn("PRIVATE USER", contents)
            self.assertNotIn("PRIVATE MARKDOWN", contents)
            records = [json.loads(line) for line in contents.splitlines()]
            request_events = [record["event"] for record in records if record.get("request_id") == turn.request_id]
            for name in ("request.queued", "request.started", "model.started", "model.completed", "artifact.saved", "history.saved", "request.completed"):
                self.assertIn(name, request_events)
            self.assertNotIn("fusion.started", request_events)
        finally:
            shutdown_logging()

    async def test_bridge_provider_error_preserves_nested_cause_without_retry(self):
        packets = []

        class Socket:
            async def send_json(_self, packet):
                packets.append(packet)
                if packet["type"] == "generate":
                    self.engine.bridge.receive({"type": "error", "job_id": packet["job_id"], "provider_id": packet["provider_id"],
                                                "error": {"code": "provider_failed", "message": "Generation failed",
                                                          "details": {"cause": {"code": "submit_not_confirmed", "message": "No submission evidence"}}}})

            async def close(_self, **kwargs):
                pass

        self.engine.bridge.websocket, self.engine.bridge.ready = Socket(), True
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat(model="chatgpt")))
        with self.assertRaises(FusionError) as caught:
            await asyncio.wait_for(turn.future, 2)
        self.assertEqual(caught.exception.code, "candidate_generation_failed")
        self.assertEqual(caught.exception.details["errors"][0]["details"]["cause"]["code"], "submit_not_confirmed")
        self.assertEqual([packet["type"] for packet in packets], ["generate", "cancel"])

    async def test_partial_fusion_json_remains_parseable_without_markdown_prefix(self):
        self.store.config.allow_partial = True
        expected = {"type": "final", "content": "useful answer"}

        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                if purpose == "candidate" and provider == "deepseek":
                    raise FusionError("login_required", "Please log in")
                return json.dumps(expected)

        self.engine.bridge = FakeBridge()
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat(messages=[{"role": "system", "content": "Return strict JSON only."},
                                                                          {"role": "user", "content": "Answer now"}])))
        response = await asyncio.wait_for(turn.future, 2)
        self.assertEqual(json.loads(response["choices"][0]["message"]["content"]), expected)
        self.assertEqual(response["fusion"]["errors"][0]["provider"], "deepseek")

    async def test_fusion_error_keeps_provider_details_when_adding_saved_source_paths(self):
        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                if purpose == "fusion":
                    raise FusionError("fusion_api_http_error", "Provider returned HTTP 502", details={"status": 502, "cause": {"code": "upstream_failed"}})
                return "candidate"

        self.engine.bridge = FakeBridge()
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat()))
        with self.assertRaises(FusionError) as caught:
            await asyncio.wait_for(turn.future, 2)
        self.assertEqual(caught.exception.details["cause"]["cause"]["code"], "upstream_failed")
        self.assertEqual(len(caught.exception.details["sources"]), 2)

    async def test_provider_error_artifact_and_api_details_exclude_credentials(self):
        class FakeBridge:
            connected = True
            websocket = None

            async def generate(_self, provider, prompt, purpose, config):
                raise FusionError("provider_failed", "Connection failed token=" + TOKEN,
                                  details={"headers": {"Authorization": "Bearer private-header"}, "env": {"PASSWORD": "private-env"},
                                           "cause": {"code": "socket_reset", "message": "Authorization: Bearer " + TOKEN}})

        self.engine.bridge = FakeBridge()
        self.engine.start()
        turn = self.engine.submit(ChatRequest.model_validate(chat(model="chatgpt")))
        with self.assertRaises(FusionError) as caught:
            await asyncio.wait_for(turn.future, 2)
        artifact = (Path(self.temporary.name) / "runs" / turn.request_id / "errors.json").read_text()
        for content in (artifact, json.dumps(caught.exception.payload())):
            for secret in (TOKEN, "private-header", "private-env"):
                self.assertNotIn(secret, content)
            self.assertIn("socket_reset", content)


class PromptTests(unittest.TestCase):
    def test_conversation_and_synthesis_preserve_agent_json_format_and_role_hierarchy(self):
        messages = [{"role": "system", "content": 'Return a JSON object only: {"type":"final","content":"answer"}.'},
                    {"role": "developer", "content": "Do not add code fences or commentary."},
                    {"role": "user", "content": "What is 2+2?"}]
        request = ChatRequest.model_validate(chat(messages=messages))
        for prompt in (conversation_prompt(request), synthesis_prompt(request, [{"provider": "chatgpt", "markdown": '{"type":"final","content":"4"}'}], [])):
            for message in messages:
                self.assertIn(message["content"], prompt)
            self.assertIn("system 高于 developer，高于 user", prompt)
            self.assertIn("仅当原始对话没有指定输出格式时", prompt)
            self.assertIn("不添加 Markdown 围栏、解释或前后缀", prompt)
            self.assertNotIn("输出完整 Markdown 正文", prompt)
            self.assertNotIn("最终只输出整合后的 Markdown 正文", prompt)


if __name__ == "__main__":
    unittest.main()
