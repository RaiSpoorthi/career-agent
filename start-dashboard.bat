@echo off
rem Double-click to start the Career Agent dashboard and open it in your browser.
rem To stop it, close this window (or press Ctrl+C in it).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No .venv folder yet. Follow "Setup" in README.md first.
  pause
  exit /b 1
)
rem open the browser after the server has had a few seconds to start
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5055"
".venv\Scripts\python.exe" jobs.py ui
pause
