"""m60 建议2 落地测试：guard.overgeneralization_flags 反绝对化词表。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "liquid_loop"))

from liquid_loop.guard import overgeneralization_flags, validate_content  # noqa: E402


class TestOvergeneralizationFlags(unittest.TestCase):
    def test_plain_content_no_flags(self):
        self.assertEqual(overgeneralization_flags("今天写完了报告并提交"), [])

    def test_chinese_absolute(self):
        flags = overgeneralization_flags("这个系统总是崩溃，完全不可靠")
        terms = {f["term"] for f in flags}
        self.assertIn("总是", terms)
        self.assertIn("完全", terms)

    def test_english_absolute(self):
        flags = overgeneralization_flags("This always works, never fails")
        terms = {f["term"] for f in flags}
        self.assertIn("always", terms)
        self.assertIn("never", terms)

    def test_claim_words(self):
        flags = overgeneralization_flags("我是这个领域的专家，完美掌握所有细节")
        terms = {f["term"] for f in flags}
        self.assertIn("专家", terms)
        self.assertIn("完美", terms)
        self.assertIn("所有", terms)

    def test_empty_content(self):
        self.assertEqual(overgeneralization_flags(""), [])

    def test_deduplicate(self):
        flags = overgeneralization_flags("总是总是总是")
        self.assertEqual(len(flags), 1)

    def test_validate_content_unchanged(self):
        self.assertIsNone(validate_content("正常内容测试"))
        self.assertIsNotNone(validate_content("!!!"))


if __name__ == "__main__":
    unittest.main()
