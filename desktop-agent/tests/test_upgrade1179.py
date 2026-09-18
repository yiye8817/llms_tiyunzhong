"""Local protocol repair and model-independent builtin/local skill regression."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fusion_agent.runtime import Runtime, parse_reply, ProtocolError
from fusion_agent.skills import AgentSkillLibrary, BUILTIN_SKILLS
from fusion_agent.registry import ToolRegistry
from fusion_agent.contracts import ToolError
from fusion_agent.repair_json import replay
from test_runtime import ScriptedClient, final, action
from test_client import FakeAPI
from fusion_agent.client import FusionClient


def malformed(tool='environment.tools'):
    return ('{"type":"action","tool":"['+tool+'](https://'+tool+'/)",'
            '"arguments":{"programs":\\["yt-dlp","ffmpeg","curl"\\]},'
            '"summary":"检查现有程序","plan":\\["检查环境","报告结果"\\]}')


def write_skill(root, name, text):
    folder=root/name
    folder.mkdir(parents=True)
    (folder/'SKILL.md').write_text(f'---\nname: {name}\ndescription: "A test skill"\n---\n{text}\n')


class LocalRepair1179(unittest.TestCase):
    def test_runtime2_link_and_arrays_repaired_without_argument_mutation(self):
        changes=[]
        row=parse_reply(malformed(), changes)
        self.assertEqual(row['tool'], 'environment.tools')
        self.assertEqual(row['arguments'], {'programs':['yt-dlp','ffmpeg','curl']})
        self.assertEqual(row['plan'], ['检查环境','报告结果'])
        self.assertTrue(any(c['kind']=='tool_markdown_self_link' for c in changes))

    def test_plain_valid_json_not_changed(self):
        row={'type':'action','tool':'files.write','arguments':{'path':'/tmp/x','content':'\\n "quoted"\n实际换行'},'summary':'save'}
        changes=[]
        self.assertEqual(parse_reply(json.dumps(row),changes),row)
        self.assertEqual(changes,[])

    def test_qualified_tool_self_links(self):
        for name in ['skills.list','skills.read','python.run','local.run','files.read','web.fetch']:
            with self.subTest(name=name):
                s=json.dumps({'type':'action','tool':f'[{name}](https://{name}/)','arguments':{},'summary':'step'})
                self.assertEqual(parse_reply(s)['tool'],name)

    def test_not_fuzzy_or_http_execution(self):
        for target in ['https://evil.invalid/','https://skills.read/path','https://skills.read/?q=1',
                       'https://skills.read/#x','https://skills.read:443/','https://x@skills.read/','javascript:skills.read']:
            with self.subTest(target=target):
                s=action(f'[skills.read]({target})')
                with self.assertRaises(ProtocolError):parse_reply(s)

    def test_suffix_and_multiple_links_rejected(self):
        for tool in ['[skills.read](https://skills.read/) extra','[skills.read](https://skills.read/)[x](https://x/)',
                     '[skill read](https://skills.read/)']:
            with self.subTest(tool=tool), self.assertRaises(ProtocolError):parse_reply(action(tool))

    def test_broken_protocol_shape_not_sent_for_server_repair(self):
        for text in ['{"type":"action","tool":"skills.list"}', '[1,2]', '{"type":"final","answer":false}',
                     '{"type":"final","answer":"truncated']:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                c=ScriptedClient([text,final('not reached')]);events=[]
                result=Runtime(c,ToolRegistry([]),Path(tmp)/'run',event=lambda e,f:events.append((e,f))).run('inspect')
                self.assertEqual(len(c.messages),1)
                self.assertIn(result['error_code'],('invalid_protocol','incomplete_response'))
                self.assertTrue(any(e=='model.local_protocol_repair_failed' for e,_ in events))
                self.assertFalse(any(e=='model.action_replan_requested' for e,_ in events))
                self.assertTrue(list((Path(tmp)/'run/json-repair').glob('*/original.invalid.json')))

    def test_unknown_valid_tool_replanned_not_json_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=ScriptedClient([action('missing.tool'),final('没有执行，工具未提供')]);events=[]
            Runtime(c,ToolRegistry([]),Path(tmp)/'run',event=lambda e,f:events.append((e,f))).run('inspect')
            self.assertGreaterEqual(len(c.messages),2)
            request=json.loads(c.messages[1][-1]['content'])
            self.assertEqual(request['type'],'tool_resolution')
            self.assertTrue(any(e=='model.action_replan_requested' for e,_ in events))
            self.assertNotIn('protocol_repair',json.dumps(request))

    def test_offline_replay_saves_corrected_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'bad.txt';source.write_text(malformed())
            report=replay(source,Path(tmp)/'out')
            self.assertEqual(report['passed'],1)
            self.assertTrue(list((Path(tmp)/'out').rglob('normalized.json')))


class Skills1179(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.local=self.root/'local';self.local.mkdir()
        self.built=self.root/'builtin';self.built.mkdir()
        write_skill(self.built,'inspect','Builtin inspect')
        write_skill(self.local,'custom','Local custom')
        self.library=AgentSkillLibrary(self.local,self.built)

    def tearDown(self):self.tmp.cleanup()

    def test_catalog_includes_both_sources(self):
        self.assertEqual({r['name']:r['source'] for r in self.library.catalog()}, {'inspect':'builtin','custom':'local'})

    def test_collision_keeps_builtin_and_local_visible(self):
        write_skill(self.local,'inspect','Local inspect')
        rows=self.library.catalog()
        self.assertIn('builtin:inspect',[r['name'] for r in rows])
        self.assertIn('Local inspect',self.library.load('inspect'))
        self.assertIn('Builtin inspect',self.library.load('builtin:inspect'))
        self.assertIn('Local inspect',self.library.load('local:inspect'))

    def test_builtin_resource_base_path_is_real(self):
        row=self.library._read_tool({'name':'inspect'})
        self.assertEqual(row['base_path'],str(self.built/'inspect'))
        self.assertEqual(row['source'],'builtin')

    def test_same_root_deduplicated(self):
        rows=AgentSkillLibrary(self.built,self.built).catalog()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source'],'builtin')

    def test_new_empty_user_dir_keeps_installed_skills(self):
        rows=AgentSkillLibrary(self.root/'not-created').catalog()
        self.assertTrue({'browser-research','desktop-note','system-report'} <= {r['name'] for r in rows})

    def test_untrusted_name_cannot_traverse(self):
        for name in ['../inspect','builtin:../inspect','unknown:inspect','/inspect','builtin:custom:inspect']:
            with self.subTest(name=name),self.assertRaises(ToolError):self.library.load(name)

    def test_symlink_override_is_not_silently_substituted(self):
        (self.local/'inspect').symlink_to(self.built/'inspect',target_is_directory=True)
        with self.assertRaises(ToolError):self.library.load('inspect')

    def test_registry_lists_and_reads_but_does_not_execute_skill_text(self):
        registry=ToolRegistry(self.library.specs(),allowed=['skills'])
        listing=registry.invoke('skills.list',{})
        self.assertTrue(listing['ok'])
        result=registry.invoke('skills.read',{'name':'inspect'})
        self.assertIn('Builtin inspect',json.dumps(result))
        self.assertFalse(list(self.root.glob('executed*')))

    def test_skill_authorization_not_bypassed(self):
        registry=ToolRegistry(self.library.specs(),allowed=[])
        result=registry.invoke('skills.read',{'name':'inspect'})
        self.assertFalse(result['ok'])

    def test_every_model_uses_real_http_client_and_same_skill_registry(self):
        # A local HTTP server is used, not a live provider. All five API model IDs
        # really roundtrip and invoke the same skill implementation.
        for model in ['qwen','glm','chatgpt','deepseek','kimi']:
            with self.subTest(model=model),FakeAPI() as api:
                registry=ToolRegistry(self.library.specs(),allowed=['skills'])
                original=registry.invoke
                def invoke(name,args):
                    result=original(name,args)
                    api.body={'choices':[{'message':{'content':final('已读取技能')}}]}
                    return result
                registry.invoke=invoke
                text=action('[skills.read](https://skills.read/)',{'name':'inspect'})
                api.body={'choices':[{'message':{'content':text}}]}
                events=[]
                result=Runtime(FusionClient(api.url,'key',model),registry,self.root/f'run-{model}',event=lambda e,f:events.append((e,f))).run('读取 inspect 技能')
                self.assertEqual(result['status'],'completed')
                self.assertEqual(result['steps'],1)
                self.assertEqual(len(api.requests),2)
                self.assertEqual(api.requests[0]['body']['model'],model)
                self.assertTrue(any(e=='model.protocol_normalized' for e,_ in events))
                obs=json.loads(api.requests[1]['body']['messages'][-1]['content'])
                self.assertIn('Builtin inspect',json.dumps(obs))

    def test_skill_and_skills_cli_alias_list_without_server(self):
        config=self.root/'config.json'
        config.write_text(json.dumps({'skills_dir':str(self.local),'workspace':str(self.root)}))
        # Avoid persistent config writes; only catalog metadata is read.
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
        for words in [['skill'],['skill','list'],['skills']]:
            with self.subTest(words=words):
                result=subprocess.run([sys.executable,'-m','fusion_agent',*words,'--config',str(config)],env=env,text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('custom',result.stdout)
                self.assertIn('system-report',result.stdout)
                self.assertIn('builtin',result.stdout)

class SkillCommands1179(unittest.TestCase):
    def test_bare_and_slash_commands_are_handled_locally_without_model_dispatch(self):
        from test_interactive import InteractiveTests
        helper=InteractiveTests()
        with tempfile.TemporaryDirectory() as tmp:
            settings=helper.settings(Path(tmp));calls=[]
            code,out,err=helper.loop(settings,['skill','skill list','skills','skill read system-report','/skill','/skill list','/skill read system-report','/quit'],lambda *a,**k:calls.append(a))
            self.assertEqual(code,0)
            self.assertEqual(calls,[])
            self.assertIn('builtin',out)
            self.assertIn('system-report',out)
            self.assertNotIn('无法执行',err)

    def test_qualified_builtin_selection_remains_valid_after_model_change(self):
        from test_interactive import InteractiveTests
        helper=InteractiveTests()
        with tempfile.TemporaryDirectory() as tmp:
            settings=helper.settings(Path(tmp));calls=[]
            def execute(task,selected,args,on_run):calls.append((selected.model,args.skill))
            code,out,err=helper.loop(settings,['/skill load builtin:system-report','/model glm','读取技能','/model qwen','读取技能','/quit'],execute,
                                   **{'fusion_agent.interactive.available_models':lambda _:['glm','qwen']})
            self.assertEqual(code,0)
            self.assertEqual(calls,[('glm',['builtin:system-report']),('qwen',['builtin:system-report'])])
            self.assertNotIn('无法执行',err)
