"""Compare offline and dynamic batch scores and market windows.

    python tools/evaluate_mode_agreement.py
"""
import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _read_csv(folder: Path, name: str) -> pd.DataFrame:
    path = folder / name
    if not path.is_file():
        raise FileNotFoundError(f"required output is missing: {path}")
    return pd.read_csv(path)


def _correlation(left: pd.Series, right: pd.Series, method: str) -> Optional[float]:
    if len(left) < 2:
        return None
    value = left.corr(right, method=method)
    return round(float(value), 4) if pd.notna(value) and math.isfinite(float(value)) else None


def evaluate_mode_agreement(offline_dir: Path, dynamic_dir: Path) -> Dict[str, Any]:
    offline = _read_csv(offline_dir, "esg_master_scores.csv")
    dynamic = _read_csv(dynamic_dir, "esg_master_scores.csv")
    required = {"Company_Code", "Master_ESG_Score", "Quality_Gate_Status"}
    for label, frame in (("offline", offline), ("dynamic", dynamic)):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{label} score file is missing columns: {sorted(missing)}")

    merged = offline[sorted(required)].merge(
        dynamic[sorted(required)], on="Company_Code", how="inner", suffixes=("_offline", "_dynamic"))
    scored = merged.dropna(subset=["Master_ESG_Score_offline", "Master_ESG_Score_dynamic"])
    differences = (scored["Master_ESG_Score_offline"] - scored["Master_ESG_Score_dynamic"]).abs()
    quality_agreement = (merged["Quality_Gate_Status_offline"] == merged["Quality_Gate_Status_dynamic"])

    offline_market = _read_csv(offline_dir, "stock_esg_correlation.csv")
    dynamic_market = _read_csv(dynamic_dir, "stock_esg_correlation.csv")
    for label, frame in (("offline", offline_market), ("dynamic", dynamic_market)):
        if "Company_Code" not in frame or "Window_End" not in frame:
            raise ValueError(f"{label} market file must contain Company_Code and Window_End")
    windows = offline_market[["Company_Code", "Window_End"]].merge(
        dynamic_market[["Company_Code", "Window_End"]], on="Company_Code",
        how="inner", suffixes=("_offline", "_dynamic"))
    windows = windows.dropna(subset=["Window_End_offline", "Window_End_dynamic"])
    same_window = windows["Window_End_offline"] == windows["Window_End_dynamic"]

    return {
        "offline_dir": str(offline_dir),
        "dynamic_dir": str(dynamic_dir),
        "companies": {
            "overlap": int(len(merged)),
            "scored_both": int(len(scored)),
            "quality_gate_agreement": round(float(quality_agreement.mean()), 4) if len(merged) else None,
        },
        "score_agreement": {
            "mean_absolute_difference": round(float(differences.mean()), 4) if len(differences) else None,
            "median_absolute_difference": round(float(differences.median()), 4) if len(differences) else None,
            "max_absolute_difference": round(float(differences.max()), 4) if len(differences) else None,
            "pearson_correlation": _correlation(
                scored["Master_ESG_Score_offline"], scored["Master_ESG_Score_dynamic"], "pearson"),
            "spearman_rank_correlation": _correlation(
                scored["Master_ESG_Score_offline"], scored["Master_ESG_Score_dynamic"], "spearman"),
        },
        "market_windows": {
            "matched_companies": int(len(windows)),
            "same_window": int(same_window.sum()),
            "different_window": int((~same_window).sum()),
            "consistent": bool(same_window.all()) if len(windows) else None,
            "offline_ends": sorted(windows["Window_End_offline"].unique().tolist()),
            "dynamic_ends": sorted(windows["Window_End_dynamic"].unique().tolist()),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", type=Path, default=ROOT / "processed-data" / "batch_offline")
    parser.add_argument("--dynamic", type=Path, default=ROOT / "processed-data" / "batch_dynamic")
    parser.add_argument("--output", type=Path, default=ROOT / "processed-data" / "mode_agreement.json")
    args = parser.parse_args()

    report = evaluate_mode_agreement(args.offline, args.dynamic)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, allow_nan=False))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
