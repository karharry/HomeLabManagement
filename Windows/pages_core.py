"""Dashboard / Storage / Processes / Services / Alerts — the pages every machine kind shows.

Mirrors App/Pages/DashboardPage.swift, StoragePage.swift, ProcessesPage.swift, ServicesPage.swift,
AlertsPage.swift.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QScrollArea, QWidget,
                                 QPushButton, QMessageBox, QTableWidget, QTableWidgetItem,
                                 QHeaderView, QListWidget, QListWidgetItem, QLineEdit, QMenu,
                                 QAbstractItemView)

from core import Fmt, AlertItem
from page_base import SessionPage
from widgets_common import Card, MeterCard, UsageBar, Sparkline, GREEN, ORANGE, RED, GRAY


class DashboardPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.subtitle_label = QLabel()
        self.subtitle_label.setStyleSheet("color: palette(mid);")
        lay.addWidget(self.title_label)
        lay.addWidget(self.subtitle_label)

        grid = QGridLayout()
        grid.setSpacing(12)
        lay.addLayout(grid)

        self.cpu_card = MeterCard("CPU", None)
        self.mem_card = MeterCard("Memory", None)
        self.gpu_card = MeterCard("GPU", None)
        self.storage_card = Card("Storage")
        self.cpu_hist_card = Card("CPU history")
        self.cpu_sparkline = Sparkline(max_value=100)
        self.cpu_hist_card.add(self.cpu_sparkline)
        self.mem_hist_card = Card("Memory history")
        self.mem_sparkline = Sparkline(max_value=100)
        self.mem_hist_card.add(self.mem_sparkline)
        self.alerts_card = Card("Alerts")
        self.alerts_label = QLabel()
        self.alerts_label.setWordWrap(True)
        self.alerts_card.add(self.alerts_label)

        grid.addWidget(self.cpu_card, 0, 0)
        grid.addWidget(self.mem_card, 0, 1)
        grid.addWidget(self.gpu_card, 0, 2)
        grid.addWidget(self.storage_card, 1, 0)
        grid.addWidget(self.cpu_hist_card, 1, 1)
        grid.addWidget(self.mem_hist_card, 1, 2)
        grid.addWidget(self.alerts_card, 2, 0, 1, 3)

        btn_row = QHBoxLayout()
        self.restart_btn = QPushButton("Restart…")
        self.shutdown_btn = QPushButton("Shut Down…")
        self.restart_btn.clicked.connect(lambda: self._confirm_power("restart"))
        self.shutdown_btn.clicked.connect(lambda: self._confirm_power("shutdown"))
        btn_row.addWidget(self.restart_btn)
        btn_row.addWidget(self.shutdown_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        lay.addStretch(1)

        self.refresh()

    def _confirm_power(self, which: str):
        verb = "Restart" if which == "restart" else "Shut Down"
        r = QMessageBox.warning(self, f"{verb} {self.session.display_name}?",
                                 "Everything running on the machine will be closed. Unsaved work can be lost.",
                                 QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            if which == "restart":
                self.session.reboot()
            else:
                self.session.shutdown()

    def _tick(self):
        pass

    def _on_updated(self, topic):
        if topic == "poll":
            self.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def refresh(self):
        s = self.session
        self.title_label.setText(s.display_name)
        self.subtitle_label.setText(f"{s.host_os or s.kind.label} · up {Fmt.uptime(s.host_uptime)}")
        self.cpu_card.set_value(s.cpu_percent, "")
        self.mem_card.set_value(s.mem_used_fraction * 100 if s.mem_total > 0 else None,
                                 f"{Fmt.bytes(s.mem_used)} of {Fmt.bytes(s.mem_total)}")
        self.gpu_card.setVisible(s.gpu_percent is not None)
        if s.gpu_percent is not None:
            self.gpu_card.set_value(s.gpu_percent, "")

        for i in reversed(range(self.storage_card.body.count())):
            self.storage_card.body.itemAt(i).widget().setParent(None)
        self.storage_card.setVisible(True)
        if not s.storage:
            self.storage_card.add(QLabel("No data yet"))
        for item in s.storage[:3]:
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            head = QHBoxLayout()
            name_lbl = QLabel(item.title)
            pct_lbl = QLabel(f"{int(item.used_fraction * 100)}%")
            pct_lbl.setStyleSheet("color: palette(mid); font-size: 13px;")
            head.addWidget(name_lbl)
            head.addStretch(1)
            head.addWidget(pct_lbl)
            rl.addLayout(head)
            bar = UsageBar()
            bar.set_fraction(item.used_fraction)
            rl.addWidget(bar)
            self.storage_card.add(row)

        self.cpu_sparkline.set_values(s.cpu_history)
        self.mem_sparkline.set_values(s.mem_history)

        alerts = s.alerts
        if not alerts:
            self.alerts_label.setText("✅ All clear")
        else:
            lines = [f"{len(alerts)} item(s) need attention"] + [a.title for a in alerts[:2]]
            self.alerts_label.setText("\n".join(lines))


class StoragePage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        self.inner = QWidget()
        scroll.setWidget(self.inner)
        self.lay = QVBoxLayout(self.inner)
        self.lay.addStretch(1)
        self._on_updated("poll")

    def _on_updated(self, topic):
        if topic not in ("poll", "storage"):
            return
        while self.lay.count() > 1:
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        s = self.session
        from truenas import TrueNASSession
        is_tn = isinstance(s, TrueNASSession)
        title = QLabel("Pools" if is_tn else "Storage")
        title.setStyleSheet("font-weight: 600;")
        self.lay.insertWidget(self.lay.count() - 1, title)
        if not s.storage:
            self.lay.insertWidget(self.lay.count() - 1, QLabel("No data yet"))
        for item in s.storage:
            self.lay.insertWidget(self.lay.count() - 1, self._row(item, is_tn))

    def _row(self, item, is_tn: bool) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        head = QHBoxLayout()
        label = item.title + (f"  {item.subtitle}" if item.subtitle else "")
        head.addWidget(QLabel(label))
        head.addStretch(1)
        head.addWidget(QLabel(f"{Fmt.bytes(item.used)} / {Fmt.bytes(item.size)}"))
        lay.addLayout(head)
        bar = UsageBar()
        bar.set_fraction(item.used_fraction)
        lay.addWidget(bar)
        if item.statusText:
            status = QLabel(item.statusText)
            status.setStyleSheet(f"color: {'red' if not item.healthy else 'gray'}; font-size: 13px;")
            lay.addWidget(status)
        if is_tn:
            btn_row = QHBoxLayout()
            scrub_btn = QPushButton("Scrub")
            stop_btn = QPushButton("Stop Scrub")
            scrub_btn.clicked.connect(lambda: self.session.scrub(item, "START"))
            stop_btn.clicked.connect(lambda: self.session.scrub(item, "STOP"))
            btn_row.addWidget(scrub_btn)
            btn_row.addWidget(stop_btn)
            btn_row.addStretch(1)
            lay.addLayout(btn_row)
        return w


class ProcessesPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["PID", "Name", "User", "CPU", "Memory"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table)
        self.start_polling(3000)

    def _tick(self):
        self.session.refresh_processes()

    def _on_updated(self, topic):
        if topic == "processes":
            self._refresh()

    def _refresh(self):
        rows = sorted(self.session.processes, key=lambda p: -p.cpu)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["PID", "Name", "User", "CPU", "Memory", ""])
        self.table.setRowCount(len(rows))
        for i, p in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(p.pid)))
            self.table.setItem(i, 1, QTableWidgetItem(p.name))
            self.table.setItem(i, 2, QTableWidgetItem(p.user))
            self.table.setItem(i, 3, QTableWidgetItem(Fmt.percent(p.cpu)))
            self.table.setItem(i, 4, QTableWidgetItem(Fmt.bytes(p.mem)))
            btn = QPushButton("Kill")
            btn.clicked.connect(lambda _, pp=p: self.session.kill_process(pp))
            self.table.setCellWidget(i, 5, btn)


class ServicesPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter…")
        self.filter_edit.textChanged.connect(self._refresh)
        lay.addWidget(self.filter_edit)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self.session.refresh_services()

    def _on_updated(self, topic):
        if topic in ("services", "poll"):
            self._refresh()

    def _refresh(self):
        self.list.clear()
        filt = self.filter_edit.text().lower()
        for svc in self.session.services:
            if filt and filt not in svc.displayName.lower():
                continue
            item = QListWidgetItem()
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(4, 2, 4, 2)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {'green' if svc.isRunning else 'gray'};")
            lay.addWidget(dot)
            text = QLabel(f"{svc.displayName}\n" + " · ".join(x for x in [svc.state, svc.startMode] if x))
            lay.addWidget(text, 1)
            if svc.canControl:
                menu_btn = QPushButton("⋯")
                menu_btn.setFixedWidth(28)
                menu = QMenu(menu_btn)
                menu.addAction("Start", lambda s=svc: self.session.service_action(s, "start"))
                menu.addAction("Stop", lambda s=svc: self.session.service_action(s, "stop"))
                menu.addAction("Restart", lambda s=svc: self.session.service_action(s, "restart"))
                menu_btn.setMenu(menu)
                lay.addWidget(menu_btn)
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
            _hint = w.sizeHint()
            _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
            item.setSizeHint(_hint)


class AlertsPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self._refresh()

    def _on_updated(self, topic):
        if topic == "poll":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        alerts = self.session.alerts
        if not alerts:
            self.list.addItem(f"✅ All clear — nothing needs attention on {self.session.display_name}.")
            return
        colors = {AlertItem.CRITICAL: "red", AlertItem.WARNING: "orange", AlertItem.INFO: "#0A84FF"}
        glyphs = {AlertItem.CRITICAL: "✖", AlertItem.WARNING: "⚠", AlertItem.INFO: "ℹ"}
        for a in alerts:
            item = QListWidgetItem()
            w = QWidget()
            lay = QHBoxLayout(w)
            icon = QLabel(glyphs[a.level])
            icon.setStyleSheet(f"color: {colors[a.level]};")
            lay.addWidget(icon)
            text_lay = QVBoxLayout()
            title = QLabel(a.title)
            text_lay.addWidget(title)
            if a.detail:
                detail = QLabel(a.detail)
                detail.setStyleSheet("color: palette(mid); font-size: 13px;")
                text_lay.addWidget(detail)
            lay.addLayout(text_lay, 1)
            from truenas import TrueNASSession
            if isinstance(self.session, TrueNASSession):
                btn = QPushButton("Dismiss")
                btn.clicked.connect(lambda _, aa=a: self.session.dismiss_alert(aa))
                lay.addWidget(btn)
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
            _hint = w.sizeHint()
            _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
            item.setSizeHint(_hint)
