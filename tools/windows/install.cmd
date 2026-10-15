@echo off
rem Create the ACES venv (repo root\.venv) with formgen's verified dependency set.
setlocal
set "ROOT=%~dp0..\..\"
set "PY=py -3.11"
%PY% -c "import sys" >nul 2>&1 || set "PY=python"

if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo Creating venv in %ROOT%.venv
  %PY% -m venv "%ROOT%.venv" || exit /b 1
)
"%ROOT%.venv\Scripts\python.exe" -m pip install --upgrade pip
"%ROOT%.venv\Scripts\python.exe" -m pip install -r "%ROOT%requirements-formgen.txt" || exit /b 1
"%ROOT%.venv\Scripts\python.exe" -m pip install -e "%ROOT%.[formgen,dev]" || exit /b 1
echo.
echo ACES + formgen ready.  tools\windows\formgen-studio.cmd for the app, tools\windows\formgen.cmd --help
endlocal
