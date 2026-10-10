"""Manages an OPNsense firewall/router over its native REST API (API key + secret, HTTP Basic auth).

Unlike the SSH-managed kinds, there's no shell script round trip here — each poll fires a handful of
GET/POST calls at documented API endpoints. See opnsense_client.py's docstring: this was built from
OPNsense's API docs rather than tested against a live box, so field lookups are deliberately
defensive (several plausible key names tried per field) and degrade to "no data" instead of crashing
if a field name doesn't match on a particular OPNsense version.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from core import Machine, MachineStore, ConnectionState, StorageItem, ServiceRow, AlertItem
from machine_session import MachineSession
from opnsense_client import OPNsenseClient, OPNsenseError


@dataclass
class OPNInterface:
    name: str
    device: str
    status: str
    addresses: list[str] = field(default_factory=list)

    @property
    def is_up(self) -> bool:
        return self.status.lower() in ("up", "active", "1")


@dataclass
class OPNDHCPLease:
    ip: str
    hostname: str
    mac: str
    interface: str
    state: str = ""


@dataclass
class OPNUpdate:
    id: str
    name: str
    newVersion: str
    isSecurity: bool = False
    source: str = ""


@dataclass
class OPNDnsmasqHost:
    uuid: str
    host: str
    domain: str
    ip: str
    mac: str = ""
    descr: str = ""


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _num(d: dict, *keys) -> Optional[float]:
    v = _first(d, *keys)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().rstrip("%").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _str(d: dict, *keys) -> str:
    v = _first(d, *keys)
    return str(v) if v is not None else ""


def _rows(obj: Any) -> list[dict]:
    """Normalizes the handful of shapes OPNsense's "search" endpoints return a list under
    (`{"rows": [...]}`, a bare list, or a dict keyed by id/name) into a plain list of dicts."""
    if isinstance(obj, dict):
        if isinstance(obj.get("rows"), list):
            return [r for r in obj["rows"] if isinstance(r, dict)]
        # dict-of-dicts, e.g. getInterfaceConfig keyed by interface name
        if obj and all(isinstance(v, dict) for v in obj.values()):
            out = []
            for key, v in obj.items():
                row = dict(v)
                row.setdefault("_key", key)
                out.append(row)
            return out
        return []
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    return []


def _describe(obj: Any, depth: int = 0) -> str:
    """Short structural summary of a JSON value for diagnostics — lets a failed field guess show
    up as 'here's what the response actually looked like' instead of just 'didn't work'."""
    if isinstance(obj, dict):
        if not obj:
            return "empty object {}"
        items = list(obj.items())[:6]
        parts = []
        for k, v in items:
            if isinstance(v, dict):
                parts.append(f"{k}: {{{', '.join(list(v.keys())[:5])}}}" if depth == 0 else f"{k}: {{...}}")
            elif isinstance(v, list):
                first = f", first item: {{{', '.join(list(v[0].keys())[:8])}}}" if v and isinstance(v[0], dict) else ""
                parts.append(f"{k}: [{len(v)} items{first}]")
            else:
                parts.append(f"{k}: {v!r}"[:40])
        more = f", +{len(obj) - len(items)} more" if len(obj) > len(items) else ""
        return "{" + ", ".join(parts) + more + "}"
    if isinstance(obj, list):
        return f"[{len(obj)} items]" + (f", first: {_describe(obj[0], depth + 1)}" if obj else "")
    return repr(obj)[:60]


_SIZE_UNITS = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def _parse_size_string(s: Optional[str]) -> Optional[float]:
    """Parses df-style human sizes like '118G', '512M', '2.1T' into bytes."""
    if not s:
        return None
    s = s.strip()
    if not s or s[-1].lower() not in _SIZE_UNITS:
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return float(s[:-1]) * _SIZE_UNITS[s[-1].lower()]
    except ValueError:
        return None


def _bool_running(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "true", "running", "yes")


class OPNsenseSession(MachineSession):
    def __init__(self, machine: Machine):
        super().__init__(machine)
        self.interfaces: list[OPNInterface] = []
        self.dhcp_leases: list[OPNDHCPLease] = []
        self.updates: list[OPNUpdate] = []
        self.updates_checked = False
        self.firmware_status_text: Optional[str] = None
        self._prev_if_bytes: Optional[tuple[float, float, float]] = None  # rx, tx, time
        self.dnsmasq_hosts: list[OPNDnsmasqHost] = []
        self.dnsmasq_hosts_error: Optional[str] = None

    @property
    def consoleIsAvailable(self) -> bool:
        return False

    @property
    def consoleUnavailableReason(self) -> str:
        return "OPNsense is managed over its REST API here, not SSH, so there's no console."

    def _client(self) -> OPNsenseClient:
        return OPNsenseClient(self.machine.host, self.machine.port, self.machine.user,
                               MachineStore.secret(self.machine.id), use_tls=self.machine.useTLS,
                               allow_self_signed=self.machine.allowSelfSigned)

    def _poll(self) -> bool:
        if self.state.kind != ConnectionState.CONNECTED:
            self.state = ConnectionState(ConnectionState.CONNECTING)
        c = self._client()
        diag_errors: list[str] = []

        def try_get(path: str) -> Any:
            try:
                return c.get(path) or {}
            except Exception as e:
                diag_errors.append(f"{path}: {e}")
                return {}

        def try_post(path: str) -> Any:
            try:
                return c.post(path) or {}
            except Exception as e:
                diag_errors.append(f"{path}: {e}")
                return {}

        try:
            info = c.get("diagnostics/system/systemInformation") or {}
        except OPNsenseError as e:
            self.state = ConnectionState(ConnectionState.FAILED, str(e))
            return False
        except Exception as e:
            self.state = ConnectionState(ConnectionState.FAILED, f"Unexpected error: {e}")
            return False

        self.host_name = _str(info, "hostname", "name") or self.machine.host
        version = _str(info, "versionstring", "product_version", "version")
        if not version and isinstance(info.get("versions"), list) and info["versions"]:
            # Some versions drop the scalar version fields entirely and only expose a "versions"
            # list like ["OPNsense 26.7.6_3-amd64", "FreeBSD 15.1-RELEASE-p4", "OpenSSL 3.5.9"] —
            # the first entry already starts with "OPNsense ", so don't double-prefix it.
            version = str(info["versions"][0])
            self.host_os = version
        else:
            self.host_os = "OPNsense " + version
        uptime_val = _first(info, "uptime", "uptime_seconds")
        if isinstance(uptime_val, (int, float)):
            self.host_uptime = float(uptime_val)
        elif isinstance(uptime_val, str):
            self.host_uptime = _parse_uptime_string(uptime_val)
        else:
            self.host_uptime = None

        res = try_get("diagnostics/system/systemResources")
        cpu = _num(res, "cpu", "cpu_usage", "cpu_total", "load")
        if cpu is None and isinstance(res.get("cpu"), dict):
            cpu = _num(res["cpu"], "total", "used", "usage", "load")
        need_uptime = self.host_uptime is None
        if cpu is None or need_uptime:
            # systemResources doesn't carry CPU on every version (confirmed on at least one real
            # box: it only had a "memory" key), and systemInformation doesn't always carry uptime
            # either — diagnostics/activity/getActivity is the non-streaming alternative (the live
            # dashboard widget otherwise uses a websocket).
            activity = try_get("diagnostics/activity/getActivity")
            if cpu is None:
                cpu = _num(activity, "cpu", "cpu_usage", "total")
                if cpu is None and isinstance(activity.get("cpu"), dict):
                    cpu = _num(activity["cpu"], "total", "used", "usage", "user")
                if cpu is None:
                    top_rows = _rows(activity.get("top") if isinstance(activity, dict) else None)
                    if top_rows:
                        # Some versions expose a process table instead of a single summary number;
                        # "idle" process's own time share approximates overall idle, so 100-idle ~ busy.
                        idle = next((_num(r, "pcpu", "percent") for r in top_rows
                                     if _str(r, "command", "name").lower() in ("idle", "[idle]")), None)
                        if idle is not None:
                            cpu = max(0.0, 100 - idle)
            # Newest versions return no structured summary or process table at all — just `top`'s
            # raw header lines (confirmed on a real 26.7.6 box) — parse those directly for whichever
            # of CPU%/uptime is still missing.
            if cpu is None or need_uptime:
                act_uptime, act_cpu = _parse_activity_summary(activity)
                if cpu is None:
                    cpu = act_cpu
                if need_uptime:
                    self.host_uptime = act_uptime
            if cpu is None and activity:
                diag_errors.append(f"diagnostics/activity/getActivity: no CPU % found — {_describe(activity)}")
        if cpu is not None:
            self.cpu_percent = min(100, max(0, cpu))
        elif res:
            diag_errors.append(f"diagnostics/system/systemResources: no CPU % found — {_describe(res)}")
        self.push(self.cpu_history, self.cpu_percent)
        self.host_uptime = self.host_uptime or 0.0

        mem = res.get("memory") if isinstance(res.get("memory"), dict) else res
        mem_total = _num(mem, "total", "memory_total", "total_bytes")
        mem_used = _num(mem, "used", "memory_used", "used_bytes")
        mem_pct = _num(mem, "used_pct", "usage", "percent")
        if mem_total:
            self.mem_total = mem_total
            if mem_used is not None:
                self.mem_used = mem_used
            elif mem_pct is not None:
                self.mem_used = mem_total * mem_pct / 100
            self.push(self.mem_history, (self.mem_used / self.mem_total * 100) if self.mem_total else None)
        elif mem_pct is not None:
            # Some versions only expose a percentage with no absolute total.
            self.mem_total = 100
            self.mem_used = mem_pct
            self.push(self.mem_history, mem_pct)

        disk = try_get("diagnostics/system/systemDisk")
        disk_rows = _rows(disk) or _rows(disk.get("devices") if isinstance(disk, dict) else None)
        storage = []
        for row in disk_rows:
            mount = (_str(row, "mountpoint", "mounted_on", "mount", "target", "device", "fs",
                          "filesystem", "_key") or "disk")
            size = (_num(row, "blocks", "size", "total", "1k-blocks", "1K-blocks", "disk_size")
                    or _parse_size_string(_str(row, "size", "blocks")) or 0)
            used = _num(row, "used", "used_bytes") or _parse_size_string(_str(row, "used"))
            avail = _num(row, "avail", "available", "free") or _parse_size_string(_str(row, "avail", "available"))
            pct = _num(row, "capacity", "used_pct", "capacity_pct", "pct")
            if used is None and avail is not None and size:
                used = max(0.0, size - avail)
            if used is None and pct is not None and size:
                used = size * pct / 100
            if not size and used is not None and avail is not None:
                size = used + avail
            if size:
                storage.append(StorageItem(id=mount, title=mount, size=int(size), used=int(used or 0)))
        if storage:
            self.storage = storage
        elif disk_rows:
            diag_errors.append(f"diagnostics/system/systemDisk: found {len(disk_rows)} device row(s) but "
                                f"couldn't read a size from any of them — first row: {_describe(disk_rows[0])}")
        else:
            diag_errors.append(f"diagnostics/system/systemDisk: no disk rows found — {_describe(disk)}")

        services = _rows(try_post("core/service/search"))
        if services:
            self.services = sorted(
                [ServiceRow(id=_str(s, "id", "name"), name=_str(s, "name", "id"),
                            displayName=_str(s, "description", "name") or _str(s, "name"),
                            state=("running" if _bool_running(_first(s, "running", "status")) else "stopped"),
                            isRunning=_bool_running(_first(s, "running", "status"))) for s in services],
                key=lambda r: r.displayName.lower())
            self.services_loaded = True

        if_cfg = _rows(try_get("diagnostics/interface/getInterfaceConfig"))
        if if_cfg:
            self.interfaces = [
                OPNInterface(name=_str(r, "description", "_key", "if") or _str(r, "_key"),
                             device=_str(r, "if", "_key"),
                             status=_str(r, "status") or ("up" if _first(r, "enable") else "down"),
                             addresses=[a for a in [_str(r, "ipaddr"), _str(r, "ipaddrv6")] if a and a.lower() != "none"])
                for r in if_cfg
            ]

        if_stats = _rows(try_get("diagnostics/interface/getInterfaceStatistics"))
        if if_stats:
            rx_total = sum(_num(r, "bytes received", "inbytes", "received_bytes") or 0 for r in if_stats)
            tx_total = sum(_num(r, "bytes transmitted", "outbytes", "transmitted_bytes") or 0 for r in if_stats)
            now = time.time()
            if self._prev_if_bytes:
                prx, ptx, pt = self._prev_if_bytes
                dt = now - pt
                if dt > 0.5:
                    self.net_rx_rate = max(0.0, rx_total - prx) / dt
                    self.net_tx_rate = max(0.0, tx_total - ptx) / dt
            self._prev_if_bytes = (rx_total, tx_total, now)

        # Surfaced as info-level alerts rather than silently swallowed: since these endpoint paths
        # are unverified against a live box (see opnsense_client.py's docstring), a wrong guess
        # should be visible and reportable instead of just leaving a page blank.
        self.extra_alerts = [AlertItem(id=f"diag-{i}", level=AlertItem.INFO,
                                        title="An OPNsense API call didn't return usable data",
                                        detail=msg)
                              for i, msg in enumerate(diag_errors)]

        self.state = ConnectionState(ConnectionState.CONNECTED)
        self.last_refresh = time.time()
        return True

    def _close_backend(self):
        pass

    # -- Services --
    def refresh_services(self):
        self.submit(lambda: self._poll(), "services")

    def service_action(self, s: ServiceRow, verb: str):
        def do():
            c = self._client()
            try:
                c.post(f"core/service/{verb}/{s.id}")
                self.action_error_text = None
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
            time.sleep(1)
            self._poll()
        self.submit(do, "services")

    # -- Network --
    def refresh_network(self):
        self.submit(lambda: self._poll(), "network")

    # -- DHCP leases --
    # OPNsense can run the unified Dnsmasq DNS/DHCP service, the legacy ISC DHCP server, or (24.1+)
    # Kea — each has its own API module with a different endpoint, so all three are tried in turn;
    # whichever one answers is whichever backend this box actually has configured. Confirmed against
    # a real box: the Services page already shows which one is actually running (look for "Dnsmasq
    # DNS/DHCP", "ISC DHCPv4", or "Kea DHCPv4" there) if this list ever needs a 4th guess.
    _DHCP_LEASE_ENDPOINTS = ("dnsmasq/leases/search", "dhcpv4/leases/search_lease", "kea/leases4/search")

    def refresh_dhcp_leases(self):
        def do():
            c = self._client()
            errors = []
            rows = []
            any_succeeded = False
            for path in self._DHCP_LEASE_ENDPOINTS:
                try:
                    rows = _rows(c.get(path))
                    any_succeeded = True
                    if rows:
                        break
                except Exception as e:
                    errors.append(f"{path}: {e}")
            # Only surface an error if every endpoint we know about failed outright — a real box
            # with genuinely no active leases right now should just show "no leases", not a popup.
            if not any_succeeded and errors:
                msg = " / ".join(errors)
                self.action_error_text = msg
                self.action_error.emit(msg)
            self.dhcp_leases = [
                OPNDHCPLease(ip=_str(r, "address", "ip", "ip-address"),
                             hostname=_str(r, "hostname", "client-hostname") or "–",
                             mac=_str(r, "mac", "hwaddr", "hw-address"), interface=_str(r, "if", "interface"),
                             state=_str(r, "state", "online"))
                for r in rows
            ]
        self.submit(do, "dhcp")

    # -- Dnsmasq static hosts (Services > Dnsmasq DNS & DHCP > Hosts in the OPNsense UI) --
    # These double as DHCP reservations and local DNS entries when Dnsmasq is the DNS/DHCP backend.
    def refresh_dnsmasq_hosts(self):
        def do():
            try:
                self._refresh_dnsmasq_hosts_sync()
                self.dnsmasq_hosts_error = None
            except Exception as e:
                self.dnsmasq_hosts_error = str(e)
        self.submit(do, "dnsmasqhosts")

    def add_dnsmasq_host(self, host: str, ip: str, mac: str, domain: str, descr: str, on_done):
        on_done = self._main_thread(on_done)
        def do():
            host_t, ip_t = host.strip(), ip.strip()
            if not host_t or not ip_t:
                on_done("Enter both a hostname and an IP address.")
                return
            c = self._client()
            # Field names are a best-effort guess at Dnsmasq.xml's Host model (host/domain/ip/
            # hwaddr/descr) — if OPNsense rejects this, its "validations" response is surfaced
            # directly below so the real field name(s) show up instead of a generic failure.
            payload = {"host": {"host": host_t, "domain": domain.strip(), "ip": ip_t,
                                 "hwaddr": mac.strip(), "descr": descr.strip()}}
            try:
                result = c.post("dnsmasq/settings/add_host", payload)
            except Exception as e:
                on_done(str(e))
                return
            if isinstance(result, dict) and result.get("result") not in ("saved", "ok", None):
                validations = result.get("validations")
                on_done(f"OPNsense rejected the host: {validations or result}")
                return
            self._apply_dnsmasq_sync()
            try:
                self._refresh_dnsmasq_hosts_sync()
            except Exception:
                pass
            on_done(None)
        self.submit(do, "dnsmasqhosts")

    def remove_dnsmasq_host(self, h: OPNDnsmasqHost, on_done):
        on_done = self._main_thread(on_done)
        def do():
            c = self._client()
            try:
                c.post(f"dnsmasq/settings/del_host/{h.uuid}")
            except Exception as e:
                on_done(str(e))
                return
            self._apply_dnsmasq_sync()
            try:
                self._refresh_dnsmasq_hosts_sync()
            except Exception:
                pass
            on_done(None)
        self.submit(do, "dnsmasqhosts")

    def _refresh_dnsmasq_hosts_sync(self):
        rows = _rows(self._client().get("dnsmasq/settings/search_host"))
        self.dnsmasq_hosts = sorted(
            [OPNDnsmasqHost(uuid=_str(r, "uuid", "uuidSelector") or _str(r, "_key"),
                             host=_str(r, "host", "hostname"), domain=_str(r, "domain"),
                             ip=_str(r, "ip", "address"), mac=_str(r, "hwaddr", "mac", "hw-address"),
                             descr=_str(r, "descr", "description"))
             for r in rows],
            key=lambda h: h.host.lower())

    def _apply_dnsmasq_sync(self):
        # OPNsense's model-based controllers stage the change; a reconfigure call is what actually
        # applies it to the running service — same pattern as the Save button in the web UI.
        try:
            self._client().post("dnsmasq/service/reconfigure")
        except Exception:
            pass

    # -- Updates (firmware status; applying updates remotely isn't offered, same policy as the
    # other kinds, and doubly so for a router/firewall) --
    def refresh_updates(self):
        def do():
            c = self._client()
            try:
                fw = c.get("core/firmware/status") or {}
            except Exception as e:
                self.action_error_text = str(e)
                return
            status = _str(fw, "status")
            self.firmware_status_text = status or None
            new_version = _str(fw, "new_version", "product_version_new")
            packages = _first(fw, "new_packages", "upgrade_packages") or []
            updates = []
            if status and status.lower() not in ("", "none", "none_fetched", "up_to_date", "ok"):
                updates.append(OPNUpdate(id="firmware", name="OPNsense firmware",
                                          newVersion=new_version or status, isSecurity=False))
            if isinstance(packages, list):
                for p in packages:
                    name = p.get("name") if isinstance(p, dict) else str(p)
                    ver = p.get("new_version", "") if isinstance(p, dict) else ""
                    updates.append(OPNUpdate(id=f"pkg-{name}", name=name, newVersion=ver, source="Package"))
            self.updates = updates
            self.updates_checked = True
        self.submit(do, "updates")

    def upgrade_all(self, on_done):
        on_done("Applying firmware/package updates remotely isn't offered here — a failed or "
                "interrupted firewall update can leave the box unreachable. Use System > Firmware in "
                "the OPNsense web UI, on-site or over a connection you trust.")

    # -- Dashboard power actions --
    def reboot(self):
        def do():
            try:
                self._client().post("core/system/reboot")
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
        self.submit(do)

    def shutdown(self):
        def do():
            try:
                self._client().post("core/system/halt")
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
        self.submit(do)


def _parse_uptime_string(s: str) -> float:
    """Best-effort parse of OPNsense's human-readable uptime (observed shapes vary by version,
    something like "3 days, 02:14:05" or "02:14:05") into seconds."""
    import re
    total = 0.0
    m = re.search(r"(\d+)\+(\d{1,2}):(\d{2}):(\d{2})", s)  # BSD top's "D+HH:MM:SS" (e.g. "0+19:22:24")
    if m:
        d, h, mi, se = (int(x) for x in m.groups())
        return d * 86400 + h * 3600 + mi * 60 + se
    m = re.search(r"(\d+)\s*day", s, re.IGNORECASE)
    if m:
        total += int(m.group(1)) * 86400
    m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", s)
    if m:
        h, mi, se = (int(x) for x in m.groups())
        total += h * 3600 + mi * 60 + se
    return total


def _parse_activity_summary(activity: dict) -> tuple[Optional[float], Optional[float]]:
    """Some OPNsense versions' diagnostics/activity/getActivity has no structured CPU/uptime field
    at all — just `top`'s raw header lines, e.g.:
      "last pid: 11190;  load averages: 0.27, 0.30, 0.30  up 0+19:22:24    23:43:39"
      "CPU:  0.0% user,  0.0% nice,  0.2% system,  1.2% interrupt, 98.6% idle"
    Returns (uptime_seconds, cpu_percent), either possibly None if its line wasn't found."""
    import re
    headers = activity.get("headers") if isinstance(activity, dict) else None
    if not isinstance(headers, list):
        return None, None
    uptime = cpu = None
    for line in headers:
        if not isinstance(line, str):
            continue
        if uptime is None and " up " in line:
            m = re.search(r"\bup\s+(.+?)(?:\s+\d{1,2}:\d{2}:\d{2}\s*$|$)", line)
            if m:
                uptime = _parse_uptime_string(m.group(1))
        if cpu is None and line.strip().startswith("CPU:"):
            m = re.search(r"([\d.]+)%\s*idle", line)
            if m:
                cpu = max(0.0, 100.0 - float(m.group(1)))
    return uptime, cpu
