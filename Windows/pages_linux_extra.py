"""Pi-hole / PiKVM / Tailscale pages. Mirrors App/Pages/LinuxExtrasPages.swift (ContainersPage lives
in pages_truenas.py since it's shared with macOS)."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QMenu,
                                 QWidget, QLineEdit, QListWidgetItem, QMessageBox)

from page_base import SessionPage
from widgets_common import Card, preserve_scroll


class PiholePage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: orange;")
        lay.addWidget(self.error_label)

        row = QHBoxLayout()
        self.blocking_card = Card("Blocking")
        self.blocking_value = QLabel("–")
        self.blocking_card.add(self.blocking_value)
        self.toggle_btn = QPushButton("Enable")
        self.toggle_btn.clicked.connect(self._toggle)
        self.blocking_card.add(self.toggle_btn)
        self.queries_card = Card("Queries today")
        self.queries_value = QLabel("–")
        self.queries_card.add(self.queries_value)
        self.blocked_card = Card("Blocked today")
        self.blocked_value = QLabel("–")
        self.blocked_card.add(self.blocked_value)
        self.pct_card = Card("% blocked")
        self.pct_value = QLabel("–")
        self.pct_card.add(self.pct_value)
        for c in (self.blocking_card, self.queries_card, self.blocked_card, self.pct_card):
            row.addWidget(c)
        lay.addLayout(row)

        self.domains_label = QLabel()
        lay.addWidget(self.domains_label)

        link_btn = QPushButton("Open Pi-hole admin UI")
        link_btn.clicked.connect(self._open_admin)
        lay.addWidget(link_btn)
        lay.addStretch(1)
        self.start_polling(15000)

    def _tick(self):
        self.session.refresh_pihole()

    def _on_updated(self, topic):
        if topic == "pihole":
            self._refresh()

    def _refresh(self):
        self.error_label.setText(self.session.pihole_error or "")
        s = self.session.pihole_status
        if not s:
            return
        self.blocking_value.setText("Enabled" if s.enabled else "Disabled")
        self.blocking_value.setStyleSheet(f"color: {'green' if s.enabled else 'red'}; font-size: 20px; font-weight: 600;")
        self.toggle_btn.setText("Disable" if s.enabled else "Enable")
        if s.queriesToday is not None:
            self.queries_value.setText(str(s.queriesToday))
        if s.blockedToday is not None:
            self.blocked_value.setText(str(s.blockedToday))
        if s.percentBlocked is not None:
            self.pct_value.setText(f"{s.percentBlocked:.1f}%")
        if s.domainsOnBlocklist is not None:
            self.domains_label.setText(f"{s.domainsOnBlocklist} domains on the blocklist")

    def _toggle(self):
        s = self.session.pihole_status
        if s is None:
            return
        if s.enabled:
            menu = QMenu(self)
            menu.addAction("For 30 seconds", lambda: self.session.pihole_set_enabled(False, "30s"))
            menu.addAction("For 5 minutes", lambda: self.session.pihole_set_enabled(False, "5m"))
            menu.addAction("Indefinitely", lambda: self.session.pihole_set_enabled(False, None))
            menu.exec(self.toggle_btn.mapToGlobal(self.toggle_btn.rect().bottomLeft()))
        else:
            self.session.pihole_set_enabled(True)

    def _open_admin(self):
        url = self.session.machine.webUrl or f"http://{self.session.machine.host}/admin/"
        QDesktopServices.openUrl(QUrl(url))


class LocalDNSPage(SessionPage):
    """Pi-hole's "Local DNS Records" — a quick stats recap up top, then the add/remove list."""

    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)

        stats_row = QHBoxLayout()
        self.blocking_card = Card("Blocking")
        self.blocking_value = QLabel("–")
        self.blocking_card.add(self.blocking_value)
        self.queries_card = Card("Queries today")
        self.queries_value = QLabel("–")
        self.queries_card.add(self.queries_value)
        self.blocked_card = Card("Blocked today")
        self.blocked_value = QLabel("–")
        self.blocked_card.add(self.blocked_value)
        for c in (self.blocking_card, self.queries_card, self.blocked_card):
            stats_row.addWidget(c)
        lay.addLayout(stats_row)

        lay.addWidget(QLabel("Local DNS Records"))
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: orange;")
        lay.addWidget(self.error_label)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        add_row = QHBoxLayout()
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("Domain, e.g. nas.lan")
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("IP address, e.g. 192.168.1.10")
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._add)
        add_row.addWidget(self.domain_edit)
        add_row.addWidget(self.ip_edit)
        add_row.addWidget(self.add_btn)
        lay.addLayout(add_row)

        self.start_polling(15000)

    def _tick(self):
        self.session.refresh_pihole()
        self.session.refresh_local_dns()

    def _on_updated(self, topic):
        if topic == "pihole":
            self._refresh_stats()
        if topic == "localdns":
            self._refresh_records()

    def _refresh_stats(self):
        s = self.session.pihole_status
        if not s:
            return
        self.blocking_value.setText("Enabled" if s.enabled else "Disabled")
        self.blocking_value.setStyleSheet(f"color: {'green' if s.enabled else 'red'}; font-weight: 600;")
        if s.queriesToday is not None:
            self.queries_value.setText(str(s.queriesToday))
        if s.blockedToday is not None:
            self.blocked_value.setText(str(s.blockedToday))

    def _refresh_records(self):
        self.error_label.setText(self.session.local_dns_error or "")
        with preserve_scroll(self.list):
            self.list.clear()
            records = self.session.local_dns_records
            if not records:
                self.list.addItem("No local DNS records configured.")
                return
            for r in records:
                item = QListWidgetItem()
                w = QWidget()
                h = QHBoxLayout(w)
                h.setContentsMargins(4, 2, 4, 2)
                h.addWidget(QLabel(f"{r.domain}  →  {r.ip}"), 1)
                remove_btn = QPushButton("Remove")
                remove_btn.clicked.connect(lambda _, rr=r: self._confirm_remove(rr))
                h.addWidget(remove_btn)
                self.list.addItem(item)
                self.list.setItemWidget(item, w)
                _hint = w.sizeHint()
                _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
                item.setSizeHint(_hint)

    def _add(self):
        domain, ip = self.domain_edit.text().strip(), self.ip_edit.text().strip()
        if not domain or not ip:
            self.error_label.setText("Enter both a domain and an IP address.")
            return
        self.add_btn.setEnabled(False)

        def done(err):
            self.add_btn.setEnabled(True)
            if err:
                self.error_label.setText(err)
            else:
                self.error_label.setText("")
                self.domain_edit.clear()
                self.ip_edit.clear()
                self.session.refresh_local_dns()

        self.session.add_local_dns(domain, ip, done)

    def _confirm_remove(self, r):
        resp = QMessageBox.warning(self, f"Remove “{r.domain}”?",
                                    f"This removes the local DNS record pointing {r.domain} to {r.ip}.",
                                    QMessageBox.Ok | QMessageBox.Cancel)
        if resp == QMessageBox.Ok:
            self.session.remove_local_dns(r.domain, lambda err: self.session.refresh_local_dns())


class PiKVMPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.service_card = Card("kvmd service")
        self.service_value = QLabel("–")
        self.service_card.add(self.service_value)
        restart_btn = QPushButton("Restart kvmd")
        restart_btn.clicked.connect(self.session.pikvm_restart)
        self.service_card.add(restart_btn)
        self.root_card = Card("Root filesystem")
        self.root_value = QLabel("–")
        self.root_card.add(self.root_value)
        rw_row = QHBoxLayout()
        rw_btn = QPushButton("Remount rw")
        rw_btn.clicked.connect(lambda: self.session.pikvm_set_writable(True))
        ro_btn = QPushButton("Remount ro")
        ro_btn.clicked.connect(lambda: self.session.pikvm_set_writable(False))
        rw_row.addWidget(rw_btn)
        rw_row.addWidget(ro_btn)
        rw_widget = QWidget()
        rw_widget.setLayout(rw_row)
        self.root_card.add(rw_widget)
        row.addWidget(self.service_card)
        row.addWidget(self.root_card)
        lay.addLayout(row)
        note = QLabel("PiKVM normally runs with a read-only root filesystem to protect the SD card; switch to "
                       "read-write only while making changes, then back to read-only.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 13px;")
        lay.addWidget(note)
        link_btn = QPushButton("Open PiKVM web UI")
        link_btn.clicked.connect(self._open_admin)
        lay.addWidget(link_btn)
        lay.addStretch(1)
        self.start_polling(10000)

    def _tick(self):
        self.session.refresh_pikvm()

    def _on_updated(self, topic):
        if topic == "pikvm":
            self._refresh()

    def _refresh(self):
        s = self.session
        self.service_value.setText("Active" if s.pikvm_active else ("Inactive" if s.pikvm_active is False else "–"))
        self.service_value.setStyleSheet(f"color: {'green' if s.pikvm_active else 'red'}; font-size: 20px; font-weight: 600;")
        self.root_value.setText("Read-write" if s.pikvm_root_rw else ("Read-only" if s.pikvm_root_rw is False else "–"))
        self.root_value.setStyleSheet(f"color: {'orange' if s.pikvm_root_rw else 'palette(mid)'}; font-size: 20px; font-weight: 600;")

    def _open_admin(self):
        url = self.session.machine.webUrl or f"https://{self.session.machine.host}/"
        QDesktopServices.openUrl(QUrl(url))


class LinuxVMsPage(SessionPage):
    """Generic libvirt/QEMU VM list for non-TrueNAS Linux hosts (e.g. a Debian server running a
    Home Assistant OS VM under KVM). No live resource graphs — just state + start/stop/restart,
    mirroring the simpler TrueNASAppsPage rather than TrueNAS's full VMRow with sparklines."""

    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: orange;")
        lay.addWidget(self.error_label)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.set_vms_active(True)
        self._refresh()

    def hideEvent(self, e):
        self.session.set_vms_active(False)
        super().hideEvent(e)

    def _on_updated(self, topic):
        if topic in ("vms", "poll"):
            self._refresh()

    def _refresh(self):
        self.error_label.setText(self.session.vms_error or "")
        with preserve_scroll(self.list):
            self.list.clear()
            vms = self.session.vms
            if not vms:
                self.list.addItem("No libvirt VMs found on this system.")
                return
            colors = {"running": "green", "paused": "orange", "shut off": "gray"}
            for v in vms:
                item = QListWidgetItem()
                w = QWidget()
                h = QHBoxLayout(w)
                h.setContentsMargins(4, 2, 4, 2)
                dot = QLabel("●")
                dot.setStyleSheet(f"color: {colors.get(v.state, 'red')};")
                h.addWidget(dot)
                mem = f"{v.memory_mib} MiB" if v.memory_mib else "–"
                detail = f"{v.state.capitalize()} · {v.vcpus} vCPU · {mem}" + (" · autostart" if v.autostart else "")
                h.addWidget(QLabel(f"{v.name}\n{detail}"), 1)
                if v.is_running:
                    r = QPushButton("Restart")
                    r.clicked.connect(lambda _, vv=v: self.session.vm_action(vv, "restart"))
                    s = QPushButton("Stop")
                    s.clicked.connect(lambda _, vv=v: self.session.vm_action(vv, "stop"))
                    p = QPushButton("Force Off")
                    p.clicked.connect(lambda _, vv=v: self._confirm_poweroff(vv))
                    h.addWidget(r)
                    h.addWidget(s)
                    h.addWidget(p)
                else:
                    st = QPushButton("Start")
                    st.clicked.connect(lambda _, vv=v: self.session.vm_action(vv, "start"))
                    h.addWidget(st)
                self.list.addItem(item)
                self.list.setItemWidget(item, w)
                _hint = w.sizeHint()
                _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
                item.setSizeHint(_hint)

    def _confirm_poweroff(self, v):
        resp = QMessageBox.warning(self, f"Force off “{v.name}”?",
                                    "This is the equivalent of pulling the plug — unsaved state in the VM will be lost.",
                                    QMessageBox.Ok | QMessageBox.Cancel)
        if resp == QMessageBox.Ok:
            self.session.vm_action(v, "poweroff")


class TailscalePage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addStretch(1)
        admin_btn = QPushButton("Admin Console")
        admin_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://login.tailscale.com/admin/machines")))
        row.addWidget(admin_btn)
        lay.addLayout(row)
        self.status_label = QLabel()
        lay.addWidget(self.status_label)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self.start_polling(20000)

    def _tick(self):
        self.session.refresh_tailscale()

    def _on_updated(self, topic):
        if topic == "tailscale":
            self._refresh()

    def _refresh(self):
        if self.session.tailscale_error:
            self.status_label.setText(f"⚠ {self.session.tailscale_error}")
            self.list.clear()
            return
        s = self.session.tailscale_status
        if not s:
            self.status_label.setText("Loading Tailscale status…")
            return
        self.status_label.setText(("● Running" if s.is_running else f"○ {s.backend_state}"))
        with preserve_scroll(self.list):
            self.list.clear()
            for p in s.peers:
                flags = []
                if p.is_self:
                    flags.append("this machine")
                if p.is_exit_node:
                    flags.append("exit node")
                name = p.host_name + (f" ({', '.join(flags)})" if flags else "")
                detail = f"{p.ip}" + (f" · {p.os}" if p.os else "")
                self.list.addItem(("● " if p.online else "○ ") + f"{name}\n{detail}")
