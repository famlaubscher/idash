# pit_calibrator.py
#
# Geführte Kalibrierung der Pit-Parameter für den "Circle of Doom".
# Der Fahrer absolviert in Practice drei Schritte; die Werte werden aus der
# Telemetrie GEMESSEN (nicht angenommen) und pro Strecke/Layout/Auto/Serie
# in pit_cache.json gespeichert:
#
#   1) Boxen-DURCHFAHRT (ohne Halt)  -> pit_lane_loss_sec
#   2) Tankstopp (von ~leer auf voll) -> fuel_rate_lps
#   3) Reifen-only-Stopp (kein Tanken) -> tire_change_sec
#
# Die Schritte können in beliebiger Reihenfolge gefahren werden; jeder füllt
# seinen Slot. Sind alle drei gemessen, gilt die Kalibrierung als komplett.

import json
import math
import os
import sys


# Schwellen
_STOP_SPEED = 0.7        # m/s — darunter gilt das Auto als "steht"
_MIN_STATIONARY = 1.2    # s   — so lange muss es stehen, damit es ein "Stopp" ist
_MIN_REFUEL_L = 2.0      # L   — ab so viel Nachtanken gilt es als Tankstopp
_MAX_TIRE_FLAT_L = 0.6   # L   — darunter gilt der Tankstand als "flach" (Reifen-only)


def _cache_path() -> str:
    if hasattr(sys, "_MEIPASS"):
        base = os.path.join(os.path.expanduser("~"), ".idash_overlay")
        os.makedirs(base, exist_ok=True)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "pit_cache.json")


class PitCalibrator:
    """State-Machine, die pro Slow-Tick mit dem Spieler-Kontext gefüttert wird."""

    def __init__(self):
        self.active = False
        self._key = None

        # Ergebnis-Slots (gemessen) für den AKTUELLEN Kalibrierlauf
        self.pit_loss = None     # s
        self.fuel_rate = None    # L/s
        self.tire_time = None    # s

        # Pro-Boxenbesuch-Tracking
        self._prev_on_pit = False
        self._entry_time = None
        self._entry_pct = None
        self._last_ontrack_pct = None
        self._stationary_start = None
        self._stationary_end = None
        self._fuel_samples = []          # (time, fuel) während des Stehens
        self._saw_stationary = False

        # Letzte Statusmeldung fürs Overlay
        self._last_event = ""

        # Persistenter Cache (alle Strecken/Autos)
        self._cache = {}
        self._cache_loaded = False

    # ------------------------------------------------------------------
    # Steuerung
    # ------------------------------------------------------------------

    def start(self, key: str):
        """Startet einen neuen Kalibrierlauf für den gegebenen Key."""
        self.active = True
        self._key = key
        self.pit_loss = None
        self.fuel_rate = None
        self.tire_time = None
        self._reset_visit()
        self._last_event = "Kalibrierung gestartet"

    def stop(self):
        """Beendet den Lauf und speichert, was gemessen wurde."""
        if self.active and self._key and self._any_measured():
            self._save_partial(self._key)
        self.active = False
        self._last_event = "Kalibrierung beendet"

    def reset(self):
        self.pit_loss = self.fuel_rate = self.tire_time = None
        self._reset_visit()
        self._last_event = "zurückgesetzt"

    def _reset_visit(self):
        self._entry_time = None
        self._entry_pct = None
        self._stationary_start = None
        self._stationary_end = None
        self._fuel_samples = []
        self._saw_stationary = False

    def _any_measured(self) -> bool:
        return any(v is not None for v in (self.pit_loss, self.fuel_rate, self.tire_time))

    def is_complete(self) -> bool:
        return all(v is not None for v in (self.pit_loss, self.fuel_rate, self.tire_time))

    # ------------------------------------------------------------------
    # Fütterung pro Tick
    # ------------------------------------------------------------------

    def feed(self, *, time, on_pit, speed, fuel, pct, ref_lap):
        """Verarbeitet einen Telemetrie-Tick des SPIELERS."""
        if not self.active:
            return
        if time is None or pct is None:
            return

        on_pit = bool(on_pit)

        # Boxeneinfahrt (Flanke on-track -> pit)
        if on_pit and not self._prev_on_pit:
            self._entry_time = time
            self._entry_pct = self._last_ontrack_pct if self._last_ontrack_pct is not None else pct
            self._stationary_start = None
            self._stationary_end = None
            self._fuel_samples = []
            self._saw_stationary = False

        # Während des Boxenbesuchs: Stillstand + Tankverlauf erfassen
        if on_pit:
            if speed is not None and speed < _STOP_SPEED:
                if self._stationary_start is None:
                    self._stationary_start = time
                self._stationary_end = time
                if (time - self._stationary_start) >= _MIN_STATIONARY:
                    self._saw_stationary = True
                if fuel is not None:
                    self._fuel_samples.append((time, fuel))

        # Boxenausfahrt (Flanke pit -> on-track): Besuch auswerten
        if (not on_pit) and self._prev_on_pit:
            self._finalize_visit(exit_time=time, exit_pct=pct, ref_lap=ref_lap)

        # On-track-Position für die nächste Einfahrt merken
        if not on_pit:
            self._last_ontrack_pct = pct

        self._prev_on_pit = on_pit

    def _finalize_visit(self, *, exit_time, exit_pct, ref_lap):
        if self._entry_time is None:
            return

        if not self._saw_stationary:
            # --- Boxen-DURCHFAHRT -> Pit-Lane-Verlust ---
            if ref_lap and ref_lap > 0.5 and self._entry_pct is not None:
                transit = exit_time - self._entry_time
                span = ((exit_pct - self._entry_pct) % 1 + 1) % 1
                loss = transit - span * ref_lap
                if 0.0 < loss < 120.0:
                    self.pit_loss = round(loss, 2)
                    self._last_event = f"Durchfahrt gemessen: Verlust {self.pit_loss:.1f}s"
                else:
                    self._last_event = "Durchfahrt unplausibel — bitte wiederholen"
        else:
            # --- STOPP: tanken oder Reifen-only? ---
            fuels = [f for _, f in self._fuel_samples if f is not None]
            fuel_delta = (max(fuels) - min(fuels)) if len(fuels) >= 2 else 0.0
            stationary_dur = (self._stationary_end - self._stationary_start) \
                if (self._stationary_start is not None and self._stationary_end is not None) else 0.0

            if fuel_delta >= _MIN_REFUEL_L:
                rate = self._fuel_rate_from_samples()
                if rate and 0.3 < rate < 20.0:
                    self.fuel_rate = round(rate, 3)
                    self._last_event = f"Tankrate gemessen: {self.fuel_rate:.2f} L/s"
                else:
                    self._last_event = "Tankrate unplausibel — bitte wiederholen"
            elif fuel_delta <= _MAX_TIRE_FLAT_L:
                if 2.0 < stationary_dur < 90.0:
                    self.tire_time = round(stationary_dur, 2)
                    self._last_event = f"Reifenzeit gemessen: {self.tire_time:.1f}s"
                else:
                    self._last_event = "Reifenzeit unplausibel — bitte wiederholen"
            else:
                self._last_event = "Stopp mehrdeutig (etwas getankt) — Schritt wiederholen"

        # Bei Komplettierung automatisch speichern
        if self.active and self._key and self.is_complete():
            self._save_partial(self._key)
            self._last_event = "Kalibrierung komplett & gespeichert ✓"

        self._reset_visit()

    def _fuel_rate_from_samples(self):
        """Robuste Steigung L/s aus den (time, fuel)-Proben während des Tankens.
        Nimmt nur den steigenden Abschnitt und bildet den Median der Schritt-Raten."""
        pts = [(t, f) for t, f in self._fuel_samples if f is not None]
        if len(pts) < 3:
            return None
        rates = []
        for (t0, f0), (t1, f1) in zip(pts, pts[1:]):
            dt = t1 - t0
            df = f1 - f0
            if dt > 0.01 and df > 0.0:          # nur steigend
                rates.append(df / dt)
        if len(rates) < 2:
            # Fallback: Gesamtsteigung über das steigende Fenster
            rising = [(t, f) for t, f in pts]
            t0, f0 = rising[0]
            t1, f1 = rising[-1]
            if t1 - t0 > 0.5 and f1 - f0 > 0:
                return (f1 - f0) / (t1 - t0)
            return None
        rates.sort()
        return rates[len(rates) // 2]           # Median

    # ------------------------------------------------------------------
    # Status fürs Overlay
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "active":   self.active,
            "pit_loss": self.pit_loss,
            "fuel_rate": self.fuel_rate,
            "tire_time": self.tire_time,
            "complete": self.is_complete(),
            "event":    self._last_event,
        }

    # ------------------------------------------------------------------
    # Persistenz (pit_cache.json), Key = car|track|config|series
    # ------------------------------------------------------------------

    def _load_cache(self):
        if self._cache_loaded:
            return
        self._cache_loaded = True
        try:
            p = _cache_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._cache = data
        except Exception as e:
            print("pit_cache laden fehlgeschlagen:", e)

    def _save_partial(self, key: str):
        self._load_cache()
        entry = self._cache.get(key) if isinstance(self._cache.get(key), dict) else {}
        if self.pit_loss is not None:
            entry["pit_lane_loss_sec"] = self.pit_loss
        if self.fuel_rate is not None:
            entry["fuel_rate_lps"] = self.fuel_rate
        if self.tire_time is not None:
            entry["tire_change_sec"] = self.tire_time
        self._cache[key] = entry
        try:
            p = _cache_path()
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, sort_keys=True)
            os.replace(tmp, p)
        except Exception as e:
            print("pit_cache schreiben fehlgeschlagen:", e)

    def learned_for(self, key: str) -> dict:
        """Gelernte Werte für den Key (snake_case, wie overlay_config.json['pit'])."""
        self._load_cache()
        entry = self._cache.get(key)
        return dict(entry) if isinstance(entry, dict) else {}

    @staticmethod
    def make_key(ir) -> str:
        """Key aus Auto + Strecke + Layout + Serie."""
        car = track = config = series = "?"
        try:
            di = ir["DriverInfo"]
            my = di.get("DriverCarIdx") if isinstance(di, dict) else getattr(di, "DriverCarIdx", None)
            drivers = di.get("Drivers", []) if isinstance(di, dict) else getattr(di, "Drivers", [])
            if isinstance(my, int) and isinstance(drivers, list) and 0 <= my < len(drivers):
                d = drivers[my]
                car = (d.get("CarPath") or d.get("CarScreenNameShort") or "?") if isinstance(d, dict) else "?"
        except Exception:
            pass
        try:
            w = ir["WeekendInfo"]
            if isinstance(w, dict):
                track = w.get("TrackName") or w.get("TrackDisplayName") or "?"
                config = w.get("TrackConfigName") or "-"
                series = w.get("SeriesName") or w.get("Category") or "-"
        except Exception:
            pass
        return f"{car}|{track}|{config}|{series}"
