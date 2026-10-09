"""Tailscale `tailscale status --json` parser, shared by Linux/macOS sessions.

Mirrors App/Backends/Tailscale.swift.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class TailscalePeer:
    id: str
    host_name: str
    ip: str
    os: str
    online: bool
    is_self: bool
    is_exit_node: bool


@dataclass
class TailscaleStatus:
    backend_state: str
    peers: list[TailscalePeer] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.backend_state == "Running"


def parse(text: str) -> TailscaleStatus | None:
    try:
        obj = json.loads(text)
    except Exception:
        return None
    backend_state = obj.get("BackendState", "Unknown")

    def make(id_, p, is_self):
        return TailscalePeer(
            id=id_, host_name=p.get("HostName", id_), ip=(p.get("TailscaleIPs") or [""])[0],
            os=p.get("OS", ""), online=True if is_self else bool(p.get("Online", False)),
            is_self=is_self, is_exit_node=bool(p.get("ExitNode", False)))

    peers = []
    self_p = obj.get("Self")
    if self_p:
        peers.append(make(self_p.get("ID", "self"), self_p, True))
    for id_, p in (obj.get("Peer") or {}).items():
        peers.append(make(id_, p, False))
    peers.sort(key=lambda p: (not p.is_self, p.host_name.lower()))
    return TailscaleStatus(backend_state=backend_state, peers=peers)
