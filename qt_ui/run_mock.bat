@echo off
cd /d "%~dp0"
set RADAR_MOCK=1
set RADAR_COMPACT=1
python -m pip install -r requirements.txt
python main.py
