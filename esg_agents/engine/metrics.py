"""BRSR metric registry and exact-concept extraction.

Every metric is read from a named BRSR XBRL concept (or derived from two such concepts), so each value
has a traceable concept + context. Whether a metric is *scored*, its direction and its anchors live in
`config/anchors.json`; this module only knows where the number comes from.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .xbrl import Fact, XbrlFiling, truthy

# kWh per unit of the energy unit label used in the filing
_ENERGY_TO_KWH = {"j": 1 / 3.6e6, "kj": 1 / 3600, "mj": 1 / 3.6, "gj": 1000 / 3.6, "tj": 1e6 / 3.6,
                  "kwh": 1.0, "mwh": 1e3, "gwh": 1e6, "wh": 1e-3}
_WATER_TO_KL = {"kl": 1.0, "l": 1e-3, "m3": 1.0, "ml": 1e3, "mld": 1e3}
_MASS_TO_T = {"t": 1.0, "kg": 1e-3, "mt": 1e6, "kt": 1e3, "g": 1e-6}


@dataclass
class Extracted:
    name: str
    value: Optional[float]
    unit: str = ""
    concept: str = ""
    context: str = ""
    raw: str = ""
    label_unit: str = ""
    derived: bool = False
    inputs: List[str] = field(default_factory=list)
    prior_value: Optional[float] = None
    note: str = ""
    scale_ambiguous: bool = False

    @property
    def found(self) -> bool:
        return self.value is not None


# name -> (pillar, unit, group)  ; group 'scored' or 'context' is decided by config/anchors.json
METRIC_UNITS: Dict[str, tuple] = {
    # Environmental
    "Renewable Energy Percentage": ("Environmental", "%"),
    "Energy Intensity": ("Environmental", "kWh/employee"),
    "GHG Intensity": ("Environmental", "tCO2e/INR crore revenue"),
    "Water Intensity": ("Environmental", "KL/employee"),
    "Waste Recovered Percentage": ("Environmental", "%"),
    "Total Energy Consumption": ("Environmental", "GWh"),
    "Scope 1 and 2 Emissions": ("Environmental", "tCO2e"),
    "Total Water Consumption": ("Environmental", "KL"),
    "Total Waste Generated": ("Environmental", "tonnes"),
    # Social
    "Female Employees Percentage": ("Social", "%"),
    "Employee Turnover Rate": ("Social", "%"),
    "Lost Time Injury Frequency Rate": ("Social", "per million hours"),
    "Human Rights Training Coverage": ("Social", "%"),
    "Differently Abled Employees Percentage": ("Social", "%"),
    "Total Employees": ("Social", "count"),
    "Employee Wellbeing Spend": ("Social", "% of revenue"),
    # Governance
    "Female Directors Percentage": ("Governance", "%"),
    "Board and KMP Disciplinary Actions": ("Governance", "count"),
    "Board and KMP Conflict of Interest Complaints": ("Governance", "count"),
    "Anti-Corruption Policy": ("Governance", "flag"),
    "Cybersecurity and Data Privacy Policy": ("Governance", "flag"),
    "Board Sustainability Committee": ("Governance", "flag"),
    "Board Size": ("Governance", "count"),
}


def _unit_key(filing: XbrlFiling, fact: Fact) -> str:
    m = filing.unit_label(fact).split(":")[-1].strip().lower()
    return m.replace("kilolitres", "kl").replace("kiloliters", "kl").replace("tonne", "t").replace("tonnes", "t")


def _num(filing: XbrlFiling, concept: str, ctx: str = "DCYMain") -> Optional[Fact]:
    f = filing.get(concept, ctx)
    return f if f is not None and f.value is not None else None


def _scaled(filing: XbrlFiling, concept: str, table: dict, ctx: str = "DCYMain"):
    """(value in canonical unit, fact, note). Unknown unit labels give (None, fact, note)."""
    f = _num(filing, concept, ctx)
    if f is None:
        return None, None, "concept not reported"
    key = _unit_key(filing, f)
    if key in table:
        return f.value * table[key], f, ""
    return None, f, f"unrecognised unit label '{filing.unit_label(f)}'"


def _employees(filing: XbrlFiling, year: str = "CY") -> Optional[Fact]:
    ctx = "D_Gender_PermanentEmployees_TableA" + ("" if year == "CY" else "_PY")
    return _num(filing, "NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", ctx)


def _revenue_inr(filing: XbrlFiling, ctx: str = "DCYMain") -> Optional[Fact]:
    for c in ("RevenueFromOperations", "TotalRevenueOfTheCompany", "Turnover"):
        f = _num(filing, c, ctx)
        if f is not None and f.value > 0:
            return f
    return None


def _energy_kwh(filing: XbrlFiling, concept: str, ctx: str):
    return _scaled(filing, concept, _ENERGY_TO_KWH, ctx)


def extract_all(filing: XbrlFiling, scales: Optional[Dict[str, float]] = None) -> Dict[str, Extracted]:
    """Extract every metric. `scales` multiplies a base quantity ('energy', 'ghg', 'water', 'waste',
    'revenue') that the plausibility audit found to be mis-scaled in the filing."""
    sc = {"energy": 1.0, "ghg": 1.0, "water": 1.0, "waste": 1.0, "revenue": 1.0, **(scales or {})}
    out: Dict[str, Extracted] = {}

    def put(name, value, unit=None, concept="", context="", raw="", label="", derived=False, inputs=None,
            prior=None, note="", ambiguous=False):
        out[name] = Extracted(name, value, unit or METRIC_UNITS[name][1], concept, context, raw, label,
                              derived, inputs or [], prior, note, ambiguous)

    # ------------------------------------------------------------- employees
    emp = _employees(filing)
    emp_py = _employees(filing, "PY")
    put("Total Employees", emp.value if emp else None, concept="NumberOfEmployeesOrWorkersIncludingDifferentlyAbled",
        context=emp.context if emp else "", raw=emp.text if emp else "",
        prior=emp_py.value if emp_py else None, note="" if emp else "permanent employee count not reported")

    # --------------------------------------------------------------- energy
    tot, tf, tn = _energy_kwh(filing, "TotalEnergyConsumedFromRenewableAndNonRenewableSources", "DCYMain")
    ren, rf, rn = _energy_kwh(filing, "TotalEnergyConsumedFromRenewableSources", "DCYMain")
    tot_p, _, _ = _energy_kwh(filing, "TotalEnergyConsumedFromRenewableAndNonRenewableSources", "DPYMain")
    ren_p, _, _ = _energy_kwh(filing, "TotalEnergyConsumedFromRenewableSources", "DPYMain")
    tot, ren, tot_p, ren_p = [None if v is None else v * sc["energy"] for v in (tot, ren, tot_p, ren_p)]
    put("Total Energy Consumption", tot / 1e6 if tot else None, concept="TotalEnergyConsumedFromRenewableAndNonRenewableSources",
        context="DCYMain", raw=tf.text if tf else "", label=filing.unit_label(tf) if tf else "",
        prior=tot_p / 1e6 if tot_p else None, note=tn)
    if tot and ren is not None and tot > 0:
        put("Renewable Energy Percentage", ren / tot * 100, concept="TotalEnergyConsumedFromRenewableSources / Total", context="DCYMain",
            derived=True, inputs=["TotalEnergyConsumedFromRenewableSources", "TotalEnergyConsumedFromRenewableAndNonRenewableSources"],
            prior=(ren_p / tot_p * 100) if (tot_p and ren_p is not None) else None)
    else:
        put("Renewable Energy Percentage", None, note=tn or rn or "energy totals not reported")
    if tot and emp and emp.value > 0:
        put("Energy Intensity", tot / emp.value, concept="TotalEnergy / permanent employees", context="DCYMain", derived=True,
            inputs=["TotalEnergyConsumedFromRenewableAndNonRenewableSources", "NumberOfEmployeesOrWorkersIncludingDifferentlyAbled"],
            prior=(tot_p / emp_py.value) if (tot_p and emp_py and emp_py.value) else None)
    else:
        put("Energy Intensity", None, note="needs total energy and permanent employee count")

    # ----------------------------------------------------------- emissions
    s1 = _num(filing, "TotalScope1Emissions"); s2 = _num(filing, "TotalScope2Emissions")
    s1p = _num(filing, "TotalScope1Emissions", "DPYMain"); s2p = _num(filing, "TotalScope2Emissions", "DPYMain")
    gsc = sc["ghg"]
    if s1 is not None and s2 is not None:
        lab = filing.unit_label(s1)
        amb = "mtco2e" in lab.lower() or "kt" in lab.lower()
        total = (s1.value + s2.value) * gsc
        put("Scope 1 and 2 Emissions", total, concept="TotalScope1Emissions + TotalScope2Emissions", context="DCYMain",
            raw=f"{s1.text}+{s2.text}", label=lab, derived=True, inputs=["TotalScope1Emissions", "TotalScope2Emissions"],
            prior=((s1p.value + s2p.value) * gsc) if (s1p and s2p) else None,
            note="unit label says '%s' but filers commonly report tCO2e; scale decided by the plausibility audit" % lab if amb else "",
            ambiguous=amb)
    else:
        put("Scope 1 and 2 Emissions", None, note="Scope 1 and/or Scope 2 not reported in XBRL")
    rev = _revenue_inr(filing); rev_p = _revenue_inr(filing, "DPYMain")
    rv = rev.value * sc["revenue"] if rev else None
    rv_p = rev_p.value * sc["revenue"] if rev_p else None
    g = out["Scope 1 and 2 Emissions"]
    if g.found and rv:
        put("GHG Intensity", g.value / (rv / 1e7), concept="Scope1+Scope2 / RevenueFromOperations", context="DCYMain",
            derived=True, inputs=["TotalScope1Emissions", "TotalScope2Emissions", "RevenueFromOperations"],
            prior=(g.prior_value / (rv_p / 1e7)) if (g.prior_value is not None and rv_p) else None, ambiguous=g.scale_ambiguous)
    else:
        put("GHG Intensity", None, note="needs Scope 1+2 and revenue")

    # ---------------------------------------------------------------- water
    w, wf, wn = _scaled(filing, "TotalVolumeOfWaterConsumption", _WATER_TO_KL, "DCYMain")
    w_p, _, _ = _scaled(filing, "TotalVolumeOfWaterConsumption", _WATER_TO_KL, "DPYMain")
    w = None if w is None else w * sc["water"]
    w_p = None if w_p is None else w_p * sc["water"]
    put("Total Water Consumption", w, concept="TotalVolumeOfWaterConsumption", context="DCYMain",
        raw=wf.text if wf else "", label=filing.unit_label(wf) if wf else "", prior=w_p, note=wn)
    if w is not None and emp and emp.value > 0:
        put("Water Intensity", w / emp.value, concept="TotalVolumeOfWaterConsumption / permanent employees", context="DCYMain",
            derived=True, inputs=["TotalVolumeOfWaterConsumption", "NumberOfEmployeesOrWorkersIncludingDifferentlyAbled"],
            prior=(w_p / emp_py.value) if (w_p is not None and emp_py and emp_py.value) else None)
    else:
        put("Water Intensity", None, note="needs water consumption and permanent employee count")

    # ---------------------------------------------------------------- waste
    wg, wgf, wgn = _scaled(filing, "TotalWasteGenerated", _MASS_TO_T, "DCYMain")
    wr, _, _ = _scaled(filing, "TotalWasteRecovered", _MASS_TO_T, "DCYMain")
    wg_p, _, _ = _scaled(filing, "TotalWasteGenerated", _MASS_TO_T, "DPYMain")
    wr_p, _, _ = _scaled(filing, "TotalWasteRecovered", _MASS_TO_T, "DPYMain")
    wg, wr, wg_p, wr_p = [None if v is None else v * sc["waste"] for v in (wg, wr, wg_p, wr_p)]
    put("Total Waste Generated", wg, concept="TotalWasteGenerated", context="DCYMain", raw=wgf.text if wgf else "",
        label=filing.unit_label(wgf) if wgf else "", prior=wg_p, note=wgn)
    if wg and wr is not None and wg > 0:
        put("Waste Recovered Percentage", wr / wg * 100, concept="TotalWasteRecovered / TotalWasteGenerated", context="DCYMain",
            derived=True, inputs=["TotalWasteRecovered", "TotalWasteGenerated"],
            prior=(wr_p / wg_p * 100) if (wg_p and wr_p is not None) else None)
    else:
        put("Waste Recovered Percentage", None, note="needs waste generated and waste recovered")

    # --------------------------------------------------------------- social
    fem = _num(filing, "NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Female_PermanentEmployees_TableA")
    fem_p = _num(filing, "NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Female_PermanentEmployees_TableA_PY")
    if fem is not None and emp and emp.value > 0:
        put("Female Employees Percentage", fem.value / emp.value * 100, concept="Female / Total permanent employees", context=fem.context,
            derived=True, inputs=["NumberOfEmployeesOrWorkersIncludingDifferentlyAbled"],
            prior=(fem_p.value / emp_py.value * 100) if (fem_p and emp_py and emp_py.value) else None)
    else:
        put("Female Employees Percentage", None, note="gender split of permanent employees not reported")
    tr = filing.first_number("TurnoverRate", ["D_Gender_PermanentEmployees_TableB_TurnOverRate_CY"])
    tr_p = filing.first_number("TurnoverRate", ["D_Gender_PermanentEmployees_TableB_TurnOverRate_PY"])
    put("Employee Turnover Rate", tr.value * 100 if tr else None, concept="TurnoverRate", context=tr.context if tr else "",
        raw=tr.text if tr else "", prior=tr_p.value * 100 if tr_p else None, note="" if tr else "permanent-employee turnover not reported")
    lt = _num(filing, "LostTimeInjuryFrequencyRatePerOneMillionPersonHoursWorked", "D_Employees")
    lt_p = _num(filing, "LostTimeInjuryFrequencyRatePerOneMillionPersonHoursWorked", "D_Employees_PY")
    put("Lost Time Injury Frequency Rate", lt.value if lt else None, concept="LostTimeInjuryFrequencyRatePerOneMillionPersonHoursWorked",
        context="D_Employees", raw=lt.text if lt else "", prior=lt_p.value if lt_p else None,
        note="" if lt else "LTIFR for employees not reported")
    hr = [f for f in filing.find_prefix("PercentageOfEmployeesOrWorkersCoveredForProvidedTrainingOnHumanRights", r"^D_Employees_p5$") if f.value is not None]
    hr_p = [f for f in filing.find_prefix("PercentageOfEmployeesOrWorkersCoveredForProvidedTrainingOnHumanRights", r"^D_Employees_p5_PY$") if f.value is not None]
    put("Human Rights Training Coverage", hr[0].value * 100 if hr else None, concept=hr[0].concept if hr else "", context="D_Employees_p5",
        raw=hr[0].text if hr else "", prior=hr_p[0].value * 100 if hr_p else None,
        note="" if hr else "human-rights training coverage not reported")
    da = _num(filing, "NumberOfDifferentlyAbledEmployeesOrWorkers", "D_Gender_PermanentEmployees_TableB")
    da_p = _num(filing, "NumberOfDifferentlyAbledEmployeesOrWorkers", "D_Gender_PermanentEmployees_TableB_PY")
    if da is not None and emp and emp.value > 0:
        put("Differently Abled Employees Percentage", da.value / emp.value * 100, concept="DifferentlyAbled / Total permanent employees",
            context=da.context, derived=True, inputs=["NumberOfDifferentlyAbledEmployeesOrWorkers"],
            prior=(da_p.value / emp_py.value * 100) if (da_p and emp_py and emp_py.value) else None)
    else:
        put("Differently Abled Employees Percentage", None, note="differently-abled headcount not reported")
    wb = _num(filing, "PercentageOfCostIncurredOnWellBeingMeasuresWithRespectToTotalRevenueOfTheCompany")
    put("Employee Wellbeing Spend", wb.value * 100 if wb else None, concept="PercentageOfCostIncurredOnWellBeingMeasuresWithRespectToTotalRevenueOfTheCompany",
        context="DCYMain", raw=wb.text if wb else "", note="" if wb else "wellbeing spend not reported")

    # ----------------------------------------------------------- governance
    fd = _num(filing, "PercentageOfFemaleBoardOfDirectors")
    bs = _num(filing, "TotalNumberOfBoardOfDirectors")
    nf = _num(filing, "NumberOfFemaleBoardOfDirectors")
    fd_val = fd.value * 100 if fd else (nf.value / bs.value * 100 if (nf and bs and bs.value) else None)
    put("Female Directors Percentage", fd_val, concept="PercentageOfFemaleBoardOfDirectors" if fd else "NumberOfFemaleBoardOfDirectors / TotalNumberOfBoardOfDirectors",
        context="DCYMain", raw=fd.text if fd else "", derived=not fd, note="" if fd_val is not None else "board gender split not reported")
    put("Board Size", bs.value if bs else None, concept="TotalNumberOfBoardOfDirectors", context="DCYMain", raw=bs.text if bs else "",
        note="" if bs else "board size not reported")

    def pair(name, c1, c2):
        a, b = _num(filing, c1), _num(filing, c2)
        if a is not None and b is not None:
            put(name, a.value + b.value, concept=f"{c1} + {c2}", context="DCYMain", derived=True, inputs=[c1, c2])
        else:
            put(name, None, note="directors/KMP counts not both reported")
    pair("Board and KMP Disciplinary Actions", "NumberOfDirectorsAgainstWhomDisciplinaryActionWasTaken", "NumberOfKMPsAgainstWhomDisciplinaryActionWasTaken")
    pair("Board and KMP Conflict of Interest Complaints", "NumberOfComplaintsReceivedInRelationToIssuesOfConflictOfInterestOfTheDirectors",
         "NumberOfComplaintsReceivedInRelationToIssuesOfConflictOfInterestOfTheKMPs")

    def flag(name, concept):
        t = filing.text_fact(concept)
        b = truthy(t)
        put(name, (1.0 if b else 0.0) if b is not None else None, concept=concept, context="DCYMain", raw=t or "",
            note="" if b is not None else "policy question not answered in XBRL")
    flag("Anti-Corruption Policy", "DoesTheEntityHaveAnAntiCorruptionOrAntiBriberyPolicy")
    flag("Cybersecurity and Data Privacy Policy", "DoesTheEntityHaveAFrameworkOrPolicyOnCyberSecurityAndRisksRelatedToDataPrivacy")
    cm = next(iter(filing.find_prefix("DoesTheEntityHaveASpecifiedCommitteeOfTheBoardOrDirectorResponsibleForDecisionMaking", r"^DCYMain$")), None)
    b = truthy(cm.text if cm else None)
    put("Board Sustainability Committee", (1.0 if b else 0.0) if b is not None else None, concept=cm.concept if cm else "", context="DCYMain",
        raw=cm.text if cm else "", note="" if b is not None else "sustainability committee question not answered")
    return out
