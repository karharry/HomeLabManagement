"""Small reusable widgets shared by every page. Mirrors App/Components.swift.

No charting library dependency (keeps the portable exe smaller) — Sparkline is a plain QPainter
widget, and UsageBar is a thin QProgressBar wrapper. Colors are pulled from `theme.current()` at
paint/style time (not cached) so every instance picks up a system dark/light change automatically
once `theme.apply_theme()` re-polishes the app.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient, QFont
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
                                 QSizePolicy)

import theme


@contextmanager
def preserve_scroll(list_widget):
    """Use as `with preserve_scroll(self.list): self.list.clear(); ...repopulate...`.

    QListWidget/QTableWidget's clear() resets the viewport scroll position to the top, which is
    jarring on the many pages here that periodically clear-and-rebuild their list on every poll —
    the user loses their scroll position every few seconds while reading further down a long list
    (e.g. TrueNAS Apps, Windows Sensors)."""
    bar = list_widget.verticalScrollBar()
    pos = bar.value()
    try:
        yield
    finally:
        bar.setValue(min(pos, bar.maximum()))

# Status-semantic colors (Apple system-color equivalents): kept fixed across themes since they're
# chosen to read clearly on both light and dark backgrounds, unlike plain gray/accent text.
GREEN = QColor("#30D158")
ORANGE = QColor("#FF9F0A")
RED = QColor("#FF453A")
GRAY = QColor("#8E8E93")
YELLOW = QColor("#FFD60A")


def accent_color() -> QColor:
    return QColor(theme.current().accent)


def usage_color(fraction: float) -> QColor:
    if fraction >= 0.95:
        return RED
    if fraction >= 0.85:
        return ORANGE
    return accent_color()


class Card(QFrame):
    """A titled rounded panel; add arbitrary widgets to `.body` layout."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._title_label: Optional[QLabel] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)
        if title:
            self._title_label = QLabel(title)
            outer.addWidget(self._title_label)
        self.body = QVBoxLayout()
        self.body.setSpacing(4)
        outer.addLayout(self.body)
        self._apply_theme()
        # Qt automatically drops this connection when the Card (the receiver) is destroyed.
        theme.ThemeManager.instance().changed.connect(self._apply_theme)

    def _apply_theme(self):
        s = theme.current()
        self.setStyleSheet(theme.card_qss(10))
        if self._title_label is not None:
            self._title_label.setStyleSheet(f"color: {s.text_secondary}; font-size: 13px; border: none;")

    def add(self, widget: QWidget):
        self.body.addWidget(widget)
        return widget


class BigStat(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        f = QFont()
        f.setPointSize(20)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)


class MeterCard(Card):
    def __init__(self, title: str, value: Optional[float], caption: str = "", parent=None):
        super().__init__(title, parent)
        self.value_label = BigStat()
        self.add(self.value_label)
        self.bar = UsageBar()
        self.add(self.bar)
        self.caption_label = QLabel()
        self.caption_label.setWordWrap(True)
        self.add(self.caption_label)
        theme.ThemeManager.instance().changed.connect(self._style_caption)
        self._style_caption()
        self.set_value(value, caption)

    def _style_caption(self):
        s = theme.current()
        self.caption_label.setStyleSheet(f"color: {s.text_secondary}; font-size: 13px; border: none;")

    def set_value(self, value: Optional[float], caption: str = ""):
        from core import Fmt
        self.value_label.setText(Fmt.percent(value))
        self.bar.set_fraction((value or 0) / 100)
        self.caption_label.setText(caption)


class UsageBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 1000)
        self.setTextVisible(False)
        self.setFixedHeight(6)
        self._fraction = 0.0
        theme.ThemeManager.instance().changed.connect(lambda: self.set_fraction(self._fraction))
        self.set_fraction(0)

    def set_fraction(self, fraction: float):
        self._fraction = max(0.0, min(1.0, fraction))
        self.setValue(int(self._fraction * 1000))
        s = theme.current()
        color = usage_color(self._fraction)
        self.setStyleSheet(
            f"QProgressBar {{ background: {s.track}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {color.name()}; border-radius: 3px; }}")


class StatRow(QWidget):
    def __init__(self, label: str, value: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.label = QLabel(label)
        self.value = QLabel(value)
        lay.addWidget(self.label)
        lay.addStretch(1)
        lay.addWidget(self.value)
        theme.ThemeManager.instance().changed.connect(self._apply_theme)
        self._apply_theme()

    def _apply_theme(self):
        self.label.setStyleSheet(f"color: {theme.current().text_secondary};")

    def set_value(self, v: str):
        self.value.setText(v)


class StatusDot(QWidget):
    COLORS = {"connected": GREEN, "connecting": YELLOW, "disconnected": GRAY, "failed": RED}

    def __init__(self, state: str = "disconnected", parent=None):
        super().__init__(parent)
        self._state = state
        self.setFixedSize(10, 10)

    def set_state(self, state: str):
        self._state = state
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = self.COLORS.get(self._state, GRAY)
        p.setBrush(QBrush(color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(self.rect())


class Sparkline(QWidget):
    def __init__(self, values: Optional[list] = None, max_value: Optional[float] = None,
                 height: int = 50, color: Optional[QColor] = None, parent=None):
        super().__init__(parent)
        self._values = values or []
        self._max_value = max_value
        self._explicit_color = color
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        theme.ThemeManager.instance().changed.connect(self.update)

    def set_values(self, values: list, max_value: Optional[float] = None):
        self._values = values
        if max_value is not None:
            self._max_value = max_value
        self.update()

    def set_color(self, color: QColor):
        self._explicit_color = color
        self.update()

    def _line_color(self) -> QColor:
        return self._explicit_color if self._explicit_color is not None else accent_color()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        line_color = self._line_color()
        if len(self._values) < 2:
            p.setPen(QPen(QColor(theme.current().card_border)))
            p.drawLine(0, h - 1, w, h - 1)
            return
        vmax = self._max_value if self._max_value is not None else max(max(self._values) * 1.2, 1)
        vmax = max(vmax, 1e-9)
        n = len(self._values)
        step = w / (n - 1)
        points = [QPointF(i * step, h - (v / vmax) * (h - 4) - 2) for i, v in enumerate(self._values)]

        grad = QLinearGradient(0, 0, 0, h)
        c = QColor(line_color)
        c.setAlpha(70)
        grad.setColorAt(0, c)
        c2 = QColor(line_color)
        c2.setAlpha(0)
        grad.setColorAt(1, c2)
        area = [QPointF(0, h)] + points + [QPointF(w, h)]
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        from PySide6.QtGui import QPolygonF
        p.drawPolygon(QPolygonF(area))

        p.setPen(QPen(line_color, 1.6))
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)
        p.drawPath(path)


def h_stack(*widgets, spacing=8) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for item in widgets:
        if isinstance(item, QWidget):
            lay.addWidget(item)
        else:
            lay.addStretch(1)
    return w


def v_stack(*widgets, spacing=6) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for item in widgets:
        lay.addWidget(item)
    return w
