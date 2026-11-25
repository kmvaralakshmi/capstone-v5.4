"""Print every number the paper/report quotes, straight from a finished run (so text and code cannot drift).

    python tools/export_paper_numbers.py [processed-data]          -> docs/PAPER_NUMBERS.md
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "processed-data" / "batch_offline"
m = pd.read_csv(src / "esg_master_scores.csv")
b = pd.read_csv(src / "brsr_extracted_metrics.csv")
rep = json.loads((src / "agent_run_report.json").read_text(encoding="utf-8"))
val = json.loads((src / "validation_summary.json").read_text(encoding="utf-8"))
met = json.loads((src / "run_metrics.json").read_text(encoding="utf-8")) if (src / "run_metrics.json").exists() else {}
agreement_path = src.parent / "mode_agreement.json"
agreement = json.loads(agreement_path.read_text(encoding="utf-8")) if agreement_path.is_file() else None
s = m[m["Master_ESG_Score"].notna()]


def tcs_planner_ablation():
    root = ROOT / "data" / "runs" / "dynamic"
    found = {}
    if not root.is_dir():
        return found
    for folder in sorted((path for path in root.iterdir() if path.is_dir()), reverse=True):
        mode_path = folder / "run_mode.json"
        report_path = folder / "agent_run_report.json"
        score_path = folder / "esg_master_scores.csv"
        if not (mode_path.is_file() and report_path.is_file() and score_path.is_file()):
            continue
        mode_meta = json.loads(mode_path.read_text(encoding="utf-8"))
        if (mode_meta.get("kind") != "on_demand" or mode_meta.get("status") != "completed"
                or "TCS" not in mode_meta.get("companies", [])):
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        agents = report.get("agents", [])
        modes = {agent.get("planner_mode") for agent in agents if agent.get("agent_id") != "agent2"}
        if len(modes) != 1 or next(iter(modes)) not in ("off", "recover"):
            continue
        planner_mode = next(iter(modes))
        if planner_mode in found:
            continue
        score_rows = pd.read_csv(score_path)
        tcs = score_rows[score_rows["Company_Code"] == "TCS"]
        found[planner_mode] = {
            "run": folder.name,
            "seconds": (report.get("timing") or {}).get("total_seconds"),
            "score": None if tcs.empty or pd.isna(tcs.iloc[0].get("Master_ESG_Score"))
            else float(tcs.iloc[0]["Master_ESG_Score"]),
            "planning_calls": sum(agent.get("llm_planning_calls", 0) for agent in agents),
            "judgements": sum(agent.get("llm_judgements", 0) for agent in agents),
            "planning_seconds": sum(agent.get("llm_planning_seconds", 0.0) for agent in agents),
            "judging_seconds": sum(agent.get("llm_judging_seconds", 0.0) for agent in agents),
        }
        if len(found) == 2:
            break
    return found


L = []
L.append(f"# Numbers for the paper (generated from `{src.name}` - run id {rep.get('run_id', 'n/a')})\n")
L.append("Regenerate with `python tools/export_paper_numbers.py` after any pipeline run; never type these by hand.\n")
L.append("## Cohort\n")
L.append(f"- companies in cohort: {len(m)}; scored: {len(s)}; withheld (insufficient evidence): {int((m['Quality_Gate_Status'] == 'Insufficient').sum())}")
L.append(f"- quality gate: {m['Quality_Gate_Status'].value_counts().to_dict()}")
L.append(f"- risk bands: {s['Risk_Level'].value_counts().to_dict()}")
L.append(f"- master score: min {s['Master_ESG_Score'].min():.1f}, max {s['Master_ESG_Score'].max():.1f}, mean {s['Master_ESG_Score'].mean():.1f}, median {s['Master_ESG_Score'].median():.1f}")
top = s.sort_values("Master_ESG_Score", ascending=False)
L.append("- top 5: " + "; ".join(f"{r.Company_Name} {r.Master_ESG_Score:.1f}" for r in top.head(5).itertuples()))
L.append("- bottom 3: " + "; ".join(f"{r.Company_Name} {r.Master_ESG_Score:.1f}" for r in top.tail(3).itertuples()))
L.append(f"- scored metrics per company: mean {s['Usable_Metrics'].mean():.1f} of 16 (min {int(s['Usable_Metrics'].min())})")
L.append(f"- companies with a governance score: {int(m['Governance_Score'].notna().sum())}\n")
L.append("## Extraction and audit\n")
L.append(f"- metric rows: {len(b)}; verified/valid: {int((b['Validation_Status'] == 'VALID').sum())}; repaired: {int((b['Validation_Status'] == 'REPAIRED').sum())}; quarantined: {int((b['Validation_Status'] == 'QUARANTINED').sum())}; not found: {int((b['Validation_Status'] == 'NOT_FOUND').sum())}")
L.append(f"- PDF evidence: {b['PDF_Status'].value_counts().to_dict()}")
L.append(f"- agent decisions: {sum(a.get('decisions_by_source', {}).get('policy', 0) for a in rep['agents'])} policy, "
         f"{sum(a.get('decisions_by_source', {}).get('llm', 0) for a in rep['agents'])} LLM; judgements by source: "
         + str({k: sum(a.get('judgements_by_source', {}).get(k, 0) for a in rep['agents']) for k in ('llm', 'rule', 'rule_fallback')}) + "\n")
L.append("## Validation\n")
for k, v in val["analyses"].items():
    L.append(f"- {k}: {v.get('status')}")
sens = val["analyses"].get("sensitivity", {})
if sens.get("status") == "ok":
    L.append(f"- sensitivity ({sens['trials']} trials): median Spearman {sens['spearman_vs_baseline_median']}, 5th pct {sens['spearman_vs_baseline_p5']}, mean rank shift {sens['mean_abs_rank_shift_median']}, risk band unchanged {sens['risk_band_unchanged_median']}")
for row in val["analyses"].get("nse_top200", {}).get("sector_comparison", []):
    L.append(f"- vs NSE IT sector ({row['Pillar']}): our mean {row['Our_IT_Mean']} (n={row['n_our_IT_companies']}) vs NSE average {row['NSE_IT_Avg']}, range {row['NSE_IT_Min']}-{row['NSE_IT_Max']}")
if agreement:
    company_agreement = agreement["companies"]
    score_agreement = agreement["score_agreement"]
    windows = agreement["market_windows"]
    L.append("\n## Offline/Dynamic agreement\n")
    L.append(f"- matched companies: {company_agreement['overlap']}; scored in both modes: {company_agreement['scored_both']}; "
             f"quality-gate agreement: {company_agreement['quality_gate_agreement']}")
    L.append(f"- absolute score difference: mean {score_agreement['mean_absolute_difference']}, "
             f"median {score_agreement['median_absolute_difference']}, max {score_agreement['max_absolute_difference']}")
    L.append(f"- score correlation: Pearson {score_agreement['pearson_correlation']}, "
             f"Spearman rank {score_agreement['spearman_rank_correlation']}")
    L.append(f"- market windows: {windows['same_window']}/{windows['matched_companies']} matched tickers share an end date "
             f"(consistent: {windows['consistent']}); offline {windows['offline_ends']}, dynamic {windows['dynamic_ends']}")
L.append("\n## Runtime\n")
for a in rep["agents"]:
    L.append(f"- {a['agent_id']}: {a.get('seconds', a.get('duration_seconds', 'n/a'))} s")
L.append("\n## Data-source caveats\n")
L.append("- Air quality: bundled CPCB data; a live data.gov.in feed is optional and requires a privately configured API key.")
L.append("- News: GDELT is requested in dynamic mode with a bundled fallback; the bundled corpus covers 2025-02-07 to 2025-08-21.")
ablation = tcs_planner_ablation()
if ablation:
    L.append("\n## Dynamic TCS planner ablation\n")
    for planner_mode in ("off", "recover"):
        result = ablation.get(planner_mode)
        if result:
            L.append(f"- {planner_mode} (`{result['run']}`): {result['seconds']} s; "
                     f"{result['planning_calls']} planning calls; {result['judgements']} LLM judgements; "
                     f"planning {result['planning_seconds']:.2f} s / judging {result['judging_seconds']:.2f} s; "
                     f"master score {result['score']}")
    if "off" in ablation and "recover" in ablation:
        off_score, recover_score = ablation["off"]["score"], ablation["recover"]["score"]
        if off_score is not None and recover_score is not None:
            L.append(f"- score comparison: {'identical' if off_score == recover_score else 'different'} "
                     f"(absolute difference {abs(off_score - recover_score):.2f})")
out = ROOT / "docs" / "PAPER_NUMBERS.md"
out.parent.mkdir(exist_ok=True)
out.write_text("\n".join(L) + "\n", encoding="utf-8")
print("wrote", out)
