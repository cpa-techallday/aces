@echo off
rem formgen CLI:  formgen panel.step --mode panel --strut 60 --model --printer x1c
setlocal
set "ROOT=%~dp0..\..\"
if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo No venv yet -- running install.cmd first...
  call "%~dp0install.cmd" || exit /b 1
)
set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
"%ROOT%.venv\Scripts\python.exe" -m formgen.cli %*
endlocal
