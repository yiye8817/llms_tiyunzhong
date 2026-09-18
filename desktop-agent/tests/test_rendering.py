import io
import json
import os
import unittest
import unicodedata
from unittest.mock import patch

from fusion_agent.rendering import format_json, normalize_final_markdown, render_markdown, safe_terminal_text


class TTYStream(io.StringIO):
    def isatty(self):
        return True


def cell_width(value):
    return sum(0 if unicodedata.combining(char) or unicodedata.category(char) in {"Mn", "Me", "Cf"}
               else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in value)


class RenderingTests(unittest.TestCase):
    def render(self, text, width=60, stream=None):
        stream = stream if stream is not None else io.StringIO()
        render_markdown(text, stream, width)
        return stream.getvalue()

    def test_attachment_double_encoded_markdown_is_formatted(self):
        source = r"# 文件报告\n\n## 文件列表\n\n| 名称 | 大小 |\n| --- | --- |\n| `a.txt` | 5B |\n\n## 说明\n\n普通文件。"
        normalized, changes = normalize_final_markdown(source)
        self.assertNotIn(r"\n", normalized)
        self.assertIn("\n| 名称 | 大小 |\n", normalized)
        self.assertEqual(changes[0]["kind"], "escaped_markdown_line_breaks")
        rendered = self.render(source)
        self.assertIn("文件报告\n────────", rendered)
        self.assertIn("┼", rendered)
        self.assertNotIn(r"\n", rendered)
        self.assertEqual(normalize_final_markdown(normalized), (normalized, []))

    def test_encoded_markdown_preserves_inline_code_and_windows_paths(self):
        source = r"# 报告\n\n命令 `printf '%s\n' x`，路径 `C:\new\notes`。\n\n## 数据\n\n原值 C:\new\notes。"
        normalized, changes = normalize_final_markdown(source)
        self.assertTrue(changes)
        self.assertIn(r"`printf '%s\n' x`", normalized)
        self.assertEqual(normalized.count(r"C:\new\notes"), 2)

    def test_presentation_normalization_does_not_decode_json_code_or_prose_escapes(self):
        originals = [json.dumps({"command": "printf '%s\\n' x", "text": "# A\\n\\nB"}),
                     r"```python\nprint('a\n\nb')\n```",
                     "# 正常报告\n\n保留 `\\n`。", r"例子：\n\n 表示两个换行。",
                     r"# 报告\\n\\n原始双反斜杠", r"# 一个标记\n普通文本中的单次转义"]
        for source in originals:
            with self.subTest(source=source):
                self.assertEqual(normalize_final_markdown(source), (source, []))

    def test_non_tty_headings_emphasis_lists_links_and_quote_are_readable(self):
        result = self.render("# 任务结果\n\n**完成**了 _检查_。\n\n- 首项\n  - 子项\n1. 有序\n"
                             "- [x] 已验证\n- [ ] 待确认\n\n> 引用\n\n[报告](https://example.com/report)")
        self.assertIn("任务结果\n────", result)
        self.assertIn("完成了 检查。", result)
        self.assertIn("• 首项", result)
        self.assertIn("  • 子项", result)
        self.assertIn("1. 有序", result)
        self.assertIn("☑ 已验证", result)
        self.assertIn("☐ 待确认", result)
        self.assertIn("│ 引用", result)
        self.assertIn("报告 (https://example.com/report)", result)
        self.assertNotIn("\x1b", result)
        self.assertNotIn("**", result)

    def test_code_blocks_preserve_commands_and_do_not_interpret_markdown(self):
        command = "printf '%s\\n' '**literal** $(touch /tmp/not-executed)' | sed 's/x/y/'"
        result = self.render(f"### 执行示例\n\n```bash\n{command}\n  # keep indentation\n```", width=24)
        self.assertIn(command + "\n  # keep indentation\n", result)
        self.assertIn("[bash]", result)
        self.assertNotIn("```", result)

    def test_long_fence_can_contain_shorter_backticks(self):
        self.assertIn("```\n", self.render("````text\n```\n**as is**\n````"))

    def test_json_response_is_pretty_and_valid_with_chinese(self):
        payload = {"结果": "完成", "文件": ["甲.txt", "乙.txt"], "内容": "第一行\n第二行"}
        result = self.render(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(json.loads(result), payload)
        self.assertIn('  "结果": "完成",', result)
        self.assertIn('    "甲.txt",', result)

    def test_json_example_inside_markdown_is_not_extracted(self):
        result = self.render('这是一个例子：\n{"type":"action"}\n仍然保留正文。')
        self.assertIn("这是一个例子：", result)
        self.assertIn("仍然保留正文。", result)

    def test_json_fence_keeps_original_string_and_valid_format(self):
        source = '{\n  "command": "echo **literal**",\n  "plan": ["甲", "乙"]\n}'
        result = self.render("```json\n" + source + "\n```")
        self.assertIn(source, result)

    def test_table_wraps_cjk_cells_to_terminal_width(self):
        result = self.render("| 文件名称 | 检查结论 |\n| --- | --- |\n"
                             "| 很长很长的中文名称.txt | **成功**，已经完成并核验文件存在 |\n"
                             "| second | 待确认 |", width=32)
        self.assertIn("文件名称", result)
        self.assertIn("检查结论", result)
        self.assertNotIn("**", result)
        self.assertIn("┼", result)
        for line in result.splitlines():
            self.assertLessEqual(cell_width(line), 32, line)

    def test_table_supports_escaped_pipe_and_inline_code_pipe(self):
        result = self.render("| 类型 | 内容 |\n| --- | --- |\n| A | `x|y` |\n| B | x\\|y |")
        self.assertEqual(result.count("x|y"), 2)

    def test_too_wide_table_uses_readable_label_value_rows(self):
        result = self.render("| 名称 | 状态 | 地址 |\n| --- | --- | --- |\n| A | 成功 | /tmp/a |", width=16)
        self.assertIn("名称: A", result)
        self.assertIn("状态: 成功", result)
        for line in result.splitlines():
            self.assertLessEqual(cell_width(line), 16)

    def test_chinese_and_combining_characters_wrap_by_terminal_cells(self):
        result = self.render("这是一段包含中文的很长说明，用来检查终端换行。 cafe\u0301 cafe\u0301 " * 4, width=24)
        for line in result.splitlines():
            self.assertLessEqual(cell_width(line), 24, line)
        self.assertIn("cafe\u0301", result)

    def test_tty_only_styles_generated_headings(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            result = self.render("# 完成\n普通正文", stream=TTYStream())
        self.assertIn("\x1b[1m完成\x1b[0m", result)
        self.assertIn("普通正文", result)

    def test_no_color_including_empty_value_and_dumb_disable_ansi(self):
        for environment in ({"NO_COLOR": "", "TERM": "xterm"}, {"TERM": "dumb"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                result = self.render("# 结果", stream=TTYStream())
                self.assertNotIn("\x1b", result)

    def test_terminal_controls_are_removed_including_osc_clipboard_and_links(self):
        source = ("before\x1b[2Jafter\x1b]52;c;SECRET\x07ok"
                  "\x1b]8;;https://evil.example\x1b\\label\x1b]8;;\x1b\\"
                  "\x1bPSECRET-DCS\x1b\\\x9b31mred\x9d52;c;C1SECRET\x9c"
                  "\x00\x08\x07end")
        result = self.render(source)
        self.assertIn("beforeafteroklabelredend", result)
        for forbidden in ("\x1b", "\x9b", "SECRET", "evil.example", "\x00", "\x08", "\x07"):
            self.assertNotIn(forbidden, result)

    def test_unterminated_controls_do_not_leak_payload(self):
        for sequence in ("\x1b]52;c;SECRET", "\x1bPSECRET", "\x9dSECRET", "\x1b[9999"):
            self.assertEqual(safe_terminal_text("safe" + sequence), "safe")

    def test_html_entities_cannot_inject_ansi_after_sanitization(self):
        result = self.render("safe &#27;[2J next &#x1b;]52;c;SECRET&#7; done")
        self.assertNotIn("\x1b", result)
        self.assertNotIn("SECRET", result)
        self.assertIn("safe", result)

    def test_inline_code_keeps_markdown_literals_and_underscored_names(self):
        result = self.render("检查 `**literal**`，文件 foo_bar_baz，以及 **完成**。")
        self.assertIn("**literal**", result)
        self.assertIn("foo_bar_baz", result)
        self.assertIn("以及 完成", result)

    def test_multibacktick_inline_code_and_emphasis_around_code(self):
        result = self.render("a ``one`two`` b；**a `b` c**")
        self.assertIn("a one`two b；a b c", result)

    def test_oversized_html_entity_is_displayed_without_crashing(self):
        result = self.render("&#" + "9" * 5000 + ";")
        self.assertIn("&#", result)
        self.assertNotIn("\x1b", result)

    def test_excessively_nested_json_falls_back_to_readable_text(self):
        source = "[" * 1500 + "0" + "]" * 1500
        result = self.render(source, width=40)
        self.assertEqual(result.replace("\n", ""), source)
        for line in result.splitlines():
            self.assertLessEqual(cell_width(line), 40)

    def test_json_duplicate_keys_are_not_silently_dropped(self):
        source = '{"result":"first","result":"second"}'
        self.assertEqual(self.render(source).strip(), source)
        nested = '{"data":{"name":"甲","name":"乙"}}'
        self.assertEqual(self.render(nested).strip(), nested)

    def test_carriage_returns_and_bidi_controls_cannot_overwrite_or_disguise_status(self):
        result = safe_terminal_text("before\rafter\r\nnext\u202eevil\u202c")
        self.assertEqual(result, "before\nafter\nnextevil")

    def test_format_json_escapes_control_characters_without_loss(self):
        value = {"command": "echo \x1b[2J", "url": "https://example.com/a"}
        result = format_json(value)
        self.assertNotIn("\x1b", result)
        self.assertEqual(json.loads(result), value)

    def test_flush_and_trailing_newline(self):
        class FlushStream(io.StringIO):
            flushed = False
            def flush(self):
                self.flushed = True
                super().flush()
        stream = FlushStream()
        self.assertIsNone(render_markdown("完成", stream))
        self.assertTrue(stream.flushed)
        self.assertEqual(stream.getvalue(), "完成\n")

    def test_empty_input_writes_no_spurious_ansi_or_whitespace(self):
        self.assertEqual(self.render(" \n\t\n"), "")


if __name__ == "__main__":
    unittest.main()
