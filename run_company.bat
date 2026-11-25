@echo off
cd /d "%~dp0"
set /p CO=Company name or NSE symbol: 
set /p MD=Mode (offline or dynamic): 
python run_agents.py --company "%CO%" --mode %MD%
pause
