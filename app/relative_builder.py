from telemetry_common import get_driver_list, normalize_tyre_label


class RelativeBuilder:
    """
    Baut die Relative-Liste um das eigene Auto herum.

    Gap-Vorzeichen-Konvention (durchgehend):
      positiv  = anderes Auto IST VOR mir
      negativ  = anderes Auto IST HINTER mir

    CarIdxEstTime = verstrichene Zeit auf der aktuellen Runde (elapsed, NICHT remaining).
    Auto weiter vorne hat MEHR elapsed time → höherer Wert.

    Primäre Quelle: CarIdxEstTime
      gap = other_est - my_est
      → other_est > my_est (Auto weiter vorne) → gap > 0 ✓

    Fallback: CarIdxLapDistPct * ref_lap
      dd = other_pct - my_pct,  wrappe auf [-0.5, +0.5]
      gap = dd * ref_lap
      → andere weiter auf der Strecke → gap > 0 ✓
    """

    WINDOW_SIZE = 5
    CENTER_INDEX = 2

    BRAND_KEYWORDS = [
        ("bmw",          "BMW"),
        ("porsche",      "Porsche"),
        ("ferrari",      "Ferrari"),
        ("mercedes",     "Mercedes"),
        ("amg",          "Mercedes"),
        ("audi",         "Audi"),
        ("mclaren",      "McLaren"),
        ("lamborghini",  "Lamborghini"),
        ("ford",         "Ford"),
        ("corvette",     "Chevrolet"),
        ("chevrolet",    "Chevrolet"),
        ("camaro",       "Chevrolet"),
        ("cadillac",     "Cadillac"),
        ("acura",        "Acura"),
        ("aston",        "Aston Martin"),
        ("amr",          "Aston Martin"),
        ("toyota",       "Toyota"),
        ("honda",        "Honda"),
        ("dallara",      "Dallara"),
        ("oreca",        "Oreca"),
        ("nissan",       "Nissan"),
        ("volkswagen",   "Volkswagen"),
        ("vw",           "Volkswagen"),
        ("hyundai",      "Hyundai"),
        ("kia",          "Kia"),
        ("mazda",        "Mazda"),
        ("buick",        "Buick"),
        ("pontiac",      "Pontiac"),
        ("renault",      "Renault"),
        ("lotus",        "Lotus"),
        ("ruf",          "Ruf"),
        ("williams",     "Williams"),
        ("radical",      "Radical"),
    ]

    def __init__(self, ir):
        self.ir = ir

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def build(self) -> list:
        ir = self.ir
        if not ir.is_connected:
            return []

        drivers, player_idx = get_driver_list(ir)
        if not drivers or player_idx is None or player_idx < 0:
            return []

        # Rohdaten aus iRacing
        est_raw        = self._arr(ir, "CarIdxEstTime")
        lap_pct_raw    = self._arr(ir, "CarIdxLapDistPct")
        lap_raw        = self._arr(ir, "CarIdxLap")
        cls_pos_raw    = self._arr(ir, "CarIdxClassPosition")
        pos_raw        = self._arr(ir, "CarIdxPosition")
        last_lap_raw   = self._arr(ir, "CarIdxLastLapTime")
        tire_raw       = self._arr(ir, "CarIdxTireCompound")
        player_tire    = self._scalar(ir, "PlayerTireCompound")

        if not lap_pct_raw:
            return []

        # Fahrer-Index (ohne Pace-Car)
        drivers_by_idx = {}
        for d in drivers:
            idx = d.get("CarIdx") if isinstance(d, dict) else getattr(d, "CarIdx", None)
            if idx is None:
                continue
            pace = (d.get("CarIsPaceCar") or d.get("IsPaceCar")) if isinstance(d, dict) \
                   else (getattr(d, "CarIsPaceCar", False) or getattr(d, "IsPaceCar", False))
            if not pace:
                drivers_by_idx[idx] = d

        # Autos einsammeln
        cars = []
        my_est = my_pct = my_last_lap = None

        for idx, drv in drivers_by_idx.items():
            pct = self._clamp(lap_pct_raw, idx, lo=-1.0, hi=2.0)
            if pct is None:
                continue

            est      = self._clamp(est_raw, idx, lo=0.0, hi=1e6)
            last_lap = self._clamp(last_lap_raw, idx, lo=0.1, hi=1e4)
            try:
                lap = int(lap_raw[idx]) if lap_raw and idx < len(lap_raw) else 0
            except Exception:
                lap = 0

            # Echte iRacing-Position: zuerst Klassen-Position, dann Gesamt
            cls_pos = self._int_pos(cls_pos_raw, idx)
            ovr_pos = self._int_pos(pos_raw, idx)

            tire = None
            try:
                if tire_raw and idx < len(tire_raw):
                    tire = tire_raw[idx]
            except Exception:
                pass
            if tire is None and idx == player_idx and player_tire is not None:
                tire = player_tire

            name       = self._drv(drv, "UserName", "AbbrevName", "Initials") or "Unknown"
            team_name  = self._drv(drv, "TeamName") or ""
            car_class  = self._drv(drv, "CarScreenNameShort", "CarClassShortName", "CarScreenName") or "-"
            car_path   = self._drv(drv, "CarPath") or "-"

            try:
                car_class_id = int(self._drv(drv, "CarClassID") or 0)
            except Exception:
                car_class_id = 0
            car_class_color = self._class_color(self._drv(drv, "CarClassColor"))
            car_number = self._drv(drv, "CarNumber", "CarNumberRaw") or ""
            country    = self._drv(drv, "ClubShortName", "ClubName") or ""
            inc_raw    = self._drv(drv, "CurDriverIncidentCount", "TeamIncidentCount")
            try:
                inc = int(inc_raw)
            except Exception:
                inc = 0

            brand = self._brand(car_path, car_class)

            if idx == player_idx:
                my_est      = est
                my_pct      = pct
                my_last_lap = last_lap

            cars.append({
                "car_idx":    idx,
                "race_pos":   0,   # computed below (echte Pos, sonst Track-Reihenfolge)
                "_cls_pos":   cls_pos,
                "_ovr_pos":   ovr_pos,
                "_lap":       lap,
                "name":       name,
                "car_class":  str(car_class),
                "car_class_id":    car_class_id,
                "car_class_color": car_class_color,
                "car_path":   str(car_path),
                "car_number": str(car_number),
                "country":    str(country),
                "car_brand":  brand,
                "team_name":  team_name,
                "est":        est,
                "pct":        pct,
                "last_lap":   last_lap,
                "tyre":       normalize_tyre_label(tire),
                "inc":        inc,
            })

        if not cars:
            return []

        # Klassenposition berechnen: innerhalb jeder Klasse nach Runden+Pct sortieren.
        # Robuster als CarIdxClassPosition, das oft 0 liefert.
        from collections import defaultdict
        cls_cars = defaultdict(list)
        for c in cars:
            cls_cars[c["car_class_id"]].append(c)
        for grp in cls_cars.values():
            grp.sort(key=lambda c: (c["_lap"] or 0, c.get("pct") or 0.0), reverse=True)
            for rank, c in enumerate(grp, start=1):
                c["race_pos"] = rank

        # Referenzrundenzeit (meine letzte Runde, sonst Schnitt aller)
        ref_lap = my_last_lap
        if not ref_lap or ref_lap <= 0:
            valid = [c["last_lap"] for c in cars
                     if c.get("last_lap") and c["last_lap"] > 0
                     and c["car_idx"] != player_idx]
            ref_lap = sum(valid) / len(valid) if valid else None

        # Gap berechnen: positiv = anderes Auto VOR mir
        for c in cars:
            c["gap"] = self._calc_gap(c, my_est, my_pct, ref_lap, my_last_lap)

        player_car = next((c for c in cars if c["car_idx"] == player_idx), None)
        if player_car is None:
            return self._build_rows(cars[:self.WINDOW_SIZE], player_idx, 0)

        my_lap = player_car.get("_lap") or 0

        # Autos in "vor mir" / "hinter mir" aufteilen.
        # Split bei 0 (nicht ±0.05), sonst verschwindet ein Auto das direkt
        # neben dir fährt (|gap| < 0.05) aus beiden Listen → fehlt im Fenster.
        ahead  = [c for c in cars if c["car_idx"] != player_idx and c["gap"] is not None and c["gap"] >= 0]
        behind = [c for c in cars if c["car_idx"] != player_idx and c["gap"] is not None and c["gap"] <  0]

        # Nächstes Auto zuerst:
        #   ahead:  kleinstes positives gap  → aufsteigend
        #   behind: kleinstes negatives gap (nächstes) → absteigend (weniger negativ zuerst)
        ahead.sort( key=lambda c: c["gap"])
        behind.sort(key=lambda c: c["gap"], reverse=True)

        slots = [None] * self.WINDOW_SIZE
        slots[self.CENTER_INDEX] = player_car
        for i, slot in enumerate(range(self.CENTER_INDEX - 1, -1, -1)):
            if i < len(ahead):
                slots[slot] = ahead[i]
        for i, slot in enumerate(range(self.CENTER_INDEX + 1, self.WINDOW_SIZE)):
            if i < len(behind):
                slots[slot] = behind[i]

        return self._build_rows(slots, player_idx, my_lap)

    # ------------------------------------------------------------------
    # Interne Hilfsmethoden
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_gap(car: dict, my_est, my_pct, ref_lap, my_last_lap) -> float | None:
        """
        Gibt den Gap zurück (positiv = Auto VOR mir, negativ = hinter mir).

        Multiclass-tauglich: Die Strecken-Distanz (LapDistPct-Differenz) wird mit
        der RUNDENZEIT DES JEWEILIGEN AUTOS in Sekunden umgerechnet — nicht mit
        meiner. So bekommt ein schnelles LMP2 vor mir einen realistischen (kleineren)
        Zeit-Gap statt eines aufgeblähten Werts auf Basis meiner GT3-Pace.

        Primär: dd = wrap(other_pct - my_pct) ∈ [-0.5, 0.5] Runden
                gap = dd * lap_time_des_autos
          - dd > 0 (Auto weiter auf der Strecke = vor mir) → positiv ✓
          - dd < 0 (Auto hinter mir)                       → negativ ✓

        Fallback: CarIdxEstTime-Differenz (mit Lap-Wrap), falls kein pct vorhanden.
        """
        est = car.get("est")
        pct = car.get("pct")
        car_lap = car.get("last_lap")  # eigene letzte Rundenzeit dieses Autos

        # Primary: track-position diff × per-car lap time (multiclass-aware)
        if my_pct is not None and pct is not None:
            dd = pct - my_pct
            if dd >  0.5: dd -= 1.0
            elif dd < -0.5: dd += 1.0
            # Rundenzeit des Autos selbst; Fallbacks: meine letzte Runde, dann ref_lap
            lap_t = car_lap or my_last_lap or ref_lap
            if lap_t:
                return dd * lap_t

        # Fallback: CarIdxEstTime diff (single reference pace) mit Lap-Wrap
        if my_est is not None and est is not None:
            diff = est - my_est
            if ref_lap and abs(diff) > ref_lap * 0.5:
                diff += ref_lap if diff < 0 else -ref_lap
            return diff

        return None

    @staticmethod
    def _arr(ir, key: str) -> list:
        try:
            return ir[key] or []
        except KeyError:
            return []

    @staticmethod
    def _scalar(ir, key: str):
        try:
            return ir[key]
        except KeyError:
            return None

    @staticmethod
    def _int_pos(arr: list, idx: int):
        """Positions-Feld lesen; iRacing liefert 0 wenn (noch) keine Position."""
        try:
            v = int(arr[idx])
            return v if v > 0 else None
        except Exception:
            return None

    @staticmethod
    def _clamp(arr: list, idx: int, lo: float, hi: float):
        try:
            v = float(arr[idx])
            if v < lo or v > hi:
                return None
            return v
        except Exception:
            return None

    @staticmethod
    def _drv(d, *keys, default=None):
        for k in keys:
            v = d.get(k) if isinstance(d, dict) else getattr(d, k, None)
            if v not in (None, "", 0):
                return v
        return default

    @staticmethod
    def _class_color(raw_color) -> str:
        """iRacing CarClassColor (Dezimal-Int oder '0xRRGGBB') -> '#RRGGBB'."""
        if raw_color is None:
            return "#888888"
        try:
            s = str(raw_color).strip()
            color_int = int(s, 16) if s.lower().startswith("0x") else int(s)
            if color_int > 0:
                return f"#{color_int:06X}"
        except Exception:
            pass
        return "#888888"

    def _brand(self, car_path: str, car_class: str) -> str:
        src = f"{car_path} {car_class}".lower()
        for kw, brand in self.BRAND_KEYWORDS:
            if kw in src:
                return brand
        return str(car_class)

    # ------------------------------------------------------------------
    # Row-Builder
    # ------------------------------------------------------------------

    def _build_rows(self, slots: list, player_idx: int, my_lap: int = 0) -> list:
        rows = []
        prev_gap = None

        for c in slots:
            if c is None:
                rows.append({
                    "car_number": "", "pos": "", "race_pos": "", "name": "",
                    "gap": "--.-", "delta": "--.-",
                    "car_class": "", "car_class_full": "", "car_path": "",
                    "car_class_id": 0, "car_class_color": "#888888",
                    "country": "", "car_brand": "", "tyre": None, "inc": 0, "me": False, "lap_diff": 0,
                })
                prev_gap = None
                continue

            is_me = (c["car_idx"] == player_idx)
            g = 0.0 if is_me else c.get("gap")

            if is_me:
                gap_str = "0.0"
            elif g is None:
                gap_str = "--.-"
            else:
                gap_str = f"{g:+.1f}"

            delta_str = "--.-"
            if not is_me and prev_gap is not None and g is not None:
                delta_str = f"{g - prev_gap:+.1f}"

            rows.append({
                "car_number":    c.get("car_number", ""),
                "pos":           c["race_pos"],
                "race_pos":      c["race_pos"],
                "name":          c["name"],
                "gap":           gap_str,
                "delta":         delta_str,
                "car_class":     c.get("car_brand") or c.get("car_class", ""),
                "car_class_full": c.get("car_class", ""),
                "car_class_id":   c.get("car_class_id", 0),
                "car_class_color": c.get("car_class_color", "#888888"),
                "car_path":      c.get("car_path", ""),
                "country":       c.get("country", ""),
                "car_brand":     c.get("car_brand", ""),
                "team_name":     c.get("team_name", ""),
                "tyre":          c.get("tyre"),
                "inc":           c.get("inc", 0),
                "me":            is_me,
                "lap_diff":      (c.get("_lap") or 0) - my_lap,
            })
            prev_gap = g

        return rows
