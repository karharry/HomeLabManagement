"""Manages a TrueNAS SCALE machine over JSON-RPC-over-WebSocket. SSH (optional) adds VM live stats,
Docker containers, and a console; the fan controller (optional) talks plain HTTP.

Mirrors App/Backends/TrueNASSession.swift + App/Backends/TrueNASClient.swift's model structs.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Optional

from core import (Machine, MachineKind, MachineStore, NavItem, ConnectionState, StorageItem, ServiceRow,
                   AlertItem, Fmt, sort_nav_items)
from machine_session import MachineSession
from ssh_runner import SSHRunner, SSHSettings, SSHError
from truenas_client import TrueNASClient, TrueNASError
from fan_client import FanClient, FanConfig, FanError
import docker_
from wireguard_ import WireGuardTunnel, tunnels as wg_tunnels, is_valid_name as wg_valid_name


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class TNApp:
    name: str
    state: str
    upgrade_available: Optional[bool] = None
    human_version: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self.state == "RUNNING"


@dataclass
class TNVMStatus:
    state: str = "UNKNOWN"
    pid: Optional[int] = None


@dataclass
class TNVMDevice:
    dtype: str
    path: Optional[str] = None


@dataclass
class TNVM:
    id: int
    name: str
    status: TNVMStatus
    vcpus: int = 1
    cores: int = 1
    threads: int = 1
    memory: Optional[int] = None  # MiB
    autostart: Optional[bool] = None
    devices: list[TNVMDevice] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.status.state == "RUNNING"

    @property
    def total_cpus(self) -> int:
        return max(1, (self.vcpus or 1) * (self.cores or 1) * (self.threads or 1))


@dataclass
class TNProcSample:
    cpu_ticks: Optional[float] = None
    rss_kib: Optional[float] = None
    read_bytes: Optional[float] = None
    write_bytes: Optional[float] = None
    nets: list[tuple] = field(default_factory=list)  # (name, rx, tx)
    timestamp: float = field(default_factory=time.time)


TICKS_PER_SECOND = 100.0


def _parse_proc_samples(text: str) -> dict[int, TNProcSample]:
    result: dict[int, TNProcSample] = {}
    pid: Optional[int] = None
    cur = TNProcSample()

    def flush():
        if pid is not None:
            result[pid] = cur

    for raw in text.splitlines():
        line = raw.strip("\r")
        if line.startswith("PID "):
            flush()
            try:
                pid = int(line[4:].strip())
            except ValueError:
                pid = None
            cur = TNProcSample()
        elif line.startswith("STAT "):
            close = line.rfind(")")
            if close != -1:
                f = line[close + 1:].split()
                if len(f) > 12:
                    try:
                        cur.cpu_ticks = float(f[11]) + float(f[12])
                    except ValueError:
                        pass
        else:
            t = line.split()
            if not t:
                continue
            if t[0] == "RSS" and len(t) > 1:
                try:
                    cur.rss_kib = float(t[1])
                except ValueError:
                    pass
            elif t[0] == "IO" and len(t) > 2:
                try:
                    if t[1] == "read_bytes:":
                        cur.read_bytes = float(t[2])
                    if t[1] == "write_bytes:":
                        cur.write_bytes = float(t[2])
                except ValueError:
                    pass
            elif t[0] == "NET" and len(t) > 3:
                try:
                    cur.nets.append((t[1], float(t[2]), float(t[3])))
                except ValueError:
                    pass
    flush()
    return result


@dataclass
class TNVMLiveDisk:
    name: str
    capacity: float
    allocation: float


@dataclass
class TNVMLive:
    HISTORY_LENGTH = 60
    vcpus: int = 1
    cpu: Optional[float] = None
    mem_used: Optional[float] = None
    mem_total: Optional[float] = None
    rx_rate: Optional[float] = None
    tx_rate: Optional[float] = None
    disk_read_rate: Optional[float] = None
    disk_write_rate: Optional[float] = None
    disks: list[TNVMLiveDisk] = field(default_factory=list)
    nics: list[str] = field(default_factory=list)
    cpu_history: list[float] = field(default_factory=list)
    mem_history: list[float] = field(default_factory=list)
    rx_history: list[float] = field(default_factory=list)
    tx_history: list[float] = field(default_factory=list)
    read_history: list[float] = field(default_factory=list)
    write_history: list[float] = field(default_factory=list)

    def update(self, now: TNProcSample, previous: Optional[TNProcSample], vm: TNVM):
        self.vcpus = vm.total_cpus
        self.nics = [n[0] for n in now.nets]
        self.mem_total = vm.memory * 1_048_576 if vm.memory else None
        self.mem_used = now.rss_kib * 1024 if now.rss_kib is not None else None
        if previous is not None:
            dt = now.timestamp - previous.timestamp
            if dt > 0.5:
                if now.cpu_ticks is not None and previous.cpu_ticks is not None:
                    self.cpu = min(100, max(0, (now.cpu_ticks - previous.cpu_ticks) / TICKS_PER_SECOND / (dt * self.vcpus) * 100))

                def rate(a, b):
                    if a is None or b is None:
                        return None
                    return max(0, a - b) / dt

                now_rx = sum(n[1] for n in now.nets)
                prev_rx = sum(n[1] for n in previous.nets)
                now_tx = sum(n[2] for n in now.nets)
                prev_tx = sum(n[2] for n in previous.nets)
                self.tx_rate = rate(now_rx, prev_rx)
                self.rx_rate = rate(now_tx, prev_tx)
                self.disk_read_rate = rate(now.read_bytes, previous.read_bytes)
                self.disk_write_rate = rate(now.write_bytes, previous.write_bytes)

        def push(arr, v):
            if v is None:
                return
            arr.append(v)
            if len(arr) > self.HISTORY_LENGTH:
                del arr[:len(arr) - self.HISTORY_LENGTH]

        push(self.cpu_history, self.cpu)
        push(self.mem_history, (self.mem_used / max(self.mem_total, 1) * 100) if (self.mem_used is not None and self.mem_total) else None)
        push(self.rx_history, self.rx_rate)
        push(self.tx_history, self.tx_rate)
        push(self.read_history, self.disk_read_rate)
        push(self.write_history, self.disk_write_rate)


@dataclass
class TNDataset:
    id: str
    name: str
    type: str
    used: int
    available: int
    mountpoint: str


@dataclass
class TNSnapshotRow:
    id: str
    name: str
    dataset: str
    used: int


@dataclass
class TNShare:
    id: str
    name: str
    path: str
    comment: str
    enabled: bool
    kind: str


@dataclass
class TNInterface:
    id: str
    name: str
    addresses: list[str]
    link_state: str


@dataclass
class TNProtectionTask:
    id: str
    kind: str
    summary: str
    enabled: bool


@dataclass
class TNUserAccount:
    id: int
    username: str
    full_name: str
    locked: bool
    uid: int


@dataclass
class TNVPNContainer:
    name: str
    container_name: str
    running: bool
    tunnel: Optional[WireGuardTunnel] = None


def _tn_num(d: dict, k: str) -> Optional[float]:
    v = d.get(k)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        p = v.get("parsed")
        return float(p) if isinstance(p, (int, float)) else None
    return None


def _tn_str(d: dict, k: str) -> Optional[str]:
    v = d.get(k)
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        p = v.get("parsed")
        return p if isinstance(p, str) else None
    return None


def _tn_bool(d: dict, k: str) -> Optional[bool]:
    v = d.get(k)
    return v if isinstance(v, bool) else None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

VPN_PREFIX = "ix-homelab-vpn-"


class TrueNASSession(MachineSession):
    def __init__(self, machine: Machine):
        super().__init__(machine)
        self._client: Optional[TrueNASClient] = None
        self._cpu_rt: Optional[float] = None
        self._mem_rt: Optional[float] = None
        self._raw_alerts: list[dict] = []

        self.apps: list[TNApp] = []
        self.vms: list[TNVM] = []
        self.vm_live: dict[int, TNVMLive] = {}
        self.vm_stats_error: Optional[str] = None
        self._vm_collector = SSHRunner()
        self._vm_stats_active = False
        self._previous_samples: dict[int, TNProcSample] = {}
        self._vm_disks: dict[int, list[TNVMLiveDisk]] = {}
        self._last_zvol_fetch = 0.0

        self.containers: list[docker_.DockerContainer] = []
        self.containers_loaded = False
        self.containers_error: Optional[str] = None
        self._container_collector = SSHRunner()
        self._containers_active = False

        self.fan_status = None
        self.fan_config: Optional[FanConfig] = None
        self.fan_error: Optional[str] = None
        self.fan_rpm_history: list[float] = []
        self.fan_duty_history: list[float] = []
        self.fan_temp_history: list[float] = []
        self._fan_active = False

        self.install_status: Optional[str] = None
        self.install_running = False
        self.install_failed = False

        self.datasets: list[TNDataset] = []
        self.snapshots: list[TNSnapshotRow] = []
        self.shares: list[TNShare] = []
        self.interfaces: list[TNInterface] = []
        self.protection_tasks: list[TNProtectionTask] = []
        self.user_accounts: list[TNUserAccount] = []
        self.update_status: Optional[str] = None
        self.update_version: Optional[str] = None

        self._console_runner = SSHRunner()

        self.vpn_containers: list[TNVPNContainer] = []
        self.vpn_error: Optional[str] = None

        self._last_poll_time = 0.0
        self._last_vm_sample_time = 0.0
        self._last_container_load_time = 0.0
        self._last_fan_poll_time = 0.0
        self._last_power_sample_time = 0.0
        self._prev_rapl: Optional[tuple] = None

    @property
    def nav_items(self):
        items = set(self.machine.kind.nav_items)
        if self.is_ssh_configured:
            items.add(NavItem.vpn)
        return sort_nav_items(items)

    @property
    def consoleIsAvailable(self) -> bool:  # ConsoleHosting
        return self.is_ssh_configured

    @property
    def consoleUnavailableReason(self) -> str:
        return "Enable SSH in this machine's Edit sheet to use the console."

    @property
    def is_ssh_configured(self) -> bool:
        return self.machine.sshEnabled and bool(self.machine.sshUser)

    def _secret(self) -> str:
        return MachineStore.secret(self.machine.id)

    def _ssh_settings(self) -> SSHSettings:
        return SSHSettings(host=self.machine.host, port=self.machine.sshPort, user=self.machine.sshUser,
                            password=MachineStore.ssh_password(self.machine.id), use_sudo=self.machine.sshSudo)

    # -- poll --
    def _poll(self) -> bool:
        if self._client is None:
            self.state = ConnectionState(ConnectionState.CONNECTING)
            c = TrueNASClient()
            c.on_realtime = self._apply_realtime
            c.on_close = self._on_close
            try:
                if self.machine.usePassword:
                    c.connect(self.machine.host, self.machine.port, self.machine.useTLS,
                               self.machine.allowSelfSigned, auth_user=self.machine.user,
                               auth_password=self._secret())
                else:
                    c.connect(self.machine.host, self.machine.port, self.machine.useTLS,
                               self.machine.allowSelfSigned, auth_api_key=self._secret())
            except Exception as e:
                self.state = ConnectionState(ConnectionState.FAILED, str(e))
                return False
            self._client = c

        ok = self._refresh_all()

        # Piggyback on the normal poll cadence for optional sub-features, like the Swift timers do.
        now = time.time()
        if self._vm_stats_active and now - self._last_vm_sample_time > 5:
            self._last_vm_sample_time = now
            self._sample_vms()
        if self._containers_active and now - self._last_container_load_time > 10:
            self._last_container_load_time = now
            self._load_containers_impl()
        if self._fan_active and now - self._last_fan_poll_time > 3:
            self._last_fan_poll_time = now
            self._poll_fan_impl()
        if self.is_ssh_configured and now - self._last_power_sample_time > 10:
            self._last_power_sample_time = now
            self._sample_power()
        return ok

    def _sample_power(self):
        # TrueNAS SCALE is Debian-based, so the same Intel RAPL technique used for Linux targets
        # applies here too — optional (needs SSH), and only attempted if the counter exists, so this
        # is a no-op (not an error) on AMD/ARM or VM-hosted TrueNAS boxes without it.
        try:
            out = self._vm_collector.sh(
                "cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null", self._ssh_settings(),
                sudo=True, merge=True)
        except Exception:
            return
        out = out.strip()
        if not out:
            self.power_watts = None
            return
        try:
            energy_uj = float(out)
        except ValueError:
            return
        now_t = time.time()
        if self._prev_rapl:
            prev_e, prev_t = self._prev_rapl
            dt = now_t - prev_t
            if dt > 0.5 and energy_uj >= prev_e:
                self.power_watts = (energy_uj - prev_e) / 1_000_000 / dt
        self._prev_rapl = (energy_uj, now_t)

    def _apply_realtime(self, rt):
        if rt.cpu is not None:
            self._cpu_rt = rt.cpu
            self.cpu_percent = rt.cpu
            self.push(self.cpu_history, rt.cpu)
        if rt.memory_used is not None:
            self._mem_rt = rt.memory_used
            if self.mem_total > 0:
                self.mem_used = self.mem_total * rt.memory_used
                self.push(self.mem_history, rt.memory_used * 100)
        self.updated.emit("poll")

    def _on_close(self, err):
        self._client = None
        if self.state.kind == ConnectionState.CONNECTED:
            self.state = ConnectionState(ConnectionState.FAILED, "Connection lost")

    def _refresh_all(self) -> bool:
        c = self._client
        if c is None:
            return False
        try:
            info = c.call("system.info")
        except Exception as e:
            self.state = ConnectionState(ConnectionState.FAILED, str(e))
            self._client = None
            return False
        try:
            pools = c.call("pool.query")
        except Exception:
            pools = None
        try:
            services = c.call("service.query")
        except Exception:
            services = None
        try:
            alerts = c.call("alert.list")
        except Exception:
            alerts = None
        try:
            apps = c.call("app.query")
        except Exception:
            apps = None
        try:
            vms = c.call("vm.query")
        except Exception:
            vms = None

        self.host_name = info.get("hostname", "")
        self.host_os = "TrueNAS " + str(info.get("version", ""))
        self.host_uptime = info.get("uptime_seconds") or 0
        loadavg = info.get("loadavg") or []
        cores = info.get("cores") or 0
        if self._cpu_rt is None and loadavg and cores:
            self.cpu_percent = min(100, loadavg[0] / cores * 100)
        physmem = info.get("physmem") or 0
        if physmem:
            self.mem_total = float(physmem)
            if self._mem_rt is not None:
                self.mem_used = self.mem_total * self._mem_rt

        if pools is not None:
            self.storage = []
            for p in pools:
                scan = p.get("scan") or {}
                summary = ""
                fn, state = scan.get("function"), scan.get("state")
                if fn and state:
                    if state == "SCANNING":
                        summary = f"{fn.capitalize()} {int(scan.get('percentage') or 0)}%"
                    else:
                        errs = scan.get("errors") or 0
                        summary = f"{fn.capitalize()} {state.lower()}" + (f" · {errs} errors" if errs else "")
                self.storage.append(StorageItem(id=p["name"], title=p["name"], subtitle=p.get("status", ""),
                                                 size=p.get("size") or 0, used=p.get("allocated") or 0,
                                                 healthy=bool(p.get("healthy")), statusText=summary))
        if services is not None:
            self.services = sorted(
                [ServiceRow(id=str(s["id"]), name=s["service"], displayName=s["service"], state=s["state"],
                            isRunning=(s["state"] == "RUNNING")) for s in services],
                key=lambda r: r.name)
            self.services_loaded = True
        if apps is not None:
            self.apps = sorted(
                [TNApp(name=a["name"], state=a["state"], upgrade_available=a.get("upgrade_available"),
                       human_version=a.get("human_version")) for a in apps],
                key=lambda a: a.name)
        if vms is not None:
            self.vms = sorted([self._parse_vm(v) for v in vms], key=lambda v: v.name.lower())
        if alerts is not None:
            self._raw_alerts = alerts
            active = [a for a in alerts if not a.get("dismissed")]
            levels = ["INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]

            def as_item(a):
                sev = levels.index(a.get("level", "INFO")) if a.get("level") in levels else 0
                level = AlertItem.CRITICAL if sev >= 4 else (AlertItem.WARNING if sev >= 2 else AlertItem.INFO)
                return AlertItem(id=a.get("uuid", ""), level=level, title=a.get("formatted") or "Alert",
                                  detail=a.get("level", ""))

            self.extra_alerts = [as_item(a) for a in active]

        self.state = ConnectionState(ConnectionState.CONNECTED)
        self.last_refresh = time.time()
        return True

    @staticmethod
    def _parse_vm(v: dict) -> TNVM:
        status = v.get("status") or {}
        devices = []
        for d in v.get("devices") or []:
            attrs = d.get("attributes") or {}
            devices.append(TNVMDevice(dtype=d.get("dtype", ""), path=attrs.get("path")))
        return TNVM(id=v["id"], name=v["name"],
                    status=TNVMStatus(state=status.get("state", "UNKNOWN"), pid=status.get("pid")),
                    vcpus=v.get("vcpus") or 1, cores=v.get("cores") or 1, threads=v.get("threads") or 1,
                    memory=v.get("memory"), autostart=v.get("autostart"), devices=devices)

    def _close_backend(self):
        c = self._client
        self._client = None
        if c:
            c.disconnect()
        self._vm_collector.close()
        self._container_collector.close()
        self._console_runner.close()
        self._prev_rapl = None

    def refresh_services(self):
        self.submit(lambda: self._refresh_all(), "services")

    def service_action(self, s: ServiceRow, verb: str):
        def do():
            c = self._client
            if not c:
                return
            try:
                c.call_raw("service.control", [verb, s.name, {}])
                self.action_error_text = None
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
            time.sleep(1.5)
            self._refresh_all()
        self.submit(do, "services")

    def scrub(self, item: StorageItem, action: str):
        def do():
            c = self._client
            if not c:
                return
            try:
                try:
                    c.call_raw("pool.scrub.scrub", [item.id, action])
                except TrueNASError as e:
                    c.call_raw("pool.scrub", [item.id, action])
                self.action_error_text = None
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
            time.sleep(1.5)
            self._refresh_all()
        self.submit(do, "storage")

    def dismiss_alert(self, a: AlertItem):
        def do():
            c = self._client
            if not c:
                return
            try:
                c.call_raw("alert.dismiss", [a.id])
                self._raw_alerts = [x for x in self._raw_alerts if x.get("uuid") != a.id]
                self.extra_alerts = [x for x in self.extra_alerts if x.id != a.id]
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
        self.submit(do, "poll")

    def reboot(self):
        def do():
            c = self._client
            if c:
                try:
                    c.call_raw("system.reboot", ["Rebooted from HomeLab Management", {}])
                except Exception:
                    pass
        self.submit(do)

    def shutdown(self):
        def do():
            c = self._client
            if c:
                try:
                    c.call_raw("system.shutdown", ["Shut down from HomeLab Management", {}])
                except Exception:
                    pass
        self.submit(do)

    # -- Apps --
    def app_action(self, a: TNApp, action: str):
        def do():
            c = self._client
            if not c:
                return
            try:
                c.call_raw(f"app.{action}", [a.name])
                self.action_error_text = None
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
            time.sleep(3)
            self._refresh_all()
        self.submit(do, "apps")

    def install_custom_app(self, name: str, yaml: str, on_done):
        on_done = self._main_thread(on_done)
        def do():
            c = self._client
            if not c:
                on_done(False)
                return
            self.install_running = True
            self.install_failed = False
            self.install_status = "Submitting…"
            self.updated.emit("install")
            try:
                params = {"custom_app": True, "app_name": name, "custom_compose_config_string": yaml}
                job_id = c.call_raw("app.create", [params])
                if not isinstance(job_id, int):
                    self.install_status = "TrueNAS accepted the request but returned no job."
                    self.install_failed = True
                    self.install_running = False
                    self.updated.emit("install")
                    on_done(False)
                    return
                for _ in range(400):
                    time.sleep(1.5)
                    jobs = c.call_raw("core.get_jobs", [[["id", "=", job_id]]])
                    job = jobs[0] if jobs else None
                    if not job:
                        continue
                    state = job.get("state", "")
                    desc = (job.get("progress") or {}).get("description")
                    if desc:
                        self.install_status = desc
                        self.updated.emit("install")
                    if state == "SUCCESS":
                        self.install_status = "Installed."
                        self.install_running = False
                        self._refresh_all()
                        self.updated.emit("install")
                        on_done(True)
                        return
                    if state in ("FAILED", "ABORTED"):
                        self.install_failed = True
                        self.install_status = job.get("error") or f"Installation {state.lower()}."
                        self.install_running = False
                        self.updated.emit("install")
                        on_done(False)
                        return
                self.install_failed = True
                self.install_status = "Timed out waiting for TrueNAS. Check the Apps page."
                self.install_running = False
                self.updated.emit("install")
                on_done(False)
            except Exception as e:
                self.install_failed = True
                self.install_status = str(e)
                self.install_running = False
                self.updated.emit("install")
                on_done(False)
        self.submit(do)

    # -- VMs --
    def vm_action(self, v: TNVM, action: str):
        def do():
            c = self._client
            if not c:
                return
            try:
                if action == "stop":
                    c.call_raw("vm.stop", [v.id, {"force": False}])
                elif action == "poweroff":
                    c.call_raw("vm.poweroff", [v.id])
                else:
                    c.call_raw(f"vm.{action}", [v.id])
                self.action_error_text = None
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
            time.sleep(2 if action == "poweroff" else 3)
            self._refresh_all()
        self.submit(do, "vms")

    def set_vm_stats_active(self, active: bool):
        self._vm_stats_active = active
        if not active:
            def do():
                self._vm_collector.close()
            self.submit(do)

    def _sample_vms(self):
        if time.time() - self._last_zvol_fetch > 60:
            self._refresh_vm_disks()
        running = [v for v in self.vms if v.is_running and v.status.pid is not None]
        if not running:
            self.vm_stats_error = None
            return
        pids = " ".join(str(v.status.pid) for v in running)
        script = f"""
for p in {pids}; do
  [ -d /proc/$p ] || continue
  echo "PID $p"
  echo "STAT $(cat /proc/$p/stat)"
  awk '/^VmRSS/{{print "RSS",$2}}' /proc/$p/status
  awk '/^(read_bytes|write_bytes)/{{print "IO",$1,$2}}' /proc/$p/io
  for i in $(cat /proc/$p/fdinfo/* 2>/dev/null | awk '/^iff:/{{print $2}}' | sort -u); do
    echo "NET $i $(cat /sys/class/net/$i/statistics/rx_bytes) $(cat /sys/class/net/$i/statistics/tx_bytes)"
  done
done
"""
        try:
            text = self._vm_collector.sh(script, self._ssh_settings(), sudo=True, merge=True)
            samples = _parse_proc_samples(text)
            if not samples:
                detail = text.strip()[:300]
                self.vm_stats_error = detail if detail else "No process data returned for the running VM(s)."
                self.updated.emit("vms")
                return
            self.vm_stats_error = None
            for vm in running:
                sample = samples.get(vm.status.pid)
                if sample is None:
                    continue
                live = self.vm_live.get(vm.id) or TNVMLive()
                live.update(sample, self._previous_samples.get(vm.id), vm)
                live.disks = self._vm_disks.get(vm.id, [])
                self.vm_live[vm.id] = live
                self._previous_samples[vm.id] = sample
            self.updated.emit("vms")
        except Exception as e:
            self._previous_samples = {}
            self.vm_stats_error = str(e)
            self.updated.emit("vms")

    def _refresh_vm_disks(self):
        c = self._client
        if not c:
            return
        self._last_zvol_fetch = time.time()
        try:
            devices = c.call_raw("vm.device.query", [[["dtype", "=", "DISK"]]])
        except Exception:
            return
        usage: dict[str, tuple[float, float]] = {}
        result: dict[int, list[TNVMLiveDisk]] = {}
        for dev in devices or []:
            vm_id = dev.get("vm")
            if isinstance(vm_id, dict):
                vm_id = vm_id.get("id")
            path = (dev.get("attributes") or {}).get("path")
            if vm_id is None or not path:
                continue
            prefix = "/dev/zvol/"
            ds_id = path[len(prefix):] if path.startswith(prefix) else ""
            import urllib.parse
            ds_id = urllib.parse.unquote(ds_id)
            if ds_id and ds_id not in usage:
                try:
                    rows = c.call_raw("pool.dataset.query", [[["id", "=", ds_id]]])
                    ds = rows[0] if rows else None
                except Exception:
                    ds = None
                if ds is not None:
                    size = _tn_num(ds, "volsize")
                    if size is not None:
                        used = _tn_num(ds, "referenced")
                        if used is None:
                            used = _tn_num(ds, "used") or 0
                        usage[ds_id] = (size, used)
            name = ds_id if ds_id else (path.split("/")[-1] if path else path)
            u = usage.get(ds_id)
            result.setdefault(vm_id, []).append(TNVMLiveDisk(name=name, capacity=u[0] if u else 0,
                                                              allocation=u[1] if u else 0))
        self._vm_disks = result

    # -- Containers --
    def set_containers_active(self, active: bool):
        self._containers_active = active

    def _load_containers_impl(self):
        try:
            text = self._container_collector.sh(docker_.SAMPLE_SCRIPT, self._ssh_settings(), sudo=True, merge=True)
            parsed = docker_.parse(text)
            if not parsed and "{" not in text:
                detail = text.replace("---STATS---", "").strip()
                if detail:
                    self.containers_error = f"On the NAS: {detail[:300]}"
                    self.updated.emit("containers")
                    return
            self.containers = parsed
            self.containers_error = None
            self.containers_loaded = True
            self.updated.emit("containers")
        except Exception as e:
            self.containers_error = str(e)
            self.updated.emit("containers")

    def load_containers(self):
        self.submit(self._load_containers_impl, "containers")

    @staticmethod
    def _safe_name(s: str) -> bool:
        import re
        return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", s))

    def container_action(self, c: docker_.DockerContainer, verb: str):
        if verb not in ("start", "stop", "restart") or not self._safe_name(c.name):
            return

        def do():
            try:
                out = self._container_collector.sh(f"docker {verb} {c.name}", self._ssh_settings(), sudo=True, merge=True)
                if c.name not in out:
                    self.action_error_text = f"docker {verb}: {out.strip()[:300]}"
                    self.action_error.emit(self.action_error_text)
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
            self._load_containers_impl()
        self.submit(do, "containers")

    def container_logs(self, c: docker_.DockerContainer, on_done):
        on_done = self._main_thread(on_done)
        if not self._safe_name(c.name):
            on_done("Invalid container name.")
            return

        def do():
            try:
                out = self._container_collector.sh(f"docker logs --tail 300 {c.name}", self._ssh_settings(), sudo=True, merge=True)
                on_done(out if out else "(no output)")
            except Exception as e:
                on_done(str(e))
        self.submit(do)

    # -- Fan --
    def _fan_client(self) -> Optional[FanClient]:
        if not self.machine.host:
            return None
        return FanClient(f"http://{self.machine.host}:{self.machine.fanPort}",
                          MachineStore.fan_password(self.machine.id))

    def set_fan_active(self, active: bool):
        self._fan_active = active

    def _poll_fan_impl(self):
        client = self._fan_client()
        if client is None:
            self.fan_error = "Set the NAS address first."
            self.updated.emit("fan")
            return
        try:
            if self.fan_config is None:
                self.fan_config = client.config()
            s = client.status()
            self.fan_status = s
            self.fan_error = None

            def push(arr, v):
                if v is None:
                    return
                arr.append(v)
                if len(arr) > 60:
                    del arr[:len(arr) - 60]

            push(self.fan_rpm_history, float(s.rpm) if s.rpm is not None else None)
            push(self.fan_duty_history, s.pwm_pct)
            push(self.fan_temp_history, s.ctl_temp)
        except Exception as e:
            self.fan_error = f"Fan controller ({self.machine.host}:{self.machine.fanPort}): {e}"
        self.updated.emit("fan")

    def save_fan(self, config: FanConfig, on_done):
        on_done = self._main_thread(on_done)
        def do():
            client = self._fan_client()
            if client is None:
                on_done(False)
                return
            try:
                self.fan_config = client.save(config)
                self.fan_error = None
                self._poll_fan_impl()
                on_done(True)
            except Exception as e:
                self.action_error_text = f"Couldn't save fan settings: {e}"
                self.action_error.emit(self.action_error_text)
                on_done(False)
        self.submit(do, "fan")

    # -- Datasets & Snapshots --
    def refresh_datasets(self):
        def do():
            c = self._client
            if not c:
                return
            try:
                rows = c.call_raw("pool.dataset.query") or []
                ds = []
                for r in rows:
                    name = _tn_str(r, "name") or _tn_str(r, "id")
                    if not name:
                        continue
                    ds.append(TNDataset(id=name, name=name, type=_tn_str(r, "type") or "",
                                         used=int(_tn_num(r, "used") or 0),
                                         available=int(_tn_num(r, "available") or 0),
                                         mountpoint=_tn_str(r, "mountpoint") or ""))
                self.datasets = sorted(ds, key=lambda d: d.name.lower())
            except Exception:
                pass
            try:
                rows = c.call_raw("zfs.snapshot.query") or []
                snaps = []
                for r in rows:
                    name = _tn_str(r, "name") or _tn_str(r, "id")
                    if not name:
                        continue
                    snaps.append(TNSnapshotRow(id=name, name=name, dataset=_tn_str(r, "dataset") or "",
                                                used=int(_tn_num(r, "used") or 0)))
                self.snapshots = sorted(snaps, key=lambda s: s.name, reverse=True)
            except Exception:
                pass
        self.submit(do, "datasets")

    # -- Shares --
    def refresh_shares(self):
        def do():
            c = self._client
            if not c:
                return
            all_shares = []
            try:
                for i, r in enumerate(c.call_raw("sharing.smb.query") or []):
                    all_shares.append(TNShare(id=f"smb-{i}", name=_tn_str(r, "name") or "",
                                               path=_tn_str(r, "path") or "", comment=_tn_str(r, "comment") or "",
                                               enabled=_tn_bool(r, "enabled") is not False, kind="SMB"))
            except Exception:
                pass
            try:
                for i, r in enumerate(c.call_raw("sharing.nfs.query") or []):
                    paths = r.get("path") or r.get("paths") or []
                    all_shares.append(TNShare(id=f"nfs-{i}", name=(paths[0] if paths else ""),
                                               path=", ".join(paths), comment=_tn_str(r, "comment") or "",
                                               enabled=_tn_bool(r, "enabled") is not False, kind="NFS"))
            except Exception:
                pass
            self.shares = all_shares
        self.submit(do, "shares")

    # -- Network --
    def refresh_network(self):
        def do():
            c = self._client
            if not c:
                return
            try:
                rows = c.call_raw("interface.query") or []
            except Exception:
                return
            out = []
            for r in rows:
                name = _tn_str(r, "name") or _tn_str(r, "id")
                if not name:
                    continue
                state = r.get("state") or {}
                aliases = [a.get("address") for a in (state.get("aliases") or []) if a.get("address")]
                out.append(TNInterface(id=name, name=name, addresses=aliases, link_state=state.get("link_state", "")))
            self.interfaces = out
        self.submit(do, "network")

    # -- Data Protection --
    def refresh_data_protection(self):
        def do():
            c = self._client
            if not c:
                return
            tasks = []

            def add(method, id_prefix, kind, field_, fallback=None):
                try:
                    rows = c.call_raw(method) or []
                except Exception:
                    return
                for i, r in enumerate(rows):
                    summary = _tn_str(r, field_)
                    if summary is None and fallback:
                        summary = _tn_str(r, fallback)
                    tasks.append(TNProtectionTask(id=f"{id_prefix}-{i}", kind=kind, summary=summary or "–",
                                                   enabled=_tn_bool(r, "enabled") is not False))

            add("pool.snapshottask.query", "snap", "Snapshot Task", "dataset")
            add("replication.query", "repl", "Replication", "name")
            add("rsynctask.query", "rsync", "Rsync", "remotehost")
            add("cloudsync.query", "cloud", "Cloud Sync", "description")
            add("cronjob.query", "cron", "Cron Job", "description", "command")
            self.protection_tasks = tasks
        self.submit(do, "dataprotection")

    # -- Updates --
    def refresh_update_status(self):
        def do():
            c = self._client
            if not c:
                return
            try:
                # update.check_available was removed in newer SCALE releases (confirmed: throws
                # "Method does not exist" on a real 25.10.7/Goldeye box) — update.status is the
                # current method, shaped as {"code": "NORMAL", "status": {"new_version": ...}, "error": ...}
                # rather than the old flat {"status", "version"}.
                obj = c.call_raw("update.status")
            except Exception:
                return
            code = _tn_str(obj, "code")
            labels = {"NORMAL": "Up to date", "REBOOT_REQUIRED": "Reboot required to finish an update",
                      "HAS_UPDATE": "Update available", "UPDATE_AVAILABLE": "Update available",
                      "ERROR": "Error checking for updates"}
            self.update_status = labels.get(code, code)
            status = obj.get("status") if isinstance(obj, dict) else None
            new_version = status.get("new_version") if isinstance(status, dict) else None
            if isinstance(new_version, dict):
                self.update_version = _tn_str(new_version, "version") or _tn_str(new_version, "train")
            elif isinstance(new_version, str):
                self.update_version = new_version
            else:
                self.update_version = None
            err = obj.get("error") if isinstance(obj, dict) else None
            if err:
                self.update_status = f"{self.update_status} — {err}" if self.update_status else str(err)
        self.submit(do, "updates")

    # -- Users --
    def refresh_user_accounts(self):
        def do():
            c = self._client
            if not c:
                return
            try:
                rows = c.call_raw("user.query") or []
            except Exception:
                return
            out = []
            for r in rows:
                uid = r.get("uid")
                username = _tn_str(r, "username")
                if uid is None or not username:
                    continue
                out.append(TNUserAccount(id=int(uid), username=username, full_name=_tn_str(r, "full_name") or "",
                                          locked=_tn_bool(r, "locked") or False, uid=int(uid)))
            self.user_accounts = sorted(out, key=lambda u: u.username.lower())
        self.submit(do, "users")

    # -- Console --
    def run_console(self, command: str, on_done):
        on_done = self._main_thread(on_done)
        def do():
            if not self.is_ssh_configured:
                on_done(self.consoleUnavailableReason)
                return
            try:
                out = self._console_runner.sh(command, self._ssh_settings(), sudo=self.machine.sshSudo, merge=True)
                on_done(out)
            except Exception as e:
                on_done(str(e))
        self.submit(do)

    # -- VPN --
    def refresh_vpn(self):
        def do():
            if not self.is_ssh_configured:
                return
            self._load_containers_impl()
            result = []
            for c in self.containers:
                if not c.name.startswith(VPN_PREFIX):
                    continue
                name = c.name[len(VPN_PREFIX):]
                tunnel = None
                if c.is_running:
                    try:
                        dump = self._container_collector.sh(f"docker exec {c.name} wg show all dump",
                                                              self._ssh_settings(), sudo=True, merge=True)
                        parsed = wg_tunnels(["wg0"], dump)
                        tunnel = parsed[0] if parsed else None
                    except Exception:
                        pass
                result.append(TNVPNContainer(name=name, container_name=c.name, running=c.is_running, tunnel=tunnel))
            self.vpn_containers = sorted(result, key=lambda c: c.name.lower())
            self.vpn_error = None
        self.submit(do, "vpn")

    def add_vpn_tunnel(self, name: str, config: str, on_done):
        on_done = self._main_thread(on_done)
        def do():
            if not wg_valid_name(name):
                on_done("Use letters, numbers, dashes and underscores only.")
                return
            if not self.is_ssh_configured:
                on_done("Enable SSH on this machine first (Edit sheet).")
                return
            cname = VPN_PREFIX + name
            dir_ = f"/tmp/homelab-vpn-{name}"
            b64 = base64.b64encode(config.encode()).decode()
            script = f"""
mkdir -p {dir_}/wg_confs
echo {b64} | base64 -d > {dir_}/wg_confs/wg0.conf
chmod 600 {dir_}/wg_confs/wg0.conf
docker rm -f {cname} >/dev/null 2>&1
docker run -d --name {cname} --cap-add NET_ADMIN --cap-add SYS_MODULE \\
  --sysctl net.ipv4.conf.all.src_valid_mark=1 -v {dir_}:/config --restart unless-stopped \\
  lscr.io/linuxserver/wireguard:latest
"""
            try:
                out = self._container_collector.sh(script, self._ssh_settings(), sudo=True, merge=True)
            except Exception as e:
                out = str(e)
            self._refresh_vpn_sync()
            lowered = out.lower()
            on_done(out if ("error" in lowered or "cannot" in lowered) else None)
        self.submit(do, "vpn")

    def _refresh_vpn_sync(self):
        """Synchronous helper used by callbacks already running on the worker thread."""
        if not self.is_ssh_configured:
            return
        self._load_containers_impl()
        result = []
        for c in self.containers:
            if not c.name.startswith(VPN_PREFIX):
                continue
            name = c.name[len(VPN_PREFIX):]
            result.append(TNVPNContainer(name=name, container_name=c.name, running=c.is_running))
        self.vpn_containers = sorted(result, key=lambda c: c.name.lower())

    def start_vpn_tunnel(self, name: str):
        def do():
            try:
                self._container_collector.sh(f"docker start {VPN_PREFIX + name}", self._ssh_settings(), sudo=True, merge=True)
            except Exception:
                pass
            self._refresh_vpn_sync()
        self.submit(do, "vpn")

    def stop_vpn_tunnel(self, name: str):
        def do():
            try:
                self._container_collector.sh(f"docker stop {VPN_PREFIX + name}", self._ssh_settings(), sudo=True, merge=True)
            except Exception:
                pass
            self._refresh_vpn_sync()
        self.submit(do, "vpn")

    def remove_vpn_tunnel(self, name: str):
        def do():
            script = f"docker rm -f {VPN_PREFIX + name}; rm -rf /tmp/homelab-vpn-{name}"
            try:
                self._container_collector.sh(script, self._ssh_settings(), sudo=True, merge=True)
            except Exception:
                pass
            self._refresh_vpn_sync()
        self.submit(do, "vpn")
