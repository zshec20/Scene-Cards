# Scene Cards

A small always-on-top note card for the desktop. It listens to MIDI from your
Allen & Heath dLive and, every time you recall a scene, shows the song title and
your notes for that scene. Go forward or back on the console and the card follows.

```
+------------------------------------------------+
| Ballad                                          |
| Long reverb on lead vox (FX 2).                 |
| Watch the keys - quiet pad, bring up.           |
|                                                 |
| Scene 4  ·  4 / 5  ·  Next: Closer              |
| ● dLive TCP: 192.168.1.70:51325 | last: Scene 4 |
+------------------------------------------------+
```

It is a single Python script with a tiny set-list file you edit in any text editor.
Works on macOS and Windows.

## 1. Install

1. Install Python 3 from <https://www.python.org/downloads/>. The python.org installer
   includes Tk, which draws the window.
   On a Mac, do this even if `python3 --version` already works: Apple's built-in
   Python 3.9 ships an old Tk that often draws blank or glitchy windows. After
   installing, run *Install Certificates.command* from the Finder window that opens,
   then quit and reopen Terminal.
2. Install Scene Cards with pip:

   ```
   python3 -m pip install git+https://github.com/zshec20/Scene-Cards.git
   ```

   If you don't have git, this does the same thing from a zip:

   ```
   python3 -m pip install https://github.com/zshec20/Scene-Cards/archive/refs/heads/main.zip
   ```

   That is all you need for the direct dLive network connection (option A below).
   To also listen to a MIDI port (option B: RTP-MIDI, the A&H MIDI driver, USB),
   install the MIDI add-on:

   ```
   python3 -m pip install "scene-cards[midi] @ git+https://github.com/zshec20/Scene-Cards.git"
   ```

   The MIDI add-on uses `python-rtmidi`, which only has ready-made downloads for
   Python 3.8 to 3.12. On Python 3.13 or newer, pip tries to build it and usually
   fails. If you need MIDI-port mode, install Python 3.12 from python.org
   (scroll down to "Looking for a specific release?") and use `python3.12` in place
   of `python3` in these commands.

3. Start it:

   ```
   scene-cards
   ```

   If Terminal says `command not found`, use `python3 -m scene_cards` instead.

To update to the latest version later, run the same pip command with `--upgrade`
added after `install`. To remove it: `python3 -m pip uninstall scene-cards`.

### Your files

The first run creates a **Scene Cards** folder in your **Documents** folder:

- `setlist.json` holds your songs. Edit this.
- `config.json` holds your settings, window size and position.

Right-click the card and choose *Show set list folder* to jump there. Updating or
reinstalling the app never touches these files. To keep a different set list per
show, point the app at another folder:

```
scene-cards --data ~/Documents/"Scene Cards - Main Stage"
```

### Without pip

Download the repo (green **Code** button, then **Download ZIP**), unzip it, and
double-click **Start Scene Cards.command** (Mac) or **Start Scene Cards.bat**
(Windows). On a Mac, if it says the file "can't be opened", right-click it and
choose Open.

## 2. Write your set list

Open `setlist.json` in *Documents/Scene Cards* (right-click the card > *Edit set list*,
or press **e**).
One entry per song, in set order:

```json
{
  "title": "Saturday - Main Stage",
  "songs": [
    { "scene": 1, "title": "Walk On",  "notes": "House music.\nAll vocal mics muted." },
    { "scene": 2, "title": "Opener",   "notes": "Unmute vox 1-3 on count-in.\nGTR solo 2:10 -> +3 dB." },
    { "scene": 3, "title": "Ballad",   "notes": "Long verb on lead vox (FX 2)." }
  ]
}
```

- `scene` is the dLive scene number (1-500). This is the normal way to use it.
- `notes` is what shows on the card. Use `\n` for a new line.
- Save the file and the card reloads by itself. If you make a typo, the card tells
  you which line.
- The footer always shows the last MIDI message received, so if you're not sure
  which scene number a song is, just recall it on the console and read it off the card.

Other triggers, if you ever need them:

- `"note": 60` matches a MIDI Note On (number 0-127). Use this with consoles or
  playback rigs that send one note per song.
- `"program": 5` matches a raw Program Change (0-127) ignoring the bank.
- `"channel": 3` on any song restricts that song to one MIDI channel.

## 3. Connect the dLive

dLive transmits a **Bank Select + Program Change** on its MIDI channel whenever a
scene is recalled (scene = bank x 128 + program + 1). You can get that into the
laptop two ways. Pick whichever you have cabled; both are in *Settings* (right-click
the card, or press **s**).

### Option A: straight over the network (easiest, no drivers)

dLive sends its MIDI over TCP on port **51325** from the MixRack and the Surface,
the same way dLive Director talks to it.

1. Put the laptop on the same network as the dLive's **Network** port (your Wi-Fi
   router is fine, or plug straight into the Surface/MixRack network port).
2. In Settings choose **dLive over the network** and type the MixRack IP
   (default `192.168.1.70`; check *Utility > Control > Network* on the Surface).
3. Save. The dot in the corner turns green when connected, and it reconnects on its
   own if the Wi-Fi drops.

Note: the **Dante card** carries audio only, not MIDI. If the laptop's only cable is
Dante, you still need the laptop on the dLive control network (the Wi-Fi router) or
use option B.

### Option B: a MIDI port (RTP-MIDI / Network Session, A&H MIDI driver, USB)

The app can listen to any MIDI input port the operating system shows. Set up the
port as you normally would, then pick it in Settings under **MIDI port**:

- **RTP-MIDI** on a Mac: *Audio MIDI Setup > Window > Show MIDI Studio > Network*,
  create a session and connect the remote. On Windows use Tobias Erichsen's
  **rtpMIDI** driver. The session appears as a port such as "Network Session 1".
- **Allen & Heath MIDI driver** (Mac/Win) creates virtual ports bridged to the
  console over the network. Pick the "dLive" or "MIDI In" port it creates.
- A **USB MIDI interface** on the Surface's MIDI Out DIN works too.

If the port isn't there yet when you start the app, it waits and connects as soon
as the port appears.

## Using it

| Action                    | How                                   |
| ------------------------- | ------------------------------------- |
| Settings                  | Right-click the card, or **s**        |
| Edit set list             | Right-click > Edit set list, or **e** |
| Step through songs        | **Left / Right** arrow keys           |
| Bigger / smaller text     | **+** / **-**                         |
| Keep on top of everything | Click **on top**, or **p**            |
| Opacity, MIDI channel     | Settings                              |

Window size and position are remembered between runs (saved in `config.json`).

## Files

| File                          | What it is                                                 |
| ----------------------------- | ---------------------------------------------------------- |
| `scene_cards/app.py`          | The window                                                 |
| `scene_cards/midi_events.py`  | Decodes dLive scene recalls from raw MIDI; MIDI port + TCP |
| `scene_cards/setlist.py`      | Loads and checks `setlist.json`                            |
| `tests/`                      | Tests: `python3 -m unittest discover -s tests`             |
| `pyproject.toml`              | Makes the repo pip-installable                             |
| `requirements.txt`            | `mido` and `python-rtmidi`, for the no-pip launchers       |

## Troubleshooting

- **"MIDI library not available"**: you're in MIDI-port mode without the MIDI add-on.
  Either switch Settings to *dLive over the network*, which needs nothing extra, or
  install the add-on as shown in step 2 of Install.
- **`scene-cards: command not found`**: run `python3 -m scene_cards` instead. Or quit
  and reopen Terminal, which picks up the newly installed command.
- **pip says "externally-managed-environment"** (Homebrew Python): use the python.org
  installer instead, or run `pipx install git+https://github.com/zshec20/Scene-Cards.git`.
- **"tkinter is missing"** (Homebrew Python on Mac): `brew install python-tk`.
- **Card shows "Scene 12 - No card for this scene"**: the console sent scene 12 but
  there is no entry for it. Add one, or check the MIDI channel in Settings.
- **Nothing arrives in TCP mode**: ping the MixRack IP from the laptop; the dot stays
  red with the reason while it keeps retrying.
