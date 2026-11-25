"""Run telemetry: per-step timing, provenance and parallel-aware stage timing.

Every data fetch and every agent run is recorded as a *span*:
    name, stage, source, status, start/end, duration, fallback_used, error, notes
For a stage whose spans ran in parallel, `stage_wall_seconds` (what the user
waited) differs from `sum_seconds` (total work) - both are reported (spec A15).
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class RunTracker:
    def __init__(self, run_id: str, company: str = "", out_dir: Optional[Path] = None):
        self.run_id, self.company = run_id, company
        self.out_dir = Path(out_dir) if out_dir else None
        self.spans: List[Dict[str, Any]] = []
        self.stages: Dict[str, Dict[str, Any]] = {}
        self.provenance: List[Dict[str, Any]] = []
        self.data_sources: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self.current_stage: Optional[str] = None
        self._t0 = time.perf_counter()
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @contextmanager
    def span(self, name: str, stage: str, source: str = "", kind: str = "fetch", **notes):
        if kind == "agent" or stage == "data_acquisition":
            stage = self.current_stage or stage
        rec = {"name": name, "stage": stage, "kind": kind, "source": source, "status": "running",
               "start": time.perf_counter() - self._t0, "fallback_used": False,
               "error": None, "notes": notes}
        try:
            yield rec
            if rec["status"] == "running":
                rec["status"] = "success"
        except Exception as exc:
            rec["status"], rec["error"] = "failed", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            rec["end"] = time.perf_counter() - self._t0
            rec["duration"] = round(rec["end"] - rec["start"], 3)
            with self._lock:
                self.spans.append(rec)
            self.flush()

    def mark_fallback(self, name: str, source: str, reason: str = "") -> None:
        """Record that a fallback data source replaced a failed primary source (always visible in summary)."""
        with self._lock:
            self.spans.append({"name": name, "stage": self.current_stage or "data_acquisition", "kind": "fetch",
                               "source": source, "status": "success", "start": time.perf_counter() - self._t0,
                               "end": time.perf_counter() - self._t0, "duration": 0.0, "fallback_used": True,
                               "error": (reason or "")[:240] or None, "notes": {}})
        self.flush()

    def record_provenance(self, metric: str, value: Any, source: str, retrieved: str = "",
                          evidence: str = "", confidence: str = "", fallback: bool = False):
        with self._lock:
            self.provenance.append({"metric": metric, "value": value, "source": source,
                                    "retrieved_at": retrieved or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "evidence_source": evidence, "confidence": confidence,
                                    "fallback_used": fallback})

    def record_data_source(self, key: str, record: Dict[str, Any]) -> None:
        normalized = {"key": key, **record}
        with self._lock:
            self.data_sources = [item for item in self.data_sources if item.get("key") != key]
            self.data_sources.append(normalized)
        self.flush()

    def stage(self, name: str):
        """Context manager measuring the wall-clock time of a whole stage."""
        tracker = self

        @contextmanager
        def _cm():
            t = time.perf_counter()
            tracker.current_stage = name
            try:
                yield
            finally:
                with tracker._lock:
                    tracker.stages[name] = {"wall_seconds": round(time.perf_counter() - t, 3)}
                tracker.flush()
        return _cm()

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            spans = list(self.spans)
        by_stage: Dict[str, Dict[str, Any]] = {}
        fetches: Dict[str, float] = {}
        for sp in spans:
            if sp.get("kind") != "agent":
                fetches[sp["name"]] = sp["duration"]
                continue
            st = by_stage.setdefault(sp["stage"], {"sum_seconds": 0.0, "spans": 0})
            st["sum_seconds"] = round(st["sum_seconds"] + sp["duration"], 3)
            st["spans"] += 1
        for name, timing in self.stages.items():
            st = by_stage.setdefault(name, {"sum_seconds": 0.0, "spans": 0})
            st["stage_wall_seconds"] = timing.get("wall_seconds")
        longest_stage = max(self.stages, key=lambda name: self.stages[name].get("wall_seconds", 0.0),
                            default=None)
        stage_agents = [x for x in spans if x.get("kind") == "agent" and
                        (longest_stage is None or x.get("stage") == longest_stage)]
        slow = max(stage_agents, key=lambda s: s["duration"], default=None)
        total_seconds = round(time.perf_counter() - self._t0, 3)
        stage_wall_total = round(sum(s.get("wall_seconds", 0.0) for s in self.stages.values()), 3)
        return {"run_id": self.run_id, "company": self.company, "started": self.started,
                "total_seconds": total_seconds, "stage_wall_total_seconds": stage_wall_total,
                "overhead_seconds": round(total_seconds - stage_wall_total, 3),
                "stages": by_stage, "fetches": fetches, "bottleneck": None if not slow else {"name": slow["name"], "seconds": slow["duration"]},
                "fallbacks_used": [s["name"] for s in spans if s.get("kind") == "fetch" and s["fallback_used"]],
                "failures": [s["name"] for s in spans if s.get("kind") == "fetch" and s["status"] == "failed"]}

    def flush(self):
        if not self.out_dir:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = {"summary": None, "spans": self.spans, "provenance": self.provenance,
                       "data_sources": sorted(self.data_sources,
                                              key=lambda item: (item.get("category", ""), item.get("key", "")))}
        payload["summary"] = self.summary()
        (self.out_dir / "run_metrics.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
