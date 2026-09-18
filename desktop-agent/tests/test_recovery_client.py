import io
import json
import unittest
from unittest.mock import patch

from fusion_agent.client import ClientError, FusionClient
from fusion_agent import parent_service
from fusion_agent.config import Settings
from fusion_agent.terminal import TerminalProgress


class Reply(io.BytesIO):
    status = 200
    headers = {}


class RecoveryClientTests(unittest.TestCase):
    def status(self, metadata, service="multillm-fusion", capability=1):
        value = {"bridge_connected": True, "busy": False, "providers": {}, "service": service,
                 "capabilities": {"web_recovery": capability}, "web_recovery": metadata}
        with patch.object(parent_service, "api_key", return_value="fixture-key"), \
             patch.object(parent_service, "_target", return_value={"managed": True, "base_url": "http://127.0.0.1:8765/v1", "origin": "http://127.0.0.1:8765"}), \
             patch.object(parent_service, "_json_get", side_effect=[{"kind": "ok", "value": {"status": "ok"}}, {"kind": "ok", "value": value}]):
            return parent_service.parent_status(Settings())

    def test_parent_handshake_validates_finite_bounded_recovery_budget(self):
        valid = {"max_wait_seconds": 1890.5, "recovery_timeout_seconds": 180.0}
        report = self.status(valid)
        self.assertTrue(report["web_recovery_supported"])
        self.assertEqual(report["web_recovery_timeout"], 1891)
        for metadata in ({}, {**valid, "max_wait_seconds": True}, {**valid, "max_wait_seconds": 7201},
                         {**valid, "max_wait_seconds": float("nan")}, {**valid, "recovery_timeout_seconds": float("inf")},
                         {**valid, "recovery_timeout_seconds": -1}):
            with self.subTest(metadata=metadata):
                self.assertFalse(self.status(metadata)["web_recovery_supported"])
        self.assertFalse(self.status(valid, service="other")["web_recovery_supported"])
        self.assertFalse(self.status(valid, capability=True)["web_recovery_supported"])

    def test_only_completion_gets_extended_budget_without_repeated_post(self):
        client = FusionClient("http://127.0.0.1:8765/v1", "fixture-key", timeout=15, web_recovery_timeout=1890)
        responses = [Reply(json.dumps({"choices": [{"message": {"content": "complete"}}]}).encode()),
                     Reply(json.dumps({"data": [{"id": "qwen"}]}).encode())]
        with patch.object(client._opener, "open", side_effect=responses) as transport:
            self.assertEqual(client.complete([{"role": "user", "content": "task"}]), "complete")
            self.assertEqual(client.models()[0]["id"], "qwen")
        self.assertEqual([call.kwargs["timeout"] for call in transport.call_args_list], [1890, 15])
        self.assertEqual([call.args[0].method for call in transport.call_args_list], ["POST", "GET"])
        generic = FusionClient("https://example.invalid/v1", "fixture-key", timeout=30)
        self.assertIsNone(generic.web_recovery_timeout)
        for value in (True, 0, -1, 7201, "180", float("nan")):
            with self.subTest(value=value), self.assertRaises(ClientError):
                FusionClient("http://127.0.0.1:8765/v1", "fixture-key", web_recovery_timeout=value)

    def test_manual_recovery_terminal_identifies_provider_without_claiming_completion(self):
        stream = io.StringIO()
        progress = TerminalProgress(stream)
        for stage in ("retrying", "manual_retry_required", "manual_retry_required", "recovering"):
            progress("model.web_progress", {"stage": stage, "provider": "qwen", "progress_id": "fixture"})
        output = stream.getvalue()
        self.assertIn("qwen", output)
        self.assertEqual(output.count("请在此模型网页"), 1)
        self.assertIn("继续等待并采集", output)
        self.assertNotIn("任务完成", output)


if __name__ == "__main__":
    unittest.main()
