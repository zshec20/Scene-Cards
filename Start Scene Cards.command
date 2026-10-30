#!/bin/bash
# macOS launcher: double-click this file. (First time: right-click > Open, or run
# `chmod +x "Start Scene Cards.command"` in Terminal if macOS refuses to run it.)
cd "$(dirname "$0")"
python3 -m pip install --quiet -r requirements.txt 2>/dev/null || python3 -m pip install --quiet --user -r requirements.txt
python3 -m scene_cards
