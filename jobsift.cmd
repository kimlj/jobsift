@echo off
rem jobsift, run with this folder's own Python environment, from anywhere:
rem   jobsift.cmd --setup      jobsift.cmd --once      jobsift.cmd --export jobs.csv
rem It runs inside this folder, because config.yaml, .env and data\ live here.
pushd "%~dp0"
".venv\Scripts\python.exe" -m jobsift %*
set JOBSIFT_EXIT=%ERRORLEVEL%
popd
exit /b %JOBSIFT_EXIT%
