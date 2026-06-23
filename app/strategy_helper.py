import json
import math
import os
import time
from collections import deque

from telemetry_common import normalize_tyre_label


class StrategyHelper:
    """
    Kapselt die komplette Strategy-Logik fuer das Strategy-Overlay.

    Klares Ablaufmodell pro Frame:
        1. _resolve_context()    – liest alle Basiswerte einmalig aus iRacing
        2. _update_*_state()     – mutiert internen Zustand (Fuel-History, Stint-Counter)
        3. _build_*_section()    – liest nur, baut Teil-Dicts ohne Seiteneffekte
        4. _maybe_persist()      – schreibt Cache bei Bedarf
    """

    def __init__(self, ir):
        self.ir = ir

        # Fuel-Tracking
        self._last_lap: int | None = None
        self._lap_fuel_start: float | None = None
        self._last_lap_fuel: float | None = None
        self._this_lap_fuel: float | None = None
        self._avg_fuel_per_lap: float | None = None
        self._fuel_lap_history: deque = deque(maxlen=5)

        # Referenz-Rundenzeit-Tracking (für Baseline-Persistenz)
        self._best_lap_time_seen: float | None = None
        self._seed_ref_lap_time: float | None = None

        # Maximales Kraftstoffniveau, das in dieser Session gesehen wurde.
        # Nach vollem Pit-Refuel = das series-spezifische Tankmaximum.
        self._max_fuel_seen: float | None = None

        # Stint-Tracking
        self._in_pit_road: bool = False
        self._stint_start_lap: int | None = None

        # Persistenz
        self._cache_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "strategy_cache.json",
        )
        self._cache: dict = {}
        self._cache_loaded: bool = False
        self._seed_loaded: bool = False
        self._last_save_time: float = 0.0

    # ================================================================== #
    #  Persistenz                                                          #
    # ================================================================== #

    def _load_cache(self):
        if self._cache_loaded:
            return
        self._cache_loaded = True
        try:
            if os.path.exists(self._cache_path):
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache = data if isinstance(data, dict) else {}
        except Exception as e:
            print("strategy_cache laden fehlgeschlagen:", e)

    def _save_cache(self):
        try:
            tmp = self._cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, sort_keys=True)
            os.replace(tmp, self._cache_path)
        except Exception as e:
            print("strategy_cache schreiben fehlgeschlagen:", e)

    def _cache_key(self, session_info: dict) -> str:
        track  = session_info.get("track_name")  or "UnknownTrack"
        series = session_info.get("series_name") or "UnknownSeries"
        car    = "UnknownCar"
        try:
            di = self.ir["DriverInfo"]
            my_idx = di.get("DriverCarIdx") if isinstance(di, dict) else getattr(di, "DriverCarIdx", None)
            drivers = di.get("Drivers", []) if isinstance(di, dict) else getattr(di, "Drivers", [])
            if my_idx is not None and isinstance(drivers, list) and 0 <= my_idx < len(drivers):
                d = drivers[my_idx]
                car = (
                    (d.get("CarPath") or d.get("CarScreenNameShort") or d.get("CarScreenName"))
                    if isinstance(d, dict)
                    else (getattr(d, "CarPath", None) or getattr(d, "CarScreenNameShort", None))
                ) or car
        except Exception:
            pass
        return f"{car}|{track}|{series}"

    def _maybe_seed_from_cache(self, session_info: dict):
        if self._seed_loaded or self._avg_fuel_per_lap is not None:
            return
        self._load_cache()
        entry = self._cache.get(self._cache_key(session_info))
        if not isinstance(entry, dict):
            return
        try:
            avg = float(entry["avg_fuel_per_lap"])
            if avg > 0.01:
                self._avg_fuel_per_lap = avg
                self._seed_loaded = True
                # Baseline-Rundenzeit mitseeden, damit die Strategie schon
                # vor der ersten gezeiteten Runde rechnen kann.
                ref = entry.get("ref_lap_time")
                if isinstance(ref, (int, float)) and ref > 0.5:
                    self._seed_ref_lap_time = float(ref)
                print(
                    f"Strategy-Seed geladen: avg_fuel_per_lap={avg:.3f} L/lap"
                    + (f", ref_lap_time={self._seed_ref_lap_time:.3f}s"
                       if self._seed_ref_lap_time else "")
                )
        except Exception:
            pass

    def _maybe_persist(self, session_info: dict):
        if self._avg_fuel_per_lap is None or len(self._fuel_lap_history) < 3:
            return
        now = time.time()
        if now - self._last_save_time < 30.0:
            return
        self._last_save_time = now
        self._load_cache()
        entry = {
            "avg_fuel_per_lap": float(self._avg_fuel_per_lap),
            "updated_at": now,
        }
        # Baseline-Rundenzeit (beste gesehene Runde, sonst Seed beibehalten)
        ref = self._best_lap_time_seen or self._seed_ref_lap_time
        if ref and ref > 0.5:
            entry["ref_lap_time"] = float(ref)
        self._cache[self._cache_key(session_info)] = entry
        self._save_cache()

    # ================================================================== #
    #  Schritt 1 — Kontext: einmalig pro Frame auflösen                   #
    # ================================================================== #

    def _resolve_context(self, ir):
        """
        Liest alle Frame-stabilen Basiswerte einmalig aus iRacing.

        Rückgabe:
            my_idx        – eigener CarIdx (int | None)
            my_class_id   – eigene CarClassID (int | None)
            lap           – aktuelle Runde (int | None)
            fuel_level    – aktueller Tankinhalt in Liter (float | None)
            tank_capacity – Tankkapazität in Liter (float | None)
            ref_lap_time  – Referenz-Rundenzeit in Sekunden (float | None)
        """
        # my_idx + my_class_id
        my_idx = None
        my_class_id = None
        try:
            di = ir["DriverInfo"]
            my_idx = di.get("DriverCarIdx") if isinstance(di, dict) else getattr(di, "DriverCarIdx", None)
            if isinstance(my_idx, int):
                drivers = di.get("Drivers", []) if isinstance(di, dict) else getattr(di, "Drivers", [])
                if isinstance(drivers, list) and 0 <= my_idx < len(drivers):
                    d = drivers[my_idx]
                    raw = d.get("CarClassID") if isinstance(d, dict) else getattr(d, "CarClassID", None)
                    try:
                        my_class_id = int(raw)
                    except Exception:
                        pass
        except Exception:
            pass

        # Aktuelle Runde
        lap = None
        try:
            car_laps = ir["CarIdxLap"]
            if my_idx is not None and isinstance(car_laps, (list, tuple)) and 0 <= my_idx < len(car_laps):
                lap = int(car_laps[my_idx])
        except Exception:
            pass
        if lap is None:
            for name in ("Lap", "LapCurrentLap", "LapCompleted"):
                try:
                    lap = int(ir[name])
                    break
                except Exception:
                    pass

        # Tankinhalt
        fuel_level = None
        try:
            fuel_level = float(ir["FuelLevel"])
        except Exception:
            pass

        # Tankkapazität: Priorität FuelMaxLtr (series-/event-spezifisches Limit),
        # dann max gesehener FuelLevel (nach vollem Pit-Refuel = echtes Rennmaximum),
        # zuletzt fuel_level / FuelLevelPct (= physischer Tank, z.B. 110 L beim McLaren).
        tank_capacity = None

        # 1) Direkt aus iRacing-Feld (event-spezifisch, z.B. 108.9 L beim 6h-Rennen)
        for name in ("FuelMaxLtr", "FuelMax", "MaxFuel"):
            try:
                v = float(ir[name])
                if 0 < v <= 500:
                    tank_capacity = v
                    break
            except Exception:
                pass

        # 2) Höchster je gesehener Tankstand (steigt nach vollem Pit-Refuel auf das Limit)
        if fuel_level is not None and fuel_level > 1.0:
            if self._max_fuel_seen is None or fuel_level > self._max_fuel_seen:
                self._max_fuel_seen = fuel_level

        if tank_capacity is None and self._max_fuel_seen and self._max_fuel_seen > 1.0:
            tank_capacity = self._max_fuel_seen

        # 3) Fallback: fuel_level / FuelLevelPct  (= physischer Tank, kann zu groß sein)
        if tank_capacity is None:
            try:
                pct = float(ir["FuelLevelPct"])
                if pct > 1.5:
                    pct /= 100.0
                if pct > 0.01 and fuel_level is not None:
                    tank_capacity = fuel_level / pct
            except Exception:
                pass

        if tank_capacity is not None and not (0 < tank_capacity <= 500):
            tank_capacity = None

        # Referenz-Rundenzeit (live: bevorzugt letzte, sonst beste Runde)
        ref_lap_time = None
        for name in ("LapLastLapTime", "LapBestLapTime"):
            try:
                v = float(ir[name])
                if v > 0.5:
                    ref_lap_time = v
                    break
            except Exception:
                pass

        # Beste gesehene Runde tracken (Baseline für die nächste Session)
        if ref_lap_time and ref_lap_time > 0.5:
            if self._best_lap_time_seen is None or ref_lap_time < self._best_lap_time_seen:
                self._best_lap_time_seen = ref_lap_time

        # Fallback: gibt es noch keine Live-Rundenzeit, nimm die Baseline aus
        # dem Cache, damit die Strategie schon ab dem Start rechnen kann.
        if ref_lap_time is None and self._seed_ref_lap_time:
            ref_lap_time = self._seed_ref_lap_time

        return my_idx, my_class_id, lap, fuel_level, tank_capacity, ref_lap_time

    # ================================================================== #
    #  Schritt 2 — State-Updates (Mutation, kein Return-Wert)             #
    # ================================================================== #

    def _update_fuel_state(self, lap, fuel_level):
        """Aktualisiert Fuel-Tracking bei Rundenwechsel."""
        if lap is None or fuel_level is None:
            return
        if self._last_lap is None:
            self._last_lap = lap
            self._lap_fuel_start = fuel_level
            return
        if lap == self._last_lap:
            return
        # Neue Runde abgeschlossen
        if self._lap_fuel_start is not None:
            used = self._lap_fuel_start - fuel_level
            if 0.01 < used < 20.0:
                self._last_lap_fuel = self._this_lap_fuel
                self._this_lap_fuel = used
                self._fuel_lap_history.append(used)
                self._avg_fuel_per_lap = sum(self._fuel_lap_history) / len(self._fuel_lap_history)
        self._lap_fuel_start = fuel_level
        self._last_lap = lap

    def _update_stint_state(self, ir, lap):
        """Aktualisiert Stint-Zähler anhand des OnPitRoad-Flags."""
        on_pit = False
        try:
            on_pit = bool(ir["OnPitRoad"])
        except Exception:
            pass

        if on_pit and not self._in_pit_road:
            self._stint_start_lap = None   # Pit-Einfahrt: Stint zurücksetzen
            self._in_pit_road = True
        elif not on_pit and self._in_pit_road:
            self._stint_start_lap = lap    # Pit-Ausfahrt: neuen Stint beginnen
            self._in_pit_road = False

    # ================================================================== #
    #  Schritt 3 — Sektions-Builder (lesen nur, keine Seiteneffekte)     #
    # ================================================================== #

    def _estimate_laps_to_finish(self, ir, session_info, my_idx, my_lap, ref_lap_time):
        """
        Schätzt, wie viele Runden ICH bis zur Zielflagge noch fahre.

        Zeitrennen enden, wenn der LEADER nach Ablauf der Uhr die S/F-Linie
        überquert. Die Renndistanz in Runden richtet sich also nach dem
        Leader-Tempo, nicht nach meinem:

          leader_laps = floor(time_left / leader_lap_time) + 1
                        └ volle Runden bis Uhr 0 ┘   └ laufende Schlussrunde ┘

        Liege ich Runden zurück, fahre ich entsprechend weniger:

          my_laps = leader_laps - (leader_lap - my_lap)

        Fällt die Leader-Info aus, wird mit der eigenen Referenzzeit
        gerechnet (immer noch inkl. +1-Schlussrunde).
        """
        time_left = session_info.get("time_left") if session_info else None
        if not time_left or time_left <= 0 or not ref_lap_time or ref_lap_time <= 0.1:
            return None

        # Leader finden (CarIdxPosition == 1)
        leader_idx = None
        try:
            for i, p in enumerate(ir["CarIdxPosition"]):
                if p == 1:
                    leader_idx = i
                    break
        except Exception:
            pass

        # Leader-Rundenzeit (sonst eigene Referenz)
        leader_lap_time = ref_lap_time
        if leader_idx is not None:
            try:
                v = float(ir["CarIdxLastLapTime"][leader_idx])
                if v > 0.5:
                    leader_lap_time = v
            except Exception:
                pass

        leader_laps = math.floor(time_left / leader_lap_time) + 1

        # Rundenrückstand auf den Leader
        lap_delta = 0
        if leader_idx is not None and my_lap is not None:
            try:
                leader_lap = int(ir["CarIdxLap"][leader_idx])
                if leader_lap >= 0:
                    lap_delta = max(leader_lap - my_lap, 0)
            except Exception:
                pass

        return float(max(leader_laps - lap_delta, 0))

    def _build_fuel_section(self, ir, session_info, my_idx, lap, fuel_level, tank_capacity, ref_lap_time):
        """Alle Fuel- und Stint-Plan-Felder."""
        # Bevorzuge geglätteten Durchschnitt, dann letzte bekannte Werte
        fuel_per_lap = next(
            (v for v in (self._avg_fuel_per_lap, self._this_lap_fuel, self._last_lap_fuel)
             if v is not None and v > 0.01),
            None,
        )

        # Live-Tankreichweite – darf laufend runterzählen (Anzeige "Laps on fuel").
        laps_by_fuel = None
        if fuel_level is not None and fuel_per_lap:
            laps_by_fuel = min(max(fuel_level / fuel_per_lap, 0.0), 999.0)

        # Runden bis Rennende – leader-basiert (Zeitrennen-Regel)
        laps_to_finish = self._estimate_laps_to_finish(
            ir, session_info, my_idx, lap, ref_lap_time
        )

        # ── Strategische Werte: stabil halten ──────────────────────────
        # Defizit/Stopps/Refuel basieren auf dem Tankstand zu RUNDENBEGINN
        # (self._lap_fuel_start), NICHT auf dem live runterzählenden Wert.
        # Sonst wächst der Refuel-Wert kontinuierlich während man fährt, weil
        # laps_to_finish (ganzzahlig) konstant bleibt, der Tank aber sinkt.
        # So steht der Wert innerhalb einer Runde fix und springt nur beim
        # Rundenwechsel auf den neuen Schnitt.
        fuel_basis = self._lap_fuel_start if self._lap_fuel_start is not None else fuel_level
        laps_by_fuel_strat = (fuel_basis / fuel_per_lap) if (fuel_basis is not None and fuel_per_lap) else None

        # Fuel-Defizit und Stopp-Kalkulation
        fuel_deficit = None
        refuel_to_end = 0.0
        refuel_next_stop = 0.0
        pit_stops_left = 0
        if laps_to_finish and fuel_per_lap and fuel_basis is not None and tank_capacity:
            fuel_deficit = laps_to_finish * fuel_per_lap - fuel_basis
            if fuel_deficit > 0:
                refuel_to_end = fuel_deficit
                pit_stops_left = math.ceil(fuel_deficit / tank_capacity)
                # "Refuel this stop": Beim nächsten Stopp kommst du ~leer rein
                # (du boxt, wenn der Tank leer ist). Daher zählt NICHT der jetzt
                # freie Tankplatz, sondern wie viel du ab Boxenausfahrt bis ins
                # Ziel brauchst – begrenzt aufs Tankvolumen.
                #   - letzter Stopp  → genau bis ins Ziel auftanken
                #   - weitere Stopps → voll tanken (fuel_after_pit > tank)
                laps_after_pit = max(laps_to_finish - (laps_by_fuel_strat or 0.0), 0.0)
                fuel_after_pit = laps_after_pit * fuel_per_lap
                refuel_next_stop = min(fuel_after_pit, tank_capacity)

        # iRacing Add-Fuel-Einstellung
        user_add = None
        for name in ("FuelAddLtr", "FuelAdd"):
            try:
                user_add = float(ir[name])
                break
            except Exception:
                pass

        # Fuel @ End: wie viel Sprit bleibt übrig wenn kein weiterer Pit
        fuel_at_end = None
        if fuel_basis is not None and laps_to_finish is not None and fuel_per_lap:
            fuel_at_end = fuel_basis - laps_to_finish * fuel_per_lap

        return {
            "fuel_level":          fuel_level,
            "fuel_tank_capacity":  tank_capacity,
            "laps_remaining":      laps_by_fuel,
            "stint_laps_remaining": laps_by_fuel,
            "stint_laps_done":     (tank_capacity - fuel_level) / fuel_per_lap
                                   if (tank_capacity and fuel_per_lap and fuel_level is not None
                                       and tank_capacity - fuel_level > 0)
                                   else None,
            "pit_stops_left":      pit_stops_left if fuel_deficit is not None else None,
            "refuel_to_end":       refuel_to_end  if fuel_deficit is not None else None,
            "refuel_next_stop":    refuel_next_stop if fuel_deficit is not None else None,
            "fuel_add_setting":    user_add,
            "fuel_at_end":         fuel_at_end,
            "last_lap_fuel":       self._last_lap_fuel,
            "this_lap_fuel":       self._this_lap_fuel,
            "laps_to_finish":      laps_to_finish,
            "lap_number":          lap,
            "pit_in_on_lap":       (lap + math.floor(laps_by_fuel_strat))
                                   if (lap is not None and laps_by_fuel_strat is not None)
                                   else None,
        }

    def _build_laptime_section(self, ir):
        """Rundenzeiten und Delta."""
        out = {
            "last_lap_time":    None,
            "best_lap_time":    None,
            "current_lap_time": None,
            "delta_to_best":    None,
        }
        for ir_name, key in (
            ("LapLastLapTime",    "last_lap_time"),
            ("LapBestLapTime",    "best_lap_time"),
            ("LapCurrentLapTime", "current_lap_time"),
        ):
            try:
                v = float(ir[ir_name])
                if v > 0.0:
                    out[key] = v
            except Exception:
                pass
        try:
            v = float(ir["LapDeltaToBestLap"])
            if abs(v) < 100:
                out["delta_to_best"] = v
        except Exception:
            pass
        return out

    def _build_race_section(self, ir, lap):
        """Position, Reifen, Incidents, Stint-Runden."""
        out = {
            "class_position":  None,
            "overall_position": None,
            "team_incidents":  None,
            "tire_compound":   None,
            "stint_laps":      None,
        }
        try:
            v = int(ir["PlayerCarClassPosition"])
            if v > 0: out["class_position"] = v
        except Exception:
            pass
        try:
            v = int(ir["PlayerCarPosition"])
            if v > 0: out["overall_position"] = v
        except Exception:
            pass
        for name in ("PlayerCarTeamIncidentCount", "PlayerCarDriverIncidentCount", "PlayerCarMyIncidentCount"):
            try:
                v = int(ir[name])
                if v >= 0:
                    out["team_incidents"] = v
                    break
            except Exception:
                pass
        try:
            out["tire_compound"] = normalize_tyre_label(ir["PlayerTireCompound"])
        except Exception:
            pass
        if lap is not None and self._stint_start_lap is not None:
            out["stint_laps"] = lap - self._stint_start_lap
        return out

    def _build_conditions_section(self, ir):
        """Luft- und Streckentemperatur."""
        out = {"air_temp": None, "track_temp": None}
        for name in ("AirTemp", "AirTempCrew", "Temp"):
            try:
                v = float(ir[name])
                if -50 < v < 80:
                    out["air_temp"] = v
                    break
            except Exception:
                pass
        for name in ("TrackTempCrew", "TrackTemp", "TrackSurfaceTemp"):
            try:
                v = float(ir[name])
                if 0 < v < 100:
                    out["track_temp"] = v
                    break
            except Exception:
                pass
        return out

    def _build_class_gaps(self, ir, my_idx, my_class_id, ref_lap_time):
        """
        Gap zu P-1 (direkt vor uns) und P+1 (direkt hinter uns) in der Klasse.

        Formel:  gap = |Δlap * ref_lap_time + (other_est - my_est)|
        CarIdxEstTime = verstrichene Zeit auf der aktuellen Runde.
        Lap-Differenz kompensiert korrekt, wenn Autos auf verschiedenen Runden sind.
        """
        out = {"gap_class_ahead": None, "gap_class_behind": None}
        if ref_lap_time is None:
            return out

        # Telemetrie-Arrays laden
        try:
            est_arr  = ir["CarIdxEstTime"]
            lap_arr  = ir["CarIdxLap"]
            cpos_arr = ir["CarIdxClassPosition"]
        except KeyError:
            return out

        def _safe(arr, idx, cast, ok=None):
            try:
                v = cast(arr[idx])
                return v if (ok is None or ok(v)) else None
            except Exception:
                return None

        my_est  = _safe(est_arr,  my_idx, float, lambda v: abs(v) < 1e6)
        my_lap  = _safe(lap_arr,  my_idx, int,   lambda v: v >= 0)
        my_cpos = _safe(cpos_arr, my_idx, int,   lambda v: v > 0)

        if None in (my_est, my_lap, my_cpos):
            return out

        # Direkt benachbarte Autos in der Klasse finden
        try:
            di = ir["DriverInfo"]
            drivers = di.get("Drivers", []) if isinstance(di, dict) else getattr(di, "Drivers", [])
        except Exception:
            return out

        # Alle Autos derselben Klasse einsammeln
        class_cars = []
        for d in (drivers if isinstance(drivers, list) else []):
            if not isinstance(d, dict) or d.get("CarIsPaceCar"):
                continue
            idx = d.get("CarIdx")
            if idx is None:
                continue
            try:
                if int(d.get("CarClassID") or 0) != my_class_id:
                    continue
            except Exception:
                continue
            cpos = _safe(cpos_arr, idx, int, lambda v: v > 0)
            class_cars.append((idx, cpos))

        # Wenn CarIdxClassPosition für alle 0 ist → selbst aus Runden+Distanz berechnen
        if all(cpos == 0 for _, cpos in class_cars):
            try:
                lap_arr2  = ir["CarIdxLap"]
                pct_arr   = ir["CarIdxLapDistPct"]
                def _sort_key(entry):
                    idx2, _ = entry
                    l = int(lap_arr2[idx2]) if idx2 < len(lap_arr2) else 0
                    p = float(pct_arr[idx2]) if idx2 < len(pct_arr) else 0.0
                    return (l, p)
                class_cars.sort(key=_sort_key, reverse=True)
                for rank, (idx2, _) in enumerate(class_cars, start=1):
                    class_cars[class_cars.index((idx2, _))] = (idx2, rank)
                # my_cpos neu setzen
                for idx2, rank in class_cars:
                    if idx2 == my_idx:
                        my_cpos = rank
                        break
            except Exception:
                pass

        ahead_idx = behind_idx = None
        for idx, cpos in class_cars:
            if idx == my_idx:
                continue
            if cpos == my_cpos - 1:
                ahead_idx = idx
            elif cpos == my_cpos + 1:
                behind_idx = idx
            if ahead_idx is not None and behind_idx is not None:
                break

        def _gap_sec(other_idx):
            o_est = _safe(est_arr, other_idx, float, lambda v: abs(v) < 1e6)
            o_lap = _safe(lap_arr, other_idx, int,   lambda v: v >= 0)
            if o_est is None or o_lap is None:
                return None
            return abs((o_lap - my_lap) * ref_lap_time + (o_est - my_est))

        if ahead_idx  is not None: out["gap_class_ahead"]  = _gap_sec(ahead_idx)
        if behind_idx is not None: out["gap_class_behind"] = _gap_sec(behind_idx)
        return out

    # ================================================================== #
    #  Öffentliche API                                                     #
    # ================================================================== #

    def reset(self):
        """Setzt alle transienten Session-Daten zurück (neues Rennen / neue Session).
        Cache-Daten (Baseline) bleiben erhalten."""
        self._last_lap = None
        self._lap_fuel_start = None
        self._last_lap_fuel = None
        self._this_lap_fuel = None
        self._avg_fuel_per_lap = None
        self._fuel_lap_history.clear()
        self._best_lap_time_seen = None
        self._seed_ref_lap_time = None
        self._max_fuel_seen = None
        self._in_pit_road = False
        self._stint_start_lap = None
        self._seed_loaded = False  # Cache-Seeding für neue Session neu auslösen

    # ================================================================== #

    def build_strategy_payload(self, session_info=None):
        """
        Baut das komplette Strategy-Payload fuer das Overlay.

        Ablauf: Kontext → State-Update → Sektionen → Persistenz
        """
        session_info = session_info or {}

        if not self.ir.is_connected:
            return self._empty_payload()

        ir = self.ir
        self._maybe_seed_from_cache(session_info)

        # 1. Kontext einmalig auflösen
        my_idx, my_class_id, lap, fuel_level, tank_capacity, ref_lap_time = (
            self._resolve_context(ir)
        )

        # 2. Zustand aktualisieren (Fuel-History, Stint-Counter)
        self._update_fuel_state(lap, fuel_level)
        self._update_stint_state(ir, lap)

        # 3. Sektionen aufbauen und zusammenführen
        payload = {}
        payload.update(self._build_fuel_section(ir, session_info, my_idx, lap, fuel_level, tank_capacity, ref_lap_time))
        payload.update(self._build_laptime_section(ir))
        payload.update(self._build_race_section(ir, lap))
        payload.update(self._build_conditions_section(ir))
        payload.update(
            self._build_class_gaps(ir, my_idx, my_class_id, ref_lap_time)
            if my_idx is not None and my_class_id is not None
            else {"gap_class_ahead": None, "gap_class_behind": None}
        )

        # 4. Persistenz (max. alle 30 s)
        self._maybe_persist(session_info)

        return payload

    # ------------------------------------------------------------------ #

    @staticmethod
    def _empty_payload() -> dict:
        """Leeres Payload wenn iRacing nicht verbunden ist."""
        keys = (
            "fuel_level", "fuel_tank_capacity", "laps_remaining", "stint_laps_done",
            "stint_laps_remaining", "pit_stops_left", "refuel_to_end", "refuel_next_stop",
            "fuel_add_setting", "fuel_at_end", "last_lap_fuel", "this_lap_fuel",
            "laps_to_finish", "lap_number", "pit_in_on_lap",
            "last_lap_time", "best_lap_time", "current_lap_time", "delta_to_best",
            "class_position", "overall_position", "team_incidents", "tire_compound", "stint_laps",
            "air_temp", "track_temp",
            "gap_class_ahead", "gap_class_behind",
        )
        return dict.fromkeys(keys, None)
