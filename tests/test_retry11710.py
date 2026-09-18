"""UI uses the same web-fusion endpoint and bridge packet as this integration test."""
import asyncio
import json
from pathlib import Path
import unittest
from backend.engine import Bridge, _request_id, _request_source, _candidate_deadline
from backend.models import AppConfig
from backend.progress import ProgressStore

class RetryProgress11710(unittest.TestCase):
    def test_private_fields_never_leak_into_progress(self):
        store = ProgressStore(); store.register('k','r')
        store.add('r','retrying',provider='qwen',retry_stage='SECRET PROMPT',remaining_seconds=-1)
        self.assertNotIn('SECRET', json.dumps(store.get('k')))
        self.assertNotIn('retry_stage',store.get('k')['events'][-1])
        self.assertNotIn('remaining_seconds',store.get('k')['events'][-1])

    def test_countdowns_are_distinct_and_finite(self):
        store = ProgressStore(); store.register('k','r')
        for n in [20,20,19,float('nan'),float('inf')]:
            store.add('r','manual_retry_required',provider='qwen',retry_stage='manual',remaining_seconds=n)
        rows=store.get('k')['events']
        self.assertEqual([r['remaining_seconds'] for r in rows if 'remaining_seconds' in r],[20,19])

class RetryBridge11710(unittest.IsolatedAsyncioTestCase):
    async def test_fusion_job_forwards_policy_and_all_steps_through_live_bridge(self):
        store=ProgressStore();store.register('k','r');packets=[]
        class Socket:
            async def send_json(self,packet):packets.append(packet)
        bridge=Bridge(store);bridge.websocket,bridge.ready=Socket(),True
        config=AppConfig.model_validate_json((Path(__file__).resolve().parents[1]/'config.example.json').read_text())
        import time
        tokens=[(_request_id,_request_id.set('r')),(_request_source,_request_source.set('fusion_chat')),(_candidate_deadline,_candidate_deadline.set(time.monotonic()+60))]
        try:task=asyncio.create_task(bridge.generate('qwen','完整界面问题','candidate',config))
        finally:
            for var,token in reversed(tokens):var.reset(token)
        for _ in range(30):
            if packets:break
            await asyncio.sleep(0)
        packet=packets[0]
        self.assertEqual(packet['request_source'],'fusion_chat')
        self.assertTrue(packet['qwen_retry_stages'])
        self.assertEqual(packet['qwen_manual_retry_wait_seconds'],20)
        self.assertGreater(packet['recovery_timeout_seconds'],0)
        self.assertTrue(0<packet['total_timeout_seconds']<=60)
        common={'job_id':packet['job_id'],'provider_id':'qwen','type':'progress'}
        for retry in ['dom_handler','screenshot_click','manual']:
            bridge.receive({**common,'stage':'manual_retry_required' if retry=='manual' else 'retrying','retry_stage':retry,'remaining_seconds':20,'payload':'PRIVATE'})
        rows=store.get('k')['events']
        self.assertEqual([r['retry_stage'] for r in rows if 'retry_stage' in r],['dom_handler','screenshot_click','manual'])
        self.assertNotIn('PRIVATE',json.dumps(rows))
        bridge.receive({**common,'type':'result','markdown':'完整答案'})
        self.assertEqual(await task,'完整答案')
