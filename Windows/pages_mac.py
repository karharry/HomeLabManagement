"""macOS Power & Thermal page. Mirrors App/Pages/MacPowerPage.swift."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QWidget

from core import Fmt
from page_base import SessionPage
from widgets_common import Card


class MacPowerPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)

        row = QHBoxLayout()
        self.source_card = Card("Power source")
        self.source_value = QLabel("–")
        self.source_card.add(self.source_value)
        self.cpu_limit_card = Card("CPU speed limit")
        self.cpu_limit_value = QLabel("100%")
        self.cpu_limit_card.add(self.cpu_limit_value)
        self.sched_limit_card = Card("Scheduler limit")
        self.sched_limit_value = QLabel("100%")
        self.sched_limit_card.add(self.sched_limit_value)
        row.addWidget(self.source_card)
        row.addWidget(self.cpu_limit_card)
        row.addWidget(self.sched_limit_card)
        lay.addLayout(row)

        batt_row = QHBoxLayout()
        self.battery_card = Card("Battery")
        self.battery_value = QLabel("–")
        self.battery_card.add(self.battery_value)
        self.charging_label = QLabel("")
        self.battery_card.add(self.charging_label)
        self.cycles_card = Card("Cycle count")
        self.cycles_value = QLabel("–")
        self.cycles_card.add(self.cycles_value)
        self.health_card = Card("Battery health")
        self.health_value = QLabel("–")
        self.health_card.add(self.health_value)
        batt_row.addWidget(self.battery_card)
        batt_row.addWidget(self.cycles_card)
        batt_row.addWidget(self.health_card)
        self.batt_row_widget = QWidget()
        self.batt_row_widget.setLayout(batt_row)
        lay.addWidget(self.batt_row_widget)
        self.no_battery_label = QLabel("No battery — this looks like a desktop Mac.")
        self.no_battery_label.setStyleSheet("color: palette(mid); font-size: 13px;")
        lay.addWidget(self.no_battery_label)

        note = QLabel("Detailed component temperatures (CPU/SSD/fans) need `powermetrics`, which requires "
                       "elevated access, so it isn't polled here.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 13px;")
        lay.addWidget(note)
        lay.addStretch(1)
        self.start_polling(15000)

    def _tick(self):
        self.session.refresh_power_thermal()

    def _on_updated(self, topic):
        if topic == "power":
            self._refresh()

    def _refresh(self):
        s = self.session
        self.source_value.setText(s.power_source or "–")
        if s.thermal_cpu_speed_limit is not None:
            self.cpu_limit_value.setText(f"{int(s.thermal_cpu_speed_limit)}%")
            self.cpu_limit_value.setStyleSheet("color: orange;" if s.thermal_cpu_speed_limit < 100 else "")
        if s.thermal_scheduler_limit is not None:
            self.sched_limit_value.setText(f"{int(s.thermal_scheduler_limit)}%")
            self.sched_limit_value.setStyleSheet("color: orange;" if s.thermal_scheduler_limit < 100 else "")
        self.batt_row_widget.setVisible(s.has_battery)
        self.no_battery_label.setVisible(not s.has_battery)
        if s.has_battery:
            self.battery_value.setText(Fmt.percent(s.battery_percent))
            self.charging_label.setText("Charging" if s.battery_charging else "Not charging")
            self.cycles_value.setText(str(s.battery_cycles) if s.battery_cycles is not None else "–")
            self.health_value.setText(Fmt.percent(s.battery_health_percent))
