"""Agent 6 - Master ESG scoring (deterministic by design).

Scoring is arithmetic, so it stays in code and is fully reproducible; the agent's job is to gate,
apply the documented formulas and flag inconsistencies.

  metric score   10 * (v - anchor_0) / (anchor_10 - anchor_0), clipped to [0, 10]; intensities use ln(v)
                 instead of v (config/anchors.json)
  pillar B_p     weighted mean of the available scored metric scores of the pillar (>= 2 required);
                 'hygiene' metrics (policy exists / zero incidents) count at hygiene_weight (0.5)
  pillar P_p     (1 - w_t) * B_p + w_t * Trust          Trust = Agent 5 consistency trust score (0-10)
  master         10 * sum_p( W_p * P_p ) / sum_p( W_p )  over the pillars that exist
  gate           Pass / Partial / Insufficient on the number of usable scored metrics

Air quality, news sentiment and market performance are NOT part of the score: they are independent
evidence *about* the company, not company-reported ESG performance (spec A6, C9). They are carried
as context columns and used by Agents 5 and 8 for consistency and validation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .common import COMPANIES, OUTPUT_FILES, PROCESSED, TARGET_FY, clean, is_num, np, pd, read_csv_safe
from .engine.anchors import load_anchors, metric_score, metric_weight
from .framework import BaseAgent, Decision, Tool

PILLARS = ("Environmental", "Social", "Governance")


def compute_score(values: Dict[str, Dict[str, Any]], trust: Optional[float], anchors: Dict[str, Any]) -> Dict[str, Any]:
    """Pure scoring function (also used by the sensitivity analysis).

    values: metric -> {"value": float|None, "confidence": float}"""
    sm = anchors["scored_metrics"]
    per_pillar: Dict[str, List[float]] = {p: [] for p in PILLARS}
    weights_in: Dict[str, List[float]] = {p: [] for p in PILLARS}
    scores: Dict[str, float] = {}
    for name, spec in sm.items():
        v = values.get(name, {}).get("value")
        if v is None or (isinstance(v, float) and v != v):
            continue
        s = metric_score(name, float(v), anchors)
        scores[name] = s
        per_pillar[spec["pillar"]].append(s)
        weights_in[spec["pillar"]].append(metric_weight(name, anchors))
    wt = float(anchors["trust_weight"]) if trust is not None else 0.0
    pillar_scores: Dict[str, Optional[float]] = {}
    for p in PILLARS:
        if len(per_pillar[p]) >= anchors["min_metrics_per_pillar"]:
            b = float(np.average(per_pillar[p], weights=weights_in[p]))
            pillar_scores[p] = (1 - wt) * b + wt * float(trust) if trust is not None else b
        else:
            pillar_scores[p] = None
    w = anchors["pillar_weights"]
    avail = {p: s for p, s in pillar_scores.items() if s is not None}
    master = (10 * sum(w[p] * s for p, s in avail.items()) / sum(w[p] for p in avail)) if avail else None
    usable = len(scores)
    gate = anchors["quality_gate"]
    counts = {p: len(per_pillar[p]) for p in PILLARS}
    if usable >= gate["pass_min_metrics"] and all(c >= gate["pass_min_per_pillar"] for c in counts.values()):
        status = "Pass"
    elif usable >= gate["partial_min_metrics"] and sum(1 for c in counts.values() if c >= 2) >= gate["partial_min_pillars"]:
        status = "Partial"
    else:
        status = "Insufficient"
    return {"metric_scores": scores, "pillar_scores": pillar_scores, "master": master, "gate": status,
            "usable": usable, "counts": counts}


def risk_band(score: Optional[float], anchors: Dict[str, Any]) -> str:
    if score is None:
        return "Insufficient evidence"
    b = anchors["risk_bands"]
    return "Low" if score >= b["low_min"] else "Medium" if score >= b["medium_min"] else "High"


class MasterScoringAgent(BaseAgent):
    agent_id = "agent6"
    name = "Agent 6 - Master ESG Scoring Agent"
    role = "Apply the documented scoring formulas to audited BRSR metrics, behind a quality gate."
    goal = "Every company is scored from verified company-reported metrics or explicitly withheld; nothing is defaulted."
    rules = ["Only company-reported, audited BRSR metrics enter the score.",
             "Air quality, news and market data are context, never score components.",
             "Withhold a score rather than fill a gap with a default value.",
             "Never change a metric value."]

    def __init__(self, *args, companies: Optional[List[str]] = None, **kw):
        self.codes = [c.upper() for c in companies] if companies else list(COMPANIES)
        self.anchors = load_anchors()
        super().__init__(*args, **kw)
        self.memory.update({"data": None, "results": {}, "finalized": False})

    def build_tools(self) -> List[Tool]:
        return [
            Tool("load_inputs", "Load audited metrics, trust scores and context evidence.", self.t_load),
            Tool("score_company", "Score ONE company (metric scores, pillars, gate, consistency flags).", self.t_score,
                 {"company_code": {"type": "string"}}, ["company_code"]),
            Tool("finalize", "Write the master score, metric score, data-quality and cross-validation files.", self.t_finalize),
        ]

    def t_load(self) -> Dict[str, Any]:
        d = {"brsr": read_csv_safe(PROCESSED / OUTPUT_FILES["brsr_metrics"]),
             "gw": read_csv_safe(PROCESSED / OUTPUT_FILES["greenwashing"]),
             "env": read_csv_safe(PROCESSED / OUTPUT_FILES["environmental_risk"]),
             "news": read_csv_safe(PROCESSED / "esg_news_company_summary.csv"),
             "news_art": read_csv_safe(PROCESSED / OUTPUT_FILES["news_sentiment"]),
             "mkt": read_csv_safe(PROCESSED / OUTPUT_FILES["stock_correlation"])}
        self.memory["data"] = d
        if d["brsr"].empty:
            self.add_finding("no_brsr", "high", "no BRSR metrics found: Agent 1 must run first")
        return {"ok": not d["brsr"].empty, "brsr_rows": len(d["brsr"]), "trust_rows": len(d["gw"])}

    def _context(self, code: str) -> Dict[str, Any]:
        d = self.memory["data"]
        out: Dict[str, Any] = {}
        e = d["env"][d["env"]["Company_Code"] == code] if not d["env"].empty else pd.DataFrame()
        if not e.empty and e["Environmental_Risk_Score"].notna().any():
            w = np.where(e["Location_Type"] == "Headquarters", 2.0, 1.0)
            ok = e["Environmental_Risk_Score"].notna().values
            out["Env_Context_Score_10"] = float(np.average(e["Environmental_Risk_Score"].values[ok], weights=w[ok]))
        n = d["news"][d["news"]["Company_Code"] == code] if not d["news"].empty else pd.DataFrame()
        if not n.empty and is_num(n.iloc[0].get("News_Sentiment_Score_10")):
            out["News_Score_10"] = float(n.iloc[0]["News_Sentiment_Score_10"])
            out["News_Articles"] = int(n.iloc[0]["Articles"])
        m = d["mkt"][d["mkt"]["Company_Code"] == code] if not d["mkt"].empty else pd.DataFrame()
        if not m.empty and is_num(m.iloc[0].get("Stock_Performance_Score")):
            out["Market_Score_10"] = float(m.iloc[0]["Stock_Performance_Score"])
        return out

    def t_score(self, company_code: str) -> Dict[str, Any]:
        code = company_code.upper()
        d = self.memory["data"]
        b = d["brsr"][d["brsr"]["Company_Code"] == code]
        values = {r["Metric_Name"]: {"value": float(r["Metric_Value"])
                                      if is_num(r["Metric_Value"]) and r.get("Audit_Status") != "UNVERIFIED_LLM" else None,
                                      "confidence": float(r["Confidence_Score"]) if is_num(r["Confidence_Score"]) else 0.0,
                                      "unit": r["Unit"], "source": r["Final_Source"], "status": r["Validation_Status"]}
                  for _, r in b.iterrows()}
        g = d["gw"][d["gw"]["Company_Code"] == code] if not d["gw"].empty else pd.DataFrame()
        trust = float(g.iloc[0]["Trust_Score"]) if (not g.empty and is_num(g.iloc[0]["Trust_Score"])) else None
        res = compute_score(values, trust, self.anchors)
        conf = [values[m]["confidence"] for m in res["metric_scores"]]
        ctx = self._context(code)
        flags = []
        P = res["pillar_scores"]
        arts = d["news_art"][d["news_art"]["Company_Code"] == code] if not d["news_art"].empty else pd.DataFrame()
        if not arts.empty:
            neg = arts[arts["Sentiment_Class"] == "Negative"]
            if P["Environmental"] and P["Environmental"] * 10 >= 75 and (neg["ESG_Category"] == "Environmental").any():
                flags.append("high Environmental score alongside negative environmental news (review)")
            if P["Governance"] and P["Governance"] * 10 >= 80 and (neg["ESG_Category"] == "Governance").any():
                flags.append("high Governance score alongside negative governance news (review)")
        if res["master"] is not None and res["master"] >= 70 and trust is not None and trust < 7:
            flags.append("high master score but low disclosure-consistency trust (review)")
        if res["gate"] == "Partial":
            flags.append("Partial evidence: score published but flagged")
        self.memory["results"][code] = {**res, "values": values, "trust": trust, "context": ctx,
                                        "confidence": float(np.mean(conf)) if conf else None, "flags": flags}
        return {"ok": True, "company": code, "gate": res["gate"], "master": None if res["master"] is None else round(res["master"], 2)}

    def t_finalize(self) -> Dict[str, Any]:
        rows, mrows, dq, cv = [], [], [], []
        sm = self.anchors["scored_metrics"]
        for code in self.codes:
            r = self.memory["results"].get(code)
            if r is None:
                continue
            P = r["pillar_scores"]
            publish = r["gate"] != "Insufficient"
            master = r["master"] if publish else None
            total_scored = len(sm)
            rows.append({
                "Company_Code": code, "Company_Name": COMPANIES[code]["full_name"], "Financial_Year": TARGET_FY,
                "Environmental_Score": round(P["Environmental"] * 10, 2) if publish and P["Environmental"] is not None else None,
                "Social_Score": round(P["Social"] * 10, 2) if publish and P["Social"] is not None else None,
                "Governance_Score": round(P["Governance"] * 10, 2) if publish and P["Governance"] is not None else None,
                "Master_ESG_Score": round(master, 2) if master is not None else None, "Score_Scale": "0-100",
                "Risk_Level": risk_band(master, self.anchors), "Quality_Gate_Status": r["gate"],
                "Usable_Metrics": r["usable"], "Metrics_E": r["counts"]["Environmental"], "Metrics_S": r["counts"]["Social"],
                "Metrics_G": r["counts"]["Governance"], "BRSR_Completeness_Pct": round(100 * r["usable"] / total_scored, 1),
                "Evidence_Confidence": round(r["confidence"], 3) if r["confidence"] is not None else None,
                "Trust_Score_10": r["trust"], "Pillars_Scored": sum(v is not None for v in P.values()),
                "Context_Env_AQI_Score_10": r["context"].get("Env_Context_Score_10"),
                "Context_News_Score_10": r["context"].get("News_Score_10"), "Context_News_Articles": r["context"].get("News_Articles"),
                "Context_Market_Score_10": r["context"].get("Market_Score_10"),
                "Context_Note": "AQI, news and market values are context only and are NOT part of the score",
                "Contradiction_Count": len(r["flags"]), "Contradiction_Summary": " | ".join(r["flags"]),
                "Withheld_Reason": "" if publish else f"only {r['usable']} usable scored metrics (need >= {self.anchors['quality_gate']['partial_min_metrics']})",
                "Analysis_Date": pd.Timestamp.today().date().isoformat()})
            for name, s in r["metric_scores"].items():
                v = r["values"][name]
                mrows.append({"Company_Code": code, "Pillar": sm[name]["pillar"], "Metric_Name": name, "Value": v["value"], "Unit": v["unit"],
                              "Score_0_10": round(s, 3), "Anchor_0": sm[name]["anchor_0"], "Anchor_10": sm[name]["anchor_10"],
                              "Source": v["source"], "Status": v["status"], "Confidence": v["confidence"]})
            missing = [m for m in sm if m not in r["metric_scores"]]
            dq.append({"Company_Code": code, "Usable_Scored_Metrics": r["usable"], "Missing_Scored_Metrics": " | ".join(missing),
                       "Quarantined_Metrics": " | ".join(m for m, v in r["values"].items() if v["status"] == "QUARANTINED"),
                       "Quality_Gate_Status": r["gate"]})
            for f in r["flags"]:
                cv.append({"Company_Code": code, "Flag": f})
        df = pd.DataFrame(rows)
        if not df.empty:
            # A rank is only meaningful inside a cohort: a 1-company run has no rank.
            ranked = df[df["Quality_Gate_Status"] == "Pass"].sort_values(
                ["Master_ESG_Score", "Company_Code"], ascending=[False, True], kind="mergesort"
            )
            if len(ranked) >= 5:
                df["Rank"] = None
                df = df.set_index("Company_Code")
                df.loc[ranked["Company_Code"], "Rank"] = range(1, len(ranked) + 1)
                df = df.reset_index()
            else:
                df["Rank"] = None
            df["Scored_Metrics_Total"] = len(self.anchors["scored_metrics"])
        df.to_csv(PROCESSED / OUTPUT_FILES["master_scores"], index=False)
        pd.DataFrame(mrows).to_csv(PROCESSED / "esg_metric_scores.csv", index=False)
        pd.DataFrame(dq).to_csv(PROCESSED / OUTPUT_FILES["data_quality"], index=False)
        pd.DataFrame(cv).to_csv(PROCESSED / OUTPUT_FILES["cross_validation"], index=False)
        self.memory["finalized"] = True
        self.artifacts.append(OUTPUT_FILES["master_scores"])
        gates = df["Quality_Gate_Status"].value_counts().to_dict() if not df.empty else {}
        return {"ok": True, "companies": len(df), "gates": gates}

    def observe(self) -> Dict[str, Any]:
        todo = [c for c in self.codes if c not in self.memory["results"]]
        return {"loaded": self.memory["data"] is not None, "to_score": todo[:8], "to_score_count": len(todo),
                "finalized": self.memory["finalized"]}

    def is_done(self, s):
        if self.memory["finalized"]:
            return True, f"{len(self.memory['results'])} companies scored or withheld"
        return False, ""

    def policy(self, s):
        if not s["loaded"]:
            return Decision("load_inputs", {}, "read audited inputs")
        if s["to_score_count"]:
            return Decision("score_company", {"company_code": s["to_score"][0]}, "score next company")
        if not s["finalized"]:
            return Decision("finalize", {}, "all companies processed")
        return None

    def check_args(self, decision, state):
        base = super().check_args(decision, state)
        if base:
            return base
        if decision.tool != "load_inputs" and not state["loaded"]:
            return "load_inputs must run first"
        if decision.tool == "finalize" and state["to_score_count"]:
            return "score every company before finalizing"
        return None
