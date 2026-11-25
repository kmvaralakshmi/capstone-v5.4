# Limitations checklist (status after v5.3)

`[x]` fixed and tested here - `[~]` fixed in code, final verification needs your machine (internet / LLM / key) - `[ ]` open

## Scoring validity
- [x] AQI removed from the Environmental pillar (context only)
- [x] Market performance removed from Governance; news removed from Social
- [~] Anchors and weights: every anchor has a rationale in `config/anchors.json`, intensities are log-scaled, hygiene metrics down-weighted,
      sensitivity (300 trials) and saturation checks added. They are still expert-set: calibrating them against labelled ground truth needs data nobody has yet
- [x] Governance coverage: rebuilt on BRSR governance concepts; all 46 companies have a governance score; 44 reach the Pass gate (was 0)
- [x] Prior-year comparability: a >3x change is flagged as a suspected boundary change and excluded from improvement claims

## Real agents
- [x] Verified judgement tasks in Agents 0, 1, 2, 3, 5, 7, 8; Agents 4 and 6 are deterministic by design (documented)
- [x] Decision cap 10 -> 40, state truncation 2.5k -> 7k characters
- [x] LLM-mode tests with scripted fake models (accept / reject / correct / fall back)
- [~] A full batch with a real model: configure `.env`, run `python run_agents.py`, and check `llm` vs `rule` counts in `agent_run_report.json`

## Data and sources
- [~] Prices for >= 10 companies: run `python tools/fetch_market_snapshot.py` once (internet)
- [~] GDELT: retry, stricter round-up filter, fallback marker fixed; live behaviour needs your network
- [~] Live CPCB via data.gov.in: provider + cache written; needs `DATA_GOV_IN_API_KEY`. Locations now come from the filing's addresses [x]
- [x] Period check (FY2024-25) in Agents 0 and 1; Hexaware, Mindtree, E2E Networks, HCL Infosystems excluded with reasons
- [x] PDF reading 155 s -> ~3 s; PDFs are evidence only, so PDF extraction failures can no longer drop a metric
- [x] XBRL-vs-PDF: 7 mismatches left across the cohort, listed in `agent1_audit_report.csv` for manual review (XBRL is kept, flagged)
- [ ] OpenAQ fallback (spec A8) not implemented
- [ ] Official NSE market data (licensed access) instead of Yahoo Finance

## Validation
- [x] NSE Top 200 benchmark built (sector level); [x] NSE/CFA reference (2 comparable indicators); [x] golden end-to-end regression test
- [ ] External plausibility: our IT-cohort overall mean (82.1) sits at the top edge of NSE's IT range (71-82) and Governance (92.2) inside it (80-94), but Environmental (78.4) is above NSE's range (53-74); Social matches (75.4 vs 76). Report this honestly, or recalibrate the Environmental anchors with a justification (NSE uses a different, proprietary model)

## Repo, docs, paper
- [x] Legacy folders removed; README, RUN_GUIDE, CHANGELOG, docs rewritten; test count now 65 everywhere
- [x] `.env` is not in the delivered folder and `.gitignore` covers it. **If your original `.env` held real keys and was ever committed, rotate them.**
- [x] `docs/PAPER_NUMBERS.md` is generated from one run (`tools/export_paper_numbers.py`); the paper draft still quotes old numbers: update it from that file
- [x] Dashboard tested in a real browser (Infosys report)
- [ ] The Phase 2/3 report texts were not edited
