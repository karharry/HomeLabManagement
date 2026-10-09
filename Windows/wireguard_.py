"""WireGuard tunnel model + `wg show all dump` parser, shared by Linux/macOS/Windows/TrueNAS sessions.

Mirrors App/Backends/WireGuard.swift.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass


@dataclass
class WireGuardTunnel:
    name: str
    is_up: bool
    public_key: str = ""
    endpoint: str = ""
    allowed_ips: str = ""
    latest_handshake: str = ""
    rx_bytes: int = 0
    tx_bytes: int = 0


_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def is_valid_name(name: str) -> bool:
    return bool(name) and len(name) <= 40 and bool(_NAME_RE.match(name))


def _relative_time(unix_seconds: str) -> str:
    try:
        t = float(unix_seconds)
    except ValueError:
        return "Never"
    if t <= 0:
        return "Never"
    seconds = time.time() - t
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def tunnels(conf_names: list[str], dump: str) -> list[WireGuardTunnel]:
    up: dict[str, WireGuardTunnel] = {}
    for line in dump.splitlines():
        f = line.split("\t")
        if not f or not f[0]:
            continue
        iface = f[0]
        if len(f) == 5:
            up[iface] = WireGuardTunnel(name=iface, is_up=True, public_key=f[2])
        elif len(f) == 9 and iface in up:
            t = up[iface]
            t.endpoint = "" if f[3] == "(none)" else f[3]
            t.allowed_ips = f[4]
            try:
                hs = int(f[5])
            except ValueError:
                hs = 0
            t.latest_handshake = _relative_time(f[5]) if hs > 0 else "Never"
            t.rx_bytes = int(f[6]) if f[6].isdigit() else 0
            t.tx_bytes = int(f[7]) if f[7].isdigit() else 0
    all_tunnels = [up.get(name, WireGuardTunnel(name=name, is_up=False)) for name in conf_names]
    for name, t in up.items():
        if name not in conf_names:
            all_tunnels.append(t)
    all_tunnels.sort(key=lambda t: t.name.lower())
    return all_tunnels
