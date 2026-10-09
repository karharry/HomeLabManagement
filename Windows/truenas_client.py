"""JSON-RPC 2.0 over WebSocket client for the TrueNAS SCALE middleware (`/api/current`, 24.10+).

Mirrors App/Backends/TrueNASClient.swift. Synchronous (uses the `websocket-client` package) with a
dedicated receiver thread that both resolves pending calls and dispatches the unsolicited
`reporting.realtime` push used for live CPU/memory.
"""
from __future__ import annotations

import itertools
import json
import ssl
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

import websocket


class TrueNASError(Exception):
    pass


@dataclass
class TNRealtime:
    cpu: Optional[float] = None
    memory_used: Optional[float] = None  # 0..1


class TrueNASClient:
    def __init__(self):
        self._ws: Optional[websocket.WebSocket] = None
        self._next_id = itertools.count(1)
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._recv_thread: Optional[threading.Thread] = None
        self.on_realtime: Optional[Callable[[TNRealtime], None]] = None
        self.on_close: Optional[Callable[[Exception], None]] = None
        self._closed = False

    def connect(self, host: str, port: int, use_tls: bool, allow_self_signed: bool,
                auth_api_key: Optional[str] = None, auth_user: Optional[str] = None,
                auth_password: Optional[str] = None):
        scheme = "wss" if use_tls else "ws"
        url = f"{scheme}://{host}:{port}/api/current"
        sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False} if (use_tls and allow_self_signed) else None
        try:
            self._ws = websocket.create_connection(url, timeout=15, sslopt=sslopt,
                                                     enable_multithread=True)
        except Exception as e:
            raise TrueNASError(f"Couldn't connect to {host}:{port} — {e}")
        self._closed = False
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

        if auth_api_key is not None:
            ok = self.call("auth.login_with_api_key", [auth_api_key])
        else:
            ok = self.call("auth.login", [auth_user, auth_password])
        if not ok:
            raise TrueNASError("Authentication failed: Credentials rejected")
        try:
            self.call_raw("core.subscribe", ["reporting.realtime"])
        except Exception:
            pass

    def disconnect(self):
        self._closed = True
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self._fail_all(TrueNASError("Not connected to TrueNAS."))

    def call(self, method: str, params: Optional[list] = None) -> Any:
        return self.call_raw(method, params)

    def call_raw(self, method: str, params: Optional[list] = None, timeout: float = 45) -> Any:
        if self._ws is None:
            raise TrueNASError("Not connected to TrueNAS.")
        params = params or []
        call_id = next(self._next_id)
        payload = {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
        ev = threading.Event()
        fut: dict = {"event": ev}
        with self._lock:
            self._pending[call_id] = fut
        try:
            self._ws.send(json.dumps(payload))
        except Exception as e:
            with self._lock:
                self._pending.pop(call_id, None)
            raise TrueNASError(f"SSH error: {e}") from e
        if not ev.wait(timeout):
            with self._lock:
                self._pending.pop(call_id, None)
            raise TrueNASError("The request timed out.")
        if "error" in fut:
            raise fut["error"]
        return fut.get("result")

    def _fail_all(self, error: Exception):
        with self._lock:
            pending = self._pending
            self._pending = {}
        for fut in pending.values():
            fut["error"] = error
            fut["event"].set()

    def _recv_loop(self):
        while not self._closed:
            try:
                msg = self._ws.recv()
            except Exception as e:
                if self._closed:
                    return
                self._fail_all(TrueNASError(f"Connection lost: {e}"))
                if self.on_close:
                    self.on_close(e)
                return
            if not msg:
                continue
            self._handle(msg)

    def _handle(self, raw: str):
        try:
            obj = json.loads(raw)
        except Exception:
            return
        if not isinstance(obj, dict):
            return
        call_id = obj.get("id")
        if call_id is not None:
            with self._lock:
                fut = self._pending.pop(call_id, None)
            if fut is None:
                return
            if "error" in obj:
                err = obj["error"] or {}
                message = err.get("message", "Unknown error")
                data = err.get("data") or {}
                if isinstance(data, dict) and data.get("reason"):
                    message = data["reason"]
                fut["error"] = TrueNASError(message)
            else:
                fut["result"] = obj.get("result")
            fut["event"].set()
        elif obj.get("method") == "collection_update":
            params = obj.get("params") or {}
            if params.get("collection") != "reporting.realtime":
                return
            fields = params.get("fields") or {}
            rt = TNRealtime()
            cpu = (fields.get("cpu") or {}).get("cpu") or {}
            if isinstance(cpu.get("usage"), (int, float)):
                rt.cpu = float(cpu["usage"])
            mem = fields.get("memory") or {}
            total = mem.get("physical_memory_total")
            avail = mem.get("physical_memory_available")
            if isinstance(total, (int, float)) and total > 0 and isinstance(avail, (int, float)):
                rt.memory_used = 1 - avail / total
            if self.on_realtime:
                self.on_realtime(rt)
