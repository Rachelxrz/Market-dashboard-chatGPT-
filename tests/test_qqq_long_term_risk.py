import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_daily.py"
SPEC = importlib.util.spec_from_file_location("generate_daily", SCRIPT)
generate_daily = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generate_daily
SPEC.loader.exec_module(generate_daily)


def yahoo_payload(closes, start_year=2024):
    timestamps = []
    current = datetime(start_year, 1, 1, tzinfo=timezone.utc)
    for index in range(len(closes)):
        timestamps.append(int(current.timestamp()) + index * 86400)
    return {
        "chart": {
            "result": [{
                "timestamp": timestamps,
                "indicators": {"quote": [{"close": closes}]},
            }]
        }
    }


class QqqLongTermRiskTests(unittest.TestCase):
    def test_insufficient_data_is_not_risk(self):
        with patch.object(generate_daily, "fetch_yahoo_chart", return_value=yahoo_payload([100.0] * 30)):
            result = generate_daily.build_qqq_long_term_risk()
        self.assertEqual(result["status_level"], "insufficient")
        self.assertFalse(result["confirmed"])

    def test_weekly_signal_requires_falling_long_average(self):
        falling = [200.0 - index for index in range(50)]
        rising = [100.0 + index for index in range(50)]
        self.assertTrue(generate_daily.weekly_risk_signal(falling, len(falling)))
        self.assertFalse(generate_daily.weekly_risk_signal(rising, len(rising)))

    def test_completed_weekly_closes_excludes_current_week(self):
        current_week_day = generate_daily.NOW_UTC
        previous_week_day = current_week_day.replace(hour=20) - generate_daily.timedelta(days=7)
        timestamps = [int(previous_week_day.timestamp()), int(current_week_day.timestamp())]
        result = generate_daily.completed_weekly_closes(timestamps, [100.0, 90.0])
        self.assertEqual(result, [100.0])

    def test_persistent_downtrend_is_confirmed(self):
        closes = [600.0 - index * 0.8 for index in range(500)]
        with patch.object(generate_daily, "fetch_yahoo_chart", return_value=yahoo_payload(closes)):
            result = generate_daily.build_qqq_long_term_risk()
        self.assertEqual(result["status_level"], "confirmed")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["confirmation_weeks"], 2)

    def test_persistent_uptrend_is_normal(self):
        closes = [100.0 + index * 0.8 for index in range(500)]
        with patch.object(generate_daily, "fetch_yahoo_chart", return_value=yahoo_payload(closes)):
            result = generate_daily.build_qqq_long_term_risk()
        self.assertEqual(result["status_level"], "normal")
        self.assertFalse(result["confirmed"])

    def test_full_dashboard_contains_long_term_risk_card(self):
        payload = {
            "generated_at": "2026-07-30 22:40:02 UTC",
            "regime": "Neutral",
            "risk_score": 2,
            "summary_zh": "测试",
            "summary_en": "Test",
            "actions": {},
            "market_snapshot": {},
            "watchlist_monitor": [],
            "structure_monitor": {},
            "layer_summary": {},
            "qqq_long_term_risk": {
                "status_zh": "风险候选",
                "status_en": "Risk candidate",
                "confirmation_weeks": 1,
                "confirmation_weeks_required": 2,
                "explanation_zh": "尚未取得连续两个完整周确认。",
                "explanation_en": "Two completed confirmation weeks are not yet present.",
                "methodology_zh": "日线预警，周线确认。",
                "methodology_en": "Daily warning, weekly confirmation.",
            },
        }
        captured = {}

        def capture_html(content, *_args):
            captured["html"] = content

        with patch.object(generate_daily, "write_html_dual", side_effect=capture_html):
            generate_daily.write_monitor_html(payload)

        dashboard_html = captured["html"]
        self.assertIn("QQQ中长期风险", dashboard_html)
        self.assertIn("Risk candidate", dashboard_html)
        self.assertIn("1/2", dashboard_html)
        self.assertIn("不是买卖指令", dashboard_html)


if __name__ == "__main__":
    unittest.main()
