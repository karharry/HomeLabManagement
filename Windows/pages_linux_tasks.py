"""Raspberry Pi telemetry + Scheduled Tasks (cron/systemd timers) pages.

Mirrors App/Pages/LinuxRaspberryAndTasksPages.swift.
"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QListWidget

from core import Fmt
from page_base import SessionPage
from widgets_common import Card


class RaspberryPiPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.temp_card = Card("Temperature")
        self.temp_value = QLabel("–")
        self.temp_card.add(self.temp_value)
        self.clock_card = Card("ARM clock")
        self.clock_value = QLabel("–")
        self.clock_card.add(self.clock_value)
        row.addWidget(self.temp_card)
        row.addWidget(self.clock_card)
        lay.addLayout(row)
        self.throttle_card = Card("Throttling")
        self.throttle_list = QListWidget()
        self.throttle_card.add(self.throttle_list)
        lay.addWidget(self.throttle_card)
        lay.addStretch(1)
        self.start_polling(10000)

    def _tick(self):
        self.session.refresh_raspberry_pi()

    def _on_updated(self, topic):
        if topic == "raspi":
            self._refresh()

    def _refresh(self):
        s = self.session
        if s.pi_temp is not None:
            self.temp_value.setText(f"{s.pi_temp:.1f}°C")
            self.temp_value.setStyleSheet("color: orange;" if s.pi_temp >= 75 else "")
        if s.pi_clock_mhz is not None:
            self.clock_value.setText(Fmt.mhz(s.pi_clock_mhz))
        self.throttle_list.clear()
        if not s.pi_throttled:
            self.throttle_list.addItem("✅ Nothing currently active")
        for f in s.pi_throttled:
            self.throttle_list.addItem(f"⚠ {f}")


class LinuxScheduledTasksPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Cron jobs"))
        self.cron_list = QListWidget()
        lay.addWidget(self.cron_list, 1)
        lay.addWidget(QLabel("systemd timers"))
        self.timer_list = QListWidget()
        lay.addWidget(self.timer_list, 1)
        session.refresh_scheduled_tasks()

    def _on_updated(self, topic):
        if topic == "scheduledTasks":
            self._refresh()

    def _refresh(self):
        self.cron_list.clear()
        if not self.session.cron_jobs:
            self.cron_list.addItem("No crontab entries for this user.")
        for j in self.session.cron_jobs:
            self.cron_list.addItem(j)
        self.timer_list.clear()
        if not self.session.timers:
            self.timer_list.addItem("No active timers.")
        for t in self.session.timers:
            self.timer_list.addItem(t)
