import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fusion_agent.audit import Audit
from fusion_agent.cli import reserve_run_directory
from fusion_agent.runtime import Runtime
from test_runtime import Registry, ScriptedClient, final


class RunDirectoryTests(unittest.TestCase):
    def test_old_events_only_directory_never_receives_new_state_or_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary) / "runtime"
            old_dir = runtime_dir / "runs/20260909T032443Z-deadbeef"
            old_dir.mkdir(parents=True)
            old_log = old_dir / "events.jsonl"
            old_log.write_text('{"event":"old task"}\n')
            with patch("fusion_agent.cli.datetime") as clock, patch("fusion_agent.cli.uuid4", side_effect=[
                SimpleNamespace(hex="deadbeef"), SimpleNamespace(hex="cafebabe")
            ]) as random_id:
                clock.now.return_value.strftime.return_value = "20260909T032443Z-"
                run_id, run_dir = reserve_run_directory(runtime_dir)
            self.assertEqual(random_id.call_count, 2)
            self.assertEqual(run_id, "20260909T032443Z-cafebabe")
            self.assertEqual(run_dir.stat().st_mode & 0o777, 0o700)
            terminal = io.StringIO()
            audit = Audit(runtime_dir / "logs/agent.log", stream=terminal, run_path=run_dir / "events.jsonl")
            try:
                result = Runtime(ScriptedClient([final("新任务结果")]), Registry(), run_dir,
                                 event=lambda event, fields: audit(event, {**fields, "run_id": run_id})).run("新任务")
            finally:
                audit.close()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(old_log.read_text(), '{"event":"old task"}\n')
            self.assertEqual([file.name for file in old_dir.iterdir()], ["events.jsonl"])
            self.assertIn("新任务结果", (run_dir / "result.md").read_text())
            saved = (run_dir / "events.jsonl").read_text()
            self.assertEqual((runtime_dir / "logs/agent.log").read_text(), saved)
            self.assertEqual(terminal.getvalue(), "")
            self.assertTrue(all(json.loads(line)["run_id"] == run_id for line in saved.splitlines()))

    def test_repeated_collisions_stop_after_eight_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            old = runtime_dir / "runs/fixed-deadbeef"
            old.mkdir(parents=True)
            with patch("fusion_agent.cli.datetime") as clock, patch("fusion_agent.cli.uuid4", return_value=SimpleNamespace(hex="deadbeef")) as random_id:
                clock.now.return_value.strftime.return_value = "fixed-"
                with self.assertRaisesRegex(ValueError, "名称冲突"):
                    reserve_run_directory(runtime_dir)
                self.assertEqual(random_id.call_count, 8)
            self.assertEqual(list(old.iterdir()), [])

    def test_runs_or_ancestor_symlink_is_rejected_before_creating_records(self):
        for ancestor in (False, True):
            with self.subTest(ancestor=ancestor), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / "target"
                target.mkdir()
                runtime_dir = root / "runtime"
                if ancestor:
                    runtime_dir.symlink_to(target, target_is_directory=True)
                else:
                    runtime_dir.mkdir()
                    (runtime_dir / "runs").symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "符号链接"):
                    reserve_run_directory(runtime_dir)
                self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
