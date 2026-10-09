"""Common base for session-bound pages: hooks up the `session.updated` topic signal and an optional
periodic-refresh QTimer (mirrors the Swift pages' `.task { await every(N) { ... } }` pattern)."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget


class SessionPage(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        session.updated.connect(self._on_updated)

    def start_polling(self, interval_ms: int, immediate: bool = True):
        if immediate:
            self._tick()
        self._timer.start(interval_ms)

    def stop_polling(self):
        self._timer.stop()

    def _tick(self):
        """Override: trigger session.refresh_x() (runs on the worker thread)."""

    def _on_updated(self, topic: str):
        """Override: refresh the UI from session state when topic matches what this page shows."""

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
