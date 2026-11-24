@echo off
set PYTHONUTF8=1
if exist "%~dp0..\.venv\Scripts\jobhunt.exe" (
  "%~dp0..\.venv\Scripts\jobhunt.exe" %*
) else (
  echo jobhunt: no .venv found - see docs\getting-started.md 1>&2
  exit /b 127
)
