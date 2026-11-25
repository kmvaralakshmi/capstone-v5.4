"""Download daily closing prices for the whole cohort once and store them for offline, reproducible runs.

    python tools/fetch_market_snapshot.py            # needs internet; uses Yahoo Finance via yfinance

Writes data/benchmark/stock/market_snapshot.csv (Date, Close, Ticker). Agent 4 reads it automatically
whenever live fetching is off or fails, and labels those prices as a static snapshot. Market data is
ONLY used for association studies (Agent 8) and context; it never enters the ESG score.
"""
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from esg_agents.common import COMPANIES, TICKER_OVERRIDES  # noqa: E402


def main() -> int:
    import yfinance as yf
    out, rows, failed = ROOT / "data" / "benchmark" / "stock" / "market_snapshot.csv", [], []
    for code, info in COMPANIES.items():
        tickers = [info["ticker"]] + TICKER_OVERRIDES.get(code, [])
        for tk in tickers:
            try:
                h = yf.Ticker(tk).history(period="4y", auto_adjust=True)
            except Exception as exc:
                print(f"{code} {tk}: {type(exc).__name__}")
                continue
            if h is not None and len(h) > 60:
                d = pd.DataFrame({"Date": pd.to_datetime(h.index).tz_localize(None).strftime("%Y-%m-%d"),
                                  "Close": h["Close"].values, "Ticker": info["ticker"]})
                rows.append(d)
                print(f"{code}: {len(d)} rows from {tk}")
                break
        else:
            failed.append(code)
        time.sleep(0.5)
    if not rows:
        print("no prices downloaded")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(rows).to_csv(out, index=False)
    print(f"wrote {out} ({sum(len(r) for r in rows)} rows); no data for: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
