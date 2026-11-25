@echo off
cd /d "%~dp0"
REM Whole cohort with LIVE prices and news (+ LLM if available). Slow. Results: processed-data\batch_dynamic
python run_agents.py --mode dynamic
pause
