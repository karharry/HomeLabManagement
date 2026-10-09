"""SSH transport shared by every non-TrueNAS machine kind (and TrueNAS's optional SSH add-on).

Mirrors App/SSH.swift: a persistent paramiko connection reused across calls, trust-on-first-use host
key pinning (stored in settings.json), POSIX shell execution with optional sudo, and PowerShell
execution via a compressed -EncodedCommand (so quoting never matters and the 8191-char cmd.exe limit
isn't hit).
"""
from __future__ import annotations

import base64
import hashlib
import threading
import zlib
from dataclasses import dataclass

import paramiko

from core import store


class SSHError(Exception):
    pass


@dataclass(frozen=True)
class SSHSettings:
    host: str
    port: int
    user: str
    password: str
    use_sudo: bool = False


def quote(s: str) -> str:
    """POSIX shell single-quoted string literal."""
    return "'" + s.replace("'", "'\\''") + "'"


def pq(s: str) -> str:
    """PowerShell single-quoted string literal."""
    return "'" + s.replace("'", "''") + "'"


class _PinningPolicy(paramiko.MissingHostKeyPolicy):
    """Trust-on-first-use: pin the key fingerprint on first connect, reject if it later changes."""

    def __init__(self, pin_id: str):
        self.pin_id = pin_id

    def missing_host_key(self, client, hostname, key):
        fingerprint = key.get_base64()
        store_key = f"sshHostKey.{self.pin_id}"
        pinned = store.pinned_host_key(store_key)
        if pinned is None:
            store.pin_host_key(store_key, fingerprint)
        elif pinned != fingerprint:
            raise SSHError(
                "The SSH host key changed since it was first trusted. Use “Forget pinned host "
                "key” in the machine's settings if this is expected.")


def forget_host_key(host: str, port: int):
    store.forget_host_key(f"sshHostKey.{host}:{port}")


class SSHRunner:
    """One SSH connection, reused across calls to the same machine. Thread-safe for sequential use
    from a single session's worker thread (not meant to be shared across threads concurrently)."""

    def __init__(self):
        self._client: paramiko.SSHClient | None = None
        self._connected_to: SSHSettings | None = None
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._connected_to = None

    def _ensure_client(self, s: SSHSettings) -> paramiko.SSHClient:
        if self._connected_to != s:
            self._close_locked()
        if self._client is not None:
            return self._client
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_PinningPolicy(f"{s.host}:{s.port}"))
        try:
            client.connect(hostname=s.host, port=s.port, username=s.user, password=s.password,
                            timeout=12, banner_timeout=12, auth_timeout=12, look_for_keys=False,
                            allow_agent=False)
        except paramiko.AuthenticationException:
            raise SSHError(f"SSH login was rejected for “{s.user}”. Check the user name and password.")
        except SSHError:
            raise
        except Exception as e:
            raise SSHError(f"Network error talking to the machine over SSH: {e}")
        self._client = client
        self._connected_to = s
        return client

    def _exec(self, client: paramiko.SSHClient, command: str, max_bytes: int) -> str:
        stdin, stdout, stderr = client.exec_command(command, timeout=60)
        chunks = []
        total = 0
        for chunk in iter(lambda: stdout.read(65536), b""):
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_bytes:
                break
        stdout.channel.recv_exit_status()
        return b"".join(chunks).decode("utf-8", errors="replace")

    def sh(self, script: str, s: SSHSettings, sudo: bool = False, merge: bool = False,
           max_bytes: int = 4 * 1024 * 1024) -> str:
        try:
            client = self._ensure_client(s)
            b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
            run = f"sh -c \"$(printf %s {b64} | base64 -d)\""
            if sudo and s.use_sudo and s.user != "root":
                command = f"printf '%s\\n' {quote(s.password)} | sudo -S -p '' {run}"
            else:
                command = run
            redirect = "2>&1" if merge else "2>/dev/null"
            return self._exec(client, f"({command}) {redirect}; true", max_bytes)
        except SSHError:
            self.close()
            raise
        except Exception as e:
            self.close()
            raise SSHError(f"SSH error: {e}")

    def ps(self, script: str, s: SSHSettings, max_bytes: int = 8 * 1024 * 1024) -> str:
        try:
            client = self._ensure_client(s)
            command = ps_command(script)
            return self._exec(client, command, max_bytes)
        except SSHError:
            self.close()
            raise
        except Exception as e:
            self.close()
            raise SSHError(f"SSH error: {e}")


def ps_command(script: str) -> str:
    """Builds the compressed -EncodedCommand bootstrap used to run `script` via PowerShell, bypassing
    cmd.exe's 8191-character command-line limit."""
    deflated = zlib.compress(script.encode("utf-8"))[2:-4]  # raw deflate (strip zlib header/adler32)
    b64 = base64.b64encode(deflated).decode("ascii")
    bootstrap = (
        "$ProgressPreference='SilentlyContinue';"
        f"$b=[Convert]::FromBase64String('{b64}');"
        "$m=New-Object IO.MemoryStream(,$b);"
        "$d=New-Object IO.Compression.DeflateStream($m,[IO.Compression.CompressionMode]::Decompress);"
        "$r=New-Object IO.StreamReader($d,[Text.Encoding]::UTF8);"
        "try{[Console]::OutputEncoding=[Text.Encoding]::UTF8}catch{};"
        "try{Invoke-Expression $r.ReadToEnd()}catch{};exit 0"
    )
    encoded = base64.b64encode(bootstrap.encode("utf-16-le")).decode("ascii")
    cmd = (f"powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass "
           f"-EncodedCommand {encoded}")
    if len(cmd) >= 8000:
        raise SSHError(f"Internal error: the script is too large to send ({len(cmd)} characters).")
    return cmd
