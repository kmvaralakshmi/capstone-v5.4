# Specification compliance (Sections A, B, C)

Status: **Done** = implemented and tested here. **Partly** = implemented, but a part could not be verified in the build
environment (no internet, no LLM) or deliberately deviates. **Open** = not done.

## A - Project improvements
| | Status | Notes |
|---|---|---|
| A1 XBRL primary, PDF evidence | Done | exact concepts; PDF page + snippet; mismatches flagged (`agent1_audit_report.csv`), never silently resolved |
| A2 automatic filing discovery | Partly | Agent 0 + `tools/discover_brsr_xbrl.py`; needs NSE access, not exercised offline. Company disambiguation judgement tested |
| A3 dynamic data + cache | Done | market 6 h, news 3 h, CPCB 3 h caches; fixtures separate from caches. Live paths need internet |
| A4 ESG history vs market history | Done | prior-year values come from the filing's own XBRL; market is separate |
| A5 parallel fetch with dependencies | Done | A1-A4 parallel under the supervisor; wall-clock vs sum recorded |
| A6 source roles explicit | Done | and now enforced in the score: AQI, news, market are not score inputs |
| A7 GDELT dynamic news + static fixture | Partly | retry, round-up filter, labelled fallback; live GDELT not testable here |
| A8 CPCB / data.gov.in, OpenAQ fallback | Partly | data.gov.in provider + cache (needs `DATA_GOV_IN_API_KEY`); **OpenAQ fallback not implemented** |
| A9 NSE market data | Partly (deviation) | Yahoo Finance is used; `tools/fetch_market_snapshot.py` makes runs reproducible. Official NSE data needs licensed access |
| A10 key / rate-limit / fallback policy | Done | every fallback is labelled in telemetry; circuit breaker for GDELT |
| A11 static datasets for tests | Done | benchmark fixtures + golden end-to-end regression (`tests/golden/`) |
| A12 rules for maths, agents for judgement | Done | `docs/AGENTS.md` |
| A13-A15 architecture, timing | Done | per-stage and per-task timings, bottleneck reported |
| A16 provenance per metric | Done | concept, context, unit label, PDF page, confidence, source, audit note |
| A17 data layout | Done | `data/downloads`, `data/benchmark`, `data/cache`, `processed-data` |
| A18 source registry | Done | `docs/DATA_SOURCE_REGISTRY.md` |

## B - Frontend
12 pages (Analyze, Dashboard, ESG Performance, Market, News, Environmental Risk, Greenwashing, Agent Activity, Performance, Sources &
Evidence, Validation & Research, Cohort). **Done and browser-tested** (headless Chromium, Infosys report loaded): every page renders, no
script errors, no undefined/NaN. Not done: visual design review, mobile layout, the live "Analyze" progress screen against a real
`--dynamic` job (needs internet).

## C - Explainability and validation
| | Status | Notes |
|---|---|---|
| C1 evidence-grounded explanation agent | Partly | deterministic evidence-grounded text with an LLM rewrite path and a grounding check; the LLM rewrite is tested with a fake model only |
| C2, C3 no own ML model, no SHAP/LIME | Done | |
| C4 NSE Top 200 validation | Done at sector level | the report has no per-company scores; mean difference and range check per pillar |
| C5 NSE/CFA comparison | Partly | two comparable indicators (energy, Scope 1+2 reporting); other CFA statistics have no counterpart in our metrics |
| C6 ESG vs stock | Blocked by data | n = 7 < 10 until `tools/fetch_market_snapshot.py` is run |
| C7 ESG vs news | Blocked by data | n = 5 < 10 until a `--dynamic` GDELT run |
| C8 cross-source consistency | Done | claims -> evidence ids -> verdict with confidence; wording is "potential inconsistency" |
| C9 air quality as context | Done | removed from the score; exploratory association only |
| C10 Validation & Research page | Done | five analyses + sensitivity + calibration, with caveats and sample sizes |
