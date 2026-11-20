@echo off
rem Double-click to start the Career Agent dashboard and open it in your browser.
rem Keep this window open while you use the dashboard; close it to stop the dashboard.
cd /d "%~dp0"
set URL=http://127.0.0.1:5055
if not exist ".venv\Scripts\python.exe" (
  echo No .venv folder yet. Follow "Setup" in README.md first.
  pause
  exit /b 1
)
rem Already running (e.g. you double-clicked twice)? Just open the page.
powershell -NoProfile -Command "try { Invoke-WebRequest %URL% -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  echo The dashboard is already running. Opening %URL% ...
  start "" %URL%
  exit /b 0
)
echo Starting the Career Agent dashboard. Your browser opens by itself in a few seconds.
echo If it doesn't, open %URL% yourself. Keep this window open; close it to stop the dashboard.
echo.
rem Open the browser as soon as the dashboard answers (up to a minute).
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for ($i=0; $i -lt 60; $i++) { try { Invoke-WebRequest %URL% -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process %URL%; break } catch { Start-Sleep 1 } }"
".venv\Scripts\python.exe" jobs.py ui
echo.
echo The dashboard stopped. Read the message above, then press a key to close this window.
pause
