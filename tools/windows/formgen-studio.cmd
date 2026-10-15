@echo off
rem formgen Studio -- double-click me.
setlocal
set "ROOT=%~dp0..\..\"
if not exist "%ROOT%.venv\Scripts\pythonw.exe" (
  echo No venv yet -- running install.cmd first...
  call "%~dp0install.cmd" || exit /b 1
)
set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
start "" "%ROOT%.venv\Scripts\pythonw.exe" -m formgen.studio
endlocal
