"""
MIDI input for Scene Cards.

Two ways to receive MIDI:

  PortSource  - any MIDI input port the operating system knows about:
                an RTP-MIDI / "Network Session" port, the Allen & Heath
                MIDI driver, a USB MIDI interface, etc. (needs mido + python-rtmidi)

  TcpSource   - a direct TCP connection to a dLive MixRack / Surface on
                port 51325. This is the same raw MIDI-over-TCP that dLive
                Director uses. No driver needed, pure Python.

Both feed raw bytes into MidiParser, which turns them into simple events.

dLive scene recall is transmitted as:
    Bn 00 bank   (Bank Select, CC 0)   bank = 0..3
    Cn program   (Program Change)      program = 0..127
    scene number = bank * 128 + program + 1       (scenes 1..500)
"""

from dataclasses import dataclass
import socket
import threading


# Controllers the dLive (and most consoles) use for housekeeping: Bank Select
# and the NRPN messages it sends for fader and parameter moves. They change
# constantly while you mix, so they make poor song triggers.
BUSY_CCS = frozenset([0, 6, 32, 38, 96, 97, 98, 99, 100, 101])


@dataclass(frozen=True)
class MidiEvent:
    kind: str       # "scene", "program", "note", "cc"
    channel: int    # 1..16
    number: int     # scene number / program / note / controller
    value: int      # bank for scene+program, velocity for note, value for cc

    def describe(self):
        if self.kind == "scene":
            return "Scene %d  (ch %d, bank %d, PC %d)" % (
                self.number, self.channel, self.value, (self.number - 1) % 128)
        if self.kind == "note":
            return "Note %d vel %d  (ch %d)" % (self.number, self.value, self.channel)
        if self.kind == "cc":
            return "CC %d = %d  (ch %d)" % (self.number, self.value, self.channel)
        return "%s %d/%d (ch %d)" % (self.kind, self.number, self.value, self.channel)


class MidiParser:
    """Turns a raw MIDI byte stream into MidiEvents.

    Copes with running status, real-time bytes mixed into messages,
    and skips SysEx. Call feed() with any number of bytes at a time.
    """

    def __init__(self, on_event):
        self.on_event = on_event
        self._status = None
        self._data = []
        self._skip = 0
        self._in_sysex = False
        self.bank = {}   # channel -> last Bank Select (CC0) value

    def feed(self, data):
        for b in data:
            if b >= 0xF8:            # real-time (clock, active sensing...) - ignore
                continue
            if b == 0xF0:            # SysEx start
                self._in_sysex = True
                self._status = None
                self._data = []
                continue
            if b == 0xF7:            # SysEx end
                self._in_sysex = False
                continue
            if self._in_sysex:
                continue
            if b & 0x80:
                if b >= 0xF0:        # system common - cancels running status
                    self._status = None
                    self._data = []
                    self._skip = {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(b, 0)
                    continue
                self._status = b
                self._data = []
                self._skip = 0
                continue
            # data byte
            if self._skip:
                self._skip -= 1
                continue
            if self._status is None:
                continue
            self._data.append(b)
            need = 1 if (self._status & 0xF0) in (0xC0, 0xD0) else 2
            if len(self._data) == need:
                self._dispatch(self._status, self._data)
                self._data = []  # keep status for running status

    def _dispatch(self, status, data):
        kind = status & 0xF0
        ch = (status & 0x0F) + 1
        if kind == 0xB0:                      # control change
            control, value = data
            if control == 0:
                self.bank[ch] = value
            self.on_event(MidiEvent("cc", ch, control, value))
        elif kind == 0xC0:                    # program change
            program = data[0]
            bank = self.bank.get(ch, 0)
            self.on_event(MidiEvent("program", ch, program, bank))
            self.on_event(MidiEvent("scene", ch, bank * 128 + program + 1, bank))
        elif kind == 0x90 and data[1] > 0:    # note on (velocity 0 = note off)
            self.on_event(MidiEvent("note", ch, data[0], data[1]))


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def list_midi_ports():
    """Names of MIDI input ports, or [] if the MIDI library is missing."""
    try:
        import mido
        return list(dict.fromkeys(mido.get_input_names()))
    except Exception:
        return []


def midi_library_error():
    """None if mido + rtmidi import fine, else a short message."""
    try:
        import importlib
        import mido
        importlib.import_module("rtmidi")
        mido.get_input_names()
        return None
    except Exception as e:
        return "MIDI library not available (%s). Run: pip install mido python-rtmidi" % e


class PortSource:
    """Listens to a named MIDI input port via mido."""

    def __init__(self, port_name, on_bytes, on_status):
        self.port_name = port_name
        self.on_bytes = on_bytes
        self.on_status = on_status
        self._port = None

    def start(self):
        try:
            import mido
        except Exception as e:
            self.on_status(False, "MIDI library missing: %s" % e)
            return
        try:
            self._port = mido.open_input(
                self.port_name, callback=lambda m: self.on_bytes(bytes(m.bytes())))
            self.on_status(True, "MIDI: %s" % self.port_name)
        except Exception as e:
            self.on_status(False, "Can't open '%s': %s" % (self.port_name, e))

    def stop(self):
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None


class TcpSource:
    """Connects to a dLive (MixRack or Surface) MIDI-over-TCP port and
    reconnects automatically if the link drops."""

    DEFAULT_PORT = 51325

    def __init__(self, host, port, on_bytes, on_status):
        self.host = host
        self.port = int(port or self.DEFAULT_PORT)
        self.on_bytes = on_bytes
        self.on_status = on_status
        self._stop = threading.Event()
        self._thread = None
        self._sock = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self):
        delay = 1
        while not self._stop.is_set():
            self.on_status(False, "Connecting to %s:%d ..." % (self.host, self.port))
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5)
            except Exception as e:
                self.on_status(False, "dLive %s unreachable (%s), retrying" % (self.host, e))
                if self._stop.wait(delay):
                    break
                delay = min(delay * 2, 10)
                continue
            delay = 1
            self._sock = sock
            sock.settimeout(1.0)
            self.on_status(True, "dLive TCP: %s:%d" % (self.host, self.port))
            try:
                while not self._stop.is_set():
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    self.on_bytes(data)
            except Exception:
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
                self._sock = None
            if not self._stop.is_set():
                self.on_status(False, "dLive connection lost, reconnecting")
                self._stop.wait(1)
