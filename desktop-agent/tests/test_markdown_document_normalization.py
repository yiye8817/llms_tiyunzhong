"""Narrow files.write Markdown normalization for the uploaded Qwen reply."""

import unittest

from fusion_agent.rendering import normalize_markdown_document


ARTICLE_ONE = "https://techcrunch.com/2026/09/09/listen-labs/"
ARTICLE_TWO = "https://techcrunch.com/2026/09/09/openai-board/"
REPORTED_DOCUMENT = (
    r"# AI 新闻摘要\n\n来源: TechCrunch AI RSS\n\n"
    r"1. **Listen Labs funding news**\n - 链接: [" + ARTICLE_ONE
    + r"\n\n2](" + ARTICLE_ONE + r"/n/n2). **OpenAI board news**\n - 链接: ["
    + ARTICLE_TWO + r"\n](" + ARTICLE_TWO + r"/n)")


class MarkdownDocumentNormalizationTests(unittest.TestCase):
    def test_uploaded_shape_restores_breaks_link_and_displaced_number(self):
        normalized, changes = normalize_markdown_document(REPORTED_DOCUMENT, "ai_news.md")
        self.assertIn("# AI 新闻摘要\n\n来源: TechCrunch AI RSS", normalized)
        self.assertIn(f"[{ARTICLE_ONE}]({ARTICLE_ONE})\n\n2. **OpenAI board news**", normalized)
        self.assertTrue(normalized.endswith(f"[{ARTICLE_TWO}]({ARTICLE_TWO})"))
        self.assertEqual([item["kind"] for item in changes],
                         ["escaped_markdown_line_breaks", "polluted_full_url_link",
                          "polluted_full_url_link"])
        self.assertTrue(all(item["scope"] == "files.write_markdown_content" for item in changes))
        self.assertEqual(changes[1]["original_destination"], ARTICLE_ONE + "/n/n2")
        self.assertEqual(changes[1]["normalized_destination"], ARTICLE_ONE)
        self.assertEqual(changes[1]["destination_suffix_removed"], "/n/n2")
        self.assertEqual(changes[1]["displaced_list_number"], 2)
        self.assertEqual(changes[2]["destination_suffix_removed"], "/n")
        self.assertIsNone(changes[2]["displaced_list_number"])
        self.assertEqual(normalize_markdown_document(normalized, "ai_news.md"), (normalized, []))

    def test_terminal_single_break_requires_exact_url_suffix_and_document_end(self):
        damaged = f"来源: [{ARTICLE_TWO}\\n]({ARTICLE_TWO}/n)"
        normalized = f"来源: [{ARTICLE_TWO}]({ARTICLE_TWO})"
        result, changes = normalize_markdown_document(damaged, "report.markdown")
        self.assertEqual(result, normalized)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["destination_suffix_removed"], "/n")
        self.assertIsNone(changes[0]["displaced_list_number"])
        self.assertEqual(normalize_markdown_document(damaged + " \n", "report.md")[0],
                         normalized + " \n")

        for source in (
            damaged + " 后续正文",
            damaged.replace(ARTICLE_TWO + "/n)", ARTICLE_ONE + "/n)"),
            damaged.replace("/n)", "/n2)"),
            damaged.replace("/n)", "/n/n)"),
            f"普通文本 {ARTICLE_TWO}\\n",
        ):
            with self.subTest(source=source[-50:]):
                self.assertEqual(normalize_markdown_document(source, "report.md"), (source, []))

    def test_non_markdown_destinations_are_never_changed(self):
        for path in ("ai_news.txt", "ai_news.md.json", "README", None):
            with self.subTest(path=path):
                self.assertEqual(normalize_markdown_document(REPORTED_DOCUMENT, path),
                                 (REPORTED_DOCUMENT, []))

    def test_prefix_number_delimiter_and_following_text_must_all_match(self):
        damaged = f"[{ARTICLE_ONE}\n\n2]({ARTICLE_ONE}/n/n2). **next**"
        variants = (
            damaged.replace(ARTICLE_ONE + "/n/n2", ARTICLE_TWO + "/n/n2"),
            damaged.replace("/n/n2)", "/n/n3)"),
            damaged.replace("). **next**", ") **next**"),
            damaged.replace("). **next**", ").   "),
            f"[label\n\n2]({ARTICLE_ONE}/n/n2). **next**",
            "[https://user@example.com/a/\n\n2](https://user@example.com/a//n/n2). **next**",
            "[https://example.com/a/?q=x\n\n2](https://example.com/a/?q=x/n/n2). **next**",
        )
        for variant in variants:
            source = "前文\n" + variant
            with self.subTest(variant=variant):
                self.assertEqual(normalize_markdown_document(source, "report.markdown"), (source, []))

    def test_normal_urls_plain_text_and_code_are_untouched(self):
        damaged = f"[{ARTICLE_ONE}\n\n2]({ARTICLE_ONE}/n/n2). **next**"
        terminal = f"[{ARTICLE_TWO}\\n]({ARTICLE_TWO}/n)"
        samples = (
            f"[{ARTICLE_ONE}]({ARTICLE_ONE})",
            f"普通文本 {ARTICLE_ONE}/n/n2 和编号 2",
            "```markdown\n" + damaged + "\n```",
            "    " + damaged,
            "示例 `" + damaged + "`",
            "<code>" + damaged + "</code>",
            "!" + damaged,
            "\\" + damaged,
            "    " + terminal,
            "!" + terminal,
            "\\" + terminal,
        )
        for source in samples:
            with self.subTest(source=source[:35]):
                self.assertEqual(normalize_markdown_document(source, "report.md"), (source, []))


if __name__ == "__main__":
    unittest.main()
