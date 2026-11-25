# Explainable Multi-Agent ESG Risk Analysis for Indian Companies (v5.4)

Scores Indian listed companies from their SEBI **BRSR** filings (FY 2024-25) and checks every number
against independent evidence. Guiding rule: **abstain rather than guess** - a value that cannot be read,
verified or audited is marked NOT_FOUND / QUARANTINED, never defaulted.

```
python run_agents.py --mode offline      # whole cohort, bundled data, NO LLM   -> processed-data/batch_offline/
python run_agents.py --mode dynamic      # whole cohort, live data + LLM        -> processed-data/batch_dynamic/
python run_agents.py --company Infosys --mode offline|dynamic      # one company -> data/runs/<mode>/...
python -m unittest discover -s tests     # 76 tests, incl. a golden end-to-end regression
python -m uvicorn backend.app:app --port 8000   # dashboard: http://localhost:8000  (Offline | Dynamic toggle, top right)
python tools/export_source_reconciliation.py  # exact XBRL/PDF sources + normalized values
```

**Two modes, two separate dashboards.** *Offline* = bundled data and no LLM (deterministic, reproducible, fast).
*Dynamic* = live Yahoo prices and GDELT news, and the LLM (if available). Every page shows only what the selected
mode actually executed; the two result sets are never mixed. See `RUN_GUIDE.md`.

## Structure

| Path | What |
|---|---|
| `esg_agents/agent0_…agent8_*.py` | one file per agent (see `docs/AGENTS.md`) |
| `esg_agents/framework/` | agent runtime (observe-decide-guardrails-act-reflect), LLM brain, **verified judgement layer** |
| `esg_agents/engine/` | exact-concept XBRL reader, metric registry, plausibility audit, PDF evidence, anchors loader |
| `config/` | cohort (`companies.csv`, `locations.csv`), exclusions with reasons, **`anchors.json`** (all scoring parameters) |
| `data/downloads/{xbrl,brsr}` | the company filings (source documents) |
| `data/benchmark/` | fixtures for testing/validation (prices, news, air quality, NSE Top 200 and NSE/CFA references) |
| `data/cache/` | regenerable caches (PDF text, live API responses) |
| `processed-data/batch_offline/`, `batch_dynamic/` | outputs of the last full batch of each mode |
| `data/runs/{offline,dynamic}/` | on-demand single-company runs (only finished runs are listed in the dashboard) |
| `backend/`, `frontend/` | FastAPI + 12-page dashboard |
| `tools/` | download/discovery scripts, benchmark builder, market snapshot, paper-number export |
| `docs/` | methodology, agents, data sources, validation, spec compliance, limitations checklist |

## What changed in v5.4 (short)

Two separate modes with a dashboard toggle; unfinished/failed runs hidden (and cleanable); single-run rank removed; consistent
rounding; trend changes shown in the right unit (pp / absolute / %) with a materiality rule; stricter ESG-news filter;
LLM narration timeout with a visible reason; size-aware LLM budgets. Details: `CHANGELOG.md`.

## What changed in v5.3 (short)

Scoring now uses **only company-reported, audited BRSR metrics** (16 scored metrics, 5 E / 5 S / 6 G, all read from
named XBRL concepts). Air quality, news and market data are context and validation evidence, not score inputs.
Governance is built from BRSR governance concepts (the old independent-director / board-meeting / audit-committee
metrics are annual-report items, not BRSR KPIs, which is why they were never available). Agents make verified
LLM/rule **judgements** (scale repair, article classification, claim verdicts, explanation, interpretation).
Details: `CHANGELOG.md`, `docs/LIMITATIONS_CHECKLIST.md`.

Scores are research indicators from a single reporting year, not an official SEBI rating.
