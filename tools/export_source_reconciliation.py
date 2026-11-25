"""Export exact BRSR XBRL/PDF evidence and normalized metric values into one CSV."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from esg_agents.engine.xbrl import XbrlFiling

src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "processed-data" / "batch_offline"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else src / "source_reconciliation.csv"

FORMULAS = {
    "Renewable Energy Percentage":
        "Renewable Energy (XBRL unit converted to kWh) / Total Energy (kWh) * 100",
    "Energy Intensity":
        "Total Energy in kWh / Permanent Employees",
    "GHG Intensity":
        "(Scope 1 tCO2e + Scope 2 tCO2e) / (Revenue in INR / 10,000,000)",
    "Water Intensity":
        "Total Water Consumption in KL / Permanent Employees",
    "Waste Recovered Percentage":
        "Total Waste Recovered in tonnes / Total Waste Generated in tonnes * 100",
    "Female Employees Percentage":
        "Female Permanent Employees / Total Permanent Employees * 100",
    "Employee Turnover Rate":
        "XBRL TurnoverRate value * 100",
    "Lost Time Injury Frequency Rate":
        "XBRL LostTimeInjuryFrequencyRatePerOneMillionPersonHoursWorked value",
    "Human Rights Training Coverage":
        "XBRL employee human-rights training fraction * 100",
    "Differently Abled Employees Percentage":
        "Differently Abled Permanent Employees / Total Permanent Employees * 100",
    "Employee Wellbeing Spend":
        "XBRL wellbeing-spend fraction * 100",
    "Total Energy Consumption":
        "XBRL energy value * XBRL unit-to-kWh factor / 1,000,000",
    "Scope 1 and 2 Emissions":
        "Scope 1 tCO2e + Scope 2 tCO2e",
    "Total Water Consumption":
        "XBRL water value * XBRL unit-to-KL factor",
    "Total Waste Generated":
        "XBRL waste value * XBRL unit-to-tonnes factor",
    "Total Employees":
        "D_Gender_PermanentEmployees_TableA value",
}

UNIT_NORMALIZATION = {
    "Energy": "J/3,600,000; kJ/3600; MJ/3.6; GJ*1000/3.6; MWh*1000; GWh*1,000,000 => kWh; kWh is then /1,000,000 for GWh",
    "Water": "L*0.001; m3*1; ML*1000; MLD*1000; KL*1 => KL",
    "Waste": "kg*0.001; g*0.000001; t/tonne*1; kt*1000; Mt*1,000,000 => tonnes",
    "Emissions": "tCO2e*1; ktCO2e*1000; MtCO2e*1,000,000",
    "Percentages": "XBRL fractions are multiplied by 100; XBRL percentage facts retain their percentage value",
    "Revenue": "Revenue in INR is divided by 10,000,000 for INR crore",
}

XBRL_FACTS = {
    "Renewable Energy Percentage": [
        ("TotalEnergyConsumedFromRenewableSources", "DCYMain"),
        ("TotalEnergyConsumedFromRenewableAndNonRenewableSources", "DCYMain"),
    ],
    "Energy Intensity": [
        ("TotalEnergyConsumedFromRenewableAndNonRenewableSources", "DCYMain"),
        ("NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Gender_PermanentEmployees_TableA"),
    ],
    "GHG Intensity": [
        ("TotalScope1Emissions", "DCYMain"),
        ("TotalScope2Emissions", "DCYMain"),
        ("RevenueFromOperations", "DCYMain"),
    ],
    "Water Intensity": [
        ("TotalVolumeOfWaterConsumption", "DCYMain"),
        ("NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Gender_PermanentEmployees_TableA"),
    ],
    "Waste Recovered Percentage": [
        ("TotalWasteRecovered", "DCYMain"),
        ("TotalWasteGenerated", "DCYMain"),
    ],
    "Female Employees Percentage": [
        ("NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Female_PermanentEmployees_TableA"),
        ("NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Gender_PermanentEmployees_TableA"),
    ],
    "Employee Turnover Rate": [
        ("TurnoverRate", "D_Gender_PermanentEmployees_TableB_TurnOverRate_CY"),
    ],
    "Lost Time Injury Frequency Rate": [
        ("LostTimeInjuryFrequencyRatePerOneMillionPersonHoursWorked", "D_Employees"),
    ],
    "Human Rights Training Coverage": [
        ("PercentageOfEmployeesOrWorkersCoveredForProvidedTrainingOnHumanRightsIssues", "D_Employees_p5"),
    ],
    "Differently Abled Employees Percentage": [
        ("NumberOfDifferentlyAbledEmployeesOrWorkers", "D_Gender_PermanentEmployees_TableB"),
        ("NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Gender_PermanentEmployees_TableA"),
    ],
    "Employee Wellbeing Spend": [
        ("PercentageOfCostIncurredOnWellBeingMeasuresWithRespectToTotalRevenueOfTheCompany", "DCYMain"),
    ],
    "Total Energy Consumption": [
        ("TotalEnergyConsumedFromRenewableAndNonRenewableSources", "DCYMain"),
    ],
    "Scope 1 and 2 Emissions": [
        ("TotalScope1Emissions", "DCYMain"),
        ("TotalScope2Emissions", "DCYMain"),
    ],
    "Total Water Consumption": [
        ("TotalVolumeOfWaterConsumption", "DCYMain"),
    ],
    "Total Waste Generated": [
        ("TotalWasteGenerated", "DCYMain"),
    ],
    "Total Employees": [
        ("NumberOfEmployeesOrWorkersIncludingDifferentlyAbled", "D_Gender_PermanentEmployees_TableA"),
    ],
}

metrics = pd.read_csv(src / "brsr_extracted_metrics.csv")
pdf_dir = ROOT / "data" / "downloads" / "brsr"
xbrl_dir = ROOT / "data" / "downloads" / "xbrl"

rows = []
for r in metrics.itertuples(index=False):
    d = r._asdict()
    metric = str(d.get("Metric_Name", ""))
    code = str(d.get("Company_Code", ""))
    status = str(d.get("PDF_Status", "") or "")
    pdf_match = status in ("MATCH", "MATCH_AFTER_SCALE")
    pdf_file = next(iter(sorted(pdf_dir.glob(f"{code}_BR_*.pdf"))), None)
    xbrl_file = next(iter(sorted(xbrl_dir.glob(f"{code}_BRSR_*.xml"))), None)
    if pdf_file is None:
        status = "NO_PDF"

    xbrl_exact = d.get("XBRL_Raw", "")
    xbrl_unit = d.get("XBRL_Unit_Label", "")
    pdf_exact = d.get("PDF_Value", "")
    if not pdf_exact and pdf_match:
        pdf_exact = d.get("Metric_Value", "")

    xbrl_components = []
    if xbrl_file:
        try:
            filing = XbrlFiling(xbrl_file)
            for concept, context in XBRL_FACTS.get(metric, []):
                fact = filing.get(concept, context)
                if fact is not None:
                    xbrl_components.append(
                        f"{concept}@{context}={fact.text} [{filing.unit_label(fact)}]"
                    )
        except Exception as exc:
            xbrl_components.append(f"XBRL_PARSE_ERROR={type(exc).__name__}: {exc}")
    xbrl_components_text = " || ".join(xbrl_components)

    rows.append({
        "Company_Code": code,
        "Company_Name": d.get("Company_Name", ""),
        "Financial_Year": d.get("Financial_Year", ""),
        "Metric_Category": d.get("Metric_Category", ""),
        "Metric_Name": metric,
        "Normalized_Unit": d.get("Unit", ""),
        "Normalized_Current_Value": d.get("Metric_Value", ""),
        "Normalized_Prior_Value": d.get("Prior_Year_Value", ""),
        "Normalization_Formulas": FORMULAS.get(metric, ""),
        "Normalization_Rules": UNIT_NORMALIZATION.get(
            "Energy" if "Energy" in metric or metric == "Renewable Energy Percentage"
            else "Water" if "Water" in metric
            else "Waste" if "Waste" in metric
            else "Emissions" if "Emissions" in metric
            else "Revenue" if "GHG Intensity" in metric
            else "Percentages",
        ),
        "XBRL_Concept": d.get("XBRL_Concept", ""),
        "XBRL_Context": d.get("XBRL_Context", ""),
        "XBRL_Exact_Raw": xbrl_exact,
        "XBRL_Unit_Label": xbrl_unit,
        "XBRL_Current_Component_Facts": xbrl_components_text,
        "XBRL_File": xbrl_file.name if xbrl_file else "",
        "PDF_Corresponding_Value": pdf_exact,
        "PDF_Override_Value": d.get("PDF_Value", ""),
        "PDF_Override_Prior_Value": d.get("PDF_Prior_Value", ""),
        "PDF_Override_Unit": d.get("PDF_Unit", ""),
        "XBRL_Override_Value": d.get("XBRL_Value", ""),
        "XBRL_Override_Prior_Value": d.get("XBRL_Prior_Value", ""),
        "XBRL_Override_Unit": d.get("XBRL_Unit", ""),
        "PDF_Status": status,
        "PDF_Page": d.get("PDF_Page", ""),
        "PDF_Evidence": d.get("PDF_Evidence", ""),
        "PDF_File": pdf_file.name if pdf_file else "",
        "Final_Source": d.get("Final_Source", ""),
        "Validation_Status": d.get("Validation_Status", ""),
        "Audit_Status": d.get("Audit_Status", ""),
        "Audit_Note": d.get("Audit_Note", ""),
        "Needs_Manual_Review": d.get("Needs_Manual_Review", False),
        "Discrepancy": d.get("Discrepancy", False),
        "Source_Discrepancy_Reason": d.get("Discrepancy_Reason", ""),
    })

out.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame(rows).to_csv(out, index=False)
print(f"Wrote {len(rows)} metric rows to {out}")
