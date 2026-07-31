import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_daily.py"
SPEC = importlib.util.spec_from_file_location("generate_daily_news", SCRIPT)
generate_daily = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generate_daily
SPEC.loader.exec_module(generate_daily)


def item(title, link):
    return {
        "title": title,
        "link": link,
        "published_dt": datetime(2026, 7, 31, tzinfo=timezone.utc),
    }


class NewsDedupTests(unittest.TestCase):
    def test_tracking_query_does_not_make_url_unique(self):
        first = item("Market update", "https://www.example.com/story/?utm_source=rss")
        second = item("Market update revised", "https://example.com/story")
        self.assertEqual(
            generate_daily.duplicate_article_reason(second, [first]),
            "same_url",
        )

    def test_current_tesla_rewrite_is_same_story(self):
        first = item(
            "Tesla weighs sale of China business to pave way for potential SpaceX merger, WSJ reports",
            "https://example.com/one",
        )
        second = item(
            "Tesla considers sale of China business amid SpaceX merger speculation- WSJ",
            "https://example.com/two",
        )
        self.assertEqual(
            generate_daily.duplicate_article_reason(second, [first]),
            "same_story",
        )

    def test_related_but_distinct_headlines_are_kept(self):
        first = item(
            "Apple reports quarterly earnings as iPhone sales rise",
            "https://example.com/apple-earnings",
        )
        second = item(
            "Apple unveils new privacy controls for developers",
            "https://example.com/apple-privacy",
        )
        self.assertIsNone(generate_daily.duplicate_article_reason(second, [first]))

    def test_dedupe_keeps_newest_version(self):
        older = item("Same title", "https://example.com/old")
        newer = item("Same title", "https://example.com/new")
        older["published_dt"] = datetime(2026, 7, 30, tzinfo=timezone.utc)
        kept = generate_daily.dedupe_items([older, newer])
        self.assertEqual(kept, [newer])


if __name__ == "__main__":
    unittest.main()
