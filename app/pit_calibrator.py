# pit_calibrator.py
#
# Geführte Kalibrierung der Pit-Parameter für den "Circle of Doom".
# Der Fahrer absolviert in Practice drei Schritte; die Werte werden aus der
# Telemetrie GEMESSEN (nicht angenommen) und pro Strecke/Layout/Auto/Serie
# in pit_cache.json gespeichert:
#
#   1) Boxen-DURCHFAHRT (ohne Halt)  -> pit_lane_loss_sec (+ Pit-Marken)
#   2) Tankstopp                      -> fuel_rate_lps
#   3) Reifen-Stopp (kein Tanken)     -> tire_change_sec (auf 4 Reifen normiert)
#
# Die Schritte können in beliebiger Reihenfolge gefahren werden; jeder füllt
# seinen Slot. Sind alle drei gemessen, gilt die Kalibrierung als komplett.
#
# DETERMINISTISCH statt heuristisch: Ob ein Service stattfindet und WAS gemacht
# wird, kommt aus der iRacing-Telemetrie — PitstopActive (Service-Fenster) und
# PitSvFlags (welche Reifen / Sprit). Die Speed-Stillstands- und
# Sprit-Schwellen-Heuristik dient nur noch als Fallback (alte Aufnahmen ohne
# diese Felder). Zusätzlich werden die EXAKTEN Boxen-Ein-/Ausfahrt-Positionen
# (LapDistPct) festgehalten und ersetzen die All-Cars-Heuristik im circle_builder.
#
# Reifenmodell: gemessen wird die Service-Dauer eines Reifen-Stopps; sie wird
# über die Reifenanzahl (PitSvFlags) auf "alle 4" normiert
# (tire_change_sec = dauer × 4 / anzahl). Die Standzeit für n Reifen ergibt sich
# später als tire_change_sec × n/4.

import json
import math
import os
import sys


# Schwellen
_STOP_SPEED = 0.7        # m/s — darunter gilt das Auto als "steht" (nur Fallback)
_MIN_STATIONARY = 1.2    # s   — so lange muss es stehen, damit es ein "Stopp" ist
_MIN_REFUEL_L = 2.0      # L   — ab so viel Nachtanken gilt es als Tankstopp (Fallback)
_MAX_TIRE_FLAT_L = 0.6   # L   — darunter gilt der Tankstand als "flach" (Fallback)

# iRacing-Blackbox (PitSvFlags) — deterministisch, was beim Stopp gemacht wird.
_PITSV_TIRE_MASK = 0x0F  # LF|RF|LR|RR — gesetzte Bits = Anzahl gewechselter Reifen
_PITSV_FUEL      = 0x10   # FuelFill-Bit


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
        self._svc_start = None           # Beginn Service-Fenster (PitstopActive/Stillstand)
        self._svc_end = None             # Ende Service-Fenster
        self._svc_active = False         # fand in diesem Besuch ein Service statt?
        self._sv_flags = 0               # OR der PitSvFlags während des Service
        self._fuel_samples = []          # (time, fuel) während des Service

        # Deterministische Boxen-Ein-/Ausfahrt-Marken (LapDistPct) + Reifeninfo
        self._pit_entry_pct = None
        self._pit_exit_pct = None
        self._tires_last = None          # Reifenanzahl des letzten Reifen-Stopps

        # Arming-Gate: erst scharf, wenn das Auto die Box einmal verlassen hat
        # UND eine Referenzrunde vorliegt (siehe feed()).
        self._armed = False
        self._ref_lap = None     # s — zuletzt gesehene Rundenzeit (nur fürs Gate)

        # Referenz-Runden-Erfassung: wir sammeln abgeschlossene Runden und
        # markieren jede Runde mit Boxen-Kontakt (In-/Out-/Durchfahrtsrunde) als
        # "unsauber". Als Tempomaß für den Pit-Verlust dient die SCHNELLSTE
        # SAUBERE (grüne) Runde. Der Pit-Verlust wird nachgerechnet, sobald (oder
        # eine schnellere) saubere Runde vorliegt — auch wenn die Durchfahrt
        # bereits davor gefahren wurde.
        self._best_clean_lap = None   # s — schnellste saubere Runde
        self._lap_had_pit = True      # hatte die laufende Runde Boxen-Kontakt?
        self._prev_ref_lap = None     # zur Flankenerkennung "Runde abgeschlossen"
        self._dt_raw = None           # (transit, span) der gemessenen Durchfahrt

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
        self._armed = False
        self._ref_lap = None
        self._best_clean_lap = None
        self._lap_had_pit = True
        self._prev_ref_lap = None
        self._dt_raw = None
        self._prev_on_pit = False
        self._pit_entry_pct = None
        self._pit_exit_pct = None
        self._tires_last = None
        self._reset_visit()
        self._last_event = "Kalibrierung gestartet — Box verlassen & Referenzrunde fahren"

    def stop(self):
        """Beendet den Lauf und speichert, was gemessen wurde."""
        if self.active and self._key and self._any_measured():
            self._save_partial(self._key)
        self.active = False
        self._last_event = "Kalibrierung beendet"

    def reset(self):
        self.pit_loss = self.fuel_rate = self.tire_time = None
        self._armed = False
        self._best_clean_lap = None
        self._lap_had_pit = True
        self._prev_ref_lap = None
        self._dt_raw = None
        self._prev_on_pit = False
        self._reset_visit()
        self._last_event = "zurückgesetzt"

    def _reset_visit(self):
        self._entry_time = None
        self._entry_pct = None
        self._svc_start = None
        self._svc_end = None
        self._svc_active = False
        self._sv_flags = 0
        self._fuel_samples = []

    def _any_measured(self) -> bool:
        return any(v is not None for v in (self.pit_loss, self.fuel_rate, self.tire_time))

    def is_complete(self) -> bool:
        return all(v is not None for v in (self.pit_loss, self.fuel_rate, self.tire_time))

    # ------------------------------------------------------------------
    # Fütterung pro Tick
    # ------------------------------------------------------------------

    def feed(self, *, time, on_pit, speed, fuel, pct, ref_lap,
             pitstop_active=None, pit_sv_flags=None):
        """Verarbeitet einen Telemetrie-Tick des SPIELERS.

        pitstop_active / pit_sv_flags sind die deterministischen iRacing-Signale
        (PitstopActive, PitSvFlags). Fehlen sie (None, z.B. alte Aufnahme), wird
        auf die Speed-/Sprit-Heuristik zurückgefallen."""
        if not self.active:
            return
        if time is None or pct is None:
            return

        on_pit = bool(on_pit)

        # Referenzrunde mitführen (für Anzeige, Durchfahrt-Messung und Gate).
        if ref_lap is not None and ref_lap > 0.5:
            self._ref_lap = float(ref_lap)

        # Arming-Gate: NICHT messen, solange die Kalibrierung nicht "scharf" ist.
        # Scharf wird sie erst, wenn das Auto auf der Strecke ist (Box mindestens
        # einmal verlassen) UND eine gültige Referenzrunde vorliegt. Ohne dieses
        # Gate würde ein beim Start bereits laufender Boxenbesuch (Auto steht
        # schon in der Box) sofort fälschlich als Reifen-/Tankstopp gemessen.
        if not self._armed:
            if (not on_pit) and self._ref_lap is not None:
                self._armed = True
                self._reset_visit()
                self._prev_on_pit = on_pit          # = False (auf Strecke)
                self._last_ontrack_pct = pct
                # Runden-Erfassung scharf stellen: die gerade laufende Runde ist
                # die Out-Lap (Box verlassen) -> als unsauber markieren.
                self._prev_ref_lap = self._ref_lap
                self._lap_had_pit = True
                self._last_event = "Bereit — Kalibrierung scharf"
                return
            if not on_pit:
                self._last_ontrack_pct = pct
                self._last_event = "Warte auf Referenzrunde…"
            else:
                self._last_event = "Bitte Box verlassen & Referenzrunde fahren"
            self._prev_on_pit = on_pit
            return

        # --- Runden-Erfassung für die Referenz (schnellste saubere Runde) ---
        # Flanke "neue offizielle Rundenzeit" => vorige Runde ist abgeschlossen.
        if ref_lap is not None and ref_lap > 0.5 and ref_lap != self._prev_ref_lap:
            if self._prev_ref_lap is not None:      # erste Flanke nach Arming überspringen
                if not self._lap_had_pit:           # nur grüne Runden zählen
                    if self._best_clean_lap is None or ref_lap < self._best_clean_lap:
                        self._best_clean_lap = round(float(ref_lap), 3)
                        self._recompute_pit_loss()
                        self._maybe_complete()
            self._prev_ref_lap = ref_lap
            self._lap_had_pit = False               # neue Runde startet sauber
        # Boxen-Kontakt der LAUFENDEN Runde merken (In-/Out-/Durchfahrtsrunde)
        if on_pit:
            self._lap_had_pit = True

        # Boxeneinfahrt (Flanke on-track -> pit)
        if on_pit and not self._prev_on_pit:
            self._entry_time = time
            self._entry_pct = self._last_ontrack_pct if self._last_ontrack_pct is not None else pct
            self._svc_start = None
            self._svc_end = None
            self._svc_active = False
            self._sv_flags = 0
            self._fuel_samples = []

        # Während des Boxenbesuchs: Service-Fenster + Tankverlauf erfassen.
        # Deterministisch über PitstopActive; Fallback = Stillstand (Speed).
        if on_pit:
            if pitstop_active is not None:
                active = bool(pitstop_active)
            else:
                active = (speed is not None and speed < _STOP_SPEED)
            if active:
                if self._svc_start is None:
                    self._svc_start = time
                self._svc_end = time
                if pit_sv_flags is not None:
                    self._sv_flags |= int(pit_sv_flags)
                if fuel is not None:
                    self._fuel_samples.append((time, fuel))
                # "echter Service": deterministisch sofort, per Fallback erst
                # nach der Mindest-Standzeit.
                if pitstop_active is not None or (time - self._svc_start) >= _MIN_STATIONARY:
                    self._svc_active = True

        # Boxenausfahrt (Flanke pit -> on-track): Besuch auswerten
        if (not on_pit) and self._prev_on_pit:
            self._finalize_visit(exit_time=time, exit_pct=pct)

        # On-track-Position für die nächste Einfahrt merken
        if not on_pit:
            self._last_ontrack_pct = pct

        self._prev_on_pit = on_pit

    def _finalize_visit(self, *, exit_time, exit_pct):
        if self._entry_time is None:
            return

        # Deterministische Boxen-Ein-/Ausfahrt-Marken bei JEDEM Besuch festhalten
        # (ersetzen die All-Cars-Heuristik im circle_builder).
        if self._entry_pct is not None:
            self._pit_entry_pct = round(self._entry_pct % 1.0, 5)
        self._pit_exit_pct = round(exit_pct % 1.0, 5)

        if not self._svc_active:
            # --- Boxen-DURCHFAHRT -> Pit-Lane-Verlust ---
            # Rohdaten (Transit + Streckenanteil) festhalten und gegen die
            # schnellste SAUBERE Runde rechnen. Liegt noch keine saubere Runde
            # vor, wird der Verlust später automatisch nachgerechnet (feed()).
            if self._entry_pct is not None:
                transit = exit_time - self._entry_time
                span = ((exit_pct - self._entry_pct) % 1 + 1) % 1
                if span > 0.0:
                    self._dt_raw = (transit, span)
                    self._recompute_pit_loss()
                    if self.pit_loss is not None:
                        self._last_event = f"Durchfahrt gemessen: Verlust {self.pit_loss:.1f}s"
                    elif self._best_clean_lap is None:
                        self._last_event = "Durchfahrt erfasst — fahre eine saubere Referenzrunde"
                    else:
                        self._last_event = "Durchfahrt unplausibel — bitte wiederholen"
        else:
            # --- STOPP mit Service: tanken oder Reifen? ---
            svc_dur = (self._svc_end - self._svc_start) \
                if (self._svc_start is not None and self._svc_end is not None) else 0.0

            fuels = [f for _, f in self._fuel_samples if f is not None]
            fuel_delta = (max(fuels) - min(fuels)) if len(fuels) >= 2 else 0.0

            # WAS wurde gemacht? Deterministisch aus PitSvFlags, sonst Heuristik.
            if self._sv_flags:
                tire_count = bin(self._sv_flags & _PITSV_TIRE_MASK).count("1")
                is_fuel = bool(self._sv_flags & _PITSV_FUEL) or fuel_delta >= _MIN_REFUEL_L
            else:
                # Fallback ohne Blackbox-Info: Reifenanzahl unbekannt -> als 4
                # annehmen; Klassifizierung über Sprit-Schwellen.
                is_fuel = fuel_delta >= _MIN_REFUEL_L
                tire_count = 0 if fuel_delta > _MAX_TIRE_FLAT_L else 4

            if tire_count > 0:
                self._tires_last = tire_count

            if is_fuel:
                rate = self._fuel_rate_from_samples()
                if rate and 0.3 < rate < 20.0:
                    self.fuel_rate = round(rate, 3)
                    extra = " (+Reifen, Reifenzeit separat messen)" if tire_count > 0 else ""
                    self._last_event = f"Tankrate gemessen: {self.fuel_rate:.2f} L/s{extra}"
                else:
                    self._last_event = "Tankrate unplausibel — bitte wiederholen"
            elif tire_count > 0:
                # Reifen-Stopp ohne Tanken -> Service-Dauer auf 4 Reifen normieren.
                if 2.0 < svc_dur < 90.0:
                    self.tire_time = round(svc_dur * 4.0 / tire_count, 2)
                    self._last_event = (
                        f"Reifen ({tire_count}) {svc_dur:.1f}s → 4er-Zeit "
                        f"{self.tire_time:.1f}s"
                    )
                else:
                    self._last_event = "Reifenzeit unplausibel — bitte wiederholen"
            else:
                self._last_event = "Stopp ohne erkennbaren Service — Schritt wiederholen"

        # Bei Komplettierung automatisch speichern
        self._maybe_complete()

        self._reset_visit()

    def _recompute_pit_loss(self):
        """Pit-Verlust = Pitlane-Transit − (Streckenanteil × schnellste saubere
        Runde). Wird neu berechnet, wenn eine Durchfahrt gemessen wurde UND eine
        (schnellere) saubere Referenzrunde vorliegt."""
        if self._dt_raw is None or self._best_clean_lap is None:
            return
        transit, span = self._dt_raw
        loss = transit - span * self._best_clean_lap
        if 0.0 < loss < 120.0:
            self.pit_loss = round(loss, 2)

    def _maybe_complete(self):
        if self.active and self._key and self.is_complete():
            self._save_partial(self._key)
            self._last_event = "Kalibrierung komplett & gespeichert ✓"

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
            "armed":    self._armed,
            # Angezeigte Referenz = schnellste SAUBERE Runde (stabil, das Maß für
            # den Pit-Verlust). None, solange noch keine grüne Runde vorliegt.
            "ref_lap":  self._best_clean_lap,
            "pit_loss": self.pit_loss,
            "fuel_rate": self.fuel_rate,
            "tire_time": self.tire_time,           # auf 4 Reifen normiert
            "tires_last": self._tires_last,        # Reifenanzahl des letzten Stopps
            "pit_entry_pct": self._pit_entry_pct,
            "pit_exit_pct":  self._pit_exit_pct,
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
        if self._pit_entry_pct is not None:
            entry["pit_entry_pct"] = self._pit_entry_pct
        if self._pit_exit_pct is not None:
            entry["pit_exit_pct"] = self._pit_exit_pct
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
