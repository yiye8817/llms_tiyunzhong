import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.history import History, MAX_TRANSCRIPT_BYTES


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "runtime"
        self.history = History(self.root, secrets=("private-fixture-key",))

    def run_record(self, name="20260909T010000Z-abcd1234", task="分析目录占用", answer="## 报告\n\n结果是 3 MB。", status="completed"):
        path = self.root / "runs" / name
        path.mkdir(parents=True)
        (path / "state.json").write_text(json.dumps({"status": status, "started_at": 1788915600,
             "updated_at": 1788915700, "steps": 1}), encoding="utf-8")
        transcript = [{"role": "system", "content": "SYSTEM PRIVATE CONTENT"},
            {"role": "user", "content": json.dumps({"type": "original_user_task", "task": task})},
            {"role": "assistant", "content": '{"type":"action","arguments":{"argv":["private command"]}}'},
            {"role": "user", "content": json.dumps({"type": "untrusted_tool_observation", "tool": "files.list",
                "observation": {"ok": True, "stdout": "TOOL PRIVATE OUTPUT", "verification": {"status": "passed"}}})}]
        (path / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
        (path / "result.md").write_text("# 任务结果\n\n" + answer, encoding="utf-8")
        if status == "completed":
            (path / "final.md").write_text(answer, encoding="utf-8")
        return path

    def test_old_runs_are_visible_without_mutation_or_session(self):
        path = self.run_record()
        before = {item.name: item.read_bytes() for item in path.iterdir()}
        rows = self.history.list_runs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task"], "分析目录占用")
        self.assertIsNone(rows[0]["session_id"])
        self.assertFalse((self.root / "sessions").exists())
        self.assertEqual(before, {item.name: item.read_bytes() for item in path.iterdir()})

    def test_details_are_read_only_and_omit_protocol_system_and_tool_stdout(self):
        path = self.run_record()
        report = self.history.detail(path.name)
        encoded = json.dumps(report)
        self.assertTrue(report["read_only"])
        self.assertIn("不会恢复执行", report["notice"])
        self.assertEqual(report["tools"][0]["tool"], "files.list")
        self.assertEqual(report["tools"][0]["status"], "成功")
        self.assertEqual(report["answer"], "## 报告\n\n结果是 3 MB。")
        self.assertNotIn("SYSTEM PRIVATE", encoded)
        self.assertNotIn("private command", encoded)
        self.assertNotIn("TOOL PRIVATE", encoded)

    def test_session_groups_survive_new_instance_and_track_selected_model(self):
        session_id = self.history.start_session("qwen")
        first = self.run_record()
        second = self.run_record("20260909T020000Z-abcd2345", task="第二任务")
        self.history.record_run(session_id, first.name, model="qwen", task="token=do-not-save")
        self.history.record_run(session_id, second.name, model="deepseek")
        self.history.record_run(session_id, second.name, model="deepseek")
        restarted = History(self.root)
        report = restarted.session_detail(session_id)
        self.assertEqual([row["task"] for row in report["runs"]], ["第二任务", "分析目录占用"])
        self.assertEqual([row["model"] for row in report["runs"]], ["deepseek", "qwen"])
        self.assertEqual(restarted.list_sessions()[0]["run_count"], 2)
        index = self.root / "sessions" / (session_id + ".json")
        self.assertEqual(stat.S_IMODE(index.stat().st_mode), 0o600)
        self.assertNotIn("do-not-save", index.read_text())
        self.assertNotIn("分析目录占用", index.read_text())
        self.assertEqual(len(list(index.parent.iterdir())), 1)

    def test_queries_search_full_original_task_and_result_not_only_excerpt(self):
        self.run_record(task="甲" * 600 + "末尾关键词", answer="乙" * 600 + "ANSWER-TAG")
        self.assertEqual(len(self.history.list_runs("末尾关键词")), 1)
        self.assertEqual(len(self.history.list_runs("answer-tag")), 1)
        self.assertLess(len(self.history.list_runs()[0]["task"]), 405)
        self.assertEqual(self.history.list_runs("不存在关键词"), [])

    def test_continued_run_lists_current_prompt_and_retains_original_goal_for_search(self):
        path = self.run_record(task="分析内存日志")
        transcript = json.loads((path / "transcript.json").read_text())
        transcript.append({"role": "user", "content": json.dumps({"type": "followup_user_task", "task": "继续并补充结论"})})
        (path / "transcript.json").write_text(json.dumps(transcript))
        report = self.history.detail(path.name)
        self.assertEqual(report["task"], "继续并补充结论")
        self.assertEqual(report["original_task"], "分析内存日志")
        self.assertEqual(report["tools"], [])
        self.assertEqual(len(self.history.list_runs("内存日志")), 1)
        self.assertEqual(len(self.history.list_runs("补充结论")), 1)

    def test_controls_and_known_credentials_are_filtered(self):
        self.run_record(task="\x1b[31m查询\x1b[0m\x00\n内容 private-fixture-key", answer="token=abc123\nAuthorization: secret")
        report = self.history.detail("20260909T010000Z-abcd1234")
        text = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("private-fixture-key", text)
        self.assertNotIn("abc123", text)
        self.assertNotIn("\\u001b", text)
        self.assertNotIn("\\u0000", text)
        self.assertEqual(self.history.recent_tasks(), ["查询 内容 [REDACTED]"])

    def test_recent_tasks_are_local_deduplicated_and_never_commands(self):
        self.run_record(task="自然语言任务")
        self.run_record("20260909T020000Z-abcd2345", task="自然语言任务")
        self.run_record("20260909T030000Z-abcd3456", task="/quit")
        self.run_record("20260909T040000Z-abcd4567", task="长" * 1000)
        self.assertEqual(self.history.recent_tasks(), ["自然语言任务"])

    def test_damaged_record_does_not_hide_other_runs(self):
        good = self.run_record()
        damaged = self.root / "runs" / "20260909T030000Z-badbad00"
        damaged.mkdir()
        (damaged / "state.json").write_text("{")
        (damaged / "transcript.json").write_text("[]")
        self.assertEqual([row["run_id"] for row in self.history.list_runs()], [good.name])
        with self.assertRaises(ValueError):
            self.history.detail(damaged.name)

    def test_oversized_transcript_is_not_read_and_valid_state_still_visible(self):
        path = self.run_record()
        with (path / "transcript.json").open("wb") as handle:
            handle.truncate(MAX_TRANSCRIPT_BYTES + 1)
        rows = self.history.list_runs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task"], "")
        self.assertIn("3 MB", rows[0]["answer"])

    def test_fifo_record_cannot_block_query(self):
        path = self.run_record()
        (path / "transcript.json").unlink()
        os.mkfifo(path / "transcript.json")
        self.assertEqual(self.history.list_runs()[0]["task"], "")

    def test_symlink_files_and_run_directories_are_not_followed(self):
        path = self.run_record()
        secret = Path(self.temp.name) / "secret.md"
        secret.write_text("OUTSIDE SECRET")
        (path / "final.md").unlink()
        (path / "final.md").symlink_to(secret)
        linked = path.parent / "20260909T090000Z-eeeeeeee"
        linked.symlink_to(path, target_is_directory=True)
        rows = self.history.list_runs()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("OUTSIDE SECRET", rows[0]["answer"])
        with self.assertRaises(ValueError):
            self.history.detail(linked.name)

    def test_linked_runtime_or_session_index_is_rejected(self):
        session = self.history.start_session()
        index = self.root / "sessions" / (session + ".json")
        outside = Path(self.temp.name) / "outside.json"
        outside.write_bytes(index.read_bytes())
        index.unlink()
        index.symlink_to(outside)
        before = outside.read_bytes()
        with self.assertRaises(ValueError):
            self.history.record_run(session, "valid-run")
        self.assertEqual(outside.read_bytes(), before)
        linked = Path(self.temp.name) / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(History(linked).list_runs(), [])
        with self.assertRaises(ValueError):
            History(linked).start_session()

    def test_invalid_ids_never_escape_runtime(self):
        for bad in ("../x", "/tmp/x", "x/y", "x.json", "", "\x1b[31m", None, "x" * 129):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.history.detail(bad)
                with self.assertRaises(ValueError):
                    self.history.session_detail(bad)

    def test_missing_runtime_is_empty_and_read_only(self):
        self.assertEqual(self.history.list_runs(), [])
        self.assertEqual(self.history.list_sessions(), [])
        self.assertEqual(self.history.recent_tasks(), [])
        self.assertFalse(self.root.exists())

    def test_failed_result_and_pending_tool_remain_distinct(self):
        path = self.run_record(status="failed", answer="任务未完成")
        messages = json.loads((path / "transcript.json").read_text())
        messages[-1]["content"] = json.dumps({"type": "untrusted_tool_observation", "tool": "browser.click",
             "observation": {"ok": True, "verification": {"status": "pending"}}})
        (path / "transcript.json").write_text(json.dumps(messages))
        report = self.history.detail(path.name)
        self.assertEqual(report["status"], "failed")
        self.assertIn("任务未完成", report["answer"])
        self.assertEqual(report["tools"][0]["status"], "待核验")

    def test_invalid_limits_fail_fast(self):
        for count in (0, -1, True, 201, "20"):
            with self.assertRaises(ValueError):
                self.history.list_runs(limit=count)
        with self.assertRaises(ValueError):
            self.history.list_runs("x" * 501)

    def test_atomic_write_failure_leaves_existing_index_intact(self):
        session_id = self.history.start_session()
        index = self.root / "sessions" / (session_id + ".json")
        before = index.read_bytes()
        with patch("fusion_agent.history.os.replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                self.history.record_run(session_id, "run-fixture")
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(len(list(index.parent.iterdir())), 1)

    def test_corrupt_timestamp_and_unpaired_surrogate_do_not_break_listing(self):
        path = self.run_record(task="任务\ud800文本")
        state = json.loads((path / "state.json").read_text())
        state["started_at"] = 10 ** 400
        state["updated_at"] = float("nan")
        (path / "state.json").write_text(json.dumps(state))
        row = self.history.list_runs()[0]
        self.assertEqual(row["started_at"], "")
        self.assertEqual(row["updated_at"], "")
        self.assertEqual(row["task"], "任务?文本")
        json.dumps(row, ensure_ascii=False).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
