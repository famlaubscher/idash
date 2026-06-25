import json
import os
import sys
import ctypes
import threading

from PyQt5.QtCore import Qt, QUrl, QTimer
from PyQt5.QtGui import QPixmap, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QLabel,
    QGridLayout,
    QSlider,
    QFrame,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView

import updater

# -------------------------------------------------------------
# Pfad-Handling (Dev + PyInstaller)
# -------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)


def resource_path(*parts: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = PROJECT_ROOT
    return os.path.join(base, *parts)


LOGO_PATH = resource_path("app", "idash_logo.png")
OVERLAY_DIR = resource_path("overlays")

if hasattr(sys, "_MEIPASS"):
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".idash_overlay")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    LAYOUT_PATH = os.path.join(CONFIG_DIR, "overlay_layout.json")
else:
    LAYOUT_PATH = os.path.join(BASE_DIR, "overlay_layout.json")

# -------------------------------------------------------------
# Spalten-Definitionen: (key, label, default_visible)
# -------------------------------------------------------------

COLUMN_DEFS = {
    "standings": [
        ("pos",        "Pos",       True),
        ("car_number", "Car #",     True),
        ("car_logo",   "Logo",      True),
        ("name",       "Driver",    True),
        ("license",    "Lic / IR",  True),
        ("tyre",       "Tyre",      True),
        ("gap_front",  "Int",       True),
        ("gap_lead",   "Gap",       True),
        ("best_lap",   "Best",      True),
        ("last_lap",   "Last",      True),
        ("pit_stops",  "Stops",     True),
        ("stint_laps", "Stint",     True),
        ("pit_time",   "Pit T.",    True),
        ("pit_status", "Status",    True),
    ],
    "relative": [
        ("pos",        "Pos",       True),
        ("car_number", "Car #",     True),
        ("name",       "Driver",    True),
        ("tyre",       "Tyre",      True),
        ("car_class",  "Class",     True),
        ("gap",        "Gap",       True),
        ("delta",      "Delta",     True),
        ("best_lap",   "Best",      True),
        ("last_lap",   "Last",      True),
        ("pit_stops",  "Stops",     True),
        ("stint_laps", "Stint",     True),
        ("pit_time",   "Pit T.",    True),
        ("pit_status", "Status",    True),
    ],
}

# -------------------------------------------------------------
# Telemetry-Server als Hintergrund-Thread starten
# -------------------------------------------------------------

def _launch_telemetry_server():
    try:
        if os.environ.get("IDASH_REPLAY"):
            import replay_server
            print("Starte im REPLAY-Modus (kein iRacing nötig).")
            replay_server.run_from_env()
            return
        import telemetry_server
        telemetry_server.main()
    except Exception as e:
        print("Telemetry-Server Fehler:", e)

_ts_thread = threading.Thread(target=_launch_telemetry_server, daemon=True)
_ts_thread.start()


QSS_STYLE = """
QWidget {
    background-color: transparent;
    color: #E5E7EB;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 10pt;
}

QFrame#RootPanel {
    background-color: #0d0d14;
    border-radius: 16px;
    border: 1px solid rgba(255,255,255,0.07);
}

QLabel#LogoLabel { padding: 2px; }

QLabel#HeaderTitleLabel {
    font-size: 22px;
    font-weight: 800;
    color: #F9FAFB;
}
QLabel#HeaderSubtitleLabel {
    font-size: 8pt;
    color: #4B5563;
    letter-spacing: 2px;
    text-transform: uppercase;
}

QLabel#SectionLabel {
    color: #374151;
    font-size: 7pt;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 0px;
    margin-top: 4px;
}

QFrame#Divider {
    background-color: rgba(255,255,255,0.05);
    max-height: 1px;
    border: none;
    margin: 2px 0;
}

QPushButton#OverlayBtn {
    background-color: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 8px;
    min-height: 36px;
    max-height: 36px;
    color: #6B7280;
    font-size: 9pt;
    font-weight: 500;
    qproperty-alignment: AlignCenter;
}
QPushButton#OverlayBtn:hover {
    background-color: rgba(255,255,255,0.08);
    border-color: rgba(255,255,255,0.12);
    color: #D1D5DB;
}
QPushButton#OverlayBtn:pressed {
    background-color: rgba(255,255,255,0.02);
}
QPushButton#OverlayBtn[checkable=true]:checked {
    background-color: rgba(59,130,246,0.15);
    border-color: rgba(59,130,246,0.45);
    color: #93C5FD;
    font-weight: 600;
}
QPushButton#OverlayBtn[checkable=true]:checked:hover {
    background-color: rgba(59,130,246,0.25);
}

QCheckBox { spacing: 8px; color: #9CA3AF; font-size: 9pt; }
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid rgba(255,255,255,0.15);
    background-color: rgba(255,255,255,0.03);
    border-radius: 4px;
}
QCheckBox::indicator:checked {
    background-color: #3B82F6;
    border-color: #3B82F6;
}
QCheckBox:hover { color: #E5E7EB; }

QRadioButton { spacing: 8px; color: #9CA3AF; font-size: 9pt; }
QRadioButton::indicator {
    width: 13px;
    height: 13px;
    border: 1px solid rgba(255,255,255,0.15);
    background-color: rgba(255,255,255,0.03);
    border-radius: 7px;
}
QRadioButton::indicator:checked {
    background-color: #3B82F6;
    border-color: #3B82F6;
}
QRadioButton:hover { color: #E5E7EB; }

QSlider::groove:horizontal {
    height: 3px;
    background-color: rgba(255,255,255,0.07);
    border-radius: 2px;
    border: none;
}
QSlider::sub-page:horizontal {
    background-color: rgba(59,130,246,0.7);
    border-radius: 2px;
}
QSlider::add-page:horizontal {
    background-color: rgba(255,255,255,0.07);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background-color: #93C5FD;
    border: none;
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
}

QLabel#OpacityLabel { color: #4B5563; font-size: 8pt; }
QLabel#OpacityValue { color: #6B7280; font-size: 8pt; font-weight: 600; min-width: 28px; }

QPushButton#ComparerBtn {
    background-color: rgba(232,72,50,0.12);
    border: 1px solid rgba(232,72,50,0.30);
    border-radius: 8px;
    padding: 0px;
    min-height: 34px;
    color: #F87171;
    font-size: 9pt;
    font-weight: 600;
    text-align: center;
    padding-left: 0px;
}
QPushButton#ComparerBtn:hover {
    background-color: rgba(232,72,50,0.20);
    border-color: rgba(232,72,50,0.50);
    color: #FCA5A5;
}
QPushButton#ComparerBtn:pressed {
    background-color: rgba(232,72,50,0.08);
}

QPushButton#BtnClose {
    background-color: transparent;
    border: none;
    border-radius: 10px;
    color: #374151;
    font-size: 13px;
    min-width: 20px;
    max-width: 20px;
    min-height: 20px;
    max-height: 20px;
    padding: 0;
    text-align: center;
    padding-left: 0px;
}
QPushButton#BtnClose:hover {
    background-color: rgba(239,68,68,0.20);
    color: #FCA5A5;
    border: none;
}
"""

# --- Win32 Click-Through Helper ---
if sys.platform == "win32":
    user32 = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_LAYERED = 0x00080000

    def _set_window_click_through(hwnd: int, enable: bool):
        try:
            exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enable:
                exstyle |= WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                exstyle &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle)
        except Exception:
            pass
else:
    def _set_window_click_through(hwnd: int, enable: bool):  # type: ignore[unused-argument]
        return


# =============================================================
# OverlayConfigWindow — separates Config-Fenster pro Overlay
# =============================================================

class OverlayConfigWindow(QWidget):
    def __init__(self, overlay_key: str, title: str, manager: "OverlayManager"):
        super().__init__()
        self.overlay_key = overlay_key
        self.overlay_title = title
        self.manager = manager
        self._col_checkboxes: dict = {}
        self._drag_offset = None

        self.setWindowTitle(f"{title} – Konfiguration")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Window
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)

        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        root_panel = QFrame()
        root_panel.setObjectName("RootPanel")
        outer.addWidget(root_panel)

        btn_close = QPushButton("✕", root_panel)
        btn_close.setObjectName("BtnClose")
        btn_close.clicked.connect(self.hide)
        btn_close.raise_()

        def _panel_resize(ev, _btn=btn_close, _panel=root_panel):
            _btn.move(_panel.width() - 28, 8)
        root_panel.resizeEvent = _panel_resize

        main = QVBoxLayout(root_panel)
        main.setContentsMargins(20, 18, 20, 20)
        main.setSpacing(0)
        main.setAlignment(Qt.AlignTop)

        # Header
        title_lbl = QLabel(self.overlay_title)
        title_lbl.setObjectName("HeaderTitleLabel")
        main.addWidget(title_lbl)

        sub_lbl = QLabel("OVERLAY KONFIGURATION")
        sub_lbl.setObjectName("HeaderSubtitleLabel")
        main.addWidget(sub_lbl)
        main.addSpacing(14)

        main.addWidget(self._divider())
        main.addSpacing(10)

        # Visibility toggle
        main.addWidget(self._section("SICHTBARKEIT"))
        main.addSpacing(6)

        self.chk_visible = QCheckBox("Overlay anzeigen")
        vis = self.manager.layout_state.get("visible", {}).get(self.overlay_key, False)
        self.chk_visible.setChecked(vis)
        self.chk_visible.stateChanged.connect(self._on_visibility_changed)
        main.addWidget(self.chk_visible)
        main.addSpacing(12)
        main.addWidget(self._divider())
        main.addSpacing(10)

        # Visibility mode
        main.addWidget(self._section("ANZEIGEN WENN"))
        main.addSpacing(6)

        self._mode_group = QButtonGroup(self)
        saved_mode = self.manager.layout_state.get("visibility_mode", {}).get(
            self.overlay_key, "race_and_replay"
        )
        for mode_key, mode_label in [
            ("always",          "Immer"),
            ("race_and_replay", "Rennen & Replay"),
            ("race",            "Nur Rennen"),
            ("replay",          "Nur Replay"),
        ]:
            rb = QRadioButton(mode_label)
            rb.setChecked(mode_key == saved_mode)
            rb.toggled.connect(lambda checked, k=mode_key: self._on_mode_changed(k, checked))
            self._mode_group.addButton(rb)
            main.addWidget(rb)

        # Column section (only for overlays that have column defs)
        col_defs = COLUMN_DEFS.get(self.overlay_key, [])
        if col_defs:
            main.addSpacing(12)
            main.addWidget(self._divider())
            main.addSpacing(10)
            main.addWidget(self._section("SPALTEN"))
            main.addSpacing(6)

            saved_vis   = self.manager.layout_state.get("columns",   {}).get(self.overlay_key, {})
            saved_order = self.manager.layout_state.get("col_order", {}).get(self.overlay_key, [])

            # Build ordered list: saved order first, then any new keys appended
            all_keys = [k for k, _, _ in col_defs]
            ordered_keys = [k for k in saved_order if k in all_keys]
            ordered_keys += [k for k in all_keys if k not in ordered_keys]
            # Map key -> (label, default)
            col_info = {k: (lbl, dflt) for k, lbl, dflt in col_defs}

            self._col_order = ordered_keys[:]  # mutable list for reordering

            col_list = QVBoxLayout()
            col_list.setSpacing(2)

            def _rebuild_col_list():
                # Clear and rebuild the list widget
                while col_list.count():
                    item = col_list.takeAt(0)
                    w = item.widget()
                    if w:
                        w.deleteLater()
                self._col_checkboxes.clear()
                for pos, k in enumerate(self._col_order):
                    lbl, dflt = col_info[k]
                    row_w = QWidget()
                    row_l = QHBoxLayout(row_w)
                    row_l.setContentsMargins(0, 0, 0, 0)
                    row_l.setSpacing(4)

                    chk = QCheckBox(lbl)
                    chk.setChecked(saved_vis.get(k, dflt))
                    chk.stateChanged.connect(lambda state, key=k: self._on_col_changed(key, state))
                    self._col_checkboxes[k] = chk
                    row_l.addWidget(chk, 1)

                    btn_up = QPushButton("↑")
                    btn_up.setFixedSize(30, 26)
                    btn_up.setStyleSheet("font-size: 15pt; font-weight: 700;")
                    btn_up.setEnabled(pos > 0)
                    btn_up.clicked.connect(lambda _, idx=pos: _move_col(idx, -1))
                    row_l.addWidget(btn_up)

                    btn_dn = QPushButton("↓")
                    btn_dn.setFixedSize(30, 26)
                    btn_dn.setStyleSheet("font-size: 15pt; font-weight: 700;")
                    btn_dn.setEnabled(pos < len(self._col_order) - 1)
                    btn_dn.clicked.connect(lambda _, idx=pos: _move_col(idx, +1))
                    row_l.addWidget(btn_dn)

                    col_list.addWidget(row_w)

            def _move_col(idx: int, delta: int):
                new_idx = idx + delta
                if new_idx < 0 or new_idx >= len(self._col_order):
                    return
                self._col_order[idx], self._col_order[new_idx] = \
                    self._col_order[new_idx], self._col_order[idx]
                orders = self.manager.layout_state.setdefault("col_order", {})
                orders[self.overlay_key] = self._col_order[:]
                self.manager._save_layout_state()
                self.manager._push_columns_to_overlay(self.overlay_key)
                _rebuild_col_list()

            def _reset_cols():
                self._col_order = [k for k, _, _ in col_defs]
                saved_vis.clear()
                saved_vis.update({k: dflt for k, _, dflt in col_defs})
                orders = self.manager.layout_state.setdefault("col_order", {})
                orders[self.overlay_key] = self._col_order[:]
                cols = self.manager.layout_state.setdefault("columns", {})
                cols[self.overlay_key] = dict(saved_vis)
                self.manager._save_layout_state()
                self.manager._push_columns_to_overlay(self.overlay_key)
                _rebuild_col_list()

            _rebuild_col_list()
            main.addLayout(col_list)

            main.addSpacing(6)
            btn_reset = QPushButton("↺  Standard wiederherstellen")
            btn_reset.clicked.connect(_reset_cols)
            main.addWidget(btn_reset)

        main.addStretch()

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setObjectName("Divider")
        d.setFrameShape(QFrame.HLine)
        return d

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionLabel")
        return lbl

    # ------------------------------------------------------------------

    def _on_visibility_changed(self, state: int):
        visible = (state == Qt.Checked)
        toggle_fn = getattr(self.manager, f"toggle_{self.overlay_key}", None)
        if callable(toggle_fn):
            toggle_fn(visible)

    def _on_mode_changed(self, mode_key: str, checked: bool):
        if not checked:
            return
        modes = self.manager.layout_state.setdefault("visibility_mode", {})
        modes[self.overlay_key] = mode_key
        self.manager._save_layout_state()
        self.manager._push_visibility_mode_to_overlay(self.overlay_key)

    def _on_col_changed(self, key: str, state: int):
        checked = (state == Qt.Checked)
        cols = self.manager.layout_state.setdefault("columns", {})
        overlay_cols = cols.setdefault(self.overlay_key, {})
        overlay_cols[key] = checked
        self.manager._save_layout_state()
        self.manager._push_columns_to_overlay(self.overlay_key)

    def sync_visibility(self, visible: bool):
        """Aktualisiert den Sichtbarkeits-Checkbox ohne Signal auszulösen."""
        self.chk_visible.blockSignals(True)
        self.chk_visible.setChecked(visible)
        self.chk_visible.blockSignals(False)

    # ------------------------------------------------------------------
    # Dragging
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


# =============================================================
# OverlayWindow
# =============================================================

class OverlayWindow(QWidget):
    def set_content_opacity(self, opacity: float):
        if not getattr(self, "webview", None):
            return
        try:
            opacity = max(0.0, min(float(opacity), 1.0))
        except Exception:
            opacity = 1.0
        js = f"document.documentElement.style.setProperty('--card-bg-alpha', '{opacity:.3f}');"
        self.webview.page().runJavaScript(js)

    def __init__(self, relative_path: str, title: str):
        super().__init__()

        self.relative_path = relative_path
        self.edit_mode = False
        self._drag_offset = None
        self._on_drag_end = None
        self._on_load_extra = None  # optional callback after page load

        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.resize(640, 200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.webview = QWebEngineView(self)
        self.webview.setAttribute(Qt.WA_TranslucentBackground, True)
        self.webview.setStyleSheet("background: transparent;")
        try:
            self.webview.page().setBackgroundColor(Qt.transparent)
        except Exception:
            pass

        layout.addWidget(self.webview)

        html_path = os.path.join(OVERLAY_DIR, self.relative_path)
        html_path = os.path.abspath(html_path)
        self.webview.load(QUrl.fromLocalFile(html_path))
        self.webview.loadFinished.connect(self._on_load_finished)
        # KEIN contentsSizeChanged-Auto-Resize: self.resize(contentsSize) erwartet
        # logische Pixel, contentsSizeChanged liefert aber Device-Pixel. Bei DPR>1
        # (z. B. Monitor @ 200 %) verdoppelt sich die Fenstergröße pro Signal-Runde
        # (280 → … → 280x128 = 35840), bis QtWebEngine den Framebuffer nicht mehr
        # allozieren kann (createDIB-Fehler) und der Prozess mit 0xC0000409 abstürzt.
        # Overlays nutzen ihre kanonische Größe; Skalierung passiert im HTML/CSS.

    def _on_load_finished(self, ok: bool):
        if ok:
            self._apply_js_edit_mode()
            if callable(self._on_load_extra):
                self._on_load_extra()

    def showEvent(self, event):
        super().showEvent(event)
        # Kanonische (Default-)Fenstergröße merken, falls noch nicht gesetzt.
        if getattr(self, "_canonical_size", None) is None:
            self._canonical_size = (self.width(), self.height())
        # Per-Monitor-DPI (z. B. 4K @ 150 % + UHD @ 100 %): QtWebEngine berechnet
        # das Viewport beim ersten Anzeigen auf einem Monitor nicht immer korrekt
        # — der Inhalt erscheint dann abgeschnitten (nur obere linke Ecke) oder
        # gar nicht, bis man das Fenster einmal über die Monitorgrenze zieht.
        # Wir erzwingen den Relayout direkt nach dem Anzeigen.
        QTimer.singleShot(0, self._kick_relayout)
        QTimer.singleShot(120, self._kick_relayout)
        wh = self.windowHandle()
        if wh is not None and not getattr(self, "_screen_hooked", False):
            self._screen_hooked = True
            wh.screenChanged.connect(self._on_screen_changed)

    def _on_screen_changed(self, *_):
        # Beim Monitorwechsel skaliert Qt (AA_EnableHighDpiScaling) die
        # Fenstergröße mit dem DPI-Faktor. Das schaukelt sich bei wiederholtem
        # Hin-/Herschieben auf (780x650 → … → 29x22), bis das Fenster
        # verschwindet. Wir setzen die kanonische Größe zurück und layouten neu.
        size = getattr(self, "_canonical_size", None)
        if size is not None and (self.width(), self.height()) != tuple(size):
            self.resize(size[0], size[1])
        QTimer.singleShot(0, self._kick_relayout)

    def _kick_relayout(self):
        # 1px-Nudge der Fenstergröße erzwingt ein resizeEvent bis in die
        # WebEngine-Ansicht, wodurch das Viewport für den aktuellen Monitor
        # neu berechnet wird. Position (top-left) bleibt unverändert.
        if not self.isVisible() or not getattr(self, "webview", None):
            return
        g = self.geometry()
        self.resize(g.width(), g.height() + 1)
        self.resize(g.width(), g.height())

    def _apply_js_edit_mode(self):
        if not self.webview:
            return
        js = f"""
            if (window.setEditMode) {{
                window.setEditMode({str(self.edit_mode).lower()});
            }}
        """
        self.webview.page().runJavaScript(js)

    def _update_click_through_style(self):
        hwnd = int(self.winId())
        _set_window_click_through(hwnd, not self.edit_mode)

    def set_edit_mode(self, enabled: bool):
        self.edit_mode = enabled

        if enabled:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            if self.webview:
                self.webview.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            if self.webview:
                self.webview.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setCursor(Qt.ArrowCursor)
            self._drag_offset = None

        self._update_click_through_style()
        self._apply_js_edit_mode()

    def mousePressEvent(self, event):
        if self.edit_mode and event.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.edit_mode
            and self._drag_offset is not None
            and (event.buttons() & Qt.LeftButton)
        ):
            new_pos = event.globalPos() - self._drag_offset
            self.move(new_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.edit_mode and event.button() == Qt.LeftButton:
            self.setCursor(Qt.OpenHandCursor)
            self._drag_offset = None
            if callable(self._on_drag_end):
                self._on_drag_end(self)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


# =============================================================
# OverlayManager
# =============================================================

class OverlayManager(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("iDash Overlay Manager")
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        self.setWindowIcon(QIcon(LOGO_PATH))

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._drag_offset = None

        self.hud_overlay = None
        self.relative_overlay = None
        self.standings_overlay = None
        self.wind_overlay = None
        self.strategy_overlay = None
        self.circle_overlay = None
        self.comparer_window = None
        self.replay_control_window = None

        self._config_windows: dict = {}

        self.current_opacity = 0.9

        self.layout_path = LAYOUT_PATH
        self.layout_state = self._load_layout_state()

        saved_opacity = self.layout_state.get("opacity")
        if isinstance(saved_opacity, (int, float)):
            self.current_opacity = max(0.0, min(float(saved_opacity), 1.0))

        self._build_ui()

        from PyQt5.QtCore import QTimer
        QTimer.singleShot(300, self._restore_visible_overlays)

        # Im Replay-Modus die Steuerleiste automatisch öffnen (Buttons, Timeline,
        # Session-Zeit/Runde). Etwas verzögert, damit der HTTP-Server steht.
        if os.environ.get("IDASH_REPLAY"):
            QTimer.singleShot(1200, self.open_replay_control)

    # ------------------------------------------------------------------
    # Overlay-Config-Fenster öffnen / Spalten pushen
    # ------------------------------------------------------------------

    def _open_overlay_config(self, key: str, title: str):
        # Button-Zustand auf tatsächlichen Sichtbarkeitszustand zurücksetzen
        # (Qt hat ihn durch den Klick schon umgeschaltet)
        actual = self.layout_state.get("visible", {}).get(key, False)
        btn = getattr(self, f"btn_{key}", None)
        if btn:
            btn.setChecked(actual)

        if key not in self._config_windows:
            win = OverlayConfigWindow(key, title, self)
            win.setStyleSheet(QSS_STYLE)
            self._config_windows[key] = win

        win = self._config_windows[key]
        if not win.isVisible():
            mgr_geo = self.frameGeometry()
            win.move(mgr_geo.right() + 10, mgr_geo.top())
        win.show()
        win.raise_()
        win.activateWindow()

    def _push_columns_to_overlay(self, key: str):
        """Schickt aktuelle Spalten-Config (Sichtbarkeit + Reihenfolge) per JS ans Overlay."""
        win = getattr(self, f"{key}_overlay", None)
        if not win or not getattr(win, "webview", None):
            return
        defs = COLUMN_DEFS.get(key, [])
        if not defs:
            return
        saved = self.layout_state.get("columns", {}).get(key, {})
        cfg = {col_key: saved.get(col_key, default) for col_key, _, default in defs}
        js = f"if (window.setColumns) window.setColumns({json.dumps(cfg)});"
        win.webview.page().runJavaScript(js)

        saved_order = self.layout_state.get("col_order", {}).get(key, [])
        all_keys = [k for k, _, _ in defs]
        order = [k for k in saved_order if k in all_keys]
        order += [k for k in all_keys if k not in order]
        js2 = f"if (window.setColumnOrder) window.setColumnOrder({json.dumps(order)});"
        win.webview.page().runJavaScript(js2)

    def _push_visibility_mode_to_overlay(self, key: str):
        """Schickt den Sichtbarkeits-Modus per JS ans Overlay."""
        win = getattr(self, f"{key}_overlay", None)
        if not win or not getattr(win, "webview", None):
            return
        mode = self.layout_state.get("visibility_mode", {}).get(key, "race_and_replay")
        js = f"if (window.setVisibilityMode) window.setVisibilityMode('{mode}');"
        win.webview.page().runJavaScript(js)

    def _on_overlay_loaded(self, key: str):
        """Wird nach dem Laden einer Overlay-Seite aufgerufen."""
        self._push_columns_to_overlay(key)
        self._push_visibility_mode_to_overlay(key)

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def _restore_visible_overlays(self):
        vis = self.layout_state.get("visible", {})
        if vis.get("hud"):
            self.toggle_hud(True)
        if vis.get("relative"):
            self.toggle_relative(True)
        if vis.get("standings"):
            self.toggle_standings(True)
        if vis.get("wind"):
            self.toggle_wind(True)
        if vis.get("strategy"):
            self.toggle_strategy(True)
        if vis.get("circle"):
            self.toggle_circle(True)

    def _set_visible(self, key: str, value: bool):
        if "visible" not in self.layout_state:
            self.layout_state["visible"] = {}
        self.layout_state["visible"][key] = value
        self._save_layout_state()
        # Button-Zustand synchronisieren
        btn = getattr(self, f"btn_{key}", None)
        if btn:
            btn.setChecked(value)
        # Config-Fenster synchronisieren (falls offen)
        if key in self._config_windows:
            self._config_windows[key].sync_visibility(value)

    # ------------------------------------------------------------------
    # Layout-Persistenz
    # ------------------------------------------------------------------

    def _load_layout_state(self):
        try:
            if os.path.exists(self.layout_path):
                with open(self.layout_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception as e:
            print("Konnte overlay_layout.json nicht laden:", e)
        return {}

    def _save_layout_state(self):
        try:
            tmp_path = self.layout_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.layout_state, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self.layout_path)
        except Exception as e:
            print("Konnte overlay_layout.json nicht schreiben:", e)

    def _clamp_to_screen(self, x: int, y: int, w: int, h: int):
        """Hält die Fensterposition im sichtbaren Gesamt-Desktop (alle Monitore),
        sodass mindestens ein Streifen sichtbar bleibt und nichts „off-screen“
        verloren geht."""
        try:
            screens = QApplication.screens()
            if not screens:
                return x, y
            virt = screens[0].geometry()
            for s in screens[1:]:
                virt = virt.united(s.geometry())
            margin = 80
            x = max(virt.left() - max(0, w - margin), min(x, virt.right() - margin))
            y = max(virt.top(), min(y, virt.bottom() - margin))
        except Exception:
            pass
        return int(x), int(y)

    def _restore_geometry(self, win: QWidget, key: str):
        if not win:
            return
        # Die kanonische (Default-)Fenstergröße kommt aus dem resize() im toggle_*
        # und wird hier festgehalten. Sie wird BEWUSST NICHT aus der Persistenz
        # übernommen: Qt skaliert die Fenstergröße beim Monitorwechsel mit dem
        # DPI-Faktor, das kann sich bis auf wenige Pixel aufschaukeln (29x22) und
        # würde das Fenster sonst dauerhaft unsichtbar machen. Persistiert wird
        # nur die Position.
        win._canonical_size = (win.width(), win.height())
        g = self.layout_state.get(key)
        x, y = win.x(), win.y()
        if isinstance(g, dict):
            try:
                x = int(g.get("x", x))
                y = int(g.get("y", y))
            except Exception:
                pass
        x, y = self._clamp_to_screen(x, y, win.width(), win.height())
        win.move(x, y)

    def _store_geometry(self, win: QWidget, key: str):
        if not win:
            return
        g = win.geometry()
        # Größe: immer die kanonische Größe schreiben, nie einen evtl. durch
        # DPI-Rescale geschrumpften Live-Wert. Beim Restore wird w/h ohnehin
        # ignoriert; wir halten die Datei nur sauber/lesbar.
        size = getattr(win, "_canonical_size", None) or (g.width(), g.height())
        self.layout_state[key] = {
            "x": g.x(),
            "y": g.y(),
            "w": int(size[0]),
            "h": int(size[1]),
        }
        self._save_layout_state()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _make_section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionLabel")
        return lbl

    def _make_divider(self) -> QFrame:
        d = QFrame()
        d.setObjectName("Divider")
        d.setFrameShape(QFrame.HLine)
        return d

    def _make_overlay_btn(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("OverlayBtn")
        btn.setCheckable(True)
        return btn

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        root_panel = QFrame()
        root_panel.setObjectName("RootPanel")
        outer.addWidget(root_panel)

        btn_close = QPushButton("✕", root_panel)
        btn_close.setObjectName("BtnClose")
        btn_close.clicked.connect(QApplication.instance().quit)
        btn_close.move(root_panel.width() - 28, 8)
        btn_close.raise_()
        def _panel_resize(ev, _btn=btn_close, _panel=root_panel):
            _btn.move(_panel.width() - 28, 8)
        root_panel.resizeEvent = _panel_resize

        main_layout = QVBoxLayout(root_panel)
        main_layout.setContentsMargins(20, 18, 20, 20)
        main_layout.setSpacing(0)
        main_layout.setAlignment(Qt.AlignTop)

        # ── Header ──────────────────────────────────────────
        logo_label = QLabel()
        logo_label.setObjectName("LogoLabel")
        logo_label.setAlignment(Qt.AlignHCenter)
        pix = QPixmap(LOGO_PATH)
        if not pix.isNull():
            scaled = pix.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled)
        main_layout.addWidget(logo_label, alignment=Qt.AlignHCenter)

        header_sub = QLabel("OVERLAY MANAGER")
        header_sub.setObjectName("HeaderSubtitleLabel")
        header_sub.setAlignment(Qt.AlignHCenter)
        main_layout.addWidget(header_sub)
        main_layout.addSpacing(16)

        main_layout.addWidget(self._make_divider())
        main_layout.addSpacing(12)

        # ── Overlays ─────────────────────────────────────────
        main_layout.addWidget(self._make_section_label("OVERLAYS"))
        main_layout.addSpacing(8)

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.btn_standings = self._make_overlay_btn("Standings")
        self.btn_standings.clicked.connect(
            lambda: self._open_overlay_config("standings", "Standings")
        )
        grid.addWidget(self.btn_standings, 0, 0)

        self.btn_relative = self._make_overlay_btn("Relative")
        self.btn_relative.clicked.connect(
            lambda: self._open_overlay_config("relative", "Relative")
        )
        grid.addWidget(self.btn_relative, 0, 1)

        self.btn_wind = self._make_overlay_btn("Wind / Env")
        self.btn_wind.clicked.connect(
            lambda: self._open_overlay_config("wind", "Wind / Env")
        )
        grid.addWidget(self.btn_wind, 1, 0)

        self.btn_hud = self._make_overlay_btn("HUD")
        self.btn_hud.clicked.connect(
            lambda: self._open_overlay_config("hud", "HUD")
        )
        grid.addWidget(self.btn_hud, 1, 1)

        self.btn_strategy = self._make_overlay_btn("Strategy / Fuel")
        self.btn_strategy.clicked.connect(
            lambda: self._open_overlay_config("strategy", "Strategy / Fuel")
        )
        grid.addWidget(self.btn_strategy, 2, 0)

        self.btn_circle = self._make_overlay_btn("Circle of Doom")
        self.btn_circle.clicked.connect(
            lambda: self._open_overlay_config("circle", "Circle of Doom")
        )
        grid.addWidget(self.btn_circle, 2, 1)

        main_layout.addLayout(grid)
        main_layout.addSpacing(16)

        main_layout.addWidget(self._make_divider())
        main_layout.addSpacing(12)

        # ── Einstellungen ─────────────────────────────────────
        main_layout.addWidget(self._make_section_label("EINSTELLUNGEN"))
        main_layout.addSpacing(8)

        self.chk_edit = QCheckBox("Bearbeitungsmodus")
        self.chk_edit.setToolTip(
            "Aktiv: Overlays greifbar (ziehen / skalieren).\n"
            "Inaktiv: Overlays sind klick-durchlässig → iRacing bekommt die Klicks."
        )
        self.chk_edit.stateChanged.connect(self.on_edit_mode_changed)
        main_layout.addWidget(self.chk_edit)
        main_layout.addSpacing(8)

        opacity_row = QHBoxLayout()
        opacity_row.setSpacing(8)
        opacity_label = QLabel("Overlay-Alpha")
        opacity_label.setObjectName("OpacityLabel")

        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setMinimum(0)
        self.slider_opacity.setMaximum(100)
        self.slider_opacity.setSingleStep(5)
        self.slider_opacity.setTickInterval(10)
        self.slider_opacity.setValue(int(self.current_opacity * 100))
        self.slider_opacity.valueChanged.connect(self.on_opacity_changed)

        self.lbl_opacity_value = QLabel(f"{int(self.current_opacity * 100)}%")
        self.lbl_opacity_value.setObjectName("OpacityValue")

        opacity_row.addWidget(opacity_label)
        opacity_row.addWidget(self.slider_opacity, 1)
        opacity_row.addWidget(self.lbl_opacity_value)
        main_layout.addLayout(opacity_row)
        main_layout.addSpacing(16)

        main_layout.addWidget(self._make_divider())
        main_layout.addSpacing(12)

        # ── Tools ─────────────────────────────────────────────
        main_layout.addWidget(self._make_section_label("TOOLS"))
        main_layout.addSpacing(8)

        btn_comparer = QPushButton("⚙  Setup Comparer")
        btn_comparer.setObjectName("ComparerBtn")
        btn_comparer.clicked.connect(self.open_comparer)
        main_layout.addWidget(btn_comparer)
        main_layout.addSpacing(6)

        self.btn_pit_cal = QPushButton("◉  Pit-Kalibrierung starten")
        self.btn_pit_cal.setObjectName("OverlayBtn")
        self.btn_pit_cal.setCheckable(True)
        self.btn_pit_cal.clicked.connect(self.toggle_pit_calibration)
        main_layout.addWidget(self.btn_pit_cal)
        main_layout.addSpacing(6)

        btn_update = QPushButton("⭯  Auf Updates prüfen")
        btn_update.setObjectName("ComparerBtn")
        btn_update.clicked.connect(lambda: updater.check_and_apply(self))
        main_layout.addWidget(btn_update)

        main_layout.addStretch()

        # ── Footer: Versionsanzeige ──────────────────────────
        version_label = QLabel(f"iDash v{updater.__version__}")
        version_label.setObjectName("HeaderSubtitleLabel")
        version_label.setAlignment(Qt.AlignHCenter)
        main_layout.addWidget(version_label)

    # ------------------------------------------------------------------
    # Window Dragging
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            new_pos = event.globalPos() - self._drag_offset
            self.move(new_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Toggle-Methoden (aufgerufen von OverlayConfigWindow)
    # ------------------------------------------------------------------

    def toggle_hud(self, checked: bool):
        if checked:
            if self.hud_overlay is None:
                self.hud_overlay = OverlayWindow("hud.html", "HUD Overlay")
                self.hud_overlay.setWindowOpacity(1.0)
                self.hud_overlay.set_content_opacity(self.current_opacity)
                self.hud_overlay.resize(520, 160)
                self.hud_overlay.move(200, 200)
                self._restore_geometry(self.hud_overlay, "hud")
                self.hud_overlay._on_drag_end = lambda w: self._store_geometry(w, "hud")
                self.hud_overlay._on_load_extra = lambda: self._on_overlay_loaded("hud")
                self.hud_overlay.show()
                self.hud_overlay.set_edit_mode(self.chk_edit.isChecked())
            else:
                self.hud_overlay.show()
            self._set_visible("hud", True)
        else:
            if self.hud_overlay:
                self._store_geometry(self.hud_overlay, "hud")
                self.hud_overlay.hide()
            self._set_visible("hud", False)

    def toggle_relative(self, checked: bool):
        if checked:
            if self.relative_overlay is None:
                self.relative_overlay = OverlayWindow("relative.html", "Relative Overlay")
                self.relative_overlay.setWindowOpacity(1.0)
                self.relative_overlay.set_content_opacity(self.current_opacity)
                self.relative_overlay.resize(700, 420)
                self.relative_overlay.move(200, 420)
                self._restore_geometry(self.relative_overlay, "relative")
                self.relative_overlay._on_drag_end = lambda w: self._store_geometry(w, "relative")
                self.relative_overlay._on_load_extra = lambda: self._on_overlay_loaded("relative")
                self.relative_overlay.show()
                self.relative_overlay.set_edit_mode(self.chk_edit.isChecked())
            else:
                self.relative_overlay.show()
            self._set_visible("relative", True)
        else:
            if self.relative_overlay:
                self._store_geometry(self.relative_overlay, "relative")
                self.relative_overlay.hide()
            self._set_visible("relative", False)

    def toggle_standings(self, checked: bool):
        if checked:
            if self.standings_overlay is None:
                self.standings_overlay = OverlayWindow("standings.html", "Standings Overlay")
                self.standings_overlay.setWindowOpacity(1.0)
                self.standings_overlay.set_content_opacity(self.current_opacity)
                self.standings_overlay.resize(780, 650)
                self.standings_overlay.move(700, 200)
                self._restore_geometry(self.standings_overlay, "standings")
                self.standings_overlay._on_drag_end = lambda w: self._store_geometry(w, "standings")
                self.standings_overlay._on_load_extra = lambda: self._on_overlay_loaded("standings")
                self.standings_overlay.show()
                self.standings_overlay.set_edit_mode(self.chk_edit.isChecked())
            else:
                self.standings_overlay.show()
            self._set_visible("standings", True)
        else:
            if self.standings_overlay:
                self._store_geometry(self.standings_overlay, "standings")
                self.standings_overlay.hide()
            self._set_visible("standings", False)

    def toggle_wind(self, checked: bool):
        if checked:
            if self.wind_overlay is None:
                self.wind_overlay = OverlayWindow("wind.html", "Wind Overlay")
                self.wind_overlay.setWindowOpacity(1.0)
                self.wind_overlay.set_content_opacity(self.current_opacity)
                self.wind_overlay.resize(280, 160)
                self.wind_overlay.move(700, 420)
                self._restore_geometry(self.wind_overlay, "wind")
                self.wind_overlay._on_drag_end = lambda w: self._store_geometry(w, "wind")
                self.wind_overlay._on_load_extra = lambda: self._on_overlay_loaded("wind")
                self.wind_overlay.show()
                self.wind_overlay.set_edit_mode(self.chk_edit.isChecked())
            else:
                self.wind_overlay.show()
            self._set_visible("wind", True)
        else:
            if self.wind_overlay:
                self._store_geometry(self.wind_overlay, "wind")
                self.wind_overlay.hide()
            self._set_visible("wind", False)

    def toggle_strategy(self, checked: bool):
        if checked:
            if self.strategy_overlay is None:
                self.strategy_overlay = OverlayWindow("strategy.html", "Strategy Overlay")
                self.strategy_overlay.setWindowOpacity(1.0)
                self.strategy_overlay.set_content_opacity(self.current_opacity)
                self.strategy_overlay.resize(780, 340)
                self.strategy_overlay.move(700, 640)
                self._restore_geometry(self.strategy_overlay, "strategy")
                self.strategy_overlay._on_drag_end = lambda w: self._store_geometry(w, "strategy")
                self.strategy_overlay._on_load_extra = lambda: self._on_overlay_loaded("strategy")
                self.strategy_overlay.show()
                self.strategy_overlay.set_edit_mode(self.chk_edit.isChecked())
            else:
                self.strategy_overlay.show()
            self._set_visible("strategy", True)
        else:
            if self.strategy_overlay:
                self._store_geometry(self.strategy_overlay, "strategy")
                self.strategy_overlay.hide()
            self._set_visible("strategy", False)

    def toggle_circle(self, checked: bool):
        if checked:
            if self.circle_overlay is None:
                self.circle_overlay = OverlayWindow("circle.html", "Circle of Doom")
                self.circle_overlay.setWindowOpacity(1.0)
                self.circle_overlay.set_content_opacity(self.current_opacity)
                self.circle_overlay.resize(580, 580)
                self.circle_overlay.move(900, 160)
                self._restore_geometry(self.circle_overlay, "circle")
                self.circle_overlay._on_drag_end = lambda w: self._store_geometry(w, "circle")
                self.circle_overlay._on_load_extra = lambda: self._on_overlay_loaded("circle")
                self.circle_overlay.show()
                self.circle_overlay.set_edit_mode(self.chk_edit.isChecked())
            else:
                self.circle_overlay.show()
            self._set_visible("circle", True)
        else:
            if self.circle_overlay:
                self._store_geometry(self.circle_overlay, "circle")
                self.circle_overlay.hide()
            self._set_visible("circle", False)

    def open_comparer(self):
        if self.comparer_window is None:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtWebEngineWidgets import QWebEngineView

            win = QWidget()
            win.setWindowTitle("Setup Comparer")
            win.resize(1400, 900)
            layout = QVBoxLayout(win)
            layout.setContentsMargins(0, 0, 0, 0)

            view = QWebEngineView()
            try:
                import telemetry_server
                http_port = telemetry_server.HTTP_PORT
            except Exception:
                http_port = 8080
            view.load(QUrl(f"http://localhost:{http_port}/comparer.html"))
            layout.addWidget(view)

            self.comparer_window = win

        self.comparer_window.show()
        self.comparer_window.raise_()
        self.comparer_window.activateWindow()

    def open_replay_control(self):
        """Steuerleiste für den Replay (Buttons, Timeline, Zeit/Runde)."""
        if self.replay_control_window is None:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtWebEngineWidgets import QWebEngineView

            win = QWidget()
            win.setWindowTitle("iDash Replay-Steuerung")
            win.resize(1100, 240)
            win.setWindowIcon(QIcon(LOGO_PATH))
            layout = QVBoxLayout(win)
            layout.setContentsMargins(0, 0, 0, 0)

            view = QWebEngineView()
            try:
                import telemetry_server
                http_port = telemetry_server.HTTP_PORT
            except Exception:
                http_port = 8080
            view.load(QUrl(f"http://localhost:{http_port}/replay_control.html"))
            layout.addWidget(view)

            self.replay_control_window = win

        self.replay_control_window.show()
        self.replay_control_window.raise_()
        self.replay_control_window.activateWindow()

    # ------------------------------------------------------------------
    # Pit-Kalibrierung
    # ------------------------------------------------------------------

    def _send_pit_cmd(self, action: str):
        nonce = int(self.layout_state.get("_pit_cmd_nonce", 0)) + 1
        self.layout_state["_pit_cmd_nonce"] = nonce
        self.layout_state["pit_cal_cmd"] = {"action": action, "nonce": nonce}
        self._save_layout_state()

    def toggle_pit_calibration(self, checked: bool):
        if checked:
            self._send_pit_cmd("start")
            self.btn_pit_cal.setText("◉  Kalibrierung läuft – stoppen")
            if not self.btn_circle.isChecked():
                self.toggle_circle(True)
        else:
            self._send_pit_cmd("stop")
            self.btn_pit_cal.setText("◉  Pit-Kalibrierung starten")

    # ------------------------------------------------------------------
    # Einstellungen
    # ------------------------------------------------------------------

    def on_edit_mode_changed(self, state: int):
        enabled = state == Qt.Checked
        for win in (
            self.hud_overlay,
            self.relative_overlay,
            self.standings_overlay,
            self.wind_overlay,
            self.strategy_overlay,
            self.circle_overlay,
        ):
            if win:
                win.set_edit_mode(enabled)

    def on_opacity_changed(self, value: int):
        opacity = max(0.0, min(value / 100.0, 1.0))
        self.current_opacity = opacity
        self.lbl_opacity_value.setText(f"{int(opacity * 100)}%")
        self.layout_state["opacity"] = opacity
        self._save_layout_state()
        for win in (
            self.hud_overlay,
            self.relative_overlay,
            self.standings_overlay,
            self.wind_overlay,
            self.strategy_overlay,
            self.circle_overlay,
        ):
            if win is not None:
                win.set_content_opacity(opacity)

    def closeEvent(self, event):
        for key, attr in [
            ("hud", self.hud_overlay),
            ("relative", self.relative_overlay),
            ("standings", self.standings_overlay),
            ("wind", self.wind_overlay),
            ("strategy", self.strategy_overlay),
            ("circle", self.circle_overlay),
        ]:
            if attr:
                self._store_geometry(attr, key)
        super().closeEvent(event)


def main():
    # Velopack-Bootstrap: MUSS vor allem anderen laufen (Install/Update/Restart-
    # Hooks). Im Dev-Betrieb ein No-op.
    updater.run_startup_hooks()

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    # NICHT --force-device-scale-factor setzen: Bei gemischter Monitor-DPI
    # (z. B. 4K @ 150 % + UHD @ 100 %) erzwingt das in der eingebetteten
    # Chromium-Ansicht einen festen DPR, der nicht mehr zur logischen
    # Fenstergröße passt → Overlay-Inhalt wird abgeschnitten / falsch skaliert.
    # Mit PER_MONITOR_DPI_AWARE + AA_EnableHighDpiScaling skaliert QtWebEngine
    # korrekt pro Monitor. --disable-gpu/--no-sandbox aus main bleiben erhalten.
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        + " --disable-gpu --no-sandbox"
    ).strip()
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(LOGO_PATH))
    app.setStyleSheet(QSS_STYLE)

    win = OverlayManager()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
