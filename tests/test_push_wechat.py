import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "push_wechat.py"
SPEC = importlib.util.spec_from_file_location("push_wechat", SCRIPT)
push_wechat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(push_wechat)


class PushWechatTests(unittest.TestCase):
    def test_build_messages_includes_bilingual_role_viewpoints(self):
        item = {
            "title": "Headline",
            "source": "Source",
            "published_utc": "2026-08-08 00:00 UTC",
            "analysis_zh": "中文分析",
            "analysis_en": "English analysis",
            "viewpoints_zh": [{"role": "风险分析师", "viewpoint": "关注下行情景。"}],
            "viewpoints_en": [{"role": "Risk analyst", "viewpoint": "Watch downside scenarios."}],
            "link": "https://example.com/article",
        }
        payload = {"sections": {"投资": {"description": "市场新闻", "items": [item]}}}

        messages = push_wechat.build_messages(payload, "morning")

        self.assertEqual(len(messages), 1)
        body = messages[0][1]
        for expected in ("风险分析师", "关注下行情景。", "Risk analyst", "Watch downside scenarios."):
            self.assertIn(expected, body)

    def test_format_viewpoints_ignores_incomplete_entries(self):
        self.assertEqual(
            push_wechat.format_viewpoints([
                {"role": "Risk analyst", "viewpoint": "Check evidence."},
                {"role": "Missing viewpoint"},
                "invalid",
            ]),
            ["- **Risk analyst:** Check evidence."],
        )


if __name__ == "__main__":
    unittest.main()
