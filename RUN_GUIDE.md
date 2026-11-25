# Run guide (v5.4)

## 1. Install
```
python -m venv .venv && .venv\Scripts\activate        # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                                 # optional: LLM settings
python -m unittest discover -s tests                   # expect: Ran 76 tests ... OK
```

## 2. The two modes
| | **OFFLINE** | **DYNAMIC** |
|---|---|---|
| Data | bundled datasets only | live Yahoo Finance prices + GDELT news (CPCB/air data as configured) |
| LLM | never | used where available (Ollama / OpenAI / compatible); falls back to rules if not |
| Speed | seconds to ~2 min | minutes (LLM latency) |
| Reproducible | yes, identical every run | no: prices, news and LLM judgements change |
| Batch results | `processed-data/batch_offline/` | `processed-data/batch_dynamic/` |
| On-demand runs | `data/runs/offline/` | `data/runs/dynamic/` |

The dashboard toggle (top right) switches between the two. Each mode shows **only its own results**; pages say when
something was not executed in that mode (e.g. no news in the bundled corpus for a company).

## 3. Commands
| Goal | Command |
|---|---|
| Whole cohort, offline | `python run_agents.py --mode offline` |
| Whole cohort, dynamic (slow) | `python run_agents.py --mode dynamic` (add `--no-llm` for live data without the LLM) |
| One company | `python run_agents.py --company "Infosys" --mode offline` or `--mode dynamic` |
| Company outside the cohort | `python run_agents.py --company HDFCBANK --hq-city Mumbai --mode dynamic` |
| Subset of companies / agents | `--companies TCS INFY` / `--agents agent6 agent7 agent8` |
| Check Ollama / Yahoo / GDELT first | `python -m esg_agents.diagnostics` |
| Dashboard | `python -m uvicorn backend.app:app --port 8000`, open http://127.0.0.1:8000 |

Windows double-click files: `run_offline_batch.bat`, `run_dynamic_batch.bat`, `run_company.bat`, `start_backend.bat`.

## 4. Dashboard usage
1. Start the backend, open the page, choose **Offline** or **Dynamic** (remembered in the browser).
2. *Analyze Company*: type a name or NSE symbol (HQ city only for a company outside the 50-company cohort). In Dynamic mode
   a "use LLM" box lets you skip the LLM. The progress card shows each agent; "Open results" appears when it finishes.
3. *Previous analyses* lists **finished runs only**, one row per company (older runs of the same company sit under
   "+N earlier"). Unfinished or failed runs are hidden; a link deletes them.
4. *Search cohort company* (top right) opens a company from the selected mode's batch or its latest finished run.
5. *Cohort Ranking* and *Validation & Research* show the selected mode's batch. If it does not exist yet, the page offers
   a button to run it.

## 5. LLM setup (Dynamic mode, optional and free)
Install Ollama, `ollama pull qwen2.5:7b-instruct`, set `LLM_PROVIDER=ollama` in `.env`, then run `python -m esg_agents.diagnostics`.
A local model on a CPU is slow; budgets are small by default for on-demand runs (see `.env.example`). The explanation is written
by the LLM only if it passes the grounding check; otherwise a template is used and the dashboard says why.

## 6. One-time data tasks (need internet)
```
python tools/fetch_market_snapshot.py      # prices for the whole cohort -> data/benchmark/stock/market_snapshot.csv
python tools/discover_brsr_xbrl.py --all   # fetch missing XBRL filings from NSE
python tools/download_brsr_reports.py      # fetch missing BRSR PDFs
python tools/build_benchmarks.py --top200-pdf "<NSE ESG Analysis on Top 200 report>.pdf"
```

After `fetch_market_snapshot.py`, the OFFLINE mode uses that snapshot (still reproducible), and Agent 8's ESG-vs-market analysis activates once at least 10 companies have prices. The DYNAMIC mode always fetches fresh prices.

## 7. Tests
`python -m unittest discover -s tests` (76 tests; the golden test re-runs the whole pipeline). If you change scoring or anchors on
purpose, run `python tests/make_golden.py` and explain the change in `CHANGELOG.md`.

## 8. Reading the outputs (a batch or run folder)
`esg_master_scores.csv` (scores, gate, context) - `esg_metric_scores.csv` (per-metric score, anchors, source) -
`brsr_extracted_metrics.csv` (every value with concept, context, PDF page, confidence, audit note) - `brsr_trends.csv`
(prior vs current, change in pp / absolute / %, direction) - `agent1_audit_report.csv` - `greenwashing_claims.csv` -
`multi_agent_explanations.csv` (text, source, **LLM_Note**) - `validation_summary.json` (+ `validation_*.csv`) -
`run_metrics.json` (timing) - `run_mode.json` (mode, LLM used) - `agent_traces/` (every decision, with its source).
