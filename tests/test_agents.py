"""Agent-level behaviour: each agent's judgement is verified by code, and every fallback is deterministic."""
import unittest

import _env  # noqa: F401
from test_framework import FakeJudgeBrain
from esg_agents.agent1_brsr_extraction import BRSRExtractionAgent
from esg_agents.agent2_environmental import EnvironmentalAgent, cpcb_sub_index
from esg_agents.agent3_news_sentiment import ROUNDUP_PAT, NewsSentimentAgent
from esg_agents.agent5_greenwashing import GreenwashingAgent
from esg_agents.agent6_master_scoring import compute_score, risk_band
from esg_agents.engine.anchors import load_anchors
from esg_agents.framework import Decision


def _a1(brain=None):
    a = BRSRExtractionAgent(verbose=False, companies=["TATAELXSI"], brain=brain) if brain else BRSRExtractionAgent(verbose=False, companies=["TATAELXSI"])
    a.t_read("TATAELXSI")
    a.t_verify("TATAELXSI")
    a.t_audit("TATAELXSI")
    return a


class Agent1Tests(unittest.TestCase):
    def test_audit_opens_a_revenue_scale_issue(self):
        a = _a1()
        self.assertTrue(any(i["quantity"] == "revenue" for i in a.memory["issues"]["TATAELXSI"]))

    def test_rule_fallback_never_guesses_between_far_apart_scales(self):
        a = _a1()
        out = a.t_resolve("TATAELXSI", "revenue")
        rows = a.memory["rows"]["TATAELXSI"]
        if out["decision"] == "quarantine":
            self.assertEqual(rows["GHG Intensity"]["Validation_Status"], "QUARANTINED")
        else:                                           # only allowed when the PDF states the unit or it is the sole candidate
            self.assertEqual(rows["GHG Intensity"]["Validation_Status"], "REPAIRED")
            self.assertTrue(rows["GHG Intensity"]["Needs_Manual_Review"])

    def test_llm_choice_outside_candidates_is_rejected(self):
        a = _a1(FakeJudgeBrain([{"choice": 12345.0, "reason": "x"}, {"choice": 777.0, "reason": "y"}]))
        out = a.t_resolve("TATAELXSI", "revenue")
        self.assertNotEqual(out["source"], "llm")

    def test_llm_valid_candidate_is_accepted(self):
        a = _a1()
        cand = a.memory["issues"]["TATAELXSI"][0]["candidates"][0]
        b = _a1(FakeJudgeBrain([{"choice": cand, "reason": "crore is the Indian reporting convention"}]))
        out = b.t_resolve("TATAELXSI", "revenue")
        self.assertEqual(out["source"], "llm")
        self.assertEqual(b.memory["scales"]["TATAELXSI"]["revenue"], cand)

    def test_audit_requires_a_read_first(self):
        a = BRSRExtractionAgent(verbose=False, companies=["TCS"])
        st = a.observe()
        self.assertIn("needs a verified", a.check_args(Decision("audit_company", {"company_code": "TCS"}), st))

    def test_unknown_company_rejected(self):
        with self.assertRaises(ValueError):
            BRSRExtractionAgent(verbose=False, companies=["NOTACODE"])


class Agent2Tests(unittest.TestCase):
    def test_cpcb_pm25(self):
        self.assertAlmostEqual(cpcb_sub_index("PM2.5", 60), 100, delta=1)

    def test_never_invents_aqi(self):
        a = EnvironmentalAgent(verbose=False, companies=["TCS"])
        a.t_load()
        out = a.t_resolve("Atlantis")
        self.assertEqual(out["method"], "unresolved")
        self.assertIsNone(out["aqi"])

    def test_headquarters_comes_from_the_filing(self):
        a = EnvironmentalAgent(verbose=False, companies=["AFFLE"])
        a.t_load()
        hq = a._locations("AFFLE")[0]
        self.assertEqual(hq["type"], "Headquarters")
        self.assertIn("XBRL", hq["evidence"])

    def test_out_of_scope_company_rejected(self):
        a = EnvironmentalAgent(verbose=False, companies=["TCS"])
        self.assertFalse(a.t_score("HCLTECH")["ok"])


class Agent3Tests(unittest.TestCase):
    def test_market_roundups_are_rejected_but_esg_news_kept(self):
        self.assertTrue(ROUNDUP_PAT.search("Stocks to watch today: Infosys, TCS"))
        self.assertTrue(ROUNDUP_PAT.search("Infosys shares jump 3% after Q4FY25 results"))
        self.assertFalse(ROUNDUP_PAT.search("Wipro launches renewable energy push at campuses"))

    def test_classification_needs_a_verbatim_quote(self):
        good = {"relevant": True, "company_is_subject": True, "pillar": "Environmental", "polarity": "positive",
                "nature": "company_announcement", "evidence_quote": "Wipro launches renewable energy push",
                "rationale": "The headline describes a specific renewable-energy initiative."}
        bad = dict(good, evidence_quote="a sentence the article never contained")
        a = NewsSentimentAgent(verbose=False, companies=["WIPRO"], brain=FakeJudgeBrain([bad, bad]))
        out = a._classify("WIPRO", "Wipro launches renewable energy push at campuses", "", {"ok": True, "compound": 0.4, "category": "Environmental"})
        self.assertEqual(out["_source"], "rule_fallback")
        a = NewsSentimentAgent(verbose=False, companies=["WIPRO"], brain=FakeJudgeBrain([good]))
        out = a._classify("WIPRO", "Wipro launches renewable energy push at campuses", "", {"ok": True, "compound": 0.4, "category": "Environmental"})
        self.assertEqual((out["_source"], out["pillar"]), ("llm", "Environmental"))

    def test_gdelt_failure_is_recorded_as_fallback(self):
        import esg_agents.agent3_news_sentiment as a3
        from esg_agents.data_providers import DataResult
        from esg_agents.telemetry import RunTracker
        orig = a3.fetch_news
        a3.fetch_news = lambda *x, **k: DataResult("failed", "GDELT", [], error="timeout")
        try:
            t = RunTracker("fb")
            ag = a3.NewsSentimentAgent(verbose=False, companies=["INFY"], dynamic=True, tracker=t)
            with t.stage("acq"):
                out = ag.t_load()
            self.assertNotEqual(out.get("source"), "GDELT")
            self.assertTrue(t.summary()["fallbacks_used"])
        finally:
            a3.fetch_news = orig


class Agent5Tests(unittest.TestCase):
    def _investigate(self, brain, evidence_kinds):
        a = GreenwashingAgent(verbose=False, companies=["INFY"], brain=brain)
        a.memory["data"] = {"brsr": __import__("pandas").DataFrame(), "trends": __import__("pandas").DataFrame(),
                            "news": __import__("pandas").DataFrame(), "news_summary": __import__("pandas").DataFrame()}
        claim = {"id": "C1", "type": "reported_improvement", "metric": None, "text": "net zero achieved", "comparable": True}
        a.memory["claims"]["INFY"] = [claim]
        a._evidence = lambda code, c: [{"id": f"E{i + 1}", "kind": k, "text": k} for i, k in enumerate(evidence_kinds)]
        return a, a.t_investigate("INFY", "C1")

    def test_inconsistency_needs_contradicting_evidence(self):
        bogus = {"verdict": "potential_inconsistency", "cited_evidence": ["E1"], "confidence": 0.5, "rationale": "x"}
        a, out = self._investigate(FakeJudgeBrain([bogus, bogus]), ["trend_improved", "news_none"])
        self.assertEqual(out["source"], "rule_fallback")
        self.assertEqual(out["verdict"], "consistent")

    def test_unknown_evidence_id_is_rejected(self):
        bogus = {"verdict": "consistent", "cited_evidence": ["E99"], "confidence": 0.4, "rationale": "x"}
        a, out = self._investigate(FakeJudgeBrain([bogus, bogus]), ["trend_improved"])
        self.assertEqual(out["source"], "rule_fallback")

    def test_valid_llm_verdict_is_accepted(self):
        ok = {"verdict": "potential_inconsistency", "cited_evidence": ["E1", "E2"], "confidence": 0.65, "rationale": "pdf and news disagree"}
        a, out = self._investigate(FakeJudgeBrain([ok]), ["news_negative_allegation", "trend_improved"])
        self.assertEqual((out["source"], out["verdict"]), ("llm", "potential_inconsistency"))

    def test_news_allegation_alone_is_low_confidence(self):
        a, out = self._investigate(None, ["news_negative_allegation"])
        v = a.memory["verdicts"]["INFY"][0]
        self.assertEqual(v["verdict"], "potential_inconsistency")
        self.assertEqual(v["confidence"], 0.6)

    def test_no_evidence_is_insufficient_not_guessed(self):
        a, out = self._investigate(None, ["news_none"])
        self.assertEqual(out["verdict"], "insufficient_evidence")


class ScoringTests(unittest.TestCase):
    A = load_anchors()

    def _vals(self, **over):
        sm = self.A["scored_metrics"]
        v = {n: {"value": ((s["anchor_0"] * s["anchor_10"]) ** 0.5 if s.get("scale") == "log" else (s["anchor_0"] + s["anchor_10"]) / 2)}
             for n, s in sm.items()}
        for k, x in over.items():
            v[k.replace("_", " ")] = {"value": x}
        return v

    def test_context_signals_are_not_inputs(self):
        # compute_score takes only BRSR metrics and trust: AQI, news and market cannot enter the score
        import inspect
        self.assertEqual(list(inspect.signature(compute_score).parameters), ["values", "trust", "anchors"])
        self.assertIn("Air quality (AQI)", self.A["excluded_from_score"])

    def test_formula_worked_example(self):
        r = compute_score(self._vals(), None, self.A)
        for p in r["pillar_scores"].values():
            self.assertAlmostEqual(p, 5.0, places=6)           # (geometric) midpoint anchors score 5 everywhere
        self.assertAlmostEqual(r["master"], 50.0, places=4)

    def test_trust_adjusts_each_pillar(self):
        r = compute_score(self._vals(), 10.0, self.A)
        self.assertAlmostEqual(r["pillar_scores"]["Environmental"], 0.85 * 5 + 0.15 * 10, places=6)

    def test_missing_metrics_are_not_defaulted(self):
        v = self._vals()
        for n, s in self.A["scored_metrics"].items():
            if s["pillar"] == "Governance":
                v[n] = {"value": None}
        r = compute_score(v, None, self.A)
        self.assertIsNone(r["pillar_scores"]["Governance"])
        self.assertAlmostEqual(r["master"], 50.0, places=4)    # weights renormalised over the pillars that exist

    def test_quality_gate(self):
        self.assertEqual(compute_score(self._vals(), None, self.A)["gate"], "Pass")
        v = {n: {"value": None} for n in self.A["scored_metrics"]}
        self.assertEqual(compute_score(v, None, self.A)["gate"], "Insufficient")
        self.assertIsNone(compute_score(v, None, self.A)["master"])

    def test_risk_bands(self):
        self.assertEqual([risk_band(x, self.A) for x in (80, 60, 40, None)], ["Low", "Medium", "High", "Insufficient evidence"])


class Agent0Tests(unittest.TestCase):
    def test_unknown_symbol_is_registered_not_invented(self):
        import tempfile
        from pathlib import Path
        import esg_agents.agent0_filing_discovery as a0
        from esg_agents.common import COMPANIES
        a0.DYN_FILE = Path(tempfile.mkdtemp()) / "dyn.json"
        ag = a0.FilingDiscoveryAgent(verbose=False, companies=["ZZTESTCO"])
        out = ag.t_resolve("ZZTESTCO")
        self.assertEqual((out["ok"], out["match"]), (True, "registered_new"))
        COMPANIES.pop("ZZTESTCO", None)

    def test_exact_name_resolves(self):
        ag = __import__("esg_agents.agent0_filing_discovery", fromlist=["x"]).FilingDiscoveryAgent(verbose=False, companies=["Infosys"])
        self.assertEqual(ag.t_resolve("Infosys")["company_code"], "INFY")


if __name__ == "__main__":
    unittest.main()


class HygieneWeightTests(unittest.TestCase):
    def test_hygiene_metrics_count_at_lower_weight(self):
        from esg_agents.engine.anchors import metric_weight
        a = load_anchors()
        self.assertEqual(metric_weight("Anti-Corruption Policy", a), 0.25)
        self.assertEqual(metric_weight("Female Directors Percentage", a), 1.0)
        sm = a["scored_metrics"]
        v = {n: {"value": None} for n in sm}
        v["Female Directors Percentage"] = {"value": 0.0}          # score 0, weight 1
        v["Anti-Corruption Policy"] = {"value": 1.0}                # score 10, weight 0.25
        r = compute_score(v, None, a)
        self.assertAlmostEqual(r["pillar_scores"]["Governance"], 10 * 0.25 / 1.25, places=6)
