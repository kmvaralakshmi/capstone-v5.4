"""Agent 8 - Validation & Research Agent (spec C4-C7, C9, C10).

ROLE
    Test whether the system's results hold up against external references and
    explore how ESG performance relates to market, news and air-quality data.

ANALYSES
    nse_top200   our ESG vs the NSE Top-200 ESG benchmark (YOU provide the file)
    nse_cfa      our BRSR disclosure rates vs the NSE-CFA Institute study (YOU provide)
    esg_stock    master score vs FY/1Y return, volatility, drawdown
    esg_news     master score vs news sentiment / negative events
    esg_air      environmental score vs local AQI (context only)

HONESTY RULES
    * Every statistic is reported with n; under 10 paired companies it is marked
      "insufficient sample" instead of presenting a correlation.
    * Results are associations, never causal claims.
    * If a benchmark file is absent the analysis says so and shows how to
      supply it - nothing is fabricated.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .common import COMPANIES, PROCESSED, ROOT, OUTPUT_FILES, clean, is_num, pd, read_csv_safe
from .engine.anchors import load_anchors
from .framework import BaseAgent, Decision, Tool

BENCH = ROOT / "data" / "benchmark"
SENS_TRIALS = 300
MIN_N = 10
ANALYSES = ["nse_top200", "nse_cfa", "esg_stock", "esg_news", "esg_air", "sensitivity", "calibration"]


def corr_report(x, y) -> Dict[str, Any]:
    from scipy import stats
    d = pd.DataFrame({"x": x, "y": y}).apply(pd.to_numeric, errors="coerce").dropna()
    n = len(d)
    out: Dict[str, Any] = {"n": n}
    if n < MIN_N or d["x"].nunique() < 3 or d["y"].nunique() < 3:
        out["status"] = f"insufficient sample (n={n}, need >= {MIN_N})"
        return out
    pr, pp = stats.pearsonr(d["x"], d["y"])
    sr, sp = stats.spearmanr(d["x"], d["y"])
    pr, pp, sr, sp = map(float, (pr, pp, sr, sp))
    if min(pp, sp) >= 0.05:
        reading = f"no statistically significant association (n={n}, p={min(pp, sp):.3f})"
    elif pr * sr < 0:
        reading = "inconsistent direction, no reliable association"
    else:
        strength = "weak" if abs(sr) < 0.3 else "moderate" if abs(sr) < 0.6 else "strong"
        reading = f"{strength} {'positive' if sr > 0 else 'negative'} association (not causation)"
    out.update({"status": "ok", "pearson_r": round(float(pr), 3), "pearson_p": round(float(pp), 4),
                "spearman_rho": round(float(sr), 3), "spearman_p": round(float(sp), 4),
                "reading": reading})
    return out


class ValidationResearchAgent(BaseAgent):
    agent_id = "agent8"
    name = "Agent 8 - Validation & Research Agent"
    role = "Validate results against external benchmarks and analyse ESG vs market, news and air quality."
    goal = "All five analyses attempted and written, each stating its sample size and limitations."
    rules = ["Report n for every statistic.", "Never claim causation.",
             "If a benchmark file is missing, say how to supply it instead of inventing numbers."]

    def __init__(self, *args, companies: Optional[List[str]] = None, **kw):
        super().__init__(*args, **kw)           # validation always uses every scored company in the run folder
        self.memory.update({"data": None, "done": {}, "results": {}, "finalized": False})

    def build_tools(self) -> List[Tool]:
        tools = [Tool("load_results", "Load scores, market, news and air-quality outputs.", self.t_load)]
        for name in ANALYSES:
            tools.append(Tool(f"run_{name}", f"Run the {name} analysis.", (lambda n=name: self.t_run(n))))
        tools.append(Tool("interpret", "Write a cautious, verified reading of every analysis.", self.t_interpret))
        tools.append(Tool("finalize", "Write validation_summary.json.", self.t_finalize))
        return tools

    def t_load(self) -> Dict[str, Any]:
        d = {k: read_csv_safe(PROCESSED / f) for k, f in {
            "scores": OUTPUT_FILES["master_scores"], "market": OUTPUT_FILES["stock_correlation"],
            "news": "esg_news_company_summary.csv", "env": OUTPUT_FILES["environmental_risk"],
            "brsr": OUTPUT_FILES["brsr_metrics"], "gw": OUTPUT_FILES["greenwashing"]}.items()}
        self.memory["data"] = d
        scored = int(d["scores"]["Master_ESG_Score"].notna().sum()) if not d["scores"].empty else 0
        return {"ok": scored > 0, "scored_companies": scored,
                **({} if scored else {"error": "no scored companies: run the pipeline first"})}

    def _scored(self) -> pd.DataFrame:
        s = self.memory["data"]["scores"]
        return s[s["Master_ESG_Score"].notna()].copy()

    def t_run(self, name: str) -> Dict[str, Any]:
        res = getattr(self, f"_a_{name}")()
        self.memory["results"][name] = res
        self.memory["done"][name] = True
        return {"ok": True, "analysis": name, "status": res.get("status")}

    # ------------------------------------------------------------ analyses
    def _a_nse_top200(self) -> Dict[str, Any]:
        """The NSE report publishes sector statistics, not per-company scores, so the comparison is made at sector level."""
        fs, fc = BENCH / "nse_top200_sector.csv", BENCH / "nse_top200_companies.csv"
        if not fs.exists() or not fc.exists():
            return {"status": "awaiting benchmark files",
                    "how": "python tools/build_benchmarks.py --top200-pdf <NSE ESG Analysis on Top 200 report>.pdf"}
        sec, comp = pd.read_csv(fs), pd.read_csv(fc)
        norm = lambda x: re.sub(r"\b(limited|ltd|services|technologies|systems|software)\b", "", re.sub(r"[^a-z0-9 ]", "", str(x).lower())).replace(" ", "")  # noqa: E731
        it_names = {norm(n): n for n in comp[comp["Sector"] == "Information Technology"]["Company_Name"]}
        all_names = {norm(n): n for n in comp["Company_Name"]}
        sc = self._scored()
        sc["_n"] = sc["Company_Name"].map(norm)
        in_top200 = sc[sc["_n"].isin(all_names) | sc["_n"].map(lambda x: any(x and (x in k or k in x) and len(x) > 4 for k in all_names))]
        it = sc[sc["_n"].isin(it_names) | sc["_n"].map(lambda x: any(x and (x in k or k in x) and len(x) > 4 for k in it_names))]
        rows, out = [], {"status": "ok", "cohort_scored": int(len(sc)), "cohort_in_top200_list": int(len(in_top200)),
                         "cohort_in_top200_IT_sector": int(len(it)), "caveat": ("different methodology (NSE uses SES ESG's model), "
                         "different year and scale basis; sector averages are compared, not company scores")}
        for pillar, col in (("Overall", "Master_ESG_Score"), ("Environmental", "Environmental_Score"), ("Social", "Social_Score"),
                            ("Governance", "Governance_Score")):
            ref = sec[(sec["Pillar"] == pillar) & (sec["Group"] == "Information Technology")]
            if ref.empty or it.empty or it[col].notna().sum() == 0:
                continue
            r = ref.iloc[0]
            ours = it[col].dropna()
            rows.append({"Pillar": pillar, "Our_IT_Mean": round(float(ours.mean()), 1), "Our_IT_Median": round(float(ours.median()), 1),
                         "Our_IT_Min": round(float(ours.min()), 1), "Our_IT_Max": round(float(ours.max()), 1), "n_our_IT_companies": int(len(ours)),
                         "NSE_IT_Avg": int(r["Avg"]), "NSE_IT_Median": int(r["Median"]), "NSE_IT_Min": int(r["Min"]), "NSE_IT_Max": int(r["Max"]),
                         "Mean_Difference": round(float(ours.mean()) - float(r["Avg"]), 1),
                         "Our_Mean_Within_NSE_Range": bool(r["Min"] <= ours.mean() <= r["Max"])})
        pd.DataFrame(rows).to_csv(PROCESSED / "validation_nse_top200.csv", index=False)
        pd.DataFrame({"Company_Name": in_top200["Company_Name"], "Master_ESG_Score": in_top200["Master_ESG_Score"]}).to_csv(
            PROCESSED / "validation_nse_top200_overlap.csv", index=False)
        out["sector_comparison"] = rows
        if not rows:
            out["status"] = "no overlapping IT-sector companies with scores"
        return out

    def _a_nse_cfa(self) -> Dict[str, Any]:
        brsr = self.memory["data"]["brsr"]
        if brsr.empty:
            return {"status": "no BRSR output"}
        n_co = brsr["Company_Code"].nunique()
        valid = brsr["Metric_Value"].notna() & brsr["Validation_Status"].isin(["VALID", "REPAIRED"])
        ours = (brsr[valid].groupby("Metric_Name")["Company_Code"].nunique() / max(n_co, 1) * 100).round(1)
        table = pd.DataFrame({"Metric": ours.index, "Our_Pct_Companies_With_Verified_Value": ours.values})
        f = BENCH / "nse_cfa_reference.csv"
        if not f.exists():
            table.to_csv(PROCESSED / "validation_nse_cfa.csv", index=False)
            return {"status": "awaiting reference file", "how": "python tools/build_benchmarks.py --top200-pdf <report>.pdf"}
        ref = pd.read_csv(f)
        m = table.merge(ref, on="Metric", how="inner")
        m["Difference_pp"] = m["Our_Pct_Companies_With_Verified_Value"] - pd.to_numeric(m["Reference_Pct_Companies_Reporting"], errors="coerce")
        m.to_csv(PROCESSED / "validation_nse_cfa.csv", index=False)
        return {"status": "ok" if len(m) else "no comparable indicators", "metrics_compared": int(len(m)),
                "comparison": clean(m[["Metric", "Our_Pct_Companies_With_Verified_Value", "Reference_Pct_Companies_Reporting", "Difference_pp"]].to_dict("records")),
                "caveat": "reference = FY2022-23, 300 listed companies; ours = FY2024-25, a technology-heavy cohort; our rate counts values that passed the audit"}

    def _pair(self, ours_col: str, other: pd.DataFrame, other_col: str) -> Tuple[pd.DataFrame, str]:
        s = self._scored()
        m = s[["Company_Code", ours_col]].merge(other[["Company_Code", other_col]], on="Company_Code")
        return m, ""

    def _a_esg_stock(self) -> Dict[str, Any]:
        mk = self.memory["data"]["market"]
        if mk.empty:
            return {"status": "no market output"}
        out, rows = {"status": "ok"}, []
        for col in ("FY_Return_Pct", "Return_1Y_Pct", "Volatility_Pct", "Max_Drawdown_Pct"):
            if col in mk:
                m, _ = self._pair("Master_ESG_Score", mk, col)
                r = corr_report(m["Master_ESG_Score"], m[col])
                out[col] = r
                rows.append({"Analysis": f"ESG vs {col}", **r})
        pd.DataFrame(rows).to_csv(PROCESSED / "validation_esg_stock.csv", index=False)
        n = max((v.get("n", 0) for v in out.values() if isinstance(v, dict)), default=0)
        out["status"] = "ok" if n >= MIN_N else f"insufficient sample (n={n}); run in --dynamic mode so more companies have prices"
        return out

    def _a_esg_news(self) -> Dict[str, Any]:
        nw = self.memory["data"]["news"]
        if nw.empty:
            return {"status": "no news output"}
        out, rows = {}, []
        for col in ("Shrunk_Sentiment", "Negative_Share", "Negative_Events"):
            if col in nw:
                m, _ = self._pair("Master_ESG_Score", nw[nw["Articles"] > 0], col)
                r = corr_report(m["Master_ESG_Score"], m[col])
                out[col] = r
                rows.append({"Analysis": f"ESG vs {col}", **r})
        pd.DataFrame(rows).to_csv(PROCESSED / "validation_esg_news.csv", index=False)
        n = max((v.get("n", 0) for v in out.values()), default=0)
        out["status"] = "ok" if n >= MIN_N else f"insufficient sample (n={n}); run in --dynamic mode (GDELT) for more articles"
        return out

    def _a_esg_air(self) -> Dict[str, Any]:
        env = self.memory["data"]["env"]
        if env.empty:
            return {"status": "no air-quality output"}
        e = env.dropna(subset=["Avg_AQI"])
        hq = e[e["Location_Type"] == "Headquarters"].groupby("Company_Code")["Avg_AQI"].mean().reset_index()
        sc = self._scored()
        # the E pillar contains no air-quality term (air quality is context only), so this is a genuine association test
        col = "Environmental_Score"
        m = sc[["Company_Code", col]].merge(hq, on="Company_Code").rename(columns={col: "Environmental_Score"})
        r = corr_report(m["Environmental_Score"], m["Avg_AQI"])
        pd.DataFrame([{"Analysis": "Environmental pillar score vs HQ AQI", **r}]).to_csv(PROCESSED / "validation_esg_air.csv", index=False)
        return {"status": r["status"] if r["status"] != "ok" else "ok", "environmental_vs_hq_aqi": r,
                "note": "air quality is local context, not evidence about the company"}

    def _a_sensitivity(self) -> Dict[str, Any]:
        """How much does the cohort ranking move if every anchor and weight is perturbed?"""
        from scipy import stats
        from .agent6_master_scoring import compute_score
        ms = read_csv_safe(PROCESSED / "esg_metric_scores.csv")
        gw = self.memory["data"].get("gw", pd.DataFrame())
        if ms.empty:
            return {"status": "no metric scores"}
        base_anchors = load_anchors()
        trust = {r["Company_Code"]: (float(r["Trust_Score"]) if is_num(r["Trust_Score"]) else None) for _, r in gw.iterrows()} if not gw.empty else {}
        vals = {}
        for code, g in ms.groupby("Company_Code"):
            vals[code] = {r["Metric_Name"]: {"value": float(r["Value"])} for _, r in g.iterrows()}

        def run(anchors):
            out = {}
            for code, v in vals.items():
                r = compute_score(v, trust.get(code), anchors)
                if r["gate"] != "Insufficient" and r["master"] is not None:
                    out[code] = r["master"]
            return pd.Series(out)
        base = run(base_anchors)
        if len(base) < 5:
            return {"status": f"insufficient scored companies (n={len(base)})"}
        rng = np.random.default_rng(42)
        rhos, shifts, top_keep, band_keep = [], [], [], []
        k = max(3, len(base) // 4)
        base_top = set(base.sort_values(ascending=False).head(k).index)
        base_band = pd.cut(base, [-1, 50, 70, 101], labels=["High", "Medium", "Low"])
        import copy
        for _ in range(SENS_TRIALS):
            a = copy.deepcopy(base_anchors)
            for name, spec in a["scored_metrics"].items():
                if spec.get("scale") == "log":
                    spec["anchor_0"] *= float(np.exp(rng.uniform(-0.4, 0.4)))     # about +/-40% multiplicatively
                    spec["anchor_10"] *= float(np.exp(rng.uniform(-0.4, 0.4)))
                else:
                    span = spec["anchor_10"] - spec["anchor_0"]
                    spec["anchor_0"] += span * rng.uniform(-0.2, 0.2)
                    spec["anchor_10"] += span * rng.uniform(-0.2, 0.2)
            a["hygiene_weight"] = float(rng.uniform(0.25, 1.0))
            a["trust_weight"] = float(np.clip(base_anchors["trust_weight"] + rng.uniform(-0.1, 0.1), 0, 0.4))
            w = np.array(list(base_anchors["pillar_weights"].values())) * rng.uniform(0.7, 1.3, 3)
            a["pillar_weights"] = dict(zip(base_anchors["pillar_weights"], (w / w.sum()).tolist()))
            s2 = run(a).reindex(base.index)
            ok = s2.notna()
            if ok.sum() < 5:
                continue
            rhos.append(float(stats.spearmanr(base[ok], s2[ok])[0]))
            rb, r2 = base[ok].rank(ascending=False), s2[ok].rank(ascending=False)
            shifts.append(float((rb - r2).abs().mean()))
            top_keep.append(len(base_top & set(s2.sort_values(ascending=False).head(k).index)) / k)
            band2 = pd.cut(s2[ok], [-1, 50, 70, 101], labels=["High", "Medium", "Low"])
            band_keep.append(float((band2 == base_band[ok]).mean()))
        res = {"status": "ok", "trials": len(rhos), "companies": int(len(base)),
               "spearman_vs_baseline_median": round(float(np.median(rhos)), 3), "spearman_vs_baseline_p5": round(float(np.percentile(rhos, 5)), 3),
               "mean_abs_rank_shift_median": round(float(np.median(shifts)), 2),
               f"top_{k}_retained_median": round(float(np.median(top_keep)), 2), "risk_band_unchanged_median": round(float(np.median(band_keep)), 2),
               "perturbation": "each anchor +/-20% of its span (log anchors +/-40% multiplicative), hygiene weight 0.25-1.0, trust weight +/-0.10, pillar weights x0.7-1.3 (300 random trials, seed 42)",
               "reading": "rankings are robust to these choices" if np.percentile(rhos, 5) >= 0.9 else "rankings are sensitive to anchor/weight choices; interpret ranks with caution"}
        pd.DataFrame({"Spearman_vs_baseline": rhos, "Mean_abs_rank_shift": shifts, "Top_quartile_retained": top_keep, "Band_unchanged": band_keep}).to_csv(
            PROCESSED / "validation_sensitivity.csv", index=False)
        return res

    def _a_calibration(self) -> Dict[str, Any]:
        """Saturation check: a scored metric that (almost) every company maxes out, or zeroes, cannot discriminate."""
        ms = read_csv_safe(PROCESSED / "esg_metric_scores.csv")
        if ms.empty:
            return {"status": "no metric scores"}
        rows = []
        for (pillar, name), g in ms.groupby(["Pillar", "Metric_Name"]):
            s = pd.to_numeric(g["Score_0_10"], errors="coerce").dropna()
            rows.append({"Pillar": pillar, "Metric": name, "n": int(len(s)), "Mean_Score": round(float(s.mean()), 2),
                         "Share_at_ceiling": round(float((s >= 9.5).mean()), 2), "Share_at_floor": round(float((s <= 0.5).mean()), 2)})
        t = pd.DataFrame(rows)
        t["Flag"] = np.where((t["Share_at_ceiling"] >= 0.9) | (t["Share_at_floor"] >= 0.9), "saturated: cannot discriminate",
                             np.where((t["Share_at_ceiling"] >= 0.6) | (t["Share_at_floor"] >= 0.6), "mostly saturated", ""))
        t.to_csv(PROCESSED / "validation_calibration.csv", index=False)
        sat = t[t["Flag"] == "saturated: cannot discriminate"]["Metric"].tolist()
        mostly = t[t["Flag"] == "mostly saturated"]["Metric"].tolist()
        return {"status": "ok", "metrics": int(len(t)), "saturated_metrics": sat, "mostly_saturated_metrics": mostly,
                "reading": ("every scored metric discriminates between companies" if not sat and not mostly else
                            "some scored metrics are saturated across the cohort: they carry little ranking information (hygiene metrics are down-weighted for this reason)")}

    # ------------------------------------------------------------ interpretation
    CAUSAL = re.compile(r"\b(because|causes?|caused|drives?|driven by|leads? to|results? in|due to|proves?|proved)\b", re.I)

    def t_interpret(self) -> Dict[str, Any]:
        """A cautious plain-language reading of each analysis. LLM judgement verified by code (numbers must exist in the
        statistics, no causal wording); deterministic text otherwise."""
        out = {}
        for name, res in self.memory["results"].items():
            blob = json.dumps(clean(res), default=str)
            nums = {round(float(x), 2) for x in re.findall(r"-?\d+(?:\.\d+)?", blob)}
            evidence = {"key": name, "analysis": name, "statistics": clean(res)}

            def verify(ans, ev, nums=nums):
                t = str(ans.get("interpretation", ""))
                if not t or len(t) > 900:
                    return False, None, "interpretation must be 1-900 characters"
                if self.CAUSAL.search(t):
                    return False, None, "do not use causal wording; these are associations"
                bad = [x for x in re.findall(r"-?\d+(?:\.\d+)?", t) if round(float(x), 2) not in nums and round(float(x), 1) not in {round(n, 1) for n in nums}]
                if bad:
                    return False, None, f"numbers not present in the statistics: {bad[:4]}"
                return True, {"interpretation": t}, ""

            def fallback(ev, res=res):
                st = str(res.get("status"))
                if st != "ok":
                    return {"interpretation": f"Not interpretable yet: {st}."}
                if name == "sensitivity":
                    return {"interpretation": f"Across {res['trials']} perturbations the median Spearman correlation with the baseline ranking is {res['spearman_vs_baseline_median']} (5th percentile {res['spearman_vs_baseline_p5']}); {res['reading']}."}
                return {"interpretation": "Results are statistical associations or descriptive comparisons; see the statistics and caveats."}

            ans = self.judge(f"interpret the '{name}' validation result cautiously",
                             'Answer JSON {"interpretation": "<=3 sentences. Report sample sizes, say associations are not causation, state limitations. Use only numbers from the statistics."}',
                             evidence, verify, fallback)
            out[name] = {"text": ans["interpretation"], "source": ans["_source"]}
        self.memory["interpretations"] = out
        return {"ok": True, "interpreted": len(out)}

    def t_finalize(self) -> Dict[str, Any]:
        summary = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "scored_companies": int(len(self._scored())), "analyses": clean(self.memory["results"]), "interpretations": self.memory.get("interpretations", {}),
                   "caveat": "All relationships are statistical associations, not causal claims."}
        (PROCESSED / "validation_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        self.memory["finalized"] = True
        return {"ok": True, "analyses": {k: v.get("status") for k, v in self.memory["results"].items()}}

    def observe(self) -> Dict[str, Any]:
        return {"loaded": self.memory["data"] is not None,
                "pending": [a for a in ANALYSES if a not in self.memory["done"]], "interpreted": "interpretations" in self.memory,
                "finalized": self.memory["finalized"]}

    def is_done(self, s) -> Tuple[bool, str]:
        return (True, "validation_summary.json written") if s["finalized"] else (False, "")

    def policy(self, s) -> Optional[Decision]:
        if not s["loaded"]:
            return Decision("load_results", {}, "load outputs")
        if s["pending"]:
            return Decision(f"run_{s['pending'][0]}", {}, "next analysis")
        if not s["interpreted"]:
            return Decision("interpret", {}, "interpret the results")
        return Decision("finalize", {}, "write summary")
