@echo off
title MetroLite Launcher
cd /d "%~dp0"
echo Starting MetroLite Backend...
start "MetroLite Backend" /b uv run python -m metrolite run
timeout /t 2 /nobreak >nul
cd /d "%~dp0ui"
echo Starting Frontend Dev Server...
start "MetroLite Vite" /b npx vite --port 1420
timeout /t 2 /nobreak >nul
echo Launching MetroLite Window...
start "" "%~dp0ui\src-tauri\target\debug\metrolite.exe"
