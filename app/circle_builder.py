# circle_builder.py

import math

from telemetry_common import get_driver_list


class CircleBuilder:
    """Baut die Daten für das "Circle of Doom"-Overlay.

    Liefert ein dict:
        {
          "cars":    [ {car_idx, pct, lap, lap_diff, pos, name, car_number,
                        car_class_id, car_class_color, in_pit, me}, ... ],
          "sectors": [0.0, 0.33, 0.66],   # SectorStartPct (0..1), inkl. Start/Ziel
          "track_name": "Spa",
        }

    pct = CarIdxLapDistPct (0..1), Streckenposition.
    Start/Ziel liegt bei pct = 0.0 (im Overlay oben auf 12 Uhr).
    """

    # iRacing CarIdxTrackSurface: -1 = NotInWorld
    NOT_IN_WORLD = -1

    # Wie viele Beobachtungen je Pit-Ein-/Ausfahrt gemittelt werden.
    PIT_SAMPLE_WINDOW = 8

    def __init__(self, ir):
        self.ir = ir

        # Auto-Erkennung Boxen-Ein-/Ausfahrt: iRacing hat dafür keine Variable.
        # Wir lernen sie aus den OnPitRoad-Flanken ALLER Autos:
        #   on-track -> pit  => Einfahrt (pct kurz vor dem Abbiegen)
        #   pit -> on-track  => Ausfahrt (pct beim Wiederauffädeln)
        self._pit_state: dict[int, bool] = {}       # car_idx -> war on_pit
        self._last_ontrack_pct: dict[int, float] = {}  # car_idx -> letztes pct auf der Strecke
        self._entry_samples: list[float] = []
        self._exit_samples: list[float] = []

    def reset(self):
        """Lernzustand zurücksetzen (Session-/Streckenwechsel)."""
        self._pit_state.clear()
        self._last_ontrack_pct.clear()
        self._entry_samples.clear()
        self._exit_samples.clear()

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def build(self) -> dict:
        ir = self.ir
        empty = {"cars": [], "sectors": [], "track_name": None}

        if not getattr(ir, "is_connected", False):
            return empty

        drivers, player_idx = get_driver_list(ir)
        if not drivers:
            return empty

        lap_pct_raw = self._arr(ir, "CarIdxLapDistPct")
        lap_raw     = self._arr(ir, "CarIdxLap")
        pos_raw     = self._arr(ir, "CarIdxPosition")
        cls_pos_raw = self._arr(ir, "CarIdxClassPosition")
        surface_raw = self._arr(ir, "CarIdxTrackSurface")
        pit_raw     = self._arr(ir, "CarIdxOnPitRoad")
        est_raw     = self._arr(ir, "CarIdxEstTime")  # verstrichene Zeit an Track-Pos

        if not lap_pct_raw:
            return empty

        # meine aktuelle Runde (für lap_diff / Überrundung)
        my_lap = None
        if player_idx is not None and player_idx >= 0:
            try:
                my_lap = int(lap_raw[player_idx])
            except Exception:
                my_lap = None

        # Referenz-Rundenzeit (für Pit-Out-Indikator): letzte, sonst beste Runde
        ref_lap = None
        for name in ("LapLastLapTime", "LapBestLapTime"):
            try:
                v = float(ir[name])
            except Exception:
                continue
            if v and v > 0.5:
                ref_lap = v
                break

        cars = []
        for d in drivers:
            if not isinstance(d, dict):
                continue
            if d.get("CarIsPaceCar") or d.get("IsSpectator"):
                continue

            idx = d.get("CarIdx")
            if idx is None:
                continue
            try:
                idx = int(idx)
            except Exception:
                continue

            # Streckenposition
            pct = self._clamp(lap_pct_raw, idx, lo=0.0, hi=1.0)
            if pct is None:
                continue

            # Nicht in der Welt (Garage / ausgeloggt) -> überspringen
            surf = self._safe_int(surface_raw, idx, default=None)
            if surf is not None and surf == self.NOT_IN_WORLD:
                continue

            lap = self._safe_int(lap_raw, idx, default=0)
            pos = self._int_pos(cls_pos_raw, idx) or self._int_pos(pos_raw, idx) or 0
            in_pit = bool(self._safe_int(pit_raw, idx, default=0))
            est = self._clamp(est_raw, idx, lo=0.0, hi=1e6)  # Sek. auf aktueller Runde

            # Boxen-Ein-/Ausfahrt aus der OnPitRoad-Flanke dieses Autos lernen
            self._learn_pit_marks(idx, pct, in_pit)

            name = (
                d.get("UserName") or d.get("AbbrevName")
                or d.get("Initials") or "Unknown"
            )
            car_number = str(d.get("CarNumber") or d.get("CarNumberRaw") or "")

            try:
                car_class_id = int(d.get("CarClassID") or 0)
            except Exception:
                car_class_id = 0
            car_class_color = self._class_color(d.get("CarClassColor"))

            cars.append({
                "car_idx":         idx,
                "pct":             round(pct, 5),
                "est":             round(est, 4) if est is not None else None,
                "lap":             lap,
                "lap_diff":        (lap - my_lap) if my_lap is not None else 0,
                "pos":             pos,
                "name":            name,
                "car_number":      car_number,
                "car_class_id":    car_class_id,
                "car_class_color": car_class_color,
                "in_pit":          in_pit,
                "me":              (idx == player_idx),
            })

        return {
            "cars":       cars,
            "sectors":    self._sector_pcts(),
            "track_name": self._track_name(),
            "ref_lap":    ref_lap,
            "time":       self._session_time(),
            "pit_entry_pct": self._circular_mean(self._entry_samples),
            "pit_exit_pct":  self._circular_mean(self._exit_samples),
        }

    # ------------------------------------------------------------------
    # Sektoren / Strecke
    # ------------------------------------------------------------------

    def _sector_pcts(self) -> list:
        """SectorStartPct aus SplitTimeInfo (0..1), aufsteigend sortiert."""
        try:
            sti = self.ir["SplitTimeInfo"]
        except Exception:
            return []

        sectors = sti.get("Sectors") if isinstance(sti, dict) else None
        if not isinstance(sectors, list):
            return []

        pcts = []
        for s in sectors:
            if not isinstance(s, dict):
                continue
            v = s.get("SectorStartPct")
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if 0.0 <= v < 1.0:
                pcts.append(round(v, 5))

        return sorted(set(pcts))

    # ------------------------------------------------------------------
    # Boxen-Ein-/Ausfahrt: Lernen + Mittelung
    # ------------------------------------------------------------------

    def _learn_pit_marks(self, idx: int, pct: float, in_pit: bool) -> None:
        prev = self._pit_state.get(idx)
        if prev is not None:
            if (not prev) and in_pit:
                # Einfahrt: pct kurz VOR dem Abbiegen (letzte On-Track-Position)
                entry = self._last_ontrack_pct.get(idx, pct)
                self._push_sample(self._entry_samples, entry)
            elif prev and (not in_pit):
                # Ausfahrt: pct beim Wiederauffädeln auf die Strecke
                self._push_sample(self._exit_samples, pct)
        self._pit_state[idx] = in_pit
        if not in_pit:
            self._last_ontrack_pct[idx] = pct

    def _push_sample(self, samples: list, value: float) -> None:
        samples.append(value)
        if len(samples) > self.PIT_SAMPLE_WINDOW:
            samples.pop(0)

    @staticmethod
    def _circular_mean(samples: list):
        """Mittelt pct-Werte (0..1) auf dem Kreis — robust über die 0/1-Naht."""
        if not samples:
            return None
        sx = sum(math.sin(2 * math.pi * p) for p in samples)
        sy = sum(math.cos(2 * math.pi * p) for p in samples)
        if abs(sx) < 1e-9 and abs(sy) < 1e-9:
            return None
        ang = math.atan2(sx, sy)
        return round((ang / (2 * math.pi)) % 1.0, 5)

    def _session_time(self):
        """Sekunden seit Session-Start. Läuft im Replay synchron (pausiert/springt
        mit). Dient dem Overlay als replay-synchrone Uhr für die Boxen-Standzeit."""
        try:
            v = float(self.ir["SessionTime"])
            if v >= 0:
                return round(v, 3)
        except Exception:
            pass
        return None

    def _track_name(self):
        try:
            weekend = self.ir["WeekendInfo"]
        except Exception:
            return None
        if isinstance(weekend, dict):
            return (
                weekend.get("TrackDisplayShortName")
                or weekend.get("TrackDisplayName")
                or weekend.get("TrackName")
            )
        return None

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    @staticmethod
    def _arr(ir, key: str) -> list:
        try:
            return ir[key] or []
        except KeyError:
            return []

    @staticmethod
    def _clamp(arr: list, idx: int, lo: float, hi: float):
        try:
            v = float(arr[idx])
            if v < lo or v > hi:
                return None
            return v
        except Exception:
            return None

    @staticmethod
    def _safe_int(arr: list, idx: int, default=0):
        try:
            return int(arr[idx])
        except Exception:
            return default

    @staticmethod
    def _int_pos(arr: list, idx: int):
        """Positions-Feld lesen; iRacing liefert 0 wenn (noch) keine Position."""
        try:
            v = int(arr[idx])
            return v if v > 0 else None
        except Exception:
            return None

    @staticmethod
    def _class_color(raw_color) -> str:
        """iRacing CarClassColor (Dezimal-Int oder '0xRRGGBB') -> '#RRGGBB'."""
        if raw_color is None:
            return "#888888"
        try:
            s = str(raw_color).strip()
            color_int = int(s, 16) if s.lower().startswith("0x") else int(s)
            if color_int > 0:
                return f"#{color_int:06X}"
        except Exception:
            pass
        return "#888888"
