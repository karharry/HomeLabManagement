"""Owns the machine list, one MachineSession per machine, and the current selection.

Mirrors App/AppModel.swift. There's no WidgetKit on Windows, so `publish()` here just refreshes the
system tray icon (see tray.py) instead of writing an App Group snapshot file.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

from core import Machine, MachineStore
from machine_session import MachineSession, make_session


class AppModel(QObject):
    machines_changed = Signal()
    selection_changed = Signal()

    def __init__(self):
        super().__init__()
        self.machines: list[Machine] = MachineStore.load()
        self.sessions: dict[str, MachineSession] = {}
        for m in self.machines:
            self.sessions[m.id] = make_session(m)
        self.selected_id: Optional[str] = MachineStore.get_selected_id()
        if self.selected_id is None or self.selected_id not in self.sessions:
            self.selected_id = self.machines[0].id if self.machines else None
        self._mark_foreground()

    @property
    def selected(self) -> Optional[MachineSession]:
        return self.sessions.get(self.selected_id) if self.selected_id else None

    @property
    def ordered_sessions(self) -> list[MachineSession]:
        return [self.sessions[m.id] for m in self.machines if m.id in self.sessions]

    def start_all(self):
        for s in self.sessions.values():
            s.start()

    def select(self, machine_id: Optional[str]):
        if machine_id == self.selected_id:
            return
        self.selected_id = machine_id
        MachineStore.set_selected_id(machine_id)
        self._mark_foreground()
        if machine_id and machine_id in self.sessions:
            self.sessions[machine_id].start()
        self.selection_changed.emit()

    def set_window_hidden(self, hidden: bool):
        for s in self.sessions.values():
            s.window_hidden = hidden
            if not hidden:
                s.poll_now()  # catch up immediately instead of waiting out the slow hidden interval

    def _mark_foreground(self):
        for s in self.sessions.values():
            s.is_foreground = (s.id == self.selected_id)

    def upsert(self, machine: Machine, secret: str = "", ssh_password: str = "", fan_password: str = ""):
        MachineStore.set_secret(secret, machine.id)
        if ssh_password or machine.sshEnabled:
            MachineStore.set_ssh_password(ssh_password, machine.id)
        if fan_password:
            MachineStore.set_fan_password(fan_password, machine.id)

        existing_idx = next((i for i, m in enumerate(self.machines) if m.id == machine.id), None)
        if existing_idx is not None:
            old_kind = self.machines[existing_idx].kind
            self.machines[existing_idx] = machine
            if old_kind == machine.kind:
                s = self.sessions[machine.id]
                s.stop()
                s.machine = machine
                s.start()
            else:
                old = self.sessions.get(machine.id)
                if old:
                    old.stop()
                s = make_session(machine)
                self.sessions[machine.id] = s
                s.start()
        else:
            self.machines.append(machine)
            s = make_session(machine)
            self.sessions[machine.id] = s
            s.start()
            self.selected_id = machine.id
            MachineStore.set_selected_id(machine.id)
            self._mark_foreground()

        MachineStore.save(self.machines)
        self.machines_changed.emit()

    def remove(self, machine_id: str):
        s = self.sessions.pop(machine_id, None)
        if s:
            s.stop()
        self.machines = [m for m in self.machines if m.id != machine_id]
        MachineStore.forget_secrets(machine_id)
        MachineStore.save(self.machines)
        if self.selected_id == machine_id:
            self.selected_id = self.machines[0].id if self.machines else None
            MachineStore.set_selected_id(self.selected_id)
            self._mark_foreground()
        self.machines_changed.emit()

    def can_move_up(self, machine_id: str) -> bool:
        idx = next((i for i, m in enumerate(self.machines) if m.id == machine_id), 0)
        return idx > 0

    def can_move_down(self, machine_id: str) -> bool:
        idx = next((i for i, m in enumerate(self.machines) if m.id == machine_id), len(self.machines) - 1)
        return idx < len(self.machines) - 1

    def move_up(self, machine_id: str):
        idx = next((i for i, m in enumerate(self.machines) if m.id == machine_id), None)
        if idx is None or idx == 0:
            return
        self.machines[idx - 1], self.machines[idx] = self.machines[idx], self.machines[idx - 1]
        MachineStore.save(self.machines)
        self.machines_changed.emit()

    def move_down(self, machine_id: str):
        idx = next((i for i, m in enumerate(self.machines) if m.id == machine_id), None)
        if idx is None or idx >= len(self.machines) - 1:
            return
        self.machines[idx + 1], self.machines[idx] = self.machines[idx], self.machines[idx + 1]
        MachineStore.save(self.machines)
        self.machines_changed.emit()
