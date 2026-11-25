# Agents

All agents subclass `framework.BaseAgent`: **observe -> decide -> guardrails -> act -> reflect** until a completion test
written in code returns true. They share a blackboard, run under one supervisor (A1-A4 in parallel) and log every decision
and judgement with its origin (LLM / policy / guardrail / rule / rule_fallback) in `processed-data/agent_traces/`.

Two ways an LLM takes part: it may *choose tools* (guardrails reject unknown tools, bad arguments, premature finish), and it
may make *judgements*: structured answers (JSON) that code then verifies; an unverified answer is discarded for the rule.
Arithmetic is never delegated to an LLM.

| Agent | File | Real judgement task (LLM when configured, else rule) | Code verification of the answer | Deterministic parts |
|---|---|---|---|---|
| 0 Filing discovery | `agent0_filing_discovery.py` | which configured company a free-text query means | choice must be a listed candidate or `none` | NSE lookup, download, XBRL period check |
| 1 Extraction & audit | `agent1_brsr_extraction.py` | which unit-scale correction fixes an implausible quantity | must be a plausible candidate; if the PDF prints the unit that scale is mandatory | exact XBRL concepts, PDF corroboration, bounds |
| 2 Environmental context | `agent2_environmental.py` | whether an unmatched office city is a spelling variant of a monitored city | choice must be among fuzzy candidates | CPCB AQI arithmetic, headquarters from filing address |
| 3 News | `agent3_news_sentiment.py` | ESG relevance, pillar, polarity, allegation vs verified finding | fixed label sets; evidence quote must be verbatim in the article | attribution (word boundary), shrinkage, reliability |
| 4 Market | `agent4_financial.py` | none (pure arithmetic: returns, volatility, drawdown) | - | all |
| 5 Consistency | `agent5_greenwashing.py` | verdict on each claim: consistent / potential inconsistency / insufficient evidence | cites only existing evidence ids; no inconsistency without contradicting evidence; confidence limits | claim extraction, signals, trust score |
| 6 Scoring | `agent6_master_scoring.py` | none by design (reproducible scoring) | - | anchors, pillars, gate, flags |
| 7 Explainability | `agent7_explainability.py` | company-specific explanation from the evidence package | every number must exist in the evidence, every sentence cites an id | evidence package, deterministic text |
| 8 Validation | `agent8_validation_research.py` | cautious interpretation of each result | numbers must exist in the statistics; no causal wording | statistics, sensitivity, calibration |

Honest status: judgements are tested with scripted fake LLMs (`tests/`). A run with a real model is configured through `.env`
but has not been executed in the build environment; the trace shows `llm` vs `rule` per judgement so you can verify it on your machine.
