"""Manages a remote Mac over SSH ("Remote Login") using stock BSD tools.

Mirrors App/Backends/MacSession.swift.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from core import Machine, MachineStore, NavItem, ConnectionState, StorageItem, ServiceRow, ProcessRow
from machine_session import MachineSession
from ssh_runner import SSHRunner, SSHSettings, quote
import docker_
import tailscale_
from wireguard_ import tunnels as wg_tunnels, is_valid_name as wg_valid_name
from linux_session import LoggedInUser, UpdateItem  # shared shapes


@dataclass
class MacStartupItem:
    label: str
    domain: str   # "gui/<uid>" or "system"
    scope: str
    disabled: bool

    @property
    def id(self):
        return self.domain + self.label


SAMPLE_SCRIPT = """
echo '##HOST'
hostname -s
sw_vers -productName
sw_vers -productVersion
echo $(( $(date +%s) - $(sysctl -n kern.boottime | awk -F'[ ,]+' '{print $4}') ))
sysctl -n hw.memsize
echo '##PAGESIZE'
sysctl -n hw.pagesize
echo '##VMSTAT'
vm_stat
echo '##TOP'
top -l 1 -n 0 | grep -E '^CPU usage'
echo '##DF'
df -k 2>/dev/null | tail -n +2
echo '##LAUNCHD'
launchctl list 2>/dev/null | tail -n +2
echo '##NETSTAT'
netstat -ib 2>/dev/null | tail -n +2
echo '##FEAT'
command -v docker >/dev/null 2>&1 && echo DOCKER
command -v tailscale >/dev/null 2>&1 && echo TAILSCALE
command -v wg >/dev/null 2>&1 && echo WIREGUARD
echo '##END'
"""

PROCESSES_SCRIPT = "ps -eo pid,pcpu,rss,comm -r | tail -n +2 | head -60\n"

POWER_SCRIPT = """
echo '##THERM'
pmset -g therm
echo '##PS'
pmset -g ps | head -1
echo '##BATT'
ioreg -rn AppleSmartBattery
echo '##END'
"""

STARTUP_SCRIPT = """
echo '##ITEMS'
for d in /Library/LaunchAgents "$HOME/Library/LaunchAgents" /Library/LaunchDaemons; do
  dom="gui/$(id -u)"; [ "$d" = "/Library/LaunchDaemons" ] && dom="system"
  [ -d "$d" ] || continue
  for f in "$d"/*.plist; do
    [ -e "$f" ] || continue
    label=$(defaults read "$f" Label 2>/dev/null)
    [ -z "$label" ] && label=$(basename "$f" .plist)
    echo "ITEM|$dom|$label|$d"
  done
done
echo '##DISABLED'
launchctl print-disabled "gui/$(id -u)" 2>/dev/null
launchctl print-disabled system 2>/dev/null
"""

SOFTWARE_SCRIPT = """
for a in /Applications/*.app /Applications/*/*.app; do
  [ -e "$a" ] || continue
  name=$(basename "$a" .app)
  ver=$(defaults read "$a/Contents/Info" CFBundleShortVersionString 2>/dev/null)
  echo "APP|$name|$ver"
done
if command -v brew >/dev/null 2>&1; then
  brew list --versions 2>/dev/null | while read -r line; do echo "BREW|$line"; done
fi
"""

UPDATES_SCRIPT = """
softwareupdate -l 2>&1
echo '---BREW---'
command -v brew >/dev/null 2>&1 && brew outdated --json=v2 2>/dev/null
"""

WIREGUARD_STATUS_SCRIPT = """
WGDIR="$(brew --prefix 2>/dev/null)/etc/wireguard"
ls "$WGDIR"/*.conf 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\\.conf$//'
echo '---DUMP---'
wg show all dump 2>/dev/null
"""


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


def _parse_memory(lines: list[str], page_size: float, mem_bytes: float):
    pages: dict[str, float] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip().strip(".")
        try:
            pages[key] = float(val)
        except ValueError:
            pass
    active = pages.get("Pages active", 0)
    wired = pages.get("Pages wired down", 0)
    compressed = pages.get("Pages occupied by compressor", 0)
    used = (active + wired + compressed) * page_size
    return used, mem_bytes


def _parse_cpu(lines: list[str]) -> Optional[float]:
    if not lines:
        return None
    line = lines[0]
    idle = next((p for p in line.split(",") if "idle" in p), None)
    if not idle:
        return None
    try:
        pct = float(idle.split("%")[0].strip())
    except ValueError:
        return None
    return max(0.0, min(100.0, 100 - pct))


def _parse_filesystems(lines: list[str]):
    out = []
    for line in lines:
        f = [x for x in line.split(" ") if x]
        if len(f) < 9 or not f[0].startswith("/dev/"):
            continue
        try:
            size1k, used1k = int(f[1]), int(f[2])
        except ValueError:
            continue
        mount = " ".join(f[8:])
        if mount.startswith("/System/Volumes") and mount != "/System/Volumes/Data":
            continue
        if mount == "/Volumes/Recovery":
            continue
        out.append((f[0], size1k * 1024, used1k * 1024, mount))
    return out


_MAC_NET_SKIP = ("lo", "gif", "stf", "utun", "awdl", "llw", "bridge", "anpi", "ap1", "p2p", "vmnet")


def _parse_net(lines: list[str]):
    rx = tx = 0.0
    for line in lines:
        f = [x for x in line.split(" ") if x]
        if len(f) < 9 or not f[2].startswith("<Link"):
            continue
        if any(f[0].startswith(s) for s in _MAC_NET_SKIP):
            continue
        try:
            ibytes, obytes = float(f[-5]), float(f[-2])
        except (ValueError, IndexError):
            continue
        rx += ibytes
        tx += obytes
    return rx, tx


def _parse_launchd(lines: list[str]) -> list[ServiceRow]:
    out = []
    for line in lines:
        f = line.split("\t")
        if len(f) < 3:
            continue
        label = f[2]
        if not label or label.lower() == "label":
            continue
        running = f[0] not in ("", "-")
        out.append(ServiceRow(id=label, name=label, displayName=label, state=("running" if running else "stopped"),
                               isRunning=running, canControl=not label.startswith("com.apple.")))
    return out


def _kv(lines: list[str], key: str) -> Optional[str]:
    for line in lines:
        t = line.strip()
        if not (t.startswith(key) or t.startswith(f'"{key}"')):
            continue
        if "=" not in t:
            continue
        return t.split("=", 1)[1].strip()
    return None


def _parse_processes(text: str) -> list[ProcessRow]:
    out = []
    for raw in text.splitlines():
        f = raw.split(None, 3)
        if len(f) != 4:
            continue
        try:
            pid, cpu, rss_kb = int(f[0]), float(f[1]), float(f[2])
        except ValueError:
            continue
        out.append(ProcessRow(pid=pid, name=f[3], cpu=cpu, mem=rss_kb * 1024))
    return out


def _parse_startup_items(text: str) -> list[MacStartupItem]:
    parts = text.split("##DISABLED")
    disabled = set()
    if len(parts) > 1:
        for line in parts[1].splitlines():
            t = line.strip()
            if not t.startswith('"') or "=> disabled" not in t:
                continue
            end = t.find('"', 1)
            if end != -1:
                disabled.add(t[1:end])
    items = []
    for line in (parts[0] if parts else "").splitlines():
        f = line.split("|")
        if len(f) < 3 or f[0] != "ITEM":
            continue
        items.append(MacStartupItem(label=f[2], domain=f[1], scope=f[3] if len(f) > 3 else "",
                                     disabled=f[2] in disabled))
    return sorted(items, key=lambda i: i.label.lower())


def _parse_software(text: str):
    out = []
    for raw in text.splitlines():
        f = raw.split("|")
        if len(f) < 2:
            continue
        if f[0] == "APP":
            out.append(("Application", f[1], f[2] if len(f) > 2 else ""))
        elif f[0] == "BREW":
            parts = f[1].split(" ", 1)
            out.append(("Homebrew", parts[0], parts[1] if len(parts) > 1 else ""))
    from windows_session import InstalledSoftware
    return sorted([InstalledSoftware(name=name, version=ver, publisher=pub) for pub, name, ver in out],
                  key=lambda s: s.name.lower())


def _parse_software_updates(text: str) -> list[UpdateItem]:
    if "no new software available" in text.lower():
        return []
    out = []
    for raw in text.splitlines():
        t = raw.strip()
        if not t.startswith("* Label:"):
            continue
        name = t.replace("* Label:", "").strip()
        out.append(UpdateItem(id=name, name=name, newVersion="", isSecurity=("security" in name.lower())))
    return out


def _parse_brew_outdated(text: str) -> list[UpdateItem]:
    import json
    text = text.strip()
    if not text:
        return []
    try:
        obj = json.loads(text)
    except Exception:
        return []

    def items(key):
        out = []
        for r in obj.get(key) or []:
            name = r.get("name")
            if isinstance(name, list):
                name = name[0] if name else None
            if not name:
                continue
            installed = ", ".join(r.get("installed_versions") or [])
            current = r.get("current_version", "")
            new_version = (installed or current) if not installed else f"{installed} → {current}"
            out.append(UpdateItem(id=f"brew-{name}", name=name, newVersion=new_version, isSecurity=False,
                                   source="Homebrew"))
        return out

    return items("formulae") + items("casks")


def _parse_who(text: str) -> list[LoggedInUser]:
    out = []
    for raw in text.splitlines():
        f = [x for x in raw.split(" ") if x]
        if len(f) < 2:
            continue
        out.append(LoggedInUser(id=f"{f[0]}-{f[1]}", name=f[0], terminal=f[1], host="",
                                 loginTime=" ".join(f[2:])))
    return out


class MacSession(MachineSession):
    def __init__(self, machine: Machine):
        super().__init__(machine)
        self._runner = SSHRunner()
        self._prev_net: Optional[tuple] = None
        self.has_docker = False
        self.has_tailscale = False
        self.has_wireguard = False

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

        self.startup_items: list[MacStartupItem] = []
        self.startup_loaded = False
        self.software = []
        self.software_loaded = False
        self.updates: list[UpdateItem] = []
        self.updates_checked = False
        self.brew_outdated_count = 0
        self.users: list[LoggedInUser] = []
        self.log_lines: list[str] = []

        self.power_source: Optional[str] = None
        self.thermal_cpu_speed_limit: Optional[float] = None
        self.thermal_scheduler_limit: Optional[float] = None
        self.has_battery = False
        self.battery_percent: Optional[float] = None
        self.battery_charging = False
        self.battery_cycles: Optional[int] = None
        self.battery_health_percent: Optional[float] = None

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
            if self.has_tailscale:
                extra.append(NavItem.tailscale)
            if self.has_wireguard:
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
        if "END" not in sec or not host_lines or len(host_lines) < 5:
            self.state = ConnectionState(ConnectionState.FAILED,
                "Unexpected reply from the Mac. Is Remote Login enabled (System Settings > General > Sharing)?")
            return False
        self.host_name = host_lines[0]
        self.host_os = f"{host_lines[1]} {host_lines[2]}"
        try:
            self.host_uptime = float(host_lines[3])
        except ValueError:
            self.host_uptime = 0.0

        try:
            page_size = float((sec.get("PAGESIZE") or ["4096"])[0])
        except (ValueError, IndexError):
            page_size = 4096.0
        try:
            mem_bytes = float(host_lines[4])
        except (ValueError, IndexError):
            mem_bytes = 0.0
        used, total = _parse_memory(sec.get("VMSTAT", []), page_size, mem_bytes)
        self.mem_used, self.mem_total = used, total
        self.push(self.mem_history, (used / total * 100) if total > 0 else None)

        cpu = _parse_cpu(sec.get("TOP", []))
        if cpu is not None:
            self.cpu_percent = cpu
        self.push(self.cpu_history, self.cpu_percent)

        self.storage = [StorageItem(id=mount, title=mount, subtitle=dev, size=size, used=u)
                         for dev, size, u, mount in _parse_filesystems(sec.get("DF", []))]

        self.services = sorted(_parse_launchd(sec.get("LAUNCHD", [])), key=lambda s: s.name)
        self.services_loaded = True

        rx, tx = _parse_net(sec.get("NETSTAT", []))
        now = time.time()
        if self._prev_net:
            dt = now - self._prev_net[2]
            if dt > 0.5:
                self.net_rx_rate = max(0, rx - self._prev_net[0]) / dt
                self.net_tx_rate = max(0, tx - self._prev_net[1]) / dt
        self._prev_net = (rx, tx, now)

        feat = set(sec.get("FEAT", []))
        self.has_docker = "DOCKER" in feat
        self.has_tailscale = "TAILSCALE" in feat
        self.has_wireguard = "WIREGUARD" in feat

        self.state = ConnectionState(ConnectionState.CONNECTED)
        self.last_refresh = time.time()

        if self._containers_active and self.has_docker and now - self._last_container_load_time > 10:
            self._last_container_load_time = now
            self._load_containers_impl()
        return True

    def _close_backend(self):
        self._runner.close()
        self._prev_net = None
        self._container_collector.close()

    # -- Containers --
    def set_containers_active(self, active: bool):
        self._containers_active = active

    def _load_containers_impl(self):
        try:
            text = self._container_collector.sh(docker_.SAMPLE_SCRIPT, self._ssh(), sudo=self.machine.useSudo, merge=True)
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
            try:
                self._container_collector.sh(f"docker {verb} {quote(c.name)}", self._ssh(), sudo=self.machine.useSudo, merge=True)
            except Exception:
                pass
            self._load_containers_impl()
        self.submit(do, "containers")

    def container_logs(self, c: docker_.DockerContainer, on_done):
        def do():
            try:
                out = self._container_collector.sh(f"docker logs --tail 300 {quote(c.name)}", self._ssh(), sudo=self.machine.useSudo, merge=True)
            except Exception:
                out = "Couldn't load logs."
            on_done(out)
        self.submit(do)

    # -- Power & Thermal --
    def refresh_power_thermal(self):
        def do():
            try:
                t = self._runner.sh(POWER_SCRIPT, self._ssh())
            except Exception:
                return
            sec = _sections(t)
            therm = sec.get("THERM", [])
            v = _kv(therm, "CPU_Speed_Limit")
            self.thermal_cpu_speed_limit = float(v) if v else None
            v = _kv(therm, "CPU_Scheduler_Limit")
            self.thermal_scheduler_limit = float(v) if v else None
            ps_lines = sec.get("PS", [])
            import re
            m = re.search(r"'([^']+)'", ps_lines[0]) if ps_lines else None
            self.power_source = m.group(1) if m else None
            batt = sec.get("BATT", [])
            max_cap = _kv(batt, "MaxCapacity")
            max_cap = float(max_cap) if max_cap else 0
            self.has_battery = max_cap > 0
            if self.has_battery:
                cur_cap = _kv(batt, "CurrentCapacity")
                cur_cap = float(cur_cap) if cur_cap else 0
                self.battery_percent = min(100, cur_cap / max_cap * 100)
                self.battery_charging = _kv(batt, "IsCharging") == "Yes"
                cycles = _kv(batt, "CycleCount")
                self.battery_cycles = int(cycles) if cycles else None
                design = _kv(batt, "DesignCapacity")
                design = float(design) if design else 0
                if design > 0:
                    self.battery_health_percent = min(100, max_cap / design * 100)
        self.submit(do, "power")

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
            if verb == "stop":
                cmd = f"launchctl kickstart -k gui/$(id -u) {quote(s.name)} 2>&1 || launchctl stop {quote(s.name)}"
            else:
                cmd = f"launchctl kickstart -k gui/$(id -u) {quote(s.name)}"
            try:
                out = self._runner.sh(cmd, self._ssh(), sudo=True, merge=True)
                if "error" in out.lower():
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
            self.refresh_processes()
        self.submit(do, "processes")

    def reboot(self):
        self.submit(lambda: self._runner.sh("shutdown -r now", self._ssh(), sudo=True))

    def shutdown(self):
        self.submit(lambda: self._runner.sh("shutdown -h now", self._ssh(), sudo=True))

    # -- Startup Items --
    def refresh_startup(self):
        def do():
            try:
                t = self._runner.sh(STARTUP_SCRIPT, self._ssh(), sudo=True, merge=True)
            except Exception:
                return
            self.startup_items = _parse_startup_items(t)
            self.startup_loaded = True
        self.submit(do, "startup")

    def set_startup_enabled(self, item: MacStartupItem, enabled: bool):
        def do():
            cmd = (f"launchctl enable {item.domain}/{quote(item.label)}" if enabled
                   else f"launchctl disable {item.domain}/{quote(item.label)}")
            try:
                self._runner.sh(cmd, self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            self.refresh_startup()
        self.submit(do, "startup")

    # -- Installed Software --
    def refresh_software(self):
        def do():
            try:
                t = self._runner.sh(SOFTWARE_SCRIPT, self._ssh())
            except Exception:
                return
            self.software = _parse_software(t)
            self.software_loaded = True
        self.submit(do, "software")

    # -- Updates --
    def refresh_updates(self):
        def do():
            try:
                t = self._runner.sh(UPDATES_SCRIPT, self._ssh())
            except Exception:
                return
            parts = t.split("---BREW---")
            brew_items = _parse_brew_outdated(parts[1] if len(parts) > 1 else "")
            self.updates = _parse_software_updates(parts[0] if parts else "") + brew_items
            self.brew_outdated_count = len(brew_items)
            self.updates_checked = True
        self.submit(do, "updates")

    def upgrade_all(self, on_done):
        def do():
            try:
                out = self._runner.sh("brew upgrade 2>&1", self._ssh(), sudo=False, merge=True)
            except Exception:
                out = "Homebrew isn't installed, or the upgrade failed."
            self.refresh_updates()
            on_done("Note: macOS system updates still need “softwareupdate -i -a” run manually (it can "
                     "require a restart). This ran “brew upgrade” for Homebrew packages:\n\n" + out)
        self.submit(do)

    # -- Logs --
    def refresh_logs(self, filter: str = ""):
        def do():
            pred = filter.strip()
            if not pred:
                cmd = "log show --last 15m --style compact --predicate 'messageType == 16 OR messageType == 17' 2>/dev/null | tail -300"
            else:
                cmd = f"log show --last 1h --style compact --predicate {quote('process == ' + chr(34) + pred + chr(34))} 2>/dev/null | tail -300"
            try:
                t = self._runner.sh(cmd, self._ssh(), sudo=True, merge=True)
            except Exception:
                t = "Couldn't load logs."
            self.log_lines = t.split("\n")
        self.submit(do, "logs")

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
            script = (f'WGDIR="$(brew --prefix)/etc/wireguard"\n'
                      f'mkdir -p "$WGDIR"\n'
                      f'echo {b64} | base64 -d > /tmp/wg-{name}.conf\n'
                      f'install -m 600 /tmp/wg-{name}.conf "$WGDIR/{name}.conf"\n'
                      f'rm -f /tmp/wg-{name}.conf\n')
            try:
                out = self._runner.sh(script, self._ssh(), sudo=True, merge=True)
            except Exception as e:
                out = str(e)
            self._refresh_wireguard_sync()
            trimmed = out.strip()
            lowered = trimmed.lower()
            on_done(out if (trimmed and ("error" in lowered or "permission" in lowered)) else None)
        self.submit(do, "wireguard")

    def _refresh_wireguard_sync(self):
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
                out = self._runner.sh(f'wg-quick up "$(brew --prefix)/etc/wireguard/{name}.conf"', self._ssh(), sudo=True, merge=True)
                if "error" in out.lower():
                    self.wireguard_error = out
            except Exception as e:
                self.wireguard_error = str(e)
            self._refresh_wireguard_sync()
        self.submit(do, "wireguard")

    def disconnect_wireguard_tunnel(self, name: str):
        def do():
            if not wg_valid_name(name):
                return
            try:
                self._runner.sh(f"wg-quick down {name}", self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            self._refresh_wireguard_sync()
        self.submit(do, "wireguard")

    def remove_wireguard_tunnel(self, name: str):
        def do():
            if not wg_valid_name(name):
                return
            try:
                self._runner.sh(f'wg-quick down {name} 2>/dev/null; rm -f "$(brew --prefix)/etc/wireguard/{name}.conf"', self._ssh(), sudo=True, merge=True)
            except Exception:
                pass
            self._refresh_wireguard_sync()
        self.submit(do, "wireguard")

    # -- Users / Console --
    def refresh_users(self):
        def do():
            try:
                t = self._runner.sh("who", self._ssh())
                self.users = _parse_who(t)
            except Exception:
                pass
        self.submit(do, "users")

    def run_console(self, command: str, on_done):
        def do():
            try:
                out = self._runner.sh(command, self._ssh(), sudo=True, merge=True)
            except Exception as e:
                out = str(e)
            on_done(out)
        self.submit(do)
