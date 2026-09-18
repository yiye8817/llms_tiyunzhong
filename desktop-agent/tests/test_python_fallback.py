import tempfile
from pathlib import Path
import unittest

from fusion_agent.local_tools import LocalTools
from fusion_agent.python_fallback import PythonFileFallbacks
from fusion_agent.registry import ToolRegistry


class PythonFileFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.tools = LocalTools(self.workspace, self.base / "runtime")
        self.fallbacks = PythonFileFallbacks(self.tools)

    def tearDown(self):
        self.tools.close()
        self.temporary.cleanup()

    @staticmethod
    def registry_for(resolution, allowed=("files",)):
        return ToolRegistry([resolution.as_tool_spec()], allowed=allowed)

    def test_missing_files_write_uses_hardened_python_spec(self):
        resolution = self.fallbacks.resolve("files.write", {})
        self.assertIsNotNone(resolution)
        self.assertFalse(resolution.uses_existing)
        self.assertEqual(resolution.implementation, "python_local_tools")
        self.assertEqual(resolution.canonical_name, "files.write")
        self.assertEqual(resolution.spec.capability, "files")
        self.assertTrue(resolution.spec.mutating)

        result = self.registry_for(resolution).invoke(
            "files.write", {"path": "notes/answer.md", "content": "完成", "create_parents": True}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"]["status"], "verified")
        self.assertEqual((self.workspace / "notes/answer.md").read_text(), "完成")

    def test_common_aliases_keep_file_contracts(self):
        write = self.fallbacks.resolve("filesystem.write_file", {})
        result = self.registry_for(write).invoke(
            "filesystem.write_file", {"path": "answer.txt", "content": "hello"}
        )
        self.assertTrue(result["ok"])

        read = self.fallbacks.resolve("read_text_file", {})
        result = self.registry_for(read).invoke("read_text_file", {"path": "answer.txt"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "hello")

        listing = self.fallbacks.resolve("list_directory", {})
        result = self.registry_for(listing).invoke("list_directory", {"path": "."})
        self.assertTrue(result["ok"])
        self.assertEqual([entry["path"] for entry in result["entries"]], ["answer.txt"])

    def test_exact_registered_tool_wins_and_alias_reuses_it(self):
        real = {spec.name: spec for spec in self.tools.specs()}["files.write"]
        self.assertIsNone(self.fallbacks.resolve("files.write", {"files.write": real}))

        alias = self.fallbacks.resolve("write_file", {"files.write": real})
        self.assertIsNotNone(alias)
        self.assertTrue(alias.uses_existing)
        self.assertIs(alias.spec, real)
        self.assertEqual(alias.implementation, "registered_tool")

        # A catalog containing only names still prevents fallback shadowing.
        self.assertIsNone(self.fallbacks.resolve("write_file", {"files.write"}))

    def test_unknown_or_code_execution_names_are_never_guessed(self):
        for name in (
            "python",
            "python.run",
            "eval",
            "shell.run",
            "execute",
            "Files.Write",
            " files.write",
            "files.append",
        ):
            with self.subTest(name=name):
                self.assertIsNone(self.fallbacks.resolve(name, {}))

    def test_model_supplied_python_is_written_as_text_not_executed(self):
        marker = self.base / "must-not-exist"
        content = f'__import__("pathlib").Path({str(marker)!r}).write_text("bad")'
        resolution = self.fallbacks.resolve("write_file", {})
        result = self.registry_for(resolution).invoke(
            "write_file", {"path": "literal.py", "content": content}
        )
        self.assertTrue(result["ok"])
        self.assertFalse(marker.exists())
        self.assertEqual((self.workspace / "literal.py").read_text(), content)

    def test_path_traversal_and_symlink_escape_are_rejected(self):
        resolution = self.fallbacks.resolve("write_file", {})
        registry = self.registry_for(resolution)
        result = registry.invoke("write_file", {"path": "../escape.txt", "content": "no"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "path_outside_workspace")
        self.assertEqual(result["execution"], {"status": "not_started"})
        self.assertFalse((self.base / "escape.txt").exists())

        outside = self.base / "outside"
        outside.mkdir()
        self.workspace.mkdir(exist_ok=True)
        (self.workspace / "link").symlink_to(outside, target_is_directory=True)
        result = registry.invoke("write_file", {"path": "link/escape.txt", "content": "no"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "path_outside_workspace")
        self.assertFalse((outside / "escape.txt").exists())

    def test_capability_denial_happens_before_file_mutation(self):
        resolution = self.fallbacks.resolve("files.write", {})
        result = self.registry_for(resolution, allowed=()).invoke(
            "files.write", {"path": "denied.txt", "content": "no"}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "capability_denied")
        self.assertEqual(result["execution"], {"status": "not_started"})
        self.assertFalse((self.workspace / "denied.txt").exists())

    def test_existing_file_requires_explicit_overwrite(self):
        resolution = self.fallbacks.resolve("files.write", {})
        registry = self.registry_for(resolution)
        self.assertTrue(registry.invoke(
            "files.write", {"path": "answer.txt", "content": "first"}
        )["ok"])

        refused = registry.invoke("files.write", {"path": "answer.txt", "content": "second"})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"]["code"], "file_exists")
        self.assertEqual((self.workspace / "answer.txt").read_text(), "first")

        replaced = registry.invoke(
            "files.write", {"path": "answer.txt", "content": "second", "overwrite": True}
        )
        self.assertTrue(replaced["ok"])
        self.assertEqual(replaced["verification"]["status"], "verified")
        self.assertEqual((self.workspace / "answer.txt").read_text(), "second")

    def test_missing_specs_add_only_absent_canonical_tools(self):
        real = {spec.name: spec for spec in self.tools.specs()}
        missing = self.fallbacks.missing_specs({"files.read": real["files.read"]})
        self.assertEqual([spec.name for spec in missing], ["files.list", "files.stat", "files.search",
                                                           "files.write", "files.mkdir", "files.copy",
                                                           "files.move", "files.delete"])

    def test_registry_materializes_missing_python_tool_without_bypassing_guards(self):
        events = []
        registry = ToolRegistry([], allowed=("files",), fallbacks=self.fallbacks,
                                event=lambda name, fields: events.append((name, fields)))
        result = registry.invoke("files.write", {"path": "generated.md", "content": "safe"})
        self.assertTrue(result["ok"])
        self.assertEqual((self.workspace / "generated.md").read_text(), "safe")
        self.assertIn("files.write", {item["name"] for item in registry.catalog()})
        resolved = next(fields for name, fields in events if name == "tool.python_fallback_resolved")
        self.assertEqual(resolved["payload"]["resolution"]["implementation"], "python_local_tools")

    def test_registry_unknown_alias_reuses_real_tool_and_unknown_code_stays_rejected(self):
        events = []
        registry = ToolRegistry(self.tools.specs(), allowed=("files",), fallbacks=self.fallbacks,
                                event=lambda name, fields: events.append((name, fields)))
        result = registry.invoke("write_file", {"path": "alias.txt", "content": "text"})
        self.assertTrue(result["ok"])
        self.assertEqual((self.workspace / "alias.txt").read_text(), "text")
        self.assertEqual(events[0][0], "tool.alias_resolved")
        rejected = registry.invoke("python.run", {"code": "print('no')"})
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"]["code"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
