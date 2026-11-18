@echo off
rem Windows launcher: runs the Python script next to this file with the Python launcher, or python.
where py >nul 2>nul
if %errorlevel%==0 (py -3 "%~dp0vpn" %*) else (python "%~dp0vpn" %*)
