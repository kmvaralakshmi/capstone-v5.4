"""Agent 5 - Disclosure-consistency investigator ("greenwashing" signals, never accusations).

For each company it builds CLAIMS (what the company says about itself) and investigates each one
against independent evidence, then turns the outcomes into a graded trust score.

  claims    (a) improvements the BRSR itself reports year over year (from Agent 1 trends)
            (b) achievement statements found in the BRSR PDF text ("carbon neutral", "100% renewable"...)
  evidence  BRSR values and trends, PDF corroboration, comparability of the two years, news
            (allegation vs verified), all given stable ids E1..En
  verdict   consistent | potential_inconsistency | insufficient_evidence - a JUDGEMENT (LLM when
            configured, deterministic rules otherwise) that code verifies: it may only cite
            evidence ids that exist, and cannot claim an inconsistency without contradicting evidence.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .common import BATCH_DIR, BRSR_PDF_DIR, COMPANIES, OUTPUT_FILES, PROCESSED, ROOT, clean, is_num, np, pd, read_csv_safe
from .engine.pdf import load_pages
from .framework import BaseAgent, Decision, Tool

PDF_CACHE = ROOT / "data" / "cache" / "pdf_text"
WEIGHTS = {"source_disagreement": 2.0, "implausible_claims": 1.5, "intensity_outlier": 2.0,
           "claim_contradicted": 3.0, "disclosure_gap": 1.0}
TREND_CLAIM_METRICS = ["Renewable Energy Percentage", "GHG Intensity", "Energy Intensity", "Water Intensity",
                       "Waste Recovered Percentage", "Female Employees Percentage", "Employee Turnover Rate",
                       "Lost Time Injury Frequency Rate"]
ABSOLUTE_OF = {"GHG Intensity": "Scope 1 and 2 Emissions", "Energy Intensity": "Total Energy Consumption",
               "Water Intensity": "Total Water Consumption"}
ENV_METRICS = {"Renewable Energy Percentage", "GHG Intensity", "Energy Intensity", "Water Intensity", "Waste Recovered Percentage"}

ACHIEVED = re.compile(r"(?:\b(?:is|are|has become|have become|achieved|attained|remains?|became)\b[^.]{0,40}"
                      r"(?:carbon[- ]neutral|net[- ]zero|water[- ]positive|zero[- ]waste)|"
                      r"\b100\s?%\s+(?:renewable|green)\s+(?:energy|electricity|power)|"
                      r"\bcarbon[- ]neutral(?:ity)?\s+(?:achieved|status|operations))", re.I)
FORWARD = re.compile(r"\b(by\s+20\d\d|aim|aims|target|targets|commit|commits|committed|plan|plans|will|goals?|maintain|pledge|"
                     r"strive|journey|ambition|towards?|roadmap|intend|expects?)\b", re.I)


class GreenwashingAgent(BaseAgent):
    agent_id = "agent5"
    name = "Agent 5 - Disclosure-Consistency Agent"
    role = "Investigate what each company claims against independent evidence and report graded, evidence-linked signals."
    goal = ("Every company has its claims investigated (or an explicit insufficient-evidence result) and a "
            "trust score derived from verified signals.")
    rules = ["Signals, not accusations: wording stays cautious.",
             "A verdict may only cite evidence that exists; no inconsistency without contradicting evidence.",
             "An allegation in the news is not a verified finding.",
             "Return insufficient evidence rather than guessing."]

    def __init__(self, *args, companies: Optional[List[str]] = None, **kw):
        self.codes = [c.upper() for c in companies] if companies else list(COMPANIES)
        super().__init__(*args, **kw)
        self.memory.update({"data": None, "base": {}, "claims": {}, "verdicts": {}, "results": {}, "finalized": False})

    def build_tools(self) -> List[Tool]:
        c = {"company_code": {"type": "string"}}
        return [
            Tool("load_inputs", "Load Agent 1-4 outputs and the cohort baseline.", self.t_load),
            Tool("extract_claims", "Build the claim list for ONE company (reported trends + PDF statements).",
                 self.t_extract, c, ["company_code"]),
            Tool("investigate_claim", "Investigate ONE claim against the evidence (verified judgement).",
                 self.t_investigate, {**c, "claim_id": {"type": "string"}}, ["company_code", "claim_id"]),
            Tool("score_company", "Turn signals and verdicts into the consistency risk and trust score.",
                 self.t_score, c, ["company_code"]),
            Tool("finalize", "Write greenwashing_detection.csv and greenwashing_claims.csv.", self.t_finalize),
        ]

    # ------------------------------------------------------------------ data
    def t_load(self) -> Dict[str, Any]:
        d = {"brsr": read_csv_safe(PROCESSED / OUTPUT_FILES["brsr_metrics"]),
             "trends": read_csv_safe(PROCESSED / "brsr_trends.csv"),
             "news": read_csv_safe(PROCESSED / "esg_news_sentiment.csv"),
             "news_summary": read_csv_safe(PROCESSED / "esg_news_company_summary.csv")}
        self.memory["data"] = d
        b = d["brsr"]
        base = {}
        if not b.empty:
            for m in ("Energy Intensity", "Water Intensity", "GHG Intensity"):
                v = pd.to_numeric(b[b["Metric_Name"] == m]["Metric_Value"], errors="coerce").dropna()
                v = v[v > 0]
                if len(v) >= 5:
                    lv = np.log(v)
                    base[m] = (float(lv.mean()), float(lv.std() or 1.0), len(v))
        self.memory["base"] = base
        return {"ok": True, "brsr_rows": len(b), "trend_rows": len(d["trends"]), "news_rows": len(d["news"]),
                "baseline_metrics": list(base)}

    def _row(self, code: str, metric: str) -> Optional[pd.Series]:
        b = self.memory["data"]["brsr"]
        r = b[(b["Company_Code"] == code) & (b["Metric_Name"] == metric)]
        return None if r.empty else r.iloc[0]

    def _val(self, code: str, metric: str) -> Optional[float]:
        r = self._row(code, metric)
        return float(r["Metric_Value"]) if r is not None and is_num(r["Metric_Value"]) else None

    # ---------------------------------------------------------------- claims
    def _pdf_statements(self, code: str) -> List[Dict[str, Any]]:
        hit = sorted(BRSR_PDF_DIR.glob(f"{code}_BR_*.pdf"))
        if not hit:
            return []
        try:
            pages = load_pages(hit[0], PDF_CACHE)
        except Exception:
            return []
        out, seen = [], set()
        for pi, text in enumerate(pages, start=1):
            flat = re.sub(r"\s+", " ", text)
            for sent in re.split(r"(?<=[.!?])\s+", flat):
                if 25 <= len(sent) <= 400 and ACHIEVED.search(sent) and not FORWARD.search(sent):
                    key = sent[:80].lower()
                    if key not in seen:
                        seen.add(key)
                        out.append({"text": sent[:260], "page": pi})
            if len(out) >= 3:
                break
        return out[:3]

    def t_extract(self, company_code: str) -> Dict[str, Any]:
        code = company_code.upper()
        tr = self.memory["data"]["trends"]
        claims: List[Dict[str, Any]] = []
        if not tr.empty:
            t = tr[(tr["Company_Code"] == code) & (tr["Direction"] == "improved") & (tr["Metric_Name"].isin(TREND_CLAIM_METRICS))]
            for _, r in t.iterrows():
                claims.append({"id": f"C{len(claims) + 1}", "type": "reported_improvement", "metric": r["Metric_Name"],
                               "text": f"Improved {r['Metric_Name']}", "comparable": bool(r["Comparable"]),
                               "change_pct": clean(r["Change_Pct"]), "current": clean(r["Current"]), "prior": clean(r["Prior"])})
            worsened = tr[(tr["Company_Code"] == code) & (tr["Direction"] == "worsened")]
            absolute_env = {"Total Energy Consumption", "Total Water Consumption", "Scope 1 and 2 Emissions", "Total Waste Generated"}
            for _, r in worsened.iterrows():
                metric = r["Metric_Name"]
                if metric in absolute_env or metric in TREND_CLAIM_METRICS:
                    claims.append({"id": f"C{len(claims) + 1}", "type": "omitted_deterioration",
                                   "metric": metric, "text": f"Unacknowledged deterioration in {metric}",
                                   "comparable": bool(r["Comparable"]), "change_pct": clean(r["Change_Pct"]),
                                   "current": clean(r["Current"]), "prior": clean(r["Prior"])})
        for s in self._pdf_statements(code):
            claims.append({"id": f"C{len(claims) + 1}", "type": "report_statement", "metric": None,
                           "text": s["text"], "page": s["page"]})
        self.memory["claims"][code] = claims
        return {"ok": True, "company": code, "claims": len(claims),
                "reported_improvements": sum(c["type"] == "reported_improvement" for c in claims),
                "pdf_statements": sum(c["type"] == "report_statement" for c in claims)}

    # -------------------------------------------------------------- evidence
    def _evidence(self, code: str, claim: Dict[str, Any]) -> List[Dict[str, str]]:
        ev: List[Dict[str, str]] = []

        def add(kind, text):
            ev.append({"id": f"E{len(ev) + 1}", "kind": kind, "text": text})
        m = claim.get("metric")
        if m:
            r = self._row(code, m)
            if r is not None and is_num(r["Metric_Value"]):
                add("metric_value", f"BRSR {m} = {float(r['Metric_Value']):.4g} ({r['Final_Source']}, confidence {float(r['Confidence_Score']):.2f})")
            if claim["type"] == "omitted_deterioration":
                chg = claim.get("change_pct")
                prior = claim.get("prior")
                current = claim.get("current")
                prior_txt = f"{float(prior):.4g}" if is_num(prior) else "unavailable"
                current_txt = f"{float(current):.4g}" if is_num(current) else "unavailable"
                chg_txt = f"{float(chg):+.1f}%" if is_num(chg) else "change unavailable"
                add("trend_worsened", f"{m} worsened from {prior_txt} to {current_txt} ({chg_txt}); PDF acknowledgement was not found")
            elif claim["comparable"]:
                chg = claim.get("change_pct")
                chg_txt = f"{chg:+.1f}%" if chg is not None else "change not defined: prior value was 0"
                add("trend_improved", f"{m} moved from {claim['prior']:.4g} to {claim['current']:.4g} ({chg_txt}), comparable years")
            else:
                add("trend_not_comparable", f"{m} changed {claim['change_pct'] if claim.get('change_pct') is None else format(claim['change_pct'], '+.1f')}% but the change is >3x: a boundary/definition change is suspected")
            absn = ABSOLUTE_OF.get(m)
            if absn:
                ar = self._row(code, absn)
                if ar is not None and is_num(ar["Metric_Value"]) and is_num(ar["Prior_Year_Value"]) and ar["Prior_Year_Value"] > 0:
                    ch = (float(ar["Metric_Value"]) / float(ar["Prior_Year_Value"]) - 1) * 100
                    if ch > 5:
                        add("absolute_offset", f"intensity improved but absolute {absn} rose {ch:+.1f}%")
            if m == "Renewable Energy Percentage":
                ar = self._row(code, "Total Energy Consumption")
                if ar is not None and is_num(ar["Metric_Value"]) and is_num(ar["Prior_Year_Value"]) and ar["Prior_Year_Value"] > 0:
                    ch = (float(ar["Metric_Value"]) / float(ar["Prior_Year_Value"]) - 1) * 100
                    if ch > 5:
                        add("absolute_offset", f"renewable share improved while total energy use rose {ch:+.1f}%")
            if r is not None and str(r.get("PDF_Status", "")) in ("MATCH", "MATCH_AFTER_SCALE"):
                add("pdf_corroborated", f"the BRSR PDF prints the XBRL value (page {int(r['PDF_Page']) if is_num(r['PDF_Page']) else '?'})")
            elif r is not None and str(r.get("PDF_Status", "")) == "MISMATCH":
                add("pdf_mismatch", "the BRSR PDF and XBRL disagree on this value")
        else:
            txt = claim["text"].lower()
            ren, s12 = self._val(code, "Renewable Energy Percentage"), self._val(code, "Scope 1 and 2 Emissions")
            if "100" in txt and "renewable" in txt and ren is not None:
                add("claim_value_contradiction" if ren < 95 else "metric_value",
                    f"BRSR reports a renewable share of {ren:.1f}%")
            if ("carbon" in txt and "neutral" in txt) or "net zero" in txt or "net-zero" in txt:
                if s12 is not None and s12 > 0:
                    add("metric_value", f"BRSR still reports Scope 1+2 emissions of {s12:,.0f} tCO2e (no offset data in BRSR)")
        news = self.memory["data"]["news"]
        if not news.empty:
            n = news[news["Company_Code"] == code]
            pillar = "Environmental" if (m in ENV_METRICS or not m) else None
            for _, a in n.iterrows():
                if pillar and a.get("ESG_Category") != pillar:
                    continue
                if str(a.get("Is_ESG_Relevant")).lower() not in ("true", "1", "yes"):
                    continue
                if str(a.get("Sentiment_Class")) == "Negative":
                    nat = str(a.get("Nature", "other"))
                    if nat in ("verified_finding", "allegation"):
                        add("news_negative_verified" if nat == "verified_finding" else "news_negative_allegation",
                            f"news ({a.get('Domain', '')}): '{str(a['Article_Title'])[:110]}' [{nat}]")
                elif str(a.get("Sentiment_Class")) == "Positive":
                    add("news_positive", f"news ({a.get('Domain', '')}): '{str(a['Article_Title'])[:110]}'")
        if not any(e["kind"].startswith("news_") for e in ev):
            add("news_none", "no relevant ESG news was retrieved for this company (no corroboration, no contradiction)")
        return ev

    SUPPORT = {"trend_improved", "pdf_corroborated", "news_positive"}
    CONTRA = {"pdf_mismatch", "news_negative_verified", "news_negative_allegation",
              "claim_value_contradiction", "trend_worsened"}

    @staticmethod
    def _verdict_confidence(cited: List[str]) -> float:
        return round(min(0.9, 0.5 + 0.1 * len(set(cited))), 2)

    def _rule_verdict(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        kinds = {item["id"]: item["kind"] for item in evidence["evidence"]}
        support = [item["id"] for item in evidence["evidence"] if item["kind"] in self.SUPPORT]
        contra = [item["id"] for item in evidence["evidence"] if item["kind"] in self.CONTRA]
        if contra:
            strong = any(kinds[ident] in ("pdf_mismatch", "news_negative_verified",
                                          "claim_value_contradiction", "trend_worsened")
                         for ident in contra)
            verdict, cited = "potential_inconsistency", contra
            rationale = "contradicting evidence found" + ("" if strong else " (news allegation only; unverified)")
        elif support and "trend_improved" in {kinds[ident] for ident in support}:
            verdict, cited = "consistent", support
            rationale = "the reported improvement is supported by comparable years" + (
                " and PDF corroboration" if len(support) > 1 else "")
        else:
            cited = [item["id"] for item in evidence["evidence"]
                     if item["kind"] in ("trend_not_comparable", "news_none")][:2]
            verdict = "insufficient_evidence"
            rationale = "the available evidence neither supports nor contradicts the claim"
        return {"verdict": verdict, "cited_evidence": cited,
                "confidence": self._verdict_confidence(cited), "rationale": rationale}

    def _claim_package(self, code: str, claim: Dict[str, Any]) -> Dict[str, Any]:
        ev = self._evidence(code, claim)
        evidence = {"key": f"{code}/{claim['id']}", "company": COMPANIES[code]["full_name"],
                    "claim": claim["text"], "claim_type": claim["type"], "evidence": ev}
        return {"claim": claim, "evidence": ev, "package": evidence, "rule": self._rule_verdict(evidence)}

    def _verify_claim_answer(self, answer: Dict[str, Any], package: Dict[str, Any]):
        v = answer.get("verdict")
        cited = answer.get("cited_evidence", [])
        ev = package["evidence"]
        ids = {e["id"] for e in ev}
        kinds = {e["id"]: e["kind"] for e in ev}
        if v not in ("consistent", "potential_inconsistency", "insufficient_evidence"):
            return False, None, "verdict must be consistent, potential_inconsistency or insufficient_evidence"
        if not isinstance(cited, list) or not all(c in ids for c in cited):
            return False, None, f"cited_evidence must be a list of existing ids {sorted(ids)}"
        if v == "potential_inconsistency" and not any(kinds[c] in self.CONTRA for c in cited):
            return False, None, "potential_inconsistency must cite contradicting evidence"
        if v == "consistent" and not any(kinds[c] in self.SUPPORT for c in cited):
            return False, None, "consistent must cite supporting evidence"
        if v == "consistent" and any(kinds[i] in self.CONTRA and kinds[i] != "news_negative_allegation" for i in ids):
            return False, None, "consistent is not allowed while verified contradicting evidence exists"
        return True, {"verdict": v, "cited_evidence": cited,
                      "confidence": self._verdict_confidence(cited),
                      "rationale": str(answer.get("rationale", ""))[:400]}, ""

    @staticmethod
    def _rule_is_decisive(package: Dict[str, Any]) -> bool:
        rule = package["rule"]
        if rule["verdict"] == "consistent" and rule["confidence"] >= 0.8:
            return True
        if rule["verdict"] != "potential_inconsistency":
            return False
        kinds = {e["kind"] for e in package["evidence"]}
        return bool(kinds & {"pdf_mismatch", "news_negative_verified",
                             "claim_value_contradiction", "trend_worsened"})

    def _investigate_packages(self, code: str, packages: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        pending = [p for p in packages if not self._rule_is_decisive(p)]
        llm_results: Dict[str, Dict[str, Any]] = {}
        if len(pending) == 1:
            item = pending[0]
            llm_results[item["claim"]["id"]] = self.judge(
                "investigate one company claim against the evidence list",
                'Answer JSON {"verdict":"consistent|potential_inconsistency|insufficient_evidence",'
                '"cited_evidence":["E1",...],"confidence":0-1,"rationale":"short"}. Cite only the ids given. '
                "A news allegation is not a verified finding. Use cautious wording; return insufficient_evidence if thin.",
                item["package"], lambda ans, ev: self._verify_claim_answer(ans, item),
                lambda _ev: item["rule"])
        elif len(pending) > 1:
            for start in range(0, len(pending), 5):
                claim_batch = pending[start:start + 5]
                by_id = {p["claim"]["id"]: p for p in claim_batch}
                batch = {"key": f"{code}/uncertain-claims",
                         "claims": [{"claim_id": p["claim"]["id"], "claim": p["claim"]["text"],
                                     "evidence": p["evidence"]} for p in claim_batch]}

                def verify(answer, _evidence):
                    outputs = answer.get("claims")
                    if not isinstance(outputs, list) or len(outputs) != len(claim_batch):
                        return False, None, "claims must contain exactly one result per input claim"
                    clean, seen = [], set()
                    for output in outputs:
                        claim_id = output.get("claim_id") if isinstance(output, dict) else None
                        if claim_id not in by_id or claim_id in seen:
                            return False, None, "each input claim id must appear exactly once"
                        ok, result, why = self._verify_claim_answer(output, by_id[claim_id])
                        if not ok:
                            return False, None, f"{claim_id}: {why}"
                        result["claim_id"] = claim_id
                        clean.append(result)
                        seen.add(claim_id)
                    if seen != set(by_id):
                        return False, None, "one or more input claim ids are missing"
                    return True, {"claims": clean}, ""

                def fallback(_evidence):
                    return {"claims": [{"claim_id": p["claim"]["id"], **p["rule"]} for p in claim_batch]}

                response = self.judge(
                    "compare several company claims against their separate evidence lists",
                    'Return JSON {"claims":[{"claim_id":"C1","verdict":"consistent|potential_inconsistency|'
                    'insufficient_evidence","cited_evidence":["E1",...],"rationale":"short"}]}. Return exactly '
                    "one item per claim id. Evidence ids are local to each claim; never copy an id from another claim. "
                    "A news allegation is not a verified finding. Use cautious wording and do not infer facts.",
                    batch, verify, fallback)
                for result in response["claims"]:
                    llm_results[result["claim_id"]] = {**result, "_source": response["_source"],
                                                       "_reason": response["_reason"]}
        results = {}
        for item in packages:
            claim = item["claim"]
            rule = item["rule"]
            answer = llm_results.get(claim["id"])
            if answer is None:
                answer = {**rule, "_source": "rule", "_reason": "rule verdict is already decisive"}
            llm_verdict = answer["verdict"] if answer["_source"] == "llm" else None
            rationale = rule["rationale"]
            if answer["_source"] == "rule_fallback" and "Verifier rejection:" in answer.get("rationale", ""):
                rationale += " " + answer["rationale"].split("Verifier rejection:", 1)[1].strip()
            caveat = "; ".join(e["text"] for e in item["evidence"] if e["kind"] == "absolute_offset")
            results[claim["id"]] = {
                "claim": claim, "evidence": item["evidence"], "verdict": rule["verdict"],
                "confidence": rule["confidence"], "cited": rule["cited_evidence"],
                "rationale": rationale, "source": answer["_source"], "llm_verdict": llm_verdict,
                "llm_confidence": answer["confidence"] if llm_verdict is not None else None,
                "llm_cited": answer["cited_evidence"] if llm_verdict is not None else [],
                "llm_rationale": answer["rationale"] if llm_verdict is not None else "",
                "agreement": llm_verdict == rule["verdict"] if llm_verdict is not None else None,
                "caveat": caveat,
            }
        self.memory["verdicts"].setdefault(code, []).extend(results.values())
        return results

    def t_investigate(self, company_code: str, claim_id: str) -> Dict[str, Any]:
        code = company_code.upper()
        claims = self.memory["claims"].get(code, [])
        claim = next((c for c in claims if c["id"] == claim_id), None)
        if claim is None:
            return {"ok": False, "error": f"unknown claim {claim_id}"}
        completed = {v["claim"]["id"] for v in self.memory["verdicts"].get(code, [])}
        pending = [c for c in claims if c["id"] not in completed]
        packages = [self._claim_package(code, c) for c in pending]
        results = self._investigate_packages(code, packages) if packages else {}
        stored = next((v for v in self.memory["verdicts"].get(code, []) if v["claim"]["id"] == claim_id), None)
        if stored is None:
            stored = results.get(claim_id)
        if stored is None:
            return {"ok": False, "error": f"no verdict was produced for {claim_id}"}
        return {"ok": True, "claim": claim_id, "verdict": stored["verdict"], "source": stored["source"]}

    # --------------------------------------------------------------- scoring
    def t_score(self, company_code: str) -> Dict[str, Any]:
        code = company_code.upper()
        b = self.memory["data"]["brsr"]
        rows = b[b["Company_Code"] == code]
        scored = rows[rows["Scored"] == True]  # noqa: E712
        usable = int(pd.to_numeric(scored["Metric_Value"], errors="coerce").notna().sum())
        if usable < 6:
            self.memory["results"][code] = {"usable": usable, "insufficient": True}
            return {"ok": True, "company": code, "insufficient_evidence": True, "usable_metrics": usable}
        sig: Dict[str, Tuple[float, str]] = {}
        mism = int((rows["PDF_Status"] == "MISMATCH").sum())
        checked = int(rows["PDF_Status"].isin(["MATCH", "MATCH_AFTER_SCALE", "MISMATCH"]).sum())
        if mism:
            sig["source_disagreement"] = (min(1.0, mism / 3), f"{mism} of {checked} PDF-checked quantities differ between XBRL and PDF")
        q = int((rows["Validation_Status"] == "QUARANTINED").sum()); rep = int((rows["Validation_Status"] == "REPAIRED").sum())
        if q or rep:
            sig["implausible_claims"] = (min(1.0, (q + 0.5 * rep) / 4), f"{q} values quarantined and {rep} repaired by the plausibility audit")
        zs = []
        for m, (mu, sd, n) in self.memory["base"].items():
            v = self._val(code, m)
            if v and v > 0:
                z = (np.log(v) - mu) / sd
                if abs(z) > 2:
                    zs.append(f"{m} z={z:+.1f}")
        if zs:
            sig["intensity_outlier"] = (1.0, "intensity outlier vs cohort: " + ", ".join(zs))
        vs = self.memory["verdicts"].get(code, [])
        bad = [v for v in vs if v["verdict"] == "potential_inconsistency"]
        if bad:
            sig["claim_contradicted"] = (min(1.0, len(bad) / 2), f"{len(bad)} claim(s) with a potential inconsistency")
        miss = 16 - usable
        if miss >= 3:
            sig["disclosure_gap"] = (min(1.0, miss / 16), f"{miss} of 16 scored metrics are not available")
        points = sum(WEIGHTS[k] * s for k, (s, _) in sig.items())
        trust = round(max(0.0, 10.0 - points), 2)
        claim_count = len(vs)
        cohort_median = 7.0
        if claim_count < 3:
            trust = round(0.5 * trust + 0.5 * cohort_median, 2)
        level = "High" if points >= 4 else "Moderate" if points >= 2 else "Low"
        self.memory["results"][code] = {"usable": usable, "insufficient": False, "points": round(points, 3), "trust": trust,
                                        "level": level, "signals": sig, "claims": claim_count,
                                        "trust_basis": f"{trust:.1f}/10 on {claim_count} claims; low-evidence shrink applied" if claim_count < 3 else f"{trust:.1f}/10 on {claim_count} claims"}
        return {"ok": True, "company": code, "trust": trust, "risk_level": level, "signals": list(sig)}

    def t_finalize(self) -> Dict[str, Any]:
        det, cl = [], []
        for code in self.codes:
            r = self.memory["results"].get(code)
            vs = self.memory["verdicts"].get(code, [])
            base = {"Company_Code": code, "Company_Name": COMPANIES[code]["full_name"]}
            if not r or r.get("insufficient"):
                det.append({**base, "Usable_Metrics": (r or {}).get("usable", 0), "Signal_Count": None, "Inconsistency_Risk_Score": None,
                            "Trust_Score": None, "Risk_Level": "Insufficient evidence", "Signals": "", "Claims_Investigated": len(vs)})
            else:
                det.append({**base, "Usable_Metrics": r["usable"], "Signal_Count": len(r["signals"]),
                            "Inconsistency_Risk_Score": r["points"], "Trust_Score": r["trust"], "Risk_Level": r["level"],
                            "Signals": " | ".join(r["signals"]), "Signal_Detail": " | ".join(t for _, t in r["signals"].values()),
                            "Trust_Basis": r["trust_basis"],
                            "Claims_Investigated": len(vs),
                            "Claims_Consistent": sum(v["verdict"] == "consistent" for v in vs),
                            "Claims_Potential_Inconsistency": sum(v["verdict"] == "potential_inconsistency" for v in vs),
                            "Claims_Insufficient_Evidence": sum(v["verdict"] == "insufficient_evidence" for v in vs)})
            for v in vs:
                cl.append({"Company_Code": code, "Claim_ID": v["claim"]["id"], "Claim_Type": v["claim"]["type"], "Claim": v["claim"]["text"],
                           "Metric": v["claim"].get("metric"), "Verdict": v["verdict"], "Confidence": v["confidence"],
                           "Cited_Evidence": ", ".join(v["cited"]), "Rationale": v["rationale"], "Caveat": v["caveat"],
                           "Evidence": " || ".join(f"{e['id']} [{e['kind']}] {e['text']}" for e in v["evidence"]),
                           "Judgement_Source": v["source"], "LLM_Verdict": v["llm_verdict"],
                           "Verdict_Agreement": v["agreement"], "LLM_Confidence": v["llm_confidence"],
                           "LLM_Cited_Evidence": ", ".join(v["llm_cited"]), "LLM_Rationale": v["llm_rationale"]})
        path = PROCESSED / OUTPUT_FILES["greenwashing"]
        pd.DataFrame(det).to_csv(path, index=False)
        pd.DataFrame(cl).to_csv(PROCESSED / "greenwashing_claims.csv", index=False)
        self.memory["finalized"] = True
        self.artifacts.append(str(path.relative_to(path.parents[1])))
        return {"ok": True, "companies": len(det), "claims": len(cl)}

    # ------------------------------------------------------------- reasoning
    def observe(self) -> Dict[str, Any]:
        pending_by_company = {}
        for c in self.codes:
            completed = {v["claim"]["id"] for v in self.memory["verdicts"].get(c, [])}
            pending_by_company[c] = [cl for cl in self.memory["claims"].get(c, []) if cl["id"] not in completed]
        order = {code: index for index, code in enumerate(self.codes)}
        companies_with_pending = sorted(
            (code for code, claims in pending_by_company.items() if claims),
            key=lambda code: (len(self.memory["verdicts"].get(code, [])), order[code]))
        pend_claims = [(code, pending_by_company[code][0]["id"]) for code in companies_with_pending]
        pending_count = sum(len(claims) for claims in pending_by_company.values())
        return {"loaded": self.memory["data"] is not None,
                "to_extract": [c for c in self.codes if c not in self.memory["claims"]][:10],
                "to_extract_count": len([c for c in self.codes if c not in self.memory["claims"]]),
                "claims_pending": pending_count, "next_claims": pend_claims[:5],
                "to_score": [c for c in self.codes if c not in self.memory["results"]][:10],
                "to_score_count": len([c for c in self.codes if c not in self.memory["results"]]),
                "finalized": self.memory["finalized"]}

    def is_done(self, s):
        if self.memory["finalized"]:
            return True, f"{sum(len(v) for v in self.memory['verdicts'].values())} claims investigated for {len(self.memory['results'])} companies"
        return False, ""

    def policy(self, s) -> Optional[Decision]:
        if not s["loaded"]:
            return Decision("load_inputs", {}, "read upstream outputs")
        if s["to_extract_count"]:
            return Decision("extract_claims", {"company_code": s["to_extract"][0]}, "build claims")
        if s["claims_pending"]:
            c, i = s["next_claims"][0]
            return Decision("investigate_claim", {"company_code": c, "claim_id": i}, "investigate the next claim")
        if s["to_score_count"]:
            return Decision("score_company", {"company_code": s["to_score"][0]}, "grade the signals")
        if not s["finalized"]:
            return Decision("finalize", {}, "all companies investigated")
        return None

    def check_args(self, decision, state):
        base = super().check_args(decision, state)
        if base:
            return base
        if decision.tool in ("extract_claims", "investigate_claim", "score_company") and not state["loaded"]:
            return "load_inputs must run first"
        if decision.tool == "score_company" and state["claims_pending"]:
            return "investigate all claims before scoring"
        return None
