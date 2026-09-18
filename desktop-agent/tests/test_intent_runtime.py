"""1.17.4 regressions: no inferred intent routes or automatic search injection."""
import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.runtime import Runtime
from fusion_agent.session_context import _make_checkpoint
from test_runtime import ScriptedClient, final


class RecordingSearchRegistry:
    """Minimal real Runtime registry with an observable read-only search tool."""

    def __init__(self, *, result_count=1):
        self.calls = []
        self.result_count = result_count

    def catalog(self):
        return [{
            "name": "web.search",
            "description": "Search the current public web",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["sequential", "parallel"]},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "capability": "web",
            "mutating": False,
        }]

    def invoke(self, name, arguments):
        self.calls.append((name, json.loads(json.dumps(arguments))))
        if name != "web.search":
            return {"ok": False, "error": {"code": "tool_not_found"},
                    "execution": {"status": "not_started"}}
        return {
            "ok": True,
            "mode": arguments.get("mode", "sequential"),
            "result_count": self.result_count,
            "results": ([{"title": "Current result", "url": "https://example.com/current",
                          "snippet": "Fresh public result", "source": "ddgo"}]
                        if self.result_count else []),
            "attempts": [{"backend": "ddgo", "status": "ok", "result_count": self.result_count}],
        }


class RuntimeUniformLoopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, name, replies, registry=None, events=None):
        registry = registry or RecordingSearchRegistry()
        callback = ((lambda event, fields: events.append((event, fields)))
                    if events is not None else None)
        return Runtime(ScriptedClient(replies), registry, self.root / name, event=callback), registry

    @staticmethod
    def legacy_search_checkpoint(original_task="AI新闻", extra_failures=()):
        failures = [
            {"action_id": "old-browser", "tool": "browser.open", "step": 2,
             "scope": "browser", "mutating": True, "retry_key": "browser-key",
             "execution_status": "not_started",
             "error": {"code": "dependency_missing", "message": "missing browser"}},
            {"action_id": "old-setup", "tool": "environment.browser_setup", "step": 3,
             "scope": "shell", "mutating": True, "retry_key": "setup-key",
             "execution_status": "not_started",
             "error": {"code": "dependency_environment", "message": "wrong environment"}},
            {"action_id": "old-shell", "tool": "shell.run", "step": 4,
             "scope": "shell", "mutating": True, "retry_key": "shell-key",
             "execution_status": None,
             "error": {"code": "command_failed", "message": "exit 1"}},
            *extra_failures,
        ]
        state = {
            "status": "failed", "steps": 5, "pending_action": None,
            "uncertain_actions": [], "pending_verifications": {},
            "failed_actions": failures, "unresolved_failures": failures,
            "last_tool_failed": True, "executed_mutations": [],
            "unresolved_action_request": None,
        }
        return _make_checkpoint({
            "version": 1, "run_id": "legacy-search-run",
            "original_task": original_task, "last_task": original_task,
            "messages": [
                {"role": "system", "content": "legacy protocol"},
                {"role": "user", "content": json.dumps(
                    {"type": "original_user_task", "task": original_task}, ensure_ascii=False)},
            ],
            "state": state,
        })

    def test_ordinary_chat_is_tool_capable_but_does_not_force_an_action(self):
        events=[]
        runtime, registry=self.runtime("chat",[final("你好")],events=events)
        self.assertEqual(runtime.run("你好")["status"],"completed")
        self.assertEqual(registry.calls,[])
        self.assertEqual(runtime.state["execution_mode"],"tool_loop")
        self.assertNotIn("intent_route",runtime.state)
        self.assertNotIn("intent.classified",[n for n,_ in events])
        self.assertEqual(runtime.state['completion_evidence']['current_run_successful_tools'],0)

    def test_no_model_final_text_is_reclassified_or_auto_injected_as_search(self):
        events=[]
        runtime,registry=self.runtime("stale",[final("无法确认实时信息")],events=events)
        result=runtime.run("AI新闻")
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["steps"],0)
        self.assertEqual(registry.calls,[])
        self.assertFalse(runtime.state["web_search_satisfied"])
        self.assertFalse(any(n.startswith("intent.") for n,_ in events))

    def test_model_claim_of_retrieval_does_not_create_tool_evidence(self):
        runtime,registry=self.runtime("claimed",[final("已经实时查询")])
        result=runtime.run("北京天气")
        self.assertEqual(result["status"],"completed")
        self.assertEqual(registry.calls,[])
        self.assertEqual(runtime.state['successful_actions'],[])
        self.assertFalse(runtime.state['realtime_satisfied'])
        self.assertEqual(runtime.state['completion_evidence']['basis'],'model_reply_only')

    def test_model_can_refine_the_query_without_keyword_matching(self):
        query="Beijing temperature official weather"
        reply=json.dumps({"type":"action","tool":"web.search","arguments":{"query":query},"summary":"查询来源"})
        runtime,registry=self.runtime("refined",[reply,final("查询完成")])
        self.assertEqual(runtime.run("搜索北京天气")["status"],"completed")
        self.assertEqual(registry.calls,[("web.search",{"query":query})])
        self.assertIsNotNone(runtime.state['last_web_search'])

    def test_first_model_response_may_call_search_without_feedback_gate(self):
        events=[]
        reply=json.dumps({"type":"action","tool":"web.search","arguments":{"query":"AI新闻"},"summary":"搜索"})
        runtime,registry=self.runtime("immediate",[reply,final("查询完成")],events=events)
        self.assertEqual(runtime.run("AI新闻")["status"],"completed")
        self.assertEqual(registry.calls,[("web.search",{"query":"AI新闻"})])
        names=[n for n,_ in events]
        self.assertIn('tool.attempt',names)
        self.assertFalse(any(n.startswith('intent.') for n in names))

    def test_model_selected_parallel_mode_reaches_tool_unchanged(self):
        args={"query":"AI news","mode":"parallel"}
        reply=json.dumps({"type":"action","tool":"web.search","arguments":args,"summary":"并行搜索"})
        runtime,registry=self.runtime("parallel",[reply,final("查询完成")])
        self.assertEqual(runtime.run("请多路并行搜索今天的 AI 新闻")["status"],"completed")
        self.assertEqual(registry.calls,[("web.search",args)])

    def test_completed_conversation_does_not_replay_search_or_reuse_proof(self):
        registry=RecordingSearchRegistry()
        reply=json.dumps({"type":"action","tool":"web.search","arguments":{"query":"BTC价格"},"summary":"查询"})
        first,_=self.runtime("first",[reply,final("已经查询")],registry=registry)
        self.assertEqual(first.run("BTC价格")["status"],"completed")
        second,_=self.runtime("second",[final("补充说明")],registry=registry)
        self.assertEqual(second.run("继续",continuation=first.export_context())["status"],"completed")
        self.assertEqual(len(registry.calls),1)
        self.assertFalse(second.state['web_search_satisfied'])
        self.assertEqual(second.state['successful_actions'],[])
        self.assertEqual(second.state['execution_mode'],'tool_loop')

    def test_legacy_failed_search_chain_is_never_cleared_by_an_unrelated_new_search(self):
        runtime, registry = self.runtime(
            "legacy-not-cleared",
            [final("我无法访问实时信息。")],
        )

        result = runtime.run("继续", continuation=self.legacy_search_checkpoint())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "tool_failed")
        self.assertEqual(registry.calls, [])
        self.assertEqual(len(runtime.state["unresolved_failures"]), 3)
        self.assertTrue(any(item["tool"] == "shell.run" and item["mutating"]
                            for item in runtime.state["unresolved_failures"]))


if __name__ == "__main__":
    unittest.main()
