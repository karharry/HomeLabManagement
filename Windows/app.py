"""Entry point. Mirrors App/HomeLabManagementApp.swift."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

import theme
from app_model import AppModel
from main_window import MainWindow
from tray import TrayIcon
from core import store


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("HomeLab Management")
    theme.apply_theme(app)

    model = AppModel()
    window = MainWindow(model)

    tray = TrayIcon(model, window)
    tray.show()
    window.tray = tray

    model.start_all()

    if store.pref("openMinimized", False):
        window.hide()
    else:
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
