"""A common candidate deadline and a durable all-complete synthesis barrier."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.engine import Engine, Bridge, FusionError
from backend.models import ChatRequest
from backend.storage import Store

class ChatBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ, {'FUSION_TOKEN':'test-token-at-least-32-characters'}); self.env.start()
        self.store=Store(Path(self.tmp.name)); self.engine=Engine(self.store)
    async def asyncTearDown(self):
        await self.engine.close(); self.store.close(); self.tmp.cleanup(); self.env.stop()
    def request(self,model='web-fusion'):
        return ChatRequest(model=model,messages=[{'role':'user','content':'test'}])
    async def test_default_timeout_is_60_and_old_config_gets_default(self):
        self.assertEqual(self.store.config.chat.timeout_seconds,60)
        raw=self.store.config.model_dump(); raw.pop('chat')
        self.assertEqual(type(self.store.config).model_validate(raw).chat.timeout_seconds,60)
    async def test_first_saved_candidate_does_not_start_fusion_and_live_progress_is_separate(self):
        gate=asyncio.Event(); calls=[]
        class Fake:
            connected=True; websocket=None
            async def generate(_s,p,prompt,purpose,config):
                calls.append((p,purpose))
                if purpose=='candidate' and p=='deepseek': await gate.wait()
                return p+' finished'
        self.engine.bridge=Fake(); self.engine.start()
        turn=self.engine.submit(self.request(),progress_id='test-progress')
        for _ in range(100):
            rows=self.engine.progress.get('test-progress')['events']
            if any(r['stage']=='candidate_saved' for r in rows): break
            await asyncio.sleep(.005)
        self.assertEqual([(r.get('provider')) for r in rows if r['stage']=='candidate_saved'],['chatgpt'])
        self.assertFalse(any(purpose=='fusion' for _,purpose in calls)); self.assertFalse(turn.future.done())
        gate.set(); await asyncio.wait_for(turn.future,2)
        rows=self.engine.progress.get('test-progress')['events']
        saved=[r['sequence'] for r in rows if r['stage']=='candidate_saved']
        fusion=next(r['sequence'] for r in rows if r['stage']=='fusion_started')
        self.assertEqual(len(saved),2); self.assertGreater(fusion,max(saved))
    async def test_timeout_cancels_slow_model_once_keeps_fast_original_and_never_fuses(self):
        self.store.config.chat.timeout_seconds=.08  # Test-only shortening after validated defaults.
        calls=[]; cancelled=[]
        class Fake:
            connected=True; websocket=None
            async def generate(_s,p,prompt,purpose,config):
                calls.append((p,purpose))
                if p=='deepseek':
                    try: await asyncio.Event().wait()
                    except asyncio.CancelledError: cancelled.append(p); raise
                return p+' complete'
        self.engine.bridge=Fake(); self.engine.start()
        turn=self.engine.submit(self.request(),progress_id='deadline')
        with self.assertRaises(FusionError) as ctx: await asyncio.wait_for(turn.future,1)
        self.assertEqual(cancelled,['deepseek']); self.assertEqual(len(calls),2)
        self.assertEqual(ctx.exception.details['errors'][0]['code'],'provider_timeout')
        run=self.store.root/'runs'/turn.request_id
        self.assertTrue((run/'chatgpt.md').exists()); self.assertFalse((run/'merged.md').exists())
        self.assertIn('candidate_timed_out',[r['stage'] for r in self.engine.progress.get('deadline')['events']])
    async def test_explicit_partial_mode_still_waits_terminal_deadline(self):
        self.store.config.chat.timeout_seconds=.05; self.store.config.allow_partial=True
        cancelled=asyncio.Event()
        class Fake:
            connected=True; websocket=None
            async def generate(_s,p,prompt,purpose,config):
                if purpose=='fusion':
                    assert cancelled.is_set(); return 'merged successful originals'
                if p=='deepseek':
                    try: await asyncio.Event().wait()
                    except asyncio.CancelledError: cancelled.set(); raise
                return 'fast original'
        self.engine.bridge=Fake(); self.engine.start()
        turn=self.engine.submit(self.request()); result=await asyncio.wait_for(turn.future,1)
        self.assertEqual(len(result['fusion']['sources']),1); self.assertEqual(len(result['fusion']['errors']),1)
    async def test_single_model_keeps_its_own_longer_timeout(self):
        self.store.config.chat.timeout_seconds=.001
        class Fake:
            connected=True; websocket=None
            async def generate(_s,p,prompt,purpose,config): await asyncio.sleep(.03); return 'single complete'
        self.engine.bridge=Fake(); self.engine.start()
        turn=self.engine.submit(self.request('glm' if any(p.id=='glm' and p.enabled for p in self.store.config.providers) else 'chatgpt'))
        self.assertEqual((await asyncio.wait_for(turn.future,1))['choices'][0]['message']['content'],'single complete')
    async def test_wire_packet_includes_global_budget_and_cancel_on_deadline(self):
        self.store.config.chat.timeout_seconds=.05; packets=[]
        class Socket:
            async def send_json(_s,packet): packets.append(packet)
            async def close(_s,**kwargs): pass
        self.engine.bridge.websocket=Socket(); self.engine.bridge.ready=True; self.engine.start()
        turn=self.engine.submit(self.request())
        with self.assertRaises(FusionError): await asyncio.wait_for(turn.future,1)
        generates=[p for p in packets if p['type']=='generate']; cancels=[p for p in packets if p['type']=='cancel']
        self.assertEqual(len(generates),2); self.assertEqual(len(cancels),2)
        self.assertTrue(all(0<p['total_timeout_seconds']<=.05 for p in generates))
