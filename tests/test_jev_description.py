import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "jev_description", Path(__file__).parents[1] / "sources/jev-fast-audit/description.py")
description = importlib.util.module_from_spec(spec)
spec.loader.exec_module(description)
strip_audit = description.strip_audit
upsert_audit = description.upsert_audit
START, END, HEADING = description.START, description.END, description.HEADING


def marked(content="Old result", newline="\n"):
    return newline.join([START, HEADING, "", content, END, ""])


class DescriptionTests(unittest.TestCase):
    def test_empty_body_and_summary(self):
        self.assertEqual(upsert_audit("", ""), START + "\n" + HEADING + "\n\n" + END + "\n")
        self.assertEqual(strip_audit(upsert_audit("", "")), "")

    def test_append_retains_all_original_bytes(self):
        for original in ["Human prose", "Human prose\n", "Human prose\n\n", "Human prose\n\n\n"]:
            with self.subTest(original=original):
                result = upsert_audit(original, "New result")
                self.assertTrue(result.startswith(original))
                self.assertEqual(result.count(HEADING), 1)
                self.assertEqual(upsert_audit(result, "New result"), result)

    def test_replaces_at_existing_position_without_touching_other_reviews(self):
        prefix = "# Change\nHuman text  \n\n"
        suffix = "\n## Full LLM review\n<details>\n<summary>Evidence</summary>\n\nKeep  exactly.\n</details>\n"
        body = prefix + marked() + suffix
        self.assertEqual(upsert_audit(body, "Fresh result"), prefix + marked("Fresh result") + suffix)
        self.assertEqual(strip_audit(body), prefix + suffix)

    def test_deduplicates_marked_and_legacy_sections(self):
        a = "Intro\n\n"
        b = "## Human review\nKeep me.\n"
        c = "# Next area\nAlso keep me.\n"
        body = a + marked() + b + "## Jev-Fast-Audit\nOld legacy result\n" + c + marked("Another old result")
        expected = a + marked("Fresh") + b + c
        self.assertEqual(upsert_audit(body, "Fresh"), expected)
        self.assertEqual(strip_audit(body), a + b + c)
        self.assertEqual(upsert_audit(expected, "Fresh"), expected)

    def test_legacy_h2_stops_at_h1_or_h2_but_keeps_own_subheadings(self):
        for boundary in ["# Other", "## Other"]:
            with self.subTest(boundary=boundary):
                prefix = "Intro\n"
                suffix = boundary + "\nKeep this prose.\n"
                body = prefix + "## Jev-Fast-Audit\nOld\n### Details\nAlso old\n" + suffix
                self.assertEqual(strip_audit(body), prefix + suffix)

    def test_similar_headings_are_not_owned(self):
        body = "# Jev-Fast-Audit\nTop level\n### Jev-Fast-Audit\nThird level\n## Jev-Fast-Audit history\nKeep\n"
        self.assertEqual(strip_audit(body), body)
        self.assertTrue(upsert_audit(body, "Fresh").startswith(body))

    def test_fenced_examples_are_preserved_with_backticks_and_tildes(self):
        for fence in ["```", "~~~~"]:
            with self.subTest(fence=fence):
                example = fence + "markdown\n" + marked() + "## Another example\n" + fence + "\n"
                body = "Intro\n" + example + "## Human\nKeep.\n"
                self.assertEqual(strip_audit(body), body)
                self.assertTrue(upsert_audit(body, "Fresh").startswith(body))

    def test_fenced_headings_do_not_end_legacy_section(self):
        body = "Before\n## Jev-Fast-Audit\nOld\n```diff\n## Not a boundary\n```\n## Human\nKeep\n"
        self.assertEqual(strip_audit(body), "Before\n## Human\nKeep\n")

    def test_longer_fence_not_closed_by_shorter_or_other_kind(self):
        body = "````markdown\n```\n" + marked() + "~~~\n````\nHuman\n"
        self.assertEqual(strip_audit(body), body)

    def test_crlf_preserved_and_new_summary_normalized(self):
        prefix = "# Change\r\nOriginal  \r\n\r\n"
        suffix = "\r\n## Other\r\nKeep\r\n"
        body = prefix + marked(newline="\r\n") + suffix
        result = upsert_audit(body, "One\nTwo\n")
        self.assertEqual(result, prefix + marked("One\r\nTwo", "\r\n") + suffix)
        self.assertEqual(strip_audit(result), prefix + suffix)
        self.assertEqual(upsert_audit(result, "One\nTwo"), result)

    def test_unbalanced_nested_or_malformed_markers_fail_closed(self):
        cases = [START, END, START + "\n" + START + "\n" + END,
                 START + "\n" + END + "\n" + END,
                 "inline " + START, "<!-- jev-fast-audit:start", "<!-- jev-fast-audit -->",
                 "<!-- jev-fast-audit:finish -->", START + "\n## Human review\nDo not swallow"]
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    strip_audit(body)
                with self.assertRaises(ValueError):
                    upsert_audit(body, "Fresh")

    def test_unclosed_fence_rejects_invisible_or_duplicate_append(self):
        with self.assertRaises(ValueError):
            upsert_audit("Human text\n```python\nprint(1)", "Fresh")
        with self.assertRaises(ValueError):
            upsert_audit("Human text", "```python\nprint(1)")

    def test_summary_cannot_inject_duplicate_owned_section(self):
        for summary in [HEADING + "\nAgain", START + "\n" + END, END]:
            with self.subTest(summary=summary), self.assertRaises(ValueError):
                upsert_audit("Human text", summary)


if __name__ == "__main__":
    unittest.main()
