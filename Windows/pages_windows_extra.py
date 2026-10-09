"""Windows Startup Items / Scheduled Tasks / Installed Software / Updates / Event Log pages.

Mirrors App/Pages/WindowsLongTailPages.swift.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                                 QWidget, QPushButton, QMenu, QTableWidget, QTableWidgetItem, QHeaderView,
                                 QAbstractItemView)

from page_base import SessionPage
from widgets_common import Card


class WindowsStartupPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.refresh_startup()

    def _on_updated(self, topic):
        if topic == "startup":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        if not self.session.startup_loaded:
            self.list.addItem("Loading startup items…")
            return
        for item in self.session.startup_items:
            row = QListWidgetItem()
            w = QWidget()
            h = QHBoxLayout(w)
            dot = QLabel("⏸" if item.disabled else "✅")
            h.addWidget(dot)
            detail = f"{item.source_label} · {item.scope}" + (f" · {item.publisher}" if item.publisher else "")
            h.addWidget(QLabel(f"{item.name}\n{detail}"), 1)
            btn = QPushButton("Enable" if item.disabled else "Disable")
            btn.clicked.connect(lambda _, it=item: self.session.set_startup(it, not it.disabled))
            h.addWidget(btn)
            row.setSizeHint(w.sizeHint())
            self.list.addItem(row)
            self.list.setItemWidget(row, w)


class WindowsScheduledTasksPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.refresh_scheduled_tasks()

    def _on_updated(self, topic):
        if topic == "scheduledTasks":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        for t in self.session.scheduled_tasks:
            row = QListWidgetItem()
            w = QWidget()
            h = QHBoxLayout(w)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {'green' if t.state in ('Ready', 'Running') else 'gray'};")
            h.addWidget(dot)
            h.addWidget(QLabel(f"{t.name}\n{t.path}"), 1)
            menu_btn = QPushButton("⋯")
            menu_btn.setFixedWidth(28)
            menu = QMenu(menu_btn)
            menu.addAction("Run Now", lambda tt=t: self.session.scheduled_task_action(tt, "run"))
            if t.state == "Disabled":
                menu.addAction("Enable", lambda tt=t: self.session.scheduled_task_action(tt, "enable"))
            else:
                menu.addAction("Disable", lambda tt=t: self.session.scheduled_task_action(tt, "disable"))
            menu_btn.setMenu(menu)
            h.addWidget(menu_btn)
            row.setSizeHint(w.sizeHint())
            self.list.addItem(row)
            self.list.setItemWidget(row, w)


class WindowsSoftwarePage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter…")
        self.filter_edit.textChanged.connect(self._refresh)
        lay.addWidget(self.filter_edit)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Version", "Publisher"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)
        session.refresh_software()

    def _on_updated(self, topic):
        if topic == "software":
            self._refresh()

    def _refresh(self):
        filt = self.filter_edit.text().lower()
        rows = [s for s in self.session.software if not filt or filt in s.name.lower()]
        self.table.setRowCount(len(rows))
        for i, s in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(s.name))
            self.table.setItem(i, 1, QTableWidgetItem(s.version))
            self.table.setItem(i, 2, QTableWidgetItem(s.publisher))


class WindowsUpdatesPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.hotfix_card = Card("Installed hotfixes")
        self.hotfix_value = QLabel("–")
        self.hotfix_card.add(self.hotfix_value)
        self.recent_card = Card("Most recent")
        self.recent_value = QLabel("–")
        self.recent_card.add(self.recent_value)
        self.wu_card = Card("Windows Update service")
        self.wu_value = QLabel("–")
        self.wu_card.add(self.wu_value)
        row.addWidget(self.hotfix_card)
        row.addWidget(self.recent_card)
        row.addWidget(self.wu_card)
        lay.addLayout(row)
        note = QLabel("Installing updates remotely over SSH isn't offered here — use Windows Update in "
                       "Settings on the machine itself.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 13px;")
        lay.addWidget(note)
        lay.addStretch(1)
        session.refresh_windows_updates()

    def _on_updated(self, topic):
        if topic == "updates":
            self._refresh()

    def _refresh(self):
        s = self.session
        if s.hotfix_count is not None:
            self.hotfix_value.setText(str(s.hotfix_count))
        self.recent_value.setText(s.hotfix_last_installed or "–")
        running = s.wuauserv_running
        self.wu_value.setText("Running" if running else "Stopped")
        self.wu_value.setStyleSheet("color: green;" if running else "")


class WindowsEventLogPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.log_edit = QLineEdit()
        self.log_edit.setPlaceholderText("Log name (default: Application, System)")
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self._reload)
        row.addWidget(self.log_edit, 1)
        row.addWidget(reload_btn)
        lay.addLayout(row)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self._reload()

    def _reload(self):
        self.session.refresh_event_log(filter=self.log_edit.text())

    def _on_updated(self, topic):
        if topic == "eventLog":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        colors = {"Error": "red", "Warning": "orange"}
        for e in self.session.event_log:
            self.list.addItem(f"[{e.level}] {e.message}\n{e.time} · {e.log}")
