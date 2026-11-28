@echo off
cd /d "%~dp0"
wscript.exe //nologo "%~dp0launch_silent.vbs"
exit /b 0
