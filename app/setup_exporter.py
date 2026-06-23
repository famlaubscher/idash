"""
Liest das aktuelle Setup aus dem iRacing SDK (ir["CarSetup"])
und speichert es automatisch als HTM-Datei wenn es sich ändert.
"""
import os
import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

SETUPS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "setups")
DEBOUNCE_SECONDS = 5.0   # Warten bis Setup stabil ist


class SetupExporter:
    def __init__(self):
        self._last_exported_count = None
        self._changed_at = None          # Zeitpunkt der letzten UpdateCount-Änderung
        self._pending_count = None
        self._session_counter = 0
        os.makedirs(SETUPS_DIR, exist_ok=True)

    def check_and_export(self, ir) -> str | None:
        try:
            setup = ir["CarSetup"]
        except Exception:
            return None
        if not isinstance(setup, dict):
            return None

        update_count = setup.get("UpdateCount", 0)

        # UpdateCount hat sich geändert → Timer starten/zurücksetzen
        if update_count != self._pending_count:
            self._pending_count = update_count
            self._changed_at = time.monotonic()
            return None

        # Noch nicht stabil genug
        if time.monotonic() - self._changed_at < DEBOUNCE_SECONDS:
            return None

        # Bereits exportiert
        if update_count == self._last_exported_count:
            return None

        # Stabil und neu → exportieren
        self._last_exported_count = update_count
        self._session_counter += 1

        try:
            filename = self._export(ir, setup)
            logger.info("Setup exportiert: %s", filename)
            return filename
        except Exception:
            logger.exception("Fehler beim Setup-Export")
            return None

    def _export(self, ir, setup: dict) -> str:
        try:
            car_path = ir["DriverInfo"]["Drivers"][ir["PlayerCarIdx"]]["CarPath"] or "unknown"
        except Exception:
            car_path = "unknown"

        try:
            track = ir["WeekendInfo"]["TrackDisplayName"] or "unknown"
        except Exception:
            track = "unknown"

        # Setup-Name: Session-Counter + Timestamp (zuverlässig)
        ts = datetime.now().strftime("%H%M%S")
        setup_name = f"setup_{self._session_counter:02d}"

        safe_car   = re.sub(r'[^\w]', '_', car_path)[:20]
        safe_track = re.sub(r'[^\w]', '_', track)[:25]
        filename   = f"{safe_car}_{safe_track}_{setup_name}_{ts}.htm"
        filepath   = os.path.join(SETUPS_DIR, filename)

        html = self._build_html(car_path, track, setup_name, setup)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        return filename

    def _build_html(self, car: str, track: str, setup_name: str, setup: dict) -> str:
        lines = [
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">',
            '<html><head>',
            '<title>iRacing.com Motorsport Simulations Car Setup</title>',
            '<meta name="GENERATOR" content="iRacing.com Simulator">',
            '</head><body>',
            f'<H2 align="center">iRacing.com Motorsport Simulations<br>',
            f'{car} setup: {setup_name}<br>',
            f'track: {track}</H2><br><br>',
        ]

        def render_section(name: str, data: dict):
            lines.append(f'<H2><U>{name.upper()}:</U></H2>')
            for key, val in data.items():
                if key == "UpdateCount":
                    continue
                if isinstance(val, dict):
                    render_section(key, val)
                elif isinstance(val, list):
                    for i, item in enumerate(val):
                        if isinstance(item, dict):
                            render_section(f"{key} {i+1}", item)
                        else:
                            lines.append(f'{key} {i+1}:<U>{item}</U><br>')
                else:
                    lines.append(f'{_fmt_key(key)}:<U>{val}</U><br>')
            lines.append('<br>')

        for section_name, section_data in setup.items():
            if section_name == "UpdateCount":
                continue
            if isinstance(section_data, dict):
                render_section(section_name, section_data)
            else:
                lines.append(f'{_fmt_key(section_name)}:<U>{section_data}</U><br><br>')

        lines.append('</body></html>')
        return '\n'.join(lines)


def _fmt_key(key: str) -> str:
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', key)
