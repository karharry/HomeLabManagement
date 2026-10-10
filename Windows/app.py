"""Entry point. Mirrors App/HomeLabManagementApp.swift."""
from __future__ import annotations

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import theme
from app_model import AppModel
from main_window import MainWindow
from tray import TrayIcon
from core import store


def _icon_path() -> str:
    """Works both frozen (PyInstaller bundles app_icon.ico into the onefile temp extraction dir,
    pointed to by sys._MEIPASS) and running from source (the file sits beside this script)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "app_icon.ico")


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("HomeLab Management")
    icon_path = _icon_path()
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
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
