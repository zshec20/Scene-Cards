import socket
import threading
import time
import unittest

from scene_cards.midi_events import MidiEvent, MidiParser, TcpSource
from scene_cards.setlist import Setlist, SetlistError, Song


def collect(data_chunks):
    events = []
    p = MidiParser(events.append)
    for chunk in data_chunks:
        p.feed(chunk)
    return events


class ParserTests(unittest.TestCase):
    def test_dlive_scene_recall_bank_0(self):
        # Bank Select 0 then Program Change 4 on channel 1 -> scene 5
        ev = collect([bytes([0xB0, 0x00, 0x00, 0xC0, 0x04])])
        self.assertIn(MidiEvent("scene", 1, 5, 0), ev)
        self.assertIn(MidiEvent("program", 1, 4, 0), ev)

    def test_dlive_scene_recall_bank_2(self):
        # bank 2, PC 10 -> scene 2*128 + 10 + 1 = 267, channel 3
        ev = collect([bytes([0xB2, 0x00, 0x02, 0xC2, 0x0A])])
        self.assertIn(MidiEvent("scene", 3, 267, 2), ev)

    def test_bank_remembered_across_program_changes(self):
        ev = collect([bytes([0xB0, 0x00, 0x01]), bytes([0xC0, 0x00]), bytes([0xC0, 0x7F])])
        scenes = [e.number for e in ev if e.kind == "scene"]
        self.assertEqual(scenes, [129, 256])

    def test_program_change_without_bank_is_scene_1_to_128(self):
        ev = collect([bytes([0xC0, 0x00])])
        self.assertEqual([e for e in ev if e.kind == "scene"], [MidiEvent("scene", 1, 1, 0)])

    def test_split_across_packets_and_running_status(self):
        # same as scene recall but bytes arrive one at a time, then a second PC with running status
        ev = collect([bytes([b]) for b in [0xB0, 0x00, 0x00, 0xC0, 0x02, 0x03]])
        scenes = [e.number for e in ev if e.kind == "scene"]
        self.assertEqual(scenes, [3, 4])

    def test_realtime_and_sysex_ignored(self):
        data = bytes([0xF8, 0xB0, 0xFE, 0x00, 0x00, 0xF0, 0x00, 0x00, 0x1A, 0x50, 0xF7, 0xC0, 0xF8, 0x07])
        ev = collect([data])
        self.assertIn(MidiEvent("scene", 1, 8, 0), ev)

    def test_note_on_and_off(self):
        ev = collect([bytes([0x90, 60, 100, 0x90, 60, 0, 0x80, 61, 64])])
        self.assertEqual(ev, [MidiEvent("note", 1, 60, 100)])

    def test_other_cc_does_not_change_bank(self):
        ev = collect([bytes([0xB0, 0x00, 0x01, 0xB0, 0x20, 0x05, 0xC0, 0x00])])
        self.assertIn(MidiEvent("scene", 1, 129, 1), ev)

    def test_bank_is_per_channel(self):
        ev = collect([bytes([0xB0, 0x00, 0x03, 0xC1, 0x00])])
        self.assertIn(MidiEvent("scene", 2, 1, 0), ev)


class SetlistTests(unittest.TestCase):
    def test_match_scene_note_program_and_channel(self):
        songs = [Song(0, {"scene": 12, "title": "A"}),
                 Song(1, {"note": 60, "title": "B", "channel": 2}),
                 Song(2, {"program": 7, "title": "C"})]
        sl = Setlist(songs)
        self.assertEqual(sl.find(MidiEvent("scene", 1, 12, 0)).title, "A")
        self.assertIsNone(sl.find(MidiEvent("scene", 1, 13, 0)))
        self.assertEqual(sl.find(MidiEvent("note", 2, 60, 100)).title, "B")
        self.assertIsNone(sl.find(MidiEvent("note", 1, 60, 100)))   # wrong channel
        self.assertEqual(sl.find(MidiEvent("program", 5, 7, 1)).title, "C")
        self.assertEqual(sl.next_after(songs[0]).title, "B")
        self.assertIsNone(sl.next_after(songs[2]))
        self.assertEqual(sl.prev_before(songs[1]).title, "A")

    def test_cc_triggers(self):
        any_value = Song(0, {"cc": 20, "title": "Any"})
        exact = Song(1, {"cc": 21, "value": 5, "title": "Exact"})
        self.assertTrue(any_value.matches(MidiEvent("cc", 1, 20, 127)))
        self.assertFalse(any_value.matches(MidiEvent("cc", 1, 20, 0)))      # button release
        self.assertFalse(any_value.matches(MidiEvent("cc", 1, 22, 127)))
        self.assertTrue(exact.matches(MidiEvent("cc", 1, 21, 5)))
        self.assertFalse(exact.matches(MidiEvent("cc", 1, 21, 6)))
        self.assertFalse(exact.matches(MidiEvent("note", 1, 21, 5)))
        # one CC number, a different value per song: jumping around picks the right one
        sl = Setlist([Song(i, {"cc": 30, "value": i + 1, "title": "Song %d" % (i + 1)})
                      for i in range(5)])
        self.assertEqual(sl.find(MidiEvent("cc", 1, 30, 4)).title, "Song 4")
        self.assertEqual(sl.find(MidiEvent("cc", 1, 30, 2)).title, "Song 2")
        self.assertEqual(exact.trigger_label(), "CC 21 = 5")
        self.assertEqual(exact.to_dict(), {"cc": 21, "title": "Exact", "notes": "", "value": 5})

    def test_song_needs_a_trigger(self):
        with self.assertRaises(SetlistError):
            Song(0, {"title": "No trigger"})
        with self.assertRaises(SetlistError):
            Song(0, {"title": "Bad", "scene": "twelve"})

    def test_load_and_errors(self):
        import json, os, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w") as f:
                json.dump({"songs": [{"scene": 1, "title": "X", "notes": "hi"}]}, f)
            sl = Setlist.load(p)
            self.assertEqual(len(sl), 1)
            self.assertEqual(sl.songs[0].notes, "hi")
            with open(p, "w") as f:
                f.write("{ not json")
            with self.assertRaises(SetlistError) as cm:
                Setlist.load(p)
            self.assertIn("typo", str(cm.exception))
            with self.assertRaises(SetlistError):
                Setlist.load(os.path.join(d, "missing.json"))


class SaveTests(unittest.TestCase):
    def test_save_round_trip_keeps_every_field(self):
        import json, os, tempfile
        raw = {"title": "Red Rocks", "songs": [
            {"scene": 4, "title": "Last Night", "notes": "OG key \u00b7 6:00\nDrums: up"},
            {"note": 60, "channel": 2, "title": "Encore", "notes": ""},
            {"program": 7, "title": "Walk in"},
        ]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "setlist.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(raw, f)
            Setlist.load(p).save(p)
            again = Setlist.load(p)
            self.assertEqual(again.title, "Red Rocks")
            self.assertEqual([s.to_dict() for s in again.songs], [
                {"scene": 4, "title": "Last Night", "notes": "OG key \u00b7 6:00\nDrums: up"},
                {"note": 60, "title": "Encore", "notes": "", "channel": 2},
                {"program": 7, "title": "Walk in", "notes": ""},
            ])
            with open(p, encoding="utf-8") as f:
                self.assertIn("\u00b7", f.read())          # stays readable, not \\u00b7
            self.assertEqual(os.listdir(d), ["setlist.json"])  # temp file cleaned up


class TcpSourceTests(unittest.TestCase):
    def test_receives_bytes_and_reconnects(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(2)
        port = srv.getsockname()[1]
        got, statuses = [], []
        done = threading.Event()

        def serve():
            for payload in (bytes([0xB0, 0x00, 0x00, 0xC0, 0x01]), bytes([0xC0, 0x02])):
                conn, _ = srv.accept()
                conn.sendall(payload)
                conn.close()           # force the client to reconnect
            done.set()

        threading.Thread(target=serve, daemon=True).start()
        src = TcpSource("127.0.0.1", port, got.append, lambda ok, t: statuses.append((ok, t)))
        src.start()
        self.assertTrue(done.wait(10))
        deadline = time.time() + 5
        while time.time() < deadline and b"".join(got) != bytes([0xB0, 0, 0, 0xC0, 1, 0xC0, 2]):
            time.sleep(0.05)
        src.stop()
        srv.close()
        self.assertEqual(b"".join(got), bytes([0xB0, 0, 0, 0xC0, 1, 0xC0, 2]))
        self.assertTrue(any(ok for ok, _ in statuses))
        self.assertTrue(any("reconnect" in t for ok, t in statuses))


if __name__ == "__main__":
    unittest.main()
