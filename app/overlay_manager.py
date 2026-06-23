import os
import sys
import json
import ctypes
import threading

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QPixmap, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QGroupBox,
    QGridLayout,
    QSlider,
    QFrame,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView

# -------------------------------------------------------------
# Pfad-Handling (Dev + PyInstaller)
# -------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)  # eine Ebene über "app" = Projekt-Root


def resource_path(*parts: str) -> str:
    """Liefert einen Pfad, der im Dev-Modus und im PyInstaller-Build passt.

    Dev:
      PROJECT_ROOT = .../iracing-overlay
      resource_path("overlays", "hud.html") → <root>/overlays/hud.html

    Frozen (PyInstaller):
      sys._MEIPASS = entpacktes Temp-Verzeichnis
      resource_path("overlays", "hud.html") → <_MEIPASS>/overlays/hud.html
    """
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = PROJECT_ROOT
    return os.path.join(base, *parts)


LOGO_PATH = resource_path("app", "idash_logo.png")
OVERLAY_DIR = resource_path("overlays")

# Layout-Datei: im Dev-Modus neben overlay_manager.py, im Build in einem
# User-spezifischen Config-Ordner (damit wir auf die Platte schreiben dürfen).
if hasattr(sys, "_MEIPASS"):
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".idash_overlay")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    LAYOUT_PATH = os.path.join(CONFIG_DIR, "overlay_layout.json")
else:
    LAYOUT_PATH = os.path.join(BASE_DIR, "overlay_layout.json")


# -------------------------------------------------------------
# Telemetry-Server als Hintergrund-Thread starten
# -------------------------------------------------------------
def _launch_telemetry_server():
    try:
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

/* Section header labels */
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

/* Overlay toggle buttons — compact tiles */
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

/* Settings controls */
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

/* Tools / CTA button */
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

    def _on_load_finished(self, ok: bool):
        if ok:
            self._apply_js_edit_mode()

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


class OverlayManager(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("iDash Overlay Manager")
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        self.setWindowIcon(QIcon(LOGO_PATH))

        # rahmenlos + transparenter Hintergrund, damit RootPanel rund sein kann
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Window
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._drag_offset = None  # zum Verschieben des Fensters

        self.hud_overlay = None
        self.relative_overlay = None
        self.standings_overlay = None
        self.wind_overlay = None
        self.strategy_overlay = None
        self.comparer_window = None

        self.current_opacity = 0.9

        self.layout_path = LAYOUT_PATH
        self.layout_state = self._load_layout_state()

        # Gespeicherte Deckkraft wiederherstellen
        saved_opacity = self.layout_state.get("opacity")
        if isinstance(saved_opacity, (int, float)):
            self.current_opacity = max(0.0, min(float(saved_opacity), 1.0))

        self._build_ui()

        # Zuletzt geöffnete Overlays wiederherstellen
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(300, self._restore_visible_overlays)

    def _restore_visible_overlays(self):
        vis = self.layout_state.get("visible", {})
        if vis.get("hud"):
            self.btn_hud.setChecked(True)
            self.toggle_hud(True)
        if vis.get("relative"):
            self.btn_relative.setChecked(True)
            self.toggle_relative(True)
        if vis.get("standings"):
            self.btn_standings.setChecked(True)
            self.toggle_standings(True)
        if vis.get("wind"):
            self.btn_wind.setChecked(True)
            self.toggle_wind(True)
        if vis.get("strategy"):
            self.btn_strategy.setChecked(True)
            self.toggle_strategy(True)

    def _set_visible(self, key: str, value: bool):
        if "visible" not in self.layout_state:
            self.layout_state["visible"] = {}
        self.layout_state["visible"][key] = value
        self._save_layout_state()

    # ------------ Layout-Persistenz ------------

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

    def _restore_geometry(self, win: QWidget, key: str):
        if not win:
            return
        g = self.layout_state.get(key)
        if not isinstance(g, dict):
            return
        try:
            x = int(g.get("x", win.x()))
            y = int(g.get("y", win.y()))
            w = int(g.get("w", win.width()))
            h = int(g.get("h", win.height()))
            win.setGeometry(x, y, w, h)
        except Exception:
            pass

    def _store_geometry(self, win: QWidget, key: str):
        if not win:
            return
        g = win.geometry()
        self.layout_state[key] = {
            "x": g.x(),
            "y": g.y(),
            "w": g.width(),
            "h": g.height(),
        }
        self._save_layout_state()

    # ------------ UI ------------

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

        # X-Button oben rechts (absolut positioniert)
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

        # ── Divider ──────────────────────────────────────────
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
        self.btn_standings.clicked.connect(self.toggle_standings)
        grid.addWidget(self.btn_standings, 0, 0)

        self.btn_relative = self._make_overlay_btn("Relative")
        self.btn_relative.clicked.connect(self.toggle_relative)
        grid.addWidget(self.btn_relative, 0, 1)

        self.btn_wind = self._make_overlay_btn("Wind / Env")
        self.btn_wind.clicked.connect(self.toggle_wind)
        grid.addWidget(self.btn_wind, 1, 0)

        self.btn_hud = self._make_overlay_btn("HUD")
        self.btn_hud.clicked.connect(self.toggle_hud)
        grid.addWidget(self.btn_hud, 1, 1)

        self.btn_strategy = self._make_overlay_btn("Strategy / Fuel")
        self.btn_strategy.clicked.connect(self.toggle_strategy)
        grid.addWidget(self.btn_strategy, 2, 0, 1, 2)

        main_layout.addLayout(grid)
        main_layout.addSpacing(16)

        # ── Divider ──────────────────────────────────────────
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

        # ── Divider ──────────────────────────────────────────
        main_layout.addWidget(self._make_divider())
        main_layout.addSpacing(12)

        # ── Tools ─────────────────────────────────────────────
        main_layout.addWidget(self._make_section_label("TOOLS"))
        main_layout.addSpacing(8)

        btn_comparer = QPushButton("⚙  Setup Comparer")
        btn_comparer.setObjectName("ComparerBtn")
        btn_comparer.clicked.connect(self.open_comparer)
        main_layout.addWidget(btn_comparer)

        main_layout.addStretch()

    # ------------ Window Dragging (über leere Bereiche des RootPanels) ------------

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

    # ------------ Button-Handler ------------

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
            view.load(QUrl("http://localhost:8080/comparer.html"))
            layout.addWidget(view)

            self.comparer_window = win

        self.comparer_window.show()
        self.comparer_window.raise_()
        self.comparer_window.activateWindow()

    def on_edit_mode_changed(self, state: int):
        enabled = state == Qt.Checked
        for win in (
            self.hud_overlay,
            self.relative_overlay,
            self.standings_overlay,
            self.wind_overlay,
            self.strategy_overlay,
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
        ):
            if win is not None:
                win.set_content_opacity(opacity)

    def closeEvent(self, event):
        if self.hud_overlay:
            self._store_geometry(self.hud_overlay, "hud")
        if self.relative_overlay:
            self._store_geometry(self.relative_overlay, "relative")
        if self.standings_overlay:
            self._store_geometry(self.standings_overlay, "standings")
        if self.wind_overlay:
            self._store_geometry(self.wind_overlay, "wind")
        if self.strategy_overlay:
            self._store_geometry(self.strategy_overlay, "strategy")
        super().closeEvent(event)


def main():
    # Windows DPI awareness must be set before any Qt/Chromium init
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--force-device-scale-factor=1")
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(LOGO_PATH))
    app.setStyleSheet(QSS_STYLE)

    win = OverlayManager()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
