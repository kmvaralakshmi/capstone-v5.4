"""Exact-concept XBRL extraction, plausibility audit and PDF evidence (golden regression values)."""
import glob
import unittest
from pathlib import Path

import _env  # noqa: F401
from esg_agents.common import ROOT, TARGET_PERIOD
from esg_agents.engine import audit as au
from esg_agents.engine.anchors import load_anchors, metric_score, polarity
from esg_agents.engine.metrics import METRIC_UNITS, extract_all
from esg_agents.engine.pdf import load_pages, unit_hint, verify_value
from esg_agents.engine.xbrl import XbrlFiling, truthy

XBRL = ROOT / "data" / "downloads" / "xbrl"


class GoldenExtraction(unittest.TestCase):
    """Infosys FY2024-25: these values are the regression baseline for the extraction engine."""
    @classmethod
    def setUpClass(cls):
        cls.f = XbrlFiling(XBRL / "INFY_BRSR_2024_25.xml")
        cls.r = extract_all(cls.f)

    def test_period(self):
        self.assertEqual(self.f.period(), TARGET_PERIOD)

    def test_golden_values(self):
        gold = {"Total Employees": 323578.0, "Renewable Energy Percentage": 68.871, "Energy Intensity": 730.061,
                "GHG Intensity": 0.2904, "Water Intensity": 6.0434, "Waste Recovered Percentage": 96.630,
                "Female Employees Percentage": 39.042, "Employee Turnover Rate": 14.1, "Female Directors Percentage": 22.23,
                "Board Size": 9.0, "Anti-Corruption Policy": 1.0, "Human Rights Training Coverage": 98.6}
        for name, v in gold.items():
            self.assertAlmostEqual(self.r[name].value, v, places=2 if v < 1000 else 0, msg=name)

    def test_every_value_is_traceable(self):
        for name, e in self.r.items():
            if e.found:
                self.assertTrue(e.concept, f"{name} has no concept")
                self.assertTrue(e.context, f"{name} has no context")

    def test_missing_concept_is_none_not_default(self):
        f = XbrlFiling(XBRL / "INFY_BRSR_2024_25.xml")
        self.assertIsNone(f.get("ThisConceptDoesNotExist"))

    def test_truthy(self):
        self.assertEqual((truthy("Yes"), truthy("No"), truthy(""), truthy(None)), (True, False, None, None))


class CohortCoverage(unittest.TestCase):
    def test_every_filing_is_the_target_year_and_governance_is_covered(self):
        files = sorted(XBRL.glob("*.xml"))
        self.assertGreaterEqual(len(files), 40)
        anchors = load_anchors()["scored_metrics"]
        weak = []
        for p in files:
            f = XbrlFiling(p)
            self.assertEqual(f.period(), TARGET_PERIOD, p.name)
            r = extract_all(f)
            g = sum(1 for n, s in anchors.items() if s["pillar"] == "Governance" and r[n].found)
            if g < 3:
                weak.append(p.name)
        self.assertEqual(weak, [], "governance coverage (3+ metrics) must hold for every filing")


class AuditTests(unittest.TestCase):
    def test_plausibility_bounds(self):
        self.assertFalse(au.plausible("Renewable Energy Percentage", 140)[0])
        self.assertFalse(au.plausible("GHG Intensity", 3.0e7)[0])
        self.assertTrue(au.plausible("Energy Intensity", 730)[0])
        self.assertTrue(au.plausible("Energy Intensity", 2.6e6)[0], "asset-heavy firms must not be flagged")

    def test_revenue_entered_in_crore_is_detected_and_rescaled(self):
        f = XbrlFiling(XBRL / "TATAELXSI_BRSR_2024_25.xml")
        raw = extract_all(f)
        self.assertFalse(au.plausible("GHG Intensity", raw["GHG Intensity"].value)[0])
        self.assertIn(1e7, au.revenue_scale_candidates(f.get("RevenueFromOperations").value, raw["Total Employees"].value))
        fixed = extract_all(f, {"revenue": 1e7})
        self.assertTrue(au.plausible("GHG Intensity", fixed["GHG Intensity"].value)[0])

    def test_anchor_scoring_directions(self):
        a = load_anchors()
        self.assertEqual(metric_score("Renewable Energy Percentage", 60, a), 10)
        self.assertEqual(metric_score("Employee Turnover Rate", 40, a), 0)
        self.assertEqual(metric_score("Board and KMP Disciplinary Actions", 0, a), 10)
        self.assertAlmostEqual(metric_score("GHG Intensity", 4.47, a), 5.0, delta=0.05)   # log scale: sqrt(200*0.1)=4.47
        self.assertEqual(metric_score("GHG Intensity", 0.0, a), 10)
        self.assertEqual(polarity("Employee Turnover Rate", a), "lower")
        self.assertEqual(polarity("Female Employees Percentage", a), "higher")

    def test_every_scored_metric_is_defined(self):
        for name in load_anchors()["scored_metrics"]:
            self.assertIn(name, METRIC_UNITS)


class PdfEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hit = sorted((ROOT / "data" / "downloads" / "brsr").glob("INFY_BR_*.pdf"))
        cls.pages = load_pages(hit[0], ROOT / "data" / "cache" / "pdf_text") if hit else []

    def test_value_found_with_page(self):
        if not self.pages:
            self.skipTest("no Infosys PDF")
        ev = verify_value(self.pages, 323578.0, [r"permanent employees"])
        self.assertEqual(ev.status, "MATCH")
        self.assertIsNotNone(ev.page)

    def test_absent_value_is_not_found(self):
        if not self.pages:
            self.skipTest("no Infosys PDF")
        self.assertIn(verify_value(self.pages, 123456789.123, [r"permanent employees"]).status, ("NOT_FOUND", "MISMATCH"))

    def test_unit_hint(self):
        pages = ["Total energy consumed (A+B+C) (in GJ)  4,000", "other"]
        self.assertEqual(unit_hint(pages, [r"total energy consumed"], "energy"), "GJ")
        self.assertIsNone(unit_hint(["nothing relevant"], [r"total energy consumed"], "energy"))


if __name__ == "__main__":
    unittest.main()
