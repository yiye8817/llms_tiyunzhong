import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fusion_agent.contracts import ToolError
from fusion_agent.skills import MAX_SKILL_BYTES, SkillLibrary


PROJECT = Path(__file__).resolve().parents[1]


def skill_text(name="demo", description="一个测试技能", body="按需阅读的正文"):
    return f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n\n{body}\n"


class SkillsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = SkillLibrary(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, name="demo", text=None):
        directory = self.root / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(text or skill_text(name), encoding="utf-8")
        return directory

    def test_catalog_sorted_and_body_loaded_only_on_request(self):
        self.add("second")
        self.add("first")
        self.assertEqual([item["name"] for item in self.library.catalog()], ["first", "second"])
        self.assertEqual(set(self.library.catalog()[0]), {"name", "description"})
        self.assertIn("按需阅读的正文", self.library.load("first"))

    def test_metadata_discovery_does_not_decode_body(self):
        directory = self.add()
        with (directory / "SKILL.md").open("ab") as stream:
            stream.write(b"\xff")
        self.assertEqual(len(self.library.catalog()), 1)
        with self.assertRaises(ToolError) as raised:
            self.library.load("demo")
        self.assertEqual(raised.exception.code, "invalid_skill")

    def test_missing_root_and_missing_skill(self):
        missing = SkillLibrary(self.root / "absent")
        self.assertEqual(missing.catalog(), [])
        with self.assertRaises(ToolError) as raised:
            missing.load("demo")
        self.assertEqual(raised.exception.code, "skill_not_found")

    def test_reject_traversal_and_invalid_names(self):
        for name in ("../demo", "demo/sub", "/demo", "Upper", "-demo", "a--b", "a" * 65, None):
            with self.subTest(name=name), self.assertRaises(ToolError) as raised:
                self.library.load(name)
            self.assertEqual(raised.exception.code, "invalid_skill_name")

    def test_invalid_metadata_is_skipped_but_other_skill_remains(self):
        self.add("good")
        for name, text in [
            ("unknown", skill_text("unknown").replace("\n---\n\n", "\npermissions: all\n---\n\n")),
            ("mismatch", skill_text("different")),
            ("duplicate", skill_text("duplicate").replace("description:", "name: duplicate\ndescription:")),
            ("newline", skill_text("newline", "multi\nline")),
            ("long", skill_text("long", "a" * 513)),
            ("list", "---\nname: list\ndescription: [bad]\n---\n"),
        ]:
            self.add(name, text)
        self.assertEqual([item["name"] for item in self.library.catalog()], ["good"])

    def test_reject_oversized_file_and_header(self):
        self.add("large", skill_text("large", body="x" * MAX_SKILL_BYTES))
        self.add("header", "---\nname: header\ndescription: " + "x" * 9000 + "\n---\n")
        self.assertEqual(self.library.catalog(), [])
        for name in ("large", "header"):
            with self.assertRaises(ToolError):
                self.library.load(name)

    def test_reject_symlink_skill_and_file(self):
        original = self.add("original")
        (self.root / "linked").symlink_to(original, target_is_directory=True)
        file_link = self.root / "file-link"
        file_link.mkdir()
        (file_link / "SKILL.md").symlink_to(original / "SKILL.md")
        self.assertEqual([item["name"] for item in self.library.catalog()], ["original"])
        for name in ("linked", "file-link"):
            with self.assertRaises(ToolError):
                self.library.load(name)

    def test_reject_symlink_ancestor_of_root(self):
        real = self.root / "real"
        real.mkdir()
        nested = real / "skills"
        nested.mkdir()
        (self.root / "link").symlink_to(real, target_is_directory=True)
        with self.assertRaises(ToolError) as raised:
            SkillLibrary(self.root / "link" / "skills").catalog()
        self.assertEqual(raised.exception.code, "invalid_skill_root")

    def test_scripts_are_never_executed_by_discovery_or_reading(self):
        directory = self.add()
        marker = self.root / "executed"
        script = directory / "run.py"
        script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.library.catalog()
        self.library.load("demo")
        self.assertFalse(marker.exists())

    def test_tool_contract_reports_base_path_without_granting_capabilities(self):
        self.add()
        specs = {tool.name: tool for tool in self.library.specs()}
        self.assertEqual(set(specs), {"skills.list", "skills.read"})
        self.assertTrue(all(tool.capability == "skills" and not tool.mutating for tool in specs.values()))
        self.assertEqual(specs["skills.list"].handler({})["skills"], self.library.catalog())
        result = specs["skills.read"].handler({"name": "demo"})
        self.assertEqual(result["base_path"], str(self.root / "demo"))
        self.assertEqual(result["content"], self.library.load("demo"))

    def test_shipped_skills_are_valid(self):
        shipped = SkillLibrary(PROJECT / "skills")
        self.assertEqual([item["name"] for item in shipped.catalog()],
                         ["browser-research", "desktop-note", "system-report"])
        for item in shipped.catalog():
            self.assertIn("\n# ", shipped.load(item["name"]))


class ReportScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = PROJECT / "skills" / "system-report" / "scripts" / "report.py"
        spec = importlib.util.spec_from_file_location("skill_system_report", cls.script)
        cls.report = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.report)

    def test_real_cli_writes_markdown_in_temporary_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, FUSION_API_KEY="secret-not-for-report")
            completed = subprocess.run([sys.executable, str(self.script), "--workspace", temporary,
                                        "--output", "reports/report.md"], capture_output=True,
                                       text=True, env=env, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            target = Path(temporary) / "reports" / "report.md"
            report = target.read_text(encoding="utf-8")
            self.assertIn("# 基础环境报告", report)
            self.assertIn("| Python |", report)
            self.assertNotIn("secret-not-for-report", report + completed.stdout)
            self.assertEqual(json.loads(completed.stdout)["bytes"], len(report.encode("utf-8")))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_report_refuses_overwrite_traversal_and_symlink_destination(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            self.report.write_report(root, "report.md")
            before = (root / "report.md").read_bytes()
            with self.assertRaises(FileExistsError):
                self.report.write_report(root, "report.md")
            self.assertEqual((root / "report.md").read_bytes(), before)
            for destination in ("../escape.md", "/tmp/escape.md", "bad.txt"):
                with self.subTest(destination=destination), self.assertRaises(ValueError):
                    self.report.write_report(root, destination)
            (root / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                self.report.write_report(root, "link/escape.md")
            self.assertFalse((Path(outside) / "escape.md").exists())


if __name__ == "__main__":
    unittest.main()
