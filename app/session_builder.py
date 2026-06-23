class SessionInfoBuilder:
    """Extrahiert Session-Metadaten fuer Standings/Relative/Strategy."""

    def __init__(self, ir):
        self.ir = ir
        # nur einmal debuggen, damit das Log nicht explodiert
        self._sof_debug_printed = False

    def build(self):
        info = {
            "type": None,
            "time_left": None,
            "track_name": None,
            "series_name": None,
            "sof": None,
            "car_count": None,
        }

        ir = self.ir

        # ------------- Weekend / Track / Series -------------
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

        # ------------- SessionInfo / aktuelle Session -------------
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

            # ---------- SoF: direkt aus der Session, wenn vorhanden ----------
            sof = (
                current.get("ResultsAverageStrengthOfField")
                or current.get("ResultsAvgStrengthOfField")
                or current.get("StrengthOfField")
                or current.get("AvgStrengthOfField")
            )
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

            # Kleiner Debug-Dump, damit wir sehen, was iRacing hier wirklich liefert
            if not self._sof_debug_printed:
                self._sof_debug_printed = True
                try:
                    print("=== SessionInfoBuilder DEBUG ===")
                    print("  current_sess_num:", current_sess_num)
                    if isinstance(current, dict):
                        print("  Session keys:", list(current.keys()))
                        print("  ResultsAverageStrengthOfField:", current.get("ResultsAverageStrengthOfField"))
                        print("  ResultsAvgStrengthOfField:", current.get("ResultsAvgStrengthOfField"))
                        print("  StrengthOfField:", current.get("StrengthOfField"))
                        print("  AvgStrengthOfField:", current.get("AvgStrengthOfField"))
                    print("=== /SessionInfoBuilder DEBUG ===")
                except Exception:
                    pass

        # ------------- time_left Fallbacks -------------
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

        # ------------- car_count Fallback -------------
        if info["car_count"] is None:
            try:
                car_positions = ir["CarIdxPosition"]
                if car_positions:
                    info["car_count"] = sum(1 for p in car_positions if p and p > 0)
            except KeyError:
                pass

        # ------------- SoF Fallback: selber aus DriverInfo berechnen -------------
        if info["sof"] is None:
            try:
                di = ir["DriverInfo"]
            except KeyError:
                di = None

            ratings = []
            if isinstance(di, dict):
                drivers = di.get("Drivers") or []
                if isinstance(drivers, list):
                    for d in drivers:
                        if not isinstance(d, dict):
                            continue
                        # Pacecar raus
                        if d.get("CarIsPaceCar"):
                            continue
                        ir_val = d.get("IRating")
                        if ir_val is None:
                            continue
                        try:
                            ir_i = int(ir_val)
                        except Exception:
                            continue
                        if ir_i > 0:
                            ratings.append(ir_i)

            if ratings:
                # ganz simpler SoF: Mittelwert aller iRatings
                avg_ir = sum(ratings) / len(ratings)
                info["sof"] = int(round(avg_ir))

                if not self._sof_debug_printed:
                    self._sof_debug_printed = True
                    try:
                        print("=== SessionInfoBuilder SoF-Fallback ===")
                        print("  Fahrer mit IRating:", len(ratings))
                        print("  Min IRating:", min(ratings), "Max IRating:", max(ratings))
                        print("  Avg IRating (SoF):", info["sof"])
                        print("=== /SessionInfoBuilder SoF-Fallback ===")
                    except Exception:
                        pass

        return info
