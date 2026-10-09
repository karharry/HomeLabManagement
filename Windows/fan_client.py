"""Client for the optional `fanctl` container some TrueNAS boxes run on a dedicated port.

Mirrors App/Backends/TrueNASFan.swift.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

import requests


class FanError(Exception):
    pass


@dataclass
class FanTemp:
    kind: str
    temp: float


@dataclass
class FanStatus:
    temps: dict[str, FanTemp] = field(default_factory=dict)
    ctl_temp: float | None = None
    pwm_pct: float | None = None
    rpm: int | None = None
    error: str | None = None
    src: str | None = None


@dataclass
class FanConfig:
    mode: str
    manual_pct: float
    min_pct: float
    cpu_offset: float
    hysteresis: float
    curve: list[list[float]]
    interval: int | None = None

    def normalized(self) -> "FanConfig":
        c = FanConfig(**{**self.__dict__})
        c.curve = sorted(self.curve, key=lambda p: p[0])
        return c

    def to_json(self) -> dict:
        return {
            "mode": self.mode, "manual_pct": self.manual_pct, "min_pct": self.min_pct,
            "cpu_offset": self.cpu_offset, "hysteresis": self.hysteresis, "curve": self.curve,
            "interval": self.interval,
        }

    @staticmethod
    def from_json(d: dict) -> "FanConfig":
        return FanConfig(mode=d.get("mode", "auto"), manual_pct=d.get("manual_pct", 0),
                          min_pct=d.get("min_pct", 0), cpu_offset=d.get("cpu_offset", 0),
                          hysteresis=d.get("hysteresis", 0), curve=d.get("curve", []),
                          interval=d.get("interval"))


class FanClient:
    def __init__(self, base_url: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.password = password

    def _headers(self) -> dict:
        if not self.password:
            return {}
        token = base64.b64encode(f"admin:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _send(self, path: str, method: str = "GET", json_body: dict | None = None) -> dict:
        try:
            resp = requests.request(method, f"{self.base_url}/{path}", json=json_body,
                                     headers=self._headers(), timeout=15)
        except Exception as e:
            raise FanError(str(e))
        if resp.status_code >= 400:
            if resp.status_code == 401:
                raise FanError("The fan controller needs a password.")
            msg = None
            try:
                msg = resp.json().get("error")
            except Exception:
                pass
            raise FanError(msg or f"Fan controller returned HTTP {resp.status_code}.")
        try:
            return resp.json()
        except Exception:
            return {}

    @staticmethod
    def _snake_temps(obj: dict) -> dict[str, FanTemp]:
        out = {}
        for k, v in (obj.get("temps") or {}).items():
            out[k] = FanTemp(kind=v.get("kind", ""), temp=v.get("temp", 0.0))
        return out

    def status(self) -> FanStatus:
        obj = self._send("api/status")
        return FanStatus(temps=self._snake_temps(obj), ctl_temp=obj.get("ctl_temp"),
                          pwm_pct=obj.get("pwm_pct"), rpm=obj.get("rpm"), error=obj.get("error"),
                          src=obj.get("src"))

    def config(self) -> FanConfig:
        return FanConfig.from_json(self._send("api/config"))

    def save(self, c: FanConfig) -> FanConfig:
        return FanConfig.from_json(self._send("api/config", method="POST", json_body=c.normalized().to_json()))
