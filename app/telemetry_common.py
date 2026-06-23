import logging

logger = logging.getLogger(__name__)

_printed_driver_debug = False


def normalize_tyre_label(comp) -> str | None:
    """Mappt einen Tyre-Compound-Wert -> 'Dry'/'Wet'/None.

    Versteht: 0/1 (int), 'd'/'w'/'dry'/'wet' (str).
    Wird von RelativeBuilder und StandingsBuilder gemeinsam genutzt.
    """
    if comp is None:
        return None

    try:
        s = str(comp).strip().lower()
    except Exception:
        s = ""

    if s in ("dry", "d"):
        return "Dry"
    if s in ("wet", "w"):
        return "Wet"

    try:
        comp_int = int(comp)
    except (TypeError, ValueError):
        return None

    if comp_int == 0:
        return "Dry"
    if comp_int == 1:
        return "Wet"
    return None


def get_driver_list(ir):
    """Holt DriverInfo['Drivers'] direkt aus dem Var-Buffer.

    Wird von Relative- und Standings-Builder genutzt.
    Liefert (drivers, my_car_idx).
    """
    global _printed_driver_debug

    try:
        driver_info = ir["DriverInfo"]
    except KeyError:
        if not _printed_driver_debug:
            logger.warning("DriverInfo nicht im Var-Buffer gefunden.")
            _printed_driver_debug = True
        return [], None

    try:
        drivers = driver_info["Drivers"]
    except Exception as e:
        if not _printed_driver_debug:
            logger.warning("Konnte DriverInfo['Drivers'] nicht lesen: %s", e)
            _printed_driver_debug = True
        return [], None

    if not isinstance(drivers, list):
        try:
            drivers = list(drivers)
        except Exception as e:
            if not _printed_driver_debug:
                logger.warning("Drivers ist kein list-artiges Objekt: %s", e)
                _printed_driver_debug = True
            return [], None

    # "mein" CarIdx
    my_idx = None
    try:
        if isinstance(driver_info, dict):
            my_idx = driver_info.get("DriverCarIdx", None)
        else:
            my_idx = getattr(driver_info, "DriverCarIdx", None)
    except Exception:
        my_idx = None

    if not _printed_driver_debug:
        _printed_driver_debug = True
        logger.debug("Driver-List initial: %d Eintraege", len(drivers))
        for i, d in enumerate(drivers[:5]):
            if isinstance(d, dict):
                name = d.get("UserName") or d.get("AbbrevName") or d.get("Initials") or "Unknown"
                car_idx = d.get("CarIdx", None)
            else:
                name = getattr(d, "UserName", "Unknown")
                car_idx = getattr(d, "CarIdx", None)
            logger.debug("  [%d] idx=%s name=%r", i, car_idx, name)

    return drivers, my_idx
