"""
Set list loading and matching for Scene Cards.

setlist.json looks like:

{
  "songs": [
    {"scene": 1,  "title": "Walk On",  "notes": "House music, lights down."},
    {"scene": 2,  "title": "Opener",   "notes": "Vox 1 +3dB on chorus.\nDelay throw on last word."},
    {"note": 60,  "title": "Encore",   "notes": "Triggered by MIDI note 60 instead of a scene."}
  ]
}

Each song needs a "title" and ONE trigger:
    "scene":   dLive scene number (1..500)          <- normal case
    "note":    MIDI note number (0..127)
    "program": raw Program Change number (0..127), ignoring bank
Optional: "channel": 1..16 to only match that MIDI channel.
Optional: "notes": the text shown on the card (use \n for line breaks).
"""

import json
import os


class SetlistError(Exception):
    pass


class Song:
    def __init__(self, index, data):
        if not isinstance(data, dict):
            raise SetlistError("song %d is not an object { ... }" % (index + 1))
        self.index = index
        self.title = str(data.get("title", "") or "Untitled %d" % (index + 1))
        self.notes = str(data.get("notes", "") or "")
        self.channel = data.get("channel")
        self.triggers = {}
        for key in ("scene", "note", "program"):
            if key in data and data[key] is not None:
                try:
                    self.triggers[key] = int(data[key])
                except (TypeError, ValueError):
                    raise SetlistError("song %d (%s): '%s' must be a number"
                                       % (index + 1, self.title, key))
        if not self.triggers:
            raise SetlistError("song %d (%s) has no 'scene', 'note' or 'program'"
                               % (index + 1, self.title))

    def matches(self, event):
        if self.channel is not None and int(self.channel) != event.channel:
            return False
        want = self.triggers.get(event.kind)
        return want is not None and want == event.number

    def trigger_label(self):
        return ", ".join("%s %d" % (k, v) for k, v in self.triggers.items())


class Setlist:
    def __init__(self, songs, title=""):
        self.songs = songs
        self.title = title

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise SetlistError("Set list file not found: %s" % path)
        except json.JSONDecodeError as e:
            raise SetlistError("setlist.json has a typo at line %d, column %d: %s"
                               % (e.lineno, e.colno, e.msg))
        if isinstance(data, list):
            raw_songs, title = data, ""
        elif isinstance(data, dict):
            raw_songs, title = data.get("songs", []), str(data.get("title", ""))
        else:
            raise SetlistError("setlist.json must contain {\"songs\": [ ... ]}")
        if not isinstance(raw_songs, list):
            raise SetlistError("\"songs\" must be a list [ ... ]")
        songs = [Song(i, s) for i, s in enumerate(raw_songs)]
        return cls(songs, title)

    def find(self, event):
        for song in self.songs:
            if song.matches(event):
                return song
        return None

    def next_after(self, song):
        if song is None or song.index + 1 >= len(self.songs):
            return None
        return self.songs[song.index + 1]

    def prev_before(self, song):
        if song is None or song.index == 0:
            return None
        return self.songs[song.index - 1]

    def __len__(self):
        return len(self.songs)


def write_example(path):
    """Create a starter setlist.json if none exists."""
    if os.path.exists(path):
        return
    example = {
        "title": "Example Set",
        "songs": [
            {"scene": 1, "title": "Walk On",
             "notes": "House music from playback.\nAll vocal mics muted."},
            {"scene": 2, "title": "Opener",
             "notes": "Unmute vox 1-3 on count-in.\nGuitar solo at 2:10 -> +3 dB on GTR 1."},
            {"scene": 3, "title": "Second Song",
             "notes": "Acoustic intro: only vox 1 + AC GTR.\nFull band drops at verse 2."},
            {"scene": 4, "title": "Ballad",
             "notes": "Long reverb on lead vox (FX 2).\nWatch the keys - quiet pad, bring up."},
            {"scene": 5, "title": "Closer",
             "notes": "Delay throw on last 'goodnight'.\nMute everything after final hit."},
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(example, f, indent=2)
        f.write("\n")
