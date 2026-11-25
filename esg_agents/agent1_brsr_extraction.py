"""Agent 1 - BRSR extraction and audit.

PRIMARY SOURCE   XBRL, read through exact BRSR concepts (engine/xbrl.py + engine/metrics.py).
EVIDENCE SOURCE  the BRSR PDF (engine/pdf.py): a value is "corroborated" when the same number is
                 printed on a page that carries the metric's anchor words.
AUDIT            filers sometimes enter a quantity in the wrong unit. Implausible intensities
                 open a scale issue; choosing the correction is a *judgement* (LLM when configured,
                 else a documented rule) and the choice is verified by code before it is applied.
Nothing is defaulted: a metric that cannot be read or verified is NOT_FOUND or QUARANTINED.

Tools: read_xbrl -> verify_pdf -> audit_company -> resolve_scale_issue -> quarantine -> finalize
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import (BASE_PROCESSED, BRSR_PDF_DIR, COMPANIES, OUTPUT_FILES, PROCESSED, ROOT, TARGET_FY,
                     TARGET_PERIOD, XBRL_RAW_DIR, clean, is_num, pd)
from .engine import audit as au
from .engine.anchors import load_anchors, polarity
from .engine.metrics import METRIC_UNITS, Extracted, extract_all
from .engine.pdf import SCALES, UNIT_TEXT_TO_SCALE, load_pages, unit_hint, verify_value
from .engine.xbrl import XbrlFiling
from .framework import BaseAgent, Decision, Tool

PDF_CACHE = ROOT / "data" / "cache" / "pdf_text"
MANUAL_OVERRIDES = ROOT / "config" / "manual_overrides.csv"

# anchor words that must appear on the PDF page carrying each absolute quantity
PDF_ANCHORS = {
    "Total Employees": [r"permanent employees"],
    "Total Energy Consumption": [r"total energy consumed", r"energy consumption"],
    "Total Water Consumption": [r"water consumption", r"total volume of water consumption"],
    "Total Waste Generated": [r"total waste generated", r"waste generated"],
    "Scope 1 and 2 Emissions": [r"scope 1", r"scope 2"],
}
# which base quantity each audited intensity depends on
QUANTITY_OF = {"Energy Intensity": "energy", "GHG Intensity": "ghg", "Water Intensity": "water"}


class BRSRExtractionAgent(BaseAgent):
    agent_id = "agent1"
    name = "Agent 1 - BRSR Extraction & Audit Agent"
    role = ("Read each company's BRSR metrics from exact XBRL concepts, corroborate them against the "
            "BRSR PDF, and audit them for plausibility before anyone else uses them.")
    goal = ("Every company with a filing has its metrics extracted, corroborated, audited and either "
            "accepted, repaired with evidence, or quarantined. No value is guessed.")
    rules = ["XBRL is the primary source; the PDF is evidence, never a silent override.",
             "A scale correction needs a plausibility reason and is verified before it is applied.",
             "When the evidence cannot decide, quarantine the value; do not invent one.",
             "Never score, rank or explain; only extract and audit."]

    def __init__(self, *args, companies: Optional[List[str]] = None, force: bool = False, **kw):
        self.target_codes = [c.upper() for c in companies] if companies else list(COMPANIES)
        unknown = [c for c in self.target_codes if c not in COMPANIES]
        if unknown:
            raise ValueError(f"Unknown company code(s): {unknown}")
        self.force = force
        self.max_steps = 80 + 14 * len(self.target_codes)
        self.anchors = load_anchors()
        self._filings: Dict[str, XbrlFiling] = {}
        self._pages: Dict[str, List[str]] = {}
        super().__init__(*args, **kw)
        self.memory.update({"status": {}, "scales": {}, "rows": {}, "issues": {}, "pdf": {},
                            "repairs": [], "timings": {}, "period": {}, "decided": {}, "finalized": False})
        self.repeatable_tools = {"audit_company"}

    # ------------------------------------------------------------------ tools
    def build_tools(self) -> List[Tool]:
        code = {"company_code": {"type": "string", "description": "Company code, e.g. INFY"}}
        return [
            Tool("read_xbrl", "Read the company's BRSR XBRL through exact concepts and build the metric rows "
                 "(also checks the reporting period).", self.t_read, code, ["company_code"]),
            Tool("verify_pdf", "Look for each absolute XBRL quantity in the BRSR PDF (page + snippet).",
                 self.t_verify, code, ["company_code"]),
            Tool("audit_company", "Plausibility audit of one company's metrics; opens scale issues for "
                 "implausible intensities and quarantines out-of-range values.", self.t_audit, code, ["company_code"]),
            Tool("resolve_scale_issue", "Decide the unit-scale correction for one open scale issue "
                 "(judgement, verified by code).", self.t_resolve,
                 {**code, "quantity": {"type": "string", "enum": ["energy", "ghg", "water", "waste", "revenue"]}},
                 ["company_code", "quantity"]),
            Tool("quarantine", "Remove a value that cannot be trusted.", self.t_quarantine,
                 {**code, "metric_name": {"type": "string"}, "reason": {"type": "string"}},
                 ["company_code", "metric_name", "reason"]),
            Tool("finalize", "Write the metric table, audit report, trends and timing files.", self.t_finalize, {}, []),
        ]

    # ----------------------------------------------------------- helpers
    def _xbrl_path(self, code: str) -> Optional[Path]:
        for pat in (f"{code}_BRSR_*.xml", f"{code}_*.xml"):
            hit = sorted(XBRL_RAW_DIR.glob(pat))
            if hit:
                return hit[0]
        return None

    def _pdf_path(self, code: str) -> Optional[Path]:
        hit = sorted(BRSR_PDF_DIR.glob(f"{code}_BR_*.pdf"))
        return hit[0] if hit else None

    def _extract(self, code: str) -> Dict[str, Extracted]:
        ex = extract_all(self._filings[code], self.memory["scales"].get(code, {}))
        if not MANUAL_OVERRIDES.exists():
            return ex
        overrides = pd.read_csv(MANUAL_OVERRIDES)
        for _, o in overrides[overrides["Company_Code"].astype(str).str.upper() == code].iterrows():
            name = str(o["Metric_Name"])
            value = o.get("Value")
            if not is_num(value):
                value = o.get("Metric_Value")
            if name not in ex or not is_num(value):
                continue
            current = ex[name]
            prior = clean(o["Prior_Year_Value"])
            unit = str(o.get("Unit", current.unit))
            value = au.manual_unit_value(name, float(value), unit)
            ex[name] = Extracted(
                name, value, current.unit, current.concept, current.context,
                current.raw, current.label_unit, derived=True, inputs=current.inputs,
                prior_value=prior if is_num(prior) else current.prior_value,
                note=f"PDF-backed manual override: {o.get('Note', o.get('Reason', ''))}",
                scale_ambiguous=False,
            )
        return ex

    def _build_rows(self, code: str, ex: Dict[str, Extracted]) -> Dict[str, Dict[str, Any]]:
        rows: Dict[str, Dict[str, Any]] = {}
        scored = self.anchors["scored_metrics"]
        old = self.memory["rows"].get(code, {})
        override_rows = {}
        if MANUAL_OVERRIDES.exists():
            ov = pd.read_csv(MANUAL_OVERRIDES)
            override_rows = {
                str(r["Metric_Name"]): r
                for _, r in ov[ov["Company_Code"].astype(str).str.upper() == code].iterrows()
            }
        for name, (cat, unit) in METRIC_UNITS.items():
            e = ex[name]
            prev = old.get(name, {})
            val = e.value
            status = "VALID" if val is not None else "NOT_FOUND"
            conf = (0.90 if e.derived else 0.95) if val is not None else 0.0
            if e.scale_ambiguous and val is not None and code not in self.memory["scales"]:
                conf = min(conf, 0.8)
            row = {
                "Company_Code": code, "Company_Name": COMPANIES[code]["full_name"], "Financial_Year": TARGET_FY,
                "Metric_Category": cat, "Metric_Name": name, "Unit": unit, "Metric_Value": val,
                "Prior_Year_Value": e.prior_value,
                "Final_Source": ("XBRL_DERIVED" if e.derived else "XBRL_EXACT") if val is not None else "NONE",
                "Confidence_Score": conf, "Validation_Status": status,
                "XBRL_Concept": e.concept, "XBRL_Context": e.context, "XBRL_Raw": e.raw, "XBRL_Unit_Label": e.label_unit,
                "Derived": e.derived, "Scored": name in scored,
                "Scale_Ambiguous": e.scale_ambiguous,
                "PDF_Status": prev.get("PDF_Status", ""), "PDF_Page": prev.get("PDF_Page"),
                "PDF_Evidence": prev.get("PDF_Evidence", ""), "Discrepancy": prev.get("Discrepancy", False),
                "Discrepancy_Reason": prev.get("Discrepancy_Reason", ""),
                "Needs_Manual_Review": prev.get("Needs_Manual_Review", False),
                "Audit_Status": prev.get("Audit_Status", "not_audited"), "Audit_Note": prev.get("Audit_Note", e.note),
                "Retrieved_At": prev.get("Retrieved_At") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            override = override_rows.get(name)
            if override is not None:
                page = override.get("PDF_Page", override.get("Source_Page"))
                note = override.get("Note", override.get("Reason", ""))
                row.update({
                    "PDF_Value": clean(override.get("PDF_Value")),
                    "PDF_Prior_Value": clean(override.get("PDF_Prior_Value")),
                    "PDF_Unit": override.get("PDF_Unit"),
                    "XBRL_Value": clean(override.get("XBRL_Value")),
                    "XBRL_Prior_Value": clean(override.get("XBRL_Prior_Value")),
                    "XBRL_Unit": override.get("XBRL_Unit"),
                    "Source_Override": override.get("Source"),
                    "Source_Page": page,
                })
                manual_value = override.get("Value")
                if not is_num(manual_value):
                    manual_value = override.get("Metric_Value")
                if is_num(manual_value):
                    row.update({
                        "Validation_Status": "REPAIRED",
                        "Final_Source": "MANUAL_PDF",
                        "Confidence_Score": 0.90,
                        "Audit_Status": "repaired",
                        "Audit_Note": str(note),
                        "PDF_Status": "MANUAL_PDF",
                        "PDF_Page": page,
                        "PDF_Evidence": str(note),
                        "Needs_Manual_Review": False,
                    })
            # carry over decisions already taken (quarantine / repair)
            if prev.get("Validation_Status") == "QUARANTINED":
                row.update({"Metric_Value": None, "Validation_Status": "QUARANTINED", "Final_Source": "NONE",
                            "Confidence_Score": 0.0, "Needs_Manual_Review": True})
            elif prev.get("Validation_Status") == "REPAIRED" and val is not None:
                row.update({"Validation_Status": "REPAIRED", "Confidence_Score": prev.get("Confidence_Score", 0.7),
                            "Final_Source": prev.get("Final_Source", row["Final_Source"])})
            rows[name] = row
        return rows

    # -------------------------------------------------------------- read
    def t_read(self, company_code: str) -> Dict[str, Any]:
        code = company_code.upper()
        t0 = time.perf_counter()
        path = self._xbrl_path(code)
        if path is None:
            self.memory["status"][code] = "no_source"
            return {"ok": True, "company": code, "status": "no_source", "message": "no XBRL filing available"}
        try:
            filing = XbrlFiling(path)
        except Exception as exc:
            self.memory["status"][code] = "no_source"
            return {"ok": False, "error": f"XBRL unreadable: {type(exc).__name__}: {exc}"}
        self._filings[code] = filing
        start, end = filing.period()
        period_ok = (start, end) == TARGET_PERIOD
        self.memory["period"][code] = {"start": start, "end": end, "matches_target": period_ok}
        if not period_ok:
            self.memory["status"][code] = "period_mismatch"
            self.add_finding("period_mismatch", "high",
                             f"{code}: filing period {start}..{end} is not FY{TARGET_FY} {TARGET_PERIOD}; not scored")
            return {"ok": True, "company": code, "status": "period_mismatch", "period": [start, end]}
        ex = self._extract(code)
        self.memory["rows"][code] = self._build_rows(code, ex)
        self.memory["status"][code] = "read"
        self.memory["timings"][code] = round(time.perf_counter() - t0, 3)
        found = sum(1 for e in ex.values() if e.found)
        return {"ok": True, "company": code, "status": "read", "metrics_found": found, "metrics_total": len(ex),
                "period": [start, end], "missing": [n for n, e in ex.items() if not e.found][:10]}

    # ------------------------------------------------------------ verify
    def _evidence_inputs(self, code: str) -> Dict[str, Tuple[float, Optional[float]]]:
        """metric -> (value to look for, as-printed raw value)"""
        f, out = self._filings[code], {}
        emp = f.get("NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Gender_PermanentEmployees_TableA")
        if emp is not None and emp.value:
            out["Total Employees"] = (emp.value, emp.value)
        for metric, concept in (("Total Energy Consumption", "TotalEnergyConsumedFromRenewableAndNonRenewableSources"),
                                ("Total Water Consumption", "TotalVolumeOfWaterConsumption"),
                                ("Total Waste Generated", "TotalWasteGenerated")):
            fx = f.get(concept)
            if fx is not None and fx.value:
                out[metric] = (fx.value, fx.value)
        s1, s2 = f.get("TotalScope1Emissions"), f.get("TotalScope2Emissions")
        if s1 is not None and s1.value:
            out["Scope 1 and 2 Emissions"] = (s1.value, s1.value)      # Scope 1 printed value
        return out

    def t_verify(self, company_code: str) -> Dict[str, Any]:
        code = company_code.upper()
        if code not in self._filings:
            return {"ok": False, "error": "read_xbrl must run first"}
        pdf = self._pdf_path(code)
        rows = self.memory["rows"][code]
        if pdf is None:
            for r in rows.values():
                r["PDF_Status"] = "NO_PDF"
            self.memory["status"][code] = "verified"
            return {"ok": True, "company": code, "pdf": None, "message": "no BRSR PDF for this company"}
        try:
            pages = load_pages(pdf, PDF_CACHE)
        except Exception as exc:
            for r in rows.values():
                r["PDF_Status"] = "PDF_UNREADABLE"
            self.memory["status"][code] = "verified"
            return {"ok": True, "company": code, "pdf": pdf.name, "warning": f"{type(exc).__name__}: {exc}"}
        self._pages[code] = pages
        summary = {"MATCH": 0, "MATCH_AFTER_SCALE": 0, "MISMATCH": 0, "NOT_FOUND": 0}
        ev_store: Dict[str, Any] = {}
        for metric, (val, raw) in self._evidence_inputs(code).items():
            ev = verify_value(pages, val, PDF_ANCHORS.get(metric, []), raw_value=raw)
            summary[ev.status] = summary.get(ev.status, 0) + 1
            ev_store[metric] = {"status": ev.status, "page": ev.page, "scale": ev.scale,
                                "snippet": ev.snippet[:220], "pdf_numbers": ev.pdf_numbers[:6]}
            r = rows[metric]
            r["PDF_Status"], r["PDF_Page"], r["PDF_Evidence"] = ev.status, ev.page, ev.snippet[:220]
            if ev.status in ("MATCH", "MATCH_AFTER_SCALE") and r["Metric_Value"] is not None:
                r["Confidence_Score"] = min(0.99, r["Confidence_Score"] + 0.04)
            if ev.status == "MISMATCH":
                r["Discrepancy"], r["Discrepancy_Reason"] = True, "XBRL_PDF_VALUE_MISMATCH"
                r["Needs_Manual_Review"] = True
                r["Confidence_Score"] = min(r["Confidence_Score"], 0.7)
        self.memory["pdf"][code] = ev_store
        self.memory["status"][code] = "verified"
        return {"ok": True, "company": code, "pdf": pdf.name, "pages": len(pages), **summary}

    # ------------------------------------------------------------- audit
    def _emp(self, code: str) -> Optional[float]:
        r = self.memory["rows"].get(code, {}).get("Total Employees")
        return r["Metric_Value"] if r and is_num(r.get("Metric_Value")) else None

    def _apply_pdf_repair(self, code: str, metric: str, scale: float,
                          pdf_value: au.PdfMetricValue) -> None:
        rows = self.memory["rows"][code]
        old_rows = rows.copy()
        quantity = {
            "Total Energy Consumption": "energy",
            "Total Water Consumption": "water",
            "Total Waste Generated": "waste",
            "Scope 1 and 2 Emissions": "ghg",
        }.get(metric)
        if quantity:
            scales = self.memory["scales"].setdefault(code, {})
            scales[quantity] = scales.get(quantity, 1.0) * scale
            rebuilt = self._build_rows(code, self._extract(code))
            self.memory["rows"][code] = rebuilt
            for name, row in rebuilt.items():
                previous = old_rows[name].get("Metric_Value")
                if is_num(previous) and is_num(row.get("Metric_Value")) and not math.isclose(
                        float(previous), float(row["Metric_Value"]), rel_tol=1e-12):
                    row.update({
                        "Validation_Status": "REPAIRED",
                        "Final_Source": "XBRL_RESCALED_PDF",
                        "Confidence_Score": 0.90,
                        "Audit_Status": "repaired",
                        "Needs_Manual_Review": False,
                        "Audit_Note": f"recomputed after PDF ratio repair of {metric}",
                    })
            cleared_ambiguity = {
                "energy": ("Total Energy Consumption", "Energy Intensity", "Renewable Energy Percentage"),
                "water": ("Total Water Consumption", "Water Intensity"),
                "waste": ("Total Waste Generated", "Waste Recovered Percentage"),
                "ghg": ("Scope 1 and 2 Emissions", "GHG Intensity"),
            }[quantity]
            for name in cleared_ambiguity:
                rebuilt[name]["Scale_Ambiguous"] = False
        else:
            row = rows[metric]
            row["Metric_Value"] = float(row["Metric_Value"]) * scale
            row.update({
                "Validation_Status": "REPAIRED",
                "Final_Source": "XBRL_RESCALED_PDF",
                "Confidence_Score": 0.90,
                "Audit_Status": "repaired",
                "Needs_Manual_Review": False,
                "Audit_Note": f"rescaled by PDF ratio x{scale:g}",
            })

        row = self.memory["rows"][code][metric]
        row.update({
            "Metric_Value": pdf_value.value,
            "Validation_Status": "REPAIRED",
            "Final_Source": "XBRL_RESCALED_PDF",
            "Confidence_Score": 0.90,
            "Audit_Status": "repaired",
            "Scale_Ambiguous": False,
            "PDF_Status": "MATCH_AFTER_SCALE",
            "PDF_Page": pdf_value.page,
            "PDF_Evidence": pdf_value.evidence,
            "Needs_Manual_Review": False,
            "Audit_Note": f"rescaled by PDF ratio x{scale:g}; PDF value={pdf_value.value:g}",
        })
        self.memory["repairs"].append({"company": code, "metric": metric, "scale": scale,
                                       "source": "pdf_ratio", "pdf_page": pdf_value.page})

    def _proposed_scale_checks(self, code: str, quantity: str, scale: float) -> Tuple[bool, str]:
        current = self.memory["scales"].get(code, {})
        proposed_scales = {**current, quantity: current.get(quantity, 1.0) * scale}
        proposed = extract_all(self._filings[code], proposed_scales)
        value_map = {name: item.value for name, item in proposed.items()}
        relevant = {
            "energy": ("Total Energy Consumption", "Energy Intensity"),
            "water": ("Total Water Consumption", "Water Intensity"),
            "ghg": ("Scope 1 and 2 Emissions", "GHG Intensity"),
            "waste": ("Total Waste Generated",),
        }.get(quantity, ())
        for name in relevant:
            ok, reason = au.plausible(name, value_map.get(name))
            if not ok:
                return False, f"{name}: {reason}"
        if quantity == "revenue":
            revenue = None
            for concept in ("RevenueFromOperations", "TotalRevenueOfTheCompany", "Turnover"):
                fact = self._filings[code].get(concept)
                if fact is not None and fact.value and fact.value > 0:
                    revenue = fact.value
                    break
            if revenue and not au.revenue_plausible(revenue * proposed_scales["revenue"], value_map.get("Total Employees")):
                return False, "revenue per employee remains implausible"
        passed, reason = au.company_cross_checks(value_map, value_map.get("Total Employees"))
        return passed, reason

    def t_audit(self, company_code: str) -> Dict[str, Any]:
        code = company_code.upper()
        if code not in self.memory["rows"]:
            return {"ok": False, "error": "read_xbrl must run first"}
        rows, issues = self.memory["rows"][code], []
        audit_metrics = (
            "Total Energy Consumption", "Energy Intensity",
            "Total Water Consumption", "Water Intensity",
            "Total Waste Generated", "Scope 1 and 2 Emissions", "GHG Intensity",
        )
        for metric in audit_metrics:
            row = self.memory["rows"][code][metric]
            if not is_num(row.get("Metric_Value")) or row.get("Validation_Status") == "QUARANTINED":
                continue
            plausible, why = au.plausible(metric, row["Metric_Value"])
            if plausible and not row.get("Scale_Ambiguous"):
                continue
            if row.get("Final_Source") == "MANUAL_PDF":
                continue
            if why.startswith("NOT_REPORTED:"):
                self._quarantine_row(code, metric, why)
                continue
            pdf_value = au.pdf_metric_value(self._pages.get(code, []), metric)
            scale = au.power_of_ten_scale(row["Metric_Value"], pdf_value.value) if pdf_value else None
            if pdf_value is not None and scale is not None:
                if not math.isclose(scale, 1.0, rel_tol=1e-12):
                    self._apply_pdf_repair(code, metric, scale, pdf_value)
                else:
                    row["PDF_Page"], row["PDF_Evidence"] = pdf_value.page, pdf_value.evidence
                    row["PDF_Status"] = "MATCH"
                    row["Scale_Ambiguous"] = False
                continue
            if metric in ("Energy Intensity", "Water Intensity", "GHG Intensity"):
                if pdf_value is not None:
                    self._quarantine_row(code, metric, "PDF value found but XBRL/PDF ratio is not within 0.5% of a power of ten")
                elif row.get("Scale_Ambiguous") and plausible:
                    qty = QUANTITY_OF[metric]
                    if any(issue["quantity"] == qty for issue in issues):
                        continue
                    base_name = {"energy": "Total Energy Consumption", "water": "Total Water Consumption",
                                 "ghg": "Scope 1 and 2 Emissions"}[qty]
                    candidates = au.intensity_scale_candidates(
                        metric, lambda candidate, current=row: current["Metric_Value"] * candidate)
                    candidates = [candidate for candidate in candidates if not math.isclose(candidate, 1.0)]
                    issues.append({"quantity": qty, "metric": metric, "reason": "ambiguous XBRL unit; PDF value unavailable",
                                   "candidates": candidates, "base_metric": base_name})
                continue
            if metric in ("Total Energy Consumption", "Total Water Consumption", "Total Waste Generated",
                          "Scope 1 and 2 Emissions"):
                qty = {"Total Energy Consumption": "energy", "Total Water Consumption": "water",
                       "Total Waste Generated": "waste", "Scope 1 and 2 Emissions": "ghg"}[metric]
                candidates = au.total_scale_candidates(metric, row["Metric_Value"])
                if candidates:
                    if not any(issue["quantity"] == qty for issue in issues):
                        issues.append({"quantity": qty, "metric": metric,
                                       "reason": f"PDF value unavailable; {why or 'ambiguous XBRL unit'}",
                                       "candidates": candidates, "base_metric": metric})
                else:
                    self._quarantine_row(code, metric, f"PDF value unavailable; {why or 'ambiguous XBRL unit'}")
                continue
            if not plausible or row.get("Scale_Ambiguous"):
                reason = (f"PDF value found but XBRL/PDF ratio is not within 0.5% of a power of ten"
                          if pdf_value is not None else f"PDF value unavailable; {why or 'ambiguous XBRL unit'}")
                self._quarantine_row(code, metric, reason)

        rows = self.memory["rows"][code]
        emp = self._emp(code)
        rev = None
        for c in ("RevenueFromOperations", "TotalRevenueOfTheCompany", "Turnover"):
            fx = self._filings[code].get(c)
            if fx is not None and fx.value and fx.value > 0:
                rev = fx.value
                break
        sc = self.memory["scales"].get(code, {})
        decided = self.memory["decided"].setdefault(code, set())
        if rev and emp and not au.revenue_plausible(rev * sc.get("revenue", 1.0), emp) and "revenue" not in decided:
            cands = au.revenue_scale_candidates(rev, emp)
            issues.append({"quantity": "revenue", "metric": "GHG Intensity", "reason":
                           f"revenue INR {rev:,.0f} for {emp:,.0f} employees is implausible", "candidates": cands})
        for metric, qty in QUANTITY_OF.items():
            r = rows[metric]
            if not is_num(r["Metric_Value"]) or r["Validation_Status"] == "QUARANTINED":
                continue
            ok, why = au.plausible(metric, r["Metric_Value"])
            if not ok and qty in decided and qty not in sc:
                self._quarantine_row(code, metric, f"scale for {qty} could not be established: {why}")
                continue
            if ok:
                if r["Audit_Status"] == "not_audited":
                    r["Audit_Status"] = "passed"
                continue
            if qty == "ghg" and any(i["quantity"] == "revenue" for i in issues):
                continue                                       # fix revenue first, then re-audit
            if "revenue" in decided and qty == "ghg" and "revenue" not in sc and not au.revenue_plausible(rev or 0, emp or 0):
                self._quarantine_row(code, metric, "revenue unit could not be established, so GHG intensity is unreliable")
                continue
            if qty in sc:
                self._quarantine_row(code, metric, f"still implausible after scale {sc[qty]:g}: {why}")
                continue
            base = {"energy": rows["Total Energy Consumption"], "water": rows["Total Water Consumption"],
                    "ghg": rows["Scope 1 and 2 Emissions"]}[qty]
            cands = au.intensity_scale_candidates(metric, lambda s, r=r: r["Metric_Value"] * s)
            if not any(issue["quantity"] == qty for issue in issues):
                issues.append({"quantity": qty, "metric": metric, "reason": why, "candidates": cands,
                               "base_metric": base["Metric_Name"]})
        # rounding tolerance: recovered waste may exceed generated waste by rounding only
        w_ = rows["Waste Recovered Percentage"]
        if is_num(w_["Metric_Value"]) and 100 < w_["Metric_Value"] <= 100.5:
            w_["Audit_Note"] = f"value {w_['Metric_Value']:.3f}% clipped to 100% (rounding of reported tonnes)"
            w_["Metric_Value"], w_["Audit_Status"] = 100.0, "passed"
        # hard range checks on everything else
        for name, r in rows.items():
            if name in QUANTITY_OF or not is_num(r["Metric_Value"]) or r["Validation_Status"] == "QUARANTINED":
                continue
            if any(issue["metric"] == name for issue in issues):
                continue
            ok, why = au.plausible(name, r["Metric_Value"])
            if not ok:
                self._quarantine_row(code, name, why)
            elif r["Audit_Status"] == "not_audited":
                r["Audit_Status"] = "passed"
        wr = rows["Waste Recovered Percentage"]
        if is_num(wr["Metric_Value"]) and wr["Metric_Value"] > 100.5:
            self._quarantine_row(code, "Waste Recovered Percentage", "recovered waste exceeds generated waste")
        self.memory["issues"][code] = issues
        self.memory["status"][code] = "audited"
        return {"ok": True, "company": code, "open_issues": [{"quantity": i["quantity"], "metric": i["metric"],
                "reason": i["reason"], "candidates": i["candidates"]} for i in issues]}

    def _quarantine_row(self, code: str, metric: str, reason: str) -> None:
        r = self.memory["rows"][code][metric]
        r.update({"Audit_Note": f"quarantined: {reason}; removed value={r['Metric_Value']}", "Metric_Value": None,
                  "Validation_Status": "NOT_REPORTED" if reason.startswith("NOT_REPORTED:") else "QUARANTINED",
                  "Audit_Status": "not_reported" if reason.startswith("NOT_REPORTED:") else "quarantined",
                  "Final_Source": "NONE",
                  "Confidence_Score": 0.0, "Needs_Manual_Review": True})

    def t_quarantine(self, company_code: str, metric_name: str, reason: str) -> Dict[str, Any]:
        code = company_code.upper()
        if metric_name not in self.memory["rows"].get(code, {}):
            return {"ok": False, "error": f"no such metric row {code}/{metric_name}"}
        self._quarantine_row(code, metric_name, reason)
        for dep in ("Energy Intensity", "Water Intensity", "GHG Intensity"):
            pass
        return {"ok": True, "quarantined": metric_name}

    # --------------------------------------------------- scale judgement
    def _pdf_scale_evidence(self, code: str, qty: str) -> Dict[str, Any]:
        key = {"energy": "Total Energy Consumption", "water": "Total Water Consumption", "ghg": "Scope 1 and 2 Emissions"}.get(qty)
        ev = self.memory["pdf"].get(code, {}).get(key) if key else None
        return ev or {}

    def _hint_scale(self, code: str, qty: str) -> Tuple[Optional[float], Optional[str]]:
        """Scale implied by the unit the PDF prints next to the quantity (e.g. '(GJ)'), relative to the
        unit label the XBRL filing used. None when the PDF does not state a unit."""
        from .engine.metrics import _ENERGY_TO_KWH
        pages = self._pages.get(code)
        if not pages:
            return None, None
        if qty == "energy":
            hint = unit_hint(pages, PDF_ANCHORS["Total Energy Consumption"], "energy")
            fx = self._filings[code].get("TotalEnergyConsumedFromRenewableAndNonRenewableSources")
            if hint and fx is not None:
                label = self._filings[code].unit_label(fx).split(":")[-1].strip().lower()
                if hint.lower() in _ENERGY_TO_KWH and label in _ENERGY_TO_KWH:
                    return _ENERGY_TO_KWH[hint.lower()] / _ENERGY_TO_KWH[label], hint
            return None, hint
        if qty == "revenue":
            hint = unit_hint(pages, [r"turnover", r"total revenue", r"revenue from operations"], "revenue")
            return (UNIT_TEXT_TO_SCALE.get(hint.lower()), hint) if hint else (None, None)
        return None, None

    def t_resolve(self, company_code: str, quantity: str) -> Dict[str, Any]:
        code = company_code.upper()
        issue = next((i for i in self.memory["issues"].get(code, []) if i["quantity"] == quantity), None)
        if issue is None:
            return {"ok": False, "error": f"no open {quantity} scale issue for {code}"}
        cands = list(issue["candidates"])
        metric_for_quantity = {
            "energy": "Total Energy Consumption", "ghg": "Scope 1 and 2 Emissions",
            "water": "Total Water Consumption", "waste": "Total Waste Generated",
        }.get(quantity)
        base_row = self.memory["rows"][code].get(metric_for_quantity, {}) if metric_for_quantity else {}
        pdf_value = au.pdf_metric_value(self._pages.get(code, []), metric_for_quantity) if metric_for_quantity else None
        pdf_scale = (au.power_of_ten_scale(base_row.get("Metric_Value"), pdf_value.value)
                     if pdf_value is not None else None)
        evidence = {"key": f"{code}/{quantity}", "company": COMPANIES[code]["full_name"], "quantity": quantity,
                    "metric_affected": issue["metric"], "problem": issue["reason"],
                    "candidate_scales": cands, "employees": self._emp(code),
                    "filing_unit_label": base_row.get("XBRL_Unit_Label", ""),
                    "pdf_evidence": ({"page": pdf_value.page, "value": pdf_value.value,
                                      "quote": pdf_value.evidence} if pdf_value else {}),
                    "pdf_scale": pdf_scale}

        def verify(ans, ev):
            ch = ans.get("choice")
            if ch == "quarantine":
                return True, {"choice": "quarantine", "reason": str(ans.get("reason", ""))[:300]}, ""
            try:
                ch = float(ch)
            except (TypeError, ValueError):
                return False, None, "choice must be one of the candidate scales or 'quarantine'"
            if not any(math.isclose(ch, c, rel_tol=1e-9) for c in ev["candidate_scales"]):
                return False, None, f"scale {ch} is not a plausible candidate {ev['candidate_scales']}"
            if math.isclose(ch, 1.0):
                return False, None, "a no-op scale does not resolve an ambiguous or implausible value"
            if ev["pdf_evidence"]:
                if ev["pdf_scale"] is None:
                    return False, None, "PDF value exists, but its ratio to XBRL is not a power of ten within 0.5%"
                if not math.isclose(ch, ev["pdf_scale"], rel_tol=1e-6):
                    return False, None, f"PDF ratio requires scale {ev['pdf_scale']:g}"
            else:
                passed, reason = self._proposed_scale_checks(code, quantity, ch)
                if not passed:
                    return False, None, reason or "candidate fails company cross-checks"
            return True, {"choice": ch, "reason": str(ans.get("reason", ""))[:300]}, ""

        def fallback(ev):
            cs, pdf_scale = ev["candidate_scales"], ev["pdf_scale"]
            if ev["pdf_evidence"] and pdf_scale is not None and any(math.isclose(pdf_scale, c, rel_tol=1e-6) for c in cs):
                return {"choice": pdf_scale, "reason": "scale confirmed by the PDF numeric ratio"}
            valid = [candidate for candidate in cs
                     if self._proposed_scale_checks(code, quantity, candidate)[0]]
            if not ev["pdf_evidence"] and len(valid) == 1:
                return {"choice": valid[0], "reason": "the only candidate passing plausibility and company cross-checks"}
            return {"choice": "quarantine", "reason": ("no scale passes PDF, plausibility, and company cross-checks"
                                                         if cs else "no rescaling makes the value plausible")}

        if self.brain.available:
            ans = self.judge(
                "propose the unit-scale correction for a mis-scaled BRSR quantity",
                "Propose a candidate scale using only the supplied evidence. The code verifier decides whether it is accepted. "
                "Answer JSON: " '{"choice": <candidate scale or "quarantine">, "reason": "<one sentence>"}.',
                evidence, verify, fallback)
        else:
            ans = {**fallback(evidence), "_source": "rule", "_reason": "offline deterministic decision"}
        self.memory["decided"].setdefault(code, set()).add(quantity)
        if ans["choice"] == "quarantine":
            self._quarantine_row(code, issue["metric"], f"scale unresolved: {ans['reason']}")
            self.memory["issues"][code] = [i for i in self.memory["issues"][code] if i is not issue]
            return {"ok": True, "quantity": quantity, "decision": "quarantine", "source": ans["_source"]}
        scale = float(ans["choice"])
        current_scale = self.memory["scales"].setdefault(code, {}).get(quantity, 1.0)
        self.memory["scales"][code][quantity] = current_scale * scale
        ex = self._extract(code)
        old_rows = self.memory["rows"][code]
        new_rows = self._build_rows(code, ex)
        unverified_llm = ans["_source"] == "llm" and pdf_value is None
        for name, row in new_rows.items():
            if row["Metric_Value"] != old_rows[name]["Metric_Value"] and old_rows[name]["Validation_Status"] != "QUARANTINED":
                row.update({"Validation_Status": "REPAIRED",
                            "Audit_Status": "UNVERIFIED_LLM" if unverified_llm else "repaired",
                            "Final_Source": "XBRL_RESCALED_LLM" if unverified_llm else
                                            "XBRL_RESCALED_PDF" if pdf_value else "XBRL_RESCALED",
                            "Confidence_Score": 0.90 if pdf_value or unverified_llm else 0.85,
                            "Needs_Manual_Review": unverified_llm,
                            "Scored": row["Scored"] and not unverified_llm,
                            "Audit_Note": f"{quantity} rescaled x{scale:g} ({ans['_source']}): {ans['reason']}; "
                                          f"original={old_rows[name]['Metric_Value']}"})
                if name == issue["metric"] and pdf_value is not None:
                    row.update({"PDF_Page": pdf_value.page, "PDF_Evidence": pdf_value.evidence,
                                "PDF_Status": "MATCH_AFTER_SCALE", "Scale_Ambiguous": False})
        self.memory["rows"][code] = new_rows
        self.memory["repairs"].append({"company": code, "quantity": quantity, "scale": scale,
                                       "source": ans["_source"], "reason": ans["reason"]})
        self.memory["issues"][code] = [i for i in self.memory["issues"][code] if i is not issue]
        return {"ok": True, "quantity": quantity, "decision": scale, "source": ans["_source"]}

    # ------------------------------------------------------------ finalize
    def _trends(self) -> List[Dict[str, Any]]:
        out = []
        for code, rows in self.memory["rows"].items():
            for name, r in rows.items():
                cur, pri = r["Metric_Value"], r["Prior_Year_Value"]
                if not (is_num(cur) and is_num(pri)):
                    continue
                pol = polarity(name, self.anchors)
                comparable, note = True, ""
                if name in ("Total Energy Consumption", "Scope 1 and 2 Emissions", "Total Water Consumption",
                            "Total Waste Generated", "Energy Intensity", "GHG Intensity", "Water Intensity"):
                    if pri > 0 and not (1 / 3 <= cur / pri <= 3):
                        comparable, note = False, "more than 3x change between years: boundary/definition change suspected"
                change = (cur - pri) / abs(pri) * 100 if pri else None
                abs_change = cur - pri
                # How the change is SHOWN and judged depends on the kind of metric, so that a move from
                # 0.01 to 0.03 on a metric whose whole scale is 0..1 is not reported as "+177.8%":
                #   percentage metrics -> percentage points (>= 0.5 pp is material)
                #   linear scored metrics (rates, counts, flags) -> absolute change (>= 5% of the anchor span)
                #   totals and log-scale intensities -> relative change (>= 2%)
                unit = METRIC_UNITS.get(name, ("", ""))[1]
                spec = self.anchors["scored_metrics"].get(name)
                base_small = False
                if unit == "%":
                    mode, disp, material = "pp", abs_change, abs(abs_change) >= 0.5
                elif spec and spec.get("scale") != "log":
                    span = abs(spec["anchor_10"] - spec["anchor_0"]) or 1.0
                    mode, disp, material = "abs", abs_change, abs(abs_change) / span >= 0.05
                    base_small = abs(pri) < 0.05 * span
                else:
                    mode, disp = "rel", change
                    material = change is not None and abs(change) >= 2
                direction = "flat"
                if material and abs_change != 0:
                    better = (abs_change > 0) == (pol == "higher")
                    direction = "improved" if better else "worsened"
                out.append({"Company_Code": code, "Metric_Name": name, "Current": cur, "Prior": pri,
                            "Change_Pct": change, "Change_Value": disp, "Change_Mode": mode, "Unit": unit,
                            "Base_Small": base_small, "Direction": direction, "Better_When": pol,
                            "Comparable": comparable, "Comparability_Note": note})
        return out

    def t_finalize(self) -> Dict[str, Any]:
        all_rows, audit_rows = [], []
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        for code in self.target_codes:
            st = self.memory["status"].get(code)
            rows = self.memory["rows"].get(code)
            if not rows:
                for name, (cat, unit) in METRIC_UNITS.items():
                    all_rows.append({"Run_ID": run_id, "Company_Code": code, "Company_Name": COMPANIES[code]["full_name"],
                                     "Financial_Year": TARGET_FY, "Metric_Category": cat, "Metric_Name": name, "Unit": unit,
                                     "Metric_Value": None, "Validation_Status": "NOT_FOUND", "Final_Source": "NONE",
                                     "Needs_Manual_Review": True, "Audit_Status": st or "no_source",
                                     "Audit_Note": {"period_mismatch": "filing period does not match the target financial year",
                                                    }.get(st, "no BRSR XBRL filing available"),
                                     "Scored": name in self.anchors["scored_metrics"]})
                continue
            for r in rows.values():
                r["Run_ID"] = run_id
                all_rows.append(r)
                if r["Audit_Status"] not in ("passed", "not_audited") or r["Discrepancy"]:
                    audit_rows.append({k: r.get(k) for k in ("Company_Code", "Metric_Name", "Metric_Value", "Final_Source",
                                       "Validation_Status", "PDF_Status", "Audit_Status", "Audit_Note")})
        df = pd.DataFrame(all_rows)
        path = PROCESSED / OUTPUT_FILES["brsr_metrics"]
        df.to_csv(path, index=False)
        pd.DataFrame(audit_rows).to_csv(PROCESSED / "agent1_audit_report.csv", index=False)
        pd.DataFrame(self._trends()).to_csv(PROCESSED / "brsr_trends.csv", index=False)
        pd.DataFrame([{"Company_Code": c, "Seconds": s} for c, s in self.memory["timings"].items()]).to_csv(
            PROCESSED / "agent1_extraction_timing.csv", index=False)
        pd.DataFrame([{"Company_Code": c, **p} for c, p in self.memory["period"].items()]).to_csv(
            PROCESSED / "filing_periods.csv", index=False)
        scored = df[df["Scored"] == True]  # noqa: E712
        per = scored.groupby("Company_Code")["Metric_Value"].apply(lambda s: int(s.notna().sum()))
        cov = {"companies": int(df["Company_Code"].nunique()), "mean_scored_metrics_per_company": round(float(per.mean()), 2) if len(per) else 0,
               "companies_below_6_scored_metrics": sorted(per[per < 6].index.tolist()), "repairs": len(self.memory["repairs"]),
               "pdf_corroborated_values": int(df["PDF_Status"].isin(["MATCH", "MATCH_AFTER_SCALE"]).sum()),
               "pdf_mismatches": int((df["PDF_Status"] == "MISMATCH").sum())}
        self.board.post(self.agent_id, "brsr_coverage", cov)
        self.memory["finalized"] = True
        self.artifacts += [str(path.relative_to(path.parents[1]))]
        return {"ok": True, "rows": len(df), "coverage": cov}

    # ------------------------------------------------------------ reasoning
    def observe(self) -> Dict[str, Any]:
        st = self.memory["status"]
        pend = [c for c in self.target_codes if c not in st]
        return {"companies_total": len(self.target_codes), "pending_count": len(pend), "pending": pend[:10],
                "read_not_verified": [c for c in self.target_codes if st.get(c) == "read"][:10],
                "verified_not_audited": [c for c in self.target_codes if st.get(c) == "verified"][:10],
                "open_issues": {c: [i["quantity"] for i in v] for c, v in self.memory["issues"].items() if v},
                "no_source": [c for c in self.target_codes if st.get(c) in ("no_source", "period_mismatch")],
                "repairs_done": len(self.memory["repairs"]), "finalized": self.memory["finalized"]}

    def is_done(self, state):
        if self.memory["finalized"]:
            return True, "all companies extracted, corroborated and audited; files written"
        return False, ""

    def policy(self, state):
        st = self.memory["status"]
        for c in self.target_codes:
            s = st.get(c)
            if s is None:
                return Decision("read_xbrl", {"company_code": c}, "extract exact XBRL concepts", "policy")
        for c in self.target_codes:
            if st.get(c) == "read":
                return Decision("verify_pdf", {"company_code": c}, "corroborate against the PDF", "policy")
        for c in self.target_codes:
            if st.get(c) == "verified":
                return Decision("audit_company", {"company_code": c}, "plausibility audit", "policy")
        for c in self.target_codes:
            for i in self.memory["issues"].get(c, []):
                return Decision("resolve_scale_issue", {"company_code": c, "quantity": i["quantity"]},
                                f"resolve open {i['quantity']} scale issue", "policy")
        if not self.memory["finalized"]:
            return Decision("finalize", {}, "all companies audited", "policy")
        return None

    def reflect(self, decision, result):
        if decision.tool == "resolve_scale_issue" and result.get("ok"):
            self.memory["status"][decision.args["company_code"].upper()] = "verified"   # re-audit after a repair

    def check_args(self, decision, state):
        base = super().check_args(decision, state)
        if base:
            return base
        code = str(decision.args.get("company_code", "")).upper()
        st = self.memory["status"]
        if decision.tool == "finalize" and (state["pending_count"] or state["open_issues"]
                                            or state["read_not_verified"] or state["verified_not_audited"]):
            return "cannot finalize: companies or issues still open"
        if decision.tool == "verify_pdf" and st.get(code) != "read":
            return "verify_pdf needs a freshly read company"
        if decision.tool == "audit_company" and st.get(code) not in ("verified",):
            return "audit_company needs a verified company"
        if decision.tool == "resolve_scale_issue" and not any(
                i["quantity"] == decision.args.get("quantity") for i in self.memory["issues"].get(code, [])):
            return "no such open scale issue"
        return None
