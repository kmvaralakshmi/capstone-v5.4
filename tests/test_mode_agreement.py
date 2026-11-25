import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import _env  # noqa: F401
from esg_agents.data_providers import _fetch_market_inner, market_metrics
from tools.evaluate_mode_agreement import evaluate_mode_agreement


class SharedMarketWindowTests(unittest.TestCase):
    def test_dynamic_market_metrics_use_offline_ticker_window(self):
        dates = pd.date_range("2021-01-01", "2026-01-01", freq="D")
        closes = list(range(100, 100 + len(dates)))
        cached = {"dates": [str(date.date()) for date in dates], "close": closes}
        with patch("esg_agents.data_providers._static_window_end", return_value="2025-03-31"), \
                patch("esg_agents.data_providers._cache_get", return_value=cached):
            result = _fetch_market_inner("TEST.NS", True, [])
        self.assertEqual(result.payload["Window_End"], "2025-03-31")
        self.assertEqual(result.payload["Window_Policy"], "capped_to_offline_history")

    def test_market_metrics_report_latest_window_when_uncapped(self):
        dates = pd.date_range("2025-01-01", "2025-06-30", freq="D")
        metrics = market_metrics(pd.Series(range(1, len(dates) + 1), index=dates))
        self.assertEqual(metrics["Window_End"], "2025-06-30")
        self.assertEqual(metrics["Window_Policy"], "latest_available")


class ModeAgreementTests(unittest.TestCase):
    def test_score_and_market_window_agreement_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            offline, dynamic = root / "offline", root / "dynamic"
            offline.mkdir()
            dynamic.mkdir()
            for folder, scores, market in (
                (offline,
                 "Company_Code,Master_ESG_Score,Quality_Gate_Status\nTCS,80,Pass\nINFY,,Insufficient\nWIPRO,65,Partial\n",
                 "Company_Code,Window_End\nTCS,2025-03-31\nINFY,2025-03-31\n"),
                (dynamic,
                 "Company_Code,Master_ESG_Score,Quality_Gate_Status\nTCS,78,Pass\nINFY,,Insufficient\nWIPRO,70,Pass\n",
                 "Company_Code,Window_End\nTCS,2025-03-31\nINFY,2025-04-01\n"),
            ):
                (folder / "esg_master_scores.csv").write_text(scores, encoding="utf-8")
                (folder / "stock_esg_correlation.csv").write_text(market, encoding="utf-8")

            result = evaluate_mode_agreement(offline, dynamic)
            self.assertEqual(result["companies"]["overlap"], 3)
            self.assertEqual(result["companies"]["scored_both"], 2)
            self.assertEqual(result["companies"]["quality_gate_agreement"], 0.6667)
            self.assertEqual(result["score_agreement"]["mean_absolute_difference"], 3.5)
            self.assertEqual(result["market_windows"]["matched_companies"], 2)
            self.assertEqual(result["market_windows"]["different_window"], 1)
            self.assertFalse(result["market_windows"]["consistent"])


if __name__ == "__main__":
    unittest.main()
