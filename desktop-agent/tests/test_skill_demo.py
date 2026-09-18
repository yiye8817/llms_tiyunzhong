from concurrent.futures import ThreadPoolExecutor
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from fusion_agent.contracts import ToolError
from fusion_agent.skill_demo import create_demo_skill
from fusion_agent.skills import SkillLibrary


class SkillDemoTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.skills = self.root / "skills"

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_and_load_complete_demo_without_executing_script(self):
        result = create_demo_skill("workspace-report-demo", self.skills)
        library = SkillLibrary(self.skills)
        self.assertEqual(library.catalog(), [{"name": result["name"], "description": result["description"]}])
        response = {tool.name: tool for tool in library.specs()}["skills.read"].handler({"name": result["name"]})
        self.assertEqual(response["base_path"], result["path"])
        self.assertIn("files.list", response["content"])
        self.assertEqual(response["content"], library.load(result["name"]))
        self.assertEqual(len(result["files"]), 2)
        for filename in result["files"]:
            self.assertTrue(Path(filename).is_file())
            self.assertEqual(Path(filename).stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(path.name for path in self.skills.iterdir()), [result["name"]])
        self.assertFalse((self.root / "workspace-report.md").exists())

    def test_reject_invalid_names_without_creating_root(self):
        for name in ("../escape", "/tmp/demo", "nested/demo", "Upper", "demo_thing", "a--b", "-demo", "a" * 65, None):
            with self.subTest(name=name), self.assertRaises(ToolError) as raised:
                create_demo_skill(name, self.skills)
            self.assertEqual(raised.exception.code, "invalid_skill_name")
        self.assertFalse(self.skills.exists())

    def test_existing_empty_directory_file_and_symlink_are_preserved(self):
        self.skills.mkdir()
        (self.skills / "empty").mkdir()
        (self.skills / "file").write_text("keep", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (self.skills / "linked").symlink_to(outside, target_is_directory=True)
        for name in ("empty", "file", "linked"):
            with self.subTest(name=name), self.assertRaises(ToolError) as raised:
                create_demo_skill(name, self.skills)
            self.assertEqual(raised.exception.code, "skill_exists")
        self.assertEqual(list((self.skills / "empty").iterdir()), [])
        self.assertEqual((self.skills / "file").read_text(), "keep")
        self.assertTrue((self.skills / "linked").is_symlink())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(sorted(path.name for path in self.skills.iterdir()), ["empty", "file", "linked"])

    def test_reject_symlink_ancestors_and_traversal_in_root(self):
        real = self.root / "real"
        real.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        for path in (linked, linked / "nested", self.root / "somewhere" / ".." / "skills", self.root / "bad\x00path"):
            with self.subTest(path=path), self.assertRaises(ToolError) as raised:
                create_demo_skill("demo", path)
            self.assertEqual(raised.exception.code, "invalid_skill_root")
        self.assertEqual(list(real.iterdir()), [])

    def test_write_failure_leaves_no_published_or_partial_skill(self):
        with mock.patch("fusion_agent.skill_demo.os.fsync", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaises(ToolError) as raised:
                create_demo_skill("demo", self.skills)
        self.assertEqual(raised.exception.code, "skill_create_failed")
        self.assertEqual(list(self.skills.iterdir()), [])

    def test_no_unsafe_fallback_when_atomic_publication_is_unavailable(self):
        with mock.patch("fusion_agent.skill_demo._publish_directory", side_effect=OSError(errno.ENOSYS, "unsupported")):
            with self.assertRaises(ToolError) as raised:
                create_demo_skill("demo", self.skills)
        self.assertEqual(raised.exception.code, "skill_atomic_unavailable")
        self.assertEqual(list(self.skills.iterdir()), [])

    def test_concurrent_creation_publishes_one_complete_skill(self):
        def create():
            try:
                return create_demo_skill("demo", self.skills)
            except ToolError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("skill_exists", results)
        self.assertEqual([item["name"] for item in SkillLibrary(self.skills).catalog()], ["demo"])
        self.assertEqual([path.name for path in self.skills.iterdir()], ["demo"])

    def run_script(self, workspace, *arguments):
        script = self.skills / "demo" / "scripts" / "workspace_report.py"
        return subprocess.run([sys.executable, str(script), "--workspace", str(workspace), *arguments],
                              capture_output=True, text=True, timeout=10)

    def test_generated_script_reads_only_top_level_metadata_without_writes(self):
        create_demo_skill("demo", self.skills)
        workspace = self.root / "workspace"
        workspace.mkdir()
        (workspace / "notes.txt").write_text("PRIVATE_FILE_CONTENT", encoding="utf-8")
        (workspace / "nested").mkdir()
        (workspace / "nested" / "not-listed.txt").write_text("PRIVATE_NESTED_CONTENT")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("OUTSIDE_CONTENT")
        (workspace / "linked").symlink_to(outside, target_is_directory=True)
        before = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        completed = self.run_script(workspace)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["entry_count"], 3)
        self.assertFalse(report["truncated"])
        self.assertFalse(report["follows_symlinks"])
        self.assertEqual({item["path"]: item["type"] for item in report["entries"]},
                         {"notes.txt": "file", "nested": "directory", "linked": "symlink"})
        self.assertNotIn("PRIVATE_", completed.stdout)
        self.assertNotIn("OUTSIDE_CONTENT", completed.stdout)
        self.assertEqual(before, sorted(path.relative_to(self.root) for path in self.root.rglob("*")))

    def test_generated_script_reports_truncation_and_invalid_limits(self):
        create_demo_skill("demo", self.skills)
        workspace = self.root / "workspace"
        workspace.mkdir()
        for index in range(3):
            (workspace / str(index)).write_text("")
        completed = self.run_script(workspace, "--limit", "2")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["entry_count"], 2)
        self.assertTrue(json.loads(completed.stdout)["truncated"])
        for limit in ("0", "501"):
            with self.subTest(limit=limit):
                completed = self.run_script(workspace, "--limit", limit)
                self.assertEqual(completed.returncode, 2)
                self.assertFalse(json.loads(completed.stderr)["ok"])

    def test_generated_script_rejects_symlink_root_and_ancestor(self):
        create_demo_skill("demo", self.skills)
        real = self.root / "real"
        real.mkdir()
        (real / "nested").mkdir()
        link = self.root / "linked"
        link.symlink_to(real, target_is_directory=True)
        for path in (link, link / "nested", real / ".." / "real"):
            with self.subTest(path=path):
                completed = self.run_script(path)
                self.assertEqual(completed.returncode, 2)
                self.assertFalse(json.loads(completed.stderr)["ok"])
                self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
