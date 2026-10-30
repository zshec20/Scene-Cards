@echo off
rem Windows launcher: double-click this file.
cd /d "%~dp0"
python -m pip install --quiet -r requirements.txt
python -m scene_cards
pause
