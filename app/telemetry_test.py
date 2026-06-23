import time
import irsdk  # kommt aus dem Paket pyirsdk

def main():
    ir = irsdk.IRSDK()
    ir.startup()

    print("Warte auf iRacing-Sitzung ...")

    try:
        while True:
            if ir.is_initialized and ir.is_connected:
                # neueste Daten holen
                ir.freeze_var_buffer_latest()

                # Ein paar Basiswerte lesen
                speed_ms = ir['Speed'] or 0.0      # m/s
                gear     = ir['Gear'] or 0         # -1 = Rückwärts, 0 = Neutral, 1..N = Gänge
                rpm      = ir['RPM'] or 0.0
                fuel     = ir['FuelLevel'] or 0.0  # Liter

                speed_kmh = speed_ms * 3.6

                print(
                    f"Speed: {speed_kmh:6.1f} km/h | "
                    f"Gear: {gear:+d} | "
                    f"RPM: {rpm:6.0f} | "
                    f"Fuel: {fuel:5.1f} L",
                    end="\r",
                    flush=True,
                )
            else:
                print("Nicht mit iRacing verbunden ...       ", end="\r", flush=True)

            time.sleep(0.05)  # ca. 20 Hz
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        ir.shutdown()

if __name__ == "__main__":
    main()
