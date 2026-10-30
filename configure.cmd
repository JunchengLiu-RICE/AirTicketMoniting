@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" configure.py
) else (
  python configure.py
)
if errorlevel 1 (
  echo.
  echo Setup help: create a Python environment and run pip install -r requirements.txt.
  pause
)
endlocal
