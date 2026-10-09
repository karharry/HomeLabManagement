"""Preferences dialog: Open at Login / Open Minimized. Mirrors App/SettingsPage.swift."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QCheckBox, QLabel, QMessageBox

from core import store
import prefs_autostart


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.resize(420, 180)
        lay = QVBoxLayout(self)

        self.login_check = QCheckBox("Open at Login")
        self.login_check.setChecked(prefs_autostart.is_enabled())
        self.login_check.toggled.connect(self._toggle_login)
        lay.addWidget(self.login_check)

        self.minimized_check = QCheckBox("Open Minimized")
        self.minimized_check.setChecked(bool(store.pref("openMinimized", False)))
        self.minimized_check.toggled.connect(lambda v: store.set_pref("openMinimized", v))
        lay.addWidget(self.minimized_check)

        note = QLabel("When on, HomeLab Management starts minimized to the system tray instead of "
                       "opening a window right away.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 13px;")
        lay.addWidget(note)
        lay.addStretch(1)

    def _toggle_login(self, enabled: bool):
        if not prefs_autostart.set_enabled(enabled):
            QMessageBox.warning(self, "Couldn't update Login setting",
                                 "Couldn't update the Open at Login setting. You may need to run the app "
                                 "elevated once to grant this registry change.")
            self.login_check.blockSignals(True)
            self.login_check.setChecked(prefs_autostart.is_enabled())
            self.login_check.blockSignals(False)
