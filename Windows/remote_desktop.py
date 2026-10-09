"""RDP / Screen Sharing quick-connect. Mirrors App/RemoteDesktop.swift.

Windows targets launch the built-in Remote Desktop Connection client (mstsc.exe). macOS targets have
no built-in Windows VNC viewer, so a vnc:// URL is handed to the shell, which uses whatever VNC
client (if any) is registered for that scheme.
"""
from __future__ import annotations

import os
import subprocess

from core import Machine, MachineKind


def connect(machine: Machine):
    if machine.kind == MachineKind.windows:
        subprocess.Popen(["mstsc.exe", f"/v:{machine.host}"])
    elif machine.kind == MachineKind.macos:
        os.startfile(f"vnc://{machine.host}")
