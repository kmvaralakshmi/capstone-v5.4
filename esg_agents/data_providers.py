"""Dynamic data providers with caching, fallback and telemetry.

    market  : Yahoo Finance via `yfinance` (free, no key, covers every NSE ticker
              as SYMBOL.NS)  ->  fallback: bundled price fixture + optional snapshot
    news    : GDELT DOC 2.0 API (free, no key)  ->  fallback: bundled news CSV
    air     : data.gov.in CPCB feed when DATA_GOV_IN_API_KEY is set, otherwise
              the bundled CPCB snapshot; OpenAQ v3 is an optional provider helper

NOTE: Yahoo is not an official NSE feed. It is used because the official NSE
historical endpoints need session handling/licensing; MarketProvider is a small
interface, so an NSE-bhavcopy provider can be added without touching agents.

Each fetch returns a DataResult with explicit source provenance and (when a
RunTracker is given) records its span and provenance for the run report.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .common import ROOT

CACHE = ROOT / "data" / "cache"
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "25"))


@dataclass
class DataResult:
    status: str                       # success | cached | fallback | failed
    source: str
    payload: Any = None
    seconds: float = 0.0
    fallback_used: bool = False
    error: Optional[str] = None
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    provenance: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("success", "cached", "fallback") and self.payload is not None


def _cache_path(kind: str, key: str) -> Path:
    d = CACHE / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json")


def _cache_get(kind: str, key: str, max_age_s: int):
    p = _cache_path(kind, key)
    if p.exists() and time.time() - p.stat().st_mtime < max_age_s:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _cache_put(kind: str, key: str, value) -> None:
    _cache_path(kind, key).write_text(json.dumps(value, default=str), encoding="utf-8")


def _cache_get_stale(kind: str, key: str) -> Optional[Any]:
    p = _cache_path(kind, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _date_bounds(values, dayfirst: bool = False):
    dates = pd.to_datetime(pd.Series(values), errors="coerce", utc=True, dayfirst=dayfirst).dropna()
    if dates.empty:
        return None, None
    return dates.min().date().isoformat(), dates.max().date().isoformat()


def _record_source(tracker, key: str, record: Dict[str, Any]) -> None:
    if tracker is not None:
        tracker.record_data_source(key, record)


# ----------------------------------------------------------------- market ---
def market_metrics(close: pd.Series, fy_start="2024-04-01", fy_end="2025-03-31",
                   as_of: Optional[str] = None) -> Dict[str, Any]:
    """Return/volatility/drawdown figures from a date-indexed close series."""
    s = close.dropna().sort_index()
    if as_of:
        s = s[s.index <= pd.Timestamp(as_of)]
    if len(s) < 60:
        return {}
    end = s.index.max()

    def ret(days):
        w = s[s.index >= end - pd.Timedelta(days=days)]
        return None if len(w) < 5 else float(w.iloc[-1] / w.iloc[0] - 1) * 100

    fy = s[(s.index >= fy_start) & (s.index <= fy_end)]
    fy_ret = None if len(fy) < 100 else float(fy.iloc[-1] / fy.iloc[0] - 1) * 100
    base = fy if len(fy) >= 100 else s[s.index >= end - pd.Timedelta(days=365)]
    daily = base.pct_change().dropna()
    return {"Return_1M_Pct": ret(30), "Return_3M_Pct": ret(91), "Return_6M_Pct": ret(182),
            "Return_1Y_Pct": ret(365), "Return_3Y_Pct": ret(365 * 3),
            "FY_Return_Pct": fy_ret,
            "Volatility_Pct": float(daily.std() * np.sqrt(252) * 100) if len(daily) > 20 else None,
            "Max_Drawdown_Pct": float((base / base.cummax() - 1).min() * 100) if len(base) > 20 else None,
            "Data_Points": int(len(s)), "Window_End": str(end.date()),
            "Window_Policy": "capped_to_offline_history" if as_of else "latest_available"}


_STATIC = {"df": None}


def _static_frame() -> Optional[pd.DataFrame]:
    """Offline prices: the bundled benchmark fixture plus an optional snapshot produced by
    tools/fetch_market_snapshot.py (run once with internet access; keeps later runs reproducible)."""
    if _STATIC["df"] is None:
        files = [ROOT / "data" / "benchmark" / "stock" / "prices_fixture.csv",
                 ROOT / "data" / "benchmark" / "stock" / "market_snapshot.csv"]
        frames = []
        for f in files:
            if f.exists():
                d = pd.read_csv(f, usecols=["Date", "Close", "Ticker"])
                d["Ticker"] = d["Ticker"].str.replace(".BO", ".NS", regex=False)
                frames.append(d)
        _STATIC["df"] = pd.concat(frames).drop_duplicates(["Ticker", "Date"], keep="last") if frames else pd.DataFrame()
    return _STATIC["df"]


def _static_close(ticker: str) -> Optional[pd.Series]:
    all_ = _static_frame()
    if all_ is None or all_.empty:
        return None
    d = all_[all_["Ticker"] == ticker]
    if d.empty:
        return None
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
    d = d.dropna(subset=["Date"]).set_index("Date")["Close"]
    return d if len(d) else None


def _static_window_end(ticker: str, alternates: List[str]) -> Optional[str]:
    for symbol in [ticker] + alternates:
        close = _static_close(symbol)
        if close is not None:
            return str(close.index.max().date())
    return None


def fetch_market(ticker: str, dynamic: bool = True, tracker=None, stage: str = "data_acquisition",
                 alternates: Optional[List[str]] = None, company_code: Optional[str] = None,
                 fallback_reason: Optional[str] = None) -> DataResult:
    t0 = time.perf_counter()
    span = tracker.span(f"market:{ticker}", stage, "yfinance" if dynamic else "static") if tracker else None
    rec = span.__enter__() if span else {}
    try:
        res = _fetch_market_inner(ticker, dynamic, alternates or [])
        if res.ok and not dynamic and fallback_reason is None:
            res.status, res.fallback_used, res.error = "success", False, None
        elif res.ok and fallback_reason:
            res.status, res.fallback_used, res.error = "fallback", True, fallback_reason
        res.seconds = round(time.perf_counter() - t0, 3)
        if res.status == "cached":
            kind = "cache"
        elif dynamic and res.fallback_used:
            kind = "bundled"
        elif dynamic:
            kind = "live"
        elif fallback_reason:
            kind = "bundled"
        else:
            kind = "static"
        payload = res.payload if isinstance(res.payload, dict) else {}
        res.provenance = {
            "category": "prices", "company_code": company_code, "source": res.source,
            "kind": kind, "as_of": payload.get("Window_End"),
            "rows": int(payload.get("Data_Points", 0)),
            "status": "failed" if not res.ok else "fallback" if res.fallback_used else "success",
            "note": res.error or "",
        }
        _record_source(tracker, f"prices:{company_code or ticker}", res.provenance)
        rec.update({"status": "success" if res.ok else "failed", "fallback_used": res.fallback_used,
                    "source": res.source, "error": res.error})
        if res.fallback_used and dynamic:
            rec["status"] = "failed"
        if span:
            span.__exit__(None, None, None)
        return res
    except Exception as exc:  # pragma: no cover
        failure = DataResult("failed", "Yahoo Finance", error=str(exc))
        failure.provenance = {
            "category": "prices", "company_code": company_code, "source": failure.source,
            "kind": "live" if dynamic else "static", "as_of": None, "rows": 0,
            "status": "failed", "note": str(exc),
        }
        _record_source(tracker, f"prices:{company_code or ticker}", failure.provenance)
        if span:
            span.__exit__(type(exc), exc, None)
        return failure


def _fetch_market_inner(ticker: str, dynamic: bool, alternates: List[str]) -> DataResult:
    err = None
    if dynamic:
        as_of = _static_window_end(ticker, alternates)
        for tk in [ticker] + list(alternates):
            cached = _cache_get("market", tk, 6 * 3600)
            if cached:
                s = pd.Series(cached["close"], index=pd.to_datetime(cached["dates"]))
                return DataResult("cached", "yfinance(cache)",
                                  market_metrics(s, as_of=as_of) | {"Source": f"yfinance(cache) {tk}"})
            try:
                import yfinance as yf
                h = yf.Ticker(tk).history(period="4y", auto_adjust=True)
                if h is not None and len(h) > 60:
                    s = h["Close"].copy()
                    s.index = pd.to_datetime(s.index).tz_localize(None)
                    _cache_put("market", tk, {"dates": [str(i.date()) for i in s.index], "close": [float(x) for x in s.values]})
                    return DataResult("success", "yfinance",
                                      market_metrics(s, as_of=as_of) | {"Source": f"yfinance {tk}"})
                err = f"yfinance: no data for {tk}"
            except Exception as exc:
                err = f"yfinance {tk}: {type(exc).__name__}: {exc}"
    s = _static_close(ticker)
    if s is not None:
        m = market_metrics(s)
        if m:
            return DataResult("fallback", "static price fixture/snapshot", m | {"Source": "static"}, fallback_used=True, error=err)
    return DataResult("failed", "market", None, error=err or "no price data available")


# ------------------------------------------------------------------- news ---
GDELT_TIMEOUT = int(os.getenv("GDELT_TIMEOUT_SECONDS", "12"))
GDELT_TIMESPANS = [os.getenv("GDELT_TIMESPAN", "3m"), "12w", "90d"]
_GDELT = {"fails": 0, "ok": 0, "consecutive_fails": 0, "last_error": None}


def reset_gdelt_circuit() -> None:
    _GDELT.update({"fails": 0, "ok": 0, "consecutive_fails": 0, "last_error": None})


def gdelt_last_error() -> Optional[str]:
    return _GDELT["last_error"]


def fetch_news(query_name: str, tracker=None, stage: str = "data_acquisition",
               timespan: str = "", maxrecords: int = 120,
               company_code: Optional[str] = None) -> DataResult:
    """GDELT DOC 2.0 article list (titles/urls/dates only).

    Fails fast: short timeout, and after 2 failures with no success the circuit opens so a
    50-company batch does not spend 25+ minutes waiting on an unreachable service."""
    t0 = time.perf_counter()
    span = tracker.span(f"news:{query_name}", stage, "GDELT") if tracker else None
    rec = span.__enter__() if span else {}
    key = f"{query_name}|v2"
    cached = _cache_get("news", key, 3 * 3600)
    if cached is not None:
        res = DataResult("cached", "GDELT(cache)", cached)
    elif _GDELT["consecutive_fails"] >= 2:
        stale = _cache_get_stale("news", key)
        if stale:
            res = DataResult("cached", "GDELT(stale-cache)", stale,
                             fallback_used=True, error=f"circuit open (GDELT unreachable): {_GDELT['last_error']}")
        else:
            res = DataResult("failed", "GDELT", None, error=f"circuit open (GDELT unreachable): {_GDELT['last_error']}")
    else:
        try:
            import requests
            q = (f'"{query_name}" (ESG OR sustainability OR emissions OR pollution OR governance '
                 f'OR fraud OR penalty OR lawsuit OR diversity OR layoffs OR climate)')
            arts, err = None, None
            for ts in ([timespan] if timespan else GDELT_TIMESPANS):
                params = {"query": q, "mode": "ArtList", "format": "json", "maxrecords": maxrecords,
                          "timespan": ts, "sort": "DateDesc"}
                try:
                    r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=GDELT_TIMEOUT)
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    time.sleep(6.0)
                    r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=GDELT_TIMEOUT)
                if r.status_code == 429:
                    retry_after = r.headers.get("Retry-After")
                    delay = min(60.0, max(6.0, float(retry_after) if retry_after and retry_after.isdigit() else 12.0))
                    time.sleep(delay)
                    r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=GDELT_TIMEOUT)
                    if r.status_code == 429:
                        raise RuntimeError("GDELT HTTP 429 rate limit persisted after retry")
                if r.status_code != 200:
                    err = f"HTTP {r.status_code}: {r.text[:120]}"
                    continue
                try:
                    arts = (r.json() or {}).get("articles", [])
                    break
                except ValueError:          # GDELT answers plain text for invalid parameters
                    err = f"non-JSON reply for timespan={ts}: {r.text[:120]!r}"
            if arts is None:
                raise RuntimeError(err or "no usable reply")
            _cache_put("news", key, arts)
            _GDELT["ok"] += 1
            _GDELT["consecutive_fails"] = 0
            res = DataResult("success", "GDELT", arts)
        except Exception as exc:
            _GDELT["fails"] += 1
            _GDELT["consecutive_fails"] += 1
            _GDELT["last_error"] = f"{type(exc).__name__}: {exc}"[:240]
            res = DataResult("failed", "GDELT", None, error=_GDELT["last_error"])
    res.seconds = round(time.perf_counter() - t0, 3)
    rows = res.payload if isinstance(res.payload, list) else []
    date_min, date_max = _date_bounds([row.get("seendate") for row in rows if isinstance(row, dict)])
    kind = "cache" if res.status == "cached" else "live"
    res.provenance = {
        "category": "news", "company_code": company_code, "source": res.source,
        "kind": kind, "date_min": date_min, "date_max": date_max, "rows": len(rows),
        "status": "failed" if not res.ok else "fallback" if res.fallback_used else "success",
        "note": res.error or "",
    }
    _record_source(tracker, f"news:{company_code or query_name}", res.provenance)
    rec.update({"status": "failed" if not res.ok else "success",
                "fallback_used": res.fallback_used, "source": res.source, "error": res.error})
    if span:
        span.__exit__(None, None, None)
    return res


# -------------------------------------------------------------------- air ---
def fetch_openaq_city(city: str, tracker=None, stage: str = "data_acquisition") -> DataResult:
    span = tracker.span(f"air_quality:openaq:{city}", stage, "OpenAQ v3") if tracker else None
    rec = span.__enter__() if span else {}

    def finish(result: DataResult, external_attempted: bool = True) -> DataResult:
        _record_source(tracker, f"air_quality:openaq:{city}", result.provenance)
        rec.update({"source": result.source,
                    "status": "failed" if external_attempted and not result.ok else "success" if result.ok else "skipped",
                    "error": result.error})
        if span:
            span.__exit__(None, None, None)
        return result

    key = os.getenv("OPENAQ_API_KEY")
    if not key:
        res = DataResult("failed", "OpenAQ v3", None, error="OPENAQ_API_KEY not set (optional)")
        res.provenance = {"category": "air_quality", "source": res.source, "kind": "live",
                          "as_of": None, "rows": 0, "status": "failed", "note": res.error}
        return finish(res, external_attempted=False)
    t0 = time.perf_counter()
    try:
        import requests
        r = requests.get("https://api.openaq.org/v3/locations", headers={"X-API-Key": key},
                         params={"limit": 20, "iso": "IN", "parameters_id": 2},   # 2 = PM2.5
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        rows = [x for x in r.json().get("results", []) if city.lower() in json.dumps(x).lower()]
        res = DataResult("success", "OpenAQ v3", rows, seconds=round(time.perf_counter() - t0, 3))
        res.provenance = {"category": "air_quality", "source": res.source, "kind": "live",
                          "as_of": res.retrieved_at[:10], "rows": len(rows),
                          "status": "success", "note": ""}
        return finish(res)
    except Exception as exc:
        res = DataResult("failed", "OpenAQ v3", None, error=f"{type(exc).__name__}: {exc}")
        res.provenance = {"category": "air_quality", "source": res.source, "kind": "live",
                          "as_of": None, "rows": 0, "status": "failed", "note": res.error}
        return finish(res)


CPCB_RESOURCE = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"       # data.gov.in: real-time AQI (CPCB), pollutant rows
CPCB_TTL_HOURS = 3
CPCB_CACHE_PATH = ROOT / "data" / "cache" / "air_quality" / "cpcb_realtime.json"
CPCB_SNAPSHOT_PATH = ROOT / "data" / "benchmark" / "air_quality" / "realtime_aqi.csv"
CPCB_REQUIRED_COLUMNS = {"city"}


def fetch_cpcb_realtime(tracker=None, force: bool = False) -> DataResult:
    """Live CPCB real-time pollutant rows from data.gov.in (needs DATA_GOV_IN_API_KEY).

    Returns the selected rows and their provenance. Responses are cached for
    CPCB_TTL_HOURS; a missing key or failed live request is explicit in the record."""
    import requests

    t0 = time.perf_counter()
    span = tracker.span("air_quality:cpcb", "data_acquisition", "data.gov.in CPCB") if tracker else None
    rec = span.__enter__() if span else {}

    def finish(result: DataResult, external_failed: bool = False) -> DataResult:
        result.seconds = round(time.perf_counter() - t0, 3)
        _record_source(tracker, "air_quality:cpcb", result.provenance)
        rec.update({"source": result.source, "status": "failed" if external_failed else "success",
                    "fallback_used": result.fallback_used, "error": result.error})
        if span:
            span.__exit__(None, None, None)
        return result

    def bundled(note: str, error: Optional[str] = None, external_failed: bool = False) -> DataResult:
        frame = pd.read_csv(CPCB_SNAPSHOT_PATH) if CPCB_SNAPSHOT_PATH.exists() else pd.DataFrame()
        date_min, date_max = _date_bounds(frame["last_update"], dayfirst=True) if "last_update" in frame else (None, None)
        date = date_max if date_min == date_max else None
        result = DataResult("fallback", "bundled CPCB snapshot", frame,
                            fallback_used=True, error=error)
        result.provenance = {
            "category": "air_quality", "source": "CPCB realtime_aqi.csv", "kind": "bundled",
            "as_of": date, "date_min": date_min, "date_max": date_max, "rows": len(frame),
            "status": "failed" if external_failed else "fallback", "note": note,
        }
        return finish(result, external_failed)

    def stale_cache(note: str) -> Optional[DataResult]:
        try:
            frame = pd.DataFrame(json.loads(CPCB_CACHE_PATH.read_text(encoding="utf-8")))
            if frame.empty or not CPCB_REQUIRED_COLUMNS.issubset(frame.columns):
                return None
            date_min, date_max = _date_bounds(frame["last_update"], dayfirst=True) if "last_update" in frame else (None, None)
            cache_age = max(0, int(time.time() - CPCB_CACHE_PATH.stat().st_mtime))
        except (OSError, ValueError, TypeError):
            return None
        message = f"{note}; using stale data.gov.in CPCB cache ({cache_age // 3600}h old)"
        result = DataResult("fallback", "data.gov.in CPCB stale cache", frame,
                            fallback_used=True, error=message)
        result.provenance = {
            "category": "air_quality", "source": result.source, "kind": "cache",
            "as_of": date_max if date_min == date_max else None, "date_min": date_min,
            "date_max": date_max, "rows": len(frame), "status": "fallback", "note": message,
        }
        return finish(result, external_failed=True)

    key = os.getenv("DATA_GOV_IN_API_KEY", "").strip()
    if not key:
        return bundled("DATA_GOV_IN_API_KEY not set: bundled CPCB snapshot")

    if CPCB_CACHE_PATH.exists() and not force and time.time() - CPCB_CACHE_PATH.stat().st_mtime < CPCB_TTL_HOURS * 3600:
        try:
            frame = pd.DataFrame(json.loads(CPCB_CACHE_PATH.read_text(encoding="utf-8")))
            if frame.empty or not CPCB_REQUIRED_COLUMNS.issubset(frame.columns):
                raise ValueError("cached CPCB response has an invalid schema")
            date_min, date_max = _date_bounds(frame["last_update"], dayfirst=True) if "last_update" in frame else (None, None)
            result = DataResult("cached", "data.gov.in CPCB cache", frame)
            result.provenance = {
                "category": "air_quality", "source": result.source, "kind": "cache",
                "as_of": date_max if date_min == date_max else None, "date_min": date_min,
                "date_max": date_max, "rows": len(frame), "status": "success",
                "note": f"cached CPCB response (under {CPCB_TTL_HOURS} hours old)",
            }
            return finish(result)
        except (OSError, ValueError, TypeError):
            pass

    try:
        timeout = min(float(os.getenv("DATA_GOV_IN_TIMEOUT_SECONDS", "20")), 20.0)
        page_size = min(max(int(os.getenv("DATA_GOV_IN_PAGE_SIZE", "500")), 1), 1000)
        fetch_budget = max(float(os.getenv("DATA_GOV_IN_FETCH_BUDGET_SECONDS", "60")), 1.0)
        if timeout <= 0:
            raise ValueError("DATA_GOV_IN_TIMEOUT_SECONDS must be positive")
        deadline = time.monotonic() + fetch_budget
    except ValueError as exc:
        return bundled(f"invalid data.gov.in request configuration: {exc}")

    rows, offset, limit = [], 0, page_size
    try:
        while True:
            page = None
            for attempt in range(2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("CPCB fetch exceeded DATA_GOV_IN_FETCH_BUDGET_SECONDS")
                try:
                    response = requests.get(
                        f"https://api.data.gov.in/resource/{CPCB_RESOURCE}",
                        params={"api-key": key, "format": "json", "limit": limit, "offset": offset},
                        timeout=min(timeout, remaining),
                    )
                    status_code = response.status_code if isinstance(response.status_code, int) else 200
                    if status_code == 429 or 500 <= status_code < 600:
                        if attempt == 0:
                            retry_after = response.headers.get("Retry-After", "")
                            delay = min(2.0, max(0.25, float(retry_after))) if retry_after.isdigit() else 1.0
                            time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
                            continue
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict) or not isinstance(body.get("records"), list):
                        raise ValueError("data.gov.in returned an invalid CPCB response")
                    page = body["records"]
                    break
                except (requests.Timeout, requests.ConnectionError):
                    if attempt:
                        raise
                    time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            if page is None:
                raise RuntimeError("data.gov.in did not return a CPCB page")
            rows.extend(page)
            if len(page) < limit or offset > 20000:
                break
            offset += limit
    except Exception as exc:
        detail = str(exc).replace(key, "[redacted]")[:180]
        note = f"data.gov.in fetch failed ({type(exc).__name__}: {detail})"
        cached = stale_cache(f"{note}; live CPCB request failed")
        if cached is not None:
            return cached
        note += "; bundled CPCB snapshot used"
        return bundled(note, error=note, external_failed=True)
    if not rows:
        note = "data.gov.in returned no rows"
        cached = stale_cache(f"{note}; live CPCB request returned an empty result")
        if cached is not None:
            return cached
        note += "; bundled CPCB snapshot used"
        return bundled(note, error=note, external_failed=True)

    frame = pd.DataFrame(rows)
    if not CPCB_REQUIRED_COLUMNS.issubset(frame.columns):
        note = "data.gov.in returned CPCB rows with an invalid schema"
        cached = stale_cache(f"{note}; live CPCB response could not be used")
        if cached is not None:
            return cached
        note += "; bundled CPCB snapshot used"
        return bundled(note, error=note, external_failed=True)
    CPCB_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CPCB_CACHE_PATH.write_text(frame.to_json(orient="records"), encoding="utf-8")
    date_min, date_max = _date_bounds(frame["last_update"], dayfirst=True) if "last_update" in frame else (None, None)
    result = DataResult("success", "data.gov.in CPCB", frame)
    result.provenance = {
        "category": "air_quality", "source": result.source, "kind": "live",
        "as_of": date_max if date_min == date_max else None, "date_min": date_min,
        "date_max": date_max, "rows": len(frame), "status": "success", "note": "",
    }
    return finish(result)