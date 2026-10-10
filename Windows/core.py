"""Shared data model: Machine, MachineKind, settings store, NavItem, alerts, formatting.

Mirrors the Swift app's Shared/MachineKind.swift, App/Machine.swift, App/MachineSession.swift,
Shared/Models.swift. Secrets are plain text in settings.json beside the exe (no Keychain on Windows,
and the app must stay a portable single file with no installs) instead of the macOS Keychain.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid as uuidlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Callable


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_PATH = os.path.join(app_dir(), "settings.json")


# ----------------------------------------------------------------------------
# MachineKind
# ----------------------------------------------------------------------------

class MachineKind(str, Enum):
    truenas = "truenas"
    debian = "debian"
    raspios = "raspios"
    arch = "arch"
    windows = "windows"
    macos = "macos"
    opnsense = "opnsense"

    @property
    def label(self) -> str:
        return {
            MachineKind.truenas: "TrueNAS",
            MachineKind.debian: "Debian / Ubuntu",
            MachineKind.raspios: "Raspberry Pi OS",
            MachineKind.arch: "Arch Linux",
            MachineKind.windows: "Windows",
            MachineKind.macos: "macOS",
            MachineKind.opnsense: "OPNsense",
        }[self]

    @property
    def icon(self) -> str:
        # Short glyph keys consumed by widgets_common.kind_icon()/draw helpers.
        return {
            MachineKind.truenas: "server",
            MachineKind.debian: "terminal",
            MachineKind.raspios: "cpu",
            MachineKind.arch: "terminal",
            MachineKind.windows: "pc",
            MachineKind.macos: "mac",
            MachineKind.opnsense: "firewall",
        }[self]

    @property
    def is_posix_shell(self) -> bool:
        return self not in (MachineKind.truenas, MachineKind.windows, MachineKind.opnsense)

    @property
    def default_port(self) -> int:
        return 443 if self in (MachineKind.truenas, MachineKind.opnsense) else 22

    @property
    def remote_desktop_protocol(self) -> Optional[str]:
        if self == MachineKind.windows:
            return "rdp"
        if self == MachineKind.macos:
            return "vnc"
        return None

    @property
    def nav_items(self) -> list["NavItem"]:
        # Membership only — sort_nav_items() (see below) places these in the canonical cross-kind
        # order/grouping, so what matters here is just which pages this kind has, not what order
        # they're written in.
        if self == MachineKind.truenas:
            items = {NavItem.dashboard, NavItem.storage, NavItem.datasets, NavItem.shares, NavItem.network,
                     NavItem.apps, NavItem.vms, NavItem.containers, NavItem.services, NavItem.data_protection,
                     NavItem.fan, NavItem.updates, NavItem.users, NavItem.console, NavItem.alerts}
        elif self == MachineKind.windows:
            items = {NavItem.dashboard, NavItem.storage, NavItem.cpu, NavItem.gpu, NavItem.sensors,
                     NavItem.processes, NavItem.services, NavItem.startup, NavItem.scheduled_tasks,
                     NavItem.software, NavItem.updates, NavItem.logs, NavItem.users, NavItem.console,
                     NavItem.alerts}
        elif self == MachineKind.macos:
            items = {NavItem.dashboard, NavItem.storage, NavItem.power, NavItem.processes, NavItem.services,
                     NavItem.startup, NavItem.software, NavItem.updates, NavItem.logs, NavItem.users,
                     NavItem.console, NavItem.alerts}
        elif self == MachineKind.opnsense:
            items = {NavItem.dashboard, NavItem.storage, NavItem.network, NavItem.services,
                     NavItem.dhcp_leases, NavItem.dnsmasq_hosts, NavItem.updates, NavItem.alerts}
        else:
            items = {NavItem.dashboard, NavItem.storage, NavItem.processes, NavItem.services,
                      NavItem.scheduled_tasks, NavItem.updates, NavItem.logs, NavItem.users, NavItem.console,
                      NavItem.alerts}
        return sort_nav_items(items)


ALL_KINDS = [MachineKind.truenas, MachineKind.debian, MachineKind.raspios, MachineKind.arch,
             MachineKind.windows, MachineKind.macos, MachineKind.opnsense]


class NavItem(str, Enum):
    dashboard = "Dashboard"
    storage = "Storage"
    processes = "Processes"
    services = "Services"
    alerts = "Alerts"
    apps = "Apps"
    vms = "VMs"
    containers = "Containers"
    fan = "Fan Control"
    pihole = "Pi-hole"
    pikvm = "PiKVM"
    tailscale = "Tailscale"
    cpu = "CPU"
    gpu = "GPU"
    sensors = "Sensors"
    power = "Power & Thermal"
    console = "Console"
    logs = "Logs"
    users = "Users"
    updates = "Updates"
    startup = "Startup Items"
    scheduled_tasks = "Scheduled Tasks"
    software = "Installed Software"
    datasets = "Datasets & Snapshots"
    shares = "Shares"
    network = "Network"
    data_protection = "Data Protection"
    raspberry_pi = "Raspberry Pi"
    vpn = "VPN"
    dhcp_leases = "DHCP Leases"
    local_dns = "Local DNS"
    dnsmasq_hosts = "Static Hosts"

    @property
    def icon(self) -> str:
        return {
            NavItem.dashboard: "gauge", NavItem.storage: "drive", NavItem.processes: "list",
            NavItem.services: "switch", NavItem.alerts: "bell", NavItem.apps: "grid", NavItem.vms: "display",
            NavItem.containers: "box", NavItem.fan: "fan", NavItem.pihole: "shield", NavItem.pikvm: "keyboard",
            NavItem.tailscale: "network", NavItem.cpu: "cpu", NavItem.gpu: "display", NavItem.sensors: "thermo",
            NavItem.power: "bolt", NavItem.console: "terminal", NavItem.logs: "doc", NavItem.users: "person",
            NavItem.updates: "down", NavItem.startup: "power", NavItem.scheduled_tasks: "clock",
            NavItem.software: "box", NavItem.datasets: "folder", NavItem.shares: "drive", NavItem.network: "network",
            NavItem.data_protection: "lock", NavItem.raspberry_pi: "cpu", NavItem.vpn: "lock",
            NavItem.dhcp_leases: "network", NavItem.local_dns: "folder", NavItem.dnsmasq_hosts: "folder",
        }[self]


# Canonical sidebar grouping/order: every machine kind's nav_items is a *subset* of this, always in
# this relative order, so the sidebar reads the same way across every machine kind instead of each
# kind inventing its own ad-hoc ordering. Dashboard and Alerts stay ungrouped (pinned first/last —
# the "glance at this first/last" pages); everything else groups by subject matter. A kind that has
# none of a group's items simply doesn't show that group's header.
NAV_GROUPS: list[tuple[Optional[str], list["NavItem"]]] = [
    (None, [NavItem.dashboard]),
    ("Hardware", [NavItem.storage, NavItem.cpu, NavItem.gpu, NavItem.sensors, NavItem.power,
                  NavItem.fan, NavItem.raspberry_pi]),
    ("Software", [NavItem.apps, NavItem.vms, NavItem.containers, NavItem.startup, NavItem.software,
                  NavItem.scheduled_tasks, NavItem.updates]),
    ("Network", [NavItem.network, NavItem.dhcp_leases, NavItem.dnsmasq_hosts, NavItem.local_dns,
                 NavItem.pihole, NavItem.tailscale, NavItem.vpn, NavItem.pikvm]),
    ("Data", [NavItem.datasets, NavItem.shares, NavItem.data_protection]),
    ("System", [NavItem.processes, NavItem.services, NavItem.logs, NavItem.users, NavItem.console]),
    (None, [NavItem.alerts]),
]

NAV_GROUP_OF: dict["NavItem", Optional[str]] = {n: g for g, items in NAV_GROUPS for n in items}
NAV_ORDER: list["NavItem"] = [n for _, items in NAV_GROUPS for n in items]


def sort_nav_items(items) -> list["NavItem"]:
    """Orders a kind/session's available nav items by the canonical NAV_ORDER above. Every nav_items
    property should build its available set in whatever order is convenient, then return
    sort_nav_items(that set) as the last step, rather than hand-maintaining list positions."""
    s = set(items)
    return [n for n in NAV_ORDER if n in s]


# ----------------------------------------------------------------------------
# Machine
# ----------------------------------------------------------------------------

_DEFAULTS = dict(
    id=None, kind=MachineKind.debian, name="", host="", port=22, user="",
    useTLS=True, allowSelfSigned=True, usePassword=False, sshEnabled=False, sshPort=22, sshUser="",
    sshSudo=True, fanPort=9595, useSudo=True, lhmPort=0, webUrl="",
)


@dataclass
class Machine:
    id: str = field(default_factory=lambda: str(uuidlib.uuid4()))
    kind: MachineKind = MachineKind.debian
    name: str = ""
    host: str = ""
    port: int = 22
    user: str = ""
    useTLS: bool = True
    allowSelfSigned: bool = True
    usePassword: bool = False
    sshEnabled: bool = False
    sshPort: int = 22
    sshUser: str = ""
    sshSudo: bool = True
    fanPort: int = 9595
    useSudo: bool = True
    lhmPort: int = 0
    webUrl: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Machine":
        out = dict(_DEFAULTS)
        out.update({k: v for k, v in d.items() if k in _DEFAULTS})
        out["id"] = d.get("id") or str(uuidlib.uuid4())
        out["kind"] = MachineKind(d.get("kind", "debian"))
        return Machine(**out)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @property
    def display_name(self) -> str:
        n = self.name.strip()
        if n:
            return n
        return self.host if self.host else self.kind.label

    @property
    def is_valid(self) -> bool:
        if not self.host.strip():
            return False
        if self.kind == MachineKind.truenas:
            return True
        return bool(self.user.strip())

    @property
    def effective_ssh_port(self) -> int:
        return self.sshPort if self.kind == MachineKind.truenas else self.port


# ----------------------------------------------------------------------------
# Settings store (replaces UserDefaults + Keychain)
# ----------------------------------------------------------------------------

class _Store:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"machines": [], "secrets": {}, "selected": None, "hostKeys": {}, "prefs": {}}

    def _save(self):
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, SETTINGS_PATH)

    # -- machines --
    def load_machines(self) -> list[Machine]:
        with self._lock:
            return [Machine.from_dict(d) for d in self._data.get("machines", [])]

    def save_machines(self, machines: list[Machine]):
        with self._lock:
            self._data["machines"] = [m.to_dict() for m in machines]
            self._save()

    @property
    def selected_id(self) -> Optional[str]:
        with self._lock:
            return self._data.get("selected")

    @selected_id.setter
    def selected_id(self, v: Optional[str]):
        with self._lock:
            self._data["selected"] = v
            self._save()

    # -- secrets (plain text; this machine's own settings file is the trust boundary) --
    def secret(self, key: str) -> str:
        with self._lock:
            return self._data.get("secrets", {}).get(key, "")

    def set_secret(self, key: str, value: str):
        with self._lock:
            secrets = self._data.setdefault("secrets", {})
            if value:
                secrets[key] = value
            else:
                secrets.pop(key, None)
            self._save()

    def forget_secrets(self, machine_id: str):
        with self._lock:
            secrets = self._data.setdefault("secrets", {})
            for suffix in ("secret", "ssh", "fan", "protonuser", "proton", "pihole"):
                secrets.pop(f"{suffix}.{machine_id}", None)
            self._save()

    # -- host key pinning (trust-on-first-use) --
    def pinned_host_key(self, key: str) -> Optional[str]:
        with self._lock:
            return self._data.get("hostKeys", {}).get(key)

    def pin_host_key(self, key: str, fingerprint: str):
        with self._lock:
            self._data.setdefault("hostKeys", {})[key] = fingerprint
            self._save()

    def forget_host_key(self, key: str):
        with self._lock:
            self._data.setdefault("hostKeys", {}).pop(key, None)
            self._save()

    # -- misc prefs (open at login, open minimized, show truenas containers...) --
    def pref(self, key: str, default=None):
        with self._lock:
            return self._data.get("prefs", {}).get(key, default)

    def set_pref(self, key: str, value):
        with self._lock:
            self._data.setdefault("prefs", {})[key] = value
            self._save()


store = _Store()


class MachineStore:
    """Thin namespace mirroring the Swift MachineStore enum's call sites."""

    @staticmethod
    def load() -> list[Machine]:
        return store.load_machines()

    @staticmethod
    def save(machines: list[Machine]):
        store.save_machines(machines)

    @staticmethod
    def secret(machine_id: str) -> str:
        return store.secret(f"secret.{machine_id}")

    @staticmethod
    def set_secret(v: str, machine_id: str):
        store.set_secret(f"secret.{machine_id}", v)

    @staticmethod
    def ssh_password(machine_id: str) -> str:
        return store.secret(f"ssh.{machine_id}")

    @staticmethod
    def set_ssh_password(v: str, machine_id: str):
        store.set_secret(f"ssh.{machine_id}", v)

    @staticmethod
    def fan_password(machine_id: str) -> str:
        return store.secret(f"fan.{machine_id}")

    @staticmethod
    def set_fan_password(v: str, machine_id: str):
        store.set_secret(f"fan.{machine_id}", v)

    @staticmethod
    def pihole_password(machine_id: str) -> str:
        return store.secret(f"pihole.{machine_id}")

    @staticmethod
    def set_pihole_password(v: str, machine_id: str):
        store.set_secret(f"pihole.{machine_id}", v)

    @staticmethod
    def proton_user(machine_id: str) -> str:
        return store.secret(f"protonuser.{machine_id}")

    @staticmethod
    def set_proton_user(v: str, machine_id: str):
        store.set_secret(f"protonuser.{machine_id}", v)

    @staticmethod
    def proton_password(machine_id: str) -> str:
        return store.secret(f"proton.{machine_id}")

    @staticmethod
    def set_proton_password(v: str, machine_id: str):
        store.set_secret(f"proton.{machine_id}", v)

    @staticmethod
    def forget_secrets(machine_id: str):
        store.forget_secrets(machine_id)

    @staticmethod
    def get_selected_id() -> Optional[str]:
        return store.selected_id

    @staticmethod
    def set_selected_id(v: Optional[str]):
        store.selected_id = v


# ----------------------------------------------------------------------------
# Shared row/record models (mirrors Shared/Models.swift + App/MachineSession.swift)
# ----------------------------------------------------------------------------

@dataclass
class StorageItem:
    id: str
    title: str
    size: int
    used: int
    subtitle: str = ""
    healthy: bool = True
    statusText: str = ""

    @property
    def used_fraction(self) -> float:
        return self.used / self.size if self.size > 0 else 0.0


@dataclass
class ServiceRow:
    id: str
    name: str
    displayName: str
    state: str
    isRunning: bool
    startMode: str = ""
    canControl: bool = True


@dataclass
class ProcessRow:
    pid: int
    name: str
    cpu: float
    mem: float
    user: str = ""


@dataclass
class AlertItem:
    id: str
    level: int  # 0 info, 1 warning, 2 critical
    title: str
    detail: str

    INFO, WARNING, CRITICAL = 0, 1, 2


class ConnectionState:
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"

    def __init__(self, kind: str, message: str = ""):
        self.kind = kind
        self.message = message

    def __eq__(self, other):
        if isinstance(other, str):
            return self.kind == other
        return isinstance(other, ConnectionState) and self.kind == other.kind and self.message == other.message

    def __repr__(self):
        return f"ConnectionState({self.kind!r}, {self.message!r})"


# ----------------------------------------------------------------------------
# Formatting (mirrors Shared/Models.swift's Fmt enum)
# ----------------------------------------------------------------------------

class Fmt:
    @staticmethod
    def bytes(n: Optional[float]) -> str:
        if n is None:
            return "–"
        n = float(n)
        if not (n == n) or n in (float("inf"), float("-inf")):
            n = 0
        neg = n < 0
        n = abs(n)
        units = ["bytes", "KB", "MB", "GB", "TB", "PB"]
        i = 0
        v = n
        while v >= 1024 and i < len(units) - 1:
            v /= 1024
            i += 1
        if i == 0:
            s = f"{int(v)} bytes"
        else:
            s = f"{v:.2f} {units[i]}" if v < 10 else (f"{v:.1f} {units[i]}" if v < 100 else f"{v:.0f} {units[i]}")
        return ("-" + s) if neg else s

    @staticmethod
    def uptime(seconds: float) -> str:
        s = int(seconds)
        d, rem = divmod(s, 86400)
        h, m = divmod(rem, 3600)
        m //= 60
        if d > 0:
            return f"{d}d {h}h"
        return f"{h}h {m}m" if h > 0 else f"{m}m"

    @staticmethod
    def rate(bytes_per_second: Optional[float]) -> str:
        if bytes_per_second is None:
            return "–"
        return Fmt.bytes(bytes_per_second) + "/s"

    @staticmethod
    def percent(f: Optional[float]) -> str:
        if f is None:
            return "–"
        return f"{round(f)}%"

    @staticmethod
    def mhz(m: Optional[float]) -> str:
        if not m or m <= 0:
            return "–"
        return f"{m / 1000:.2f} GHz" if m >= 1000 else f"{int(m)} MHz"

    @staticmethod
    def temp(c: Optional[float]) -> str:
        if c is None:
            return "–"
        return f"{round(c)}°C"

    @staticmethod
    def count(n: Optional[float]) -> str:
        if n is None:
            return "–"
        return f"{int(round(n)):,}"
