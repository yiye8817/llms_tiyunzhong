import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from fusion_agent.registry import ToolRegistry
from fusion_agent.cli import main
from fusion_agent.runtime import Runtime
from fusion_agent.skills import AgentSkillLibrary, SkillLibrary, _PACKAGED_BUILTIN_SKILLS
from fusion_agent.web_search import SearchResult, WebSearchTools


class _Client:
    model = "qwen"

    def __init__(self):
        self.messages = None

    def complete(self, messages):
        self.messages = messages
        return json.dumps({"type": "final", "answer": "direct result"})


class _Backend:
    def __init__(self, name):
        self.name = name

    def search(self, query, maximum, timeout):
        return [SearchResult(self.name, f"https://{self.name}.example/", "result", self.name)]


class ResearchPolicyTests(unittest.TestCase):
    def test_runtime_tells_web_models_to_prefer_direct_search_results(self):
        client = _Client()
        with tempfile.TemporaryDirectory() as temporary:
            result = Runtime(client, ToolRegistry([]), Path(temporary) / "run").run("latest news")
        self.assertEqual(result["status"], "completed")
        system = client.messages[0]["content"]
        self.assertIn("默认已启用其站内 web_search", system)
        self.assertIn("优先让当前网页模型直接使用站内搜索", system)
        self.assertIn("明确要求本地多路/并行抓取", system)

    def test_browser_research_requests_parallel_configured_backends(self):
        content = SkillLibrary(Path(__file__).resolve().parents[1] / "skills").load("browser-research")
        self.assertIn('"mode":"parallel"', content)
        for name in ("DDG/DDGo", "Browser Use", "OpenCLI", "Playwright"):
            self.assertIn(name, content)

    def test_packaged_copy_contains_every_builtin_skill(self):
        names = {row["name"] for row in SkillLibrary(_PACKAGED_BUILTIN_SKILLS).catalog()}
        self.assertEqual(names, {"browser-research", "desktop-note", "system-report"})
        self.assertIn('"mode":"parallel"', SkillLibrary(_PACKAGED_BUILTIN_SKILLS).load("browser-research"))

    def test_custom_skill_directory_still_merges_packaged_builtins(self):
        with tempfile.TemporaryDirectory() as temporary:
            rows = AgentSkillLibrary(Path(temporary), _PACKAGED_BUILTIN_SKILLS).catalog()
        self.assertEqual({row["source"] for row in rows}, {"builtin"})
        self.assertEqual({row["name"] for row in rows},
                         {"browser-research", "desktop-note", "system-report"})

    def test_plural_skills_command_supports_list_and_read_forms(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({
                "workspace": str(root),
                "runtime_dir": str(root / "runtime"),
                "skills_dir": str(root / "user-skills"),
            }))
            for arguments, expected in ((["skills", "list"], '"source": "builtin"'),
                                        (["skills", "read", "browser-research"],
                                         '"mode":"parallel"')):
                output = io.StringIO()
                with self.subTest(arguments=arguments), contextlib.redirect_stdout(output):
                    self.assertEqual(main([*arguments, "--config", str(config)]), 0)
                self.assertIn(expected, output.getvalue())

    def test_parallel_reports_only_routes_configured_on_the_tool(self):
        output = WebSearchTools([_Backend("opencli"), _Backend("playwright")]).search(
            {"query": "example", "mode": "parallel"}
        )
        self.assertEqual(output["backend_order"], ["opencli", "playwright"])
        self.assertEqual([row["backend"] for row in output["attempts"]],
                         ["opencli", "playwright"])


if __name__ == "__main__":
    unittest.main()
