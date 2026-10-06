@echo off
rem Link the ACES formgen add-in into Fusion's AddIns folder (a junction, so Fusion runs the repo code).
setlocal
set "SRC=%~dp0..\..\fusion_addin\Formgen"
set "ADDINS=%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns"
if not exist "%ADDINS%" (
  echo Fusion's AddIns folder was not found at "%ADDINS%". Is Fusion installed for this user?
  exit /b 1
)
if exist "%ADDINS%\Formgen" (
  echo "%ADDINS%\Formgen" already exists; remove it first if it points somewhere else.
  dir /al "%ADDINS%" | findstr /i Formgen
  exit /b 1
)
mklink /J "%ADDINS%\Formgen" "%SRC%" || exit /b 1
if not exist "%~dp0..\..\.venv\Scripts\python.exe" echo Run tools\windows\install.cmd too: the add-in needs the repo .venv.
echo Done. Restart Fusion, or run Formgen from Scripts and Add-Ins.
endlocal
