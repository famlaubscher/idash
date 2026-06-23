# telemetry_server.py

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from http.server import SimpleHTTPRequestHandler, HTTPServer
from threading import Thread

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

import irsdk
from websockets.server import serve

from strategy_helper import StrategyHelper
from hud_builder import HudBuilder
from relative_builder import RelativeBuilder
from session_builder import SessionInfoBuilder
from standings_builder import StandingsBuilder
from env_builder import WindBuilder, EnvBuilder
UPDATE_INTERVAL = 0.016  # ~60 Hz

# Pfad zur Layout-Datei (gleiche Logik wie overlay_manager.py)
if hasattr(sys, "_MEIPASS"):
    _CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".idash_overlay")
    LAYOUT_PATH = os.path.join(_CONFIG_DIR, "overlay_layout.json")
else:
    LAYOUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overlay_layout.json")


def _read_opacity() -> float:
    """Liest die gespeicherte Opacity aus overlay_layout.json."""
    try:
        with open(LAYOUT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        v = float(data.get("opacity", 0.9))
        return max(0.0, min(v, 1.0))
    except Exception:
        return 0.9
LOG_INTERVAL = 5.0      # Ausgabe im Terminal alle 5 Sekunden
HTTP_PORT = 8080        # Port für OBS Browser Sources

# Overlays-Verzeichnis (funktioniert im Dev-Modus und im PyInstaller-Build)
if hasattr(sys, "_MEIPASS"):
    OVERLAYS_DIR = os.path.join(sys._MEIPASS, "overlays")  # type: ignore[attr-defined]
    # setups: NICHT aus dem (temporären) Bundle, sondern neben der EXE,
    # damit der Nutzer dort eigene HTM-Setups ablegen kann.
    SETUPS_DIR = os.path.join(os.path.dirname(sys.executable), "setups")
else:
    OVERLAYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlays")
    SETUPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "setups")
OVERLAYS_DIR = os.path.abspath(OVERLAYS_DIR)
SETUPS_DIR = os.path.abspath(SETUPS_DIR)


def _start_http_server(port: int = HTTP_PORT) -> None:
    """Startet einen einfachen HTTP-Server, der den Overlays-Ordner ausliefert.
    Läuft in einem Daemon-Thread, damit er beim Programmende automatisch stoppt."""

    overlays_dir = OVERLAYS_DIR

    setups_dir = SETUPS_DIR
    os.makedirs(setups_dir, exist_ok=True)

    # Logging unterdrücken + UTF-8 für HTML/JS/CSS erzwingen
    class _QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=overlays_dir, **kwargs)

        def log_message(self, fmt, *args):  # noqa: ANN001
            pass

        def do_GET(self):
            import json as _json
            from urllib.parse import urlparse, unquote

            # Query-String entfernen, sonst landet "?foo" im Dateinamen
            path = urlparse(self.path).path

            if path == "/api/setups":
                try:
                    files = sorted(
                        [f for f in os.listdir(setups_dir) if f.endswith((".htm", ".html"))],
                        key=lambda f: os.path.getmtime(os.path.join(setups_dir, f)),
                        reverse=True
                    )
                except Exception:
                    files = []
                body = _json.dumps(files).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path.startswith("/setups/"):
                # Nur den reinen Dateinamen zulassen — kein Pfad, kein Traversal
                fname = unquote(path[len("/setups/"):])
                fname = os.path.basename(fname)
                fpath = os.path.join(setups_dir, fname)

                # Sicherstellen, dass das Ergebnis wirklich in setups_dir liegt
                real_base = os.path.realpath(setups_dir)
                real_path = os.path.realpath(fpath)
                in_base = real_path == real_base or real_path.startswith(real_base + os.sep)

                if (
                    in_base
                    and fname.lower().endswith((".htm", ".html"))
                    and os.path.isfile(real_path)
                ):
                    with open(real_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)
                return

            super().do_GET()

        def guess_type(self, path):  # noqa: ANN001
            ctype = super().guess_type(path)
            if isinstance(ctype, str):
                if ctype.startswith("text/html"):
                    return "text/html; charset=utf-8"
                if ctype.startswith("text/javascript") or ctype.startswith("application/javascript"):
                    return "text/javascript; charset=utf-8"
                if ctype.startswith("text/css"):
                    return "text/css; charset=utf-8"
            return ctype

    server = HTTPServer(("127.0.0.1", port), _QuietHandler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"HTTP-Server gestartet: http://localhost:{port}/")
    print(f"  OBS Browser Sources:")
    for name in ("hud", "relative", "strategy", "standings", "wind"):
        print(f"    http://localhost:{port}/{name}.html")


class TelemetryServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port

        # IRSDK-Client
        self.ir = irsdk.IRSDK()
        self.clients: set = set()

        # Interne Zustände
        self._last_ir_connected: bool = False
        self._last_log_time: float = 0.0
        self._seq: int = 0
        self._last_session_num: int | None = None  # Session-Wechsel-Erkennung
        self._slow_tick: int = 0
        self._slow_cache: dict = {}  # gecachte Daten der schweren Builder
        self._cached_opacity: float = _read_opacity()

        # Tyre-Cache über mehrere Ticks hinweg
        # dict[car_idx] -> Tyre-String (z.B. "Dry"/"Wet")
        self._tyre_cache: dict[int, str] = {}

        # Initialisierung aller Builder
        self.strategy = StrategyHelper(self.ir)
        self.builders = {
            "hud": HudBuilder(self.ir),
            "relative": RelativeBuilder(self.ir),
            "session_info": SessionInfoBuilder(self.ir),
            "standings": StandingsBuilder(self.ir),
            "wind": WindBuilder(self.ir),
            "env": EnvBuilder(self.ir),
        }

    # ------------------------------------------------------------------
    # WebSocket-Handling
    # ------------------------------------------------------------------

    async def client_handler(self, websocket):
        """Verwaltet die Verbindung eines einzelnen Clients."""
        self.clients.add(websocket)
        print("Client verbunden. Aktive Clients:", len(self.clients))

        try:
            await websocket.wait_closed()
        except Exception as e:
            msg = str(e)
            # 1000 = normal closure, das spammen wir nicht ins Log
            if "code=1000" not in msg and "wait_closed" not in msg:
                print(
                    f"WebSocket-Fehler (Client {getattr(websocket, 'remote_address', '?')}):",
                    e,
                )
        finally:
            self.clients.discard(websocket)
            print("Client getrennt. Aktive Clients:", len(self.clients))

    async def broadcast(self, payload: dict) -> None:
        """Sendet die Payload an alle verbundenen Clients."""
        if not self.clients:
            return

        msg = json.dumps(payload)
        dead = []

        await asyncio.gather(
            *[self._send_to_client(ws, msg, dead) for ws in list(self.clients)]
        )

        for ws in dead:
            self.clients.discard(ws)
            print("Entferne toten Client.")

    async def _send_to_client(self, ws, msg: str, dead_list: list) -> None:
        """Hilfsfunktion zum Senden an einen einzelnen Client mit Fehlerbehandlung."""
        try:
            await ws.send(msg)
        except Exception:
            dead_list.append(ws)

    # ------------------------------------------------------------------
    # iRSDK und Hauptlogik
    # ------------------------------------------------------------------

    def ensure_irsdk(self) -> None:
        """Stellt sicher, dass die iRSDK initialisiert ist."""
        if not self.ir.is_initialized:
            self.ir.startup()

    def _reset_builders(self) -> None:
        """Setzt Builder-Caches bei kompletter Trennung zurück."""
        print("Cache der Builder wird zurückgesetzt.")
        try:
            standings_builder = self.builders.get("standings")
            if hasattr(standings_builder, "reset"):
                standings_builder.reset()

            if hasattr(self.strategy, "reset"):
                self.strategy.reset()
        except Exception as e:
            print("WARNUNG: Fehler beim Zurücksetzen der Builder:", e)

        # Caches leeren
        self._tyre_cache.clear()
        self._slow_cache.clear()
        self._slow_tick = 0

    # -------------------- Tyre-Handling ---------------------------------

    @staticmethod
    def _looks_like_valid_tyre(s: str) -> bool:
        """Heuristik: ist das schon ein 'echter' Tyre-String, den das Overlay versteht?"""
        s_l = s.strip().lower()
        if not s_l:
            return False
        return ("dry" in s_l) or ("wet" in s_l) or s_l in ("d", "w")

    def _merge_tyre_from_relative_into_standings(self, payload: dict) -> None:
        """Tyre-Infos aus `relative` stabil in `standings` übernehmen.

        Regeln:
        - Nur dann überschreiben, wenn wir einen Tyre-Wert haben.
        - Nie bestehende *gültige* Tyre-Werte löschen, wenn `relative` nichts liefert.
        - Tyre-Werte werden in einem Cache gehalten, damit sie bei Lücken
          in den Daten nicht flackern / verschwinden.
        """
        rel_list = payload.get("relative")
        if not isinstance(rel_list, list):
            rel_list = []

        standings_list = payload.get("standings")
        if not isinstance(standings_list, list):
            return

        # 1) Aus `relative`: Map car_idx -> Tyre-String für diesen Tick
        tyre_by_car_idx: dict[int, str] = {}

        for r in rel_list:
            if not isinstance(r, dict):
                continue

            car_idx = r.get("car_idx")
            if car_idx is None:
                # Fallback: manche Builder nennen es carIdx
                try:
                    car_idx = int(r.get("carIdx"))
                except Exception:
                    car_idx = None
            if car_idx is None:
                continue

            tyre_val = (
                r.get("tyre")
                or r.get("tire")
                or r.get("tyre_compound")
                or r.get("tyreCompound")
            )
            if not tyre_val:
                continue

            tyre_str = str(tyre_val).strip()
            if not tyre_str:
                continue

            tyre_by_car_idx[car_idx] = tyre_str

        # 2) Cache mit neuen Werten aktualisieren
        if tyre_by_car_idx:
            self._tyre_cache.update(tyre_by_car_idx)

        # 3) In die Standings eintragen:
        #    - wenn bereits ein „gültiger“ Tyre (Dry/Wet) drin ist, lassen wir ihn
        #    - sonst nehmen wir den Wert aus dem Cache, falls vorhanden
        for row in standings_list:
            if not isinstance(row, dict):
                continue

            car_idx = row.get("car_idx")
            if car_idx is None:
                continue

            current_val = row.get("tyre")
            current_str = str(current_val).strip() if current_val is not None else ""

            if current_str and self._looks_like_valid_tyre(current_str):
                continue

            cached = self._tyre_cache.get(car_idx)
            if cached:
                row["tyre"] = cached

    # -------------------- Payload-Bau ------------------------------------

    def _build_robust_payload(self) -> dict:
        """Ruft alle Builder auf und behandelt Fehler robust.

        Fast builders (HUD, wind, env) laufen jeden Tick.
        Slow builders (relative, standings, session_info, strategy) laufen
        nur jeden 5. Tick (~6 Hz), um den asyncio-Loop nicht zu blockieren.
        """
        SLOW_EVERY = 5

        payload: dict = {}

        # ── Fast builders: HUD-Inputs immer aktuell ──────────────────────
        fast_keys = {"hud", "wind", "env"}
        for key, builder in self.builders.items():
            if key not in fast_keys:
                continue
            try:
                data = builder.build()
                if isinstance(data, dict):
                    payload.update(data)
            except Exception:
                logger.exception("Fehler in Builder '%s'", key)

        # ── Slow builders: alle 5 Ticks (~12 Hz), gecacht ───────────────
        self._slow_tick += 1
        if self._slow_tick % 5 == 0:
            slow_payload: dict = {}
            for key, builder in self.builders.items():
                if key in fast_keys:
                    continue
                try:
                    slow_payload[key] = builder.build()
                except Exception:
                    logger.exception("Fehler in Builder '%s'", key)
                    slow_payload[key] = [] if key in ("relative", "standings") else {}
            try:
                slow_payload["strategy"] = self.strategy.build_strategy_payload(
                    session_info=slow_payload.get("session_info", {})
                )
            except Exception:
                logger.exception("Fehler in StrategyHelper")
                slow_payload["strategy"] = {}
            self._slow_cache = slow_payload

        payload.update(self._slow_cache)

        # Opacity aus Layout-Datei in Payload (alle 5 Ticks aktualisiert)
        if self._slow_tick % 5 == 0:
            self._cached_opacity = _read_opacity()
        payload["card_bg_alpha"] = self._cached_opacity

        # --- Car-Count Korrektur ---
        standings_list = payload.get("standings")
        if not isinstance(standings_list, list):
            standings_list = []
            payload["standings"] = standings_list

        sess_info = payload.get("session_info")
        if not isinstance(sess_info, dict):
            sess_info = {}
            payload["session_info"] = sess_info

        current_car_count = sess_info.get("car_count")
        if not isinstance(current_car_count, int) or current_car_count < len(standings_list):
            sess_info["car_count"] = len(standings_list)

        # --- Tyre-Merge: Relative -> Standings ---
        self._merge_tyre_from_relative_into_standings(payload)

        return payload

    # ------------------------------------------------------------------
    # Hauptloop
    # ------------------------------------------------------------------

    async def telemetry_loop(self) -> None:
        print("Telemetry-Loop gestartet...")

        while True:
            self.ensure_irsdk()
            self._seq += 1

            if self.ir.is_initialized and self.ir.is_connected:
                if not self._last_ir_connected:
                    print("--> Mit iRacing verbunden.")
                    self._last_ir_connected = True

                # Snapshot des aktuellen Var-Buffers ziehen
                self.ir.freeze_var_buffer_latest()

                # Session-Wechsel erkennen (z.B. Practice → Race, Rennen → Rennen)
                try:
                    cur_session = int(self.ir["SessionNum"])
                    if self._last_session_num is not None and cur_session != self._last_session_num:
                        print(f"--> Session gewechselt ({self._last_session_num} → {cur_session}), Builder werden zurückgesetzt.")
                        self._reset_builders()
                    self._last_session_num = cur_session
                except Exception:
                    pass

                # Fast builders (HUD, wind, env) — immer aktuell
                payload = self._build_robust_payload()
                payload["connected"] = True
                payload["seq"] = self._seq

                # asyncio Kontrolle abgeben damit WebSocket-Handler laufen können
                await asyncio.sleep(0)

            else:
                if self._last_ir_connected:
                    print("--> Von iRacing getrennt.")
                    self._reset_builders()

                self._last_ir_connected = False
                self._last_session_num = None

                # Payload für Disconnected-Zustand
                payload = {
                    "connected": False,
                    "relative": [],
                    "standings": [],
                    "session_info": {},
                    "wind_rel": None,
                    "wind_speed": None,
                    "air_temp": None,
                    "track_temp": None,
                    "track_wetness": None,
                    "strategy": {},
                    "seq": self._seq,
                }

            self._log_status(payload)
            await self.broadcast(payload)
            await asyncio.sleep(UPDATE_INTERVAL)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_status(self, payload: dict) -> None:
        """Gibt den Status des Servers im Terminal aus (gedrosselt)."""
        now = time.time()
        if now - self._last_log_time <= LOG_INTERVAL:
            return

        self._last_log_time = now

        rel_len = len(payload.get("relative", []))
        std_len = len(payload.get("standings", []))
        sess = payload.get("session_info", {})

        print(
            f"Status: connected={payload['connected']} | clients={len(self.clients)} "
            f"| rel_len={rel_len} | std_len={std_len} | seq={self._seq}"
        )

        if isinstance(sess, dict) and sess:
            print(
                f"  Session: {sess.get('type')} | time_left={sess.get('time_left')} "
                f"| track={sess.get('track_name')} | SoF={sess.get('sof')} "
                f"| cars={sess.get('car_count')}"
            )

        if std_len > 0:
            try:
                first_standing = payload["standings"][0]
                print(f"  Beispiel-Standing: {str(first_standing)[:80]}...")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Startet den WebSocket-Server und den Telemetrie-Loop."""
        print(f"Starte WebSocket-Server auf ws://127.0.0.1:{self.port} (IPv4+IPv6)")

        async with serve(
            self.client_handler,
            "127.0.0.1",
            self.port,
            ping_interval=None,  # kein automatischer Ping — localhost braucht das nicht
        ):
            await self.telemetry_loop()


def main() -> None:
    try:
        _start_http_server(HTTP_PORT)
        server = TelemetryServer()
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\nServer manuell gestoppt.")
    except Exception as e:
        print("\nFATALER FEHLER IM TELEMETRY SERVER:")
        print(e)
        traceback.print_exc()


if __name__ == "__main__":
    main()
