"""
The set list editor window: edit songs, scene numbers and card notes
without touching setlist.json by hand.
"""

import shutil
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from scene_cards.midi_events import BUSY_CCS
from scene_cards.setlist import Setlist, SetlistError, Song

KINDS = [("scene", "Scene"), ("cc", "MIDI CC"), ("note", "MIDI note"), ("program", "Program change")]
LIMITS = {"scene": (1, 500), "cc": (0, 127), "note": (0, 127), "program": (0, 127)}
KIND_LABEL = dict(KINDS)
LABEL_KIND = {label: kind for kind, label in KINDS}
MOD = "Command" if sys.platform == "darwin" else "Control"
MOD_NAME = "Cmd" if sys.platform == "darwin" else "Ctrl"


def song_to_row(song):
    kind, number = next(iter(song.triggers.items()))
    return {"kind": kind, "number": number, "value": song.value, "title": song.title,
            "notes": song.notes, "channel": song.channel}


def row_to_dict(row):
    d = {row["kind"]: row["number"], "title": row["title"], "notes": row["notes"]}
    if row["kind"] == "cc" and row.get("value") is not None:
        d["value"] = row["value"]
    if row.get("channel") is not None:
        d["channel"] = row["channel"]
    return d


def row_label(row):
    prefix = {"scene": "", "cc": "CC ", "note": "Note ", "program": "PC "}[row["kind"]]
    number = str(row["number"]) if row["number"] is not None else "?"
    if row["kind"] == "cc" and row.get("value") is not None:
        number += "=%d" % row["value"]
    return " %s%-4s %s" % (prefix, number, row["title"] or "(no title)")


def find_duplicates(rows):
    """[(label, [titles...])] for triggers used by more than one song."""
    seen = {}
    for row in rows:
        value = row.get("value") if row["kind"] == "cc" else None
        key = (row["kind"], row["number"], value, row.get("channel"))
        seen.setdefault(key, []).append(row["title"] or "(no title)")
    out = []
    for (kind, number, value, _), titles in seen.items():
        if len(titles) > 1:
            label = "%s %s" % (KIND_LABEL[kind], number)
            if value is not None:
                label += " value %d" % value
            out.append((label, titles))
    return out


class SetlistEditor(tk.Toplevel):
    def __init__(self, app, path, select_index=None):
        super().__init__(app)
        self.app = app
        self.path = path
        self.cur = None          # index of the row shown in the fields
        self.dirty = False
        self._loading = False

        self.title("Scene Cards - Edit Set List")
        self.geometry("820x560")
        self.minsize(640, 420)
        self.protocol("WM_DELETE_WINDOW", self.close)

        if not self._load_rows():
            self.after(10, self.destroy)
            return
        self._build()
        self._refresh_list()
        if self.rows:
            idx = select_index if select_index is not None and select_index < len(self.rows) else 0
            self.select(idx)
        else:
            self._set_fields_state("disabled")
        self.on_midi(app.last_event)
        self.lift()
        self.focus_force()

    # ------------------------------------------------------------ data

    def _load_rows(self):
        try:
            setlist = Setlist.load(self.path)
        except SetlistError as e:
            start_fresh = messagebox.askyesno(
                "Scene Cards",
                "The set list file has a problem:\n\n%s\n\n"
                "Start a new, empty set list? Your current file is kept as "
                "setlist.broken.json." % e, parent=self.app)
            if not start_fresh:
                return False
            try:
                shutil.copyfile(self.path, self.path.replace(".json", ".broken.json"))
            except OSError:
                pass
            setlist = Setlist([], "")
            self.dirty = True
        self.set_name = tk.StringVar(value=setlist.title)
        self.rows = [song_to_row(s) for s in setlist.songs]
        return True

    # ------------------------------------------------------------ layout

    def _build(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        top = ttk.Frame(outer)
        top.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 10))
        ttk.Label(top, text="Set name:").pack(side="left")
        name = ttk.Entry(top, textvariable=self.set_name, width=40)
        name.pack(side="left", padx=6)
        name.bind("<KeyRelease>", lambda e: self._mark_dirty())

        # ---- left: song list
        left = ttk.Frame(outer)
        left.grid(row=1, column=0, sticky="ns", padx=(0, 12))
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="Songs, in set order").grid(row=0, column=0, columnspan=2, sticky="w")
        self.listbox = tk.Listbox(left, width=34, activestyle="none", exportselection=False,
                                  font=("Menlo" if sys.platform == "darwin" else "Consolas", 12))
        self.listbox.grid(row=1, column=0, sticky="ns")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        btns = ttk.Frame(left)
        btns.grid(row=2, column=0, columnspan=2, sticky="we", pady=(6, 0))
        ttk.Button(btns, text="+ Add song", command=self.add).pack(side="left")
        ttk.Button(btns, text="Delete", command=self.delete).pack(side="left", padx=4)
        ttk.Button(btns, text="Up", width=4, command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(btns, text="Down", width=5, command=lambda: self.move(1)).pack(side="left", padx=(4, 0))

        # ---- right: fields for the selected song
        right = ttk.Frame(outer)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(6, weight=1)

        ttk.Label(right, text="Song title").grid(row=0, column=0, sticky="w")
        self.title_var = tk.StringVar()
        self.title_entry = ttk.Entry(right, textvariable=self.title_var, font=("", 14))
        self.title_entry.grid(row=1, column=0, sticky="we", pady=(0, 10))
        self.title_entry.bind("<KeyRelease>", lambda e: self._commit())

        ttk.Label(right, text="Shows when the console sends").grid(row=2, column=0, sticky="w")
        trig = ttk.Frame(right)
        trig.grid(row=3, column=0, sticky="we", pady=(0, 10))
        self.kind_var = tk.StringVar(value="Scene")
        self.kind_box = ttk.Combobox(trig, textvariable=self.kind_var, state="readonly", width=14,
                                     values=[label for _, label in KINDS])
        self.kind_box.pack(side="left")
        self.kind_box.bind("<<ComboboxSelected>>", lambda e: self._commit())
        self.number_var = tk.StringVar()
        self.number_box = ttk.Spinbox(trig, from_=0, to=500, width=6, textvariable=self.number_var,
                                      command=self._commit)
        self.number_box.pack(side="left", padx=6)
        self.number_box.bind("<KeyRelease>", lambda e: self._commit())
        self.value_lbl = ttk.Label(trig, text="value")
        self.value_var = tk.StringVar()
        self.value_box = ttk.Spinbox(trig, from_=0, to=127, width=5, textvariable=self.value_var,
                                     command=self._commit)
        self.value_box.bind("<KeyRelease>", lambda e: self._commit())
        self.learn_btn = ttk.Button(trig, text="Use last from console", command=self.learn)
        self.learn_btn.pack(side="left", padx=(6, 6))
        self.last_lbl = ttk.Label(trig, text="", foreground="#777")
        self.last_lbl.pack(side="left")
        self.trig_hint = ttk.Label(right, text="", foreground="#777")

        self.trig_hint.grid(row=4, column=0, sticky="w", pady=(0, 6))
        ttk.Label(right, text="Notes on the card  (one line per item, e.g.  Drums: up slightly)").grid(
            row=5, column=0, sticky="w")
        txt_frame = ttk.Frame(right)
        txt_frame.grid(row=6, column=0, sticky="nsew")
        txt_frame.columnconfigure(0, weight=1)
        txt_frame.rowconfigure(0, weight=1)
        self.notes = tk.Text(txt_frame, wrap="word", undo=True, height=12,
                             font=("Helvetica Neue" if sys.platform == "darwin" else "Segoe UI", 14),
                             padx=8, pady=6)
        self.notes.grid(row=0, column=0, sticky="nsew")
        nsb = ttk.Scrollbar(txt_frame, orient="vertical", command=self.notes.yview)
        nsb.grid(row=0, column=1, sticky="ns")
        self.notes.configure(yscrollcommand=nsb.set)
        self.notes.bind("<KeyRelease>", lambda e: self._commit())
        self.notes.bind("<FocusOut>", lambda e: self._commit())

        # ---- bottom bar
        bottom = ttk.Frame(outer)
        bottom.grid(row=2, column=0, columnspan=2, sticky="we", pady=(10, 0))
        self.msg = ttk.Label(bottom, text="", foreground="#777")
        self.msg.pack(side="left")
        ttk.Button(bottom, text="Save  (%s+S)" % MOD_NAME, command=self.save).pack(side="right")
        ttk.Button(bottom, text="Close", command=self.close).pack(side="right", padx=6)

        self.bind("<%s-s>" % MOD, lambda e: (self.save(), "break")[1])
        self.bind("<%s-n>" % MOD, lambda e: (self.add(), "break")[1])
        self.bind("<Escape>", lambda e: self.close())

    def _update_trigger_widgets(self):
        """Show the CC value box only for CC triggers, and a hint for each kind."""
        kind = LABEL_KIND.get(self.kind_var.get(), "scene")
        if kind == "cc":
            if not self.value_box.winfo_ismapped():
                self.value_lbl.pack(side="left", after=self.number_box)
                self.value_box.pack(side="left", padx=(4, 0), after=self.value_lbl)
            hint = ("CC number, plus a value if songs share one CC (blank value = any value "
                    "above 0).")
            try:
                if int(self.number_var.get()) in BUSY_CCS:
                    hint = ("CC %s is used by the dLive for faders / bank select and will fire "
                            "while you mix. Pick another number." % self.number_var.get())
            except ValueError:
                pass
        else:
            self.value_lbl.pack_forget()
            self.value_box.pack_forget()
            lo, hi = LIMITS[kind]
            hint = {"scene": "dLive scene number (%d-%d)." % (lo, hi),
                    "note": "MIDI note number (0-127). Any velocity above 0.",
                    "program": "Program Change number (0-127), ignoring bank."}[kind]
        self.trig_hint.configure(text=hint)

    def _set_fields_state(self, state):
        for w in (self.title_entry, self.number_box, self.value_box, self.learn_btn):
            w.configure(state=state)
        self.kind_box.configure(state="readonly" if state == "normal" else "disabled")
        self.notes.configure(state=state)

    # ------------------------------------------------------------ list handling

    def _refresh_list(self):
        self.listbox.delete(0, "end")
        for row in self.rows:
            self.listbox.insert("end", row_label(row))
        if self.cur is not None and self.cur < len(self.rows):
            self.listbox.selection_set(self.cur)
            self.listbox.see(self.cur)

    def _on_list_select(self, event=None):
        sel = self.listbox.curselection()
        if sel and sel[0] != self.cur:
            self._commit()
            self.select(sel[0])

    def select(self, idx):
        self.cur = idx
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self.listbox.see(idx)
        row = self.rows[idx]
        self._loading = True
        self._set_fields_state("normal")
        self.title_var.set(row["title"])
        self.title_entry.selection_clear()
        self.kind_var.set(KIND_LABEL[row["kind"]])
        self.number_var.set("" if row["number"] is None else str(row["number"]))
        self.value_var.set("" if row.get("value") is None else str(row["value"]))
        self._update_trigger_widgets()
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", row["notes"])
        self.notes.edit_reset()
        self._loading = False

    def _commit(self):
        """Copy the fields into the selected row."""
        if self._loading or self.cur is None or self.cur >= len(self.rows):
            return
        row = self.rows[self.cur]
        try:
            number = int(self.number_var.get().strip())
        except ValueError:
            number = None
        kind = LABEL_KIND.get(self.kind_var.get(), "scene")
        try:
            value = int(self.value_var.get().strip()) if kind == "cc" else None
        except ValueError:
            value = None
        self._update_trigger_widgets()
        new = {"kind": kind,
               "number": number,
               "value": value,
               "title": self.title_var.get().strip(),
               "notes": self.notes.get("1.0", "end-1c").rstrip(),
               "channel": row.get("channel")}
        if new != row:
            self.rows[self.cur] = new
            label = row_label(new)
            if self.listbox.get(self.cur) != label:
                self.listbox.delete(self.cur)
                self.listbox.insert(self.cur, label)
                self.listbox.selection_set(self.cur)
            self._mark_dirty()

    def _mark_dirty(self):
        self.dirty = True
        self.msg.configure(text="Unsaved changes", foreground="#b36b00")

    def add(self, scene=None):
        """Add a song after the selected one. With `scene`, add a card for that
        scene number and place it among the other scenes in number order."""
        self._commit()
        scenes = [r["number"] for r in self.rows if r["kind"] == "scene" and r["number"] is not None]
        if scene is None:
            at = (self.cur + 1) if self.cur is not None else len(self.rows)
            scene = (max(scenes) + 1) if scenes else 1
        else:
            at = 0
            for i, r in enumerate(self.rows):
                if r["kind"] == "scene" and r["number"] is not None and r["number"] < scene:
                    at = i + 1
        self.rows.insert(at, {"kind": "scene", "number": scene,
                              "value": None, "title": "New song", "notes": "", "channel": None})
        self.cur = at
        self._refresh_list()
        self.select(at)
        self._mark_dirty()
        self.title_entry.focus_set()
        self.title_entry.select_range(0, "end")

    def delete(self):
        if self.cur is None:
            return
        title = self.rows[self.cur]["title"] or "this song"
        if not messagebox.askyesno("Scene Cards", "Delete \"%s\"?" % title, parent=self):
            return
        del self.rows[self.cur]
        self._mark_dirty()
        if not self.rows:
            self.cur = None
            self._refresh_list()
            self._loading = True
            self.title_var.set("")
            self.number_var.set("")
            self.notes.delete("1.0", "end")
            self._loading = False
            self._set_fields_state("disabled")
            return
        self.cur = min(self.cur, len(self.rows) - 1)
        self._refresh_list()
        self.select(self.cur)

    def move(self, delta):
        if self.cur is None:
            return
        self._commit()
        new = self.cur + delta
        if not 0 <= new < len(self.rows):
            return
        self.rows[self.cur], self.rows[new] = self.rows[new], self.rows[self.cur]
        self.cur = new
        self._refresh_list()
        self._mark_dirty()

    # ------------------------------------------------------------ console

    def on_midi(self, event):
        """Called by the app whenever a scene or note arrives."""
        if event is None:
            self.last_lbl.configure(text="(nothing received yet)")
        elif event.kind == "scene":
            self.last_lbl.configure(text="last: Scene %d" % event.number)
        elif event.kind == "cc":
            self.last_lbl.configure(text="last: CC %d = %d" % (event.number, event.value))
        else:
            self.last_lbl.configure(text="last: Note %d" % event.number)

    def learn(self):
        event = self.app.last_event
        if event is None:
            messagebox.showinfo("Scene Cards",
                                "Nothing has arrived from the console yet.\n\n"
                                "Recall the scene on the desk, then click this again.", parent=self)
            return
        self.kind_var.set(KIND_LABEL[event.kind])
        self.number_var.set(str(event.number))
        self.value_var.set(str(event.value) if event.kind == "cc" else "")
        self._commit()

    # ------------------------------------------------------------ save / close

    def save(self):
        self._commit()
        for i, row in enumerate(self.rows):
            if row["number"] is None:
                self.select(i)
                messagebox.showerror("Scene Cards", "\"%s\" needs a %s number."
                                     % (row["title"] or "Song %d" % (i + 1),
                                        KIND_LABEL[row["kind"]].lower()), parent=self)
                self.number_box.focus_set()
                return False
            lo, hi = LIMITS[row["kind"]]
            if not lo <= row["number"] <= hi:
                self.select(i)
                messagebox.showerror("Scene Cards", "\"%s\": %s number must be %d-%d."
                                     % (row["title"], KIND_LABEL[row["kind"]].lower(), lo, hi),
                                     parent=self)
                return False
            if row["kind"] == "cc" and row.get("value") is not None and not 0 <= row["value"] <= 127:
                self.select(i)
                messagebox.showerror("Scene Cards", "\"%s\": CC value must be 0-127."
                                     % row["title"], parent=self)
                return False
            if not row["title"]:
                self.select(i)
                messagebox.showerror("Scene Cards", "Song %d needs a title." % (i + 1), parent=self)
                self.title_entry.focus_set()
                return False
        dups = find_duplicates(self.rows)
        if dups:
            lines = "\n".join("%s: %s" % (label, ", ".join(titles)) for label, titles in dups)
            if not messagebox.askyesno("Scene Cards",
                                       "These songs share a trigger, so only the first one "
                                       "will ever show:\n\n%s\n\nSave anyway?" % lines, parent=self):
                return False
        try:
            songs = [Song(i, row_to_dict(r)) for i, r in enumerate(self.rows)]
            Setlist(songs, self.set_name.get().strip()).save(self.path)
        except (SetlistError, OSError) as e:
            messagebox.showerror("Scene Cards", "Couldn't save:\n%s" % e, parent=self)
            return False
        self.dirty = False
        self.msg.configure(text="Saved", foreground="#2e8b57")
        self.app.after_editor_save(self.cur)
        return True

    def close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel("Scene Cards", "Save your changes?", parent=self)
            if answer is None:
                return
            if answer and not self.save():
                return
        self.app.editor = None
        self.destroy()
