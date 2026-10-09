"""Add / Edit Machine dialog. Mirrors App/MachineEditor.swift."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QFormLayout, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
                                 QCheckBox, QSpinBox, QPushButton, QLabel, QGroupBox, QWidget,
                                 QMessageBox, QProgressBar)

from core import Machine, MachineKind, ALL_KINDS, MachineStore, ConnectionState, store
from machine_session import make_session


class _TestWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, session):
        super().__init__()
        self.session = session

    def run(self):
        import time
        self.session._run_poll()
        # one poll is synchronous within this thread call (not the session's own worker thread,
        # since we haven't started() it) — call the kind-specific _poll directly.
        ok = self.session.state.kind == ConnectionState.CONNECTED
        msg = "OK — connected." if ok else (self.session.state.message or "Couldn't connect.")
        try:
            self.session._close_backend()
        except Exception:
            pass
        self.done.emit(ok, msg)


class MachineEditor(QDialog):
    def __init__(self, model, existing: Optional[Machine] = None, parent=None):
        super().__init__(parent)
        self.model = model
        self.existing = existing
        self.machine = Machine(**existing.__dict__) if existing else Machine()
        self.secret = MachineStore.secret(existing.id) if existing else ""
        self.ssh_password = ""
        self.fan_password = MachineStore.fan_password(existing.id) if existing else ""

        self.setWindowTitle("Edit Machine" if existing else "Add Machine")
        self.setMinimumWidth(460)
        self._worker: Optional[_TestWorker] = None

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        root.addLayout(form)

        self.kind_combo = QComboBox()
        for k in ALL_KINDS:
            # Stored as the plain string value, not the enum: PySide6 unwraps a str-Enum's itemData
            # back to a plain str (it looks like a QString to the C++ side), so _current_kind()
            # re-wraps it with MachineKind(...).
            self.kind_combo.addItem(k.label, k.value)
        self.kind_combo.setCurrentIndex(ALL_KINDS.index(self.machine.kind))
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        form.addRow("Kind", self.kind_combo)

        self.name_edit = QLineEdit(self.machine.name)
        self.name_edit.setPlaceholderText("Optional — defaults to the host")
        form.addRow("Name", self.name_edit)

        self.host_edit = QLineEdit(self.machine.host)
        self.host_edit.setPlaceholderText("192.168.1.10 or nas.local")
        form.addRow("Host", self.host_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self.machine.port)
        form.addRow("Port", self.port_spin)

        self._kind_group_box: Optional[QGroupBox] = None
        self.kind_container = QVBoxLayout()
        root.addLayout(self.kind_container)

        # TrueNAS section
        self.tn_group = QGroupBox("TrueNAS API")
        tn_form = QFormLayout(self.tn_group)
        self.use_tls = QCheckBox("Use TLS (wss://)")
        self.use_tls.setChecked(self.machine.useTLS)
        self.allow_self_signed = QCheckBox("Allow self-signed certificate")
        self.allow_self_signed.setChecked(self.machine.allowSelfSigned)
        self.use_password = QCheckBox("Log in with username + password")
        self.use_password.setChecked(self.machine.usePassword)
        self.use_password.toggled.connect(self._update_tn_fields)
        self.tn_user_edit = QLineEdit(self.machine.user)
        self.tn_secret_edit = QLineEdit(self.secret)
        self.tn_secret_edit.setEchoMode(QLineEdit.Password)
        tn_form.addRow(self.use_tls)
        tn_form.addRow(self.allow_self_signed)
        tn_form.addRow(self.use_password)
        tn_form.addRow("Username", self.tn_user_edit)
        tn_form.addRow("Password / API Key", self.tn_secret_edit)

        self.tn_ssh_group = QGroupBox("SSH (optional — for VMs and containers)")
        tn_ssh_form = QFormLayout(self.tn_ssh_group)
        self.ssh_enabled = QCheckBox("Enable SSH")
        self.ssh_enabled.setChecked(self.machine.sshEnabled)
        self.ssh_port_spin = QSpinBox()
        self.ssh_port_spin.setRange(1, 65535)
        self.ssh_port_spin.setValue(self.machine.sshPort)
        self.ssh_user_edit = QLineEdit(self.machine.sshUser)
        self.ssh_password_edit = QLineEdit("")
        self.ssh_password_edit.setEchoMode(QLineEdit.Password)
        self.ssh_sudo = QCheckBox("Use sudo")
        self.ssh_sudo.setChecked(self.machine.sshSudo)
        tn_ssh_form.addRow(self.ssh_enabled)
        tn_ssh_form.addRow("SSH Port", self.ssh_port_spin)
        tn_ssh_form.addRow("SSH User", self.ssh_user_edit)
        tn_ssh_form.addRow("SSH Password", self.ssh_password_edit)
        tn_ssh_form.addRow(self.ssh_sudo)

        self.tn_fan_group = QGroupBox("Fan Control (optional — needs a ‘fanctl’ container)")
        tn_fan_form = QFormLayout(self.tn_fan_group)
        self.fan_port_spin = QSpinBox()
        self.fan_port_spin.setRange(1, 65535)
        self.fan_port_spin.setValue(self.machine.fanPort)
        self.fan_password_edit = QLineEdit(self.fan_password)
        self.fan_password_edit.setEchoMode(QLineEdit.Password)
        tn_fan_form.addRow("Fan Controller Port", self.fan_port_spin)
        tn_fan_form.addRow("Fan Controller Password", self.fan_password_edit)

        # Non-TrueNAS login section
        self.login_group = QGroupBox("Login")
        login_form = QFormLayout(self.login_group)
        self.user_edit = QLineEdit(self.machine.user)
        self.secret_edit = QLineEdit(self.secret)
        self.secret_edit.setEchoMode(QLineEdit.Password)
        self.use_sudo = QCheckBox("Use sudo for privileged actions")
        self.use_sudo.setChecked(self.machine.useSudo)
        login_form.addRow("User", self.user_edit)
        login_form.addRow("Password", self.secret_edit)
        login_form.addRow(self.use_sudo)

        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: palette(mid); font-size: 13px;")

        self.lhm_group = QGroupBox("Hardware sensors (optional)")
        lhm_form = QFormLayout(self.lhm_group)
        self.lhm_port_spin = QSpinBox()
        self.lhm_port_spin.setRange(0, 65535)
        self.lhm_port_spin.setValue(self.machine.lhmPort)
        lhm_hint = QLabel("In LibreHardwareMonitor: Options → check “Remote Web Server” "
                           "(default port 8085). Falls back to WMI if left at 0.")
        lhm_hint.setWordWrap(True)
        lhm_hint.setStyleSheet("color: palette(mid); font-size: 13px;")
        lhm_form.addRow("LibreHardwareMonitor Port", self.lhm_port_spin)
        lhm_form.addRow(lhm_hint)

        self.weburl_group = QGroupBox("Web UI (optional — Pi-hole / PiKVM admin link)")
        weburl_form = QFormLayout(self.weburl_group)
        self.weburl_edit = QLineEdit(self.machine.webUrl)
        self.weburl_edit.setPlaceholderText("https://192.168.1.20/")
        weburl_form.addRow("URL", self.weburl_edit)

        for w in (self.tn_group, self.tn_ssh_group, self.tn_fan_group, self.login_group, self.hint_label,
                  self.lhm_group, self.weburl_group):
            self.kind_container.addWidget(w)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self._test_connection)
        self.test_progress = QProgressBar()
        self.test_progress.setRange(0, 0)
        self.test_progress.setVisible(False)
        self.test_progress.setFixedWidth(80)
        self.test_result = QLabel("")
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_progress)
        test_row.addWidget(self.test_result, 1)
        root.addLayout(test_row)

        if existing is not None:
            forget_btn = QPushButton("Forget pinned SSH host key")
            forget_btn.clicked.connect(self._forget_host_key)
            root.addWidget(forget_btn)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)

        self._kind_changed()

    def _current_kind(self) -> MachineKind:
        return MachineKind(self.kind_combo.currentData())

    def _kind_changed(self):
        kind = self._current_kind()
        if self.existing is None or self.existing.port == 0:
            self.port_spin.setValue(kind.default_port)
        is_tn = kind == MachineKind.truenas
        self.tn_group.setVisible(is_tn)
        self.tn_ssh_group.setVisible(is_tn)
        self.tn_fan_group.setVisible(is_tn)
        self.login_group.setVisible(not is_tn)
        self.hint_label.setVisible(not is_tn)
        self.lhm_group.setVisible(kind == MachineKind.windows)
        self.weburl_group.setVisible(kind in (MachineKind.raspios, MachineKind.arch, MachineKind.debian))
        hints = {
            MachineKind.windows: "Needs OpenSSH Server installed and running (Settings > Apps > Optional "
                                  "Features), with password authentication enabled.",
            MachineKind.macos: "Enable Remote Login in System Settings > General > Sharing on the target Mac.",
        }
        self.hint_label.setText(hints.get(kind, "Needs an SSH server reachable with a password "
                                                 "(PasswordAuthentication yes in sshd_config)."))
        self._update_tn_fields()

    def _update_tn_fields(self):
        pw_mode = self.use_password.isChecked()
        self.tn_user_edit.setVisible(pw_mode)

    def _collect(self) -> Machine:
        m = Machine(**self.machine.__dict__)
        m.kind = self._current_kind()
        m.name = self.name_edit.text()
        m.host = self.host_edit.text()
        m.port = self.port_spin.value()
        if m.kind == MachineKind.truenas:
            m.useTLS = self.use_tls.isChecked()
            m.allowSelfSigned = self.allow_self_signed.isChecked()
            m.usePassword = self.use_password.isChecked()
            m.user = self.tn_user_edit.text()
            m.sshEnabled = self.ssh_enabled.isChecked()
            m.sshPort = self.ssh_port_spin.value()
            m.sshUser = self.ssh_user_edit.text()
            m.sshSudo = self.ssh_sudo.isChecked()
            m.fanPort = self.fan_port_spin.value()
        else:
            m.user = self.user_edit.text()
            m.useSudo = self.use_sudo.isChecked()
            if m.kind == MachineKind.windows:
                m.lhmPort = self.lhm_port_spin.value()
            if m.kind in (MachineKind.raspios, MachineKind.arch, MachineKind.debian):
                m.webUrl = self.weburl_edit.text()
        return m

    def _collect_secret(self) -> str:
        return self.tn_secret_edit.text() if self._current_kind() == MachineKind.truenas else self.secret_edit.text()

    def _test_connection(self):
        m = self._collect()
        if not m.id:
            import uuid
            m.id = str(uuid.uuid4())
        secret = self._collect_secret()
        ssh_pw = self.ssh_password_edit.text()
        MachineStore.set_secret(secret, m.id)
        if m.sshEnabled:
            MachineStore.set_ssh_password(ssh_pw, m.id)
        session = make_session(m)
        self.test_button.setEnabled(False)
        self.test_progress.setVisible(True)
        self.test_result.setText("")
        self._worker = _TestWorker(session)

        def finished(ok, msg):
            self.test_progress.setVisible(False)
            self.test_button.setEnabled(True)
            self.test_result.setText(msg)
            self.test_result.setStyleSheet("color: " + ("green" if ok else "red"))
            if self.existing is None:
                MachineStore.forget_secrets(m.id)
        self._worker.done.connect(finished)
        self._worker.start()

    def _forget_host_key(self):
        if self.existing:
            from ssh_runner import forget_host_key
            port = self.ssh_port_spin.value() if self.existing.kind == MachineKind.truenas else self.port_spin.value()
            forget_host_key(self.host_edit.text(), port)
            QMessageBox.information(self, "Forgotten", "The pinned SSH host key has been forgotten.")

    def _save(self):
        m = self._collect()
        if not m.host.strip():
            QMessageBox.warning(self, "Missing host", "Enter a host name or IP address.")
            return
        if m.kind != MachineKind.truenas and not m.user.strip():
            QMessageBox.warning(self, "Missing user", "Enter a user name.")
            return
        self.model.upsert(m, secret=self._collect_secret(), ssh_password=self.ssh_password_edit.text(),
                           fan_password=self.fan_password_edit.text())
        self.accept()
