import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_daily.py"
SPEC = importlib.util.spec_from_file_location("generate_daily_news_only", SCRIPT)
generate_daily = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generate_daily
SPEC.loader.exec_module(generate_daily)


class NewsOnlyOutputTests(unittest.TestCase):
    def test_cleanup_removes_current_and_historical_monitor_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            docs = Path(temp_dir) / "docs"
            history_day = docs / "history" / "2026-07-31"
            history_day.mkdir(parents=True)
            (docs / "monitor.json").write_text("{}", encoding="utf-8")
            (history_day / "monitor.json").write_text("{}", encoding="utf-8")
            (history_day / "index.html").write_text("monitor", encoding="utf-8")
            (history_day / "reading.html").write_text("news", encoding="utf-8")

            with patch.object(generate_daily, "DOCS_DIR", docs), patch.object(
                generate_daily, "HISTORY_DIR", docs / "history"
            ):
                generate_daily.cleanup_structure_outputs()

            self.assertFalse((docs / "monitor.json").exists())
            self.assertFalse((history_day / "monitor.json").exists())
            self.assertFalse((history_day / "index.html").exists())
            self.assertTrue((history_day / "reading.html").exists())

    def test_main_never_builds_structure_monitor(self):
        with patch.object(generate_daily, "cleanup_structure_outputs"), patch.object(
            generate_daily, "build_reading_payload", return_value={}
        ), patch.object(generate_daily, "write_reading_json"), patch.object(
            generate_daily, "write_reading_html"
        ), patch.object(generate_daily, "cleanup_old_history"), patch.object(
            generate_daily, "build_monitor_payload"
        ) as build_monitor:
            generate_daily.main()

        build_monitor.assert_not_called()

    def test_reading_html_is_written_as_site_homepage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            docs = Path(temp_dir) / "docs"
            history_day = docs / "history" / "2026-07-31"
            history_day.mkdir(parents=True)
            payload = {
                "generated_at": "2026-07-31 12:00:00 UTC",
                "window": {"hours": 24},
                "sections": {},
            }
            with patch.object(generate_daily, "DOCS_DIR", docs), patch.object(
                generate_daily, "TODAY_HISTORY_DIR", history_day
            ), patch.object(generate_daily, "HISTORY_DIR", docs / "history"):
                generate_daily.write_reading_html(payload)

            homepage = (docs / "index.html").read_text(encoding="utf-8")
            self.assertEqual(homepage, (docs / "reading.html").read_text(encoding="utf-8"))
            self.assertNotIn("返回结构监控", homepage)
            self.assertTrue((history_day / "reading.html").exists())


if __name__ == "__main__":
    unittest.main()
