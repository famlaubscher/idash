# standings_builder.py

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple, Optional

from telemetry_common import normalize_tyre_label


class StandingsBuilder:
    """
    Baut die Standings aus den offiziellen iRacing-Resultdaten
    (SessionInfo.Sessions[].ResultsPositions).

    Vorteile:
      - Reihenfolge identisch zu iRacing, sobald offizielle Resultdaten existieren
      - Fahrer, die den Server bereits verlassen haben, bleiben mit ihrer Zeit
      - Fahrer ohne gültige Runde werden trotzdem angezeigt
      - Wenn noch niemand eine Zeit gefahren ist, wird die Liste aus DriverInfo aufgebaut
      - 'me' wird über DriverCarIdx markiert
      - Tyre-Infos werden über CarIdxTireCompound/PlayerTireCompound befüllt (Dry/Wet)
    """

    def __init__(self, ir):
        self.ir = ir

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------

    def _get_driver_map(self) -> Tuple[Dict[int, Dict[str, Any]], Optional[int]]:
        """
        Liefert:
          - dict[CarIdx] -> Driver-Info (Name, CarNumber, Lic, IRating, ...)
          - meine CarIdx (DriverCarIdx)
        """
        driver_map: Dict[int, Dict[str, Any]] = {}
        my_car_idx: Optional[int] = None

        try:
            di = self.ir["DriverInfo"]
        except Exception:
            return driver_map, None

        # DriverCarIdx
        try:
            my_car_idx = int(di.get("DriverCarIdx", -1))
            if my_car_idx < 0:
                my_car_idx = None
        except Exception:
            my_car_idx = None

        drivers = di.get("Drivers") or []
        if not isinstance(drivers, list):
            return driver_map, my_car_idx

        for d in drivers:
            if not isinstance(d, dict):
                continue

            try:
                car_idx = int(d.get("CarIdx", -1))
            except Exception:
                continue
            if car_idx < 0:
                continue

            # Pacecar kann raus
            if d.get("CarIsPaceCar"):
                continue

            name = d.get("UserName") or d.get("AbbrevName") or ""
            team_name = d.get("TeamName") or ""
            team_id   = d.get("TeamID") or 0
            car_number = (
                d.get("CarNumberRaw")
                or d.get("CarNumber")
                or ""
            )
            lic = d.get("LicString") or ""
            irating = d.get("IRating", None)

            car_class_id = d.get("CarClassID") or 0
            try:
                car_class_id = int(car_class_id)
            except Exception:
                car_class_id = 0

            car_class_name = (
                d.get("CarClassShortName")
                or d.get("CarClassName")
                or ""
            )

            # iRacing liefert CarClassColor als Dezimalzahl (z.B. 16776960)
            # oder manchmal als Hex-String "0xFFD700"
            raw_color = d.get("CarClassColor")
            car_class_color = "#888888"
            if raw_color is not None:
                try:
                    s = str(raw_color).strip()
                    if s.startswith("0x") or s.startswith("0X"):
                        color_int = int(s, 16)
                    else:
                        color_int = int(s)   # Dezimal-Integer
                    if color_int > 0:
                        car_class_color = f"#{color_int:06X}"
                except Exception:
                    pass

            driver_map[car_idx] = {
                "car_idx": car_idx,
                "name": name,
                "team_name": team_name,
                "team_id": team_id,
                "car_number": car_number,
                "license": lic,
                "irating": irating,
                "car_class_id": car_class_id,
                "car_class_name": car_class_name,
                "car_class_color": car_class_color,
            }

        return driver_map, my_car_idx

    def _get_sessions_tree(self) -> Dict[str, Any]:
        """
        Versucht, an die 'Sessions'-Struktur zu kommen – robust gegen
        unterschiedliche irsdk-Implementierungen.
        """
        data: Dict[str, Any] = {}

        # 1) Bevorzugt get_session_info(), falls vorhanden
        get_si = getattr(self.ir, "get_session_info", None)
        if callable(get_si):
            try:
                info = get_si()
                if isinstance(info, dict):
                    data = info
            except Exception:
                data = {}

        # 2) Fallback: direktes Auslesen von "SessionInfo"
        if not data:
            try:
                info = self.ir["SessionInfo"]
                if isinstance(info, dict):
                    data = info
            except Exception:
                data = {}

        # Manche Varianten haben die Sessions direkt, andere unter "SessionInfo"
        if "Sessions" in data:
            return data
        if "SessionInfo" in data and isinstance(data["SessionInfo"], dict):
            return data["SessionInfo"]

        return data

    def _get_results_session(self) -> Dict[str, Any]:
        """
        Wählt die passende Session aus (normalerweise die aktuelle SessionNum)
        und liefert deren Struktur inkl. ResultsPositions.
        """
        tree = self._get_sessions_tree()
        sessions = tree.get("Sessions") or []
        if not isinstance(sessions, list):
            return {}

        current_session_num: Optional[int] = None
        try:
            current_session_num = int(self.ir["SessionNum"])
        except Exception:
            current_session_num = None

        chosen: Optional[Dict[str, Any]] = None

        # 1) Versuchen, die Session mit der aktuellen SessionNum zu finden
        if current_session_num is not None:
            for sess in sessions:
                try:
                    if int(sess.get("SessionNum", -1)) == current_session_num:
                        chosen = sess
                        break
                except Exception:
                    continue

        # 2) Fallback: letzte Session, die überhaupt ResultsPositions hat
        if chosen is None:
            for sess in reversed(sessions):
                if sess.get("ResultsPositions"):
                    chosen = sess
                    break

        return chosen or {}

    @staticmethod
    def _format_gap_val(val: Any) -> str:
        """
        Konvertiert TimeBehindLeader / TimeBehindNext in einen hübschen
        String wie '0.0', '1.2', '--.-'.
        """
        if val is None:
            return "--.-"
        try:
            f = float(val)
        except (TypeError, ValueError):
            # wenn iRacing irgendeinen Text liefert, einfach so anzeigen
            return str(val)

        # Viele Felder sind 0.0 für Leader bzw. -1, wenn nicht relevant.
        if math.isclose(f, 0.0, abs_tol=1e-4):
            return "0.0"
        if f < 0:
            return "--.-"

        return f"{f:.1f}"

    # -------------------- Tyre-Handling ---------------------------------

    def _get_tyre_map(self) -> Dict[int, str]:
        """
        Liest CarIdxTireCompound + PlayerTireCompound aus dem Var-Buffer und baut
        ein dict[car_idx] -> 'Dry'/'Wet'/...
        Logik identisch zur Tyre-Logik im RelativeBuilder.
        """
        tyre_map: Dict[int, str] = {}

        # Hauptquelle: CarIdxTireCompound (Array)
        try:
            comp_arr = self.ir["CarIdxTireCompound"]
        except Exception:
            comp_arr = None

        # Fallback für eigenen Wagen: PlayerTireCompound
        try:
            player_comp = self.ir["PlayerTireCompound"]
        except Exception:
            player_comp = None

        # my_car_idx holen (kann man aus DriverInfo ziehen)
        my_car_idx: Optional[int] = None
        try:
            di = self.ir["DriverInfo"]
            my_car_idx_raw = di.get("DriverCarIdx", -1)
            my_car_idx = int(my_car_idx_raw) if my_car_idx_raw is not None else None
            if my_car_idx is not None and my_car_idx < 0:
                my_car_idx = None
        except Exception:
            my_car_idx = None

        seq = None
        if comp_arr is not None:
            try:
                seq = list(comp_arr)
            except Exception:
                seq = comp_arr

        if seq is not None and isinstance(seq, (list, tuple)):
            for idx, val in enumerate(seq):
                label = normalize_tyre_label(val)
                if label:
                    tyre_map[idx] = label

        # Falls der eigene Wagen nichts im Array hat, aber PlayerTireCompound existiert
        if my_car_idx is not None and player_comp is not None:
            if my_car_idx not in tyre_map:
                label = normalize_tyre_label(player_comp)
                if label:
                    tyre_map[my_car_idx] = label

        return tyre_map

    # ------------------------------------------------------------------
    # Hauptfunktion
    # ------------------------------------------------------------------

    def build(self) -> List[Dict[str, Any]]:
        """
        Liefert eine Liste von Standings-Einträgen mit Feldern:
          - pos
          - car_idx
          - car_number
          - name
          - license
          - irating
          - gap_lead
          - gap_front
          - best_lap  (Sekunden, float)
          - last_lap  (Sekunden, float)
          - pos_gain  (falls vorhanden, sonst 0)
          - tyre      ('Dry'/'Wet'/...)
          - me        (bool)
        """
        if not getattr(self.ir, "is_connected", False):
            return []

        driver_map, my_car_idx = self._get_driver_map()
        tyre_map = self._get_tyre_map()

        # Offizielle Ergebnis-Session holen
        results_session = self._get_results_session()
        results = results_session.get("ResultsPositions") or []
        if not isinstance(results, list):
            results = []

        rows: List[Dict[str, Any]] = []
        seen_car_idxs = set()
        max_pos = 0

        # ------------------- 1) Offizielle ResultsPositions -------------------
        # wir merken uns Totalzeiten für evtl. Gap-Fallback
        times_for_gap: List[Optional[float]] = []

        for rp in results:
            if not isinstance(rp, dict):
                times_for_gap.append(None)
                continue

            try:
                car_idx = int(rp.get("CarIdx", -1))
            except Exception:
                times_for_gap.append(None)
                continue
            if car_idx < 0:
                times_for_gap.append(None)
                continue

            seen_car_idxs.add(car_idx)

            # Driver-Daten dazu holen (Name, Nummer, Lizenz, IR)
            d = driver_map.get(car_idx, {})
            name = d.get("name") or rp.get("UserName") or f"Car {car_idx}"
            car_number = d.get("car_number") or rp.get("CarNumber") or ""
            license_str = d.get("license") or ""
            irating = d.get("irating")

            # Position – iRacing liefert hier i.d.R. 1-basiert
            pos = rp.get("Position")
            try:
                pos = int(pos)
            except Exception:
                pos = None

            if isinstance(pos, int) and pos > max_pos:
                max_pos = pos

            # Best-/Lastlap in Sekunden
            best_lap = rp.get("FastestTime") or rp.get("FastestLapTime")
            last_lap = rp.get("LastTime") or rp.get("LastLapTime")

            # Gaps: TimeBehindLeader / TimeBehindNext
            gap_lead_raw = rp.get("TimeBehindLeader")
            gap_front_raw = rp.get("TimeBehindNext")

            gap_lead = self._format_gap_val(gap_lead_raw)
            gap_front = self._format_gap_val(gap_front_raw)

            # Gesamtzeit (für Fallback-Gap)
            total_time = None
            for key in ("Time", "TotalTime", "RaceTime"):
                val = rp.get(key)
                if val is None:
                    continue
                try:
                    total_time = float(val)
                    break
                except (TypeError, ValueError):
                    continue
            times_for_gap.append(total_time)

            # Positionsgewinn falls vorhanden
            pos_gain_raw = (
                rp.get("PositionGain")
                or rp.get("PosGain")
                or rp.get("PositionChange")
            )
            try:
                pos_gain = int(pos_gain_raw)
            except Exception:
                pos_gain = 0

            d_info = driver_map.get(car_idx, {})
            rows.append(
                {
                    "car_idx": car_idx,
                    "car_number": car_number,
                    "pos": pos,
                    "name": name,
                    "license": license_str,
                    "irating": irating,
                    "gap_lead": gap_lead,
                    "gap_front": gap_front,
                    "best_lap": best_lap,
                    "last_lap": last_lap,
                    "pos_gain": pos_gain,
                    "me": (my_car_idx is not None and car_idx == my_car_idx),
                    "total_time": total_time,
                    "tyre": tyre_map.get(car_idx, ""),
                    "team_name": d_info.get("team_name", ""),
                    "car_class_id": d_info.get("car_class_id", 0),
                    "car_class_name": d_info.get("car_class_name", ""),
                    "car_class_color": d_info.get("car_class_color", "#888888"),
                }
            )

        # ------------------- 1b) Gap-Fallback über Totalzeit -------------------
        if rows:
            any_gap_value = any(
                (r.get("gap_lead") not in (None, "--.-") or
                 r.get("gap_front") not in (None, "--.-"))
                for r in rows
            )

            if not any_gap_value:
                leader_time = times_for_gap[0] if times_for_gap else None
                for i, r in enumerate(rows):
                    t = r.get("total_time")
                    if leader_time is None or t is None:
                        continue

                    try:
                        dt_lead = float(t) - float(leader_time)
                    except Exception:
                        continue

                    if dt_lead <= 0.05:
                        r["gap_lead"] = "0.0"
                    elif 0.0 < dt_lead < 1e5:
                        r["gap_lead"] = f"{dt_lead:.1f}"
                    else:
                        r["gap_lead"] = "--.-"

                    if i > 0:
                        prev_t = rows[i - 1].get("total_time")
                        if prev_t is not None:
                            try:
                                dt_front = float(t) - float(prev_t)
                            except Exception:
                                dt_front = None

                            if dt_front is not None:
                                if dt_front <= 0.05:
                                    r["gap_front"] = "0.0"
                                elif 0.0 < dt_front < 1e5:
                                    r["gap_front"] = f"{dt_front:.1f}"
                                else:
                                    r["gap_front"] = "--.-"

        # ------------------- 2) Fallback für Fahrer ohne Results -------------
        next_pos = max_pos + 1 if max_pos > 0 else 1

        for car_idx in sorted(driver_map.keys()):
            if car_idx in seen_car_idxs:
                continue

            d = driver_map[car_idx]
            name = d.get("name") or f"Car {car_idx}"
            car_number = d.get("car_number") or ""
            license_str = d.get("license") or ""
            irating = d.get("irating")

            rows.append(
                {
                    "car_idx": car_idx,
                    "car_number": car_number,
                    "pos": next_pos,
                    "name": name,
                    "license": license_str,
                    "irating": irating,
                    "gap_lead": "--.-",
                    "gap_front": "--.-",
                    "best_lap": None,
                    "last_lap": None,
                    "pos_gain": 0,
                    "me": (my_car_idx is not None and car_idx == my_car_idx),
                    "total_time": None,
                    "tyre": tyre_map.get(car_idx, ""),
                    "team_name": d.get("team_name", ""),
                    "car_class_id": d.get("car_class_id", 0),
                    "car_class_name": d.get("car_class_name", ""),
                    "car_class_color": d.get("car_class_color", "#888888"),
                }
            )
            next_pos += 1

        if not rows:
            return []

        def _sort_key(r: Dict[str, Any]) -> int:
            p = r.get("pos")
            try:
                return int(p)
            except Exception:
                return 9999

        rows.sort(key=_sort_key)

        # SOF pro Klasse berechnen und jedem Row anhängen
        class_iratings: Dict[int, List[float]] = {}
        for r in rows:
            cid = r.get("car_class_id", 0)
            ir = r.get("irating")
            if ir is not None:
                try:
                    class_iratings.setdefault(cid, []).append(float(ir))
                except (TypeError, ValueError):
                    pass

        class_sof: Dict[int, int] = {}
        for cid, ratings in class_iratings.items():
            if ratings:
                class_sof[cid] = round(sum(ratings) / len(ratings))

        for r in rows:
            r["class_sof"] = class_sof.get(r.get("car_class_id", 0))

        return rows
