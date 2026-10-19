"""Preferences dialog: Open at Login / Open Minimized. Mirrors App/SettingsPage.swift."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QCheckBox, QLabel, QMessageBox, QFormLayout,
                                 QSpinBox)

from core import store
import prefs_autostart
from version import __version__


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.resize(460, 320)
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
        self.notify_check = QCheckBox("Show notifications when a machine goes offline or raises an alert")
        self.notify_check.setChecked(bool(store.pref("notifications", True)))
        self.notify_check.toggled.connect(lambda v: store.set_pref("notifications", v))
        lay.addWidget(self.notify_check)

        form = QFormLayout()
        for key, label, default, lo, hi, suffix in (
                ("alertStoragePct", "Storage alert at", 85, 50, 99, " % full"),
                ("alertMemPct", "Memory alert at", 92, 50, 99, " % used"),
                ("alertOfflineMinutes", "Offline alert after", 0, 0, 120, " min (0 = immediately)")):
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setSuffix(suffix)
            spin.setValue(int(store.pref(key, default)))
            spin.valueChanged.connect(lambda v, k=key: store.set_pref(k, v))
            form.addRow(label, spin)
        lay.addLayout(form)

        lay.addStretch(1)
        lay.addWidget(QLabel(f"HomeLab Management {__version__}"))

    def _toggle_login(self, enabled: bool):
        if not prefs_autostart.set_enabled(enabled):
            QMessageBox.warning(self, "Couldn't update Login setting",
                                 "Couldn't update the Open at Login setting. You may need to run the app "
                                 "elevated once to grant this registry change.")
            self.login_check.blockSignals(True)
            self.login_check.setChecked(prefs_autostart.is_enabled())
            self.login_check.blockSignals(False)
