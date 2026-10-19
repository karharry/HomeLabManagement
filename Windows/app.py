"""Entry point. Mirrors App/HomeLabManagementApp.swift."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading

from PySide6.QtCore import QDir, QLockFile, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

import theme
from app_model import AppModel
from main_window import MainWindow
from tray import TrayIcon
from core import store, app_dir
from version import __version__


def _setup_logging():
    """The exe is windowed, so without a log file every worker-thread exception just vanishes."""
    handler = logging.handlers.RotatingFileHandler(os.path.join(app_dir(), "homelab.log"),
                                                    maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    sys.excepthook = lambda *a: logging.critical("Unhandled exception", exc_info=a)
    threading.excepthook = lambda a: logging.critical(
        "Unhandled exception in thread %s", a.thread and a.thread.name,
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def _icon_path() -> str:
    """Works both frozen (PyInstaller bundles app_icon.ico into the onefile temp extraction dir,
    pointed to by sys._MEIPASS) and running from source (the file sits beside this script)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "app_icon.ico")


def main():
    try:
        _setup_logging()
    except OSError:
        pass  # read-only install folder: run without a log rather than not at all
    app = QApplication(sys.argv)
    # Held for the process lifetime; stale locks from a crash are detected and cleared by Qt.
    lock = QLockFile(QDir.tempPath() + "/HomeLabManagement.lock")
    if not lock.tryLock(0):
        QMessageBox.information(None, "HomeLab Management",
                                "HomeLab Management is already running — look for it in the system tray.")
        return
    logging.info("Starting HomeLab Management %s", __version__)
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
    QTimer.singleShot(5000, lambda: window.check_updates(False))

    if store.pref("openMinimized", False):
        window.hide()
    else:
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
