"""System tray icon: the closest Windows analog to the Mac app's WidgetKit desktop widgets.

There's no Windows equivalent of a WidgetKit widget (the Windows 11 widgets board has no public API
for third-party live tiles), so this substitutes a tray icon whose tooltip/menu shows each machine's
status at a glance and jumps to it on click — flagged here, not silently dropped.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication


def _dot_icon(color: QColor) -> QIcon:
    pix = QPixmap(32, 32)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(QColor(0, 0, 0, 0))
    p.drawEllipse(4, 4, 24, 24)
    p.end()
    return QIcon(pix)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, model, main_window):
        super().__init__(_dot_icon(QColor("gray")))
        self.model = model
        self.main_window = main_window
        self.setToolTip("HomeLab Management")
        self.activated.connect(self._on_activated)

        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(15000)
        self._refresh()

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_window()

    def _show_window(self):
        self.main_window.showNormal()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def _refresh(self):
        sessions = self.model.ordered_sessions
        menu = QMenu()
        menu.addAction("Open HomeLab Management", self._show_window)
        menu.addSeparator()
        any_alert = False
        lines = []
        for s in sessions:
            alerts = s.alerts
            if alerts:
                any_alert = True
            dot = "\U0001f7e2" if s.is_online else ("\U0001f534" if s.is_configured else "⚪")
            label = f"{dot} {s.display_name}" + (f"  ({len(alerts)} alert(s))" if alerts else "")
            lines.append(f"{s.display_name}: " + ("online" if s.is_online else "offline")
                          + (f", {len(alerts)} alert(s)" if alerts else ""))
            menu.addAction(label, lambda sid=s.id: self._select(sid))
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.quit)
        self.setContextMenu(menu)
        self.setIcon(_dot_icon(QColor("orange") if any_alert else QColor("green" if sessions else "gray")))
        self.setToolTip("HomeLab Management\n" + ("\n".join(lines) if lines else "No machines yet"))

    def _select(self, machine_id: str):
        self.model.select(machine_id)
        self._show_window()
