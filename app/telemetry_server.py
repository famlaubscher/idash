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
from circle_builder import CircleBuilder
from pit_calibrator import PitCalibrator
UPDATE_INTERVAL = 0.016  # ~60 Hz

# Pfad zur Layout-Datei (gleiche Logik wie overlay_manager.py)
if hasattr(sys, "_MEIPASS"):
    _CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".idash_overlay")
    LAYOUT_PATH = os.path.join(_CONFIG_DIR, "overlay_layout.json")
    CONFIG_PATH = os.path.join(_CONFIG_DIR, "overlay_config.json")
else:
    LAYOUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overlay_layout.json")
    # overlay_config.json liegt im Projekt-Root (eine Ebene über app/)
    CONFIG_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "overlay_config.json"
    )


def _read_opacity() -> float:
    """Liest die gespeicherte Opacity aus overlay_layout.json."""
    try:
        with open(LAYOUT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        v = float(data.get("opacity", 0.9))
        return max(0.0, min(v, 1.0))
    except Exception:
        return 0.9


# Default-Annahmen für den Pit-Out-Indikator (Circle of Doom).
# Werden aus overlay_config.json["pit"] überschrieben (snake_case-Keys).
_PIT_DEFAULTS = {
    "enabled":          True,
    "fuelRateLps":      2.84,   # Betankung: Liter pro Sekunde (108 L in 38 s)
    "tireChangeSec":    18.0,   # Dauer kompletter Reifenwechsel (alle 4)
    "changeTires":      True,   # Reifen wechseln?
    "pitLaneLossSec":   20.0,   # Zeitverlust Boxengasse ggü. Strecke (Durchfahrt)
    "manualRefuelL":    None,   # feste Tankmenge (überschreibt IMMER, auch live)
    "refuelFallbackL":  None,   # Tankmenge, wenn keine Fuel-Telemetrie da ist (z.B. Replay)
    "refLapFallbackSec": 100.0, # Ersatz-Rundenzeit, solange keine echte vorliegt
}

# JSON-Key (snake_case)  ->  (Payload-Key, Caster)
_PIT_FIELDS = {
    "enabled":              ("enabled",          bool),
    "fuel_rate_lps":        ("fuelRateLps",      float),
    "tire_change_sec":      ("tireChangeSec",    float),
    "change_tires":         ("changeTires",      bool),
    "pit_lane_loss_sec":    ("pitLaneLossSec",   float),
    "manual_refuel_l":      ("manualRefuelL",    float),
    "refuel_fallback_l":    ("refuelFallbackL",  float),
    "ref_lap_fallback_sec": ("refLapFallbackSec", float),
}


def _read_pit_cmd() -> dict | None:
    """Liest ein Kalibrier-Kommando aus overlay_layout.json (vom Manager gesetzt).
    Format: {"pit_cal_cmd": {"action": "start"|"stop"|"reset", "nonce": <int>}}"""
    try:
        with open(LAYOUT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        cmd = data.get("pit_cal_cmd") if isinstance(data, dict) else None
        if isinstance(cmd, dict) and "action" in cmd:
            return cmd
    except Exception:
        pass
    return None


def _read_pit_config() -> dict:
    """Liest den 'pit'-Abschnitt aus overlay_config.json und mischt ihn über
    die Defaults. Robust gegen fehlende Datei / fehlerhafte Werte."""
    cfg = dict(_PIT_DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        pit = data.get("pit") if isinstance(data, dict) else None
        if isinstance(pit, dict):
            for json_key, (out_key, caster) in _PIT_FIELDS.items():
                if json_key not in pit:
                    continue
                val = pit[json_key]
                if val is None:
                    cfg[out_key] = None
                    continue
                try:
                    cfg[out_key] = caster(val)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return cfg
LOG_INTERVAL = 5.0      # Ausgabe im Terminal alle 5 Sekunden


def _read_http_port(default: int = 8080) -> int:
    """Ermittelt den HTTP-Port (OBS Browser Sources / Setup-Comparer).

    Priorität: Umgebungsvariable IDASH_HTTP_PORT > overlay_config.json
    ["server"]["http_port"] > Default 8080.
    """
    # 1) Env-Override
    env = os.environ.get("IDASH_HTTP_PORT")
    if env:
        try:
            p = int(env)
            if 1 <= p <= 65535:
                return p
        except (TypeError, ValueError):
            pass
    # 2) overlay_config.json -> server.http_port
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        server = data.get("server") if isinstance(data, dict) else None
        if isinstance(server, dict):
            p = int(server.get("http_port"))
            if 1 <= p <= 65535:
                return p
    except Exception:
        pass
    return default


HTTP_PORT = _read_http_port()   # Port für OBS Browser Sources

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
    for name in ("hud", "relative", "strategy", "standings", "wind", "circle"):
        print(f"    http://localhost:{port}/{name}.html")


class TelemetryServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765,
                 ir=None, record_path: str | None = None):
        self.host = host
        self.port = port

        # IRSDK-Client – echt, injiziert (Replay) oder aufzeichnend gewrappt.
        self.ir = ir if ir is not None else irsdk.IRSDK()

        self._recorder = None
        if record_path:
            from replay_common import RecordingIRSDK, Recorder
            self.ir = RecordingIRSDK(self.ir)
            self._recorder = Recorder(record_path)
            print(f"Aufzeichnung aktiv → {record_path}")

        self.clients: set = set()

        # Interne Zustände
        self._last_ir_connected: bool = False
        self._last_log_time: float = 0.0
        self._seq: int = 0
        self._last_session_num: int | None = None  # Session-Wechsel-Erkennung
        self._slow_tick: int = 0
        self._slow_cache: dict = {}  # gecachte Daten der schweren Builder
        self._cached_opacity: float = _read_opacity()
        self._cached_pit_config: dict = _read_pit_config()

        # Pit-Kalibrierung (geführtes Einlernen in Practice)
        self._calibrator = PitCalibrator()
        self._pit_cmd_nonce = None

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
            "circle": CircleBuilder(self.ir),
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

        # Caches leeren – explizite Leer-Werte damit das Overlay sofort
        # die alten Daten verwirft und nicht bis zum nächsten Slow-Tick wartet.
        self._tyre_cache.clear()
        self._slow_cache = {
            "relative":     [],
            "standings":    [],
            "session_info": {},
            "strategy":     {},
            "circle": {
                "cars": [], "sectors": [], "track_name": None,
                "ref_lap": None, "time": None,
                "pit_entry_pct": None, "pit_exit_pct": None,
            },
        }
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
            self._cached_pit_config = _read_pit_config()
        payload["card_bg_alpha"] = self._cached_opacity

        # Pit-Kalibrierung: Kommandos, Fütterung, gelernte Werte einmischen
        pit_config, pit_cal = self._handle_calibration()
        payload["pit_config"] = pit_config
        payload["pit_cal"] = pit_cal

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
    # Pit-Kalibrierung
    # ------------------------------------------------------------------

    def _player_scalar(self, key):
        try:
            return self.ir[key]
        except Exception:
            return None

    def _handle_calibration(self):
        """Verarbeitet Kalibrier-Kommandos, füttert die State-Machine und mischt
        gelernte Werte über die Config-Defaults (gelernt gewinnt).
        Liefert (pit_config_für_payload, pit_cal_status)."""
        cal = self._calibrator

        # Kommando (start/stop/reset) nur bei neuer Nonce anwenden
        cmd = _read_pit_cmd()
        if cmd is not None:
            nonce = cmd.get("nonce")
            if nonce != self._pit_cmd_nonce:
                self._pit_cmd_nonce = nonce
                action = cmd.get("action")
                if action == "start":
                    cal.start(PitCalibrator.make_key(self.ir))
                elif action == "stop":
                    cal.stop()
                elif action == "reset":
                    cal.reset()

        # Spieler-Kontext füttern (nur sinnvoll wenn verbunden)
        if self.ir.is_connected:
            time = self._player_scalar("SessionTime")
            on_pit = self._player_scalar("OnPitRoad")
            speed = self._player_scalar("Speed")
            fuel = self._player_scalar("FuelLevel")
            pct = self._player_scalar("LapDistPct")
            ref_lap = None
            for name in ("LapLastLapTime", "LapBestLapTime"):
                v = self._player_scalar(name)
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if v > 0.5:
                    ref_lap = v
                    break
            try:
                cal.feed(time=float(time) if time is not None else None,
                         on_pit=on_pit,
                         speed=float(speed) if speed is not None else None,
                         fuel=float(fuel) if fuel is not None else None,
                         pct=float(pct) if pct is not None else None,
                         ref_lap=ref_lap)
            except Exception:
                logger.exception("Fehler in PitCalibrator.feed")

        # pit_config = Defaults+Config, dann gelernte Werte (gewinnen) drüber
        pit_config = dict(self._cached_pit_config)
        try:
            learned = cal.learned_for(PitCalibrator.make_key(self.ir)) if self.ir.is_connected else {}
            for json_key, (out_key, caster) in _PIT_FIELDS.items():
                if json_key in learned and learned[json_key] is not None:
                    try:
                        pit_config[out_key] = caster(learned[json_key])
                    except (TypeError, ValueError):
                        pass
        except Exception:
            logger.exception("Fehler beim Einmischen gelernter Pit-Werte")

        return pit_config, cal.status()

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

                # Recorder: neuen Tick beginnen (vor jedem ir[...]-Zugriff)
                if self._recorder is not None:
                    self.ir.begin_tick()

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
                    "circle": {"cars": [], "sectors": [], "track_name": None, "ref_lap": None,
                               "time": None, "pit_entry_pct": None, "pit_exit_pct": None},
                    "seq": self._seq,
                }

            # Recorder: Tick mitschneiden (nur im verbundenen Zustand mit ir-Daten)
            if self._recorder is not None and payload.get("connected"):
                self._recorder.write(self._seq, self.ir.drain_tick(), payload)

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


def _default_record_path() -> str:
    """dumps/recording_<ts>.jsonl (Ordner wird angelegt)."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    rec_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps")
    os.makedirs(rec_dir, exist_ok=True)
    return os.path.join(rec_dir, f"recording_{ts}.jsonl")


def _resolve_record_path() -> str | None:
    """Aufzeichnungspfad aus CLI (--record [pfad]) oder ENV IDASH_RECORD.

    ENV-Werte: "1"/"true"/"yes" oder ein Verzeichnis → Auto-Timestamp-Name
    (wie das nackte --record-Flag); sonst wörtlich als Dateipfad.
    """
    argv = sys.argv[1:]
    if "--record" in argv:
        i = argv.index("--record")
        # optionaler Pfad direkt nach dem Flag
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            return argv[i + 1]
        return _default_record_path()

    env = os.environ.get("IDASH_RECORD")
    if not env:
        return None
    if env.strip().lower() in ("1", "true", "yes", "on"):
        return _default_record_path()
    # Verzeichnis (vorhanden oder mit Pfadtrenner endend) → Timestamp-Datei darin
    if os.path.isdir(env) or env.endswith(("/", "\\")):
        os.makedirs(env, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        return os.path.join(env, f"recording_{ts}.jsonl")
    return env


def main() -> None:
    record_path = _resolve_record_path()
    server = None
    try:
        _start_http_server(HTTP_PORT)
        server = TelemetryServer(record_path=record_path)
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\nServer manuell gestoppt.")
    except Exception as e:
        print("\nFATALER FEHLER IM TELEMETRY SERVER:")
        print(e)
        traceback.print_exc()
    finally:
        if server is not None and getattr(server, "_recorder", None) is not None:
            server._recorder.close()
            print(f"Aufzeichnung gespeichert: {server._recorder.path}")


if __name__ == "__main__":
    main()
