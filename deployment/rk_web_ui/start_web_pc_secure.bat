@echo off
setlocal
cd /d "%~dp0"

if "%WEB_PASS%"=="" (
  echo Set WEB_PASS before starting a public or LAN-shared server.
  echo Example: set WEB_PASS=change-me
  echo Then run: start_web_pc_secure.bat
  exit /b 1
)

if "%PORT%"=="" set PORT=8081
if "%HOST%"=="" set HOST=0.0.0.0
if "%WEB_USER%"=="" set WEB_USER=admin

python server.py
