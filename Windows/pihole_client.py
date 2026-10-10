"""Client for Pi-hole v6's REST API.

Pi-hole v6 dropped the old unauthenticated `pihole -c -j` JSON stats dump that earlier versions
exposed, so getting queries/blocked/percent-blocked numbers now needs this API and the Pi-hole web
admin password (a separate credential from the machine's SSH login — see MachineStore.pihole_password).

Auth: POST /api/auth with {"password": ...} returns a session id ("sid") that's then sent back on a
`sid` header for subsequent calls (docs.pi-hole.net/api/auth/). Built from Pi-hole's own API docs, not
tested against a live instance from here — like opnsense_client.py, exceptions carry the HTTP status
so a wrong guess is visible and fixable instead of silently empty.
"""
from __future__ import annotations

from typing import Any, Optional

import requests


class PiholeError(Exception):
    pass


class PiholeClient:
    def __init__(self, host: str, password: str, use_https: bool = False, port: Optional[int] = None,
                 verify: bool = True, timeout: float = 10):
        scheme = "https" if use_https else "http"
        port_part = f":{port}" if port else ""
        self.base = f"{scheme}://{host}{port_part}/api"
        self.password = password
        self.verify = verify
        self.timeout = timeout
        self.sid: Optional[str] = None

    def _login(self):
        try:
            resp = requests.post(f"{self.base}/auth", json={"password": self.password},
                                  timeout=self.timeout, verify=self.verify)
        except Exception as e:
            raise PiholeError(f"Couldn't reach {self.base}/auth: {e}")
        if resp.status_code >= 400:
            raise PiholeError(f"Pi-hole login returned HTTP {resp.status_code} — check the password.")
        try:
            data = resp.json()
        except Exception:
            raise PiholeError("Pi-hole's /auth response wasn't JSON.")
        session = data.get("session") or {}
        if session.get("valid") is False:
            raise PiholeError("Pi-hole rejected that password.")
        sid = session.get("sid")
        if not sid:
            raise PiholeError("Pi-hole's /auth response didn't include a session id.")
        self.sid = sid

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def _request(self, method: str, path: str, retry: bool = True) -> Any:
        if self.sid is None:
            self._login()
        try:
            resp = requests.request(method, f"{self.base}/{path.lstrip('/')}", headers={"sid": self.sid},
                                     timeout=self.timeout, verify=self.verify)
        except Exception as e:
            raise PiholeError(f"Couldn't reach Pi-hole: {e}")
        if resp.status_code == 401 and retry:
            self.sid = None
            return self._request(method, path, retry=False)
        if resp.status_code >= 400:
            raise PiholeError(f"Pi-hole returned HTTP {resp.status_code} for {path}.")
        try:
            return resp.json()
        except Exception:
            return {}
