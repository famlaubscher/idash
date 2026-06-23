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
#                    ir-Werten; die ECHTEN Builder laufen erneut. Testet die
#                    komplette Pipeline (Relative/Standings/Strategy/…).
#
# Beispiele:
#   python replay_server.py dumps/recording_20260623_120000.jsonl
#   python replay_server.py rec.jsonl --mode irsdk --speed 2.0 --no-loop

import argparse
import asyncio
import os
import sys

from websockets.server import serve

import telemetry_server as ts
from replay_common import ReplayIRSDK, load_recording


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


async def _replay_timing(records, speed, on_tick, loop):
    """Iteriert Records im aufgezeichneten Tempo und ruft on_tick(record) auf."""
    if not records:
        print("WARNUNG: Aufnahme ist leer.")
        return

    while True:
        prev_t = None
        for rec in records:
            t = rec.get("t")
            if prev_t is not None and isinstance(t, (int, float)):
                delay = (t - prev_t) / max(speed, 0.01)
                # große Lücken (Disconnects/Pausen) deckeln
                await asyncio.sleep(min(max(delay, 0.0), 1.0))
            prev_t = t if isinstance(t, (int, float)) else prev_t
            await on_tick(rec)
        if not loop:
            break
        print("--> Aufnahme zu Ende, Loop neu gestartet.")


class PayloadReplayServer(ts.TelemetryServer):
    """Sendet aufgezeichnete Payloads direkt erneut."""

    def __init__(self, records, speed=1.0, loop=True, **kw):
        super().__init__(ir=ReplayIRSDK(), **kw)
        self._records = records
        self._speed = speed
        self._loop = loop

    async def telemetry_loop(self):
        print(f"Payload-Replay gestartet ({len(self._records)} Ticks, "
              f"speed={self._speed}x, loop={self._loop}).")

        async def on_tick(rec):
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                return
            self._seq += 1
            payload["seq"] = self._seq
            payload["connected"] = True
            await self.broadcast(payload)

        await _replay_timing(self._records, self._speed, on_tick, self._loop)
        print("Payload-Replay beendet.")


class IrsdkReplayServer(ts.TelemetryServer):
    """Füttert ein Fake-IRSDK und lässt die echten Builder neu rechnen."""

    def __init__(self, records, speed=1.0, loop=True, **kw):
        self._replay_ir = ReplayIRSDK()
        super().__init__(ir=self._replay_ir, **kw)
        self._records = records
        self._speed = speed
        self._loop = loop

    async def telemetry_loop(self):
        print(f"irsdk-Replay gestartet ({len(self._records)} Ticks, "
              f"speed={self._speed}x, loop={self._loop}). Builder laufen live.")

        async def on_tick(rec):
            self._replay_ir.feed(rec.get("ir") or {})
            payload = self._build_robust_payload()
            self._seq += 1
            payload["connected"] = True
            payload["seq"] = self._seq
            await self.broadcast(payload)

        await _replay_timing(self._records, self._speed, on_tick, self._loop)
        print("irsdk-Replay beendet.")


def run_replay(path=None, mode="payload", speed=1.0, loop=True,
               start_http=True):
    """Startet HTTP- + WebSocket-Server und spielt eine Aufnahme ab.

    Blockiert (eigener asyncio-Loop) – kann daher direkt im Hauptthread oder
    in einem Hintergrund-Thread (z.B. aus overlay_manager) aufgerufen werden.
    """
    path = path or _newest_recording()
    if not path:
        print("FEHLER: Keine Aufnahme angegeben und keine in app/dumps/ gefunden.")
        return
    if not os.path.isfile(path):
        print(f"FEHLER: Datei nicht gefunden: {path}")
        return

    records = load_recording(path)
    print(f"Aufnahme geladen: {path} ({len(records)} Ticks)")

    # Gleicher HTTP-Server wie im Live-Betrieb → Overlays unter denselben URLs.
    # (Wird der Replay vom overlay_manager gestartet, übernimmt dieser den
    #  HTTP-Server nicht separat – wir starten ihn hier.)
    if start_http:
        ts._start_http_server(ts.HTTP_PORT)

    cls = PayloadReplayServer if mode == "payload" else IrsdkReplayServer
    server = cls(records, speed=speed, loop=loop)

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
                        help="Abspielgeschwindigkeit (1.0 = Echtzeit)")
    parser.add_argument("--no-loop", dest="loop", action="store_false",
                        help="Aufnahme nur einmal abspielen statt endlos")
    args = parser.parse_args()
    run_replay(args.file, mode=args.mode, speed=args.speed, loop=args.loop)


if __name__ == "__main__":
    main()
