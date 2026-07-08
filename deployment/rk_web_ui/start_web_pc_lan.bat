@echo off
setlocal
cd /d "%~dp0"

rem PC 局域网调试：同一 Wi-Fi/网段内的设备可通过本机 IP 访问。
if "%PORT%"=="" set PORT=8081
set HOST=0.0.0.0

python server.py
