import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fusion_agent.intent import classify_intent
from fusion_agent.local_tools import LocalTools
from fusion_agent.local_execution import LocalExecutionTools
from fusion_agent.web_fetch import WebFetchTools
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import Runtime
from test_runtime import ScriptedClient, final


def action(tool, args):
    return json.dumps({'type':'action','tool':tool,'arguments':args,'summary':'执行原任务并检查结果'}, ensure_ascii=False)


class RuntimeLocalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.local=LocalTools(self.root/'workspace',self.root/'runtime')
        self.web=WebFetchTools(self.root/'runtime')
        self.execution=LocalExecutionTools(self.local)
        self.events=[]
        self.registry=ToolRegistry([*self.local.specs(),*self.web.specs(),*self.execution.specs()],
                                   allowed=('files','web','skills','shell'))

    def tearDown(self):
        self.local.close();self.web.close();self.tmp.cleanup()

    def run_task(self, task, replies):
        client=ScriptedClient(replies)
        self.runtime=Runtime(client,self.registry,self.root/'run',event=lambda n,f:self.events.append((n,f)))
        return self.runtime.run(task)

    def test_events2_exact_task_enters_loop_and_retrieves_page(self):
        with patch.object(self.web,'_get',return_value=(200,{'content-type':'text/html'}, b'<title>Trending</title><p>Actual project</p>')) as get:
            result=self.run_task('解析https://github.com/trending中今天的项目详情',[
                action('web.fetch',{'url':'https://github.com/trending'}),final('Actual project from retrieved page')])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['steps'],1)
        self.assertEqual(get.call_count,1)
        self.assertEqual(self.runtime.state['last_freshness']['method'],'web.fetch')

    def test_model_may_fetch_immediately_without_a_refusal_assessment(self):
        with patch.object(self.web,'_get',return_value=(200,{'content-type':'text/plain'},b'Actual project')):
            result=self.run_task('解析https://github.com/trending中今天的项目详情',[
                action('web.fetch',{'url':'https://github.com/trending'}),final('Actual project')])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['steps'],1)
        self.assertFalse(any(n.startswith('intent.') for n,_ in self.events))

    def test_model_claim_does_not_create_a_web_fetch_evidence_record(self):
        result=self.run_task('解析https://github.com/trending中今天的项目详情',[final('模型直接回复')])
        self.assertEqual(result['steps'],0)
        self.assertEqual(self.runtime.state['successful_actions'],[])
        self.assertIsNone(self.runtime.state['last_freshness'])
        self.assertEqual(self.runtime.state['completion_evidence']['basis'],'model_reply_only')

    def test_multiple_model_selected_sources_are_not_keyword_restricted(self):
        with patch.object(self.web,'_get',return_value=(200,{'content-type':'text/plain'},b'Actual project')) as get:
            result=self.run_task('解析https://github.com/trending中今天的项目详情',[
                action('web.fetch',{'url':'https://example.com/context'}),
                action('web.fetch',{'url':'https://github.com/trending'}),final('Actual project')])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(get.call_count,2)

    def test_actual_discovered_project_link_can_be_read(self):
        page=b'<title>Trending</title><a href="/owner/repo">project</a>'
        with patch.object(self.web,'_get',return_value=(200,{'content-type':'text/html'},page)) as get:
            result=self.run_task('解析https://github.com/trending中今天的项目详情',[
                action('web.fetch',{'url':'https://github.com/trending'}),
                action('web.fetch',{'url':'https://github.com/owner/repo'}),final('Project details')])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(get.call_count,2)

    def test_unknown_tool_can_replan_to_python_then_really_execute(self):
        result=self.run_task('运行一个本地命令输出计算结果 21 * 2',[
            action('unknown.compute',{'expression':'21*2'}),
            action('python.run',{'code':'print(21 * 2)','purpose':'计算原任务的表达式'}),final('计算结果是42')])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['steps'],1)
        self.assertTrue(any(n=='tool.alternatives_found' for n,_ in self.events))
        self.assertTrue(list(self.execution.artifact_dir.glob('*/result.json')))

    def test_native_missing_same_action_runs_fallback(self):
        result=self.run_task('运行一个本地命令计算 21 * 2',[
            action('local.run',{'argv':['fusion-command-does-not-exist'], 'fallback_python':'print(21*2)', 'purpose':'执行原计算'}),
            final('42')])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['steps'],1)

    def test_missing_shell_command_can_recover_with_same_local_request(self):
        argv=['fusion-recovery-command-missing', '21', '2']
        result=self.run_task('运行一个本地命令计算 21 * 2',[
            action('shell.run',{'argv':argv}),
            action('local.run',{'argv':argv,'cwd':'.','fallback_python':'print(21*2)','purpose':'原命令的Python实现'}),
            final('42')])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['steps'],2)
        self.assertFalse(self.runtime.state['unresolved_failures'])
        failure=self.runtime.state['failed_actions'][0]
        self.assertEqual(failure['resolution_method'],'same_missing_command_replacement_process_exit')
        self.assertTrue(any(n=='failure.resolved' for n,_ in self.events))

    def test_different_argv_fallback_does_not_clear_missing_command(self):
        result=self.run_task('运行一个本地命令计算 21 * 2',[
            action('shell.run',{'argv':['fusion-recovery-command-missing','original']}),
            action('local.run',{'argv':['fusion-recovery-command-missing','different'],
                'fallback_python':'print(42)','purpose':'different request'}),
            final('42'),final('42'),final('42')])
        self.assertNotEqual(result['status'],'completed',result)
        self.assertTrue(self.runtime.state['unresolved_failures'])
        self.assertFalse(any(n=='failure.resolved' for n,_ in self.events))

    def test_unbound_python_success_does_not_clear_missing_command(self):
        result=self.run_task('运行一个本地命令计算 21 * 2',[
            action('shell.run',{'argv':['fusion-recovery-command-missing']}),
            action('python.run',{'code':'print(42)','purpose':'unbound implementation'}),
            final('42'),final('42'),final('42')])
        self.assertNotEqual(result['status'],'completed',result)
        self.assertTrue(self.runtime.state['unresolved_failures'])
        self.assertFalse(any(n=='failure.resolved' for n,_ in self.events))

    def test_different_cwd_cannot_resolve_not_started_request(self):
        (self.local.workspace/'other').mkdir()
        argv=['fusion-recovery-command-missing']
        result=self.run_task('运行一个本地命令计算 21 * 2',[
            action('shell.run',{'argv':argv,'cwd':'.'}),
            action('local.run',{'argv':argv,'cwd':'other','fallback_python':'print(42)','purpose':'different directory'}),
            final('42'),final('42'),final('42')])
        self.assertNotEqual(result['status'],'completed',result)
        self.assertTrue(self.runtime.state['unresolved_failures'])
        self.assertFalse(any(n=='failure.resolved' for n,_ in self.events))

    def test_page_read_does_not_grant_shell_when_not_authorized(self):
        self.registry.allowed.discard('shell')
        result=self.run_task('解析https://github.com/trending中今天的项目详情',[
            action('python.run',{'code':'open("bad", "w").write("x")','purpose':'not authorized'}),final('未执行')])
        self.assertEqual(result['error_code'],'tool_failed')
        self.assertFalse((self.local.workspace/'bad').exists())

    def test_previous_denial_not_bypassed_with_python(self):
        self.registry.denied.add('files')
        result=self.run_task('运行一个本地命令计算 21 * 2',[
            action('python.run',{'code':'print(42)','purpose':'test'})]*3)
        self.assertEqual(result['steps'],0)
        self.assertFalse(self.execution.artifact_dir.exists())

    def test_routing_negations_howto_and_chat_remain_non_action(self):
        for text in ('不要访问 https://example.com/page','别抓取 https://example.com',
                     'Do not fetch https://example.com','如何解析https://github.com/trending中今天的项目详情',
                     '你好','翻译 https://example.com 的这个单词'):
            with self.subTest(text=text):
                self.assertFalse(classify_intent(text).action_loop_requested)

    def test_previous_user_url_resolves_anaphora(self):
        previous=classify_intent('解析https://github.com/trending中今天的项目详情')
        route=classify_intent('继续解析上面链接的项目详情', previous=previous)
        self.assertTrue(route.action_loop_requested)
        self.assertEqual(route.requested_targets,('url:https://github.com/trending',))
