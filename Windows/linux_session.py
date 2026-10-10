"""Manages a Debian/Ubuntu, Raspberry Pi OS, or Arch Linux machine over SSH + POSIX shell.

Mirrors App/Backends/LinuxSession.swift.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from core import Machine, MachineStore, NavItem, ConnectionState, StorageItem, ServiceRow, ProcessRow, AlertItem
from machine_session import MachineSession
from ssh_runner import SSHRunner, SSHSettings
import docker_
import tailscale_
from wireguard_ import tunnels as wg_tunnels, is_valid_name as wg_valid_name
from pihole_client import PiholeClient, PiholeError


@dataclass
class LoggedInUser:
    id: str
    name: str
    terminal: str
    host: str
    loginTime: str
    idle: str = ""


@dataclass
class UpdateItem:
    id: str
    name: str
    newVersion: str
    isSecurity: bool
    source: str = ""


@dataclass
class PiholeStatus:
    enabled: bool
    queriesToday: Optional[int] = None
    blockedToday: Optional[int] = None
    percentBlocked: Optional[float] = None
    domainsOnBlocklist: Optional[int] = None


@dataclass
class PiholeDNSRecord:
    ip: str
    domain: str


SAMPLE_SCRIPT = r"""
echo '##HOST'
hostname
( . /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" ) || echo Linux
uname -r
uname -m
cut -d' ' -f1 /proc/uptime
nproc
echo '##STAT'
head -1 /proc/stat
echo '##MEM'
cat /proc/meminfo
echo '##NET'
cat /proc/net/dev
echo '##DF'
df -PT -B1 -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs -x iso9660 2>/dev/null | tail -n +2
echo '##SVC'
systemctl list-units --type=service --all --plain --no-legend --no-pager 2>/dev/null
echo '##ZPOOL'
command -v zpool >/dev/null 2>&1 && zpool list -Hp -o name,size,allocated,free,health
echo '##FEAT'
command -v docker >/dev/null 2>&1 && echo DOCKER
command -v pihole >/dev/null 2>&1 && echo PIHOLE
systemctl list-unit-files 2>/dev/null | grep -q '^kvmd\.service' && echo PIKVM
command -v tailscale >/dev/null 2>&1 && echo TAILSCALE
command -v vcgencmd >/dev/null 2>&1 && echo RASPI
command -v wg >/dev/null 2>&1 && echo WIREGUARD
command -v protonvpn-cli >/dev/null 2>&1 && echo PROTONVPN
echo '##END'
"""

PROCESSES_SCRIPT = "ps -eo pid,user:20,pcpu,pmem,comm --sort=-pcpu --no-headers | head -60\n"

PIHOLE_STATUS_SCRIPT = "pihole status 2>/dev/null\necho '---JSON---'\npihole -c -j 2>/dev/null\n"

PIKVM_STATUS_SCRIPT = ("systemctl is-active kvmd 2>/dev/null\n"
                       "mount | awk '$3==\"/\"{if($0~/\\(rw/)print \"rw\"; else print \"ro\"}'\n")

RASPI_SCRIPT = "vcgencmd measure_temp\nvcgencmd measure_clock arm\nvcgencmd get_throttled\n"

SCHEDULED_SCRIPT = ("crontab -l 2>/dev/null\n"
                    "echo '---TIMERS---'\n"
                    "systemctl list-timers --all --no-legend --no-pager 2>/dev/null\n")

WIREGUARD_STATUS_SCRIPT = ("ls /etc/wireguard/*.conf 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\\.conf$//'\n"
                           "echo '---DUMP---'\n"
                           "wg show all dump 2>/dev/null\n")


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("##"):
            current = line[2:]
            out[current] = []
            continue
        if current is not None:
            out[current].append(line)
    return out


def _parse_host(lines: list[str]):
    if len(lines) < 5:
        return "", "", 0.0
    hostname, os_ = lines[0], lines[1]
    try:
        uptime = float(lines[4])
    except (ValueError, IndexError):
        uptime = 0.0
    return hostname, os_, uptime


def _parse_cpu_ticks(lines: list[str]):
    line = next((l for l in lines if l.startswith("cpu ")), None)
    if not line:
        return None
    f = [x for x in line.split(" ")[1:] if x]
    try:
        f = [float(x) for x in f]
    except ValueError:
        return None
    if len(f) < 4:
        return None
    idle = f[3] + (f[4] if len(f) > 4 else 0)
    total = sum(f[:8])
    return total - idle, total


def _parse_mem(lines: list[str]):
    total = available = 0.0
    for line in lines:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        parts = v.split()
        if not parts:
            continue
        try:
            kb = float(parts[0])
        except ValueError:
            continue
        if k == "MemTotal":
            total = kb * 1024
        if k == "MemAvailable":
            available = kb * 1024
    return total, max(0.0, total - available)


_NET_SKIP = ("veth", "docker", "br-", "virbr", "tap", "tailscale", "wg")


def _parse_net(lines: list[str]):
    rx = tx = 0.0
    for line in lines:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        if name == "lo" or any(name.startswith(p) for p in _NET_SKIP):
            continue
        f = rest.split()
        try:
            f = [float(x) for x in f]
        except ValueError:
            continue
        if len(f) >= 9:
            rx += f[0]
            tx += f[8]
    return rx, tx


def _parse_filesystems(lines: list[str]):
    seen = set()
    out = []
    for line in lines:
        f = line.split(None, 6)
        if len(f) < 7:
            continue
        try:
            size, used = int(f[2]), int(f[3])
        except ValueError:
            continue
        if size <= 0:
            continue
        if f[0].startswith("/dev/loop") or f[6].startswith("/var/lib/docker") or f[6].startswith("/snap") or f[1] == "zfs":
            continue
        key = f[0] + f[6]
        if key in seen:
            continue
        seen.add(key)
        out.append((f[0], size, used, f[6]))
    return out


def _parse_zpools(lines: list[str]):
    out = []
    for line in lines:
        f = line.split("\t")
        if len(f) < 5:
            continue
        try:
            size, alloc = int(f[1]), int(f[2])
        except ValueError:
            continue
        out.append((f[0], size, alloc, f[4]))
    return out


def _parse_services(lines: list[str]) -> list[ServiceRow]:
    out = []
    for line in lines:
        f = [x for x in line.split(" ") if x]
        if f and f[0] in ("●", "*"):
            f = f[1:]
        if len(f) < 4 or not f[0].endswith(".service"):
            continue
        name = f[0][:-8]
        if "@" in name and f[2] != "failed":
            continue
        out.append(ServiceRow(id=name, name=name, displayName=name, state=f[2],
                               isRunning=(f[2] == "active" and f[3] == "running")))
    return out


def _parse_processes(text: str) -> list[ProcessRow]:
    out = []
    for raw in text.splitlines():
        f = raw.split(None, 4)
        if len(f) != 5:
            continue
        try:
            pid, cpu, mem = int(f[0]), float(f[2]), float(f[3])
        except ValueError:
            continue
        out.append(ProcessRow(pid=pid, name=f[4], user=f[1], cpu=cpu, mem=mem))
    return out


def _parse_local_dns(text: str) -> list[PiholeDNSRecord]:
    """Parses /etc/pihole/custom.list — one "IP domain" pair per line, '#' comments allowed."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        out.append(PiholeDNSRecord(ip=parts[0], domain=parts[1]))
    return sorted(out, key=lambda r: r.domain.lower())


def _parse_pihole(text: str) -> Optional[PiholeStatus]:
    parts = text.split("---JSON---")
    status_text = parts[0] if parts else ""
    low = status_text.lower()
    if "enabled" not in low and "disabled" not in low:
        return None
    s = PiholeStatus(enabled=("disabled" not in low))
    if len(parts) > 1:
        import json
        try:
            obj = json.loads(parts[1].strip())
            s.queriesToday = obj.get("dns_queries_today")
            s.blockedToday = obj.get("ads_blocked_today")
            s.percentBlocked = obj.get("ads_percentage_today")
            s.domainsOnBlocklist = obj.get("domains_being_blocked")
        except Exception:
            pass
    return s


def _parse_throttle_flags(hex_str: str) -> list[str]:
    try:
        v = int(hex_str, 16)
    except ValueError:
        return []
    flags = []
    if v & 0x1:
        flags.append("Under-voltage")
    if v & 0x2:
        flags.append("ARM frequency capped")
    if v & 0x4:
        flags.append("Currently throttled")
    if v & 0x8:
        flags.append("Soft temperature limit active")
    return flags


def _parse_who(text: str) -> list[LoggedInUser]:
    out = []
    for raw in text.splitlines():
        f = [x for x in raw.split(" ") if x]
        if len(f) < 2:
            continue
        host = next((x.strip("()") for x in f if x.startswith("(")), "")
        date_fields = [x for x in f[2:] if not x.startswith("(")]
        out.append(LoggedInUser(id=f"{f[0]}-{f[1]}", name=f[0], terminal=f[1], host=host,
                                 loginTime=" ".join(date_fields)))
    return out


def _parse_apt_updates(text: str) -> list[UpdateItem]:
    out = []
    for line in text.splitlines():
        if "/" not in line:
            continue
        name, rest = line.split("/", 1)
        parts = rest.split()
        if len(parts) < 2:
            continue
        source = parts[0]
        out.append(UpdateItem(id=name, name=name, newVersion=parts[1], isSecurity=("security" in source.lower())))
    return out


def _parse_pacman_updates(text: str) -> list[UpdateItem]:
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        out.append(UpdateItem(id=f[0], name=f[0], newVersion=f[3], isSecurity=False))
    return out


class LinuxSession(MachineSession):
    def __init__(self, machine: Machine):
        super().__init__(machine)
        self._runner = SSHRunner()
        self._prev_cpu: Optional[tuple] = None
        self._prev_net: Optional[tuple] = None

        self.has_docker = False
        self.has_pihole = False
        self.has_pikvm = False
        self.has_tailscale = False
        self.has_raspberry_pi = False
        self.has_wireguard = False
        self.has_protonvpn_cli = False
        self.proton_status: Optional[str] = None
        self.proton_busy = False

        self.users: list[LoggedInUser] = []
        self.log_lines: list[str] = []
        self.updates: list[UpdateItem] = []
        self.updates_checked = False
        self.cron_jobs: list[str] = []
        self.timers: list[str] = []
        self.pi_temp: Optional[float] = None
        self.pi_throttled: list[str] = []
        self.pi_clock_mhz: Optional[float] = None

        self.pihole_status: Optional[PiholeStatus] = None
        self.pihole_error: Optional[str] = None
        self._pihole_client: Optional[PiholeClient] = None
        self.local_dns_records: list[PiholeDNSRecord] = []
        self.local_dns_error: Optional[str] = None
        self.pikvm_root_rw: Optional[bool] = None
        self.pikvm_active: Optional[bool] = None
        self.tailscale_status = None
        self.tailscale_error: Optional[str] = None
        self.wireguard_tunnels = []
        self.wireguard_error: Optional[str] = None

        self.containers: list[docker_.DockerContainer] = []
        self.containers_loaded = False
        self.containers_error: Optional[str] = None
        self._container_collector = SSHRunner()
        self._containers_active = False
        self._last_container_load_time = 0.0

    @property
    def consoleIsAvailable(self):
        return True

    @property
    def consoleUnavailableReason(self):
        return ""

    @property
    def nav_items(self):
        items = list(self.machine.kind.nav_items)
        if NavItem.alerts in items:
            idx = items.index(NavItem.alerts)
            extra = []
            if self.has_docker:
                extra.append(NavItem.containers)
            if self.has_pihole:
                extra.append(NavItem.pihole)
                extra.append(NavItem.local_dns)
            if self.has_pikvm:
                extra.append(NavItem.pikvm)
            if self.has_tailscale:
                extra.append(NavItem.tailscale)
            if self.has_raspberry_pi:
                extra.append(NavItem.raspberry_pi)
            if self.has_wireguard or self.has_protonvpn_cli:
                extra.append(NavItem.vpn)
            items[idx:idx] = extra
        return items

    def _ssh(self) -> SSHSettings:
        return SSHSettings(host=self.machine.host, port=self.machine.port, user=self.machine.user,
                            password=MachineStore.secret(self.machine.id), use_sudo=self.machine.useSudo)

    def _poll(self) -> bool:
        if self.state.kind != ConnectionState.CONNECTED:
            self.state = ConnectionState(ConnectionState.CONNECTING)
        try:
            text = self._runner.sh(SAMPLE_SCRIPT, self._ssh())
        except Exception as e:
            self.state = ConnectionState(ConnectionState.FAILED, str(e))
            return False
        sec = _sections(text)
        host_lines = sec.get("HOST")
        if "END" not in sec or not host_lines or len(host_lines) < 6:
            self.state = ConnectionState(ConnectionState.FAILED,
                                          "Unexpected reply from the machine. Is a POSIX shell available over SSH?")
            return False
        self.host_name, self.host_os, self.host_uptime = _parse_host(host_lines)

        total, used = _parse_mem(sec.get("MEM", []))
        self.mem_total, self.mem_used = total, used
        self.push(self.mem_history, (used / total * 100) if total > 0 else None)

        fs = [StorageItem(id=mount, title=mount, subtitle=dev, size=size, used=u)
              for dev, size, u, mount in _parse_filesystems(sec.get("DF", []))]
        zp = [StorageItem(id=f"zfs-{name}", title=f"ZFS {name}", subtitle=health, size=size, used=alloc,
                           healthy=(health == "ONLINE"), statusText=health)
              for name, size, alloc, health in _parse_zpools(sec.get("ZPOOL", []))]
        self.storage = fs + zp

        self.services = sorted(_parse_services(sec.get("SVC", [])), key=lambda s: s.name)
        self.services_loaded = True
        self.extra_alerts = [AlertItem(id=f"svc-{s.name}", level=AlertItem.CRITICAL,
                                        title=f"Service failed: {s.name}", detail="")
                              for s in self.services if s.state == "failed"]

        ticks = _parse_cpu_ticks(sec.get("STAT", []))
        if ticks:
            busy, total_ticks = ticks
            if self._prev_cpu and total_ticks > self._prev_cpu[1]:
                self.cpu_percent = min(100, max(0, (busy - self._prev_cpu[0]) / (total_ticks - self._prev_cpu[1]) * 100))
            self._prev_cpu = ticks
        self.push(self.cpu_history, self.cpu_percent)

        rx, tx = _parse_net(sec.get("NET", []))
        now = time.time()
        if self._prev_net:
            dt = now - self._prev_net[2]
            if dt > 0.5:
                self.net_rx_rate = max(0, rx - self._prev_net[0]) / dt
                self.net_tx_rate = max(0, tx - self._prev_net[1]) / dt
        self._prev_net = (rx, tx, now)

        feat = set(sec.get("FEAT", []))
        self.has_docker = "DOCKER" in feat
        self.has_pihole = "PIHOLE" in feat
        self.has_pikvm = "PIKVM" in feat
        self.has_tailscale = "TAILSCALE" in feat
        self.has_raspberry_pi = "RASPI" in feat
        self.has_wireguard = "WIREGUARD" in feat
        self.has_protonvpn_cli = "PROTONVPN" in feat

        self.state = ConnectionState(ConnectionState.CONNECTED)
        self.last_refresh = time.time()

        now = time.time()
        if self._containers_active and self.has_docker and now - self._last_container_load_time > 10:
            self._last_container_load_time = now
            self._load_containers_impl()
        return True

    def _close_backend(self):
        self._runner.close()
        self._prev_cpu = None
        self._prev_net = None
        self._container_collector.close()

    # -- Pi-hole --
    def refresh_pihole(self):
        def do():
            try:
                t = self._runner.sh(PIHOLE_STATUS_SCRIPT, self._ssh())
                status = _parse_pihole(t)
            except Exception:
                status = None
            password = MachineStore.pihole_password(self.machine.id)
            if password:
                # Pi-hole v6 dropped the unauthenticated JSON stats dump the plain-text path above
                # relies on for query counts, so fetch those from the v6 REST API instead when a
                # web admin password is configured for it. Enabled/disabled still comes from SSH
                # above either way, since that keeps working across versions.
                try:
                    api_stats = self._fetch_pihole_api_stats(password)
                    if status is None:
                        status = PiholeStatus(enabled=api_stats.get("enabled", True))
                    status.queriesToday = api_stats.get("queriesToday", status.queriesToday)
                    status.blockedToday = api_stats.get("blockedToday", status.blockedToday)
                    status.percentBlocked = api_stats.get("percentBlocked", status.percentBlocked)
                    status.domainsOnBlocklist = api_stats.get("domainsOnBlocklist", status.domainsOnBlocklist)
                    self.pihole_error = None
                except PiholeError as e:
                    self.pihole_error = str(e)
            elif status is None:
                self.pihole_error = ("Couldn't read Pi-hole stats over SSH. If this is Pi-hole v6, add its "
                                      "web admin password in the Edit sheet — v6 needs the API for stats.")
            else:
                self.pihole_error = None
            self.pihole_status = status
        self.submit(do, "pihole")

    def _fetch_pihole_api_stats(self, password: str) -> dict:
        if self._pihole_client is None:
            self._pihole_client = PiholeClient(self.machine.host, password)
        summary = self._pihole_client.get("stats/summary")
        queries = summary.get("queries") or {}
        gravity = summary.get("gravity") or {}
        out = {}
        if "total" in queries:
            out["queriesToday"] = queries.get("total")
        if "blocked" in queries:
            out["blockedToday"] = queries.get("blocked")
        if "percent_blocked" in queries:
            out["percentBlocked"] = queries.get("percent_blocked")
        if "domains_being_blocked" in gravity:
            out["domainsOnBlocklist"] = gravity.get("domains_being_blocked")
        try:
            blocking = self._pihole_client.get("dns/blocking")
            if "blocking" in blocking:
                out["enabled"] = str(blocking["blocking"]).lower() == "enabled"
        except PiholeError:
            pass
        return out

    def pihole_set_enabled(self, enabled: bool, duration: Optional[str] = None):
        def do():
            cmd = "pihole enable" if enabled else ("pihole disable" + (f" {duration}" if duration else ""))
            try:
                self._runner.sh(cmd, self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            time.sleep(1)
            t = self._runner.sh(PIHOLE_STATUS_SCRIPT, self._ssh())
            self.pihole_status = _parse_pihole(t)
        self.submit(do, "pihole")

    # -- Pi-hole local DNS records --
    # Uses Pi-hole's own `pihole -a hostrecord` CLI (documented, stable across v5/v6) for
    # add/remove, and reads /etc/pihole/custom.list directly for listing (the file both versions
    # use to back "Local DNS Records" in the admin UI).

    def refresh_local_dns(self):
        def do():
            try:
                t = self._runner.sh("cat /etc/pihole/custom.list 2>/dev/null", self._ssh(), sudo=True, merge=True)
            except Exception as e:
                self.local_dns_error = str(e)
                return
            self.local_dns_records = _parse_local_dns(t)
            self.local_dns_error = None
        self.submit(do, "localdns")

    def add_local_dns(self, domain: str, ip: str, on_done):
        def do():
            domain_t, ip_t = domain.strip(), ip.strip()
            if not domain_t or not ip_t:
                on_done("Enter both a domain and an IP address.")
                return
            from ssh_runner import quote
            try:
                out = self._runner.sh(f"pihole -a hostrecord {quote(domain_t)} {quote(ip_t)}",
                                       self._ssh(), sudo=True, merge=True)
            except Exception as e:
                on_done(str(e))
                return
            self._refresh_local_dns_sync()
            on_done(None if "error" not in out.lower() else out)
        self.submit(do, "localdns")

    def remove_local_dns(self, domain: str, on_done):
        def do():
            from ssh_runner import quote
            try:
                out = self._runner.sh(f"pihole -a hostrecord {quote(domain)}", self._ssh(), sudo=True, merge=True)
            except Exception as e:
                on_done(str(e))
                return
            self._refresh_local_dns_sync()
            on_done(None if "error" not in out.lower() else out)
        self.submit(do, "localdns")

    def _refresh_local_dns_sync(self):
        try:
            t = self._runner.sh("cat /etc/pihole/custom.list 2>/dev/null", self._ssh(), sudo=True, merge=True)
        except Exception:
            return
        self.local_dns_records = _parse_local_dns(t)

    # -- PiKVM --
    def refresh_pikvm(self):
        def do():
            try:
                t = self._runner.sh(PIKVM_STATUS_SCRIPT, self._ssh())
                lines = t.splitlines()
                self.pikvm_active = (lines[0] == "active") if lines else None
                self.pikvm_root_rw = (lines[1] == "rw") if len(lines) > 1 else None
            except Exception:
                pass
        self.submit(do, "pikvm")

    def pikvm_set_writable(self, writable: bool):
        def do():
            try:
                self._runner.sh("rw" if writable else "ro", self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            time.sleep(1)
            t = self._runner.sh(PIKVM_STATUS_SCRIPT, self._ssh())
            lines = t.splitlines()
            self.pikvm_active = (lines[0] == "active") if lines else None
            self.pikvm_root_rw = (lines[1] == "rw") if len(lines) > 1 else None
        self.submit(do, "pikvm")

    def pikvm_restart(self):
        def do():
            try:
                self._runner.sh("systemctl restart kvmd", self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            time.sleep(1)
            t = self._runner.sh(PIKVM_STATUS_SCRIPT, self._ssh())
            lines = t.splitlines()
            self.pikvm_active = (lines[0] == "active") if lines else None
            self.pikvm_root_rw = (lines[1] == "rw") if len(lines) > 1 else None
        self.submit(do, "pikvm")

    # -- Tailscale --
    def refresh_tailscale(self):
        def do():
            try:
                t = self._runner.sh("tailscale status --json", self._ssh())
                s = tailscale_.parse(t)
                if s:
                    self.tailscale_status = s
                    self.tailscale_error = None
                else:
                    self.tailscale_error = "Couldn't read Tailscale status."
            except Exception as e:
                self.tailscale_error = str(e)
        self.submit(do, "tailscale")

    # -- Raspberry Pi --
    def refresh_raspberry_pi(self):
        def do():
            try:
                t = self._runner.sh(RASPI_SCRIPT, self._ssh())
            except Exception:
                return
            lines = t.splitlines()
            if len(lines) < 3:
                return
            temp_str = lines[0].split("=")[-1].replace("'C", "").strip()
            try:
                self.pi_temp = float(temp_str)
            except ValueError:
                self.pi_temp = None
            try:
                hz = float(lines[1].split("=")[-1])
                self.pi_clock_mhz = hz / 1_000_000
            except (ValueError, IndexError):
                pass
            hexv = lines[2].split("=")[-1].replace("0x", "")
            self.pi_throttled = _parse_throttle_flags(hexv)
        self.submit(do, "raspi")

    # -- WireGuard --
    def refresh_wireguard(self):
        def do():
            try:
                t = self._runner.sh(WIREGUARD_STATUS_SCRIPT, self._ssh(), sudo=True, merge=True)
            except Exception:
                return
            parts = t.split("---DUMP---")
            conf_names = [x for x in (parts[0] if parts else "").splitlines() if x]
            self.wireguard_tunnels = wg_tunnels(conf_names, parts[1] if len(parts) > 1 else "")
            self.wireguard_error = None
        self.submit(do, "wireguard")

    def add_wireguard_tunnel(self, name: str, config: str, on_done):
        def do():
            if not wg_valid_name(name):
                on_done("Use letters, numbers, dashes and underscores only.")
                return
            import base64
            b64 = base64.b64encode(config.encode()).decode()
            script = (f"echo {b64} | base64 -d > /tmp/wg-{name}.conf\n"
                      f"install -m 600 /tmp/wg-{name}.conf /etc/wireguard/{name}.conf\n"
                      f"rm -f /tmp/wg-{name}.conf\n")
            try:
                out = self._runner.sh(script, self._ssh(), sudo=True, merge=True)
            except Exception as e:
                out = str(e)
            self._do_refresh_wireguard_sync()
            trimmed = out.strip()
            lowered = trimmed.lower()
            on_done(out if (trimmed and ("error" in lowered or "permission" in lowered)) else None)
        self.submit(do, "wireguard")

    def _do_refresh_wireguard_sync(self):
        try:
            t = self._runner.sh(WIREGUARD_STATUS_SCRIPT, self._ssh(), sudo=True, merge=True)
        except Exception:
            return
        parts = t.split("---DUMP---")
        conf_names = [x for x in (parts[0] if parts else "").splitlines() if x]
        self.wireguard_tunnels = wg_tunnels(conf_names, parts[1] if len(parts) > 1 else "")

    def connect_wireguard_tunnel(self, name: str):
        def do():
            if not wg_valid_name(name):
                return
            try:
                out = self._runner.sh(f"wg-quick up {name}", self._ssh(), sudo=True, merge=True)
                if "error" in out.lower():
                    self.wireguard_error = out
            except Exception as e:
                self.wireguard_error = str(e)
            self._do_refresh_wireguard_sync()
        self.submit(do, "wireguard")

    def disconnect_wireguard_tunnel(self, name: str):
        def do():
            if not wg_valid_name(name):
                return
            try:
                self._runner.sh(f"wg-quick down {name}", self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            self._do_refresh_wireguard_sync()
        self.submit(do, "wireguard")

    def remove_wireguard_tunnel(self, name: str):
        def do():
            if not wg_valid_name(name):
                return
            try:
                self._runner.sh(f"wg-quick down {name} 2>/dev/null; rm -f /etc/wireguard/{name}.conf",
                                 self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            self._do_refresh_wireguard_sync()
        self.submit(do, "wireguard")

    # -- ProtonVPN CLI --
    def proton_login(self, username: str, password: str):
        def do():
            self.proton_busy = True
            self.updated.emit("vpn")
            MachineStore.set_proton_user(username, self.machine.id)
            MachineStore.set_proton_password(password, self.machine.id)
            from ssh_runner import quote
            script = f"printf '%s\\n' {quote(password)} | protonvpn-cli login {quote(username)}"
            try:
                self.proton_status = self._runner.sh(script, self._ssh(), sudo=False, merge=True)
            except Exception as e:
                self.proton_status = str(e)
            self.proton_busy = False
        self.submit(do, "vpn")

    def proton_connect_fastest(self):
        def do():
            self.proton_busy = True
            self.updated.emit("vpn")
            try:
                self.proton_status = self._runner.sh("protonvpn-cli c -f", self._ssh(), sudo=True, merge=True)
            except Exception as e:
                self.proton_status = str(e)
            self.proton_busy = False
        self.submit(do, "vpn")

    def proton_disconnect(self):
        def do():
            self.proton_busy = True
            self.updated.emit("vpn")
            try:
                self.proton_status = self._runner.sh("protonvpn-cli d", self._ssh(), sudo=True, merge=True)
            except Exception as e:
                self.proton_status = str(e)
            self.proton_busy = False
        self.submit(do, "vpn")

    def proton_refresh_status(self):
        def do():
            try:
                self.proton_status = self._runner.sh("protonvpn-cli status", self._ssh(), sudo=False, merge=True)
            except Exception:
                pass
        self.submit(do, "vpn")

    # -- Users / Logs / Updates / Console / Scheduled Tasks --
    def refresh_users(self):
        def do():
            try:
                t = self._runner.sh("who", self._ssh())
                self.users = _parse_who(t)
            except Exception:
                pass
        self.submit(do, "users")

    def refresh_logs(self, filter: str = ""):
        def do():
            unit = filter.strip()
            from ssh_runner import quote
            cmd = "journalctl -n 300 --no-pager" if not unit else f"journalctl -u {quote(unit)} -n 300 --no-pager"
            try:
                t = self._runner.sh(cmd, self._ssh(), sudo=True, merge=True)
            except Exception:
                t = "Couldn't load logs."
            self.log_lines = t.split("\n")
        self.submit(do, "logs")

    def refresh_updates(self):
        def do():
            from core import MachineKind
            cmd = "pacman -Qu 2>/dev/null" if self.machine.kind == MachineKind.arch else "apt list --upgradable 2>/dev/null | tail -n +2"
            try:
                t = self._runner.sh(cmd, self._ssh())
            except Exception:
                return
            self.updates = _parse_pacman_updates(t) if self.machine.kind == MachineKind.arch else _parse_apt_updates(t)
            self.updates_checked = True
        self.submit(do, "updates")

    def upgrade_all(self, on_done):
        def do():
            from core import MachineKind
            cmd = ("pacman -Syu --noconfirm" if self.machine.kind == MachineKind.arch else
                   "export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get -y upgrade")
            try:
                out = self._runner.sh(cmd, self._ssh(), sudo=True, merge=True)
            except Exception as e:
                out = str(e)
            self.refresh_updates()
            on_done(out)
        self.submit(do)

    def refresh_scheduled_tasks(self):
        def do():
            try:
                t = self._runner.sh(SCHEDULED_SCRIPT, self._ssh(), sudo=True, merge=True)
            except Exception:
                return
            parts = t.split("---TIMERS---")
            self.cron_jobs = [l for l in (parts[0] if parts else "").splitlines()
                               if l.strip() and not l.startswith("#")]
            self.timers = [l for l in (parts[1] if len(parts) > 1 else "").splitlines() if l]
        self.submit(do, "scheduledTasks")

    def run_console(self, command: str, on_done):
        def do():
            try:
                out = self._runner.sh(command, self._ssh(), sudo=True, merge=True)
            except Exception as e:
                out = str(e)
            on_done(out)
        self.submit(do)

    # -- Containers --
    def set_containers_active(self, active: bool):
        self._containers_active = active

    def _load_containers_impl(self):
        try:
            text = self._container_collector.sh(docker_.SAMPLE_SCRIPT, self._ssh(), sudo=True, merge=True)
            self.containers = docker_.parse(text)
            self.containers_error = None
            self.containers_loaded = True
        except Exception as e:
            self.containers_error = str(e)
        self.updated.emit("containers")

    def load_containers(self):
        self.submit(self._load_containers_impl, "containers")

    def container_action(self, c: docker_.DockerContainer, verb: str):
        if verb not in ("start", "stop", "restart"):
            return

        def do():
            from ssh_runner import quote
            try:
                self._container_collector.sh(f"docker {verb} {quote(c.name)}", self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            self._load_containers_impl()
        self.submit(do, "containers")

    def container_logs(self, c: docker_.DockerContainer, on_done):
        def do():
            from ssh_runner import quote
            try:
                out = self._container_collector.sh(f"docker logs --tail 300 {quote(c.name)}", self._ssh(), sudo=True, merge=True)
            except Exception:
                out = "Couldn't load logs."
            on_done(out)
        self.submit(do)

    def refresh_services(self):
        self.submit(lambda: self._poll(), "services")

    def refresh_processes(self):
        def do():
            try:
                t = self._runner.sh(PROCESSES_SCRIPT, self._ssh())
                self.processes = _parse_processes(t)
            except Exception:
                pass
        self.submit(do, "processes")

    def service_action(self, s: ServiceRow, verb: str):
        def do():
            from ssh_runner import quote
            try:
                out = self._runner.sh(f"systemctl {verb} {quote(s.name + '.service')}", self._ssh(), sudo=True, merge=True)
                if out.strip():
                    self.action_error_text = out
                    self.action_error.emit(out)
            except Exception as e:
                self.action_error_text = str(e)
                self.action_error.emit(str(e))
            time.sleep(1)
            self._poll()
        self.submit(do, "services")

    def kill_process(self, p: ProcessRow):
        def do():
            try:
                self._runner.sh(f"kill -9 {p.pid}", self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            try:
                t = self._runner.sh(PROCESSES_SCRIPT, self._ssh())
                self.processes = _parse_processes(t)
            except Exception:
                pass
        self.submit(do, "processes")

    def reboot(self):
        self.submit(lambda: self._runner.sh("systemctl reboot", self._ssh(), sudo=True))

    def shutdown(self):
        self.submit(lambda: self._runner.sh("systemctl poweroff", self._ssh(), sudo=True))
