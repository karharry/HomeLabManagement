"""Thin REST client for the OPNsense API.

Auth is HTTP Basic with the API key as the username and the API secret as the
password (generated under System > Access > Users > <user> > API keys in the
OPNsense web UI). Every call is `<module>/<controller>/<command>`, e.g.
`core/service/search`, `diagnostics/system/systemResources`.

NOTE: built from OPNsense's published API docs and third-party client source
(pyopnsense/aiopnsense), not verified against a live OPNsense box — exact
JSON field names can vary a little by OPNsense version. Every caller in
opnsense_session.py reads fields defensively (tries a few plausible key
names, falls back to "no data" instead of raising) for exactly this reason.
If a page comes up empty on a real box, the fix is almost always just adding
the real field name to the relevant `_num`/`_str` lookup list.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

import tls_pin


class OPNsenseError(Exception):
    pass


class OPNsenseClient:
    def __init__(self, host: str, port: int, api_key: str, api_secret: str,
                 use_tls: bool = True, allow_self_signed: bool = True, timeout: float = 15):
        scheme = "https" if use_tls else "http"
        self.base = f"{scheme}://{host}:{port}/api"
        self.auth = (api_key, api_secret)
        self.verify = not allow_self_signed
        self.timeout = timeout
        self.host, self.port, self.pin = host, port, use_tls and allow_self_signed

    def get(self, path: str) -> Any:
        return self._send("GET", path)

    def post(self, path: str, json_body: Optional[dict] = None) -> Any:
        return self._send("POST", path, json_body)

    def _send(self, method: str, path: str, json_body: Optional[dict] = None) -> Any:
        url = f"{self.base}/{path.lstrip('/')}"
        try:
            http = tls_pin.session(self.host, self.port) if self.pin else requests
            resp = http.request(method, url, auth=self.auth, timeout=self.timeout,
                                json=json_body if method == "POST" else None,
                                **({} if self.pin else {"verify": self.verify}))
        except Exception as e:
            raise OPNsenseError(f"Couldn't reach {url}: {e}")
        if resp.status_code == 401:
            raise OPNsenseError("Authentication failed: check the API key and secret.")
        if resp.status_code >= 400:
            raise OPNsenseError(f"OPNsense returned HTTP {resp.status_code} for {path}.")
        try:
            return resp.json()
        except Exception:
            return {}
