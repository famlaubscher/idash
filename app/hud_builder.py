import json
import math
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overlay_config.json")

def _load_steer_scale() -> float:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return float(cfg.get("hud", {}).get("steer_scale", 2.4))
    except Exception:
        return 2.4


class HudBuilder:
    """Baut das HUD-Payload (Speed, Gear, Pedale, Lenkwinkel, Brake Bias)."""

    def __init__(self, ir):
        self.ir = ir
        self._max_abs_steer = 0.0
        self._steer_scale = _load_steer_scale()

    def build(self):
        ir = self.ir
        data = {}

        # --- Speed in km/h ---
        try:
            speed_ms = ir["Speed"]
            data["speed"] = float(speed_ms) * 3.6 if speed_ms is not None else 0.0
        except KeyError:
            data["speed"] = 0.0

        # --- Gang ---
        try:
            data["gear"] = int(ir["Gear"])
        except (KeyError, TypeError, ValueError):
            data["gear"] = 0

        # --- Throttle (0..1) ---
        throttle = None
        for name in ("Throttle", "ThrottleRaw"):
            try:
                v = ir[name]
            except KeyError:
                continue
            else:
                if v is not None:
                    throttle = float(v)
                    break

        if throttle is not None:
            # Falls 0..255 (Raw) → normalisieren
            if throttle > 1.01:
                throttle = max(0.0, min(throttle / 255.0, 1.0))
            else:
                throttle = max(0.0, min(throttle, 1.0))
        else:
            throttle = 0.0

        data["throttle"] = throttle

        # --- Brake (0..1) ---
        brake = None
        for name in ("Brake", "BrakeRaw"):
            try:
                v = ir[name]
            except KeyError:
                continue
            else:
                if v is not None:
                    brake = float(v)
                    break

        if brake is not None:
            if brake > 1.01:
                brake = max(0.0, min(brake / 255.0, 1.0))
            else:
                brake = max(0.0, min(brake, 1.0))
        else:
            brake = 0.0

        data["brake"] = brake

        # --- Steering (normiert -1..+1, plus Lenkrad-Winkel in Grad) ---
        angle = None
        try:
            # iRacing liefert Radiant
            angle = float(ir["SteeringWheelAngle"])
        except KeyError:
            angle = None

        try:
            max_lock_irsdk = float(ir["SteeringWheelAngleMax"])
        except KeyError:
            max_lock_irsdk = None

        steering_deg = None
        steering_max_deg = 450.0  # Fallback

        if angle is not None:
            steering_deg = angle * (180.0 / math.pi)

            if max_lock_irsdk is not None and max_lock_irsdk > 1e-3:
                steering_max_deg = abs(max_lock_irsdk) * (180.0 / math.pi)
            else:
                self._max_abs_steer = max(self._max_abs_steer, abs(angle))
                if self._max_abs_steer > 1e-3:
                    steering_max_deg = self._max_abs_steer * (180.0 / math.pi)

        data["steering"] = steering_deg if steering_deg is not None else 0.0
        data["steering_max_deg"] = steering_max_deg

        # --- Brake Bias (Prozent) ---
        bb = None
        for name in ("dcBrakeBias", "BrakeBias"):
            try:
                v = ir[name]
            except KeyError:
                continue
            else:
                if v is not None:
                    bb = float(v)
                    break

        if bb is not None:
            if 0.0 <= bb <= 1.0:
                bb *= 100.0
            data["brake_bias"] = bb
        else:
            data["brake_bias"] = None

        return data
