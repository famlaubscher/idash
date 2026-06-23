import os
import time
import json
import datetime

import irsdk


def safe_get(ir, name, default=None):
    try:
        v = ir[name]
    except KeyError:
        return default
    except Exception:
        return default
    return v


def main():
    ir = irsdk.IRSDK()

    print("Starte Track-Wetness-Dump… warte auf iRacing-Verbindung.")
    ir.startup()

    # Warten bis iRacing verbunden ist
    while not ir.is_connected:
        ir.startup()
        time.sleep(0.5)

    print("Mit iRacing verbunden, beginne Dump (nur Wetness).")

    # Output-Ordner
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps")
    os.makedirs(log_dir, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(log_dir, f"track_wetness_dump_{ts}.jsonl")

    print(f"Schreibe nach: {out_path}")
    print("Beende mit CTRL+C.")

    with open(out_path, "w", encoding="utf-8") as f:
        while True:
            if not ir.is_connected:
                print("iRacing nicht mehr verbunden, warte…")
                time.sleep(0.5)
                ir.startup()
                continue

            # aktuellen Buffer ziehen
            ir.freeze_var_buffer_latest()

            # Basis-Zeit aus Session
            sess_time = safe_get(ir, "SessionTime", None)

            # Wetness-Rohwerte
            track_wet_pct = safe_get(ir, "TrackWetPct", None)
            track_wetness_idx = safe_get(ir, "TrackWetness", None)

            # Optional: abgeleiteter "Prozentwert", damit du später leichter thresholds bauen kannst
            wet_normalized = None
            try:
                if track_wet_pct is not None:
                    w = float(track_wet_pct)
                    # 0..1 → Prozent
                    if 0.0 <= w <= 1.0:
                        wet_normalized = w * 100.0
                    # 0..100 → schon Prozent
                    elif 0.0 <= w <= 100.0:
                        wet_normalized = w
                elif track_wetness_idx is not None:
                    w = float(track_wetness_idx)
                    # 0..10 Index → *10
                    if 0.0 <= w <= 10.0:
                        wet_normalized = w * 10.0
            except Exception:
                wet_normalized = None

            row = {
                "wall_time": time.time(),
                "session_time": sess_time,
                "track_wet_pct": track_wet_pct,
                "track_wetness_idx": track_wetness_idx,
                "wet_normalized_pct": wet_normalized,
            }

            f.write(json.dumps(row))
            f.write("\n")
            f.flush()

            # kleines Delay, sonst wird die Datei unnötig fett
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDump beendet (CTRL+C).")
