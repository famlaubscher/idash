class SessionInfoBuilder:
    """Extrahiert Session-Metadaten fuer Standings/Relative/Strategy."""

    def __init__(self, ir):
        self.ir = ir

    def build(self):
        info = {
            "type": None,
            "time_left": None,
            "track_name": None,
            "series_name": None,
            "sof": None,
            "car_count": None,
            "incidents": None,   # NEU: wird unten befüllt
        }

        ir = self.ir

        # --- Weekend/Track/Series ---
        try:
            weekend = ir["WeekendInfo"]
        except KeyError:
            weekend = None

        if isinstance(weekend, dict):
            info["track_name"] = (
                weekend.get("TrackDisplayName")
                or weekend.get("TrackDisplayShortName")
                or weekend.get("TrackName")
            )
            info["series_name"] = weekend.get("SeriesName") or weekend.get("Category")
        else:
            try:
                info["track_name"] = (
                    getattr(weekend, "TrackDisplayName", None)
                    or getattr(weekend, "TrackDisplayShortName", None)
                    or getattr(weekend, "TrackName", None)
                )
                info["series_name"] = getattr(weekend, "SeriesName", None) or getattr(
                    weekend, "Category", None
                )
            except Exception:
                pass

        # --- SessionInfo / Sessions ---
        try:
            session_info = ir["SessionInfo"]
        except KeyError:
            session_info = None

        sessions = None
        if isinstance(session_info, dict):
            sessions = session_info.get("Sessions")
        else:
            try:
                sessions = getattr(session_info, "Sessions", None)
            except Exception:
                sessions = None

        try:
            current_sess_num = ir["SessionNum"]
        except KeyError:
            current_sess_num = None

        current = None
        if sessions and isinstance(sessions, list):
            if current_sess_num is not None and 0 <= current_sess_num < len(sessions):
                current = sessions[current_sess_num]
            else:
                current = sessions[0]

        # --- Typ / TimeLeft / SoF / CarCount aus aktueller Session ---
        if isinstance(current, dict):
            info["type"] = current.get("SessionType") or current.get("Name")

            t = (
                current.get("SessionTimeRemain")
                or current.get("SessionTimeRemainEx")
                or current.get("SessionTimeRemaining")
            )
            if t is not None:
                try:
                    info["time_left"] = float(t)
                except Exception:
                    pass

            sof = current.get("ResultsAverageStrengthOfField")
            if sof is not None:
                try:
                    info["sof"] = int(sof)
                except Exception:
                    pass

            num_cars = current.get("ResultsNumCars")
            if num_cars is not None:
                try:
                    info["car_count"] = int(num_cars)
                except Exception:
                    pass

        # --- TimeLeft Fallbacks ---
        if info["time_left"] is None:
            for name in ("SessionTimeRemainEx", "SessionTimeRemain", "SessionTimeRemaining"):
                try:
                    v = ir[name]
                except KeyError:
                    continue
                else:
                    try:
                        v = float(v)
                    except Exception:
                        continue
                    info["time_left"] = max(0.0, v)
                    break

        if info["time_left"] is None and isinstance(current, dict):
            laps_remain = (
                current.get("SessionLapsRemainEx")
                or current.get("SessionLapsRemain")
                or current.get("SessionLapsTotal")
            )

            try:
                laps_remain = float(laps_remain) if laps_remain is not None else None
            except Exception:
                laps_remain = None

            avg_lap = None
            for name in ("LapBestLapTime", "LapLastLapTime"):
                try:
                    v = ir[name]
                except KeyError:
                    continue
                try:
                    v = float(v)
                except Exception:
                    continue
                if v > 0.1:
                    avg_lap = v
                    break

            if laps_remain is not None and avg_lap:
                info["time_left"] = laps_remain * avg_lap

        # --- CarCount Fallback ---
        if info["car_count"] is None:
            try:
                car_positions = ir["CarIdxPosition"]
                if car_positions:
                    info["car_count"] = sum(1 for p in car_positions if p and p > 0)
            except KeyError:
                pass

        # ------------------------------------------------------------------
        # NEU: Incidents (für Relative-Overlay-Header "Inc:")
        # ------------------------------------------------------------------

        inc_val = None

        # 1) Direkt aus Telemetrie-Variablen
        for name in (
            "PlayerCarDriverIncidentCount",
            "PlayerCarTeamIncidentCount",
            "PlayerCarMyIncidentCount",
        ):
            try:
                v = ir[name]
            except KeyError:
                continue
            else:
                if v is not None:
                    try:
                        iv = int(v)
                        if iv >= 0:
                            inc_val = iv
                            break
                    except Exception:
                        continue

        # 2) Fallback: aus den ResultsPositions der aktuellen Session
        if inc_val is None and isinstance(current, dict):
            # meinen CarIdx bestimmen
            my_idx = None
            try:
                di = ir["DriverInfo"]
                if isinstance(di, dict):
                    my_idx = di.get("DriverCarIdx", None)
                else:
                    my_idx = getattr(di, "DriverCarIdx", None)
            except Exception:
                my_idx = None

            try:
                if my_idx is not None:
                    my_idx = int(my_idx)
            except Exception:
                my_idx = None

            if my_idx is not None:
                results = current.get("ResultsPositions") or []
                if isinstance(results, list):
                    for rp in results:
                        if not isinstance(rp, dict):
                            continue
                        try:
                            car_idx = int(rp.get("CarIdx", -1))
                        except Exception:
                            continue
                        if car_idx != my_idx:
                            continue

                        # bekannte Incident-Felder durchprobieren
                        for field in (
                            "CurDriverIncidentCount",
                            "CurTeamIncidentCount",
                            "CurIncidentCount",
                            "Incidents",
                        ):
                            val = rp.get(field)
                            if val is None:
                                continue
                            try:
                                iv = int(val)
                                if iv >= 0:
                                    inc_val = iv
                                    break
                            except Exception:
                                continue
                        break  # eigenen Eintrag gefunden, wir können abbrechen

        info["incidents"] = inc_val

        return info
