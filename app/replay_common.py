# replay_common.py
#
# Record/Replay-Infrastruktur, damit Overlays ohne laufendes iRacing getestet
# werden können. Es gibt zwei Ebenen:
#
#   * Payload-Ebene  – der fertige Broadcast-JSON wird 1:1 mitgeschnitten und
#                       beim Replay wieder gesendet. Testet die Overlays.
#   * irsdk-Ebene     – jeder lesende ir[...]-Zugriff der Builder wird mit-
#                       geschnitten. Beim Replay füttert ReplayIRSDK die echten
#                       Builder → testet die komplette Pipeline.
#
# Beide Ebenen landen in EINER JSONL-Datei (eine Zeile pro Tick):
#   {"t": <rel_sek>, "seq": <int>, "ir": {<geänderte ir-keys>}, "payload": {...}}

import json
import time

# Reservierter Key, unter dem das Ergebnis von get_session_info() abgelegt wird.
RESERVED_SESSION_INFO = "__session_info__"


def sanitize(value):
    """Macht beliebige irsdk-Rückgaben JSON-serialisierbar."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    # numpy-Skalar?
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    # numpy-Array / iterierbar?
    try:
        return [sanitize(v) for v in value]
    except Exception:
        return str(value)


class RecordingIRSDK:
    """Transparenter Wrapper um ein echtes IRSDK.

    Delegiert alles, schneidet aber jeden ir[...]-Zugriff (und das Ergebnis von
    get_session_info()) des aktuellen Ticks mit. Der Server ruft begin_tick()
    direkt nach freeze_var_buffer_latest() und drain_tick() nach dem Bauen der
    Payload auf.
    """

    def __init__(self, real):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_tick", {})

    # -- Aufnahme-Steuerung -------------------------------------------------
    def begin_tick(self):
        object.__setattr__(self, "_tick", {})

    def drain_tick(self):
        t = object.__getattribute__(self, "_tick")
        object.__setattr__(self, "_tick", {})
        return t

    # -- abgefangene Zugriffe ----------------------------------------------
    def __getitem__(self, key):
        val = object.__getattribute__(self, "_real")[key]
        object.__getattribute__(self, "_tick")[key] = sanitize(val)
        return val

    def get_session_info(self, *args, **kwargs):
        real = object.__getattribute__(self, "_real")
        m = getattr(real, "get_session_info", None)
        if not callable(m):
            raise AttributeError("get_session_info")
        val = m(*args, **kwargs)
        object.__getattribute__(self, "_tick")[RESERVED_SESSION_INFO] = sanitize(val)
        return val

    # -- alles andere ans echte Objekt durchreichen ------------------------
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)


class ReplayIRSDK:
    """Fake-IRSDK, das aus aufgezeichneten Tick-Dicts gespeist wird.

    Hält einen persistenten Merge-State: Keys, die nur auf langsamen Ticks
    aufgezeichnet wurden (z.B. DriverInfo), bleiben über schnelle Ticks hinweg
    erhalten.
    """

    def __init__(self):
        self._state = {}
        self.is_initialized = True
        self.is_connected = True

    def feed(self, tick_dict):
        if tick_dict:
            self._state.update(tick_dict)

    # API-Kompatibilität zu irsdk.IRSDK ------------------------------------
    def startup(self, *args, **kwargs):
        return True

    def shutdown(self):
        pass

    def freeze_var_buffer_latest(self, *args, **kwargs):
        return None

    def get_session_info(self, *args, **kwargs):
        return self._state.get(RESERVED_SESSION_INFO)

    def __getitem__(self, key):
        try:
            return self._state[key]
        except KeyError:
            raise KeyError(key)


class Recorder:
    """Schreibt pro Tick eine JSONL-Zeile.

    Unveränderte ir-Keys (z.B. der große DriverInfo-Block) werden NICHT erneut
    geschrieben – ReplayIRSDK rekonstruiert sie über seinen Merge-State. Das
    hält die Datei klein, obwohl mit ~60 Hz aufgezeichnet wird.
    """

    def __init__(self, path):
        self._fh = open(path, "w", encoding="utf-8")
        self._t0 = None
        self._last_ir = {}
        self._flush_counter = 0
        self.path = path

    def write(self, seq, ir_changes, payload):
        now = time.monotonic()
        if self._t0 is None:
            self._t0 = now
        rel = round(now - self._t0, 4)

        # ir-Keys gegen letzten Wert deduplizieren
        diff = {}
        for key, val in (ir_changes or {}).items():
            if self._last_ir.get(key) != val:
                diff[key] = val
                self._last_ir[key] = val

        line = json.dumps(
            {"t": rel, "seq": seq, "ir": diff, "payload": payload},
            default=str,
        )
        self._fh.write(line)
        self._fh.write("\n")

        self._flush_counter += 1
        if self._flush_counter >= 30:
            self._fh.flush()
            self._flush_counter = 0

    def close(self):
        try:
            self._fh.flush()
            self._fh.close()
        except Exception:
            pass


def load_recording(path):
    """Liest eine JSONL-Aufnahme in eine Liste von Records."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records
