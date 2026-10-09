"""TrueNAS Fan Control page, including a draggable temp/duty curve editor.

Mirrors App/Pages/TrueNASFanPage.swift. The Swift version uses Swift Charts' drag gesture over chart
coordinates; this is a plain QPainter + mouse-event widget doing the same temp<->pixel mapping.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QWidget,
                                 QGridLayout, QDoubleSpinBox, QComboBox, QSlider, QListWidget,
                                 QListWidgetItem)

from core import Fmt
from fan_client import FanConfig
from page_base import SessionPage
from widgets_common import Card, Sparkline


@dataclass
class CurvePoint:
    temp: float
    pct: float


class CurveEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points: list[CurvePoint] = []
        self.now_temp: Optional[float] = None
        self.setMinimumHeight(220)
        self._dragging: Optional[int] = None
        self._lo, self._hi = 20.0, 70.0

    def set_points(self, points: list[CurvePoint]):
        self.points = sorted(points, key=lambda p: p.temp)
        self._recompute_bounds()
        self.update()

    def _recompute_bounds(self):
        temps = [p.temp for p in self.points] or [20]
        self._lo = min(20, min(temps) - 2)
        self._hi = max(70, max(temps) + 5)

    def _to_px(self, temp, pct):
        w, h = self.width() - 40, self.height() - 30
        x = 30 + (temp - self._lo) / max(self._hi - self._lo, 1e-9) * w
        y = 10 + (1 - pct / 105) * h
        return QPointF(x, y)

    def _from_px(self, x, y):
        w, h = self.width() - 40, self.height() - 30
        temp = self._lo + (x - 30) / max(w, 1) * (self._hi - self._lo)
        pct = (1 - (y - 10) / max(h, 1)) * 105
        return temp, pct

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        grid_pen = QPen(QColor(127, 127, 127, 60))
        p.setPen(grid_pen)
        f = QFont()
        f.setPointSize(8)
        p.setFont(f)
        for pct in (0, 25, 50, 75, 100):
            pt = self._to_px(self._lo, pct)
            p.drawLine(30, int(pt.y()), self.width() - 10, int(pt.y()))
            p.drawText(2, int(pt.y()) + 4, f"{pct}%")
        import math
        step = max(5, round((self._hi - self._lo) / 8 / 5) * 5)
        t = math.ceil(self._lo / step) * step
        while t <= self._hi:
            pt = self._to_px(t, 0)
            p.drawText(int(pt.x()) - 8, self.height() - 2, f"{int(t)}°")
            t += step

        if self.now_temp is not None:
            p.setPen(QPen(QColor("red"), 1, Qt.DashLine))
            pt1 = self._to_px(self.now_temp, 0)
            pt2 = self._to_px(self.now_temp, 105)
            p.drawLine(pt1, pt2)
            p.drawText(int(pt1.x()) + 2, 12, f"{self.now_temp:.0f}°C now")

        if len(self.points) >= 2:
            p.setPen(QPen(QColor("#0A84FF"), 2))
            pts = [self._to_px(pt.temp, pt.pct) for pt in self.points]
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
        p.setBrush(QColor("#0A84FF"))
        p.setPen(Qt.NoPen)
        for i, pt in enumerate(self.points):
            r = 6 if i == self._dragging else 4
            center = self._to_px(pt.temp, pt.pct)
            p.drawEllipse(center, r, r)

    def mousePressEvent(self, e):
        pos = e.position()
        best, best_d = None, 999
        for i, pt in enumerate(self.points):
            c = self._to_px(pt.temp, pt.pct)
            d = ((c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2) ** 0.5
            if d < best_d:
                best, best_d = i, d
        if best is not None and best_d < 24:
            self._dragging = best

    def mouseMoveEvent(self, e):
        if self._dragging is None:
            return
        temp, pct = self._from_px(e.position().x(), e.position().y())
        temp = max(self._lo, min(self._hi, round(temp)))
        pct = max(0, min(100, round(pct)))
        self.points[self._dragging].temp = temp
        self.points[self._dragging].pct = pct
        self.update()

    def mouseReleaseEvent(self, e):
        if self._dragging is not None:
            self._dragging = None
            self.points.sort(key=lambda p: p.temp)
            self.changed.emit()
        self.update()


class TrueNASFanPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        self.draft: Optional[FanConfig] = None
        self._dirty = False

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: orange;")
        self.error_label.setWordWrap(True)
        lay.addWidget(self.error_label)

        readings = QHBoxLayout()
        self.rpm_card = Card("Fan speed")
        self.rpm_value = QLabel("–")
        self.rpm_card.add(self.rpm_value)
        self.rpm_spark = Sparkline(height=34)
        self.rpm_card.add(self.rpm_spark)
        self.duty_card = Card("PWM duty")
        self.duty_value = QLabel("–")
        self.duty_card.add(self.duty_value)
        self.duty_spark = Sparkline(height=34, max_value=100)
        self.duty_card.add(self.duty_spark)
        self.temp_card = Card("Control temp")
        self.temp_value = QLabel("–")
        self.temp_card.add(self.temp_value)
        self.temp_spark = Sparkline(height=34)
        self.temp_card.add(self.temp_spark)
        readings.addWidget(self.rpm_card)
        readings.addWidget(self.duty_card)
        readings.addWidget(self.temp_card)
        lay.addLayout(readings)

        self.mode_card = Card("Mode")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Automatic (curve)", "Manual"])
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.mode_card.add(self.mode_combo)
        manual_row = QHBoxLayout()
        self.manual_slider = QSlider(Qt.Horizontal)
        self.manual_slider.setRange(0, 100)
        self.manual_slider.sliderReleased.connect(self._save)
        self.manual_value_label = QLabel("0%")
        manual_row.addWidget(self.manual_slider)
        manual_row.addWidget(self.manual_value_label)
        self.manual_row_widget = QWidget()
        self.manual_row_widget.setLayout(manual_row)
        self.mode_card.add(self.manual_row_widget)
        self.manual_slider.valueChanged.connect(lambda v: self.manual_value_label.setText(f"{v}%"))
        lay.addWidget(self.mode_card)

        self.curve_card = Card("Fan curve (drag points to edit)")
        self.curve_editor = CurveEditor()
        self.curve_editor.changed.connect(self._curve_changed)
        self.curve_card.add(self.curve_editor)
        curve_btn_row = QHBoxLayout()
        add_pt_btn = QPushButton("Add point")
        add_pt_btn.clicked.connect(self._add_point)
        remove_pt_btn = QPushButton("Remove last point")
        remove_pt_btn.clicked.connect(self._remove_point)
        curve_btn_row.addWidget(add_pt_btn)
        curve_btn_row.addWidget(remove_pt_btn)
        curve_btn_row.addStretch(1)
        curve_row_widget = QWidget()
        curve_row_widget.setLayout(curve_btn_row)
        self.curve_card.add(curve_row_widget)
        lay.addWidget(self.curve_card)

        self.tuning_card = Card("Tuning")
        tuning_grid = QGridLayout()
        self.min_pct_spin = self._tuning_spin(0, 100)
        self.cpu_offset_spin = self._tuning_spin(0, 40)
        self.hysteresis_spin = self._tuning_spin(0, 10)
        tuning_grid.addWidget(QLabel("Minimum speed (auto) %"), 0, 0)
        tuning_grid.addWidget(self.min_pct_spin, 0, 1)
        tuning_grid.addWidget(QLabel("CPU offset °C"), 1, 0)
        tuning_grid.addWidget(self.cpu_offset_spin, 1, 1)
        tuning_grid.addWidget(QLabel("Hysteresis °C"), 2, 0)
        tuning_grid.addWidget(self.hysteresis_spin, 2, 1)
        tuning_widget = QWidget()
        tuning_widget.setLayout(tuning_grid)
        self.tuning_card.add(tuning_widget)
        lay.addWidget(self.tuning_card)

        save_row = QHBoxLayout()
        self.revert_btn = QPushButton("Revert")
        self.save_btn = QPushButton("Save")
        self.revert_btn.clicked.connect(self._revert)
        self.save_btn.clicked.connect(self._save)
        self.dirty_label = QLabel("")
        self.dirty_label.setStyleSheet("color: orange;")
        save_row.addWidget(self.revert_btn)
        save_row.addWidget(self.save_btn)
        save_row.addWidget(self.dirty_label)
        save_row.addStretch(1)
        lay.addLayout(save_row)

        self.temps_card = Card("Temperatures")
        self.temps_list = QListWidget()
        self.temps_card.add(self.temps_list)
        lay.addWidget(self.temps_card)
        lay.addStretch(1)

        session.set_fan_active(True)
        self._mode_changed()

    def _tuning_spin(self, lo, hi):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.editingFinished.connect(self._mark_dirty)
        return s

    def hideEvent(self, e):
        self.session.set_fan_active(False)
        super().hideEvent(e)

    def _on_updated(self, topic):
        if topic == "fan":
            self._refresh()

    def _refresh(self):
        s = self.session
        self.error_label.setText(s.fan_error or "")
        status = s.fan_status
        if status:
            self.rpm_value.setText(f"{status.rpm} RPM" if status.rpm is not None else "–")
            self.duty_value.setText(f"{int(status.pwm_pct)}%" if status.pwm_pct is not None else "–")
            self.temp_value.setText(f"{status.ctl_temp:.1f}°C" if status.ctl_temp is not None else "–")
            self.curve_editor.now_temp = status.ctl_temp
            self.temps_list.clear()
            for key, t in sorted((status.temps or {}).items(), key=lambda kv: -kv[1].temp):
                marker = "  [controlling]" if key == status.src else ""
                self.temps_list.addItem(f"{key} ({t.kind}): {t.temp:.1f}°C{marker}")
            if status.error:
                self.error_label.setText((self.error_label.text() + "\n" if self.error_label.text() else "")
                                          + f"{status.error} — the controller forces the fan to 100%.")
        self.rpm_spark.set_values(s.fan_rpm_history)
        self.duty_spark.set_values(s.fan_duty_history)
        self.temp_spark.set_values(s.fan_temp_history)

        if s.fan_config is not None and not self._dirty:
            self.draft = FanConfig(**s.fan_config.__dict__)
            self._load_draft_into_ui()

    def _load_draft_into_ui(self):
        if self.draft is None:
            return
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(1 if self.draft.mode == "manual" else 0)
        self.mode_combo.blockSignals(False)
        self.manual_row_widget.setVisible(self.draft.mode == "manual")
        self.manual_slider.blockSignals(True)
        self.manual_slider.setValue(int(self.draft.manual_pct))
        self.manual_slider.blockSignals(False)
        self.manual_value_label.setText(f"{int(self.draft.manual_pct)}%")
        self.min_pct_spin.setValue(self.draft.min_pct)
        self.cpu_offset_spin.setValue(self.draft.cpu_offset)
        self.hysteresis_spin.setValue(self.draft.hysteresis)
        self.curve_editor.set_points([CurvePoint(temp=p[0], pct=p[1]) for p in self.draft.curve])
        self._dirty = False
        self.dirty_label.setText("")

    def _mode_changed(self):
        if self.draft is None:
            return
        self.draft.mode = "manual" if self.mode_combo.currentIndex() == 1 else "auto"
        self.manual_row_widget.setVisible(self.draft.mode == "manual")
        self._mark_dirty()
        self._save()

    def _curve_changed(self):
        self._mark_dirty()
        self._save()

    def _add_point(self):
        if not self.curve_editor.points:
            self.curve_editor.points.append(CurvePoint(temp=50, pct=100))
        else:
            last = max(p.temp for p in self.curve_editor.points)
            self.curve_editor.points.append(CurvePoint(temp=last + 5, pct=100))
        self.curve_editor._recompute_bounds()
        self.curve_editor.update()
        self._mark_dirty()

    def _remove_point(self):
        if len(self.curve_editor.points) > 2:
            self.curve_editor.points.pop()
            self.curve_editor.update()
            self._mark_dirty()

    def _mark_dirty(self):
        self._dirty = True
        self.dirty_label.setText("Unsaved changes")

    def _revert(self):
        self._dirty = False
        if self.session.fan_config is not None:
            self.draft = FanConfig(**self.session.fan_config.__dict__)
        self._load_draft_into_ui()

    def _save(self):
        if self.draft is None:
            return
        self.draft.manual_pct = self.manual_slider.value()
        self.draft.min_pct = self.min_pct_spin.value()
        self.draft.cpu_offset = self.cpu_offset_spin.value()
        self.draft.hysteresis = self.hysteresis_spin.value()
        self.draft.curve = [[p.temp, p.pct] for p in self.curve_editor.points]

        def done(ok):
            if ok:
                self._dirty = False
                self.dirty_label.setText("")

        self.session.save_fan(self.draft, done)
