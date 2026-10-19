"""Trust-on-first-use pinning of HTTPS / WSS certificates, the TLS counterpart of ssh_runner's host
key pinning. Used instead of verify=False for machines with "allow self-signed" on: the first
connect pins the certificate's SHA-256 (stored in settings.json), later connects must match."""
from __future__ import annotations

import hashlib
import socket
import ssl

import requests
from requests.adapters import HTTPAdapter

from core import store


class TLSPinError(Exception):
    pass


def _key(host: str, port: int) -> str:
    return f"tlsCert.{host}:{port}"


def forget(host: str, port: int):
    store.forget_host_key(_key(host, port))


def check_der(host: str, port: int, der: bytes):
    """Pins `der` on first sight; raises TLSPinError if it later differs."""
    fp = hashlib.sha256(der).hexdigest()
    pinned = store.pinned_host_key(_key(host, port))
    if pinned is None:
        store.pin_host_key(_key(host, port), fp)
    elif pinned != fp:
        raise TLSPinError(f"{host}:{port} is presenting a different TLS certificate than the one "
                          "first trusted. Use “Forget pinned host key” in the machine's settings "
                          "if this is expected (e.g. you renewed the certificate).")


def _probe(host: str, port: int) -> bytes:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=8) as s, ctx.wrap_socket(s) as t:
        return t.getpeercert(binary_form=True)


class _PinAdapter(HTTPAdapter):
    def __init__(self, fingerprint: str):
        self._fp = fingerprint
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["assert_fingerprint"] = self._fp
        super().init_poolmanager(*args, **kwargs)


def session(host: str, port: int) -> requests.Session:
    """A requests.Session that only talks to the pinned certificate (pinning it first if new)."""
    if store.pinned_host_key(_key(host, port)) is None:
        check_der(host, port, _probe(host, port))
    s = requests.Session()
    s.verify = False  # trust is the fingerprint assertion below, not the CA chain
    s.mount("https://", _PinAdapter(store.pinned_host_key(_key(host, port))))
    return s
