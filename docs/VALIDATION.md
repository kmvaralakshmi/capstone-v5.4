# Validation

Five analyses plus two structural checks, all written by Agent 8 to `processed-data/validation_*.csv` and `validation_summary.json`.

1. **Our ESG vs NSE Top 200** - sector level only (the NSE report gives IT-sector max/avg/median/min for overall, E, S, G; no per-company
   scores). We compare our IT-cohort mean with the NSE IT average and range. Different methodology (SES ESG), different year: read as a
   plausibility check, not agreement. Result in `docs/PAPER_NUMBERS.md`.
2. **Our BRSR coverage vs NSE/CFA** - share of companies with a verified energy and Scope 1+2 value vs the CFA study (FY2022-23, 300 companies).
3. **ESG vs stock** - Pearson and Spearman, only with n >= 10 companies with prices (association, not causation).
4. **ESG vs news** - same rule.
5. **ESG vs air quality** - exploratory association of the Environmental pillar (which contains no AQI term) with headquarters AQI.
6. **Sensitivity** - 300 random perturbations of every anchor, the trust and hygiene weights and the pillar weights; reports Spearman
   vs baseline, rank shift, top-quartile retention, risk-band stability.
7. **Calibration** - share of companies at the ceiling/floor of each scored metric; flags metrics that cannot discriminate.

Statistics with n < 10 are reported as "insufficient sample", never as numbers.
