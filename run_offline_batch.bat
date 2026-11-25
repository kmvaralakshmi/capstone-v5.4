@echo off
cd /d "%~dp0"
REM Whole cohort, bundled data, no LLM (results: processed-data\batch_offline)
python run_agents.py --mode offline
pause
