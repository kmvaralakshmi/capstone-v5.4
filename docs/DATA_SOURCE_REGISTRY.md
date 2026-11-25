# Data source registry

| Dimension | Source | Role | Access | Cache / fallback |
|---|---|---|---|---|
| Company-reported ESG | NSE BRSR filings: XBRL instance + PDF (Corporate Filings > Business Sustainability Reports) | primary + evidence | `tools/discover_brsr_xbrl.py`, `tools/download_brsr_reports.py` (NSE session cookies; may need retries) | files kept in `data/downloads/` as source documents |
| BRSR XBRL taxonomy | NSE XBRL taxonomy (`Taxonomy_Archives` zip supplied by the team) | concept names / meaning | static | - |
| Market performance | Yahoo Finance via `yfinance` (**deviation from spec A9: NSE official data needs licensed access**) | association studies only | `--dynamic`, or `tools/fetch_market_snapshot.py` | 6 h cache; snapshot CSV; bundled 7-company fixture |
| External news | GDELT DOC API (titles only; 12 s timeout, one retry, circuit breaker) | consistency evidence | `--dynamic` | 3 h cache; bundled Zenodo ESG-news corpus (labelled as fallback) |
| Air-quality context | CPCB real-time data via data.gov.in (`DATA_GOV_IN_API_KEY`) | context | `--dynamic` + key | 3 h cache; bundled CPCB snapshot + 2015-2024 city history |
| OpenAQ | not integrated (spec lists it as a fallback; needs a key) | - | - | - |
| NSE sector benchmark | NSE "ESG Analysis on Top 200 Listed Companies" (2026): sector statistics (the report has no per-company scores) | validation | `tools/build_benchmarks.py` | `data/benchmark/nse_top200_*.csv` |
| BRSR disclosure rates | CFA Institute / NSE / CFA Society India, "The Current State of BRSR at Corporate India" (Oct 2024; FY2022-23, 300 companies) | validation of extraction coverage | quoted figures | `data/benchmark/nse_cfa_reference.csv` |

Every external value is labelled live / cached / static fixture / fallback in the run telemetry (`run_metrics.json`) and on the
dashboard's Sources & Evidence and Performance pages.
