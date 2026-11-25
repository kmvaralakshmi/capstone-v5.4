"""Shared paths, configuration loading and small helpers for every agent file."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_DIR = ROOT / "config"
BRSR_PDF_DIR = ROOT / "data" / "downloads" / "brsr"
XBRL_RAW_DIR = ROOT / "data" / "downloads" / "xbrl"
BENCH_RAW = ROOT / "data" / "benchmark"
BASE_PROCESSED = ROOT / "processed-data"
# BASE_PROCESSED holds shared caches; PROCESSED is where THIS run writes. On-demand runs set
# ESG_OUT_DIR so one company never overwrites batch results.
# Two completely separate result sets: OFFLINE (bundled data, no LLM) and DYNAMIC (live data + LLM).
# ESG_MODE is set by run_agents.py before this module is imported.
MODE = os.getenv("ESG_MODE", "offline").lower()
if MODE not in ("offline", "dynamic"):
    MODE = "offline"
BATCH_DIR = BASE_PROCESSED / f"batch_{MODE}"
PROCESSED = Path(os.getenv("ESG_OUT_DIR")) if os.getenv("ESG_OUT_DIR") else BATCH_DIR
PROCESSED.mkdir(parents=True, exist_ok=True)

TARGET_FY = "2024-25"
TARGET_PERIOD = ("2024-04-01", "2025-03-31")

OUTPUT_FILES = {
    "brsr_metrics": "brsr_extracted_metrics.csv",
    "environmental_risk": "company_location_environmental_risk.csv",
    "news_sentiment": "esg_news_sentiment.csv",
    "news_rejected": "esg_news_rejected_articles.csv",
    "cross_validation": "cross_validation_report.csv",
    "data_quality": "data_quality_report.csv",
    "external_benchmark": "external_benchmark_report.csv",
    "run_metadata": "run_metadata.json",
    "stock_correlation": "stock_esg_correlation.csv",
    "greenwashing": "greenwashing_detection.csv",
    "master_scores": "esg_master_scores.csv",
    "explanations": "multi_agent_explanations.csv",
}


def _load_companies() -> dict:
    comp = pd.read_csv(CONFIG_DIR / "companies.csv")
    loc = pd.read_csv(CONFIG_DIR / "locations.csv") if (CONFIG_DIR / "locations.csv").exists() else pd.DataFrame()
    out = {}
    for _, r in comp.iterrows():
        code = r["company_code"]
        rows = loc[loc["company_code"] == code] if not loc.empty else pd.DataFrame()
        hq = rows[rows["role"] == "HQ"]
        hq_city = hq.iloc[0]["city"] if len(hq) else r["headquarters_city"]
        hq_area = (hq.iloc[0]["area"] if len(hq) and isinstance(hq.iloc[0]["area"], str) else "")
        offices = [{"city": x["city"], "area": x["area"] if isinstance(x["area"], str) else ""}
                   for _, x in rows[rows["role"] == "office"].iterrows()]
        out[code] = {"full_name": r["company_name"], "ticker": r["ticker"],
                     "search_alias": r.get("search_alias", ""),
                     "headquarters": {"city": hq_city, "area": hq_area}, "major_offices": offices}
    return out


COMPANIES = _load_companies()
_DYN = ROOT / "data" / "dynamic_companies.json"      # companies registered on demand by Agent 0
if _DYN.exists():
    try:
        for _k, _v in json.loads(_DYN.read_text(encoding="utf-8")).items():
            COMPANIES.setdefault(_k, _v)
    except Exception:
        pass
EXCLUDED = (pd.read_csv(CONFIG_DIR / "excluded_companies.csv").to_dict("records")
            if (CONFIG_DIR / "excluded_companies.csv").exists() else [])
_kw = json.loads((CONFIG_DIR / "esg_keywords.json").read_text(encoding="utf-8"))
ESG_INCLUDE_KEYWORDS, ESG_EXCLUDE_KEYWORDS = _kw["include"], _kw["exclude"]
TICKER_OVERRIDES = json.loads((CONFIG_DIR / "ticker_overrides.json").read_text()) \
    if (CONFIG_DIR / "ticker_overrides.json").exists() else {}


def out_path(key: str) -> Path:
    return PROCESSED / OUTPUT_FILES[key]


def read_csv_safe(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path) if Path(path).exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def is_num(x) -> bool:
    try:
        return x is not None and not (isinstance(x, float) and np.isnan(x)) and not pd.isna(x)
    except Exception:
        return False


def clean(obj):
    """Make a structure JSON-safe (NaN -> None, numpy -> python)."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if (obj != obj) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj
