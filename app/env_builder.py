import math


class WindBuilder:
    """Wind relativ zum Fahrzeug."""

    def __init__(self, ir):
        self.ir = ir

    def build(self):
            data = {
                "wind_rel": None,
                "wind_speed": None,
            }

            try:
                wind_dir = self.ir["WindDir"]      # Richtung, aus der der Wind kommt (rad)
                wind_vel = self.ir["WindVel"]      # m/s
                yaw_north = self.ir["YawNorth"]    # Fahrzeugausrichtung relativ Norden (rad)
            except KeyError:
                return data
            except Exception:
                return data

            try:
                wind_dir = float(wind_dir)
                wind_vel = float(wind_vel)
                yaw_north = float(yaw_north)
            except Exception:
                return data

            # relativer Winkel: Windrichtung - Fahrzeugausrichtung
            rel = wind_dir - yaw_north
            while rel > math.pi:
                rel -= 2 * math.pi
            while rel < -math.pi:
                rel += 2 * math.pi

            rel_deg = rel * 180.0 / math.pi
            speed_kmh = wind_vel * 3.6

            data["wind_rel"] = rel_deg
            data["wind_speed"] = speed_kmh

            return data


class EnvBuilder:
    """
    Air-/Track-Temperatur + Streckennässe.

    Schickt:
      - air_temp (°C)
      - track_temp (°C)
      - track_wetness       -> grobe "0..100"-Skala (fallback)
      - track_wetness_idx   -> 1..7 aus iRacing
      - track_wet_label     -> "Dry", "Mostly Dry", ...
      - track_wet_bucket    -> "dry", "mostly_dry", ...
      - track_wet_color     -> Hex-Farbe für das aktive Kästchen
    """

    def __init__(self, ir):
        self.ir = ir

    def build(self):
        data = {
            "air_temp": None,
            "track_temp": None,
            "track_wetness": None,        # numerisch 0..~100 (nur falls du es irgendwo brauchst)
            "track_wetness_idx": None,    # 1..7 laut iRacing
            "track_wet_label": None,      # Text fürs Overlay
            "track_wet_bucket": None,     # dry / mostly_dry / slightly_wet / ...
            "track_wet_color": None,      # z.B. "#22c55e"
        }

        ir = self.ir

        # --- Temperaturen ---
        try:
            air = ir["AirTemp"]
        except KeyError:
            air = None

        try:
            track = ir["TrackTempCrew"]
        except KeyError:
            try:
                track = ir["TrackTemp"]
            except KeyError:
                track = None

        if air is not None:
            try:
                data["air_temp"] = float(air)
            except Exception:
                data["air_temp"] = air

        if track is not None:
            try:
                data["track_temp"] = float(track)
            except Exception:
                data["track_temp"] = track

        # --- Streckennässe ---
        track_wet_pct = None    # TrackWetPct (falls iRacing das irgendwann sinnvoll füllt)
        track_wet_idx = None    # TrackWetness (enum 1..7)

        # 1) TrackWetPct
        try:
            v = ir["TrackWetPct"]
        except KeyError:
            v = None

        if v is not None:
            try:
                w = float(v)
                # 0..1.0 => Prozent
                if 0.0 <= w <= 1.5:
                    track_wet_pct = w * 100.0
                else:
                    track_wet_pct = w
            except Exception:
                track_wet_pct = None

        # 2) TrackWetness (Index 1..7)
        try:
            idx = ir["TrackWetness"]
        except KeyError:
            idx = None

        if idx is not None:
            try:
                track_wet_idx = int(idx)
            except Exception:
                track_wet_idx = None

        # numerische 0..100-Skala für "track_wetness" (nur noch Fallback / Debug)
        wet_numeric = None
        if track_wet_pct is not None:
            wet_numeric = max(0.0, min(track_wet_pct, 100.0))
        elif track_wet_idx is not None:
            # grobe Mapping der 7 Index-Stufen auf eine 0..100 Skala
            # laut deinen Dumps: 1→10, 2→20, ..., 7→70
            mapping = {
                1: 10.0,  # Dry
                2: 20.0,  # Mostly Dry
                3: 30.0,  # Very lightly wet
                4: 40.0,  # Lightly wet
                5: 50.0,  # Moderately wet
                6: 60.0,  # Very wet
                7: 70.0,  # Extreme wet
            }
            wet_numeric = mapping.get(track_wet_idx, None)

        data["track_wetness"] = wet_numeric
        data["track_wetness_idx"] = track_wet_idx

        # --- Label + Bucket + Farbe für das Overlay (offizielle 7 Stufen) ---
        label = None
        bucket = None
        color = None

        if track_wet_idx is not None:
            # Dein offizielles Mapping:
            # 1: Dry
            # 2: Mostly Dry
            # 3: Very lightly wet
            # 4: Lightly wet
            # 5: Moderately wet
            # 6: Very wet
            # 7: Extreme wet
            if track_wet_idx <= 1:
                label = "Dry"
                bucket = "dry"
                color = "#22c55e"
            elif track_wet_idx == 2:
                label = "Mostly Dry"
                bucket = "mostly_dry"
                color = "#4ade80"
            elif track_wet_idx == 3:
                label = "Very lightly wet"
                bucket = "slightly_wet"
                color = "#a3e635"
            elif track_wet_idx == 4:
                label = "Lightly wet"
                bucket = "moderately_wet"
                color = "#facc15"
            elif track_wet_idx == 5:
                label = "Moderately wet"
                bucket = "wet"
                color = "#0ea5e9"
            elif track_wet_idx == 6:
                label = "Very wet"
                bucket = "heavy_wet"
                color = "#6366f1"
            else:  # 7 oder mehr
                label = "Extreme wet"
                bucket = "very_wet"
                color = "#4f46e5"

        # Fallback nur, falls TrackWetness-Index fehlt, aber Prozent vorhanden sind
        elif wet_numeric is not None:
            pct = wet_numeric
            # Wir legen die Grenzen so, dass sie grob zu 10/20/.../70 passen
            if pct < 15:
                label, bucket, color = "Dry", "dry", "#22c55e"
            elif pct < 25:
                label, bucket, color = "Mostly Dry", "mostly_dry", "#4ade80"
            elif pct < 35:
                label, bucket, color = "Very lightly wet", "slightly_wet", "#a3e635"
            elif pct < 45:
                label, bucket, color = "Lightly wet", "moderately_wet", "#facc15"
            elif pct < 55:
                label, bucket, color = "Moderately wet", "wet", "#0ea5e9"
            elif pct < 65:
                label, bucket, color = "Very wet", "heavy_wet", "#6366f1"
            else:
                label, bucket, color = "Extreme wet", "very_wet", "#4f46e5"

        data["track_wet_label"] = label
        data["track_wet_bucket"] = bucket
        data["track_wet_color"] = color

        return data
