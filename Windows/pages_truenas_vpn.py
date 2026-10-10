"""TrueNAS VPN page (WireGuard-in-a-container). Mirrors App/Pages/TrueNASVPNPage.swift."""
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QWidget,
                                 QPushButton, QDialog, QLineEdit, QTextEdit, QFileDialog, QMessageBox)

from core import Fmt
from page_base import SessionPage
from widgets_common import preserve_scroll
from wireguard_ import is_valid_name


class TrueNASVPNPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
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
        self.session.refresh_vpn()

    def _on_updated(self, topic):
        if topic == "vpn":
            self._refresh()

    def _refresh(self):
        with preserve_scroll(self.list):
            self.list.clear()
            if self.session.vpn_error:
                self.list.addItem(f"⚠ {self.session.vpn_error}")
            if not self.session.vpn_containers:
                self.list.addItem("No tunnels configured. Add a WireGuard config — your own, or one exported "
                                   "from a VPN provider like ProtonVPN or Mullvad.")
                return
            for c in self.session.vpn_containers:
                item = QListWidgetItem()
                w = QWidget()
                lay = QVBoxLayout(w)
                head = QHBoxLayout()
                dot = QLabel("●")
                dot.setStyleSheet(f"color: {'green' if c.running else 'gray'};")
                head.addWidget(dot)
                head.addWidget(QLabel(c.name))
                head.addStretch(1)
                if c.running:
                    btn = QPushButton("Stop")
                    btn.clicked.connect(lambda _, cc=c: self.session.stop_vpn_tunnel(cc.name))
                else:
                    btn = QPushButton("Start")
                    btn.clicked.connect(lambda _, cc=c: self.session.start_vpn_tunnel(cc.name))
                head.addWidget(btn)
                remove_btn = QPushButton("Remove")
                remove_btn.clicked.connect(lambda _, cc=c: self._confirm_remove(cc))
                head.addWidget(remove_btn)
                lay.addLayout(head)
                if c.tunnel:
                    t = c.tunnel
                    endpoint = t.endpoint or "–"
                    detail = QLabel(f"Endpoint: {endpoint}    Handshake: {t.latest_handshake}\n"
                                     f"Transfer: ↓ {Fmt.bytes(t.rx_bytes)} · ↑ {Fmt.bytes(t.tx_bytes)}")
                    detail.setStyleSheet("color: palette(mid); font-size: 13px;")
                    lay.addWidget(detail)
                elif c.running:
                    waiting = QLabel("Container is running; waiting for tunnel stats…")
                    waiting.setStyleSheet("color: palette(mid); font-size: 13px;")
                    lay.addWidget(waiting)
                self.list.addItem(item)
                self.list.setItemWidget(item, w)
                _hint = w.sizeHint()
                _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
                item.setSizeHint(_hint)

    def _confirm_remove(self, c):
        r = QMessageBox.warning(self, f"Remove “{c.name}”?",
                                 "This stops and deletes the container and its config. It can't be undone.",
                                 QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.session.remove_vpn_tunnel(c.name)

    def _add(self):
        dlg = AddTrueNASVPNDialog(self.session, self)
        dlg.exec()


class AddTrueNASVPNDialog(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Add VPN Tunnel")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Paste a WireGuard config — your own, or exported from a VPN provider's "
                              "dashboard. This starts a small container that brings the tunnel up; it needs "
                              "SSH enabled on this machine."))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. protonvpn")
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

        self.session.add_vpn_tunnel(name, config, done)
