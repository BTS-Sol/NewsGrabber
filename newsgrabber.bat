@echo off
rem Startet den NewsGrabber aus diesem Ordner - egal, in welchem Ordner man gerade steht.
rem Beispiele:
rem   "C:\...\NewsGrabber\newsgrabber.bat" init
rem   "C:\...\NewsGrabber\newsgrabber.bat" erstellen
rem   "C:\...\NewsGrabber\newsgrabber.bat" erstellen --datum 2026-10-14
rem Ohne -c wird newsgrabber.yaml in diesem Ordner verwendet (relative Pfade gelten ab hier).
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
if not defined NEWSGRABBER_KONFIG set "NEWSGRABBER_KONFIG=%~dp0newsgrabber.yaml"
set "PYTHONUTF8=1"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m newsgrabber %*
) else (
    python -m newsgrabber %*
)
exit /b %ERRORLEVEL%
