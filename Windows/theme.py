"""Dark/light theming, following the Windows system setting (with live switching), since SwiftUI's
automatic light/dark appearance has no built-in Qt equivalent.

Centralizing colors here (rather than scattering per-page stylesheet strings) also fixes the root
cause of the original low-contrast look: most pages use the Qt stylesheet pseudo-color
`palette(mid)` for secondary text, which only reads well if the app's QPalette actually defines a
sensible `Mid` role for the current theme — the default Fusion-on-Windows palette does not.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtGui import QPalette, QColor, QGuiApplication
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Scheme:
    window: str
    base: str
    card: str
    card_border: str
    text: str
    text_secondary: str
    accent: str
    success: str
    warning: str
    danger: str
    track: str


DARK = Scheme(
    window="#1e1f22", base="#17181a", card="#323438", card_border="#53565e",
    text="#ffffff", text_secondary="#d3d6dc", accent="#5b9dff",
    success="#3ba55c", warning="#faa61a", danger="#ff5c5c", track="#141518",
)
LIGHT = Scheme(
    window="#f2f3f5", base="#ffffff", card="#ffffff", card_border="#c7cad0",
    text="#13151a", text_secondary="#33353b", accent="#1a73e8",
    success="#1f9254", warning="#b36a00", danger="#d93025", track="#e3e5e8",
)


def _is_system_dark() -> bool:
    if sys.platform == "win32":
        try:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                value, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
                return value == 0
        except OSError:
            pass
    try:
        hints = QGuiApplication.styleHints()
        return hints.colorScheme() == hints.colorScheme().Dark
    except Exception:
        return True


class ThemeManager(QObject):
    changed = Signal()
    _instance: "ThemeManager | None" = None

    def __init__(self):
        super().__init__()
        self.dark = _is_system_dark()
        self.scheme: Scheme = DARK if self.dark else LIGHT
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(3000)

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    def _poll(self):
        dark = _is_system_dark()
        if dark != self.dark:
            self.dark = dark
            self.scheme = DARK if dark else LIGHT
            app = QApplication.instance()
            if app is not None:
                apply_theme(app)
            self.changed.emit()


def current() -> Scheme:
    return ThemeManager.instance().scheme


def card_qss(radius: int = 10) -> str:
    """Stylesheet for a QFrame with `objectName("Card")` — shared by every hand-rolled card-style
    panel (widgets_common.Card, the overview grid cards, VM/VPN row panels) so they all stay in sync
    with the current theme."""
    s = current()
    return f"#Card {{ background-color: {s.card}; border: 1px solid {s.card_border}; border-radius: {radius}px; }}"


def apply_theme(app: QApplication):
    tm = ThemeManager.instance()
    s = tm.scheme
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(s.window))
    pal.setColor(QPalette.WindowText, QColor(s.text))
    pal.setColor(QPalette.Base, QColor(s.base))
    pal.setColor(QPalette.AlternateBase, QColor(s.card))
    pal.setColor(QPalette.ToolTipBase, QColor(s.card))
    pal.setColor(QPalette.ToolTipText, QColor(s.text))
    pal.setColor(QPalette.Text, QColor(s.text))
    pal.setColor(QPalette.Button, QColor(s.card))
    pal.setColor(QPalette.ButtonText, QColor(s.text))
    pal.setColor(QPalette.BrightText, QColor(s.danger))
    pal.setColor(QPalette.Link, QColor(s.accent))
    pal.setColor(QPalette.Highlight, QColor(s.accent))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.PlaceholderText, QColor(s.text_secondary))
    # `palette(mid)`/`palette(dark)` are what most pages use for secondary/caption text in their
    # inline stylesheets — defining them explicitly is what actually fixes the contrast.
    pal.setColor(QPalette.Mid, QColor(s.text_secondary))
    pal.setColor(QPalette.Dark, QColor(s.text_secondary))
    pal.setColor(QPalette.Midlight, QColor(s.card_border))
    pal.setColor(QPalette.Light, QColor(s.card_border))
    for group in (QPalette.Disabled,):
        pal.setColor(group, QPalette.Text, QColor(s.text_secondary))
        pal.setColor(group, QPalette.WindowText, QColor(s.text_secondary))
        pal.setColor(group, QPalette.ButtonText, QColor(s.text_secondary))
    app.setPalette(pal)
    app.setStyleSheet(_stylesheet(s))


def _stylesheet(s: Scheme) -> str:
    return f"""
    QMainWindow, QDialog {{ background-color: {s.window}; color: {s.text}; }}
    QMenuBar {{ background-color: {s.window}; color: {s.text}; }}
    QMenuBar::item:selected {{ background-color: {s.card_border}; }}
    QMenu {{ background-color: {s.card}; color: {s.text}; border: 1px solid {s.card_border}; }}
    QMenu::item:selected {{ background-color: {s.accent}; color: #ffffff; }}
    QToolBar {{ background-color: {s.window}; border: none; spacing: 6px; padding: 4px; }}
    QToolButton {{ background: transparent; color: {s.text}; padding: 4px 8px; border-radius: 6px; }}
    QToolButton:hover {{ background-color: {s.card_border}; }}

    QTreeWidget, QListWidget, QTableWidget, QTextEdit, QPlainTextEdit {{
        background-color: {s.base}; color: {s.text}; border: 1px solid {s.card_border};
        border-radius: 6px; alternate-background-color: {s.card};
    }}
    QTreeWidget::item, QListWidget::item {{ padding: 4px; }}
    QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{
        background-color: {s.accent}; color: #ffffff;
    }}
    QHeaderView::section {{
        background-color: {s.card}; color: {s.text_secondary}; border: none;
        border-bottom: 1px solid {s.card_border}; padding: 4px;
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {s.base}; color: {s.text}; border: 1px solid {s.card_border};
        border-radius: 6px; padding: 4px 6px;
    }}
    QLineEdit:focus, QComboBox:focus {{ border: 1px solid {s.accent}; }}
    QComboBox QAbstractItemView {{
        background-color: {s.card}; color: {s.text}; selection-background-color: {s.accent};
        selection-color: #ffffff; border: 1px solid {s.card_border};
    }}

    QPushButton {{
        background-color: {s.card}; color: {s.text}; border: 1px solid {s.card_border};
        border-radius: 6px; padding: 5px 14px;
    }}
    QPushButton:hover {{ border-color: {s.accent}; }}
    QPushButton:pressed {{ background-color: {s.accent}; color: #ffffff; }}
    QPushButton:disabled {{ color: {s.text_secondary}; }}
    QPushButton:default {{ background-color: {s.accent}; color: #ffffff; border-color: {s.accent}; }}

    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
    QScrollBar::handle {{ background: {s.card_border}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}

    QGroupBox {{
        border: 1px solid {s.card_border}; border-radius: 8px; margin-top: 12px; padding-top: 10px;
        font-weight: 600;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {s.text}; }}

    QProgressBar {{ background: {s.track}; border: none; border-radius: 3px; }}
    QProgressBar::chunk {{ background: {s.accent}; border-radius: 3px; }}

    QLabel {{ color: {s.text}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QSplitter::handle {{ background-color: {s.card_border}; }}
    """
