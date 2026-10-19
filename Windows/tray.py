"""System tray icon: the closest Windows analog to the Mac app's WidgetKit desktop widgets.

There's no Windows equivalent of a WidgetKit widget (the Windows 11 widgets board has no public API
for third-party live tiles), so this substitutes a tray icon whose tooltip/menu shows each machine's
status at a glance and jumps to it on click — flagged here, not silently dropped.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from core import store, ConnectionState


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

        self._menu = QMenu()
        self.setContextMenu(self._menu)
        self._machine_actions: dict[str, QAction] = {}
        self._prev_online: dict[str, bool] = {}   # only machines whose first poll has finished
        self._prev_alerts: dict[str, set] = {}
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(15000)
        model.machines_changed.connect(self._refresh)
        self._refresh()

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_window()

    def _show_window(self):
        self.main_window.showNormal()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def _rebuild_menu(self, sessions):
        self._menu.clear()
        self._machine_actions.clear()
        self._menu.addAction("Open HomeLab Management", self._show_window)
        self._menu.addSeparator()
        for s in sessions:
            self._machine_actions[s.id] = self._menu.addAction("", lambda sid=s.id: self._select(sid))
        self._menu.addSeparator()
        self._menu.addAction("Quit", QApplication.quit)

    def _notify(self, sessions):
        """Toasts on offline/online transitions and newly raised alerts (not on startup)."""
        if not store.pref("notifications", True):
            return
        for s in sessions:
            state = s.state.kind
            if state not in (ConnectionState.CONNECTED, ConnectionState.FAILED):
                continue
            online = state == ConnectionState.CONNECTED
            ids = {a.id for a in s.alerts}
            if s.id in self._prev_online:
                if online != self._prev_online[s.id]:
                    self.showMessage(s.display_name, "Back online" if online else f"Went offline: {s.state.message}",
                                     QSystemTrayIcon.Information if online else QSystemTrayIcon.Warning, 6000)
                else:
                    for a in s.alerts:
                        if a.id not in self._prev_alerts[s.id] and a.id != "offline":
                            self.showMessage(f"{s.display_name}: {a.title}", a.detail, QSystemTrayIcon.Warning, 6000)
            self._prev_online[s.id] = online
            self._prev_alerts[s.id] = ids

    def _refresh(self):
        sessions = self.model.ordered_sessions
        if list(self._machine_actions) != [s.id for s in sessions]:
            self._rebuild_menu(sessions)
        self._notify(sessions)
        any_alert = False
        lines = []
        for s in sessions:
            alerts = s.alerts
            any_alert = any_alert or bool(alerts)
            dot = "🟢" if s.is_online else ("🔴" if s.is_configured else "⚪")
            self._machine_actions[s.id].setText(
                f"{dot} {s.display_name}" + (f"  ({len(alerts)} alert(s))" if alerts else ""))
            lines.append(f"{s.display_name}: " + ("online" if s.is_online else "offline")
                          + (f", {len(alerts)} alert(s)" if alerts else ""))
        self.setIcon(_dot_icon(QColor("orange") if any_alert else QColor("green" if sessions else "gray")))
        self.setToolTip("HomeLab Management\n" + ("\n".join(lines) if lines else "No machines yet"))

    def _select(self, machine_id: str):
        self.model.select(machine_id)
        self._show_window()
