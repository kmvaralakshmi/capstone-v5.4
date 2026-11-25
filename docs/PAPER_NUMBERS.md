# Numbers for the paper (generated from `batch_offline` - run id 20261125_123352)

Regenerate with `python tools/export_paper_numbers.py` after any pipeline run; never type these by hand.

## Cohort

- companies in cohort: 46; scored: 46; withheld (insufficient evidence): 0
- quality gate: {'Pass': 43, 'Partial': 3}
- risk bands: {'Medium': 23, 'Low': 22, 'High': 1}
- master score: min 49.9, max 83.4, mean 68.1, median 69.1
- top 5: Infosys 83.4; Persistent Systems 82.2; HCL Technologies 81.6; Tech Mahindra 80.6; Tata Consultancy Services 80.2
- bottom 3: Nazara Technologies 51.3; Delhivery 50.4; Mazagon Dock Shipbuilders 49.9
- scored metrics per company: mean 15.1 of 16 (min 11)
- companies with a governance score: 46

## Extraction and audit

- metric rows: 1058; verified/valid: 977; repaired: 13; quarantined: 47; not found: 12
- PDF evidence: {'MATCH': 179, 'NO_PDF': 115, 'MATCH_AFTER_SCALE': 8, 'NOT_FOUND': 6, 'MISMATCH': 4, 'MANUAL_PDF': 2}
- agent decisions: 756 policy, 0 LLM; judgements by source: {'llm': 0, 'rule': 425, 'rule_fallback': 0}

## Validation

- nse_top200: ok
- nse_cfa: ok
- esg_stock: insufficient sample (n=7); run in --dynamic mode so more companies have prices
- esg_news: insufficient sample (n=0); run in --dynamic mode (GDELT) for more articles
- esg_air: ok
- sensitivity: ok
- calibration: ok
- sensitivity (300 trials): median Spearman 0.977, 5th pct 0.959, mean rank shift 2.13, risk band unchanged 0.87
- vs NSE IT sector (Overall): our mean 76.6 (n=11) vs NSE average 77, range 71-82
- vs NSE IT sector (Environmental): our mean 74.1 (n=11) vs NSE average 64, range 53-74
- vs NSE IT sector (Social): our mean 71.3 (n=11) vs NSE average 76, range 64-83
- vs NSE IT sector (Governance): our mean 84.2 (n=11) vs NSE average 85, range 80-94

## Offline/Dynamic agreement

- matched companies: 46; scored in both modes: 46; quality-gate agreement: 1.0
- absolute score difference: mean 0.0, median 0.0, max 0.0
- score correlation: Pearson 1.0, Spearman rank 1.0
- market windows: 7/7 matched tickers share an end date (consistent: True); offline ['2025-11-11'], dynamic ['2025-11-11']

## Runtime

- agent1: 11.33 s
- agent2: 13.46 s
- agent3: 3.46 s
- agent4: 3.82 s
- agent5: 2.91 s
- agent6: 0.42 s
- agent7: 0.46 s
- agent8: 4.17 s

## Data-source caveats

- Air quality: bundled CPCB data; a live data.gov.in feed is optional and requires a privately configured API key.
- News: GDELT is requested in dynamic mode with a bundled fallback; the bundled corpus covers 2025-02-07 to 2025-08-21.

## Dynamic TCS planner ablation

- off (`TCS_20261125_123500`): 1199.644 s; 0 planning calls; 12 LLM judgements; planning 0.00 s / judging 600.57 s; master score 80.2
- recover (`TCS_20261125_150426`): 2396.4 s; 1 planning calls; 9 LLM judgements; planning 33.30 s / judging 525.66 s; master score 80.2
- score comparison: identical (absolute difference 0.00)
