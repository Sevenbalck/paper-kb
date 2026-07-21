@echo off
cd /d "%~dp0"
echo Avvio backend paper-kb su http://127.0.0.1:8000 ...
echo (lascia questa finestra aperta finche' usi il frontend - Ctrl+C per fermare)
echo.
uv run uvicorn backend:app --reload --port 8000
pause
