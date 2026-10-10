"""OPNsense Network (interfaces), DHCP Leases, and Static Hosts (Dnsmasq) pages."""
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                                 QWidget, QLineEdit, QPushButton, QMessageBox)

from page_base import SessionPage


class OPNsenseNetworkPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.refresh_network()

    def _on_updated(self, topic):
        if topic in ("network", "poll"):
            self._refresh()

    def _refresh(self):
        self.list.clear()
        if not self.session.interfaces:
            self.list.addItem("No interfaces reported.")
            return
        for i in self.session.interfaces:
            addr = ", ".join(i.addresses) if i.addresses else "No address"
            self.list.addItem(("● " if i.is_up else "○ ") + f"{i.name} ({i.device})\n{i.status} · {addr}")


class OPNsenseDHCPPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self.start_polling(20000)

    def _tick(self):
        self.session.refresh_dhcp_leases()

    def _on_updated(self, topic):
        if topic == "dhcp":
            self._refresh()

    def _refresh(self):
        self.list.clear()
        leases = self.session.dhcp_leases
        if not leases:
            self.list.addItem("No active DHCP leases.")
            return
        for l in leases:
            self.list.addItem(f"{l.ip}  —  {l.hostname}\n{l.mac} · {l.interface}"
                               + (f" · {l.state}" if l.state else ""))


class OPNsenseStaticHostsPage(SessionPage):
    """Services > Dnsmasq DNS & DHCP > Hosts — static DHCP reservations that double as local DNS
    entries when Dnsmasq is the configured DNS/DHCP backend."""

    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: orange;")
        lay.addWidget(self.error_label)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        add_row = QHBoxLayout()
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("Hostname, e.g. nas")
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("Domain (optional)")
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("IP address, e.g. 192.168.1.10")
        self.mac_edit = QLineEdit()
        self.mac_edit.setPlaceholderText("MAC address (optional), e.g. aa:bb:cc:dd:ee:ff")
        self.descr_edit = QLineEdit()
        self.descr_edit.setPlaceholderText("Description (optional)")
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._add)
        for w in (self.host_edit, self.domain_edit, self.ip_edit, self.mac_edit, self.descr_edit):
            add_row.addWidget(w)
        add_row.addWidget(self.add_btn)
        lay.addLayout(add_row)
        hint = QLabel("A MAC address binds this entry as a DHCP reservation (same device always gets "
                       "this IP); leave it blank for a DNS-only host entry.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 12px;")
        lay.addWidget(hint)

        session.refresh_dnsmasq_hosts()

    def _on_updated(self, topic):
        if topic == "dnsmasqhosts":
            self._refresh()

    def _refresh(self):
        self.error_label.setText(self.session.dnsmasq_hosts_error or "")
        self.list.clear()
        hosts = self.session.dnsmasq_hosts
        if not hosts:
            self.list.addItem("No static hosts configured.")
            return
        for h in hosts:
            item = QListWidgetItem()
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(4, 2, 4, 2)
            fqdn = f"{h.host}.{h.domain}" if h.domain else h.host
            detail_bits = [b for b in (h.mac, h.descr) if b]
            label = f"{fqdn}  →  {h.ip}" + (f"\n{' · '.join(detail_bits)}" if detail_bits else "")
            label_widget = QLabel(label)
            label_widget.setWordWrap(True)
            lay.addWidget(label_widget, 1)
            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda _, hh=h: self._confirm_remove(hh))
            lay.addWidget(remove_btn)
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
            _hint = w.sizeHint()
            _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
            item.setSizeHint(_hint)

    def _add(self):
        host, ip = self.host_edit.text().strip(), self.ip_edit.text().strip()
        if not host or not ip:
            self.error_label.setText("Enter both a hostname and an IP address.")
            return
        self.add_btn.setEnabled(False)

        def done(err):
            self.add_btn.setEnabled(True)
            if err:
                self.error_label.setText(err)
            else:
                self.error_label.setText("")
                self.host_edit.clear()
                self.domain_edit.clear()
                self.ip_edit.clear()
                self.mac_edit.clear()
                self.descr_edit.clear()
                self.session.refresh_dnsmasq_hosts()

        self.session.add_dnsmasq_host(host, ip, self.mac_edit.text(), self.domain_edit.text(),
                                       self.descr_edit.text(), done)

    def _confirm_remove(self, h):
        fqdn = f"{h.host}.{h.domain}" if h.domain else h.host
        resp = QMessageBox.warning(self, f"Remove “{fqdn}”?",
                                    f"This removes the static host entry pointing {fqdn} to {h.ip}.",
                                    QMessageBox.Ok | QMessageBox.Cancel)
        if resp == QMessageBox.Ok:
            self.session.remove_dnsmasq_host(h, lambda err: self.session.refresh_dnsmasq_hosts())
