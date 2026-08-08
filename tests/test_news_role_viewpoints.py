import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_daily.py"
SPEC = importlib.util.spec_from_file_location("generate_daily_role_viewpoints", SCRIPT)
generate_daily = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_daily)


class NewsRoleViewpointTests(unittest.TestCase):
    def test_each_section_targets_twenty_items(self):
        self.assertEqual(generate_daily.TARGET_ITEMS_PER_SECTION, 20)

    def test_fallback_has_three_distinct_roles_in_both_languages(self):
        for language in ("zh", "en"):
            viewpoints = generate_daily.fallback_role_viewpoints("投资", language)
            self.assertEqual(len(viewpoints), 3)
            self.assertEqual(len({item["role"] for item in viewpoints}), 3)
            self.assertTrue(all(item["viewpoint"] for item in viewpoints))

    def test_no_api_analysis_includes_role_viewpoints(self):
        original_client = generate_daily.client
        generate_daily.client = None
        try:
            result = generate_daily.gpt_bilingual_analysis(
                "Headline", "Summary", "Body", "投资", "Source"
            )
        finally:
            generate_daily.client = original_client

        self.assertEqual(len(result["viewpoints_zh"]), 3)
        self.assertEqual(len(result["viewpoints_en"]), 3)


if __name__ == "__main__":
    unittest.main()
