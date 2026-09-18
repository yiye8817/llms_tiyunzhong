"""Request-scoped webpage stages without exposing chat contents."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import app
from backend.engine import Bridge, _request_id
from backend.models import AppConfig
from backend.progress import ProgressStore

TOKEN = "progress-test-token-at-least-32-characters"
AUTH = {"Authorization": "Bearer " + TOKEN}


class ProgressStoreTests(unittest.TestCase):
    def test_isolation_cursor_completion_and_expiry(self):
        now = [0]
        store = ProgressStore(clock=lambda: now[0])
        self.assertTrue(store.register("a", "request-a"))
        self.assertTrue(store.register("b", "request-b"))
        self.assertFalse(store.register("a", "other"))
        store.add("request-a", "accepted", provider="qwen")
        store.add("request-b", "server_responded", provider="deepseek", http_status=429)
        self.assertEqual(store.get("a", 1)["events"], [{"sequence": 2, "stage": "accepted", "provider": "qwen"}])
        self.assertEqual(store.get("b")["events"][-1]["http_status"], 429)
        store.finish("request-a", "completed")
        store.add("request-a", "generating")
        self.assertEqual(store.get("a")["events"][-1]["stage"], "completed")
        self.assertTrue(store.get("a")["done"])
        now[0] = 121
        self.assertIsNone(store.get("a"))
        self.assertEqual(store.requests, {"request-b": "b"})

    def test_event_and_record_buffers_are_bounded(self):
        store = ProgressStore()
        store.register("a", "r")
        for i in range(1000):
            store.add("r", "generating", provider="qwen" if i % 2 else "deepseek")
        report = store.get("a")
        self.assertEqual(len(report["events"]), 256)
        self.assertTrue(report["truncated"])
        store.finish("r", "completed")
        for i in range(store.MAX_RECORDS + 10):
            if store.register(str(i), str(i)):
                store.finish(str(i), "completed")
        self.assertEqual(len(store.records), store.MAX_RECORDS)
        self.assertEqual(len(store.requests), store.MAX_RECORDS)


class BridgeProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_live_job_and_provider_can_report_progress(self):
        store = ProgressStore()
        store.register("key", "request")
        packets = []
        class Socket:
            async def send_json(self, packet):
                packets.append(packet)
        bridge = Bridge(store)
        bridge.websocket, bridge.ready = Socket(), True
        token = _request_id.set("request")
        try:
            config = AppConfig.model_validate_json((Path(__file__).resolve().parents[1] / "config.example.json").read_text())
            task = asyncio.create_task(bridge.generate("qwen", "PRIVATE PROMPT", "candidate", config))
        finally:
            _request_id.reset(token)
        for _ in range(20):
            if packets:
                break
            await asyncio.sleep(0)
        job = packets[0]
        common = {"type": "progress", "job_id": job["job_id"], "provider_id": "qwen"}
        bridge.receive({**common, "stage": "accepted", "provider_id": "deepseek"})
        bridge.receive({**common, "stage": "accepted", "job_id": str(uuid4())})
        bridge.receive({**common, "stage": ["bad"]})
        bridge.receive({**common, "stage": "server_responded", "http_status": 200,
                        "request_id": "FORGED", "body": "PRIVATE", "url": "SECRET"})
        self.assertEqual(store.get("key")["events"][-1], {"sequence": 3, "stage": "server_responded",
                          "provider": "qwen", "purpose": "candidate", "http_status": 200})
        bridge.receive({**common, "type": "result", "markdown": "# Result"})
        self.assertEqual(await task, "# Result")
        self.assertFalse(bridge.pending)
        self.assertFalse(bridge.job_context)
        before = store.get("key")
        bridge.receive({**common, "stage": "generating"})
        self.assertEqual(store.get("key"), before)
        self.assertNotIn("PRIVATE", json.dumps(before))


class HTTPProgressTests(unittest.TestCase):
    def test_authenticated_correlated_endpoint_and_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"FUSION_DATA_DIR": directory, "FUSION_TOKEN": TOKEN,
                                         "FUSION_LOG_DIR": str(Path(directory) / "logs")}):
                with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                    key = str(uuid4())
                    self.assertEqual(client.get("/v1/progress/" + key).status_code, 401)
                    self.assertTrue(client.get("/v1/progress/" + key, headers=AUTH).json()["pending"])
                    status = client.get("/internal/status", headers=AUTH).json()
                    self.assertEqual(status["capabilities"], {"web_progress": 1, "web_recovery": 1})
                    self.assertEqual(status["web_recovery"]["recovery_timeout_seconds"], 180)
                    self.assertEqual(status["web_recovery"]["max_wait_seconds"], 1890)
                    self.assertEqual(client.get("/v1/progress/" + key + "?after=-1", headers=AUTH).status_code, 400)
                    with client.websocket_connect("/internal/bridge", headers=AUTH) as ws:
                        ws.receive_json()
                        ws.send_json({"type": "ready"})
                        def webpage():
                            packet = ws.receive_json()
                            identity = {"job_id": packet["job_id"], "provider_id": packet["provider_id"]}
                            for stage in ("send_dispatched", "accepted", "server_responded", "collecting"):
                                ws.send_json({"type": "progress", **identity, "stage": stage, "http_status": 200,
                                              "payload": "PRIVATE RESPONSE"})
                            ws.send_json({"type": "result", **identity, "markdown": "# Done"})
                        thread = threading.Thread(target=webpage)
                        thread.start()
                        response = client.post("/v1/chat/completions", headers={**AUTH, "X-Fusion-Progress-ID": key},
                                               json={"model": "chatgpt", "messages": [{"role": "user", "content": "SECRET TASK"}]})
                        thread.join(timeout=2)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(response.status_code, 200)
                        report = client.get("/v1/progress/" + key, headers=AUTH).json()
                        self.assertEqual(report["request_id"], response.headers["X-Request-ID"])
                        self.assertTrue(report["done"])
                        self.assertEqual(report["events"][-1]["stage"], "completed")
                        self.assertIn("server_responded", [row["stage"] for row in report["events"]])
                        self.assertNotIn("PRIVATE", json.dumps(report))
                        self.assertNotIn("SECRET", json.dumps(report))
                        self.assertEqual(client.get("/v1/progress/" + str(uuid4()), headers=AUTH).json()["events"], [])
                        repeated = client.post("/v1/chat/completions", headers={**AUTH, "X-Fusion-Progress-ID": key},
                                               json={"model": "chatgpt", "messages": [{"role": "user", "content": "NO RESUBMISSION"}]})
                        self.assertEqual(repeated.status_code, 409)
