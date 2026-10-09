""""All Machines" grid, shown when nothing is selected in the sidebar. Mirrors App/OverviewPage.swift."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                                 QPushButton, QMenu, QFrame)

import theme
from core import Fmt
from widgets_common import UsageBar, StatusDot


class MachineCard(QFrame):
    def __init__(self, session, on_open, on_edit, on_remove, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName("Card")
        self.setStyleSheet(theme.card_qss(12))
        theme.ThemeManager.instance().changed.connect(lambda: self.setStyleSheet(theme.card_qss(12)))
        self.setFixedHeight(260)
        lay = QVBoxLayout(self)

        head = QHBoxLayout()
        self.dot = StatusDot(session.state.kind)
        head.addWidget(self.dot)
        self.name_label = QLabel(session.display_name)
        self.name_label.setStyleSheet("font-weight: 600;")
        head.addWidget(self.name_label, 1)
        menu_btn = QPushButton("⋯")
        menu_btn.setFixedWidth(28)
        menu = QMenu(menu_btn)
        menu.addAction("Edit…", lambda: on_edit(session))
        menu.addAction("Remove", lambda: on_remove(session))
        menu_btn.setMenu(menu)
        head.addWidget(menu_btn)
        lay.addLayout(head)

        stats_row = QHBoxLayout()
        self.cpu_label = self._pill("CPU")
        self.ram_label = self._pill("RAM")
        self.gpu_label = self._pill("GPU")
        stats_row.addWidget(self.cpu_label)
        stats_row.addWidget(self.ram_label)
        stats_row.addWidget(self.gpu_label)
        lay.addLayout(stats_row)
        stats_row2 = QHBoxLayout()
        self.net_label = self._pill("Net")
        self.alerts_label = self._pill("Alerts")
        stats_row2.addWidget(self.net_label)
        stats_row2.addWidget(self.alerts_label)
        lay.addLayout(stats_row2)

        storage_title = QLabel("Pools" if session.kind.value == "truenas" else "Storage")
        storage_title.setStyleSheet("color: palette(mid); font-size: 12px;")
        lay.addWidget(storage_title)
        self.storage_rows = []
        for _ in range(2):
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            name_lbl = QLabel("")
            name_lbl.setStyleSheet("font-size: 13px;")
            bar = UsageBar()
            rl.addWidget(name_lbl)
            rl.addWidget(bar)
            lay.addWidget(row)
            self.storage_rows.append((name_lbl, bar))

        lay.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: palette(mid); font-size: 12px;")
        lay.addWidget(self.status_label)
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(lambda: on_open(session))
        lay.addWidget(open_btn)

        self.refresh()

    @staticmethod
    def _pill(label: str) -> QLabel:
        lbl = QLabel(f"–\n{label}")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("font-weight: 600;")
        return lbl

    @staticmethod
    def _set_pill(lbl: QLabel, value: str, label: str):
        lbl.setText(f"{value}\n{label}")

    def refresh(self):
        s = self.session
        self.dot.set_state(s.state.kind)
        self.name_label.setText(s.display_name)
        self._set_pill(self.cpu_label, Fmt.percent(s.cpu_percent), "CPU")
        self._set_pill(self.ram_label, Fmt.percent(s.mem_used_fraction * 100 if s.mem_total > 0 else None), "RAM")
        self._set_pill(self.gpu_label, Fmt.percent(s.gpu_percent), "GPU")
        self._set_pill(self.net_label, Fmt.rate(s.net_total_rate), "Net")
        alerts = s.alerts
        self._set_pill(self.alerts_label, str(len(alerts)), "Alerts")
        for i, (name_lbl, bar) in enumerate(self.storage_rows):
            if i < len(s.storage):
                item = s.storage[i]
                name_lbl.setText(f"{item.title}  {int(item.used_fraction * 100)}%")
                bar.set_fraction(item.used_fraction)
                name_lbl.setVisible(True)
                bar.setVisible(True)
            else:
                name_lbl.setVisible(False)
                bar.setVisible(False)
        if s.is_online:
            self.status_label.setText(f"Up {Fmt.uptime(s.host_uptime)}")
        elif s.is_configured:
            self.status_label.setText("Offline")
        else:
            self.status_label.setText("Needs a password — Edit…")


class OverviewPage(QWidget):
    def __init__(self, model, on_open, on_add, on_edit, on_remove, parent=None):
        super().__init__(parent)
        self.model = model
        self.on_open = on_open
        self.on_add = on_add
        self.on_edit = on_edit
        self.on_remove = on_remove
        root = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addStretch(1)
        add_btn = QPushButton("Add Machine")
        add_btn.clicked.connect(on_add)
        row.addWidget(add_btn)
        root.addLayout(row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        self.inner = QWidget()
        scroll.setWidget(self.inner)
        self.grid = QGridLayout(self.inner)
        self.grid.setSpacing(14)

        self.empty_label = QLabel("No machines yet. Add a TrueNAS, Linux, Windows, or Mac machine to get started.")
        self.empty_label.setAlignment(Qt.AlignCenter)

        self.cards: dict[str, MachineCard] = {}
        self._timer = None
        self._start_timer()
        self.rebuild()

    def _start_timer(self):
        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_all)
        self._timer.start(4000)

    def rebuild(self):
        for c in self.cards.values():
            c.setParent(None)
        self.cards.clear()
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        sessions = self.model.ordered_sessions
        if not sessions:
            self.grid.addWidget(self.empty_label, 0, 0)
            return
        cols = 3
        for i, s in enumerate(sessions):
            card = MachineCard(s, self.on_open, self.on_edit, self.on_remove)
            self.cards[s.id] = card
            self.grid.addWidget(card, i // cols, i % cols)

    def _refresh_all(self):
        for c in self.cards.values():
            c.refresh()
