"""1.17.4 migration: uniform tool loop, actual evidence, explicit permissions and continuations."""

import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.client import ClientError
from fusion_agent.contracts import ToolSpec
from fusion_agent.local_tools import LocalTools
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import Runtime
from test_runtime import ScriptedClient, final


def action(tool, arguments, summary="执行用户明确要求的步骤"):
    return json.dumps({
        "type": "action",
        "tool": tool,
        "arguments": arguments,
        "summary": summary,
    }, ensure_ascii=False)


class RecordingFileSearchRegistry:
    """Real file tools plus a deterministic read-only web.search test double."""

    def __init__(self, workspace, runtime_dir, *, search_outcomes=(), event=None):
        self.calls = []
        self.search_outcomes = list(search_outcomes)
        self.local = LocalTools(workspace, runtime_dir)
        web_search = ToolSpec(
            "web.search",
            "Search current public information",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["sequential", "parallel"]},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "web",
            False,
            self._search,
        )
        self.inner = ToolRegistry(
            [*self.local.specs(), web_search],
            allowed=("files", "web"),
            event=event,
        )
        # Runtime intentionally inspects the real file-tool owner to bind
        # relative user targets to this workspace.
        self.tools = self.inner.tools

    def close(self):
        self.local.close()

    def catalog(self):
        return self.inner.catalog()

    def resolve_tool(self, name):
        return self.inner.resolve_tool(name)

    def invoke(self, name, arguments):
        self.calls.append((name, json.loads(json.dumps(arguments))))
        return self.inner.invoke(name, arguments)

    def _search(self, arguments):
        outcome = self.search_outcomes.pop(0) if self.search_outcomes else True
        if not outcome:
            return {
                "ok": False,
                "error": {
                    "code": "all_backends_failed",
                    "message": "All configured search backends failed",
                },
                "execution": {"status": "completed"},
            }
        return {
            "mode": arguments.get("mode", "sequential"),
            "result_count": 1,
            "results": [{
                "title": "Fresh AI news",
                "url": "https://example.com/fresh-ai-news",
                "snippet": "Current, deterministic test result",
                "source": "ddgo",
            }],
            "attempts": [{"backend": "ddgo", "status": "ok", "result_count": 1}],
        }


class ExactOperationRegistry:
    def __init__(self, capability, names):
        self.calls = []
        self.tools = {}
        specs = []
        for name in names:
            schema = {"type": "object", "properties": {}, "additionalProperties": True}
            spec = ToolSpec(name, "fixture", schema, capability, True,
                            lambda arguments, tool=name: self._invoke(tool, arguments))
            specs.append(spec)
        self.inner = ToolRegistry(specs, allowed=(capability,))
        self.tools = self.inner.tools

    def _invoke(self, name, arguments):
        self.calls.append((name, json.loads(json.dumps(arguments))))
        return {"verification": {"status": "verified", "scope": name.split(".")[0],
                                 "method": "fixture_assertion"}}

    def catalog(self):
        return self.inner.catalog()

    def invoke(self, name, arguments):
        return self.inner.invoke(name, arguments)


class RuntimeExecutionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.registries = []

    def tearDown(self):
        for registry in self.registries:
            registry.close()
        self.temp.cleanup()

    def registry(self, name, *, search_outcomes=(), events=None):
        callback = ((lambda event, fields: events.append((event, fields)))
                    if events is not None else None)
        registry = RecordingFileSearchRegistry(
            self.workspace,
            self.root / (name + "-tool-runtime"),
            search_outcomes=search_outcomes,
            event=callback,
        )
        self.registries.append(registry)
        return registry

    def runtime(self, name, replies, *, registry=None, events=None, max_steps=20):
        registry = registry or self.registry(name, events=events)
        callback = ((lambda event, fields: events.append((event, fields)))
                    if events is not None else None)
        return Runtime(
            ScriptedClient(replies),
            registry,
            self.root / name,
            max_steps=max_steps,
            event=callback,
        )

    def failed_search_context(self,name,task="今天 AI 新闻"):
        registry=self.registry(name,search_outcomes=(False,))
        runtime=self.runtime(name,[action('web.search',{'query':task,'mode':'sequential'}),final('搜索失败')],registry=registry)
        result=runtime.run(task)
        self.assertEqual(result['error_code'],'tool_failed')
        return runtime.export_context()

    def failed_action_context(self, name, task):
        runtime = self.runtime(name, [ClientError("http_error", "HTTP 502")])
        result = runtime.run(task)
        self.assertEqual(result["status"], "failed")
        return runtime.export_context()

    def test_model_final_without_execution_records_no_file_success(self):
        registry=self.registry('reply-only')
        runtime=self.runtime('reply-only',[final('report.md 已保存')],registry=registry)
        result=runtime.run('请保存到 report.md')
        self.assertEqual(result['status'],'completed')  # Reply ended, not an OS-operation proof.
        self.assertEqual(registry.calls,[])
        self.assertFalse((self.workspace/'report.md').exists())
        self.assertEqual(runtime.state['successful_actions'],[])
        self.assertEqual(runtime.state['completion_evidence']['basis'],'model_reply_only')

    def test_successful_file_write_is_required_before_final(self):
        events = []
        registry = self.registry("real-write", events=events)
        runtime = self.runtime(
            "real-write",
            [
                action("files.write", {"path": "report.md", "content": "verified"}),
                final("report.md 已保存并核验。"),
            ],
            registry=registry,
            events=events,
        )

        result = runtime.run("请保存到 report.md")

        self.assertEqual(result["status"], "completed")
        self.assertEqual([name for name, _ in registry.calls], ["files.write"])
        self.assertEqual((self.workspace / "report.md").read_text(), "verified")
        self.assertTrue(any(
            item["tool"] == "files.write"
            and item["capability"] == "files"
            and item["mutating"] is True
            for item in runtime.state["successful_actions"]
        ))
        self.assertNotIn("intent.action_completion_required", [name for name, _ in events])

    def test_action_evidence_contains_only_the_file_actually_written(self):
        registry=self.registry('one-file')
        runtime=self.runtime('one-file',[action('files.write',{'path':'a.md','content':'a'}),final('已写入a.md')],registry=registry)
        self.assertEqual(runtime.run('请保存 a.md 和 b.md')['status'],'completed')
        self.assertTrue((self.workspace/'a.md').exists())
        self.assertFalse((self.workspace/'b.md').exists())
        self.assertEqual([e['target'] for e in runtime.state['successful_actions']],['file:'+str(self.workspace/'a.md')])

    def test_mixed_file_read_and_write_require_the_correct_operation_per_target(self):
        (self.workspace / "input.txt").write_text("source", encoding="utf-8")
        registry = self.registry("mixed-file-operations")
        runtime = self.runtime(
            "mixed-file-operations",
            [action("files.read", {"path": "input.txt"}),
             action("files.write", {"path": "output.md", "content": "source"}),
             final("读取并保存完成。")],
            registry=registry,
        )

        result = runtime.run("请把 input.txt 的内容保存到 output.md")

        self.assertEqual(result["status"], "completed")
        self.assertEqual([name for name, _ in registry.calls], ["files.read", "files.write"])
        self.assertEqual((self.workspace / "output.md").read_text(encoding="utf-8"), "source")

    def test_overwrite_still_requires_an_explicit_tool_argument(self):
        (self.workspace/'input.txt').write_text('keep')
        registry=self.registry('overwrite-denied')
        runtime=self.runtime('overwrite-denied',[action('files.write',{'path':'input.txt','content':'changed'}),final('完成')],registry=registry)
        result=runtime.run('处理这个文件')
        self.assertEqual(result['error_code'],'tool_failed')
        self.assertEqual((self.workspace/'input.txt').read_text(),'keep')
        self.assertTrue(runtime.state['unresolved_failures'])

    def test_multi_file_read_and_write_run_without_inferred_target_binding(self):
        (self.workspace/'a.txt').write_text('A');(self.workspace/'b.txt').write_text('B')
        registry=self.registry('multi')
        replies=[action('files.read',{'path':'a.txt'}),action('files.read',{'path':'b.txt'}),
                 action('files.write',{'path':'out.md','content':'A\nB'}),final('已读回来源并保存')]
        runtime=self.runtime('multi',replies,registry=registry)
        self.assertEqual(runtime.run('合并这两个文件')['status'],'completed')
        self.assertEqual((self.workspace/'a.txt').read_text(),'A')
        self.assertEqual((self.workspace/'b.txt').read_text(),'B')
        self.assertEqual((self.workspace/'out.md').read_text(),'A\nB')
        self.assertEqual(len(runtime.state['successful_actions']),3)

    def test_only_performed_browser_operation_is_recorded(self):
        registry=ExactOperationRegistry('browser',('browser.open','browser.click'))
        runtime=self.runtime('browser-proof',[action('browser.open',{'url':'https://example.com/'}),final('网页已打开')],registry=registry)
        self.assertEqual(runtime.run('打开网页后检查按钮')['status'],'completed')
        self.assertEqual([x['tool'] for x in runtime.state['successful_actions']],['browser.open'])
        self.assertNotIn('browser.click',[n for n,_ in registry.calls])

    def test_allowed_desktop_preparation_not_blocked_by_keyword_operation_family(self):
        registry=ExactOperationRegistry('desktop',('desktop.move','desktop.click'))
        runtime=self.runtime('desktop-preparation',[action('desktop.move',{'x':10,'y':10}),
              action('desktop.click',{'x':10,'y':10}),final('完成')],registry=registry)
        self.assertEqual(runtime.run('请点击桌面按钮')['status'],'completed')
        self.assertEqual([n for n,_ in registry.calls],['desktop.move','desktop.click'])

    def test_completed_context_keeps_history_but_clears_execution_proofs(self):
        first_registry=ExactOperationRegistry('desktop',('desktop.click',))
        first=self.runtime('completed-parent',[action('desktop.click',{'x':1,'y':1}),final('已点击')],registry=first_registry)
        self.assertEqual(first.run('点击确定')['status'],'completed')
        second_registry=ExactOperationRegistry('desktop',('desktop.click',));events=[]
        second=self.runtime('completed-child',[final('说明后续步骤')],registry=second_registry,events=events)
        self.assertEqual(second.run('解释后续步骤',continuation=first.export_context())['status'],'completed')
        self.assertEqual(second_registry.calls,[])
        self.assertEqual(second.state['successful_actions'],[])
        self.assertEqual(second.state['executed_mutations'],[])
        self.assertEqual(second._original_task,'解释后续步骤')
        self.assertTrue(any(n=='run.new_task_state_isolated' for n,_ in events))
        envelopes=[json.loads(m['content']) for m in second.messages if m['role']=='user' and m['content'].startswith('{')]
        context=next(e for e in envelopes if e.get('type')=='completed_conversation_context')
        self.assertEqual(context['historical_original_task'],'点击确定')
        self.assertEqual(context['current_task'],'解释后续步骤')
        self.assertNotIn('original_task',context)

    def test_failed_run_followup_preserves_progress_without_new_proofs(self):
        first_registry=ExactOperationRegistry('desktop',('desktop.click',))
        first=self.runtime('failed-parent',[action('desktop.click',{'x':1,'y':1}),ClientError('http_error','502')],registry=first_registry)
        self.assertEqual(first.run('点击确定')['status'],'failed')
        second_registry=ExactOperationRegistry('desktop',('desktop.click',))
        second=self.runtime('failed-child',[final('先解释一下')],registry=second_registry)
        self.assertEqual(second.run('先解释一下',continuation=first.export_context())['status'],'completed')
        self.assertEqual(second_registry.calls,[])
        self.assertEqual(len(second.state['successful_actions']),1)
        self.assertEqual(second.state['completion_evidence']['current_run_successful_tools'],0)

    def test_step_limit_followup_cannot_automatically_replay_the_first_click(self):
        registry=ExactOperationRegistry('desktop',('desktop.click',))
        first=self.runtime('limited-parent',[action('desktop.click',{'x':1,'y':1}),action('desktop.click',{'x':2,'y':2})],registry=registry,max_steps=1)
        self.assertEqual(first.run('两次点击')['error_code'],'step_limit')
        child=ExactOperationRegistry('desktop',('desktop.click',))
        second=self.runtime('limited-child',[final('暂停解释')],registry=child)
        self.assertEqual(second.run('暂停解释',continuation=first.export_context())['status'],'completed')
        self.assertEqual(child.calls,[])
        self.assertEqual(len(second.state['successful_actions']),1)

    def test_changed_followup_completes_after_a_new_verified_click(self):
        first_registry = ExactOperationRegistry("desktop", ("desktop.click",))
        first = self.runtime(
            "failed-click-parent-for-real-followup",
            [action("desktop.click", {"x": 1, "y": 1}),
             ClientError("http_error", "HTTP 502")],
            registry=first_registry,
        )
        self.assertEqual(first.run("请点击桌面上的确定按钮")["status"], "failed")

        second_registry = ExactOperationRegistry("desktop", ("desktop.click",))
        second = self.runtime(
            "failed-click-followup-real-action",
            [action("desktop.click", {"x": 2, "y": 2}), final("取消按钮已点击。")],
            registry=second_registry,
        )
        result = second.run("改为点击取消", continuation=first.export_context())

        self.assertEqual(result["status"], "completed")
        self.assertEqual(second_registry.calls, [("desktop.click", {"x": 2, "y": 2})])
        current_proofs = [item for item in second.state["successful_actions"]
                          if item.get("run_id") == second.run_dir.name]
        self.assertEqual(len(current_proofs), 1)

    def test_format_followup_uses_historical_progress_without_reclicking(self):
        registry=ExactOperationRegistry('desktop',('desktop.click',))
        first=self.runtime('format-parent',[action('desktop.click',{'x':1,'y':1}),ClientError('http_error','502')],registry=registry)
        self.assertEqual(first.run('点击确定')['status'],'failed')
        child=ExactOperationRegistry('desktop',('desktop.click',))
        second=self.runtime('format-child',[final('已点击确定')],registry=child)
        self.assertEqual(second.run('请用中文回答',continuation=first.export_context())['status'],'completed')
        self.assertEqual(child.calls,[])
        self.assertEqual(second.state['successful_actions'][0]['run_id'],first.run_dir.name)

    def test_followup_can_select_a_new_operation_without_reclassifying(self):
        registry=ExactOperationRegistry('desktop',('desktop.click',))
        first=self.runtime('change-parent',[action('desktop.click',{'x':1,'y':1}),ClientError('http_error','502')],registry=registry)
        self.assertEqual(first.run('点击确定')['status'],'failed')
        child=ExactOperationRegistry('desktop',('desktop.move','desktop.click'))
        second=self.runtime('change-child',[action('desktop.move',{'x':3,'y':4}),final('已移动')],registry=child)
        self.assertEqual(second.run('改为移动鼠标',continuation=first.export_context())['status'],'completed')
        self.assertEqual(child.calls,[('desktop.move',{'x':3,'y':4})])
        self.assertEqual(second.state['completion_evidence']['current_run_successful_tools'],1)

    def test_model_can_plan_search_then_save_without_runtime_injected_actions(self):
        events=[];registry=self.registry('search-save',events=events)
        runtime=self.runtime('search-save',[action('web.search',{'query':'AI news'}),
              action('files.write',{'path':'report.md','content':'retrieved result'}),final('已搜索并保存')],registry=registry,events=events)
        self.assertEqual(runtime.run('搜索今天AI新闻并保存')['status'],'completed')
        self.assertEqual([n for n,_ in registry.calls],['web.search','files.write'])
        self.assertEqual((self.workspace/'report.md').read_text(),'retrieved result')
        self.assertIsNotNone(runtime.state['last_web_search'])
        self.assertFalse(any(n.startswith('intent.') for n,_ in events))

    def test_search_mode_is_schema_checked_not_inferred_from_user_keywords(self):
        events=[];registry=self.registry('selected-mode',events=events)
        runtime=self.runtime('selected-mode',[action('web.search',{'query':'AI','mode':'parallel'}),final('完成')],registry=registry,events=events)
        self.assertEqual(runtime.run('AI')['status'],'completed')
        self.assertEqual(registry.calls,[('web.search',{'query':'AI','mode':'parallel'})])
        self.assertFalse(any(n=='intent.action_blocked' for n,_ in events))

    def test_explicit_parallel_search_allows_parallel_mode(self):
        for index, query in enumerate((
            "请多路并行搜索今天 AI 新闻",
            "请多路并行查找今天 AI 新闻",
            "Please parallel look up today's AI news",
        )):
            with self.subTest(query=query):
                name = f"search-mode-parallel-{index}"
                registry = self.registry(name, search_outcomes=(True,))
                runtime = self.runtime(
                    name,
                    [action("web.search", {"query": query, "mode": "parallel"}, "按要求多路搜索"),
                     final("已根据多路实时搜索结果回答。")],
                    registry=registry,
                )

                result = runtime.run(query)

                self.assertEqual(result["status"], "completed")
                self.assertEqual(registry.calls, [(
                    "web.search", {"query": query, "mode": "parallel"},
                )])

    def test_matching_read_only_retry_resolves_the_recorded_failure(self):
        context=self.failed_search_context('retry-parent');registry=self.registry('retry-child')
        args={'query':'今天 AI 新闻','mode':'sequential'}
        runtime=self.runtime('retry-child',[action('web.search',args),final('已查询')],registry=registry)
        self.assertEqual(runtime.run('继续',continuation=context)['status'],'completed')
        self.assertEqual(registry.calls,[('web.search',args)])
        self.assertEqual(runtime.state['unresolved_failures'],[])

    def test_explicit_default_result_limit_matches_the_same_failed_request(self):
        context=self.failed_search_context('limit-parent');registry=self.registry('limit-child')
        args={'query':'今天 AI 新闻','mode':'sequential','max_results':8}
        runtime=self.runtime('limit-child',[action('web.search',args),final('已查询')],registry=registry)
        self.assertEqual(runtime.run('继续',continuation=context)['status'],'completed')
        self.assertEqual(runtime.state['unresolved_failures'],[])
        self.assertEqual(registry.calls,[('web.search',args)])

    def test_different_query_can_run_but_cannot_erase_original_failure(self):
        context=self.failed_search_context('different-parent');registry=self.registry('different-child')
        runtime=self.runtime('different-child',[action('web.search',{'query':'cats'}),final('已查询')],registry=registry)
        result=runtime.run('继续',continuation=context)
        self.assertEqual(result['error_code'],'tool_failed')
        self.assertEqual(registry.calls,[('web.search',{'query':'cats'})])
        self.assertEqual(len(runtime.state['unresolved_failures']),1)

    def test_followup_text_is_passed_to_model_without_synthetic_search(self):
        context=self.failed_action_context('text-parent','搜索新闻并保存out.md')
        registry=self.registry('text-child')
        runtime=self.runtime('text-child',[action('files.write',{'path':'out.md','content':'现有资料'}),final('已保存')],registry=registry)
        result=runtime.run('不搜索了，只保存现有资料',continuation=context)
        self.assertEqual(result['status'],'completed')
        self.assertEqual([n for n,_ in registry.calls],['files.write'])
        self.assertEqual((self.workspace/'out.md').read_text(),'现有资料')

    def test_followup_cannot_silently_discard_an_actual_failed_tool(self):
        context=self.failed_search_context('retain-parent');registry=self.registry('retain-child')
        runtime=self.runtime('retain-child',[final('先不搜索')],registry=registry)
        result=runtime.run('先不搜索',continuation=context)
        self.assertEqual(result['error_code'],'tool_failed')
        self.assertEqual(registry.calls,[])
        self.assertEqual(len(runtime.state['unresolved_failures']),1)
        self.assertFalse(runtime.state['superseded_failures'])

    def test_current_capability_denial_stops_mutation_regardless_of_task_text(self):
        registry=self.registry('denied')
        registry.inner.allowed.discard('files')
        registry.inner.denied.add('files')
        runtime=self.runtime('denied',[action('files.write',{'path':'report.md','content':'not allowed'}),final('无法写入')],registry=registry)
        result=runtime.run('请处理这个任务')
        self.assertEqual(result['error_code'],'tool_failed')
        self.assertFalse((self.workspace/'report.md').exists())
        self.assertFalse(runtime.state['successful_actions'])


if __name__ == "__main__":
    unittest.main()
