"""TrueNAS Apps / VMs / Containers pages. Mirrors App/Pages/TrueNASPages.swift."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QListWidget,
                                 QListWidgetItem, QWidget, QDialog, QLineEdit, QTextEdit, QFileDialog,
                                 QMessageBox, QScrollArea, QProgressBar, QCheckBox)

import theme
from core import Fmt
from page_base import SessionPage
from widgets_common import Card, Sparkline, UsageBar, preserve_scroll


class TrueNASAppsPage(SessionPage):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addStretch(1)
        add_btn = QPushButton("Add App")
        add_btn.clicked.connect(self._install)
        row.addWidget(add_btn)
        lay.addLayout(row)
        self.list = QListWidget()
        lay.addWidget(self.list)
        self._refresh()

    def _on_updated(self, topic):
        if topic in ("apps", "poll"):
            self._refresh()

    def _refresh(self):
        with preserve_scroll(self.list):
            self.list.clear()
            apps = self.session.apps
            if not apps:
                self.list.addItem("No apps installed, or this TrueNAS version doesn't support them.")
                return
            colors = {"RUNNING": "green", "DEPLOYING": "orange", "STOPPED": "gray"}
            for a in apps:
                item = QListWidgetItem()
                w = QWidget()
                h = QHBoxLayout(w)
                dot = QLabel("●")
                dot.setStyleSheet(f"color: {colors.get(a.state, 'red')};")
                h.addWidget(dot)
                detail = f"{a.state.capitalize()}" + (f" · {a.human_version}" if a.human_version else "") + \
                         (" · update available" if a.upgrade_available else "")
                h.addWidget(QLabel(f"{a.name}\n{detail}"), 1)
                if a.is_running:
                    r = QPushButton("Restart")
                    r.clicked.connect(lambda _, aa=a: self.session.app_action(aa, "redeploy"))
                    s = QPushButton("Stop")
                    s.clicked.connect(lambda _, aa=a: self.session.app_action(aa, "stop"))
                    h.addWidget(r)
                    h.addWidget(s)
                elif a.state == "STOPPED":
                    st = QPushButton("Start")
                    st.clicked.connect(lambda _, aa=a: self.session.app_action(aa, "start"))
                    h.addWidget(st)
                self.list.addItem(item)
                self.list.setItemWidget(item, w)
                _hint = w.sizeHint()
                _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
                item.setSizeHint(_hint)

    def _install(self):
        dlg = InstallAppDialog(self.session, self)
        dlg.exec()


class InstallAppDialog(QDialog):
    EXAMPLE = ("services:\n  web:\n    image: nginx:latest\n    restart: unless-stopped\n"
               "    ports:\n      - \"8088:80\"\n")

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Install custom app")
        self.resize(640, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Paste a Docker Compose file, the same as TrueNAS's “Install via YAML”."))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("my-app")
        lay.addWidget(QLabel("Application name"))
        lay.addWidget(self.name_edit)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        example_btn = QPushButton("Insert example")
        example_btn.clicked.connect(lambda: self.yaml_edit.setPlainText(self.EXAMPLE))
        open_btn = QPushButton("Open file…")
        open_btn.clicked.connect(self._open_file)
        btn_row.addWidget(example_btn)
        btn_row.addWidget(open_btn)
        lay.addLayout(btn_row)
        self.yaml_edit = QTextEdit()
        lay.addWidget(self.yaml_edit, 1)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.install_btn = QPushButton("Install")
        self.install_btn.clicked.connect(self._install)
        actions.addWidget(cancel)
        actions.addWidget(self.install_btn)
        lay.addLayout(actions)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Compose file")
        if path:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                self.yaml_edit.setPlainText(f.read())
            if not self.name_edit.text():
                import os, re
                base = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(path))[0].lower()).strip("-")
                if base and base[0].isalpha():
                    self.name_edit.setText(base[:40])

    def _install(self):
        name = self.name_edit.text().strip()
        yaml = self.yaml_edit.toPlainText()
        if not name or not yaml.strip():
            return
        self.install_btn.setEnabled(False)

        def done(ok):
            self.install_btn.setEnabled(True)
            self.status_label.setText(self.session.install_status or "")
            if ok:
                self.accept()

        self.session.install_custom_app(name, yaml, done)


class TrueNASVMsPage(SessionPage):
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
        self._rows: dict[int, "VMRow"] = {}
        session.set_vm_stats_active(True)
        self._refresh()

    def hideEvent(self, e):
        self.session.set_vm_stats_active(False)
        super().hideEvent(e)

    def _on_updated(self, topic):
        if topic in ("vms", "poll"):
            self._refresh()

    def _refresh(self):
        vms = self.session.vms
        if not vms:
            while self.lay.count() > 1:
                item = self.lay.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            self.lay.insertWidget(0, QLabel("No VMs are configured on this system."))
            return
        existing_ids = set(self._rows.keys())
        vm_ids = set()
        for vm in vms:
            vm_ids.add(vm.id)
            if vm.id not in self._rows:
                row = VMRow(self.session, vm)
                self._rows[vm.id] = row
                self.lay.insertWidget(self.lay.count() - 1, row)
            self._rows[vm.id].update_vm(vm)
        for old_id in existing_ids - vm_ids:
            self._rows[old_id].setParent(None)
            del self._rows[old_id]


class VMRow(QWidget):
    def __init__(self, session, vm, parent=None):
        super().__init__(parent)
        self.session = session
        self.vm = vm
        self.setObjectName("Card")
        self.setStyleSheet(theme.card_qss(10))
        theme.ThemeManager.instance().changed.connect(lambda: self.setStyleSheet(theme.card_qss(10)))
        self.lay = QVBoxLayout(self)
        self.header_row = QHBoxLayout()
        self.dot = QLabel("●")
        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-weight: 600;")
        self.detail_label = QLabel()
        self.detail_label.setStyleSheet("color: palette(mid); font-size: 13px;")
        name_col = QVBoxLayout()
        name_col.addWidget(self.name_label)
        name_col.addWidget(self.detail_label)
        self.header_row.addWidget(self.dot)
        self.header_row.addLayout(name_col)
        self.header_row.addStretch(1)
        self.start_btn = QPushButton("Start")
        self.restart_btn = QPushButton("Restart")
        self.stop_btn = QPushButton("Stop")
        self.poweroff_btn = QPushButton("Power Off")
        for b in (self.start_btn, self.restart_btn, self.stop_btn, self.poweroff_btn):
            self.header_row.addWidget(b)
        self.start_btn.clicked.connect(lambda: self.session.vm_action(self.vm, "start"))
        self.restart_btn.clicked.connect(lambda: self._confirm("restart", "Restart"))
        self.stop_btn.clicked.connect(lambda: self._confirm("stop", "Stop"))
        self.poweroff_btn.clicked.connect(lambda: self._confirm("poweroff", "Power Off"))
        self.lay.addLayout(self.header_row)

        self.metrics_grid = QGridLayout()
        self.lay.addLayout(self.metrics_grid)
        self.tiles: dict[str, tuple] = {}
        for i, (key, title, tint) in enumerate([
            ("cpu", "CPU", "#0A84FF"), ("mem", "Memory", "#FF9F0A"), ("rx", "Network in", "#30D158"),
            ("tx", "Network out", "#30B0C7"), ("read", "Disk read", "#BF5AF2"), ("write", "Disk write", "#FF2D55"),
        ]):
            card = Card(title)
            value_lbl = QLabel("–")
            value_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
            card.add(value_lbl)
            spark = Sparkline(height=34)
            spark.set_color_hex = tint
            from PySide6.QtGui import QColor
            spark.set_color(QColor(tint))
            card.add(spark)
            caption_lbl = QLabel(" ")
            caption_lbl.setStyleSheet("color: palette(mid); font-size: 12px;")
            card.add(caption_lbl)
            self.metrics_grid.addWidget(card, i // 3, i % 3)
            self.tiles[key] = (value_lbl, spark, caption_lbl)

        self.disks_label = QLabel()
        self.disks_label.setWordWrap(True)
        self.lay.addWidget(self.disks_label)
        self.update_vm(vm)

    def _confirm(self, action: str, title: str):
        r = QMessageBox.warning(self, f"{title} “{self.vm.name}”?",
                                 "Anything relying on this VM will be interrupted.",
                                 QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.session.vm_action(self.vm, action)

    def update_vm(self, vm):
        self.vm = vm
        self.dot.setStyleSheet(f"color: {'green' if vm.is_running else 'gray'};")
        self.name_label.setText(vm.name)
        mem = Fmt.bytes((vm.memory or 0) * 1_048_576)
        self.detail_label.setText(f"{vm.status.state.capitalize()} · {vm.total_cpus} vCPU · {mem}"
                                   + (" · autostart" if vm.autostart else ""))
        running = vm.is_running
        self.start_btn.setVisible(not running)
        for b in (self.restart_btn, self.stop_btn, self.poweroff_btn):
            b.setVisible(running)

        if not self.session.is_ssh_configured:
            self._set_caption_all("Needs SSH access to the NAS (Edit sheet).")
            return
        live = self.session.vm_live.get(vm.id)
        if not running:
            self._set_caption_all("VM is stopped.")
            return
        if live is None:
            self._set_caption_all("Collecting stats…")
            return
        self._set_tile("cpu", Fmt.percent(live.cpu), f"{live.vcpus} vCPU", live.cpu_history)
        mem_used = Fmt.bytes(live.mem_used) if live.mem_used is not None else "–"
        mem_total = Fmt.bytes(live.mem_total) if live.mem_total is not None else "–"
        self._set_tile("mem", mem_used, f"of {mem_total} allocated", live.mem_history)
        self._set_tile("rx", Fmt.rate(live.rx_rate), ", ".join(live.nics), live.rx_history)
        self._set_tile("tx", Fmt.rate(live.tx_rate), ", ".join(live.nics), live.tx_history)
        self._set_tile("read", Fmt.rate(live.disk_read_rate), "", live.read_history)
        self._set_tile("write", Fmt.rate(live.disk_write_rate), "", live.write_history)

        if live.disks:
            lines = [f"{d.name}: {Fmt.bytes(d.allocation)} / {Fmt.bytes(d.capacity)}" if d.capacity else
                     f"{d.name}: size unavailable" for d in live.disks]
            self.disks_label.setText("Disk usage\n" + "\n".join(lines))
        else:
            self.disks_label.setText("")

    def _set_tile(self, key, value, caption, history):
        value_lbl, spark, caption_lbl = self.tiles[key]
        value_lbl.setText(value)
        spark.set_values(history, max_value=100 if key in ("cpu", "mem") else None)
        caption_lbl.setText(caption or " ")

    def _set_caption_all(self, text):
        for value_lbl, spark, caption_lbl in self.tiles.values():
            caption_lbl.setText(text)


class ContainersPage(SessionPage):
    """Plain Docker containers page (Linux / macOS)."""

    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        session.set_containers_active(True)
        session.load_containers()

    def hideEvent(self, e):
        self.session.set_containers_active(False)
        super().hideEvent(e)

    def _on_updated(self, topic):
        if topic == "containers":
            self._refresh()

    def _refresh(self):
        with preserve_scroll(self.list):
            self.list.clear()
            if self.session.containers_error:
                self.list.addItem(f"⚠ {self.session.containers_error}")
            containers = self.session.containers
            if not containers:
                self.list.addItem("No containers." if self.session.containers_loaded else "Loading containers…")
                return
            for c in containers:
                item = QListWidgetItem()
                w = self._row_widget(c)
                self.list.addItem(item)
                self.list.setItemWidget(item, w)
                _hint = w.sizeHint()
                _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
                item.setSizeHint(_hint)

    def _row_widget(self, c) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {'green' if c.is_running else ('orange' if c.state == 'restarting' else 'gray')};")
        lay.addWidget(dot)
        detail = f"{c.image}\n{c.status}" + (f" · {c.ports}" if c.ports else "")
        if c.cpu and c.is_running:
            detail += f"\nCPU {c.cpu} · RAM {c.memory or '–'}"
        lay.addWidget(QLabel(f"{c.name}\n{detail}"), 1)
        logs_btn = QPushButton("Logs")
        logs_btn.clicked.connect(lambda: self._show_logs(c))
        lay.addWidget(logs_btn)
        if c.is_running:
            restart_btn = QPushButton("Restart")
            restart_btn.clicked.connect(lambda: self._confirm(c, "restart"))
            stop_btn = QPushButton("Stop")
            stop_btn.clicked.connect(lambda: self._confirm(c, "stop"))
            lay.addWidget(restart_btn)
            lay.addWidget(stop_btn)
        else:
            start_btn = QPushButton("Start")
            start_btn.clicked.connect(lambda: self.session.container_action(c, "start"))
            lay.addWidget(start_btn)
        return w

    def _confirm(self, c, verb):
        r = QMessageBox.warning(self, f"{verb.capitalize()} “{c.name}”?",
                                 "Anything relying on this container will be interrupted.",
                                 QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.session.container_action(c, verb)

    def _show_logs(self, c):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Logs · {c.name}")
        dlg.resize(720, 460)
        lay = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        from PySide6.QtGui import QFont
        f = QFont("Consolas")
        f.setPointSize(9)
        text.setFont(f)
        lay.addWidget(text, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        refresh_btn = QPushButton("Refresh")
        done_btn = QPushButton("Done")
        done_btn.clicked.connect(dlg.accept)
        row.addWidget(refresh_btn)
        row.addWidget(done_btn)
        lay.addLayout(row)

        def load():
            self.session.container_logs(c, lambda out: text.setPlainText(out))
        refresh_btn.clicked.connect(load)
        load()
        dlg.exec()


class TrueNASContainersPage(ContainersPage):
    """TrueNAS's containers page additionally flags app-managed containers and can show/hide them."""

    def __init__(self, session, parent=None):
        self._show_managed = False
        super().__init__(session, parent)
        toggle_row = QHBoxLayout()
        toggle_row.addStretch(1)
        self.toggle_btn = QPushButton("Show TrueNAS apps")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.toggled.connect(self._toggle)
        toggle_row.addWidget(self.toggle_btn)
        self.layout().insertLayout(0, toggle_row)

    def _toggle(self, checked):
        self._show_managed = checked
        self._refresh()

    def _refresh(self):
        with preserve_scroll(self.list):
            self.list.clear()
            if not self.session.is_ssh_configured:
                self.list.addItem("SSH required — enable it in the machine's Edit sheet to list containers "
                                   "outside TrueNAS's app system.")
                return
            if self.session.containers_error:
                self.list.addItem(f"⚠ {self.session.containers_error}")
            visible = [c for c in self.session.containers if self._show_managed or c.truenas_app is None]
            if not visible:
                self.list.addItem("No containers." if self.session.containers_loaded else "Loading containers…")
                return
            for c in visible:
                item = QListWidgetItem()
                w = self._row_widget(c)
                if c.truenas_app:
                    lbl = w.layout().itemAt(1).widget()
                    lbl.setText(f"[TrueNAS app: {c.truenas_app}] " + lbl.text())
                self.list.addItem(item)
                self.list.setItemWidget(item, w)
                _hint = w.sizeHint()
                _hint.setHeight(_hint.height() + 10)  # headroom for descenders (g/y/p/j/q) that sizeHint() alone cut off on real Windows DPI scaling
                item.setSizeHint(_hint)
