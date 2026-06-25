"""Velopack-Self-Update für iDash.

Kapselt die Velopack-Integration, damit der Rest der App nichts davon weiß und
auch ohne installiertes `velopack`-Paket (z. B. roher Dev-Start) lauffähig
bleibt.

Zwei Einstiegspunkte:
  * ``run_startup_hooks()``  — MUSS als Allererstes in ``main()`` laufen, noch
    vor dem ``QApplication``. Verarbeitet Install/Update/Restart-Hooks von
    Velopack; bei einem Hook beendet sich der Prozess danach selbst.
  * ``check_and_apply(parent)`` — manueller „Auf Updates prüfen"-Trigger aus
    der GUI. Zeigt Qt-Dialoge und startet bei Erfolg neu.

Der Update-Feed wird über ``IDASH_UPDATE_FEED`` (ENV) oder ``DEFAULT_FEED_URL``
konfiguriert. Solange keiner gesetzt ist, ist die Update-Funktion inaktiv
(meldet das nur), bricht aber nie die App.
"""

from __future__ import annotations

import os

try:
    from app._version import __version__
except Exception:  # pragma: no cover - bei Bundle/Dev ohne Paketpfad
    try:
        from _version import __version__
    except Exception:
        __version__ = "0.0.0"

# Wird gesetzt, sobald das GitLab-Projekt + Release-Feed existiert (Teil F des
# Migrationsplans). Per ENV überschreibbar für lokale Tests
# (z. B. http://localhost:8000 über `python -m http.server` im releases-Ordner).
DEFAULT_FEED_URL = ""


def _feed_url() -> str:
    return os.environ.get("IDASH_UPDATE_FEED", DEFAULT_FEED_URL).strip()


def _import_velopack():
    """Importiert velopack lazy; gibt None zurück, wenn nicht verfügbar."""
    try:
        import velopack  # type: ignore
        return velopack
    except Exception:
        return None


def run_startup_hooks() -> None:
    """Velopack-Bootstrap. Als Erstes in main() aufrufen (vor QApplication)."""
    velopack = _import_velopack()
    if velopack is None:
        return
    try:
        velopack.App().run()
    except Exception:
        # Im Dev-Betrieb (nicht als Velopack-Installation gestartet) ist das
        # erwartbar – niemals den App-Start blockieren.
        pass


def check_and_apply(parent=None) -> None:
    """Manueller Update-Check inkl. GUI-Rückmeldung. Startet bei Erfolg neu."""
    # Importe hier lokal halten, damit das Modul auch headless nutzbar bleibt.
    from PyQt5.QtWidgets import QMessageBox

    feed = _feed_url()
    if not feed:
        QMessageBox.information(
            parent, "Updates",
            "Kein Update-Feed konfiguriert.\n"
            "Setze IDASH_UPDATE_FEED oder DEFAULT_FEED_URL (app/updater.py).",
        )
        return

    velopack = _import_velopack()
    if velopack is None:
        QMessageBox.warning(
            parent, "Updates",
            "Velopack ist nicht installiert (pip install velopack).",
        )
        return

    try:
        mgr = velopack.UpdateManager(feed)
        info = mgr.check_for_updates()
    except Exception as exc:
        QMessageBox.warning(parent, "Updates", f"Update-Prüfung fehlgeschlagen:\n{exc}")
        return

    if not info:
        QMessageBox.information(
            parent, "Updates", f"iDash {__version__} ist aktuell.",
        )
        return

    if QMessageBox.question(
        parent, "Update verfügbar",
        "Ein Update ist verfügbar. Jetzt herunterladen und neu starten?",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
    ) != QMessageBox.Yes:
        return

    try:
        mgr.download_updates(info)
        mgr.apply_updates_and_restart(info)
    except Exception as exc:
        QMessageBox.warning(parent, "Updates", f"Update fehlgeschlagen:\n{exc}")
