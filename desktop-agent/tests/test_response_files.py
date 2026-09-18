"""Response-file handoff, corruption containment and offline repair regression."""
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.client import ClientError, FusionClient
from fusion_agent.config import Settings
from fusion_agent.response_files import ResponseFile, ResponseFileError, ResponseStore, RepairArchive
from fusion_agent.repair_json import extract_event_replies, replay
from fusion_agent.runtime import ProtocolError, Runtime, parse_reply
from test_client import FakeAPI
from test_runtime import Registry, ScriptedClient, final, action


class ResponseFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.events = []
        self.store = ResponseStore(self.root/'responses', event=lambda name, fields: self.events.append((name, fields)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_and_validation(self):
        self.assertEqual(Settings().validate().response_delivery, 'file')
        self.assertTrue(Settings().save_problem_json)
        with self.assertRaises(ValueError): Settings(response_delivery='remote').validate()

    def test_utf8_exact_read_hash_permissions_and_one_time_reference(self):
        value = '中文\n\\n' + final()
        ref = self.store.put(value)
        self.assertEqual(ref.sha256, hashlib.sha256(value.encode()).hexdigest())
        self.assertEqual(Path(ref.path).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(ref.path).parent.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(ResponseFileError): self.store.read(dataclasses.replace(ref))
        self.assertEqual(self.store.read(ref), value)
        with self.assertRaises(ResponseFileError): self.store.read(ref)

    def test_same_size_tamper_fails_digest(self):
        ref = self.store.put('abc')
        Path(ref.path).write_text('abd')
        with self.assertRaisesRegex(ResponseFileError, 'SHA-256'): self.store.read(ref)

    def test_permission_change_rejected(self):
        ref = self.store.put('abc'); os.chmod(ref.path, 0o644)
        with self.assertRaises(ResponseFileError): self.store.read(ref)

    def test_symlink_replacement_and_unreserved_id_rejected(self):
        ref = self.store.put('abc'); Path(ref.path).unlink()
        (self.root/'other').write_text('abc'); Path(ref.path).symlink_to(self.root/'other')
        with self.assertRaises(ResponseFileError): self.store.read(ref)
        with self.assertRaises(ResponseFileError): self.store.put('abc', response_id='a'*32)

    def test_hardlink_replacement_rejected(self):
        ref = self.store.put('abc'); os.link(ref.path, self.root/'copy')
        with self.assertRaises(ResponseFileError): self.store.read(ref)

    def test_metadata_only_transient_reply_deleted_and_body_never_archived(self):
        store = ResponseStore(self.root/'private', retain_content=False)
        ident = store.reserve(); store.save_http(ident, b'SECRET BODY', {'status':200})
        ref = store.put('SECRET REPLY', response_id=ident)
        self.assertEqual(store.read(ref), 'SECRET REPLY')
        self.assertFalse(Path(ref.path).exists())
        for path in (self.root/'private').rglob('*'):
            if path.is_file(): self.assertNotIn('SECRET', path.read_text())

    def test_http_to_agent_reads_file_before_dispatch_and_archives_exact_body(self):
        registry = Registry()
        with FakeAPI() as api:
            api.body = {'choices':[{'message':{'content': final('问答完成')}, 'finish_reason':'stop'}]}
            client = FusionClient(api.url, 'DO-NOT-ARCHIVE-KEY', 'glm', response_delivery='file', response_store=self.store)
            result = Runtime(client, registry, self.root/'run', response_store=self.store,
                             event=lambda name, fields: self.events.append((name, fields))).run('你好')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(registry.calls, [])
            self.assertEqual(len(api.requests), 1)
            names = [event[0] for event in self.events]
            self.assertLess(names.index('model.response_file_saved'), names.index('agent.response_file_read'))
            self.assertLess(names.index('agent.response_file_handoff'), names.index('agent.response_file_read'))
            handoff = next(fields for name, fields in self.events if name == 'agent.response_file_handoff')
            self.assertEqual(handoff['payload']['purpose'], 'protocol_analysis')
            self.assertFalse(handoff['payload']['verified'])
            self.assertEqual(handoff['path'], handoff['payload']['response_file']['path'])
            self.assertEqual(handoff['payload']['response_file']['path'],
                             next(fields for name, fields in self.events if name == 'model.response_file_saved')['path'])
            bodies = list((self.root/'responses').rglob('http-response.body'))
            self.assertEqual(len(bodies), 1)
            self.assertEqual(json.loads(bodies[0].read_text()), api.body)
            for path in (self.root/'responses').rglob('*'):
                if path.is_file(): self.assertNotIn('DO-NOT-ARCHIVE-KEY', path.read_text())

    def test_http_file_read_precedes_real_local_file_action(self):
        source = self.root / 'input.txt'
        source.write_text('文件通路已验证', encoding='utf-8')
        with FakeAPI() as api:
            api.body = {'choices': [{'message': {'content': action(arguments={'path': str(source)})}}]}

            def read_local(name, arguments):
                names = [event[0] for event in self.events]
                self.assertIn('agent.response_file_read', names)
                self.assertLess(names.index('model.response_file_saved'), names.index('agent.response_file_read'))
                self.assertEqual(len(list((self.root/'responses').rglob('reply.txt'))), 1)
                content = Path(arguments['path']).read_text(encoding='utf-8')
                api.body = {'choices': [{'message': {'content': final(content)}}]}
                return {'ok': True, 'content': content}

            registry = Registry(read_local)
            client = FusionClient(api.url, 'key', 'glm', response_delivery='file', response_store=self.store)
            result = Runtime(client, registry, self.root/'run', response_store=self.store).run(f'读取 {source}')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['answer'], '文件通路已验证')
            self.assertEqual(len(registry.calls), 1)
            self.assertEqual(len(api.requests), 2)
            self.assertEqual(sum(name == 'agent.response_file_read' for name, _ in self.events), 2)

    def test_post_request_disk_failure_never_retries_or_executes(self):
        with FakeAPI() as api, patch.object(self.store, 'save_http', side_effect=OSError('disk full')):
            api.body = {'choices': [{'message': {'content': action(arguments={'path': 'input.txt'})}}]}
            client = FusionClient(api.url, 'key', 'glm', response_delivery='file', response_store=self.store)
            registry = Registry()
            result = Runtime(client, registry, self.root/'run', response_store=self.store).run('读取 input.txt')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error_code'], 'response_file_write_failed')
            self.assertEqual(len(api.requests), 1)
            self.assertEqual(registry.calls, [])

    def test_corrupt_handoff_never_executes_or_retries(self):
        ref = self.store.put(action(arguments={'path':'readme.md'}))
        raw = Path(ref.path).read_bytes(); Path(ref.path).write_bytes(raw.replace(b'readme', b'attack'))
        client = ScriptedClient([ref]); registry = Registry()
        result = Runtime(client, registry, self.root/'run', response_store=self.store).run('读取 readme.md')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(registry.calls, []); self.assertEqual(len(client.messages), 1)

    def test_no_store_write_no_http_request(self):
        with FakeAPI() as api, patch.object(self.store, 'reserve', side_effect=OSError('disk full')):
            client = FusionClient(api.url, 'key', response_delivery='file', response_store=self.store)
            with self.assertRaises(ClientError): client.complete([{'role':'user','content':'hello'}])
            self.assertEqual(api.requests, [])

    def test_server_path_is_not_a_response_file_capability(self):
        registry = Registry(); path = self.root/'instructions.json'; path.write_text(action())
        client = ScriptedClient([str(path)])
        result = Runtime(client, registry, self.root/'run', response_store=self.store).run('你好')
        self.assertEqual(result['status'], 'failed'); self.assertEqual(registry.calls, [])


class RepairReplayTests(unittest.TestCase):
    def test_reported_underscore_with_newline_and_trailing_comma_combination(self):
        source = '{"type":"final","answer":"realtime\\_query\n下一行\\_end",}'
        changes = []
        result = parse_reply(source, changes)
        self.assertEqual(result['answer'], 'realtime_query\n下一行_end')
        self.assertGreaterEqual(len(changes), 3)

    def test_controls_are_preserved_without_joining_command_lines(self):
        source = '{"type":"final","answer":"a\t\x00b\\\nc"}'
        result = parse_reply(source)
        self.assertEqual(result['answer'], 'a\t\x00b\\\nc')

    def test_double_encoded_backslash_is_not_json_repaired_again(self):
        source = json.dumps({'type':'final','answer':r'literal\_name and \n'})
        changes = []; result = parse_reply(source, changes)
        self.assertEqual(result['answer'], r'literal\_name and \n'); self.assertEqual(changes, [])

    def test_mixed_safe_and_executable_invalid_escapes_are_not_partially_repaired(self):
        for source in [r'{"type":"action","tool":"shell.run","arguments":{"command":"ls bad\_path"}}',
                       r'{"type":"final","answer":"realtime\_query","extra":"bad\_field"}',
                       r'{"type":"action","tool":"browser.open","arguments":{"url":"https://bad\_host.test"}}',
                       '{"type":"final","answer":"first","answer":"second"}',
                       '{"type":"action","tool":"shell.run","arguments":{"command":"incomplete']:
            with self.subTest(source=source), self.assertRaises(ProtocolError): parse_reply(source)

    def test_archive_success_failure_exact_original_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = r'{"type":"final","answer":"realtime\_query"}'
            changes=[]; obj=parse_reply(source, changes)
            archive=RepairArchive(Path(tmp)/'debug')
            good=archive.save(source,action=obj,normalizations=changes)
            self.assertEqual(Path(good['original']).read_text(),source)
            self.assertEqual(json.loads(Path(good['normalized']).read_text()),obj)
            bad=archive.save('BROKEN',error=ProtocolError('invalid'))
            self.assertEqual(bad['status'],'failed'); self.assertNotIn('normalized',bad)
            metadata=RepairArchive(Path(tmp)/'metadata',content=False).save('SECRET',error=ProtocolError('SECRET'))
            self.assertNotIn('original',metadata)
            self.assertNotIn('SECRET',Path(metadata['report']).read_text())

    def test_runtime_saves_repaired_and_failed_without_requesting_model_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, source in enumerate([r'{"type":"final","answer":"realtime\_query"}', '{"type":"action","tool":']):
                client=ScriptedClient([source]); registry=Registry(); run=Path(tmp)/str(index)
                result=Runtime(client,registry,run).run('你好')
                self.assertEqual(len(client.messages),1); self.assertEqual(registry.calls,[])
                originals=list((run/'json-repair').rglob('original.invalid.json'))
                self.assertEqual(len(originals),1); self.assertEqual(originals[0].read_text(),source)
                self.assertEqual(result['status'],'completed' if index==0 else 'failed')

    def test_replay_does_not_change_existing_parent_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)/'shared'
            parent.mkdir(mode=0o755)
            os.chmod(parent, 0o755)
            source = Path(tmp)/'bad.json'
            source.write_text(r'{"type":"final","answer":"realtime\_query"}')
            report = replay(source, parent/'replay')
            self.assertEqual(report['passed'], 1)
            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual((parent/'replay').stat().st_mode & 0o777, 0o700)

    def test_jsonl_reassembly_deduplication_and_incomplete_segment_warning(self):
        source = r'{"type":"final","answer":"realtime\_query"}'
        payload=json.dumps({'raw_reply':source}); cut=len(payload)//2
        lines=[{'event':'model.raw_reply','payload_id':'a','parts':2,'part':2,'payload':payload[cut:]},
               {'event':'model.raw_reply','payload_id':'a','parts':2,'part':1,'payload':payload[:cut]},
               {'event':'model.invalid_protocol','payload':{'raw_reply':source}},
               {'event':'model.raw_reply','payload_id':'b','parts':2,'part':1,'payload':'incomplete'},
               {'event':'model.response','payload':{'response_file':{'path':'/etc/passwd'}}}]
        text='\n'.join(json.dumps(row) for row in lines)
        samples,warnings=extract_event_replies(text)
        self.assertEqual(len(samples),1); self.assertEqual(samples[0][0],source); self.assertEqual(len(warnings),1)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'events.jsonl'; path.write_text(text)
            report=replay(path,Path(tmp)/'replay',events=True)
            self.assertEqual(report['passed'],1); self.assertEqual(report['server_requests'],0); self.assertEqual(report['executed_actions'],0)
