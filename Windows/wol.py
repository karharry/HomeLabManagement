"""Wake-on-LAN magic packet."""
from __future__ import annotations

import re
import socket


def parse_mac(mac: str) -> bytes:
    hexes = re.sub(r"[-:.\s]", "", mac)
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", hexes):
        raise ValueError(f"“{mac}” isn't a valid MAC address (expected like AA:BB:CC:DD:EE:FF).")
    return bytes.fromhex(hexes)


def magic_packet(mac: str) -> bytes:
    return b"\xff" * 6 + parse_mac(mac) * 16


def wake(mac: str):
    pkt = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(pkt, ("255.255.255.255", 9))
