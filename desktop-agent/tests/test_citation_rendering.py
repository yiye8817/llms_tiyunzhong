import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.rendering import normalize_final_markdown, render_markdown, safe_terminal_text
from fusion_agent.runtime import Runtime, parse_reply
from test_rendering import TTYStream, cell_width
from test_runtime import Registry, ScriptedClient, final


BAD_URL = "http://www.bbc.com/n/n%E3%80%82/n3"
BAD_LINK = "[www.bbc.com\n\n。\\n3](" + BAD_URL + ").\n"
SOURCE = ("2. 机器人索要小费：BBC 报道指出，随着服务机器人的普及，机器人开始向顾客索要小费\n"
          "   ，引发了关于资金流向的讨论\n\n" + BAD_LINK
          + "陪伴经济新尝试：中国科技公司正在尝试推出“性爱”机器人，瞄准日益增长的“陪伴经济”市\n场\n\n"
          "[www.bbc.com\n\n。](http://www.bbc.com/n/n%E3%80%82)")


class CitationRenderingTests(unittest.TestCase):
    def test_reported_citation_moves_punctuation_and_list_number_without_inventing_url(self):
        cleaned, changes = normalize_final_markdown(SOURCE)
        self.assertIn("小费，引发了关于资金流向的讨论。", cleaned)
        self.assertIn("\n\n3. 陪伴经济新尝试", cleaned)
        self.assertIn("“陪伴经济”市场。", cleaned)
        self.assertEqual(cleaned.count("来源：`www.bbc.com`（链接异常）"), 2)
        self.assertNotIn("http", cleaned)
        self.assertNotIn(r"\n", cleaned)
        self.assertEqual([row["displaced_list_number"] for row in changes], [3, None])
        self.assertEqual(sum(row["joined_soft_line_breaks"] for row in changes), 2)
        self.assertEqual(normalize_final_markdown(cleaned), (cleaned, []))

    def test_actual_newline_instead_of_literal_escape_has_same_display(self):
        cleaned, changes = normalize_final_markdown(SOURCE.replace("。\\n3", "。\n3"))
        self.assertEqual(cleaned, normalize_final_markdown(SOURCE)[0])
        self.assertEqual(len(changes), 2)

    def test_plain_and_tty_display_are_readable_and_obey_cjk_width(self):
        for stream in (io.StringIO(), TTYStream()):
            with self.subTest(tty=stream.isatty()), patch.dict(os.environ, {"TERM": "xterm"}, clear=True):
                render_markdown("# 新闻摘录\n\n" + SOURCE, stream, 42)
            rendered = safe_terminal_text(stream.getvalue())
            self.assertIn("3. 陪伴经济新尝试", rendered)
            self.assertIn("www.bbc.com（链接异常）", rendered)
            self.assertNotIn(BAD_URL, rendered)
            self.assertNotIn("](", rendered)
            for line in rendered.splitlines():
                self.assertLessEqual(cell_width(line), 42, line)

    def test_single_polluted_citation_is_not_misidentified_as_json_array(self):
        source = "[www.bbc.com\n\n。](http://www.bbc.com/n/n%E3%80%82)"
        cleaned, changes = normalize_final_markdown(source)
        self.assertEqual(cleaned, "来源：`www.bbc.com`（链接异常）。")
        self.assertEqual(len(changes), 1)

    def test_legal_multiline_label_and_real_n_paths_queries_remain_exact(self):
        for source in (
            "[BBC 新闻\n报道](https://www.bbc.com/news/articles/example)",
            "[www.bbc.com\n新闻](https://www.bbc.com/n/n3)",
            "[www.bbc.com\n\n。](http://www.bbc.com/news/2026/n)",
            "[www.bbc.com\n\n。](http://www.bbc.com/n/n%E3%80%82?lang=zh)",
            "[www.bbc.com\n\n。](http://www.bbc.com/n/n%E3%80%82#section)",
            "[www.bbc.com](http://www.bbc.com/n/n%E3%80%82/n3)",
            "路径 /n/n3 与字面量 \\n 保留。\n[URL](https://example.com/a/n?q=/n/n3)",
        ):
            with self.subTest(source=source):
                self.assertEqual(normalize_final_markdown(source), (source, []))

    def test_mismatched_host_punctuation_or_number_does_not_trigger_repair(self):
        for source in (
            SOURCE.replace("http://www.bbc.com", "http://other.example"),
            SOURCE.replace("%E3%80%82", "%EF%BC%81"),
            BAD_LINK.replace("/n3)", "/n4)") + "下一项",
            BAD_LINK.replace("www.bbc.com/n", "www.bbc.com:8080/n") + "下一项",
            BAD_LINK.replace("www.bbc.com/n", "user@www.bbc.com/n") + "下一项",
        ):
            with self.subTest(source=source):
                self.assertEqual(normalize_final_markdown(source), (source, []))

    def test_displaced_number_needs_visible_list_delimiter_and_following_text(self):
        for source in (BAD_LINK.rstrip(), BAD_LINK.replace(").\n", ")\n") + "下一项"):
            self.assertEqual(normalize_final_markdown(source), (source, []))

    def test_whole_json_and_protocol_arguments_are_never_presentation_repaired(self):
        for value in ({"type": "final", "answer": SOURCE}, [SOURCE], {"type": "action", "tool": "files.write",
                      "arguments": {"path": "raw.md", "content": SOURCE}, "summary": "保留源文本"}):
            source = json.dumps(value, ensure_ascii=False, indent=2)
            self.assertEqual(normalize_final_markdown(source), (source, []))
        action = {"type": "action", "tool": "files.write", "arguments": {"path": "raw.md", "content": SOURCE},
                  "summary": "保留源文本"}
        self.assertEqual(parse_reply(json.dumps(action, ensure_ascii=False)), action)

    def test_fenced_indented_inline_and_html_code_remain_exact(self):
        for source in ("```text\n" + SOURCE + "\n```", "~~~~text\n" + SOURCE + "\n~~~~",
                       "\n".join("    " + row for row in SOURCE.splitlines()),
                       "示例 ``" + SOURCE + "``。", "<pre>" + SOURCE + "</pre>",
                       "<code>" + SOURCE + "</code>"):
            with self.subTest(source=source[:30]):
                self.assertEqual(normalize_final_markdown(source), (source, []))

    def test_code_block_in_same_document_is_preserved_while_prose_is_repaired(self):
        code = "```text\n" + SOURCE + "\n```"
        cleaned, changes = normalize_final_markdown(code + "\n\n" + SOURCE)
        self.assertTrue(cleaned.startswith(code + "\n\n"))
        self.assertEqual(cleaned.count(BAD_URL), 1)
        self.assertEqual(len(changes), 2)

    def test_images_and_escaped_brackets_are_not_citations(self):
        for prefix in ("!", "\\"):
            source = prefix + BAD_LINK + "下一项"
            self.assertEqual(normalize_final_markdown(source), (source, []))

    def test_no_global_chinese_line_join_or_literal_slash_n_decoding(self):
        source = "诗句\n   ，保留换行\n市\n场\n\n/n/n。/n3\\n"
        self.assertEqual(normalize_final_markdown(source), (source, []))

    def test_runtime_keeps_raw_transcript_and_audit_but_saves_formatted_markdown(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            runtime = Runtime(ScriptedClient([final(SOURCE)]), Registry(), run,
                              event=lambda name, fields: events.append((name, fields)))
            result = runtime.run("整理给定新闻摘录的显示格式")
            self.assertEqual(result["status"], "completed")
            cleaned = normalize_final_markdown(SOURCE)[0]
            self.assertEqual(result["answer"], cleaned)
            self.assertEqual((run / "final.md").read_text(), cleaned + "\n")
            transcript = json.loads((run / "transcript.json").read_text())
            self.assertIn(SOURCE, [json.loads(row["content"]).get("answer") for row in transcript
                                   if row.get("role") == "assistant"])
            normalized_event = next(fields for name, fields in events if name == "result.normalized")
            self.assertEqual(normalized_event["payload"]["raw_answer"], SOURCE)
            self.assertEqual(normalized_event["payload"]["answer"], cleaned)


if __name__ == "__main__":
    unittest.main()
