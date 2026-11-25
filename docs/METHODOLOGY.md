# Methodology

**Source hierarchy.** XBRL (primary, exact concept + context) > BRSR PDF (evidence: same number printed on a page with the
metric's anchor words; page and snippet recorded) > nothing. No value is defaulted.

**Audit.** Hard bounds per metric. Filers sometimes enter a quantity in the wrong unit (revenue in crore, energy labelled J but
printed in GJ). Implausible intensities open a scale issue; candidate rescalings are generated; the correction is applied only when
the PDF states the unit, or when exactly one rescaling is plausible; otherwise the dependent metrics are quarantined.
Values rescaled this way carry `Final_Source = XBRL_RESCALED`, lower confidence and `Needs_Manual_Review`.

**Metric score** (0-10, `config/anchors.json`): linear, or log for intensities, between `anchor_0` and `anchor_10`.

**Pillar** = (1 - 0.15) x weighted mean of metric scores + 0.15 x Trust (Agent 5, 0-10). >= 2 metrics required.
Hygiene metrics (policy exists / zero incidents) have weight 0.5.

**Master** = 10 x (0.33 E + 0.33 S + 0.34 G), weights renormalised over the pillars that exist.

**Quality gate.** Pass: >= 12 usable scored metrics and >= 3 in every pillar. Partial: >= 6 and >= 2 pillars (published and flagged).
Otherwise the score is withheld. **Risk bands** (of the 0-100 score): Low >= 70, Medium 50-70, High < 50.

**Trust score** (Agent 5) = 10 - sum(weight x signal): XBRL/PDF disagreement (2.0), audit repairs/quarantines (1.5), cohort intensity
outlier |z| > 2 on a log scale (2.0), claim with a potential inconsistency (3.0), disclosure gap (1.0). Level: High >= 4 points, Moderate >= 2.

**Not in the score (specification A6/C9):** air quality, news sentiment, market performance. They are independent evidence *about*
the company: used for consistency checks (news, PDF), context (AQI) and association studies (Agent 8, only with n >= 10).

**Mode agreement.** Offline and Dynamic batches remain separate. For a ticker with bundled offline prices, Dynamic market
metrics are cut off at that ticker's last offline price date so returns use a comparable window; tickers without bundled
history use the latest available Dynamic window. Compare matched-company scores and reported market windows with
`python tools/evaluate_mode_agreement.py` after both batches have completed.

**Known modelling limits.** Anchors and weights are expert-set; the sensitivity analysis shows how much rankings move but does not
calibrate them. One reporting year. Governance metrics are mostly hygiene checks that nearly all companies pass; governance quality
beyond BRSR (independent directors, board meetings) needs the annual report and is out of scope.
