"""Console / Users / Logs / Updates — pages shared across machine kinds via duck-typed session
methods (ConsoleHosting/UserHosting/LogHosting/UpdateHosting in the Swift app).

Mirrors App/Pages/SharedExtraPages.swift.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
                                 QListWidget, QListWidgetItem, QTextEdit, QScrollArea, QWidget)

from page_base import SessionPage


class ConsolePage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        available = getattr(session, "consoleIsAvailable", True)
        if not available:
            lay.addWidget(QLabel(getattr(session, "consoleUnavailableReason", "Console unavailable")))
            return
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(self._mono_font())
        self.output.setPlaceholderText("Run a command to see its output here.")
        lay.addWidget(self.output, 1)
        row = QHBoxLayout()
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("e.g. uptime")
        self.command_edit.returnPressed.connect(self._run)
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._run)
        row.addWidget(self.command_edit, 1)
        row.addWidget(self.run_btn)
        lay.addLayout(row)
        self.history: list[str] = []
        self.history_row = QHBoxLayout()
        lay.addLayout(self.history_row)

    @staticmethod
    def _mono_font():
        from PySide6.QtGui import QFont
        f = QFont("Consolas")
        f.setPointSize(10)
        return f

    def _run(self):
        cmd = self.command_edit.text().strip()
        if not cmd:
            return
        self.run_btn.setEnabled(False)

        def done(out):
            self.output.setPlainText(out)
            self.run_btn.setEnabled(True)
            if cmd not in self.history:
                self.history.append(cmd)
                btn = QPushButton(cmd)
                btn.clicked.connect(lambda: self.command_edit.setText(cmd))
                self.history_row.addWidget(btn)

        self.session.run_console(cmd, done)


class UsersPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self.start_polling(30000)

    def _tick(self):
        self.session.refresh_users()

    def _on_updated(self, topic):
        if topic == "users":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        users = self.session.users
        if not users:
            self.list.addItem("No active sessions — nobody appears to be logged in right now.")
            return
        for u in users:
            detail = " · ".join(x for x in [u.terminal, u.host, u.loginTime] if x)
            self.list.addItem(f"{u.name}\n{detail}")


class LogsPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter (unit/service name, optional)")
        self.reload_btn = QPushButton("Reload")
        self.reload_btn.clicked.connect(self._reload)
        row.addWidget(self.filter_edit, 1)
        row.addWidget(self.reload_btn)
        lay.addLayout(row)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        from PySide6.QtGui import QFont
        f = QFont("Consolas")
        f.setPointSize(9)
        self.text.setFont(f)
        lay.addWidget(self.text, 1)
        self._reload()

    def _reload(self):
        self.session.refresh_logs(filter=self.filter_edit.text())

    def _on_updated(self, topic):
        if topic == "logs":
            self.text.setPlainText("\n".join(self.session.log_lines))


class UpdatesPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addStretch(1)
        self.upgrade_btn = QPushButton("Upgrade All")
        self.upgrade_btn.clicked.connect(self._upgrade)
        row.addWidget(self.upgrade_btn)
        lay.addLayout(row)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        lay.addWidget(self.result_label)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.session.refresh_updates()

    def _on_updated(self, topic):
        if topic == "updates":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        updates = self.session.updates
        self.upgrade_btn.setEnabled(bool(updates))
        if not getattr(self.session, "updates_checked", False):
            self.list.addItem("Checking for updates…")
            return
        if not updates:
            self.list.addItem("Up to date — no updates are pending.")
            return
        groups: dict[str, list] = {}
        for u in updates:
            groups.setdefault(u.source or "System", []).append(u)
        for source in sorted(groups):
            header = QListWidgetItem(f"— {source} —")
            header.setFlags(header.flags() & ~1)  # not selectable-ish; keep simple
            self.list.addItem(header)
            for item in sorted(groups[source], key=lambda i: i.name.lower()):
                label = f"{item.name}\n{item.newVersion}" + ("  [security]" if item.isSecurity else "")
                self.list.addItem("    " + label)

    def _upgrade(self):
        self.upgrade_btn.setEnabled(False)

        def done(out):
            self.result_label.setText(out)
            self.upgrade_btn.setEnabled(True)

        self.session.upgrade_all(done)
