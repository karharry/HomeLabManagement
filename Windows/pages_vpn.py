"""Generic WireGuard VPN page (Linux / Windows / macOS) + ProtonVPN CLI card (Linux only).

Mirrors App/Pages/VPNPage.swift.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem,
                                 QWidget, QDialog, QLineEdit, QTextEdit, QFileDialog, QMessageBox,
                                 QLineEdit as QLE)

import theme
from core import Fmt
from page_base import SessionPage
from wireguard_ import is_valid_name
from linux_session import LinuxSession


class VPNPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)

        if isinstance(session, LinuxSession) and session.has_protonvpn_cli:
            self.proton_card = ProtonVPNCard(session)
            lay.addWidget(self.proton_card)

        row = QHBoxLayout()
        row.addStretch(1)
        add_btn = QPushButton("Add Tunnel")
        add_btn.clicked.connect(self._add)
        row.addWidget(add_btn)
        lay.addLayout(row)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self.start_polling(10000)

    def _tick(self):
        self.session.refresh_wireguard()

    def _on_updated(self, topic):
        if topic == "wireguard":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        tunnels = self.session.wireguard_tunnels
        if self.session.wireguard_error:
            self.list.addItem(f"⚠ {self.session.wireguard_error}")
        if not tunnels:
            self.list.addItem("No tunnels configured. Add a WireGuard config — your own, or one exported "
                               "from a VPN provider like ProtonVPN or Mullvad.")
            return
        for t in tunnels:
            item = QListWidgetItem()
            w = QWidget()
            lay = QVBoxLayout(w)
            head = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {'green' if t.is_up else 'gray'};")
            head.addWidget(dot)
            head.addWidget(QLabel(t.name))
            head.addStretch(1)
            if t.is_up:
                btn = QPushButton("Disconnect")
                btn.clicked.connect(lambda _, tt=t: self.session.disconnect_wireguard_tunnel(tt.name))
            else:
                btn = QPushButton("Connect")
                btn.clicked.connect(lambda _, tt=t: self.session.connect_wireguard_tunnel(tt.name))
            head.addWidget(btn)
            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda _, tt=t: self._confirm_remove(tt))
            head.addWidget(remove_btn)
            lay.addLayout(head)
            if t.is_up:
                endpoint = t.endpoint or "–"
                allowed = t.allowed_ips or "–"
                detail = QLabel(f"Endpoint: {endpoint}    Allowed IPs: {allowed}\n"
                                 f"Handshake: {t.latest_handshake}    Transfer: ↓ {Fmt.bytes(t.rx_bytes)} "
                                 f"· ↑ {Fmt.bytes(t.tx_bytes)}")
                detail.setStyleSheet("color: palette(mid); font-size: 13px;")
                lay.addWidget(detail)
            item.setSizeHint(w.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, w)

    def _confirm_remove(self, t):
        r = QMessageBox.warning(self, f"Remove “{t.name}”?",
                                 "This deletes the tunnel's configuration from the machine. It can't be undone.",
                                 QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.session.remove_wireguard_tunnel(t.name)

    def _add(self):
        dlg = AddTunnelDialog(self.session, self)
        dlg.exec()


class AddTunnelDialog(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Add WireGuard Tunnel")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Paste a WireGuard config — your own, or exported from a VPN provider's "
                              "dashboard (ProtonVPN, Mullvad, and most others all publish standard WireGuard "
                              "`.conf` files)."))
        self.name_edit = QLE()
        self.name_edit.setPlaceholderText("e.g. protonvpn or home")
        lay.addWidget(QLabel("Tunnel name"))
        lay.addWidget(self.name_edit)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        open_btn = QPushButton("Open file…")
        open_btn.clicked.connect(self._open_file)
        btn_row.addWidget(open_btn)
        lay.addLayout(btn_row)
        self.config_edit = QTextEdit()
        lay.addWidget(self.config_edit, 1)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: red;")
        self.error_label.setWordWrap(True)
        lay.addWidget(self.error_label)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._save)
        actions.addWidget(cancel)
        actions.addWidget(self.add_btn)
        lay.addLayout(actions)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open WireGuard config")
        if path:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                self.config_edit.setPlainText(f.read())
            if not self.name_edit.text():
                import os, re
                base = re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.splitext(os.path.basename(path))[0])
                if base:
                    self.name_edit.setText(base[:40])

    def _save(self):
        name = self.name_edit.text().strip()
        config = self.config_edit.toPlainText()
        if not is_valid_name(name) or not config.strip():
            self.error_label.setText("Enter a valid name and config.")
            return
        self.add_btn.setEnabled(False)

        def done(err):
            self.add_btn.setEnabled(True)
            if err:
                self.error_label.setText(err)
            else:
                self.accept()

        self.session.add_wireguard_tunnel(name, config, done)


class ProtonVPNCard(QWidget):
    """Drives the classic `protonvpn-cli` tool over SSH — best-effort, since ProtonVPN only ships an
    official CLI on Linux and its flags vary by version, so raw output is always shown."""

    def __init__(self, session: LinuxSession, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName("Card")
        self.setStyleSheet(theme.card_qss(10))
        theme.ThemeManager.instance().changed.connect(lambda: self.setStyleSheet(theme.card_qss(10)))
        self.lay = QVBoxLayout(self)
        self.lay.addWidget(QLabel("ProtonVPN CLI"))

        from core import MachineStore
        has_saved_user = bool(MachineStore.proton_user(session.machine.id))

        self.login_row = QWidget()
        lr = QVBoxLayout(self.login_row)
        self.user_edit = QLE(MachineStore.proton_user(session.machine.id))
        self.user_edit.setPlaceholderText("ProtonVPN username")
        self.pass_edit = QLE()
        self.pass_edit.setPlaceholderText("ProtonVPN password")
        self.pass_edit.setEchoMode(QLE.Password)
        login_btn = QPushButton("Log In")
        login_btn.clicked.connect(self._login)
        lr.addWidget(self.user_edit)
        lr.addWidget(self.pass_edit)
        lr.addWidget(login_btn)
        self.lay.addWidget(self.login_row)
        self.login_row.setVisible(not has_saved_user)

        self.action_row = QWidget()
        ar = QHBoxLayout(self.action_row)
        connect_btn = QPushButton("Connect (Fastest)")
        connect_btn.clicked.connect(session.proton_connect_fastest)
        disconnect_btn = QPushButton("Disconnect")
        disconnect_btn.clicked.connect(session.proton_disconnect)
        refresh_btn = QPushButton("Refresh Status")
        refresh_btn.clicked.connect(session.proton_refresh_status)
        change_btn = QPushButton("Change Login")
        change_btn.clicked.connect(lambda: self.login_row.setVisible(True))
        ar.addWidget(connect_btn)
        ar.addWidget(disconnect_btn)
        ar.addWidget(refresh_btn)
        ar.addStretch(1)
        ar.addWidget(change_btn)
        self.lay.addWidget(self.action_row)
        self.action_row.setVisible(has_saved_user)

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(160)
        self.lay.addWidget(self.status_text)

        session.updated.connect(self._on_updated)
        if session.proton_status is None:
            session.proton_refresh_status()
        else:
            self.status_text.setPlainText(session.proton_status)

    def _login(self):
        self.session.proton_login(self.user_edit.text(), self.pass_edit.text())
        self.login_row.setVisible(False)
        self.action_row.setVisible(True)

    def _on_updated(self, topic):
        if topic == "vpn" and self.session.proton_status is not None:
            self.status_text.setPlainText(self.session.proton_status)
