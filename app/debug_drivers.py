import time
import irsdk

def main():
    ir = irsdk.IRSDK()
    ir.startup()

    print("Warte auf iRacing-Verbindung...")
    while not ir.is_connected:
        time.sleep(1)
        ir.startup()
        print("  noch nicht verbunden...")

    print("Mit iRacing verbunden!")
    time.sleep(1)

    # Neueste Werte holen
    ir.freeze_var_buffer_latest()

    # 1) Versuchen wir zuerst, direkt über den Variablen-Buffer zu gehen:
    try:
        driver_info = ir["DriverInfo"]
        print("DriverInfo-Typ:", type(driver_info))
    except KeyError as e:
        print("DriverInfo nicht gefunden:", e)
        ir.shutdown()
        return

    try:
        drivers = driver_info["Drivers"]
    except Exception as e:
        print("Konnte DriverInfo['Drivers'] nicht lesen:", e)
        ir.shutdown()
        return

    # Wenn das kein list ist, versuchen wir, es in eine Liste umzuwandeln
    if not isinstance(drivers, list):
        try:
            drivers = list(drivers)
        except Exception as e:
            print("Drivers ist kein list-artiges Objekt:", e)
            ir.shutdown()
            return

    print(f"Anzahl Drivers: {len(drivers)}")
    if not drivers:
        print("KEINE Drivers gefunden – dann kann das Relative auch nichts anzeigen.")
    else:
        print("Erste paar Fahrer aus DriverInfo['Drivers']:")
        for i, d in enumerate(drivers[:10]):
            if isinstance(d, dict):
                name = d.get("UserName") or d.get("AbbrevName") or d.get("Initials") or "Unknown"
                car_idx = d.get("CarIdx", None)
            else:
                # Fallback falls das kein dict ist
                name = getattr(d, "UserName", "Unknown")
                car_idx = getattr(d, "CarIdx", None)

            print(f"  [{i}] idx={car_idx} name={name!r} | raw={d!r}")

    ir.shutdown()


if __name__ == "__main__":
    main()
