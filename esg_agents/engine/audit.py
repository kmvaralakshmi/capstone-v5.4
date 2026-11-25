"""Plausibility audit and scale repair for extracted BRSR metrics (deterministic).

Filers sometimes enter a quantity in the wrong unit (revenue in crore, energy in joules ...). The
XBRL unit label cannot be trusted in those cases, so each absolute quantity is checked through the
intensity it implies. Candidate rescalings are generated here; choosing among several plausible
candidates is a judgement made by Agent 1 and verified by code.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .pdf import _NUM, _to_float

# Hard physical / definitional bounds of the *final* metric value.
BOUNDS: Dict[str, Tuple[float, float]] = {
    "Total Energy Consumption": (0.01, 5000),
    "Total Water Consumption": (1.0, 1.0e8),
    "Total Waste Generated": (0.1, 1.0e6),
    "Scope 1 and 2 Emissions": (1.0, 5.0e6),
    "Renewable Energy Percentage": (0, 100), "Waste Recovered Percentage": (0, 100),
    "Female Employees Percentage": (0, 100), "Employee Turnover Rate": (0, 200),
    "Human Rights Training Coverage": (0, 105), "Differently Abled Employees Percentage": (0, 100),
    "Female Directors Percentage": (0, 100), "Lost Time Injury Frequency Rate": (0, 100),
    # Wide on purpose: asset-heavy firms (towers, logistics) legitimately have very high per-employee
    # intensity, so only gross unit errors (orders of magnitude) are treated as implausible.
    "Energy Intensity": (1.0, 1.0e7),         # kWh per permanent employee
    "Water Intensity": (0.005, 5.0e4),        # KL per employee
    "GHG Intensity": (0.0005, 5.0e4),         # tCO2e per INR crore revenue
    "Total Employees": (20, 3.0e6), "Board Size": (3, 30),
    "Board and KMP Disciplinary Actions": (0, 1e4), "Board and KMP Conflict of Interest Complaints": (0, 1e4),
    "Anti-Corruption Policy": (0, 1), "Cybersecurity and Data Privacy Policy": (0, 1), "Board Sustainability Committee": (0, 1),
    "Employee Wellbeing Spend": (0, 50),
}
REVENUE_PER_EMPLOYEE_CRORE = (0.002, 60.0)

CANDIDATE_SCALES = (1e-9, 1e-7, 1e-6, 1e-5, 1e-3, 1.0, 1e3, 1e5, 1e6, 1e7, 1e9)

# base quantity -> the metric whose plausibility reveals a mis-scaled input
SCALE_TARGETS = {"energy": "Energy Intensity", "ghg": "GHG Intensity", "water": "Water Intensity"}


def plausible(metric: str, value: Optional[float]) -> Tuple[bool, str]:
    if value is None:
        return True, ""
    if metric in ("Total Water Consumption", "Total Waste Generated", "Scope 1 and 2 Emissions") and value == 0:
        return False, "NOT_REPORTED: exact zero is not a reported positive quantity"
    lo, hi = BOUNDS.get(metric, (float("-inf"), float("inf")))
    if value < lo or value > hi:
        return False, f"{value:.6g} outside plausible range [{lo:g}, {hi:g}]"
    return True, ""


def revenue_plausible(revenue_inr: Optional[float], employees: Optional[float]) -> bool:
    if not revenue_inr or not employees:
        return True
    per = revenue_inr / 1e7 / employees
    return REVENUE_PER_EMPLOYEE_CRORE[0] <= per <= REVENUE_PER_EMPLOYEE_CRORE[1]


def revenue_scale_candidates(revenue_inr: float, employees: float) -> List[float]:
    return [s for s in (1.0, 1e3, 1e5, 1e6, 1e7, 1e9) if revenue_plausible(revenue_inr * s, employees)]


def intensity_scale_candidates(metric: str, evaluate) -> List[float]:
    """`evaluate(scale)` returns the intensity for a candidate scale. Plausible scales are returned
    ordered by closeness to 1 (smallest correction first)."""
    ok = []
    for s in CANDIDATE_SCALES:
        v = evaluate(s)
        if v is not None and plausible(metric, v)[0]:
            ok.append(s)
    return sorted(ok, key=lambda s: abs(__import__("math").log10(s)))


def total_scale_candidates(metric: str, value: Optional[float]) -> List[float]:
    """Power-of-ten corrections that put an absolute quantity inside its hard bounds."""
    if value is None:
        return []
    return [scale for scale in CANDIDATE_SCALES
            if not math.isclose(scale, 1.0) and plausible(metric, value * scale)[0]]


@dataclass(frozen=True)
class PdfMetricValue:
    value: float
    page: int
    evidence: str


_PDF_ROW_LABELS = {
    "Total Energy Consumption": re.compile(r"total energy consumed\s*\(a\s*\+\s*b\s*\+\s*c\s*\+\s*d\s*\+\s*e\s*\+\s*f\s*\)", re.I),
    "Total Water Consumption": re.compile(r"total volume of water consumption", re.I),
    "Total Waste Generated": re.compile(r"total\s*\(\s*a\s*\+\s*b\s*\+\s*c\s*\+\s*d\s*\+\s*e\s*\+\s*f\s*\+\s*g\s*\+\s*h\s*\)", re.I),
    "Energy Intensity": re.compile(r"energy intensity in terms of physical output", re.I),
    "Water Intensity": re.compile(r"water intensity in terms of physical output", re.I),
    "GHG Intensity": re.compile(r"total scope 1 and scope 2 emission intensity per rupee of turnover", re.I),
}


def _following_number(lines: List[str], index: int, lookahead: int = 6,
                      allow_energy_unit: bool = False) -> Optional[float]:
    for line in lines[index + 1:index + lookahead + 1]:
        if re.search(r"[A-Za-z]", line) and not (
                allow_energy_unit and re.fullmatch(r"\s*[\d,]+(?:\.\d+)?\s*(?:GJ|MJ|TJ|kWh|MWh|GWh|kJ)\s*", line, re.I)):
            continue
        for token in _NUM.findall(line):
            number = _to_float(token)
            if number is not None:
                return number
    return None


def _unit_multiplier(metric: str, text: str) -> float:
    unit_text = text.lower().replace(",", "")
    if metric == "Total Energy Consumption":
        if re.search(r"\bTJ\b", unit_text, re.I):
            return 1e6 / 3600
        if re.search(r"\bGJ\b", unit_text, re.I):
            return 1 / 3600
        if re.search(r"\bMJ\b", unit_text, re.I):
            return 1 / 3.6e6
        if re.search(r"\bkJ\b", unit_text, re.I):
            return 1 / 3.6e9
        if re.search(r"\bkWh\b", unit_text, re.I):
            return 1 / 1e6
        if re.search(r"\bMWh\b", unit_text, re.I):
            return 1 / 1e3
        return 1.0
    if metric == "Energy Intensity":
        if re.search(r"\bGJ\b", unit_text, re.I):
            return 1000 / 3.6
        if re.search(r"\bMJ\b", unit_text, re.I):
            return 1 / 3.6
        return 1.0
    if metric == "Total Water Consumption" and re.search(r"\b(?:litres?|l)\b", unit_text, re.I):
        return 1e-3
    if metric == "Total Waste Generated" and re.search(r"\bkg\b", unit_text, re.I):
        return 1e-3
    return 1.0


def pdf_metric_value(pages: List[str], metric: str) -> Optional[PdfMetricValue]:
    """Read a metric value from its labeled PDF row and convert it to the canonical metric unit."""
    if metric == "Scope 1 and 2 Emissions":
        components = []
        for scope in (1, 2):
            label = re.compile(rf"total scope {scope} emissions\b", re.I)
            found = None
            for page_no, text in enumerate(pages, start=1):
                lines = text.splitlines()
                for index, line in enumerate(lines):
                    if label.search(line):
                        value = _following_number(lines, index)
                        if value is not None:
                            excerpt = " ".join(lines[index:index + 5])
                            found = PdfMetricValue(value, page_no, re.sub(r"\s+", " ", excerpt)[:300])
                            break
                if found is not None:
                    break
            if found is None:
                return None
            components.append(found)
        if components[0].page != components[1].page:
            return None
        evidence = f"Scope 1: {components[0].evidence}; Scope 2: {components[1].evidence}"
        return PdfMetricValue(sum(item.value for item in components), components[0].page, evidence[:500])

    pattern = _PDF_ROW_LABELS.get(metric)
    if pattern is None:
        return None
    for page_no, text in enumerate(pages, start=1):
        lines = text.splitlines()
        if metric == "Total Waste Generated" and not re.search(r"total waste generated\s*\(in metric tonnes\)", text, re.I):
            continue
        for index, line in enumerate(lines):
            if not pattern.search(line):
                continue
            value = _following_number(lines, index, allow_energy_unit=metric == "Total Energy Consumption")
            if value is None:
                continue
            excerpt = " ".join(lines[index:index + 7])
            value *= _unit_multiplier(metric, excerpt)
            return PdfMetricValue(value, page_no, re.sub(r"\s+", " ", excerpt)[:400])
    return None


def power_of_ten_scale(xbrl_value: Optional[float], pdf_value: Optional[float]) -> Optional[float]:
    """Return the XBRL multiplier that aligns with PDF within 0.5%, or None."""
    if xbrl_value is None or pdf_value is None or pdf_value == 0:
        return None
    ratio = xbrl_value / pdf_value
    if ratio <= 0 or not math.isfinite(ratio):
        return None
    exponent = round(math.log10(ratio))
    if math.isclose(ratio, 10.0 ** exponent, rel_tol=0.005):
        return 10.0 ** -exponent
    return None


def manual_unit_value(metric: str, value: float, unit: str) -> float:
    """Convert a manually transcribed PDF quantity to the application's canonical unit."""
    return value * _unit_multiplier(metric, unit)


def company_cross_checks(values: Dict[str, Optional[float]], employees: Optional[float]) -> Tuple[bool, str]:
    """Check independent physical ratios for any quantities available in this company record."""
    checks = []
    energy = values.get("Total Energy Consumption")
    emissions = values.get("Scope 1 and 2 Emissions")
    water = values.get("Total Water Consumption")
    if energy and emissions:
        checks.append(("tCO2e per kWh", emissions / (energy * 1e6), 1e-4, 2e-3))
    if employees and water:
        checks.append(("water KL per employee", water / employees, 0.05, 500))
    if employees and energy:
        checks.append(("energy kWh per employee", energy * 1e6 / employees, 20, 50000))
    failed = [f"{name}={value:.6g} outside [{low:g}, {high:g}]"
              for name, value, low, high in checks if not low <= value <= high]
    return not failed, "; ".join(failed)
