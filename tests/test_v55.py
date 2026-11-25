import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
os.environ["LLM_PROVIDER"] = "none"


class V55NewsTests(unittest.TestCase):
    def test_false_positive_headlines_are_not_esg(self):
        from esg_agents.agent3_news_sentiment import BUSINESS_NOISE_RE, ESG_FACT_RE, MARKET_NOISE_RE
        headlines = [
            "Clients adjusting to uncertainty, discretionary spend reviving only in pockets: Wipro Chairman Rishad Premji",
            "Global environment 'more stable' but not yet fully settled, says Infosys CEO Salil Parekh",
            "MSCI August rejig: Swiggy, Mazagon Dock among likely additions",
            "Stocks of IREDA, Tata Tech, 4 other F&O entrants extend losses for 2nd day",
        ]
        for headline in headlines:
            self.assertFalse(ESG_FACT_RE.search(headline) and not (BUSINESS_NOISE_RE.search(headline) or MARKET_NOISE_RE.search(headline)), headline)
            self.assertTrue(BUSINESS_NOISE_RE.search(headline) or MARKET_NOISE_RE.search(headline), headline)

    def test_specific_effluent_headline_is_esg(self):
        from esg_agents.agent3_news_sentiment import ESG_FACT_RE
        self.assertTrue(ESG_FACT_RE.search("Wipro fined by pollution control board over effluent discharge"))

    def test_tcs_news_batch_uses_url_context_and_keeps_commercial_noise_out(self):
        import pandas as pd
        from unittest.mock import patch
        from test_framework import FakeJudgeBrain
        from esg_agents.agent3_news_sentiment import NewsSentimentAgent

        articles = [
            {"Company_Code": "TCS",
             "Title": "TCS goIT brings teachers back for foundational student skills development",
             "URL": "https://finanznachrichten.de/tcs-goit-student-skills.htm",
             "Domain": "finanznachrichten.de", "Description": "", "Date": "2026-09-29",
             "evidence": "title"},
            {"Company_Code": "TCS",
             "Title": "TCS commits $7.4B to a one-gigawatt AI campus in Hyderabad",
             "URL": ("https://thenextweb.com/news/tcs-hypervault-one-gigawatt-hyderabad-campus-"
                     "7-4-billion-water-neutral-claim-eu-data-centre-reporting-delegated-regulation-2024-1364"),
             "Domain": "thenextweb.com", "Description": "", "Date": "2026-09-05",
             "evidence": "title"},
            {"Company_Code": "TCS",
             "Title": "TCS launches Industrial Intelligence Foundry to boost AI-led engineering",
             "URL": "https://example.test/tcs-ai-foundry",
             "Domain": "example.test", "Description": "", "Date": "2026-10-16",
             "evidence": "title"},
        ]
        from esg_agents.agent3_news_sentiment import _url_context
        keys = [
            f"TCS|{row['Date']}|{i}|{row['Title'][:80]}"
            for i, row in enumerate(articles[:2])
        ]
        answers = {"items": [
            {"key": keys[0], "relevant": True, "company_is_subject": True, "pillar": "Social",
             "polarity": "positive", "nature": "company_announcement",
             "evidence_quote": articles[0]["Title"],
             "rationale": "A named education and student-skills program has social beneficiaries."},
            {"key": keys[1], "relevant": True, "company_is_subject": True, "pillar": "Environmental",
             "polarity": "positive", "nature": "company_announcement",
             "evidence_quote": "water neutral claim eu data centre reporting delegated regulation",
             "rationale": "The source URL identifies a water-neutrality claim and data-centre reporting."},
        ]}
        brain = FakeJudgeBrain([answers])
        agent = NewsSentimentAgent(companies=["TCS"], verbose=True, brain=brain)
        agent.memory["attributed"] = pd.DataFrame(articles)
        with patch.dict(os.environ, {"NEWS_ESG_DEBUG": "1"}), patch.object(agent, "log") as log:
            result = agent.t_score()

        self.assertEqual((result["candidates"], result["esg_relevant"], result["rejected_not_esg"]), (3, 2, 1))
        self.assertEqual(len(agent.judgements), 1)
        kept = agent.memory["scored"].set_index("Domain")
        self.assertEqual(kept.loc["finanznachrichten.de", "ESG_Category"], "Social")
        self.assertEqual(kept.loc["thenextweb.com", "Evidence_Source"], "url_path")
        self.assertIn("water neutral", kept.loc["thenextweb.com", "URL_Path_Context"])
        rejected = agent.memory["rejected"].iloc[0]
        self.assertIn("commercial or market headline", rejected["Rejection_Reason"])
        self.assertEqual(log.call_count, 3)
        self.assertIn("water neutral", _url_context(articles[1]["URL"]))


class V55AuditTests(unittest.TestCase):
    def test_absurd_totals_are_not_plausible(self):
        from esg_agents.engine.audit import plausible
        self.assertFalse(plausible("Scope 1 and 2 Emissions", 66_000_000_000)[0])
        self.assertFalse(plausible("Total Water Consumption", 1.8e12)[0])
        self.assertFalse(plausible("Total Waste Generated", 303_720_000)[0])

    def test_exact_zero_totals_are_not_reported(self):
        from esg_agents.engine.audit import plausible
        for metric in ("Total Water Consumption", "Total Waste Generated", "Scope 1 and 2 Emissions"):
            ok, reason = plausible(metric, 0)
            self.assertFalse(ok)
            self.assertIn("NOT_REPORTED", reason)

    def test_totals_have_hard_plausibility_bounds(self):
        from esg_agents.engine.audit import plausible
        cases = [
            ("Total Energy Consumption", 5000.01),
            ("Total Water Consumption", 100000001),
            ("Total Waste Generated", 1000001),
            ("Scope 1 and 2 Emissions", 5000001),
        ]
        for metric, value in cases:
            with self.subTest(metric=metric):
                self.assertFalse(plausible(metric, value)[0])

    def test_sis_pdf_ratio_repairs_totals_and_rejects_bad_llm_scales(self):
        from esg_agents.agent1_brsr_extraction import BRSRExtractionAgent
        from esg_agents.engine import audit

        agent = BRSRExtractionAgent(companies=["SIS"], verbose=False)
        self.assertTrue(agent.t_read("SIS")["ok"])
        self.assertTrue(agent.t_verify("SIS")["ok"])
        agent.t_audit("SIS")
        rows = agent.memory["rows"]["SIS"]

        expected = {
            "Total Energy Consumption": 17.2660166667,
            "Scope 1 and 2 Emissions": 6630.56,
            "Total Water Consumption": 179070.13,
        }
        for metric, value in expected.items():
            with self.subTest(metric=metric):
                row = rows[metric]
                self.assertAlmostEqual(row["Metric_Value"], value, places=5)
                self.assertEqual(row["Validation_Status"], "REPAIRED")
                self.assertEqual(row["Final_Source"], "XBRL_RESCALED_PDF")
                self.assertEqual(row["Confidence_Score"], 0.90)
                self.assertTrue(row["PDF_Page"])
                self.assertTrue(row["PDF_Evidence"])

        proposal_agent = BRSRExtractionAgent(companies=["SIS"], verbose=False, dynamic=True)
        self.assertTrue(proposal_agent.t_read("SIS")["ok"])
        self.assertTrue(proposal_agent.t_verify("SIS")["ok"])
        rows = proposal_agent.memory["rows"]["SIS"]

        wrong_scales = {"energy": 1e-9, "ghg": 1e-6, "water": 1e-5}
        metric_for_quantity = {
            "energy": "Total Energy Consumption",
            "ghg": "Scope 1 and 2 Emissions",
            "water": "Total Water Consumption",
        }

        def fake_judge(task, instructions, evidence, verify, fallback):
            quantity = evidence["quantity"]
            ok, _, reason = verify({"choice": wrong_scales[quantity], "reason": "bad proposal"}, evidence)
            self.assertFalse(ok, reason)
            return {**fallback(evidence), "_source": "rule_fallback", "_reason": reason}

        proposal_agent.judge = fake_judge
        for quantity, metric in metric_for_quantity.items():
            pdf_value = audit.pdf_metric_value(proposal_agent._pages["SIS"], metric)
            self.assertIsNotNone(pdf_value)
            base_value = rows[metric]["Metric_Value"]
            correct_scale = audit.power_of_ten_scale(base_value, pdf_value.value)
            self.assertIsNotNone(correct_scale)
            proposal_agent.memory["issues"]["SIS"] = [{
                "quantity": quantity, "metric": metric, "reason": "test",
                "candidates": [wrong_scales[quantity], correct_scale], "base_metric": metric,
            }]
            result = proposal_agent.t_resolve("SIS", quantity)
            self.assertTrue(result["ok"])
            self.assertAlmostEqual(result["decision"], correct_scale)


class V55VerdictTests(unittest.TestCase):
    def _agent_with_claims(self, order, answers=None, brain=None):
        from esg_agents.agent5_greenwashing import GreenwashingAgent

        agent = GreenwashingAgent(companies=order, verbose=False, brain=brain)
        claim = {"id": "C1", "type": "reported_improvement", "metric": "Energy Intensity",
                 "text": "Improved Energy Intensity", "comparable": True}
        evidence = [
            {"id": "E1", "kind": "trend_improved", "text": "energy intensity improved"},
            {"id": "E2", "kind": "news_negative_allegation", "text": "an unverified allegation"},
        ]
        agent.memory["claims"] = {code: [dict(claim)] for code in order}
        agent._evidence = lambda code, item: [dict(entry) for entry in evidence]
        if answers is not None:
            answer_queue = list(answers)

            def fake_judge(task, instructions, package, verify, fallback):
                answer = answer_queue.pop(0)
                ok, cleaned, reason = verify(answer, package)
                if not ok:
                    return {**fallback(package), "_source": "rule_fallback", "_reason": f"rejected: {reason}"}
                return {**cleaned, "_source": "llm", "_reason": "accepted"}

            agent.judge = fake_judge
        return agent

    def test_rule_verdict_controls_claims_and_trust_independent_of_order(self):
        import pandas as pd

        orders = (["TCS", "INFY"], ["INFY", "TCS"])
        answers = [
            {"verdict": "consistent", "cited_evidence": ["E1"], "confidence": 0.6, "rationale": "LLM says consistent"},
            {"verdict": "potential_inconsistency", "cited_evidence": ["E2"], "confidence": 0.6, "rationale": "LLM sees allegation"},
        ]
        results = []
        for order, run_answers in zip(orders, (answers, list(reversed(answers)))):
            agent = self._agent_with_claims(order, run_answers)
            for code in order:
                result = agent.t_investigate(code, "C1")
                self.assertEqual(result["verdict"], "potential_inconsistency")
            for index, code in enumerate(order):
                row = agent.memory["verdicts"][code][0]
                self.assertEqual(row["verdict"], "potential_inconsistency")
                self.assertEqual(row["agreement"], run_answers[index]["verdict"] == "potential_inconsistency")
                self.assertAlmostEqual(row["confidence"], 0.6)
                self.assertAlmostEqual(row["llm_confidence"], 0.6)
                self.assertEqual(row["llm_verdict"], run_answers[index]["verdict"])
                self.assertTrue(row["llm_cited"])
                evidence_ids = {entry["id"] for entry in row["evidence"]}
                self.assertLessEqual(set(row["cited"]), evidence_ids)
                self.assertLessEqual(set(row["llm_cited"]), evidence_ids)

            brsr = pd.DataFrame([
                {"Company_Code": code, "Metric_Name": f"metric-{index}", "Scored": True,
                 "Metric_Value": 50.0, "PDF_Status": "", "Validation_Status": "VALID"}
                for code in order for index in range(6)
            ])
            empty = pd.DataFrame()
            agent.memory["data"] = {"brsr": brsr, "news": empty, "env": empty, "mkt": empty,
                                    "news_art": empty}
            agent.memory["base"] = {}
            for code in order:
                agent.t_score(code)
            results.append({code: agent.memory["results"][code]["trust"] for code in order})
        self.assertEqual(results[0], results[1])

    def test_high_confidence_consistency_skips_llm(self):
        agent = self._agent_with_claims(["TCS"])
        evidence = [
            {"id": "E1", "kind": "trend_improved", "text": "trend"},
            {"id": "E2", "kind": "pdf_corroborated", "text": "PDF"},
            {"id": "E3", "kind": "news_positive", "text": "news"},
        ]
        agent._evidence = lambda code, claim: evidence
        agent.judge = lambda *args: self.fail("high-confidence consistent claim must not be sent to the LLM")
        result = agent.t_investigate("TCS", "C1")
        self.assertEqual(result["verdict"], "consistent")
        row = agent.memory["verdicts"]["TCS"][0]
        self.assertEqual(row["confidence"], 0.8)
        self.assertEqual(row["source"], "rule")
        self.assertIsNone(row["llm_verdict"])

    def test_tcs_claim_export_contains_both_verdict_paths_and_agreement(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import pandas as pd

        answer = {"verdict": "consistent", "cited_evidence": ["E1"],
                  "confidence": 0.6, "rationale": "Supported by the trend."}
        agent = self._agent_with_claims(["TCS"], [answer])
        agent.t_investigate("TCS", "C1")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("esg_agents.agent5_greenwashing.PROCESSED", Path(tmp)):
                agent.t_finalize()
            row = pd.read_csv(Path(tmp) / "greenwashing_claims.csv").iloc[0]
        self.assertIn(row["Verdict"], ("consistent", "potential_inconsistency", "insufficient_evidence"))
        self.assertEqual(row["LLM_Verdict"], "consistent")
        self.assertIn(row["Verdict_Agreement"], (True, False))
        self.assertTrue(row["Cited_Evidence"])
        self.assertEqual(row["LLM_Cited_Evidence"], "E1")

    def test_claim_judgements_are_round_robin_and_rule_fallback_explains_rejection(self):
        from test_framework import FakeJudgeBrain

        agent = self._agent_with_claims(["TCS", "INFY"])
        agent.memory["data"] = {}
        agent.memory["claims"]["TCS"].append({"id": "C2", "type": "report_statement", "text": "claim"})
        first = agent.policy(agent.observe())
        self.assertEqual(first.args["company_code"], "TCS")
        agent.memory["verdicts"]["TCS"] = [{"claim": {"id": "C1"}}]
        next_claim = agent.policy(agent.observe())
        self.assertEqual(next_claim.args["company_code"], "INFY")

        bad = {"verdict": "potential_inconsistency", "cited_evidence": ["E1"],
               "confidence": 0.5, "rationale": "unsupported"}
        fallback_agent = self._agent_with_claims(["TCS"], brain=FakeJudgeBrain([bad, bad]))
        fallback_agent._evidence = lambda code, claim: [{"id": "E1", "kind": "news_none", "text": "no news"}]
        fallback_agent.t_investigate("TCS", "C1")
        row = fallback_agent.memory["verdicts"]["TCS"][0]
        self.assertEqual(row["source"], "rule_fallback")
        self.assertIn("must cite contradicting evidence", row["rationale"])

    def test_multi_claim_judgements_batch_uncertain_claims_and_keep_rules_authoritative(self):
        from test_framework import FakeJudgeBrain

        claims = [
            {"id": "C1", "type": "reported_improvement", "metric": "Energy Intensity",
             "text": "Improved Energy Intensity", "comparable": True},
            {"id": "C2", "type": "reported_improvement", "metric": "Renewable Energy Percentage",
             "text": "Improved renewable energy", "comparable": True},
            {"id": "C3", "type": "omitted_deterioration", "metric": "Water Intensity",
             "text": "Unacknowledged deterioration in water intensity", "comparable": True},
        ]
        batch_answer = {"claims": [
            {"claim_id": "C1", "verdict": "consistent", "cited_evidence": ["E1"],
             "rationale": "The comparable trend supports the improvement."},
            {"claim_id": "C2", "verdict": "consistent", "cited_evidence": ["E1"],
             "rationale": "The comparable trend supports the improvement."},
        ]}
        agent = self._agent_with_claims(["TCS"], brain=FakeJudgeBrain([batch_answer]))
        agent.memory["claims"]["TCS"] = claims
        agent._evidence = lambda _code, claim: [{
            "id": "E1", "kind": "trend_worsened" if claim["id"] == "C3" else "trend_improved",
            "text": "the checked year-over-year trend",
        }]

        result = agent.t_investigate("TCS", "C1")
        rows = {row["claim"]["id"]: row for row in agent.memory["verdicts"]["TCS"]}
        self.assertEqual(result["source"], "llm")
        self.assertEqual(len(agent.judgements), 1)
        self.assertEqual(rows["C1"]["llm_verdict"], "consistent")
        self.assertEqual(rows["C2"]["llm_verdict"], "consistent")
        self.assertEqual(rows["C3"]["source"], "rule")
        self.assertEqual(rows["C3"]["verdict"], "potential_inconsistency")
        self.assertIn("contradicting evidence found", rows["C3"]["rationale"])
        self.assertEqual(agent.t_investigate("TCS", "C2")["source"], "llm")
        self.assertEqual(len(agent.judgements), 1)

    def test_large_claim_batch_is_split_to_bound_response_size(self):
        from test_framework import FakeJudgeBrain

        claims = [{"id": f"C{i}", "type": "reported_improvement", "metric": "Energy Intensity",
                   "text": f"Claim {i}", "comparable": True} for i in range(1, 8)]
        answers = []
        for batch_ids in (range(1, 6), range(6, 8)):
            answers.append({"claims": [
                {"claim_id": f"C{i}", "verdict": "consistent", "cited_evidence": ["E1"],
                 "rationale": "Comparable trend supports the claim."}
                for i in batch_ids
            ]})
        agent = self._agent_with_claims(["TCS"], brain=FakeJudgeBrain(answers))
        agent.memory["claims"]["TCS"] = claims
        agent._evidence = lambda _code, _claim: [
            {"id": "E1", "kind": "trend_improved", "text": "Comparable trend improved."}
        ]

        agent.t_investigate("TCS", "C1")

        self.assertEqual(len(agent.judgements), 2)
        self.assertEqual(len(agent.memory["verdicts"]["TCS"]), 7)
        self.assertTrue(all(row["source"] == "llm" for row in agent.memory["verdicts"]["TCS"]))


class V55ScoringAndMetricsTests(unittest.TestCase):
    def test_unverified_llm_repair_is_excluded_from_scoring(self):
        import pandas as pd
        from esg_agents.agent6_master_scoring import MasterScoringAgent

        agent = MasterScoringAgent(companies=["TCS"], verbose=False)
        empty = pd.DataFrame()
        agent.memory["data"] = {
            "brsr": pd.DataFrame([{
                "Company_Code": "TCS", "Metric_Name": "Renewable Energy Percentage",
                "Metric_Value": 42.0, "Confidence_Score": 0.9, "Unit": "%",
                "Final_Source": "XBRL_RESCALED_LLM", "Validation_Status": "REPAIRED",
                "Audit_Status": "UNVERIFIED_LLM", "Scored": True,
            }]),
            "gw": empty, "env": empty, "news": empty, "news_art": empty, "mkt": empty,
        }
        agent.t_score("TCS")
        self.assertNotIn("Renewable Energy Percentage", agent.memory["results"]["TCS"]["metric_scores"])

    def test_batch_llm_usage_counts_verdicts_narrations_and_budget_flags(self):
        import json
        import tempfile
        from pathlib import Path
        from esg_agents.supervisor import Supervisor

        with tempfile.TemporaryDirectory() as tmp:
            supervisor = Supervisor(agents=["agent5"], companies=["TCS"], use_llm=False, verbose=False)
            supervisor.tracker.out_dir = Path(tmp)
            (Path(tmp) / "run_metrics.json").write_text('{"summary": {}}', encoding="utf-8")
            (Path(tmp) / "greenwashing_claims.csv").write_text(
                "Judgement_Source\nllm\nrule\nrule_fallback\n", encoding="utf-8")
            (Path(tmp) / "multi_agent_explanations.csv").write_text(
                "Narrative_Source\nllm(test)\ndeterministic\ndeterministic\n", encoding="utf-8")
            supervisor.reports = [{
                "agent_id": "agent5", "judge_calls": 10, "judge_seconds": 80.0,
                "judge_budget": 10, "judge_time_budget_seconds": 60.0,
                "judge_budget_reached": True, "judge_time_budget_reached": True,
            }]
            supervisor._write_llm_usage()
            usage = json.loads((Path(tmp) / "run_metrics.json").read_text(encoding="utf-8"))["llm_usage"]
            self.assertEqual(usage["verdicts"], {"llm": 1, "rule": 1, "rule_fallback": 1, "total": 3})
            self.assertEqual(usage["narrations"], {"llm": 1, "template": 2, "total": 3})
            self.assertTrue(usage["judge_budget_reached"])
            self.assertTrue(usage["judge_time_budget_reached"])


class V55ProvenanceTests(unittest.TestCase):
    def test_empty_data_gov_key_uses_bundled_snapshot_even_with_cache(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from esg_agents.data_providers import fetch_cpcb_realtime
        from esg_agents.telemetry import RunTracker

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cpcb_realtime.json"
            cache.write_text(json.dumps([{"city": "wrong cached city"}]), encoding="utf-8")
            tracker = RunTracker("cpcb-provenance", out_dir=Path(tmp))
            with patch.dict(os.environ, {"DATA_GOV_IN_API_KEY": ""}), \
                    patch("esg_agents.data_providers.CPCB_CACHE_PATH", cache):
                result = fetch_cpcb_realtime(tracker)

            self.assertEqual(result.provenance["kind"], "bundled")
            self.assertEqual(result.provenance["note"], "DATA_GOV_IN_API_KEY not set: bundled CPCB snapshot")
            self.assertEqual(result.provenance["date_min"], "2025-05-19")
            self.assertEqual(result.provenance["date_max"], "2025-05-19")
            self.assertEqual(result.provenance["rows"], 3077)
            self.assertEqual(tracker.data_sources[0]["kind"], "bundled")
            metrics = json.loads((Path(tmp) / "run_metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(metrics["data_sources"][0]["source"], "CPCB realtime_aqi.csv")

    def test_cpcb_uses_configured_timeout_for_live_request(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, patch
        from esg_agents.data_providers import fetch_cpcb_realtime

        response = Mock()
        response.json.return_value = {"records": [{"city": "Mumbai"}]}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cpcb_realtime.json"
            with patch.dict(os.environ, {"DATA_GOV_IN_API_KEY": "test-key-not-sent",
                                         "DATA_GOV_IN_TIMEOUT_SECONDS": "7"}), \
                    patch("esg_agents.data_providers.CPCB_CACHE_PATH", cache), \
                    patch("requests.get", return_value=response) as get:
                result = fetch_cpcb_realtime(force=True)

        self.assertEqual(result.provenance["kind"], "live")
        self.assertEqual(get.call_args.kwargs["timeout"], 7.0)

    def test_gdelt_429_stops_retries_and_opens_circuit_after_two_failures(self):
        from unittest.mock import Mock, patch
        from esg_agents.data_providers import fetch_news, reset_gdelt_circuit

        reset_gdelt_circuit()
        responses = [Mock(status_code=429, headers={}, text="rate limited") for _ in range(4)]
        try:
            with patch("esg_agents.data_providers._cache_get", return_value=None), \
                    patch("esg_agents.data_providers._cache_get_stale", return_value=None), \
                    patch("esg_agents.data_providers.time.sleep"), \
                    patch("requests.get", side_effect=responses) as get:
                first = fetch_news("rate-limit-test-one", company_code="TST")
                second = fetch_news("rate-limit-test-two", company_code="TST")
                third = fetch_news("rate-limit-test-three", company_code="TST")

            self.assertFalse(first.ok)
            self.assertFalse(second.ok)
            self.assertIn("429", first.error)
            self.assertIn("circuit open", third.error)
            self.assertEqual(get.call_count, 4)
        finally:
            reset_gdelt_circuit()

    def test_offline_news_records_the_bundled_corpus_date_range(self):
        from esg_agents.agent3_news_sentiment import NewsSentimentAgent
        from esg_agents.telemetry import RunTracker

        tracker = RunTracker("news-provenance")
        agent = NewsSentimentAgent(companies=["TCS"], dynamic=False, tracker=tracker, verbose=False)
        result = agent.t_load()
        self.assertTrue(result["ok"])
        corpus = next(source for source in tracker.data_sources if source["key"] == "news:corpus")
        self.assertEqual(corpus["kind"], "static")
        self.assertEqual(corpus["date_min"], "2025-02-07")
        self.assertEqual(corpus["date_max"], "2025-08-21")
        self.assertEqual(corpus["rows"], 3348)

    def test_health_and_report_expose_data_source_records(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from backend import app as backend_app

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "esg_master_scores.csv").write_text(
                "Company_Code,Company_Name,Master_ESG_Score\nTCS,Tata Consultancy Services,80\n",
                encoding="utf-8")
            sources = [
                {"key": "prices:TCS", "category": "prices", "company_code": "TCS",
                 "source": "yfinance", "kind": "live", "as_of": "2026-11-18", "rows": 200, "note": ""},
                {"key": "air_quality:cpcb", "category": "air_quality", "source": "CPCB snapshot",
                 "kind": "bundled", "as_of": "2025-05-19", "rows": 3000, "note": "no key"},
                {"key": "prices:INFY", "category": "prices", "company_code": "INFY",
                 "source": "yfinance", "kind": "live", "as_of": "2026-11-18", "rows": 200, "note": ""},
            ]
            (folder / "run_metrics.json").write_text(json.dumps({"data_sources": sources}), encoding="utf-8")
            (folder / "agent_run_report.json").write_text(json.dumps({"llm_mode": False}), encoding="utf-8")
            folders = {"offline": folder, "dynamic": folder}
            with patch.dict(os.environ, {"LLM_PROVIDER": "none"}), \
                    patch("backend.app.batch_dir", side_effect=lambda mode: folders[backend_app.norm_mode(mode)]), \
                    patch("backend.app.list_runs", return_value={"runs": []}):
                health = backend_app.health()
            self.assertEqual(health["modes"]["offline"]["data_sources"], sources)

            with patch("backend.app.batch_dir", return_value=folder), \
                    patch("backend.app.resolve_folder", return_value=folder):
                report = backend_app.report(mode="offline", run="batch", company="TCS")
            self.assertEqual([x.get("company_code") for x in report["data_sources"]], ["TCS", None])

    def test_offline_market_fixture_is_static_not_a_fallback(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from esg_agents.data_providers import DataResult, fetch_market
        from esg_agents.telemetry import RunTracker

        tracker = RunTracker("static-market")
        payload = {"Window_End": "2025-03-31", "Data_Points": 60}
        with patch("esg_agents.data_providers._fetch_market_inner",
                   return_value=DataResult("fallback", "static price fixture/snapshot",
                                           payload, fallback_used=True, error="not used")):
            result = fetch_market("TCS.NS", dynamic=False, tracker=tracker, company_code="TCS")
        self.assertEqual(result.provenance["kind"], "static")
        self.assertFalse(result.fallback_used)
        self.assertEqual(tracker.summary()["fallbacks_used"], [])
        self.assertEqual(tracker.data_sources[0]["rows"], 60)

    def test_dynamic_banner_names_bundled_news_after_gdelt_failure(self):
        import json
        import shutil
        import subprocess
        from pathlib import Path

        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        records = [
            {"category": "prices", "source": "yfinance", "kind": "live",
             "status": "success", "as_of": "2026-11-18", "rows": 200},
            {"category": "news", "source": "GDELT", "kind": "live",
             "status": "failed", "note": "HTTP 503"},
            {"category": "news", "source": "bundled news corpus", "kind": "bundled",
             "status": "fallback", "note": "GDELT failed; bundled corpus used"},
            {"category": "air_quality", "source": "CPCB realtime_aqi.csv", "kind": "bundled",
             "status": "fallback", "as_of": "2025-05-19",
             "note": "DATA_GOV_IN_API_KEY not set: bundled CPCB snapshot"},
        ]
        root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require('fs');
const vm = require('vm');
const app = fs.readFileSync('frontend/app.js', 'utf8');
const source = app.slice(0, app.lastIndexOf('document.querySelectorAll("#modeSwitch button")'));
const context = {
  localStorage: { getItem: () => null },
  document: { querySelector: () => ({}), querySelectorAll: () => [] },
  window: { addEventListener: () => {} },
};
vm.createContext(context);
vm.runInContext(source, context);
console.log(context.modeBannerText('dynamic', RECORDS, true));
const emptyNews = RECORDS.map(x => x.category === 'news' && x.kind === 'live'
  ? {...x, status: 'success', rows: 0}
  : x.category === 'news' && x.kind === 'bundled'
    ? {...x, note: 'GDELT returned no articles; bundled corpus used'}
    : x);
console.log(context.modeBannerText('dynamic', emptyNews, true));
const failedFeeds = RECORDS.map(x => x.kind === 'live' ? {...x, status: 'failed', rows: 0} : x);
console.log(context.modeBannerText('dynamic', failedFeeds, true));
const diagnostics = {
  data_sources: [{
    key: 'news:corpus', category: 'news', kind: 'live', status: 'success',
    rows: 68, date_min: '2026-09-28', date_max: '2026-11-25'
  }],
  agents: {agent3: {history: [
    {tool: 'load_corpus', observation: JSON.stringify({articles: 68})},
    {tool: 'attribute_articles', observation: JSON.stringify({
      attributed_rows: 11, rejected_roundups: 13
    })},
    {tool: 'score_articles', observation: JSON.stringify({esg_relevant: 0})}
  ]}}
};
console.log(context.newsFetchSummary(diagnostics));
""".replace("RECORDS", json.dumps(records))
        completed = subprocess.run([node, "-e", script], cwd=root, capture_output=True, text=True, check=True)
        self.assertIn("news bundled corpus (GDELT failed)", completed.stdout)
        self.assertIn("news bundled corpus (GDELT returned no articles)", completed.stdout)
        self.assertIn("air quality bundled (no API key", completed.stdout)
        self.assertIn("LLM on", completed.stdout)
        self.assertIn("no live feeds succeeded", completed.stdout)
        self.assertIn("GDELT live returned 68 raw articles (2026-09-28 to 2026-11-25)", completed.stdout)
        self.assertIn("11 passed company attribution; 0 passed ESG relevance screening", completed.stdout)
        self.assertIn("13 roundup headlines were excluded", completed.stdout)


class V55PlannerTests(unittest.TestCase):
    def test_recover_rejects_out_of_scope_proposal_and_matches_off_score(self):
        import os
        import tempfile
        from unittest.mock import patch
        from esg_agents.agent6_master_scoring import compute_score
        from esg_agents.engine.anchors import load_anchors
        from esg_agents.framework import BaseAgent, Decision, LLMBrain, Tool

        class OutOfScopeBrain(LLMBrain):
            def __init__(self):
                super().__init__(enabled=True, client=object())

            def decide(self, system, state, history, tools):
                return Decision("score_company", {"company_code": "MSFT"}, "out of scope", "llm")

            def judge(self, system, prompt, max_chars=14000):
                return {"accepted": True}

        anchors = load_anchors()
        values = {name: {"value": spec["anchor_10"], "confidence": 1.0}
                  for name, spec in anchors["scored_metrics"].items()}
        expected_score = compute_score(values, 8.0, anchors)["master"]

        class PlannerScoreAgent(BaseAgent):
            agent_id, name = "planner_score_test", "Planner score test"

            def __init__(self, *args, **kwargs):
                self.codes = ["TCS"]
                self.order = []
                self.score = None
                super().__init__(*args, **kwargs)
                self.memory.update(finalized=False)

            def build_tools(self):
                return [
                    Tool("score_company", "Score one company", self.t_score,
                         {"company_code": {"type": "string"}}, ["company_code"]),
                    Tool("finalize", "Finish scoring", self.t_finalize),
                ]

            def t_score(self, company_code):
                self.order.append(("score_company", company_code))
                self.score = compute_score(values, 8.0, anchors)["master"]
                return {"ok": True, "score": self.score}

            def t_finalize(self):
                self.order.append(("finalize", ""))
                self.memory["finalized"] = True
                return {"ok": True}

            def observe(self):
                return {"scored": self.score is not None, "finalized": self.memory["finalized"]}

            def is_done(self, state):
                return (state["finalized"], "scored") if state["finalized"] else (False, "")

            def policy(self, state):
                if not state["scored"]:
                    return Decision("score_company", {"company_code": "TCS"})
                if not state["finalized"]:
                    return Decision("finalize")
                return None

        with tempfile.TemporaryDirectory() as off_dir, tempfile.TemporaryDirectory() as recover_dir:
            with patch.dict(os.environ, {"LLM_PLANNER": "off", "ESG_OUT_DIR": off_dir}):
                off = PlannerScoreAgent(brain=OutOfScopeBrain(), verbose=False)
                judgement = off.judge("test", "return accepted", {}, lambda ans, _: (ans.get("accepted"), ans, ""),
                                      lambda _: {"accepted": False})
                off_report = off.run()
            with patch.dict(os.environ, {"LLM_PLANNER": "recover", "ESG_OUT_DIR": recover_dir}):
                recover = PlannerScoreAgent(brain=OutOfScopeBrain(), verbose=False)
                recover_report = recover.run()

        self.assertEqual(judgement["_source"], "llm")
        self.assertEqual(off_report.planner_mode, "off")
        self.assertEqual(off_report.llm_planning_calls, 0)
        self.assertEqual(off_report.llm_judgements, 1)
        self.assertEqual(recover_report.status, "completed")
        self.assertEqual(recover_report.planner_mode, "recover")
        self.assertEqual(recover_report.decisions_by_source["guardrail"], 1)
        self.assertEqual(recover_report.llm_planning_calls, 1)
        self.assertEqual(recover_report.llm_planning_decisions, 0)
        self.assertEqual(recover_report.guardrail_rejections[0]["proposal"]["args"]["company_code"], "MSFT")
        self.assertIn("not part of this run", recover_report.guardrail_rejections[0]["reason"])
        self.assertEqual(off.order, recover.order)
        self.assertEqual(off.score, recover.score)
        self.assertEqual(recover.score, expected_score)

    def test_agent_two_never_uses_the_planner(self):
        import os
        from unittest.mock import patch
        from esg_agents.agent2_environmental import EnvironmentalAgent
        from esg_agents.framework import LLMBrain

        with patch.dict(os.environ, {"LLM_PLANNER": "on"}):
            agent = EnvironmentalAgent(companies=["TCS"], brain=LLMBrain(client=object()), verbose=False)
        self.assertEqual(agent.planner_mode, "off")
        self.assertFalse(agent._consult())


class V55PerformanceTests(unittest.TestCase):
    def test_stage_overhead_reconciles_end_to_end_and_small_gain_is_sequential(self):
        import shutil
        import subprocess
        import time
        from pathlib import Path
        from esg_agents.telemetry import RunTracker

        tracker = RunTracker("timing")
        with tracker.stage("acquisition"):
            with tracker.span("one agent", "acquisition", "agent", kind="agent"):
                time.sleep(0.02)
        summary = tracker.summary()
        self.assertAlmostEqual(summary["stage_wall_total_seconds"] + summary["overhead_seconds"],
                               summary["total_seconds"], delta=0.01)

        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require('fs');
const vm = require('vm');
const app = fs.readFileSync('frontend/app.js', 'utf8');
const source = app.slice(0, app.lastIndexOf('document.querySelectorAll("#modeSwitch button")'));
const view = { innerHTML: '' };
const context = {
  localStorage: { getItem: () => null },
  document: { querySelector: selector => selector === '#view' ? view : ({}), querySelectorAll: () => [] },
  window: { addEventListener: () => {} },
};
vm.createContext(context);
vm.runInContext(source, context);
vm.runInContext(`S.report = { run_metrics: { summary: {
  total_seconds: 10.1,
  stages: {
    acquisition: { stage_wall_seconds: 3.2, sum_seconds: 3.18 },
    scoring: { stage_wall_seconds: 4.1, sum_seconds: 4.1 },
  },
  fallbacks_used: [], failures: [],
}, spans: [] } }; pTiming();`, context);
console.log(view.innerHTML);
console.log(context.parallelGainLabel(1.02, 1.00));
"""
        completed = subprocess.run([node, "-e", script], cwd=root, capture_output=True, text=True, check=True)
        self.assertIn("Overhead (startup, scheduling, writing files)", completed.stdout)
        self.assertIn("2.80 s", completed.stdout)
        self.assertIn("10.10 s", completed.stdout)
        self.assertIn("sequential", completed.stdout)


class V55ValidationTests(unittest.TestCase):
    def test_tcs_pdf_and_xbrl_source_totals_reconcile(self):
        from esg_agents.engine.metrics import extract_all
        from esg_agents.engine.xbrl import XbrlFiling
        import csv

        filing = XbrlFiling("data/downloads/xbrl/TCS_BRSR_2024_25.xml")
        extracted = extract_all(filing)

        self.assertAlmostEqual(extracted["Total Energy Consumption"].value, 539.1463144444444, places=6)
        self.assertAlmostEqual(extracted["Total Water Consumption"].value, 2871784.0)
        self.assertAlmostEqual(extracted["Scope 1 and 2 Emissions"].value, 76093.8)
        self.assertAlmostEqual(extracted["Total Waste Generated"].value, 9983.0)
        self.assertAlmostEqual(extracted["Energy Intensity"].value, 886.7844357197279)
        self.assertAlmostEqual(extracted["Water Intensity"].value, 4.72349209429931)

        with open("config/manual_overrides.csv", newline="", encoding="utf-8") as f:
            rows = {r["Metric_Name"]: r for r in csv.DictReader(f) if r["Company_Code"] == "TCS"}
        self.assertAlmostEqual(float(rows["Energy Intensity"]["Value"]), 846.6111111111111)
        self.assertAlmostEqual(float(rows["Water Intensity"]["Value"]), 4.5)

    def test_nonsignificant_correlation_reading(self):
        import numpy as np
        from esg_agents.agent8_validation_research import corr_report
        rng = np.random.default_rng(12345)
        x = np.arange(30, dtype=float)
        y = rng.normal(size=30)
        r = corr_report(x, y)
        self.assertIn("no statistically significant association", r["reading"])

    def test_population_share_sentence_removed(self):
        from esg_agents.agent7_explainability import ACTIONS
        self.assertNotIn("2.2%", ACTIONS["Differently Abled Employees Percentage"])


if __name__ == "__main__":
    unittest.main()
