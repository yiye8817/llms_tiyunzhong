"""Regression coverage for uniform tools, JSON quotes, host paths and config."""
from __future__ import annotations
import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.cli import components, main, parser
from fusion_agent.config import (Settings, initialize, load_settings, save_settings,
                                 setting_value, update_settings)
from fusion_agent.contracts import ToolError
from fusion_agent.interactive import interactive_loop
from fusion_agent.local_tools import LocalTools
from fusion_agent.local_execution import LocalExecutionTools
from fusion_agent.protocol_format import PROTOCOL_SCHEMA, format_contract
from fusion_agent.registry import ToolRegistry, validate
from fusion_agent.response_files import RepairArchive, ResponseStore
from fusion_agent.runtime import ProtocolError, Runtime, parse_reply
from fusion_agent.repair_json import replay
from test_runtime import ScriptedClient, action, final


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def settings(self):
        path = self.root / 'agent.config.json'
        initialize(path)
        s = load_settings(path)
        update_settings(s, {'workspace': str(self.root/'work'), 'runtime_dir': str(self.root/'runtime'),
                            'skills_dir': str(self.root/'skills')})
        return s


class QuoteRepair1174Tests(TempCase):
    def test_runtime_attachment_prompt_as_code(self):
        raw = '{ "type": "final", "answer": "核心亮点：主打 "Prompt as Code" 理念。" }'
        changes = []
        value = parse_reply(raw, changes)
        self.assertEqual(value['answer'], '核心亮点：主打 "Prompt as Code" 理念。')
        self.assertEqual(changes[0]['count'], 2)
        self.assertEqual(changes[0]['fields'], ['answer'])
    def test_valid_source_byte_values_unchanged(self):
        data = {'type':'final', 'answer':'合法 "引号" 和 \\n、\\_、C:\\foo\\bar，以及\n换行'}
        changes=[]
        self.assertEqual(parse_reply(json.dumps(data),changes),data)
        self.assertEqual(changes,[])
    def test_combined_newline_tab_quotes_and_markdown(self):
        raw = '{"type":"final","answer":"say "hi"\nnext\tvalue\\_x",}'
        changes=[]
        value=parse_reply(raw,changes)
        self.assertEqual(value['answer'],'say "hi"\nnext\tvalue_x')
        self.assertEqual(set(x['kind'] for x in changes), {'unescaped_text_quotes','raw_json_string_newlines',
            'raw_json_string_controls','markdown_payload_string_escapes','trailing_commas'})
    def test_python_payload_can_preserve_nested_quotes(self):
        raw='{"type":"action","tool":"python.run","arguments":{"code":"print("hello", end="")\n","purpose":"演示"},"summary":"检查"}'
        changes=[];value=parse_reply(raw,changes)
        self.assertEqual(value['arguments']['code'],'print("hello", end="")\n')
        compile(value['arguments']['code'],'<fixture>','exec')
    def test_content_and_summary_quotes(self):
        raw='{"type":"action","tool":"files.write","arguments":{"path":"note.md","content":"# "标题"\n正文"},"summary":"写入 "标题" 文档"}'
        value=parse_reply(raw)
        self.assertEqual(value['arguments']['content'],'# "标题"\n正文')
        self.assertEqual(value['summary'],'写入 "标题" 文档')
    def test_selectors_can_follow_payload(self):
        raw='{"answer":"含 "quote" 内容","type":"final"}'
        self.assertEqual(parse_reply(raw)['answer'],'含 "quote" 内容')
    def test_member_like_boundary_cannot_hide_additional_action_fields(self):
        for raw in ('{"type":"final","answer":"a "x", "tool":"shell.run"}',
                    '{"type":"final","answer":"a "x", "answer":"replacement"}',
                    '{"type":"final","answer":"a "x" text"} {"type":"final","answer":"extra"}'):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):parse_reply(raw)
    def test_shell_command_argv_path_and_urls_not_guessed(self):
        repaired = '{"type":"action","tool":"shell.run","arguments":{"command":"echo "hi""},"summary":"s"}'
        self.assertEqual(parse_reply(repaired)["arguments"]["command"], 'echo "hi"')
        for tool, args in [('shell.run','{"argv":["echo","a "hi" b"]}'),
                           ('files.read','{"path":"/a "hi" b"}'),
                           ('web.fetch','{"url":"https://example.test/"a""}')]:
            raw='{"type":"action","tool":'+json.dumps(tool)+',"arguments":'+args+',"summary":"s"}'
            with self.subTest(tool=tool,args=args),self.assertRaises(ProtocolError):parse_reply(raw)
    def test_truncated_final_only_gets_diagnostic_artifact(self):
        raw='{ "type": "final", "answer": "目标目录 `.runtime` 位于当前配置的工作'
        with self.assertRaises(ProtocolError) as ctx:parse_reply(raw)
        self.assertTrue(ctx.exception.details['partial_only'])
        result=RepairArchive(self.root/'samples').save(raw,error=ctx.exception)
        self.assertEqual(result['status'],'incomplete')
        info=json.loads(Path(result['partial_json']).read_text())
        self.assertFalse(info['complete']);self.assertFalse(info['executable'])
        self.assertFalse((Path(result['directory'])/'normalized.json').exists())
        self.assertEqual(Path(result['partial_text']).read_text(),'目标目录 `.runtime` 位于当前配置的工作')
    def test_truncated_action_never_completed(self):
        raw='{"type":"action","tool":"python.run","arguments":{"code":"print('
        with self.assertRaises(ProtocolError) as ctx:parse_reply(raw)
        self.assertTrue(ctx.exception.details['incomplete_response'])
        self.assertNotIn('partial_answer',ctx.exception.details)
    def test_truncated_reply_no_tool_or_server_retry(self):
        client=ScriptedClient(['{"type":"final","answer":"部分正文'])
        registry=ToolRegistry([])
        result=Runtime(client,registry,self.root/'run').run('处理目录')
        self.assertEqual(result['error_code'],'incomplete_response')
        self.assertEqual(len(client.messages),1)
        self.assertIn('部分正文',result['answer'])
        self.assertEqual(result['steps'],0)
    def test_replay_repairs_quotes_without_executing(self):
        p=self.root/'reply.txt';p.write_text('{"type":"final","answer":"say "hi" now"}')
        result=replay(p,self.root/'out')
        self.assertEqual(result['passed'],1)
        self.assertEqual(result['executed_actions'],0);self.assertEqual(result['server_requests'],0)
    def test_metadata_only_does_not_archive_partial_text(self):
        with self.assertRaises(ProtocolError) as c:parse_reply('{"type":"final","answer":"private')
        d=RepairArchive(self.root/'out',content=False).save('private',error=c.exception)
        all_text=''.join(x.read_text() for x in Path(d['directory']).iterdir())
        self.assertNotIn('private',all_text)
    def test_strict_schema_and_prompt_are_real(self):
        for value in ({'type':'final','answer':'ok'},{'type':'action','tool':'files.read','arguments':{'path':'.'},'summary':'s'}):
            validate(value,PROTOCOL_SCHEMA)
        with self.assertRaises(ToolError):validate({'type':'final','answer':'ok','tool':'x'},PROTOCOL_SCHEMA)
        self.assertIn('\\"',format_contract())
        self.assertIn('Prompt as Code',format_contract())


class HostFiles1174Tests(TempCase):
    def setUp(self):
        super().setUp()
        self.local=LocalTools(self.root/'work',self.root/'runtime',filesystem_scope='host')
        self.out=self.root/'outside';self.out.mkdir()
    def tearDown(self):
        self.local.close();super().tearDown()
    def test_absolute_and_parent_read_write(self):
        target=self.out/'中文 text.txt'
        self.assertTrue(self.local.files_write({'path':str(target),'content':'outside'})['ok'])
        self.assertEqual(self.local.files_read({'path':'../outside/中文 text.txt'})['content'],'outside')
        self.assertEqual(self.local.files_stat({'path':str(target)})['type'],'file')
    def test_home_expansion(self):
        with patch.dict(os.environ,{'HOME':str(self.out)}):
            self.local.files_write({'path':'~/home.txt','content':'home'})
            self.assertEqual(self.local.files_read({'path':'~/home.txt'})['content'],'home')
            self.assertEqual(self.local.files_stat({'path':'~/home.txt'})['type'],'file')
    def test_copy_move_mkdir_delete_outside(self):
        self.local.files_mkdir({'path':str(self.out/'a/b'),'parents':True})
        self.local.files_write({'path':str(self.out/'a/x'),'content':'data'})
        self.local.files_copy({'source':str(self.out/'a/x'),'destination':str(self.out/'a/b/y')})
        self.local.files_move({'source':str(self.out/'a/b/y'),'destination':str(self.out/'z')})
        self.assertEqual((self.out/'z').read_text(),'data')
        self.local.files_delete({'path':str(self.out/'a'),'recursive':True})
        self.assertFalse((self.out/'a').exists())
    def test_list_returns_usable_absolute_paths(self):
        (self.out/'one').write_text('a')
        result=self.local.files_list({'path':str(self.out)})
        self.assertEqual(result['path'],str(self.out))
        self.assertEqual(result['entries'][0]['path'],str(self.out/'one'))
    def test_delete_symlink_leaves_target(self):
        (self.out/'keep').write_text('x');(self.out/'link').symlink_to(self.out/'keep')
        self.assertEqual(self.local.files_stat({'path':str(self.out/'link')})['type'],'symlink')
        self.local.files_delete({'path':str(self.out/'link')})
        self.assertTrue((self.out/'keep').exists())
    def test_root_delete_move_remain_blocked(self):
        for name,args in [('files_delete',{'path':'/','recursive':True}),('files_move',{'source':'/','destination':str(self.out/'root')})]:
            with self.subTest(name=name),self.assertRaises(ToolError):getattr(self.local,name)(args)
    def test_overwrite_requires_explicit_option(self):
        target=self.out/'x';target.write_text('keep')
        with self.assertRaises(ToolError):self.local.files_write({'path':str(target),'content':'bad'})
        self.assertEqual(target.read_text(),'keep')
    def test_shell_and_generated_python_cwd_outside(self):
        result=self.local.shell_run({'argv':[sys.executable,'-c','import os;print(os.getcwd())'],'cwd':str(self.out)})
        self.assertEqual(result['stdout'].strip(),str(self.out))
        result=LocalExecutionTools(self.local).python_run({'code':'from pathlib import Path\nPath("made.txt").write_text("done")','cwd':str(self.out),'purpose':'test'})
        self.assertTrue(result['ok']);self.assertEqual((self.out/'made.txt').read_text(),'done')
    def test_fixed_python_search_works_outside_without_rg_grep(self):
        (self.out/'search.txt').write_text('line\nneedle\n')
        original=self.local.shell_run
        def run(args):
            if args['argv'][0] in ('rg','grep'):
                raise ToolError('command_not_found','missing',not_executed=True)
            return original(args)
        with patch.object(self.local,'shell_run',side_effect=run):
            result=self.local.files_search({'query':'needle','path':str(self.out)})
        self.assertTrue(result['ok']);self.assertEqual(result['backend'],'python')
        self.assertEqual(result['matches'][0]['path'],str(self.out/'search.txt'))
    def test_workspace_opt_in_still_restricts(self):
        local=LocalTools(self.root/'limited',self.root/'runtime',filesystem_scope='workspace')
        try:
            with self.assertRaises(ToolError):local.files_read({'path':str(self.out/'x')})
        finally:local.close()
    def test_product_default_uses_host_scope(self):
        s=self.settings();owners,_,_=components(s)
        try:self.assertEqual(next(o for o in owners if isinstance(o,LocalTools)).filesystem_scope,'host')
        finally:
            for owner in reversed(owners):getattr(owner,"close",lambda:None)()


class UnifiedLoop1174Tests(TempCase):
    def test_every_input_can_call_tools_without_classifier(self):
        for index,task in enumerate(('你好','AI 新闻','是否能够处理','解析https://github.com/trending中今天的项目详情','压缩.runtime')):
            with self.subTest(task=task):
                local=LocalTools(self.root/f'work{index}',self.root/f'art{index}',filesystem_scope='host')
                execution=LocalExecutionTools(local)
                registry=ToolRegistry([*local.specs(),*execution.specs()],allowed=('files','skills','shell'))
                client=ScriptedClient([action('environment.tools',{'programs':['python3']}),final('已检查')])
                events=[]
                with patch('fusion_agent.intent.classify_intent',side_effect=AssertionError('classifier must not be called')):
                    result=Runtime(client,registry,self.root/f'run{index}',event=lambda n,f:events.append(n)).run(task)
                local.close()
                self.assertEqual(result['status'],'completed');self.assertEqual(result['steps'],1)
                self.assertNotIn('intent.classified',events)
                envelope=json.loads(client.messages[0][-1]['content'])
                self.assertNotIn('intent_route',envelope)
                self.assertEqual(envelope['execution_mode'],'tool_loop')
    def test_real_python_fallback_outside_workspace(self):
        local=LocalTools(self.root/'work',self.root/'art',filesystem_scope='host')
        execution=LocalExecutionTools(local)
        output=self.root/'outside.json'
        code='import json,sys\nfrom pathlib import Path\nv=json.loads(Path(sys.argv[1]).read_text())\nPath(v["path"]).write_text(json.dumps({"count":3}))\nprint("saved")\n'
        raw=json.dumps({'type':'action','tool':'local.run','arguments':{'argv':['fusion1174-nonexistent-tool-91'],
            'fallback_python':code,'input':{'path':str(output)},'purpose':'生成本地统计'},'summary':'执行替代工具'})
        client=ScriptedClient([raw,action('files.read',{'path':str(output)}),final('已读回')])
        try:
            result=Runtime(client,ToolRegistry([*local.specs(),*execution.specs()],allowed=('files','shell')),
                           self.root/'run').run('这个功能需要在本机完成')
            self.assertEqual(result['status'],'completed');self.assertEqual(result['steps'],2)
            self.assertEqual(json.loads(output.read_text()),{'count':3})
        finally:local.close()
    def test_shell_grant_not_inferred_or_silently_added(self):
        local=LocalTools(self.root/'work',self.root/'art',filesystem_scope='host')
        execution=LocalExecutionTools(local)
        client=ScriptedClient([action('python.run',{'code':'print("x")','purpose':'test'}),final('done')])
        try:
            result=Runtime(client,ToolRegistry(execution.specs(),allowed=('files',)),self.root/'run').run('请执行本地操作')
            self.assertEqual(result['error_code'],'tool_failed')
            self.assertFalse((self.root/'art'/'python-tools').exists())
        finally:local.close()
    def test_file_handoff_then_quote_repair(self):
        store=ResponseStore(self.root/'responses')
        ref=store.put('{"type":"final","answer":"主打 "Prompt as Code" 理念"}')
        client=ScriptedClient([ref]);client.response_store=store
        result=Runtime(client,ToolRegistry([]),self.root/'run',response_store=store).run('说明')
        self.assertEqual(result['status'],'completed')
        self.assertIn('"Prompt as Code"',result['answer'])
    def test_zero_tool_greeting_is_not_forced_to_execute(self):
        result=Runtime(ScriptedClient([final('你好')]),ToolRegistry([]),self.root/'run').run('你好')
        self.assertEqual(result['status'],'completed');self.assertEqual(result['steps'],0)


class Config1174Tests(TempCase):
    def test_model_workspace_round_trip_and_permissions(self):
        s=self.settings();update_settings(s,{'model':'glm','workspace':str(self.root/'other')})
        again=load_settings(s._config_path)
        self.assertEqual(again.model,'glm');self.assertEqual(again.workspace,str(self.root/'other'))
        self.assertEqual(stat.S_IMODE(Path(s._config_path).stat().st_mode),0o600)
    def test_all_settings_round_trip(self):
        s=self.settings();s.model='qwen';s.timeout=123;s.max_steps=33;s.max_context_chars=170000
        s.response_delivery='inline';s.filesystem_scope='workspace';s.python_tool_fallback=False
        s.verbose=True;s.non_interactive=True;s.auto_start_parent=False;s.default_skills=['sample']
        s.allowed_capabilities=['files','shell'];save_settings(s)
        loaded=load_settings(s._config_path)
        for name in Settings.__dataclass_fields__:
            a,b=getattr(s,name),getattr(loaded,name)
            self.assertEqual(list(a) if isinstance(a,tuple) else a,list(b) if isinstance(b,tuple) else b,name)
    def test_concurrent_sessions_merge_distinct_keys(self):
        s=self.settings();a=load_settings(s._config_path);b=load_settings(s._config_path)
        update_settings(a,{'model':'glm'});update_settings(b,{'workspace':str(self.root/'new')})
        loaded=load_settings(s._config_path)
        self.assertEqual(loaded.model,'glm');self.assertEqual(loaded.workspace,str(self.root/'new'))
    def test_invalid_change_is_transactional(self):
        s=self.settings();before=Path(s._config_path).read_bytes()
        with self.assertRaises(ValueError):update_settings(s,{'max_steps':0})
        self.assertEqual(s.max_steps,20);self.assertEqual(Path(s._config_path).read_bytes(),before)
    def test_corrupt_config_not_overwritten(self):
        s=self.settings();Path(s._config_path).write_text('{bad')
        with self.assertRaises(ValueError):update_settings(s,{'model':'glm'})
        self.assertEqual(s.model,'chatgpt');self.assertEqual(Path(s._config_path).read_text(),'{bad')
    def test_symlink_target_not_overwritten(self):
        s=self.settings();path=Path(s._config_path);path.unlink();target=self.root/'real.json';target.write_text('{}');path.symlink_to(target)
        with self.assertRaises(ValueError):update_settings(s,{'model':'glm'})
        self.assertEqual(target.read_text(),'{}')
    def test_atomic_write_failure_keeps_old_file_and_memory(self):
        s=self.settings();before=Path(s._config_path).read_bytes()
        with patch('os.replace',side_effect=OSError('full disk')),self.assertRaises(OSError):update_settings(s,{'model':'glm'})
        self.assertEqual(Path(s._config_path).read_bytes(),before);self.assertEqual(s.model,'chatgpt')
        self.assertEqual(list(self.root.glob('.agent.config.json.*.tmp')),[])
    def test_autosave_can_be_disabled_and_reenabled(self):
        s=self.settings();update_settings(s,{'auto_save_config':False});update_settings(s,{'model':'glm'})
        self.assertEqual(load_settings(s._config_path).model,'chatgpt')
        update_settings(s,{'auto_save_config':True});update_settings(s,{'model':'qwen'})
        self.assertEqual(load_settings(s._config_path).model,'qwen')
    def test_cli_config_commands_offline(self):
        s=self.settings()
        with contextlib.redirect_stdout(io.StringIO()),patch('fusion_agent.client.FusionClient.complete',side_effect=AssertionError('offline')):
            self.assertEqual(main(['config','--config',s._config_path,'set','model','glm']),0)
            self.assertEqual(main(['config','--config',s._config_path,'set','allowed_capabilities','["files","shell"]']),0)
            self.assertEqual(main(['config','--config',s._config_path,'set','timeout','120']),0)
        loaded=load_settings(s._config_path)
        self.assertEqual(loaded.model,'glm');self.assertEqual(loaded.allowed_capabilities,['files','shell']);self.assertEqual(loaded.timeout,120)
    def test_cli_overrides_saved_and_no_save_flag(self):
        s=self.settings()
        with patch('fusion_agent.cli.execute_run',return_value=0):
            self.assertEqual(main(['run','--config',s._config_path,'--model','glm','--workspace',str(self.root/'other'),'--allow','shell','task']),0)
            loaded=load_settings(s._config_path)
            self.assertEqual(loaded.model,'glm');self.assertIn('shell',loaded.allowed_capabilities)
            self.assertEqual(main(['run','--config',s._config_path,'--no-save-config','--model','qwen','task']),0)
            self.assertEqual(load_settings(s._config_path).model,'glm')
    def test_interactive_switches_persist_and_restart(self):
        s=self.settings();calls=[]
        lines=iter(['/model glm',f'/workspace "{self.root}/dir with space"','/response-mode inline',
                    '/config set timeout 120','/allow shell','/verbose on','检查','/quit'])
        with patch('fusion_agent.parent_service.ensure_parent',return_value={'state':'ready'}),\
             patch('fusion_agent.interactive.available_models',return_value=['chatgpt','glm']),\
             contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            rc=interactive_loop(s,parser().parse_args(['chat']),lambda task,settings,*a,**kw:calls.append(deepcopy(settings)),read_line=lambda _:next(lines))
        self.assertEqual(rc,0);self.assertEqual(len(calls),1)
        loaded=load_settings(s._config_path)
        self.assertEqual(loaded.model,'glm');self.assertEqual(loaded.workspace,str(self.root/'dir with space'))
        self.assertEqual(loaded.response_delivery,'inline');self.assertEqual(loaded.timeout,120)
        self.assertTrue(loaded.verbose);self.assertIn('shell',loaded.allowed_capabilities)
    def test_saved_key_references_never_contain_credential(self):
        s=self.settings()
        with patch.dict(os.environ,{'FUSION_AGENT_API_KEY':'secret-1174'}):save_settings(s)
        self.assertNotIn('secret-1174',Path(s._config_path).read_text())
    def test_setting_value_types(self):
        self.assertEqual(setting_value('max_steps','40'),40)
        self.assertEqual(setting_value('log_content','false'),False)
        self.assertEqual(setting_value('default_skills','["a"]'),['a'])
        self.assertEqual(setting_value('browser_channel','null'),None)
        with self.assertRaises(ValueError):setting_value('unknown','x')


if __name__ == '__main__':unittest.main()

class ConfigAndRoot1174Tests(TempCase):
    def test_root_stat_and_list_use_real_host_root(self):
        local=LocalTools(self.root/'work',self.root/'art',filesystem_scope='host')
        try:
            self.assertEqual(local.files_stat({'path':'/'})['type'],'directory')
            self.assertEqual(local.files_list({'path':'/','limit':1})['path'],'/')
        finally:local.close()

    def test_new_console_commands_cannot_be_skill_names(self):
        from fusion_agent.skill_console import validate_command_name
        for name in ('config','allow','deny','response-mode'):
            with self.subTest(name=name),self.assertRaises(ToolError):validate_command_name(name)

    def test_load_unload_default_skill_is_saved(self):
        from fusion_agent.skill_demo import create_demo_skill
        s=self.settings();create_demo_skill('report-demo',Path(s.skills_dir))
        for command,expected in (('/skill load report-demo',['report-demo']),('/skill unload report-demo',[])):
            lines=iter([command,'/quit'])
            args=parser().parse_args(['chat']);args.skill=list(load_settings(s._config_path).default_skills)
            with patch('fusion_agent.interactive._start_session'),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(interactive_loop(load_settings(s._config_path),args,lambda *a,**k:None,read_line=lambda _:next(lines)),0)
            self.assertEqual(list(load_settings(s._config_path).default_skills),expected)

    def test_invalid_skill_setting_does_not_replace_live_config(self):
        s=self.settings();before=Path(s._config_path).read_bytes();lines=iter(['/config set default_skills ["missing"]','/quit'])
        with patch('fusion_agent.interactive._start_session'),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            interactive_loop(s,parser().parse_args(['chat']),lambda *a,**k:None,read_line=lambda _:next(lines))
        self.assertEqual(Path(s._config_path).read_bytes(),before)

    def test_empty_working_directory_is_not_saved(self):
        s=self.settings();before=Path(s._config_path).read_bytes()
        with self.assertRaises(ValueError):update_settings(s,{'workspace':''})
        self.assertEqual(Path(s._config_path).read_bytes(),before)
