"""Loader for config/anchors.json (scored metrics, anchors, weights, gate)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

PATH = Path(__file__).resolve().parents[2] / "config" / "anchors.json"
_LOWER_BETTER_CONTEXT = {"Total Energy Consumption", "Scope 1 and 2 Emissions", "Total Water Consumption", "Total Waste Generated"}


@lru_cache(maxsize=1)
def _cached() -> str:
    return PATH.read_text(encoding="utf-8")


def load_anchors() -> Dict[str, Any]:
    return json.loads(_cached())


def polarity(metric: str, anchors: Dict[str, Any]) -> str:
    """'higher' if a higher value is better, else 'lower'."""
    m = anchors["scored_metrics"].get(metric)
    if m:
        return "higher" if m["anchor_10"] > m["anchor_0"] else "lower"
    return "lower" if metric in _LOWER_BETTER_CONTEXT else "higher"


def metric_score(metric: str, value: float, anchors: Dict[str, Any]) -> float:
    """0-10 score by linear (default) or log interpolation between the two anchors."""
    import math
    m = anchors["scored_metrics"][metric]
    a0, a10 = float(m["anchor_0"]), float(m["anchor_10"])
    if m.get("scale") == "log":
        if value <= 0:
            return 10.0 if a10 < a0 else 0.0           # a zero intensity is better than any positive anchor
        s = 10.0 * (math.log(value) - math.log(a0)) / (math.log(a10) - math.log(a0))
    else:
        s = 10.0 * (value - a0) / (a10 - a0)
    return max(0.0, min(10.0, s))


def metric_weight(metric: str, anchors: Dict[str, Any]) -> float:
    m = anchors["scored_metrics"][metric]
    return float(anchors.get("hygiene_weight", 1.0)) if m.get("hygiene") else 1.0
