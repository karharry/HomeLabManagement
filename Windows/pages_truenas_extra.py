"""TrueNAS Datasets & Snapshots / Shares / Network / Data Protection / Users / Updates.

Mirrors App/Pages/TrueNASExtraPages.swift.
"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QWidget

from core import Fmt
from page_base import SessionPage


class TrueNASDatasetsPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Datasets"))
        self.datasets_list = QListWidget()
        lay.addWidget(self.datasets_list, 1)
        lay.addWidget(QLabel("Snapshots (most recent first)"))
        self.snaps_list = QListWidget()
        lay.addWidget(self.snaps_list, 2)
        session.refresh_datasets()

    def _on_updated(self, topic):
        if topic == "datasets":
            self._refresh()

    def _refresh(self):
        self.datasets_list.clear()
        if not self.session.datasets:
            self.datasets_list.addItem("No data yet")
        for d in self.session.datasets:
            detail = " · ".join(x for x in [d.type, d.mountpoint] if x)
            self.datasets_list.addItem(f"{d.name}  —  {Fmt.bytes(d.used)}\n{detail}")
        self.snaps_list.clear()
        if not self.session.snapshots:
            self.snaps_list.addItem("No snapshots")
        for s in self.session.snapshots[:200]:
            self.snaps_list.addItem(f"{s.name}  —  {Fmt.bytes(s.used)}\n{s.dataset}")


class TrueNASSharesPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.refresh_shares()

    def _on_updated(self, topic):
        if topic == "shares":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        if not self.session.shares:
            self.list.addItem("No shares configured")
            return
        for s in self.session.shares:
            name = s.name or s.path
            detail = " · ".join(x for x in [s.path, s.comment] if x)
            self.list.addItem(f"[{s.kind}] {name} {'(enabled)' if s.enabled else '(disabled)'}\n{detail}")


class TrueNASNetworkPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.refresh_network()

    def _on_updated(self, topic):
        if topic == "network":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        for i in self.session.interfaces:
            up = i.link_state.upper() == "LINK_STATE_UP"
            addr = ", ".join(i.addresses) if i.addresses else "No address"
            self.list.addItem(f"{'●' if up else '○'} {i.name}\n{addr}")


class TrueNASDataProtectionPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.refresh_data_protection()

    def _on_updated(self, topic):
        if topic == "dataprotection":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        tasks = self.session.protection_tasks
        if not tasks:
            self.list.addItem("Nothing configured — no snapshot tasks, replication, rsync, cloud sync or "
                               "cron jobs found.")
            return
        groups: dict[str, list] = {}
        for t in tasks:
            groups.setdefault(t.kind, []).append(t)
        for kind in sorted(groups):
            self.list.addItem(f"— {kind} —")
            for t in groups[kind]:
                self.list.addItem(("● " if t.enabled else "○ ") + t.summary)


class TrueNASUpdatesPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.status_label = QLabel()
        self.version_label = QLabel()
        lay.addWidget(self.status_label)
        lay.addWidget(self.version_label)
        lay.addWidget(QLabel("Installing updates remotely isn't offered here — use the TrueNAS web UI for "
                              "update installs, which may require a reboot."))
        lay.addStretch(1)
        session.refresh_update_status()

    def _on_updated(self, topic):
        if topic == "updates":
            status = self.session.update_status or "–"
            self.status_label.setText(f"Status: {status}")
            if self.session.update_version:
                self.version_label.setText(f"Available version: {self.session.update_version}")


class TrueNASUsersPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.refresh_user_accounts()

    def _on_updated(self, topic):
        if topic == "users":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        for u in self.session.user_accounts:
            lock = "\U0001f512 " if u.locked else ""
            detail = " · ".join(x for x in [u.full_name, f"uid {u.uid}"] if x)
            self.list.addItem(f"{lock}{u.username}\n{detail}")
