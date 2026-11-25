"""End-to-end offline regression: the full cohort run must reproduce the frozen golden scores.

If you change the scoring, the anchors, or the extraction ON PURPOSE, regenerate the golden file with
    python tests/make_golden.py
and record why in CHANGELOG.md."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import _env  # noqa: F401
from test_framework import FakeJudgeBrain

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).resolve().parent / "golden" / "master_scores_golden.csv"


def run_pipeline(out: Path):
    return subprocess.run([sys.executable, "run_agents.py", "--offline", "--quiet", "--out-dir", str(out)],
                          cwd=ROOT, capture_output=True, text=True, timeout=900)


class GoldenPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="esg_golden_"))
        cls.proc = run_pipeline(cls.out)

    def test_all_agents_completed(self):
        rep = json.loads((self.out / "agent_run_report.json").read_text(encoding="utf-8"))
        bad = [a["agent_id"] for a in rep["agents"] if a["status"] != "completed"]
        self.assertEqual(bad, [], self.proc.stdout[-800:] + self.proc.stderr[-800:])

    def test_scores_match_golden(self):
        got = pd.read_csv(self.out / "esg_master_scores.csv").set_index("Company_Code")
        gold = pd.read_csv(GOLDEN).set_index("Company_Code")
        self.assertEqual(sorted(got.index), sorted(gold.index))
        for code, row in gold.iterrows():
            self.assertAlmostEqual(got.loc[code, "Master_ESG_Score"], row["Master_ESG_Score"], places=1, msg=code)
            self.assertEqual(got.loc[code, "Quality_Gate_Status"], row["Quality_Gate_Status"], code)

    def test_no_default_values_and_gate_is_honest(self):
        m = pd.read_csv(self.out / "esg_master_scores.csv")
        pass_rows = m[m["Quality_Gate_Status"] == "Pass"]
        self.assertTrue((pass_rows["Usable_Metrics"] >= 12).all())
        self.assertTrue((pass_rows[["Metrics_E", "Metrics_S", "Metrics_G"]] >= 3).all().all())
        withheld = m[m["Quality_Gate_Status"] == "Insufficient"]
        self.assertTrue(withheld["Master_ESG_Score"].isna().all())

    def test_context_is_not_in_the_score(self):
        m = pd.read_csv(self.out / "esg_master_scores.csv")
        self.assertIn("Context_Note", m.columns)
        a = json.loads((ROOT / "config" / "anchors.json").read_text(encoding="utf-8"))
        self.assertTrue(all(k in a["excluded_from_score"] for k in ("Air quality (AQI)", "Stock-market performance", "News sentiment")))

    def test_every_metric_row_is_traceable(self):
        b = pd.read_csv(self.out / "brsr_extracted_metrics.csv")
        found = b[b["Metric_Value"].notna()]
        self.assertTrue(found["XBRL_Concept"].notna().all())
        self.assertTrue(found["Final_Source"].isin([
            "XBRL_EXACT", "XBRL_DERIVED", "XBRL_RESCALED", "XBRL_RESCALED_PDF",
            "XBRL_RESCALED_LLM", "MANUAL_PDF",
        ]).all())

    def test_validation_files(self):
        v = json.loads((self.out / "validation_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(v["analyses"]["sensitivity"]["status"], "ok")
        self.assertEqual(v["analyses"]["nse_top200"]["status"], "ok")
        self.assertIn("interpretations", v)
        # statistics are reported only with an adequate sample
        self.assertTrue(str(v["analyses"]["esg_stock"]["status"]).startswith(("insufficient", "ok")))

    def test_explanations_pass_grounding(self):
        e = pd.read_csv(self.out / "multi_agent_explanations.csv")
        self.assertTrue((e["Grounding_Check"] == "pass").all())


class Agent8Verifiers(unittest.TestCase):
    def test_interpretation_rejects_causal_wording_and_invented_numbers(self):
        from esg_agents.agent8_validation_research import ValidationResearchAgent
        res = {"status": "ok", "n": 46, "spearman": 0.31}
        causal = {"interpretation": "ESG score drives returns (rho 0.31, n 46)."}
        invented = {"interpretation": "A correlation of 0.77 was found."}
        fine = {"interpretation": "Weak positive association (rho 0.31, n 46); not causation."}
        a = ValidationResearchAgent(verbose=False, brain=FakeJudgeBrain([causal, invented]))
        a.memory["results"] = {"x": res}
        a.t_interpret()
        self.assertEqual(a.memory["interpretations"]["x"]["source"], "rule_fallback")
        b = ValidationResearchAgent(verbose=False, brain=FakeJudgeBrain([fine]))
        b.memory["results"] = {"x": res}
        b.t_interpret()
        self.assertEqual(b.memory["interpretations"]["x"]["source"], "llm")

    def test_corr_report_requires_sample(self):
        from esg_agents.agent8_validation_research import corr_report
        self.assertIn("insufficient", corr_report([1, 2, 3], [3, 2, 1])["status"])


if __name__ == "__main__":
    unittest.main()
