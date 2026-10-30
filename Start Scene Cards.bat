@echo off
rem Windows launcher: double-click this file.
cd /d "%~dp0"
python -m pip install --quiet -r requirements.txt >nul 2>&1
if errorlevel 1 (
  echo Note: the MIDI-port libraries didn't install ^(they need Python 3.12 or older^).
  echo The direct dLive network connection still works.
)
python -m scene_cards
pause
