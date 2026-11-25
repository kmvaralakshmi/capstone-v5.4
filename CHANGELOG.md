# Changelog

## v5.5
- Tightened ESG-news relevance: removed generic environment/social/governance-only matches, rejected market round-ups and Nature=other articles, and added `Retain_Reason`.
- Added physical plausibility bounds for total energy, water, waste and Scope 1+2 emissions, and added deterministic zero-value handling.
- Removed the hard-coded differently-abled population-share recommendation and added safe Markdown rendering for explanations.
- Changed correlation readings to require statistical significance and rejected `Nature=other` news from greenwashing evidence.
- Added omitted-deterioration claims and low-evidence shrinkage in Agent 5.
- Ranked only Pass companies using unrounded scores and updated governance hygiene weight to 0.25.
- Regenerated the offline golden fixture and added v5.5 regression tests.

## v5.4
**Two separate modes**
- `run_agents.py --mode offline|dynamic` (legacy `--offline/--dynamic` still work). Offline = bundled data, no LLM; dynamic = live data + LLM.
  Results never mix: `processed-data/batch_<mode>/` and `data/runs/<mode>/`. The dashboard has an Offline | Dynamic toggle; every page, the cohort,
  the validation page and the company search show only the selected mode and say what was not executed in it.
- Run history: only finished runs (every agent completed, scores written) are listed; one row per company with "+N earlier"; delete and
  "delete unfinished/failed" actions; `run_mode.json` records mode, live data and LLM use.

**Corrections**
- Rank is blank in a single-company run (needs a cohort of >= 5); the dashboard shows the batch rank "4/46" when a batch exists.
- Score rounding is half-up everywhere (the text said 86.2 while the ring showed 86.3).
- "x/18 metrics" label replaced by "x/16 scored metrics" (`Scored_Metrics_Total`).
- Agent 7: LLM narration has its own timeout (`LLM_NARRATIVE_TIMEOUT`) and token cap, a compact evidence prompt, and a visible reason (`LLM_Note`)
  when the template is used (timeout, failed grounding check, budget).
- News: deterministic guard + stricter LLM prompt: product launches, partnerships, deals and investments are not ESG articles unless the text contains
  an environmental/social/governance fact.
- Trends: percentage metrics show percentage points, linear metrics absolute change with a 5%-of-anchor-span materiality rule ("tiny base"),
  totals and log-scale intensities relative change with a 2% rule. Labels read "better / worse / flat".
- Market page: signed bars (green right / red left). Agent 5 no longer crashes when a prior value is 0.
- LLM budgets are size-aware (on-demand run: 6 decisions / 40 judgements / 420 s; batch: 10 / 300 / 1800 s).
- 76 tests (new: modes, run filtering, rounding, news guard, narration errors).

## v5.3
**Structure**
- New exact-concept extraction engine (`esg_agents/engine/`): every metric is read from a named BRSR XBRL concept and context. The fuzzy legacy extractors (`agents/`, `utils/`, `v4_core/`, `v5_core/`, `ai_agents/`, `legacy/`, `legacy-v1-outputs/`, `demo-output/`) were removed.
- PDF is evidence only (PyMuPDF): a 369-page filing is read in ~3 s (was ~155 s) and cached.
- Data layout per spec A17: `data/downloads/{xbrl,brsr}`, `data/benchmark/…`, `data/cache/`, `processed-data/`.
- Cohort 50 -> 46: Mindtree (legacy symbol of LTIM), E2E Networks and HCL Infosystems (no BRSR filing) and Hexaware (calendar-year reporter) moved to `config/excluded_companies.csv` with reasons.

**Scoring (fixes to specification A6/C9)**
- AQI removed from the Environmental pillar; market performance removed from Governance; news removed from Social. They are context columns and validation inputs.
- 16 scored metrics (E 5, S 5, G 6). Governance now uses BRSR governance concepts (female directors, board/KMP disciplinary actions, conflict-of-interest complaints, anti-corruption policy, cyber/data-privacy policy, board sustainability committee). Independent directors, board meetings and audit-committee size were annual-report items, not BRSR XBRL concepts.
- All parameters live in `config/anchors.json` with rationale. Intensities use a log scale (a linear scale saturated 76-92% of the cohort). Hygiene metrics count at weight 0.5.
- Quality gate now reachable: Pass = 12+ scored metrics and 3+ per pillar.

**Agents**
- Verified judgement layer (`BaseAgent.judge`): LLM answers are accepted only after code verification (schema, evidence ids exist, verbatim quotes, candidate-set membership); otherwise a deterministic rule answers and the source is recorded (`llm` / `rule` / `rule_fallback`).
- Judgement tasks: A0 company disambiguation, A1 unit-scale repair (PDF-stated unit decides; no guessing), A2 spelling-variant city match, A3 article classification with verbatim evidence quote, A5 claim verdicts, A7 explanation, A8 cautious interpretation. A4 and A6 stay deterministic by design (arithmetic).
- Agent 5 investigates real claims (reported improvements + achievement statements found in the PDF text), with cited evidence ids and confidence.
- Decision cap 10 -> 40, state truncation 2.5k -> 7k characters.

**Data and validation**
- Locations from the filing's own office addresses; live CPCB (data.gov.in) provider with cache; GDELT retry and stricter market round-up filter; telemetry fallback marker fixed.
- Agent 8: NSE Top 200 comparison at sector level (the report publishes no per-company scores), NSE/CFA indicators, sensitivity analysis (300 perturbation trials), calibration/saturation check, verified interpretations.
- 65 tests including a golden end-to-end regression and fake-LLM tests of every judgement.
