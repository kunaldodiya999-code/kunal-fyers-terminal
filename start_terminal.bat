@echo off
REM Starts the Kunal FYERS Terminal (double-click this file). Keep this window open while trading.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run:  python setup_pc.py
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run app.py
pause
