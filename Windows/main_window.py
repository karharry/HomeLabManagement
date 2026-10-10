"""Main window: sidebar (All Machines + per-machine nav) and detail pane. Mirrors App/ContentView.swift."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget,
                                 QTreeWidgetItem, QStackedWidget, QToolBar, QPushButton, QLabel,
                                 QMessageBox, QMenu, QApplication)
from PySide6.QtGui import QAction

from core import NavItem, MachineKind
from app_model import AppModel
from overview_page import OverviewPage
from machine_editor import MachineEditor
import remote_desktop

from pages_core import DashboardPage, StoragePage, ProcessesPage, ServicesPage, AlertsPage
from pages_shared_extra import ConsolePage, UsersPage, LogsPage, UpdatesPage
from pages_truenas import TrueNASAppsPage, TrueNASVMsPage, ContainersPage, TrueNASContainersPage
from pages_truenas_extra import (TrueNASDatasetsPage, TrueNASSharesPage, TrueNASNetworkPage,
                                   TrueNASDataProtectionPage, TrueNASUpdatesPage, TrueNASUsersPage)
from pages_truenas_vpn import TrueNASVPNPage
from pages_truenas_fan import TrueNASFanPage
from pages_linux_extra import PiholePage, PiKVMPage, TailscalePage
from pages_linux_tasks import RaspberryPiPage, LinuxScheduledTasksPage
from pages_windows import WindowsCPUPage, WindowsGPUPage, WindowsSensorsPage
from pages_windows_extra import (WindowsStartupPage, WindowsScheduledTasksPage, WindowsSoftwarePage,
                                   WindowsUpdatesPage, WindowsEventLogPage)
from pages_mac import MacPowerPage
from pages_mac_extra import MacStartupPage, MacSoftwarePage
from pages_vpn import VPNPage
from pages_opnsense import OPNsenseNetworkPage, OPNsenseDHCPPage, OPNsenseStaticHostsPage
from pages_linux_extra import LocalDNSPage

ROLE_KIND = Qt.UserRole
ROLE_MACHINE_ID = Qt.UserRole + 1
ROLE_NAV = Qt.UserRole + 2


def build_page(nav: NavItem, session) -> QWidget:
    kind = session.kind
    if nav == NavItem.dashboard:
        return DashboardPage(session)
    if nav == NavItem.storage:
        return StoragePage(session)
    if nav == NavItem.processes:
        return ProcessesPage(session)
    if nav == NavItem.services:
        return ServicesPage(session)
    if nav == NavItem.alerts:
        return AlertsPage(session)
    if nav == NavItem.apps:
        return TrueNASAppsPage(session)
    if nav == NavItem.vms:
        return TrueNASVMsPage(session)
    if nav == NavItem.containers:
        return TrueNASContainersPage(session) if kind == MachineKind.truenas else ContainersPage(session)
    if nav == NavItem.fan:
        return TrueNASFanPage(session)
    if nav == NavItem.pihole:
        return PiholePage(session)
    if nav == NavItem.pikvm:
        return PiKVMPage(session)
    if nav == NavItem.tailscale:
        return TailscalePage(session)
    if nav == NavItem.cpu:
        return WindowsCPUPage(session)
    if nav == NavItem.gpu:
        return WindowsGPUPage(session)
    if nav == NavItem.sensors:
        return WindowsSensorsPage(session)
    if nav == NavItem.power:
        return MacPowerPage(session)
    if nav == NavItem.console:
        return ConsolePage(session)
    if nav == NavItem.users:
        return TrueNASUsersPage(session) if kind == MachineKind.truenas else UsersPage(session)
    if nav == NavItem.logs:
        return WindowsEventLogPage(session) if kind == MachineKind.windows else LogsPage(session)
    if nav == NavItem.updates:
        if kind == MachineKind.windows:
            return WindowsUpdatesPage(session)
        if kind == MachineKind.truenas:
            return TrueNASUpdatesPage(session)
        return UpdatesPage(session)
    if nav == NavItem.raspberry_pi:
        return RaspberryPiPage(session)
    if nav == NavItem.scheduled_tasks:
        return WindowsScheduledTasksPage(session) if kind == MachineKind.windows else LinuxScheduledTasksPage(session)
    if nav == NavItem.startup:
        return WindowsStartupPage(session) if kind == MachineKind.windows else MacStartupPage(session)
    if nav == NavItem.software:
        return WindowsSoftwarePage(session) if kind == MachineKind.windows else MacSoftwarePage(session)
    if nav == NavItem.datasets:
        return TrueNASDatasetsPage(session)
    if nav == NavItem.shares:
        return TrueNASSharesPage(session)
    if nav == NavItem.network:
        return OPNsenseNetworkPage(session) if kind == MachineKind.opnsense else TrueNASNetworkPage(session)
    if nav == NavItem.data_protection:
        return TrueNASDataProtectionPage(session)
    if nav == NavItem.vpn:
        return TrueNASVPNPage(session) if kind == MachineKind.truenas else VPNPage(session)
    if nav == NavItem.dhcp_leases:
        return OPNsenseDHCPPage(session)
    if nav == NavItem.local_dns:
        return LocalDNSPage(session)
    if nav == NavItem.dnsmasq_hosts:
        return OPNsenseStaticHostsPage(session)
    return QLabel(f"Unimplemented page: {nav.value}")


class MainWindow(QMainWindow):
    def __init__(self, model: AppModel):
        super().__init__()
        self.model = model
        self.setWindowTitle("HomeLab Management")
        self.resize(1180, 760)
        self.tray = None
        self._tray_message_shown = False

        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(220)
        self.tree.setMaximumWidth(340)
        self.tree.itemClicked.connect(self._item_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        splitter.addWidget(self.tree)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QToolBar()
        right_lay.addWidget(self.toolbar)
        self.refresh_action = QAction("Refresh", self)
        self.refresh_action.triggered.connect(self._refresh_selected)
        self.toolbar.addAction(self.refresh_action)
        self.connect_action = QAction("Connect", self)
        self.connect_action.triggered.connect(self._connect_remote)
        self.toolbar.addAction(self.connect_action)
        self.connect_action.setVisible(False)

        self.stack = QStackedWidget()
        right_lay.addWidget(self.stack, 1)
        splitter.addWidget(right)
        splitter.setSizes([260, 920])

        self.overview = OverviewPage(model, self._open_machine, self._add_machine, self._edit_machine,
                                      self._remove_machine)
        self.stack.addWidget(self.overview)

        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")
        file_menu.addAction("Add Machine…", self._add_machine)
        file_menu.addSeparator()
        file_menu.addAction("Preferences…", self._show_preferences)

        self._current_page: Optional[QWidget] = None
        self._current_machine_id: Optional[str] = None
        self._current_nav: Optional[NavItem] = None

        model.machines_changed.connect(self.rebuild_sidebar)
        model.selection_changed.connect(self._on_selection_changed)
        self._connected_sessions: set[str] = set()

        self.rebuild_sidebar()
        if model.selected_id:
            self._show_machine(model.selected_id, NavItem.dashboard)
        else:
            self.stack.setCurrentWidget(self.overview)
            self.toolbar.setVisible(False)

    # -- sidebar --
    def rebuild_sidebar(self):
        self.tree.clear()
        all_item = QTreeWidgetItem(["All Machines"])
        all_item.setData(0, ROLE_KIND, "overview")
        self.tree.addTopLevelItem(all_item)
        for s in self.model.ordered_sessions:
            m_item = QTreeWidgetItem([self._machine_label(s)])
            m_item.setData(0, ROLE_KIND, "machine")
            m_item.setData(0, ROLE_MACHINE_ID, s.id)
            self.tree.addTopLevelItem(m_item)
            if s.id == self.model.selected_id:
                for nav in s.nav_items:
                    nav_item = QTreeWidgetItem([self._nav_label(s, nav)])
                    nav_item.setData(0, ROLE_KIND, "nav")
                    nav_item.setData(0, ROLE_MACHINE_ID, s.id)
                    nav_item.setData(0, ROLE_NAV, nav)
                    m_item.addChild(nav_item)
                m_item.setExpanded(True)
            # Connect once per session (new machines get sessions created after __init__); never
            # disconnect here, since open pages also subscribe to these same signals.
            if s.id not in self._connected_sessions:
                self._connected_sessions.add(s.id)
                s.updated.connect(lambda topic, sid=s.id: self._on_session_updated(sid, topic))
                s.action_error.connect(lambda msg, sid=s.id: self._on_action_error(sid, msg))

    def _machine_label(self, s) -> str:
        dot = {"connected": "\U0001f7e2", "connecting": "\U0001f7e1", "failed": "\U0001f534"}.get(s.state.kind, "⚪")
        return f"{dot} {s.display_name}"

    def _nav_label(self, s, nav: NavItem) -> str:
        if nav == NavItem.alerts and s.alerts:
            return f"{nav.value} ({len(s.alerts)})"
        return nav.value

    def _item_clicked(self, item: QTreeWidgetItem, column: int):
        kind = item.data(0, ROLE_KIND)
        if kind == "overview":
            self.model.select(None)
        elif kind == "machine":
            machine_id = item.data(0, ROLE_MACHINE_ID)
            self.model.select(machine_id)
            item.setExpanded(True)
        elif kind == "nav":
            machine_id = item.data(0, ROLE_MACHINE_ID)
            nav = item.data(0, ROLE_NAV)
            self._show_machine(machine_id, nav)

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None or item.data(0, ROLE_KIND) != "machine":
            return
        machine_id = item.data(0, ROLE_MACHINE_ID)
        session = self.model.sessions.get(machine_id)
        if not session:
            return
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self._edit_machine(session))
        proto = session.kind.remote_desktop_protocol
        if proto:
            label = "Connect via RDP" if proto == "rdp" else "Connect via Screen Sharing"
            menu.addAction(label, lambda: remote_desktop.connect(session.machine))
        menu.addSeparator()
        up_action = menu.addAction("Move Up", lambda: self._move(machine_id, True))
        up_action.setEnabled(self.model.can_move_up(machine_id))
        down_action = menu.addAction("Move Down", lambda: self._move(machine_id, False))
        down_action.setEnabled(self.model.can_move_down(machine_id))
        menu.addSeparator()
        menu.addAction("Remove", lambda: self._remove_machine(session))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _move(self, machine_id, up: bool):
        if up:
            self.model.move_up(machine_id)
        else:
            self.model.move_down(machine_id)
        self.rebuild_sidebar()

    # -- selection / detail --
    def _on_selection_changed(self):
        self.rebuild_sidebar()
        if self.model.selected_id:
            self._show_machine(self.model.selected_id, NavItem.dashboard)
        else:
            self.stack.setCurrentWidget(self.overview)
            self.toolbar.setVisible(False)

    def _show_machine(self, machine_id: str, nav: NavItem):
        session = self.model.sessions.get(machine_id)
        if session is None:
            return
        if self.model.selected_id != machine_id:
            self.model.select(machine_id)
            return  # selection_changed will re-enter with the right default nav

        if self._current_page is not None:
            self.stack.removeWidget(self._current_page)
            self._current_page.deleteLater()
            self._current_page = None

        page = build_page(nav, session)
        self._current_page = page
        self._current_machine_id = machine_id
        self._current_nav = nav
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.setWindowTitle(f"HomeLab Management — {session.display_name}")
        self.toolbar.setVisible(True)

        proto = session.kind.remote_desktop_protocol
        self.connect_action.setVisible(proto is not None)
        self.connect_action.setText("Connect (RDP)" if proto == "rdp" else "Connect (Screen Sharing)")

    def _refresh_selected(self):
        if self._current_machine_id:
            session = self.model.sessions.get(self._current_machine_id)
            if session:
                session.start()

    def _connect_remote(self):
        if self._current_machine_id:
            session = self.model.sessions.get(self._current_machine_id)
            if session:
                remote_desktop.connect(session.machine)

    def _on_session_updated(self, machine_id: str, topic: str):
        if topic == "poll":
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item.data(0, ROLE_MACHINE_ID) == machine_id:
                    session = self.model.sessions.get(machine_id)
                    if session:
                        item.setText(0, self._machine_label(session))
                        for ci in range(item.childCount()):
                            child = item.child(ci)
                            nav = child.data(0, ROLE_NAV)
                            if nav:
                                child.setText(0, self._nav_label(session, nav))
                    break

    def _on_action_error(self, machine_id: str, message: str):
        if machine_id == self._current_machine_id:
            QMessageBox.warning(self, "Action failed", message)

    # -- machine CRUD --
    def _open_machine(self, session):
        self.model.select(session.id)

    def _show_preferences(self):
        from settings_dialog import SettingsDialog
        SettingsDialog(self).exec()

    def _add_machine(self):
        dlg = MachineEditor(self.model, None, self)
        dlg.exec()
        self.overview.rebuild()
        self.rebuild_sidebar()

    def _edit_machine(self, session):
        dlg = MachineEditor(self.model, session.machine, self)
        dlg.exec()
        self.overview.rebuild()
        self.rebuild_sidebar()

    def closeEvent(self, event):
        if self.tray is not None:
            event.ignore()
            self.hide()
            if not self._tray_message_shown:
                self._tray_message_shown = True
                self.tray.showMessage("HomeLab Management", "Still running in the background — "
                                      "click the tray icon to reopen.", msecs=4000)
        else:
            event.accept()

    def _remove_machine(self, session):
        r = QMessageBox.warning(self, f"Remove {session.display_name}?",
                                 "This removes the machine and forgets its stored credentials.",
                                 QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.model.remove(session.id)
            self.overview.rebuild()
            self.rebuild_sidebar()
