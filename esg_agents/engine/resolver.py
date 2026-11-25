"""Resolve a company name / NSE symbol to a configured company code (deterministic candidates)."""
from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Dict, List

from ..common import COMPANIES


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().replace(".ns", ""))


def _index() -> Dict[str, str]:
    idx: Dict[str, str] = {}
    for code, info in COMPANIES.items():
        for key in (code, info["full_name"], info.get("ticker", ""), info.get("search_alias", "")):
            if key:
                idx[normalize(str(key))] = code
    return idx


def exact(value: str):
    """Exact or alias match -> company code, else None."""
    return _index().get(normalize(value))


def candidates(value: str, n: int = 3, cutoff: float = 0.7) -> List[dict]:
    """Close (fuzzy) matches, best first, each with its similarity score."""
    key = normalize(value)
    idx = _index()
    out, seen = [], set()
    from difflib import SequenceMatcher
    for k in get_close_matches(key, list(idx), n=n * 2, cutoff=cutoff):
        code = idx[k]
        if code in seen:
            continue
        seen.add(code)
        out.append({"company_code": code, "company_name": COMPANIES[code]["full_name"],
                    "similarity": round(SequenceMatcher(None, key, k).ratio(), 3)})
    return out[:n]
