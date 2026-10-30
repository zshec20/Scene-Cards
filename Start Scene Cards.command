#!/bin/bash
# macOS launcher: double-click this file. (First time: right-click > Open, or run
# `chmod +x "Start Scene Cards.command"` in Terminal if macOS refuses to run it.)
cd "$(dirname "$0")"
if ! python3 -m pip install --quiet -r requirements.txt >/dev/null 2>&1; then
  echo "Note: the MIDI-port libraries didn't install (they need Python 3.12 or older)."
  echo "The direct dLive network connection still works."
fi
python3 -m scene_cards
