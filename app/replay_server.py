# replay_server.py
#
# Spielt eine mit telemetry_server.py (--record) erzeugte JSONL-Aufnahme ab –
# OHNE laufendes iRacing. Die Overlays verbinden sich exakt wie im Live-Betrieb
# (HTTP auf :8080, WebSocket auf :8765), merken also keinen Unterschied.
#
# Zwei Modi:
#   --mode payload   (Default) Sendet die aufgezeichneten Broadcast-Payloads
#                    1:1 erneut. Schnell & robust, testet die Overlays.
#   --mode irsdk     Füttert ein Fake-IRSDK mit den rohen aufgezeichneten
#                    ir-Werten; die ECHTEN Builder laufen erneut.
#
# STEUERUNG (wie ein Videoplayer): Der Replay-Server ist index-getrieben und
# nimmt Steuerbefehle über denselben WebSocket entgegen (JSON
# {"replay_cmd": {...}}): play/pause/toggle, forward/reverse, speed (1–16x),
# start/end, seek (index|frac), step. Pro Frame wird ein "replay"-Statusblock
# mitgesendet; beim Verbinden bekommt der Client einmalig "replay_meta"
# (Timeline: Gesamtzeit, Runden-Marken, Pit-Segmente für Inlap/Outlap).
# Die Steuer-UI ist overlays/replay_control.html.
#
# Beispiele:
#   python replay_server.py dumps/recording_20260623_120000.jsonl
#   python replay_server.py rec.jsonl --mode irsdk --speed 2.0 --no-loop

import argparse
import asyncio
import json
import os

from websockets.server import serve

import telemetry_server as ts
from replay_common import ReplayIRSDK, load_recording

# Erlaubte Abspielgeschwindigkeiten (das Control-UI bietet genau diese an).
SPEEDS = [1, 2, 4, 8, 16]
# Ziel-Broadcast-Takt: bei hohen Speeds werden Frames übersprungen, statt
# tausende Mini-Broadcasts/s zu erzeugen.
_TARGET_DT = 1.0 / 33.0


def _newest_recording():
    """Neueste *.jsonl in app/dumps/ (nach Änderungszeit) oder None."""
    rec_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps")
    try:
        files = [os.path.join(rec_dir, f) for f in os.listdir(rec_dir)
                 if f.endswith(".jsonl")]
    except OSError:
        return None
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _build_timeline(records):
    """Leitet pro-Frame-Metadaten aus der Aufnahme ab (für Anzeige + Marken).

    Liefert (rel_t, laps, on_pit, meta):
      rel_t   – Liste relativer Zeiten (s) ab Aufnahmebeginn
      laps    – Liste der (synthetischen) Rundennummer je Frame
      on_pit  – Liste bool je Frame (Spieler auf Pit-Road)
      meta    – dict für die UI (Gesamtzeit, Rundenmarken, Pit-Segmente)
    """
    n = len(records)
    raw_t = []
    last = 0.0
    for rec in records:
        t = rec.get("t")
        last = float(t) if isinstance(t, (int, float)) else last
        raw_t.append(last)
    t0 = raw_t[0] if raw_t else 0.0
    rel_t = [t - t0 for t in raw_t]
    t_total = rel_t[-1] if rel_t else 0.0

    laps = []
    on_pit = []
    lap = 1
    prev_pct = None
    lap_marks = []          # {frac, lap}
    pit_segments = []       # (i0, i1)
    seg_start = None
    for i, rec in enumerate(records):
        ir = rec.get("ir") or {}
        pct = ir.get("LapDistPct")
        if isinstance(pct, (int, float)):
            if prev_pct is not None and prev_pct > 0.7 and pct < 0.3:
                lap += 1
                if t_total > 0:
                    lap_marks.append({"frac": rel_t[i] / t_total, "lap": lap})
            prev_pct = pct
        laps.append(lap)

        op = bool(ir.get("OnPitRoad"))
        on_pit.append(op)
        if op and seg_start is None:
            seg_start = i
        elif (not op) and seg_start is not None:
            pit_segments.append((seg_start, i - 1))
            seg_start = None
    if seg_start is not None:
        pit_segments.append((seg_start, n - 1))

    pit_marks = []
    for a, b in pit_segments:
        if t_total > 0:
            pit_marks.append({"frac0": rel_t[a] / t_total,
                              "frac1": rel_t[b] / t_total})
    meta = {
        "total":     n,
        "t_total":   t_total,
        "lap_total": lap,
        "lap_marks": lap_marks,
        "pit_marks": pit_marks,
    }
    return rel_t, laps, on_pit, meta


class ControllableReplayServer(ts.TelemetryServer):
    """Index-getriebener Replay mit Videoplayer-Steuerung über WebSocket."""

    def __init__(self, records, mode="payload", speed=1.0, loop=True, **kw):
        self._replay_ir = ReplayIRSDK()
        super().__init__(ir=self._replay_ir, **kw)
        self._records = records
        self._mode = mode
        self._rel_t, self._laps, self._on_pit, self._meta = _build_timeline(records)
        self._total = len(records)

        # Steuerzustand
        self._idx = 0
        self._playing = True
        self._speed = float(speed) if speed else 1.0
        self._dir = 1                 # +1 vorwärts, -1 rückwärts
        self._loop = loop
        self._seek = None             # ausstehendes Sprungziel (Index)

    # ------------------------------------------------------------------
    # Status / Emit
    # ------------------------------------------------------------------

    def _status(self) -> dict:
        i = self._idx
        return {
            "idx":       i,
            "total":     self._total,
            "t_cur":     self._rel_t[i] if i < len(self._rel_t) else 0.0,
            "t_total":   self._meta["t_total"],
            "frac":      (self._rel_t[i] / self._meta["t_total"])
                         if self._meta["t_total"] > 0 else 0.0,
            "lap":       self._laps[i] if i < len(self._laps) else 0,
            "lap_total": self._meta["lap_total"],
            "on_pit":    self._on_pit[i] if i < len(self._on_pit) else False,
            "playing":   self._playing,
            "speed":     self._speed,
            "dir":       self._dir,
        }

    def _make_payload(self, idx):
        if self._mode == "irsdk":
            self._replay_ir.feed(self._records[idx].get("ir") or {})
            payload = self._build_robust_payload()
        else:
            base = self._records[idx].get("payload")
            payload = dict(base) if isinstance(base, dict) else {}
        self._seq += 1
        payload["seq"] = self._seq
        payload["connected"] = True
        payload["replay"] = self._status()
        return payload

    # ------------------------------------------------------------------
    # Steuerbefehle (eingehend über WebSocket)
    # ------------------------------------------------------------------

    def _handle_command(self, raw):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        cmd = data.get("replay_cmd") if isinstance(data, dict) else None
        if not isinstance(cmd, dict):
            return
        a = cmd.get("action")
        if a == "play":
            self._playing = True
        elif a == "pause":
            self._playing = False
        elif a == "toggle":
            self._playing = not self._playing
        elif a == "forward":
            self._dir = 1
            self._playing = True
        elif a == "reverse":
            self._dir = -1
            self._playing = True
        elif a == "speed":
            try:
                self._speed = max(0.1, float(cmd.get("value", 1)))
            except (TypeError, ValueError):
                pass
        elif a == "dir":
            self._dir = -1 if cmd.get("value") in (-1, "-1", "reverse", "back") else 1
        elif a == "start":
            self._seek = 0
            self._playing = False
        elif a == "end":
            self._seek = self._total - 1
            self._playing = False
        elif a == "seek":
            try:
                if "index" in cmd:
                    self._seek = int(cmd["index"])
                elif "frac" in cmd:
                    self._seek = int(round(float(cmd["frac"]) * (self._total - 1)))
            except (TypeError, ValueError):
                pass
        elif a == "step":
            self._playing = False
            try:
                self._seek = self._idx + int(cmd.get("delta", 1))
            except (TypeError, ValueError):
                pass

    async def client_handler(self, websocket):
        """Wie im Live-Server, liest aber zusätzlich Steuerbefehle und schickt
        dem neuen Client einmalig die Timeline-Metadaten."""
        self.clients.add(websocket)
        print("Client verbunden. Aktive Clients:", len(self.clients))
        try:
            await websocket.send(json.dumps({"replay_meta": self._meta}))
        except Exception:
            pass
        try:
            async for message in websocket:
                self._handle_command(message)
        except Exception as e:
            msg = str(e)
            if "code=1000" not in msg:
                print(f"WebSocket-Fehler (Client {getattr(websocket,'remote_address','?')}):", e)
        finally:
            self.clients.discard(websocket)
            print("Client getrennt. Aktive Clients:", len(self.clients))

    # ------------------------------------------------------------------
    # Steuerbarer Abspiel-Loop
    # ------------------------------------------------------------------

    async def telemetry_loop(self):
        if not self._records:
            print("WARNUNG: Aufnahme ist leer.")
            return
        print(f"Replay gestartet ({self._total} Ticks, mode={self._mode}, "
              f"speed={self._speed}x, loop={self._loop}). Steuerung via "
              f"replay_control.html.")

        while True:
            # Ausstehenden Sprung anwenden
            if self._seek is not None:
                self._idx = max(0, min(self._seek, self._total - 1))
                self._seek = None

            payload = self._make_payload(self._idx)
            await self.broadcast(payload)

            if not self._playing:
                await asyncio.sleep(0.1)        # pausiert: ruhig halten
                continue

            # Frames im Zeit-Budget vorrücken (überspringt bei hohem Speed)
            acc = 0.0
            idx = self._idx
            boundary = False
            while acc / self._speed < _TARGET_DT:
                nxt = idx + self._dir
                if nxt < 0 or nxt >= self._total:
                    boundary = True
                    break
                acc += abs(self._rel_t[nxt] - self._rel_t[idx])
                idx = nxt

            if boundary:
                if self._dir > 0 and self._loop:
                    self._idx = 0               # vorwärts am Ende -> Loop
                else:
                    self._idx = 0 if self._dir < 0 else self._total - 1
                    self._playing = False       # an Start/Ende anhalten
                await asyncio.sleep(_TARGET_DT)
            else:
                self._idx = idx
                delay = acc / self._speed
                await asyncio.sleep(min(delay if delay > 0 else _TARGET_DT, 1.0))


def run_replay(path=None, mode="payload", speed=1.0, loop=True, start_http=True):
    """Startet HTTP- + WebSocket-Server und spielt eine Aufnahme steuerbar ab."""
    path = path or _newest_recording()
    if not path:
        print("FEHLER: Keine Aufnahme angegeben und keine in app/dumps/ gefunden.")
        return
    if not os.path.isfile(path):
        print(f"FEHLER: Datei nicht gefunden: {path}")
        return

    records = load_recording(path)
    print(f"Aufnahme geladen: {path} ({len(records)} Ticks)")

    if start_http:
        ts._start_http_server(ts.HTTP_PORT)

    server = ControllableReplayServer(records, mode=mode, speed=speed, loop=loop)

    async def run():
        print(f"Starte WebSocket-Server auf ws://127.0.0.1:{server.port}")
        async with serve(server.client_handler, "127.0.0.1", server.port,
                         ping_interval=None):
            await server.telemetry_loop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nReplay manuell gestoppt.")


def run_from_env():
    """Einstieg für overlay_manager: Parameter kommen aus ENV-Variablen."""
    mode = os.environ.get("IDASH_REPLAY_MODE", "payload")
    if mode not in ("payload", "irsdk"):
        mode = "payload"
    try:
        speed = float(os.environ.get("IDASH_REPLAY_SPEED", "1.0") or 1.0)
    except ValueError:
        speed = 1.0
    loop = (os.environ.get("IDASH_REPLAY_LOOP", "1").strip().lower()
            not in ("0", "false", "no", "off"))
    path = os.environ.get("IDASH_REPLAY_FILE") or None
    run_replay(path, mode=mode, speed=speed, loop=loop)


def main():
    parser = argparse.ArgumentParser(description="iDash Telemetrie-Replay")
    parser.add_argument("file", nargs="?", default=None,
                        help="Pfad zur .jsonl-Aufnahme (ohne Angabe: neueste in app/dumps/)")
    parser.add_argument("--mode", choices=["payload", "irsdk"], default="payload")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Start-Abspielgeschwindigkeit (1.0 = Echtzeit)")
    parser.add_argument("--no-loop", dest="loop", action="store_false",
                        help="Am Ende anhalten statt zum Anfang zu springen")
    args = parser.parse_args()
    run_replay(args.file, mode=args.mode, speed=args.speed, loop=args.loop)


if __name__ == "__main__":
    main()
