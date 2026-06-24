"""Synthetischer State-Machine-Test für den PitCalibrator — ohne iRacing.

Einfach ausführbar: `python app/tests/test_pit_calibrator.py`
Deckt ab: Start-in-Box ignoriert, deterministische Pit-Marken, Durchfahrt +
Nachrechnen über die schnellste saubere Runde, Meatball/Reparatur sowie
Stopps ohne Blackbox-Telemetrie werden NICHT als Reifenwechsel gemessen,
Reifen-Hochrechnung (2 -> 4) und Tankrate.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pit_calibrator import PitCalibrator


def main():
    cal = PitCalibrator()
    cal._save_partial = lambda key: None  # nicht in pit_cache.json schreiben
    cal.start("TEST")

    LAP = 94.0  # saubere Rundenzeit (s)

    def feed(t, on_pit, speed, fuel, pct, ref, active=None, flags=None):
        cal.feed(time=t, on_pit=on_pit, speed=speed, fuel=fuel, pct=pct,
                 ref_lap=ref, pitstop_active=active, pit_sv_flags=flags)

    # Phase A: Start MIT Auto in der Box (stationär, KEIN Service aktiv)
    for t in [0.0, 0.5, 1.0, 1.5]:
        feed(t, True, 0.0, 50.0, 0.95, 95.0, active=False, flags=0)
    assert not cal._armed
    assert cal.tire_time is None, "BUG: Reifenzeit beim Start in der Box gemessen!"

    # Phase B: Box verlassen -> scharf
    feed(2.0, False, 30.0, 50.0, 0.98, 95.0, active=False, flags=0)
    assert cal._armed

    # Phase C: Out-Lap kreuzt S/F (dirty)
    feed(5.0, False, 40.0, 50.0, 0.01, 96.0, active=False, flags=0)
    assert cal._best_clean_lap is None

    # Phase E: Durchfahrt (Auto stand NIE) ohne vorhandene saubere Runde
    feed(150.0, False, 40.0, 49.0, 0.30, 96.0, active=False, flags=0)
    feed(150.5, True, 25.0, 49.0, 0.31, 96.0, active=False, flags=0)
    feed(160.0, True, 25.0, 49.0, 0.40, 96.0, active=False, flags=0)
    feed(170.5, False, 40.0, 49.0, 0.45, 96.0, active=False, flags=0)
    assert cal._dt_raw is not None
    assert cal.pit_loss is None
    assert abs(cal._pit_entry_pct - 0.30) < 1e-6
    assert abs(cal._pit_exit_pct - 0.45) < 1e-6

    # Durchfahrtsrunde abschliessen (dirty)
    feed(190.0, False, 50.0, 49.0, 0.02, 110.0, active=False, flags=0)

    # Phase D: saubere grüne Runde -> Nachrechnen
    for i, t in enumerate([200.0, 220.0, 240.0, 260.0]):
        feed(t, False, 50.0, 48.0, 0.2 * (i + 1), 110.0, active=False, flags=0)
    feed(280.0, False, 50.0, 48.0, 0.02, LAP, active=False, flags=0)
    assert cal._best_clean_lap == LAP
    assert abs(cal.pit_loss - 5.9) < 0.01, f"erwartet 5.9, ist {cal.pit_loss}"

    # Phase M: Meatball/Reparatur (Service, FastRepair-Bit, KEINE Reifen/Sprit)
    feed(290.0, False, 40.0, 48.0, 0.48, LAP, active=False, flags=0)
    feed(290.5, True, 10.0, 48.0, 0.49, LAP, active=False, flags=0)
    for t in [291.0, 295.0, 300.0]:
        feed(t, True, 0.0, 48.0, 0.495, LAP, active=True, flags=0x40)
    feed(300.2, False, 30.0, 48.0, 0.50, LAP, active=False, flags=0)
    assert cal.tire_time is None, "BUG: Meatball/Reparatur als Reifenwechsel gemessen!"

    # Phase N: Stopp OHNE Blackbox-Telemetrie (active/flags=None) -> nicht messbar
    feed(305.0, False, 40.0, 48.0, 0.51, LAP)
    feed(305.5, True, 10.0, 48.0, 0.52, LAP)
    for t in [306.0, 310.0]:
        feed(t, True, 0.0, 48.0, 0.53, LAP)
    feed(311.0, False, 30.0, 48.0, 0.54, LAP)
    assert cal.tire_time is None, "BUG: ohne Blackbox Reifenzeit gemessen!"

    # Phase F: Reifen-Stopp mit NUR 2 Reifen (0x03), kein Tanken -> 4er = 10*4/2 = 20s
    feed(320.0, False, 40.0, 48.0, 0.50, LAP, active=False, flags=0)
    feed(320.5, True, 10.0, 48.0, 0.51, LAP, active=False, flags=0)
    for t in [321.0, 325.0, 331.0]:
        feed(t, True, 0.0, 48.0, 0.52, LAP, active=True, flags=0x03)
    feed(332.0, False, 30.0, 48.0, 0.55, LAP, active=False, flags=0)
    assert cal._tires_last == 2
    assert abs(cal.tire_time - 20.0) < 0.01, f"2 Reifen 10s -> 4er 20s, ist {cal.tire_time}"

    # Phase G: Tankstopp (FuelFill-Bit 0x10) -> 4 L/s
    feed(340.0, False, 40.0, 5.0, 0.60, LAP, active=False, flags=0)
    feed(340.5, True, 10.0, 5.0, 0.61, LAP, active=False, flags=0)
    fuel = 5.0
    for t in [341.0, 342.0, 343.0, 344.0, 345.0, 346.0]:
        fuel += 4.0
        feed(t, True, 0.0, fuel, 0.62, LAP, active=True, flags=0x10)
    feed(347.0, False, 30.0, fuel, 0.65, LAP, active=False, flags=0)
    assert cal.fuel_rate is not None
    assert abs(cal.fuel_rate - 4.0) < 0.01

    assert cal.is_complete()
    print("test_pit_calibrator: ALLE ASSERTS OK")


def test_passive_marks():
    """Passives Marken-Lernen: Vorbeifahr-Flacker ignorieren, echte Durchquerung
    übernehmen, unveränderte Marken nicht erneut schreiben."""
    c = PitCalibrator()
    writes = []
    c._write_cache = lambda: writes.append(1)   # Datei-Write abfangen
    c._cache_loaded = True
    KEY = "testcar|testtrack|cfg|Race"

    def step(on_pit, pct):
        c.observe_marks(key=KEY, on_pit=on_pit, pct=pct)

    # 1) Flacker an der Grenzlinie (1 Frame on_pit) -> kein Update
    step(False, 0.90); step(False, 0.953); step(True, 0.9531)
    step(False, 0.9532); step(False, 0.96)
    assert KEY not in c._cache, f"Flacker gespeichert: {c._cache}"

    # 2) echte Durchquerung 0.90 -> 0.04 -> Update
    step(False, 0.88); step(False, 0.90)
    step(True, 0.92); step(True, 0.97); step(True, 0.99); step(True, 0.02)
    step(False, 0.04)
    m = c._cache.get(KEY, {})
    assert abs(m.get("pit_entry_pct", 0) - 0.90) < 1e-6, m
    assert abs(m.get("pit_exit_pct", 0) - 0.04) < 1e-6, m

    # 3) unveränderte Durchquerung -> kein erneuter Write
    nw = len(writes)
    step(False, 0.90); step(True, 0.95); step(False, 0.04)
    assert len(writes) == nw, "unveränderte Marken erneut geschrieben"
    print("test_passive_marks: ALLE ASSERTS OK")


def test_service_sequential():
    """Erkennung sequenziell (sum) vs parallel (max) aus einem kombinierten
    Stopp: Service-Fenster gegen refuel+tire bzw. max(refuel,tire)."""
    c = PitCalibrator()
    c._save_partial = lambda k: None
    c.active = True
    c._key = "K"
    c.fuel_rate = 2.5      # L/s
    c.tire_time = 18.0     # 4 Reifen
    # 60 L -> refuel 24 s, tire 18 s ; seq=42, par=24
    c._detect_sequential(svc_dur=42.0, fuel_delta=60.0, rate=2.5, tire_count=4)
    assert c.service_sequential is True, c.service_sequential
    c.service_sequential = None
    c._detect_sequential(svc_dur=24.0, fuel_delta=60.0, rate=2.5, tire_count=4)
    assert c.service_sequential is False, c.service_sequential
    print("test_service_sequential: ALLE ASSERTS OK")


if __name__ == "__main__":
    main()
    test_passive_marks()
    test_service_sequential()
