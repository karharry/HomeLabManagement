"""Base class for live state + polling of one managed machine.

Mirrors App/MachineSession.swift. Concrete subclasses (TrueNASSession, LinuxSession, WindowsSession,
MacSession) implement the kind-specific transport/parsing; this base class owns:
 - the background worker thread (one per session) that polls on an interval and runs on-demand
   actions submitted from the UI thread
 - the alerts/snapshot logic shared by every kind
 - Qt signals used to notify pages of new data, since PySide6 widgets aren't reactive like SwiftUI

Threading model: everything that talks to the network happens on the session's single worker thread
(matching the Swift app's one-persistent-connection-per-session design). UI code calls `submit(fn, topic)`
to run `fn` there; when it returns, `updated(topic)` is emitted — PySide6 automatically marshals that
signal emission to the main thread's event loop since the receiver (a QWidget) lives there.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal

from core import Machine, MachineKind, ConnectionState, StorageItem, ServiceRow, ProcessRow, AlertItem, Fmt


class MachineSession(QObject):
    updated = Signal(str)          # topic: "poll", "services", "processes", "datasets", "vms", ...
    action_error = Signal(str)

    HISTORY_LENGTH = 90

    def __init__(self, machine: Machine):
        super().__init__()
        self.machine = machine
        self.is_foreground = False

        self.state = ConnectionState(ConnectionState.DISCONNECTED)
        self.host_name = ""
        self.host_os = ""
        self.host_uptime = 0.0
        self.cpu_percent: Optional[float] = None
        self.cpu_history: list[float] = []
        self.mem_used = 0.0
        self.mem_total = 0.0
        self.mem_history: list[float] = []
        self.storage: list[StorageItem] = []
        self.gpu_percent: Optional[float] = None
        self.net_rx_rate: Optional[float] = None
        self.net_tx_rate: Optional[float] = None
        self.extra_alerts: list[AlertItem] = []
        self.last_refresh = None
        self.action_error_text: Optional[str] = None

        self.services: list[ServiceRow] = []
        self.services_loaded = False
        self.processes: list[ProcessRow] = []

        self._queue: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- identity / convenience --
    @property
    def id(self) -> str:
        return self.machine.id

    @property
    def kind(self) -> MachineKind:
        return self.machine.kind

    @property
    def display_name(self) -> str:
        return self.machine.display_name

    @property
    def nav_items(self):
        return self.machine.kind.nav_items

    @property
    def is_online(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    @property
    def is_configured(self) -> bool:
        return self.machine.is_valid

    @property
    def mem_used_fraction(self) -> float:
        return self.mem_used / self.mem_total if self.mem_total > 0 else 0.0

    @property
    def net_total_rate(self) -> Optional[float]:
        if self.net_rx_rate is None and self.net_tx_rate is None:
            return None
        return (self.net_rx_rate or 0) + (self.net_tx_rate or 0)

    @property
    def effective_ssh_port(self) -> int:
        return self.machine.sshPort if self.kind == MachineKind.truenas else self.machine.port

    @property
    def alerts(self) -> list[AlertItem]:
        a = list(self.extra_alerts)
        if self.state.kind == ConnectionState.FAILED and self.last_refresh is not None:
            a.append(AlertItem(id="offline", level=AlertItem.CRITICAL,
                                title=f"Can't reach {self.display_name}", detail=self.state.message))
        for item in self.storage:
            if item.used_fraction >= 0.85:
                a.append(AlertItem(
                    id=f"storage-{item.id}",
                    level=AlertItem.CRITICAL if item.used_fraction >= 0.95 else AlertItem.WARNING,
                    title=f"{item.title} is {int(item.used_fraction * 100)}% full",
                    detail=f"{Fmt.bytes(item.size - item.used)} free"))
        for item in self.storage:
            if not item.healthy:
                a.append(AlertItem(id=f"unhealthy-{item.id}", level=AlertItem.CRITICAL,
                                    title=f"{item.title}: {item.statusText}", detail=item.subtitle))
        if self.mem_total > 0 and self.mem_used_fraction >= 0.92:
            a.append(AlertItem(id="mem", level=AlertItem.WARNING,
                                title=f"Memory is {int(self.mem_used_fraction * 100)}% used",
                                detail=f"{Fmt.bytes(self.mem_total - self.mem_used)} available"))
        a.sort(key=lambda x: -x.level)
        return a

    def push(self, arr: list, v, limit: Optional[int] = None):
        limit = limit or self.HISTORY_LENGTH
        if v is None:
            return
        arr.append(v)
        if len(arr) > limit:
            del arr[:len(arr) - limit]

    # -- lifecycle --
    def start(self):
        self.stop()
        if not self.is_configured:
            self.state = ConnectionState(ConnectionState.DISCONNECTED)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"session-{self.machine.host}")
        self._thread.start()

    def stop(self):
        if self._thread is None:
            return
        self._stop.set()
        self._queue.put(None)
        self._thread.join(timeout=3)
        self._thread = None
        try:
            self._close_backend()
        except Exception:
            pass
        self.state = ConnectionState(ConnectionState.DISCONNECTED)

    def submit(self, fn, topic: str = ""):
        """Runs fn() on this session's worker thread; emits updated(topic) when it returns (or errors)."""
        self._queue.put((fn, topic))

    def forget_host_key(self):
        from ssh_runner import forget_host_key
        forget_host_key(self.machine.host, self.effective_ssh_port)

    def perform_action(self, out: str, success_contains: Optional[str] = None) -> bool:
        trimmed = (out or "").strip()
        if trimmed.startswith("ERROR:"):
            self.action_error_text = trimmed[len("ERROR:"):].strip()
            self.action_error.emit(self.action_error_text)
            return False
        if success_contains is not None and success_contains not in trimmed:
            self.action_error_text = trimmed if trimmed else "The machine didn't confirm the action."
            self.action_error.emit(self.action_error_text)
            return False
        self.action_error_text = None
        return True

    def snapshot(self) -> dict:
        return dict(id=self.id, kind=self.kind.value, name=self.display_name, online=self.is_online,
                    cpu=self.cpu_percent, memory=(self.mem_used_fraction if self.mem_total > 0 else None),
                    alert_count=len(self.alerts), uptime=self.host_uptime)

    # -- overridden by subclasses --
    def _poll(self) -> bool:
        return False

    def _close_backend(self):
        pass

    def refresh_services(self):
        pass

    def refresh_processes(self):
        pass

    def service_action(self, s: ServiceRow, verb: str):
        pass

    def kill_process(self, p: ProcessRow):
        pass

    def reboot(self):
        pass

    def shutdown(self):
        pass

    # -- worker thread --
    def _run(self):
        failures = 0
        next_poll = 0.0
        while not self._stop.is_set():
            timeout = max(0.01, next_poll - time.monotonic())
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = "___POLL___"
            if self._stop.is_set():
                return
            if item is None:
                continue
            if item == "___POLL___":
                ok = self._run_poll()
                failures = 0 if ok else failures + 1
                base = 4.0 if self.is_foreground else 15.0
                delay = base if ok else min(failures * 5 + base, 60)
                next_poll = time.monotonic() + delay
            else:
                fn, topic = item
                try:
                    fn()
                except Exception as e:
                    self.action_error_text = str(e)
                    self.action_error.emit(str(e))
                if topic:
                    self.updated.emit(topic)

    def _run_poll(self) -> bool:
        try:
            ok = self._poll()
        except Exception as e:
            self.state = ConnectionState(ConnectionState.FAILED, str(e))
            ok = False
        self.updated.emit("poll")
        return ok


def make_session(machine: Machine) -> MachineSession:
    """Builds the right concrete session for a machine's kind. Mirrors MachineSession.make(for:)."""
    if machine.kind == MachineKind.truenas:
        from truenas import TrueNASSession
        return TrueNASSession(machine)
    if machine.kind in (MachineKind.debian, MachineKind.raspios, MachineKind.arch):
        from linux_session import LinuxSession
        return LinuxSession(machine)
    if machine.kind == MachineKind.windows:
        from windows_session import WindowsSession
        return WindowsSession(machine)
    from macos_session import MacSession
    return MacSession(machine)
