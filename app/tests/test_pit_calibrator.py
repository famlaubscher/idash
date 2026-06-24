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


if __name__ == "__main__":
    main()
