"""v5.4 tests: two separate modes, run-history filtering, rank/rounding/trend/news fixes, narration errors."""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LLM_PROVIDER"] = "none"


class RoundingAndLabels(unittest.TestCase):
    def test_half_up_matches_dashboard(self):
        from esg_agents.agent7_explainability import fmt
        self.assertEqual(fmt(86.25, 1), "86.3")        # python's format() printed 86.2
        self.assertEqual(fmt(0.5, 0), "1")
        self.assertEqual(fmt(73.24, 1), "73.2")

    def test_news_guard_rejects_business_announcements(self):
        from esg_agents.agent3_news_sentiment import ESG_FACT_RE, BUSINESS_NOISE_RE
        for t in ["TCS launches Industrial Intelligence Foundry to boost AI - led engine",
                  "TCS renews Jaguar Formula E deal with AI partnership",
                  "TCS HyperVault To Invest INR 70 ,000 Crore In 1GW AI Data Center Campus"]:
            self.assertTrue(BUSINESS_NOISE_RE.search(t) and not ESG_FACT_RE.search(t), t)
        for t in ["TCS fined by SEBI over audit lapses", "Infosys cuts emissions with renewable power"]:
            self.assertTrue(ESG_FACT_RE.search(t), t)

    def test_llm_write_failure_is_reported_not_fatal(self):
        from esg_agents.framework import LLMBrain

        def boom(**kw):
            raise TimeoutError("Request timed out.")
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=boom)))
        b = LLMBrain(client=client)
        b.api_style = "chat"
        self.assertIsNone(b.write("s", "p"))
        self.assertIn("TimeoutError", b.last_write_error)
        self.assertTrue(b.available)                      # narration timeouts must not disable the LLM

    def test_agent7_explains_why_the_template_was_used(self):
        from esg_agents.agent7_explainability import ExplainabilityAgent
        from esg_agents.framework import LLMBrain
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            create=lambda **kw: (_ for _ in ()).throw(TimeoutError("Request timed out.")))))
        brain = LLMBrain(client=client)
        brain.api_style = "chat"
        a = ExplainabilityAgent(brain=brain, verbose=False, companies=["INFY"], dynamic=True)
        ev = {"company": "Infosys", "withheld": False, "weak_metrics": [],
              "scores": {"id": "S1", "master": 73.25, "risk_level": "Low", "rank": None, "evidence_quality": "Pass",
                         "usable_scored_metrics": 16},
              "metrics": [], "trends": [], "news": {"available": False}, "market": {"available": False},
              "air_quality": {"available": False}, "consistency": {"level": "Low", "claims": []},
              "limitations": {"id": "L1", "items": []}}
        a.memory["evidence"]["INFY"] = ev
        out = a.t_explain("INFY")
        self.assertTrue(out["ok"])
        res = a.memory["results"]["INFY"]
        self.assertIn("73.3/100", res["Score_Explanation"])           # half-up
        self.assertNotIn("rank", res["Score_Explanation"])            # no rank in a single-company run
        self.assertIn("TimeoutError", res["LLM_Note"])

    def test_agent7_compacts_narration_context_and_verifies_against_that_context(self):
        from esg_agents.agent7_explainability import ExplainabilityAgent, verify_grounding

        evidence = {
            "company": "TCS", "code": "TCS", "scale": "0-100", "withheld": False,
            "scores": {"id": "S1", "master": 80.2, "environmental": 80.27},
            "metrics": [{"id": f"M{i}", "metric": f"metric-{i}", "value": float(i),
                         "score_0_100": float(i * 10)} for i in range(1, 11)],
            "trends": [{"id": f"T{i}", "metric": f"trend-{i}", "change": float(i),
                        "direction": "worsening" if i <= 5 else "improving"} for i in range(1, 11)],
            "news": {"id": "N1", "available": False}, "market": {"id": "K1", "available": False},
            "air_quality": {"id": "A1", "available": False},
            "consistency": {"id": "G1", "level": "Low", "signals": [], "claims": [
                {"claim": f"claim-{i}", "verdict": "consistent", "evidence": "detail"} for i in range(5)
            ]},
            "limitations": {"id": "L1", "items": []}, "weak_metrics": [],
        }
        compact = ExplainabilityAgent._narrative_package(evidence)
        self.assertEqual(len(compact["metrics"]), 6)
        self.assertEqual(len(compact["trends"]), 6)
        self.assertEqual(len(compact["consistency"]["claims"]), 3)
        self.assertTrue(verify_grounding("TCS scores 80.2 [S1].", compact)["ok"])
        self.assertIn("M5", verify_grounding("The metric changed [M5].", compact)["unknown_citations"])


class OfflineBatchOutputs(unittest.TestCase):
    """Checks on the generated offline batch (skipped until `python run_agents.py --mode offline` has run)."""
    def test_trend_display_modes(self):
        import pandas as pd
        f = ROOT / "processed-data" / "batch_offline" / "brsr_trends.csv"
        if not f.exists():
            self.skipTest("offline batch not generated yet")
        t = pd.read_csv(f)
        self.assertIn("Change_Mode", t.columns)
        self.assertTrue((t[t.Metric_Name == "Lost Time Injury Frequency Rate"].Change_Mode == "abs").all())
        self.assertTrue((t[t.Metric_Name == "Renewable Energy Percentage"].Change_Mode == "pp").all())

    def test_rank_and_scored_total(self):
        import pandas as pd
        f = ROOT / "processed-data" / "batch_offline" / "esg_master_scores.csv"
        if not f.exists():
            self.skipTest("offline batch not generated yet")
        df = pd.read_csv(f)
        self.assertEqual(int(df["Scored_Metrics_Total"].iloc[0]), 16)
        self.assertTrue(df["Rank"].notna().sum() >= 5)


class BackendModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import backend.app as app_mod
        cls.app_mod = app_mod
        cls.saved = (app_mod.BASE, app_mod.RUNS_ROOT, app_mod.JOBS_ROOT)
        cls.tmp = Path(tempfile.mkdtemp())
        app_mod.BASE = cls.tmp / "processed-data"
        app_mod.RUNS_ROOT = cls.tmp / "runs"
        app_mod.JOBS_ROOT = cls.tmp / "jobs"
        for mode, code, score in (("offline", "TCS", 80.0), ("dynamic", "TCS", 60.0)):
            b = app_mod.BASE / f"batch_{mode}"
            b.mkdir(parents=True)
            (b / "esg_master_scores.csv").write_text(
                "Company_Code,Company_Name,Master_ESG_Score,Rank\n" + f"{code},Tata,{score},1\n", encoding="utf-8")
        cls.client = TestClient(app_mod.app)

    @classmethod
    def tearDownClass(cls):
        cls.app_mod.BASE, cls.app_mod.RUNS_ROOT, cls.app_mod.JOBS_ROOT = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        shutil.rmtree(self.app_mod.RUNS_ROOT, ignore_errors=True)       # every test starts with no runs
        self.app_mod.JOBS.clear()

    def _make_run(self, mode, name, ok=True):
        d = self.app_mod.RUNS_ROOT / mode / name
        d.mkdir(parents=True)
        agents = [{"agent_id": "agent1", "status": "completed" if ok else "stalled"}]
        (d / "agent_run_report.json").write_text(json.dumps(
            {"agents": agents, "companies": ["TCS"], "finished": "2026-10-29T10:00:00",
             "timing": {"total_seconds": 5}, "llm": "none", "llm_mode": False}), encoding="utf-8")
        (d / "esg_master_scores.csv").write_text("Company_Code,Company_Name,Master_ESG_Score,Rank\nTCS,Tata,77.0,\n", encoding="utf-8")
        return d

    def test_modes_are_separate(self):
        off = self.client.get("/api/company/TCS?mode=offline").json()
        dyn = self.client.get("/api/company/TCS?mode=dynamic").json()
        self.assertEqual(off["scores"]["Master_ESG_Score"], 80.0)
        self.assertEqual(dyn["scores"]["Master_ESG_Score"], 60.0)
        self.assertEqual((off["mode"], dyn["mode"]), ("offline", "dynamic"))

    def test_missing_mode_data_is_a_clear_404(self):
        r = self.client.get("/api/company/INFY?mode=dynamic")
        self.assertEqual(r.status_code, 404)
        self.assertIn("dynamic-mode results", r.json()["detail"])

    def test_capabilities_follow_published_outputs_and_are_company_scoped(self):
        offline = self.app_mod.batch_dir("offline")
        scores_path = offline / "esg_master_scores.csv"
        market_path = offline / "stock_esg_correlation.csv"
        metrics_path = offline / "run_metrics.json"
        original_scores = scores_path.read_text(encoding="utf-8")
        self.addCleanup(lambda: scores_path.write_text(original_scores, encoding="utf-8"))
        self.addCleanup(lambda: market_path.unlink(missing_ok=True))
        self.addCleanup(lambda: metrics_path.unlink(missing_ok=True))
        initial = self.client.get("/api/cohort?mode=offline").json()["capabilities"]
        self.assertTrue(initial["dashboard"])
        self.assertFalse(initial["market"])
        self.assertFalse(initial["timing"])

        (offline / "stock_esg_correlation.csv").write_text(
            "Company_Code,Coverage,Data_Source\nTCS,price_data,bundled prices\nINFY,no_price_data,\n", encoding="utf-8")
        with scores_path.open("a", encoding="utf-8") as scores:
            scores.write("INFY,Infosys,70,2\n")
        (offline / "run_metrics.json").write_text('{"summary": {}}', encoding="utf-8")
        batch_caps = self.client.get("/api/cohort?mode=offline").json()["capabilities"]
        report_caps = self.client.get("/api/report?mode=offline&run=batch&company=TCS").json()["capabilities"]
        no_market_caps = self.client.get("/api/report?mode=offline&run=batch&company=INFY").json()["capabilities"]
        self.assertTrue(batch_caps["market"])
        self.assertTrue(report_caps["market"])
        self.assertFalse(no_market_caps["market"])
        self.assertTrue(report_caps["timing"])

    def test_unfinished_and_failed_runs_are_hidden_and_cleanable(self):
        self._make_run("offline", "TCS_20261029_100000", ok=True)
        self._make_run("offline", "TCS_20261029_110000", ok=False)           # failed (agent stalled)
        (self.app_mod.RUNS_ROOT / "offline" / "TCS_20261029_120000").mkdir(parents=True)   # unfinished: no report
        r = self.client.get("/api/runs?mode=offline").json()
        self.assertEqual([x["run"] for x in r["runs"]], ["TCS_20261029_100000"])
        self.assertEqual(r["hidden_unfinished_or_failed"], 2)
        self.assertEqual(self.client.get("/api/runs?mode=dynamic").json()["runs"], [])    # other mode untouched
        self.assertEqual(self.client.post("/api/runs/cleanup?mode=offline").json()["removed"], 2)
        self.assertEqual(self.client.get("/api/runs?mode=offline").json()["hidden_unfinished_or_failed"], 0)

    def test_company_lookup_prefers_latest_finished_run_of_same_mode(self):
        self._make_run("dynamic", "TCS_20261029_090000")
        got = self.client.get("/api/company/TCS?mode=dynamic").json()
        self.assertEqual(got["run"], "TCS_20261029_090000")
        off = self.client.get("/api/company/TCS?mode=offline").json()      # never falls back to the dynamic run
        self.assertEqual((off["run"], off["scores"]["Master_ESG_Score"]), ("batch", 80.0))

    def test_path_traversal_rejected(self):
        r = self.client.delete("/api/runs/offline/..%2F..")
        self.assertIn(r.status_code, (400, 404))

    def test_batch_failure_keeps_published_results_and_success_replaces_them(self):
        import pandas as pd
        from unittest.mock import patch

        app = self.app_mod
        job_id = "batch-test"
        published = app.batch_dir("offline")
        original_scores = "Company_Code,Company_Name,Master_ESG_Score,Rank\nTCS,Tata,80.0,1\n"
        self.addCleanup(lambda: published.joinpath("esg_master_scores.csv").write_text(
            original_scores, encoding="utf-8"))
        self.addCleanup(lambda: app.JOBS.pop(job_id, None))

        class FakeProcess:
            def __init__(self, returncode, output_dir):
                self.stdout = [f"Output folder: {output_dir}\n"]
                self.returncode = returncode

            def wait(self):
                return self.returncode

        failed_stage = app.BASE / ".batch_offline_failed.staging"
        failed_stage.mkdir()
        (failed_stage / "run_mode.json").write_text('{"status":"failed"}', encoding="utf-8")
        app.JOBS[job_id] = {"id": job_id, "company": "ALL (offline batch)", "mode": "offline",
                            "status": "running", "log": [], "run": "batch",
                            "_staging_dir": str(failed_stage)}
        with patch.object(app.subprocess, "Popen", return_value=FakeProcess(1, failed_stage)):
            app._run_job(job_id, [], failed_stage)
        self.assertEqual(app.JOBS[job_id]["status"], "failed")
        self.assertEqual(float(pd.read_csv(published / "esg_master_scores.csv").iloc[0]["Master_ESG_Score"]), 80.0)

        success_stage = app.BASE / ".batch_offline_success.staging"
        success_stage.mkdir()
        (success_stage / "run_mode.json").write_text('{"status":"completed"}', encoding="utf-8")
        (success_stage / "agent_run_report.json").write_text(
            '{"agents":[{"status":"completed"}]}', encoding="utf-8")
        (success_stage / "esg_master_scores.csv").write_text(
            "Company_Code,Company_Name,Master_ESG_Score,Rank\nTCS,Tata,90,1\n", encoding="utf-8")
        app.JOBS[job_id]["status"] = "running"
        with patch.object(app.subprocess, "Popen", return_value=FakeProcess(0, success_stage)):
            app._run_job(job_id, [], success_stage)
        self.assertEqual(app.JOBS[job_id]["status"], "completed", app.JOBS[job_id].get("error"))
        self.assertEqual(app.JOBS[job_id]["run"], "batch")
        self.assertEqual(float(pd.read_csv(published / "esg_master_scores.csv").iloc[0]["Master_ESG_Score"]), 90.0)
        app.JOBS.pop(job_id, None)

    def test_job_progress_persists_and_interrupted_jobs_are_failed(self):
        app = self.app_mod
        job_id = "abcdef1234"
        app.JOBS[job_id] = {
            "id": job_id, "company": "ALL (offline batch)", "mode": "offline",
            "status": "running", "run": "batch",
            "expected_agents": ["agent1", "agent2"],
            "log": ["[agent1] start | mode=policy", "[agent1] completed in 1s", "[agent2] start | mode=policy"],
        }
        app._persist_job(app.JOBS[job_id])
        app.JOBS.pop(job_id)

        response = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertIn("server restarted", payload["error"])
        self.assertEqual(payload["progress"]["completed"], 1)
        self.assertEqual(payload["progress"]["percent"], 50)
        self.assertEqual(payload["progress"]["agents"][1]["status"], "running")


if __name__ == "__main__":
    unittest.main()
