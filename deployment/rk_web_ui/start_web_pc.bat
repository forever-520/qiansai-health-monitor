@echo off
setlocal
cd /d "%~dp0"

rem PC 本机调试默认只监听本机，避免无意开放到局域网。
if "%PORT%"=="" set PORT=8081
if "%HOST%"=="" set HOST=127.0.0.1

python server.py
