"""Recovery stays inside one website job and shares a bounded transport budget."""
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
from pydantic import ValidationError

from backend.app import app
from backend.engine import Bridge, _request_id
from backend.models import AppConfig, GenerationSettings
from backend.progress import ProgressStore

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'recovery-fixture-token-at-least-32-characters'
AUTH = {'Authorization': 'Bearer ' + TOKEN}


def config():
    return AppConfig.model_validate_json((ROOT / 'config.example.json').read_text())


class RecoveryBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_wait_includes_recovery_and_dispatches_exactly_one_original_job(self):
        for purpose, base, recovery in [('candidate', 600, 180), ('fusion', 42, 3.5), ('candidate', 600, 0)]:
            with self.subTest(purpose=purpose, recovery=recovery):
                settings = config()
                settings.fusion.timeout_seconds = 42
                settings.generation.recovery_timeout_seconds = recovery
                packets, waits = [], []
                class Socket:
                    async def send_json(self, packet):
                        packets.append(packet)
                bridge = Bridge()
                bridge.websocket, bridge.ready = Socket(), True
                wait_for = asyncio.wait_for
                async def intercept(awaitable, timeout):
                    waits.append(timeout)
                    if timeout != 10:
                        packet = packets[0]
                        bridge.receive({'type': 'result', 'job_id': packet['job_id'], 'provider_id': packet['provider_id'], 'markdown': '# Recovered'})
                    return await wait_for(awaitable, timeout)
                with patch('backend.engine.asyncio.wait_for', side_effect=intercept):
                    self.assertEqual(await bridge.generate('qwen', 'ORIGINAL PROMPT', purpose, settings), '# Recovered')
                self.assertEqual(waits, [10, base + recovery + 5])
                self.assertEqual(len(packets), 1)
                self.assertEqual(packets[0]['recovery_timeout_seconds'], recovery)
                self.assertEqual(packets[0]['prompt'], 'ORIGINAL PROMPT')
                self.assertFalse(bridge.pending)

    async def test_recovery_progress_is_live_job_scoped_and_cannot_finish_a_job(self):
        progress = ProgressStore(); progress.register('progress-key', 'request')
        packets = []
        class Socket:
            async def send_json(self, packet):
                packets.append(packet)
        bridge = Bridge(progress); bridge.websocket, bridge.ready = Socket(), True
        token = _request_id.set('request')
        try:
            task = asyncio.create_task(bridge.generate('qwen', 'PRIVATE', 'candidate', config()))
        finally:
            _request_id.reset(token)
        while not packets:
            await asyncio.sleep(0)
        packet = packets[0]
        identity = {'type': 'progress', 'job_id': packet['job_id'], 'provider_id': 'qwen'}
        for stage in ['retrying', 'manual_retry_required', 'recovering']:
            bridge.receive({**identity, 'stage': stage, 'payload': 'PRIVATE', 'http_status': 503})
            self.assertFalse(task.done())
            row = progress.get('progress-key')['events'][-1]
            self.assertEqual(row['stage'], stage)
            self.assertEqual(row['provider'], 'qwen')
            self.assertNotIn('http_status', row)
            before = progress.get('progress-key')
            bridge.receive({**identity, 'stage': stage, 'provider_id': 'deepseek'})
            bridge.receive({**identity, 'stage': stage, 'job_id': 'other-job'})
            self.assertEqual(before, progress.get('progress-key'))
        bridge.receive({**identity, 'type': 'result', 'markdown': 'Complete answer'})
        self.assertEqual(await task, 'Complete answer')
        self.assertNotIn('PRIVATE', json.dumps(progress.get('progress-key')))


class RecoveryHTTPTests(unittest.TestCase):
    def test_one_http_post_waits_through_manual_recovery_and_returns_complete_content(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'FUSION_DATA_DIR': directory, 'FUSION_TOKEN': TOKEN, 'FUSION_LOG_DIR': str(Path(directory) / 'logs'),
        }):
            with TestClient(app, base_url='http://127.0.0.1:8765') as client:
                progress_id = str(uuid4()); observed = []
                with client.websocket_connect('/internal/bridge', headers=AUTH) as ws:
                    ws.receive_json(); ws.send_json({'type': 'ready'})
                    def webpage():
                        packet = ws.receive_json(); observed.append(packet)
                        identity = {'job_id': packet['job_id'], 'provider_id': packet['provider_id']}
                        for stage in ['send_dispatched', 'server_responded', 'retrying', 'manual_retry_required', 'recovering', 'collecting']:
                            ws.send_json({'type': 'progress', **identity, 'stage': stage, 'http_status': 503})
                        ws.send_json({'type': 'result', **identity, 'markdown': '# Full recovered answer'})
                    thread = threading.Thread(target=webpage); thread.start()
                    response = client.post('/v1/chat/completions', headers={**AUTH, 'X-Fusion-Progress-ID': progress_id},
                        json={'model': 'chatgpt', 'messages': [{'role': 'user', 'content': 'Original task'}]})
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive()); self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['choices'][0]['message']['content'], '# Full recovered answer')
                    self.assertEqual(len(observed), 1)
                    self.assertEqual(observed[0]['recovery_timeout_seconds'], 180)
                    report = client.get('/v1/progress/' + progress_id, headers=AUTH).json()
                    self.assertTrue(report['done'])
                    self.assertEqual([row['stage'] for row in report['events']][-3:], ['provider_completed', 'candidate_saved', 'completed'])
                    self.assertIn('manual_retry_required', [row['stage'] for row in report['events']])

    def test_status_advertises_finite_ceil_budget_for_both_fusion_modes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'FUSION_DATA_DIR': directory, 'FUSION_TOKEN': TOKEN, 'FUSION_LOG_DIR': str(Path(directory) / 'logs'),
        }):
            with TestClient(app, base_url='http://127.0.0.1:8765') as client:
                self.assertEqual(client.get('/internal/status').status_code, 401)
                original = client.get('/internal/status', headers=AUTH).json()
                self.assertEqual(original['capabilities']['web_recovery'], 1)
                self.assertEqual(original['web_recovery']['max_wait_seconds'], 1890)
                settings = client.get('/internal/config', headers=AUTH).json()
                settings['generation']['recovery_timeout_seconds'] = 0.2
                settings['fusion'].update(mode='api', model='fixture', base_url='http://127.0.0.1:11434/v1')
                self.assertEqual(client.put('/internal/config', headers=AUTH, json=settings).status_code, 200)
                report = client.get('/internal/status', headers=AUTH).json()['web_recovery']
                self.assertEqual(report['max_wait_seconds'], 1531)
                self.assertIs(type(report['max_wait_seconds']), int)
                self.assertEqual(report['recovery_timeout_seconds'], 0.2)

    def test_recovery_config_default_disable_and_invalid_bounds(self):
        self.assertEqual(GenerationSettings().recovery_timeout_seconds, 180)
        self.assertEqual(GenerationSettings(recovery_timeout_seconds=0).recovery_timeout_seconds, 0)
        self.assertEqual(GenerationSettings(recovery_timeout_seconds=600).recovery_timeout_seconds, 600)
        for value in [-1, 600.1, float('nan'), float('inf')]:
            with self.assertRaises(ValidationError):
                GenerationSettings(recovery_timeout_seconds=value)
