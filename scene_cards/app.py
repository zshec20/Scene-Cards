#!/usr/bin/env python3
"""
Scene Cards - a small always-on-top note card for live mixing.

Listens for MIDI from an Allen & Heath dLive (or any console) and shows the
song title + your notes for the scene that was just recalled. Step forward or
back on the console and the card follows.

Run:   scene-cards            (after: pip install git+https://github.com/zshec20/Scene-Cards.git)
  or:  python3 -m scene_cards (from a downloaded copy of the repo)
Edit:  Documents/Scene Cards/setlist.json - one entry per song / scene
"""

import argparse
import json
import os
import queue
import subprocess
import sys

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:  # pragma: no cover
    print("tkinter is missing. On macOS with Homebrew: brew install python-tk\n"
          "Or install Python from python.org, which includes it.")
    sys.exit(1)

from scene_cards.midi_events import (MidiEvent, MidiParser, PortSource, TcpSource,
                         list_midi_ports, midi_library_error)
from scene_cards.setlist import Setlist, SetlistError, write_example

from scene_cards import __version__


def default_data_dir():
    """Where setlist.json and config.json live: Documents/Scene Cards."""
    env = os.environ.get("SCENE_CARDS_DIR")
    if env:
        return os.path.expanduser(env)
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return os.path.join(docs if os.path.isdir(docs) else home, "Scene Cards")


DATA_DIR = default_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SETLIST_PATH = os.path.join(DATA_DIR, "setlist.json")


def set_data_dir(path):
    global DATA_DIR, CONFIG_PATH, SETLIST_PATH
    DATA_DIR = os.path.abspath(os.path.expanduser(path))
    CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
    SETLIST_PATH = os.path.join(DATA_DIR, "setlist.json")
    os.makedirs(DATA_DIR, exist_ok=True)

DEFAULTS = {
    "source": "midi",            # "midi" = a MIDI port (RTP-MIDI, driver, USB)
                                 # "tcp"  = direct to dLive on port 51325
    "midi_port": "",
    "tcp_host": "192.168.1.70",  # dLive MixRack default IP
    "tcp_port": 51325,
    "channel": 0,                # 0 = listen on all MIDI channels
    "font_scale": 1.0,
    "opacity": 0.96,
    "topmost": True,
    "geometry": "440x280+60+60",
}

BG = "#141414"
FG_TITLE = "#ffffff"
FG_NOTES = "#e8e8e8"
FG_DIM = "#8c8c8c"
OK = "#3ddc84"
BAD = "#ff5c5c"
WARN = "#ffb648"


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def open_in_editor(path):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path] if os.path.isdir(path) else ["open", "-t", path])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        messagebox.showerror("Scene Cards", "Couldn't open %s\n%s" % (path, e))


class SceneCardsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.queue = queue.Queue()
        self.parser = MidiParser(self._on_event)
        self.source = None
        self._retry_job = None
        self.setlist = Setlist([], "")
        self.current = None
        self._setlist_mtime = None
        self._connected = False
        self._status_text = "Starting..."
        self._last_midi = ""

        self.topmost_var = tk.BooleanVar(value=bool(self.cfg.get("topmost", True)))
        self.title("Scene Cards")
        self.configure(bg=BG)
        self.geometry(self.cfg.get("geometry", DEFAULTS["geometry"]))
        self.minsize(260, 150)
        self._apply_window_attrs()

        self._build_widgets()
        self._bind_keys()
        self._load_setlist(first=True)
        self._start_source()
        self.after(40, self._poll)
        self.after(1500, self._watch_setlist)
        self.protocol("WM_DELETE_WINDOW", self._quit)

    # ------------------------------------------------------------- widgets

    def _build_widgets(self):
        self.title_var = tk.StringVar(value="Scene Cards")
        self.notes_var = tk.StringVar(
            value="Waiting for the console...\n\n"
                  "Right-click for settings. Edit setlist.json to add songs.")
        self.footer_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value=self._status_text)

        pad = {"padx": 16}
        self.title_lbl = tk.Label(self, textvariable=self.title_var, bg=BG, fg=FG_TITLE,
                                  anchor="w", justify="left")
        self.title_lbl.pack(fill="x", pady=(14, 4), **pad)

        self.notes_lbl = tk.Label(self, textvariable=self.notes_var, bg=BG, fg=FG_NOTES,
                                  anchor="nw", justify="left")
        self.notes_lbl.pack(fill="both", expand=True, pady=(0, 6), **pad)

        self.footer_lbl = tk.Label(self, textvariable=self.footer_var, bg=BG, fg=FG_DIM,
                                   anchor="w", justify="left")
        self.footer_lbl.pack(fill="x", pady=(0, 2), **pad)

        bar = tk.Frame(self, bg="#0c0c0c")
        bar.pack(fill="x", side="bottom")
        self.dot = tk.Label(bar, text="●", bg="#0c0c0c", fg=BAD)
        self.dot.pack(side="left", padx=(10, 2))
        gear = tk.Label(bar, text="settings", bg="#0c0c0c", fg=FG_DIM, cursor="hand2")
        gear.pack(side="right", padx=8)
        gear.bind("<Button-1>", lambda e: self.open_settings())
        self.pin_lbl = tk.Label(bar, text="on top", bg="#0c0c0c", fg=FG_DIM, cursor="hand2")
        self.pin_lbl.pack(side="right", padx=4)
        self.pin_lbl.bind("<Button-1>", lambda e: self.toggle_topmost())
        self.status_lbl = tk.Label(bar, textvariable=self.status_var, bg="#0c0c0c",
                                   fg=FG_DIM, anchor="w")
        self.status_lbl.pack(side="left", fill="x", expand=True)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Settings...", command=self.open_settings)
        self.menu.add_command(label="Edit set list (setlist.json)",
                              command=lambda: open_in_editor(SETLIST_PATH))
        self.menu.add_command(label="Reload set list", command=self._load_setlist)
        self.menu.add_command(label="Show set list folder", command=lambda: open_in_editor(DATA_DIR))
        self.menu.add_separator()
        self.menu.add_command(label="Previous song  (Left)", command=lambda: self.step(-1))
        self.menu.add_command(label="Next song  (Right)", command=lambda: self.step(1))
        self.menu.add_separator()
        self.menu.add_checkbutton(label="Always on top", variable=self.topmost_var,
                                  command=self._apply_window_attrs)
        self.menu.add_command(label="Bigger text  (+)", command=lambda: self.scale_font(1.1))
        self.menu.add_command(label="Smaller text  (-)", command=lambda: self.scale_font(1 / 1.1))
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self._quit)
        for w in (self, self.title_lbl, self.notes_lbl, self.footer_lbl):
            w.bind("<Button-3>", self._popup)
            w.bind("<Button-2>", self._popup)       # macOS middle / ctrl-click
            w.bind("<Control-Button-1>", self._popup)

        self.bind("<Configure>", self._on_resize)
        self._apply_fonts()
        self._apply_window_attrs()

    def _popup(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _bind_keys(self):
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<plus>", lambda e: self.scale_font(1.1))
        self.bind("<equal>", lambda e: self.scale_font(1.1))
        self.bind("<minus>", lambda e: self.scale_font(1 / 1.1))
        self.bind("<KeyPress-p>", lambda e: self.toggle_topmost())
        self.bind("<KeyPress-r>", lambda e: self._load_setlist())
        self.bind("<KeyPress-s>", lambda e: self.open_settings())
        self.bind("<KeyPress-e>", lambda e: open_in_editor(SETLIST_PATH))

    def _apply_fonts(self):
        s = float(self.cfg.get("font_scale", 1.0))
        family = "Helvetica Neue" if sys.platform == "darwin" else "Segoe UI"
        self.title_lbl.configure(font=(family, max(10, int(24 * s)), "bold"))
        self.notes_lbl.configure(font=(family, max(8, int(15 * s))))
        self.footer_lbl.configure(font=(family, max(7, int(10 * s))))
        self.status_lbl.configure(font=(family, 9))

    def _apply_window_attrs(self):
        top = bool(self.topmost_var.get())
        self.cfg["topmost"] = top
        try:
            self.attributes("-topmost", top)
            self.attributes("-alpha", float(self.cfg.get("opacity", 0.96)))
        except tk.TclError:
            pass
        if hasattr(self, "pin_lbl"):
            self.pin_lbl.configure(fg=OK if top else FG_DIM)

    def _on_resize(self, event):
        if event.widget is self:
            wrap = max(120, event.width - 36)
            self.title_lbl.configure(wraplength=wrap)
            self.notes_lbl.configure(wraplength=wrap)
            self.footer_lbl.configure(wraplength=wrap)

    # ------------------------------------------------------------ set list

    def _load_setlist(self, first=False):
        write_example(SETLIST_PATH)
        try:
            self.setlist = Setlist.load(SETLIST_PATH)
            self._setlist_mtime = os.path.getmtime(SETLIST_PATH)
            if not first:
                self._flash("Set list reloaded (%d songs)" % len(self.setlist))
            # keep showing the same song if it still exists
            if self.current is not None and self.current.index < len(self.setlist):
                self.show_song(self.setlist.songs[self.current.index])
        except SetlistError as e:
            self._setlist_mtime = os.path.getmtime(SETLIST_PATH) if os.path.exists(SETLIST_PATH) else None
            self.title_var.set("Set list problem")
            self.notes_var.set(str(e))
            self.footer_var.set("Fix setlist.json and save - it reloads automatically.")

    def _watch_setlist(self):
        try:
            m = os.path.getmtime(SETLIST_PATH)
            if self._setlist_mtime is not None and m != self._setlist_mtime:
                self._load_setlist()
        except OSError:
            pass
        self.after(1500, self._watch_setlist)

    # ------------------------------------------------------------ display

    def show_song(self, song):
        self.current = song
        self.title_var.set(song.title)
        self.notes_var.set(song.notes or "(no notes)")
        parts = ["%s" % song.trigger_label().capitalize(),
                 "%d / %d" % (song.index + 1, len(self.setlist))]
        nxt = self.setlist.next_after(song)
        parts.append("Next: %s" % nxt.title if nxt else "Last song")
        self.footer_var.set("   ·   ".join(parts))

    def show_unknown(self, event):
        self.current = None
        if event.kind == "scene":
            self.title_var.set("Scene %d" % event.number)
            self.notes_var.set("No card for this scene.\n\n"
                               "Add  {\"scene\": %d, \"title\": \"...\", \"notes\": \"...\"}  to setlist.json"
                               % event.number)
        else:
            self.title_var.set(event.describe())
            self.notes_var.set("No card for this message.")
        self.footer_var.set("")

    def step(self, delta):
        """Preview the previous / next song locally (keyboard arrows)."""
        if not len(self.setlist):
            return
        if self.current is None:
            idx = 0 if delta > 0 else len(self.setlist) - 1
        else:
            idx = min(len(self.setlist) - 1, max(0, self.current.index + delta))
        self.show_song(self.setlist.songs[idx])

    def scale_font(self, factor):
        s = min(3.0, max(0.5, float(self.cfg.get("font_scale", 1.0)) * factor))
        self.cfg["font_scale"] = round(s, 3)
        self._apply_fonts()
        self._apply_window_attrs()
        save_config(self.cfg)

    def toggle_topmost(self):
        self.topmost_var.set(not self.topmost_var.get())
        self._apply_window_attrs()
        save_config(self.cfg)

    def _set_status(self, ok, text):
        self._connected = ok
        self._status_text = text
        self.dot.configure(fg=OK if ok else BAD)
        self._refresh_status()

    def _refresh_status(self):
        text = self._status_text
        if self._last_midi:
            text += "    |  last: " + self._last_midi
        self.status_var.set(text)

    def _flash(self, text):
        """Show a short notice in the status bar, then go back to the live status."""
        self.dot.configure(fg=WARN)
        self.status_var.set(text)
        self.after(2500, lambda: (self.dot.configure(fg=OK if self._connected else BAD),
                                  self._refresh_status()))

    # ------------------------------------------------------------ MIDI

    def _start_source(self):
        if self._retry_job is not None:
            self.after_cancel(self._retry_job)
            self._retry_job = None
        if self.source is not None:
            self.source.stop()
            self.source = None
        on_bytes = lambda data: self.queue.put(("bytes", data))
        on_status = lambda ok, text: self.queue.put(("status", ok, text))
        if self.cfg.get("source") == "tcp":
            self.source = TcpSource(self.cfg.get("tcp_host", ""), self.cfg.get("tcp_port", 51325),
                                    on_bytes, on_status)
            self.source.start()
            return
        err = midi_library_error()
        if err:
            on_status(False, err)
            return
        port = self.cfg.get("midi_port", "")
        ports = list_midi_ports()
        if not port and ports:
            port = ports[0]
        if not port or port not in ports:
            on_status(False, "MIDI port '%s' not found - waiting for it (right-click > Settings)"
                      % (port or "none"))
            self._retry_job = self.after(3000, self._start_source)
            return
        self.source = PortSource(port, on_bytes, on_status)
        self.source.start()

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "bytes":
                    self.parser.feed(item[1])
                elif item[0] == "status":
                    self._set_status(item[1], item[2])
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _on_event(self, event):
        want_ch = int(self.cfg.get("channel", 0) or 0)
        if want_ch and event.channel != want_ch:
            return
        if event.kind in ("scene", "note"):
            self._last_midi = event.describe()
            self._refresh_status()
        if event.kind == "program":
            return   # the parser sends a "scene" event right after; handled there
        if event.kind == "scene":
            song = self.setlist.find(event)
            if song is None:   # maybe a plain program-change mapping
                song = self.setlist.find(MidiEvent("program", event.channel,
                                                   (event.number - 1) % 128, event.value))
            if song is not None:
                self.show_song(song)
            else:
                self.show_unknown(event)
        elif event.kind == "note":
            song = self.setlist.find(event)
            if song is not None:
                self.show_song(song)
            # unmatched notes are ignored: dLive also sends notes for mutes etc.

    # ------------------------------------------------------------ settings

    def open_settings(self):
        SettingsDialog(self)

    def apply_settings(self, new_cfg):
        self.cfg.update(new_cfg)
        save_config(self.cfg)
        self._apply_fonts()
        self._apply_window_attrs()
        self._start_source()

    def _quit(self):
        self.cfg["geometry"] = self.geometry()
        save_config(self.cfg)
        if self.source is not None:
            self.source.stop()
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Scene Cards - Settings")
        self.resizable(False, False)
        self.transient(app)
        cfg = app.cfg
        f = ttk.Frame(self, padding=14)
        f.grid(sticky="nsew")

        self.source = tk.StringVar(value=cfg.get("source", "midi"))
        ttk.Label(f, text="Where does the MIDI come from?", font=("", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        ttk.Radiobutton(f, text="MIDI port (RTP-MIDI / Network Session, A&H MIDI driver, USB)",
                        variable=self.source, value="midi").grid(row=1, column=0, columnspan=3, sticky="w")
        self.port = tk.StringVar(value=cfg.get("midi_port", ""))
        self.port_box = ttk.Combobox(f, textvariable=self.port, width=38)
        self.port_box.grid(row=2, column=1, sticky="we", padx=(24, 4))
        ttk.Button(f, text="Refresh", command=self.refresh_ports).grid(row=2, column=2)
        self.port_note = ttk.Label(f, text="", foreground="#a00")
        self.port_note.grid(row=3, column=1, columnspan=2, sticky="w", padx=(24, 0))

        ttk.Radiobutton(f, text="dLive over the network (TCP port 51325, no driver needed)",
                        variable=self.source, value="tcp").grid(row=4, column=0, columnspan=3,
                                                                sticky="w", pady=(10, 0))
        row = ttk.Frame(f)
        row.grid(row=5, column=1, columnspan=2, sticky="w", padx=(24, 0))
        ttk.Label(row, text="MixRack / Surface IP:").pack(side="left")
        self.host = tk.StringVar(value=cfg.get("tcp_host", ""))
        ttk.Entry(row, textvariable=self.host, width=16).pack(side="left", padx=4)
        ttk.Label(row, text="Port:").pack(side="left")
        self.tcp_port = tk.StringVar(value=str(cfg.get("tcp_port", 51325)))
        ttk.Entry(row, textvariable=self.tcp_port, width=7).pack(side="left", padx=4)

        ttk.Separator(f).grid(row=6, column=0, columnspan=3, sticky="we", pady=10)

        opts = ttk.Frame(f)
        opts.grid(row=7, column=0, columnspan=3, sticky="w")
        ttk.Label(opts, text="Only listen on MIDI channel:").pack(side="left")
        self.channel = tk.StringVar(value="Any" if not cfg.get("channel") else str(cfg["channel"]))
        ttk.Combobox(opts, textvariable=self.channel, width=5, state="readonly",
                     values=["Any"] + [str(i) for i in range(1, 17)]).pack(side="left", padx=4)
        ttk.Label(opts, text="   Opacity:").pack(side="left")
        self.opacity = tk.DoubleVar(value=float(cfg.get("opacity", 0.96)))
        ttk.Scale(opts, from_=0.3, to=1.0, variable=self.opacity, length=110).pack(side="left", padx=4)

        self.topmost = tk.BooleanVar(value=bool(cfg.get("topmost", True)))
        ttk.Checkbutton(f, text="Keep the card on top of other windows",
                        variable=self.topmost).grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Label(f, foreground="#666", justify="left", text=(
            "Keys:  Left/Right = step through songs    +/- = text size    "
            "p = pin on top    e = edit set list    r = reload")).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(10, 0))

        btns = ttk.Frame(f)
        btns.grid(row=10, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Edit set list...", command=lambda: open_in_editor(SETLIST_PATH)).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", command=self.save).pack(side="left", padx=4)

        self.refresh_ports()
        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())

    def refresh_ports(self):
        err = midi_library_error()
        ports = list_midi_ports()
        self.port_box["values"] = ports
        if err:
            self.port_note.configure(text=err)
        elif not ports:
            self.port_note.configure(text="No MIDI input ports found. Start your RTP-MIDI "
                                          "session / driver, then Refresh.")
        else:
            self.port_note.configure(text="")
            if not self.port.get() or self.port.get() not in ports:
                self.port.set(ports[0])

    def save(self):
        ch = self.channel.get()
        try:
            tcp_port = int(self.tcp_port.get() or 51325)
        except ValueError:
            messagebox.showerror("Scene Cards", "Port must be a number (dLive uses 51325).")
            return
        self.app.apply_settings({
            "source": self.source.get(),
            "midi_port": self.port.get().strip(),
            "tcp_host": self.host.get().strip(),
            "tcp_port": tcp_port,
            "channel": 0 if ch == "Any" else int(ch),
            "opacity": round(float(self.opacity.get()), 2),
            "topmost": bool(self.topmost.get()),
        })
        self.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scene-cards",
        description="Always-on-top set list card that follows dLive scene recalls over MIDI.")
    parser.add_argument("--data", metavar="FOLDER", default=DATA_DIR,
                        help="folder for setlist.json and config.json (default: %(default)s)")
    parser.add_argument("--version", action="version", version="scene-cards " + __version__)
    args = parser.parse_args(argv)
    set_data_dir(args.data)
    print("Scene Cards %s - set list: %s" % (__version__, SETLIST_PATH))
    app = SceneCardsApp()
    app.mainloop()


if __name__ == "__main__":
    main()
