"""Manages a Windows machine over SSH + PowerShell.

Mirrors App/Backends/WindowsSession.swift. The remote PowerShell scripts are copied over close to
verbatim (same WMI classes/perf counters, same `Sx` JSON-dump helper convention), since they're the
part of this app most sensitive to subtle behavioral drift.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from core import Machine, MachineStore, NavItem, ConnectionState, StorageItem, ServiceRow, ProcessRow, Fmt, sort_nav_items
from machine_session import MachineSession
from ssh_runner import SSHRunner, SSHSettings, pq
from wireguard_ import tunnels as wg_tunnels, is_valid_name as wg_valid_name


@dataclass
class LoggedInUser:
    id: str
    name: str
    terminal: str
    host: str
    loginTime: str
    idle: str = ""


@dataclass
class HWSensor:
    name: str
    type: str
    value: float
    identifier: str

    @property
    def formatted(self) -> str:
        if self.type == "Temperature":
            return f"{self.value:.0f}\u00b0C"
        if self.type == "Fan":
            return f"{self.value:.0f} RPM"
        if self.type == "Power":
            return f"{self.value:.1f} W"
        if self.type == "Clock":
            return Fmt.mhz(self.value)
        if self.type == "Voltage":
            return f"{self.value:.3f} V"
        if self.type in ("Load", "Control"):
            return f"{self.value:.0f}%"
        if self.type == "Data":
            return f"{self.value:.1f} GB"
        return f"{self.value:.1f}"


@dataclass
class NVInfo:
    name: str = ""
    util: Optional[float] = None
    mem_util: Optional[float] = None
    mem_used: Optional[float] = None
    mem_total: Optional[float] = None
    temp: Optional[float] = None
    power: Optional[float] = None
    power_limit: Optional[float] = None
    fan: Optional[float] = None
    clock_gr: Optional[float] = None
    clock_max_gr: Optional[float] = None
    clock_mem: Optional[float] = None
    clock_max_mem: Optional[float] = None
    driver: str = ""
    pstate: str = ""


@dataclass
class GPUAdapter:
    id: str
    name: str
    driver: str = ""
    driver_date: str = ""
    vram_total: Optional[float] = None
    status: str = ""
    processor: str = ""
    luid: Optional[str] = None
    engines: dict = field(default_factory=dict)
    vram_used: Optional[float] = None
    nvidia: Optional[NVInfo] = None
    sensors: list = field(default_factory=list)

    @property
    def util(self) -> Optional[float]:
        if self.nvidia and self.nvidia.util is not None:
            return self.nvidia.util
        if self.engines:
            return max(self.engines.values())
        return 0.0 if self.luid else None

    @property
    def vram_fraction(self) -> Optional[float]:
        if not self.vram_total or self.vram_used is None:
            return None
        return min(1.0, self.vram_used / self.vram_total)

    @property
    def temp(self) -> Optional[float]:
        if self.nvidia and self.nvidia.temp is not None:
            return self.nvidia.temp
        for s in self.sensors:
            if s.type == "Temperature" and ("Core" in s.name or "GPU" in s.name):
                return s.value
        for s in self.sensors:
            if s.type == "Temperature":
                return s.value
        return None

    @property
    def power(self) -> Optional[float]:
        if self.nvidia and self.nvidia.power is not None:
            return self.nvidia.power
        for s in self.sensors:
            if s.type == "Power":
                return s.value
        return None

    @property
    def is_virtual(self) -> bool:
        needles = ["Basic Display", "Basic Render", "Remote Display", "Hyper-V", "Virtual Display",
                   "Parsec", "Indirect"]
        return any(n.lower() in self.name.lower() for n in needles)


@dataclass
class GPUProcess:
    pid: int
    name: str
    util: float
    vram: float


@dataclass
class GPUHistory:
    util: list = field(default_factory=list)
    vram: list = field(default_factory=list)


@dataclass
class StartupItem:
    kind: str  # hklm, hklm32, hkcu, folderUser, folderAll, task
    name: str
    command: str
    publisher: str
    scope: str
    disabled: bool
    taskPath: str = ""

    @property
    def id(self):
        return f"{self.kind}|{self.taskPath}|{self.name}"

    @property
    def source_label(self):
        return {"hklm": "Registry", "hklm32": "Registry", "hkcu": "Registry",
                "folderUser": "Startup folder", "folderAll": "Startup folder",
                "task": "Scheduled task"}[self.kind]


@dataclass
class WinScheduledTask:
    name: str
    path: str
    state: str


@dataclass
class InstalledSoftware:
    name: str
    version: str
    publisher: str


@dataclass
class EventLogEntry:
    time: str
    level: str
    log: str
    message: str


@dataclass
class CoreLoad:
    name: str
    util: float = 0
    freqMHz: float = 0

    @property
    def id(self):
        return self.name


@dataclass
class WinCPUInfo:
    name: str = ""
    manufacturer: str = ""
    cores: int = 0
    logical: int = 0
    maxMHz: float = 0
    l2KB: float = 0
    l3KB: float = 0
    virtualization: Optional[bool] = None


# ---------------------------------------------------------------------------
# Remote PowerShell scripts (PowerShell 5.1 compatible)
# ---------------------------------------------------------------------------

PRELUDE = (
    "$ErrorActionPreference='SilentlyContinue'\n"
    "function Sx($n,$o){'##'+$n;$j=ConvertTo-Json -InputObject @($o) -Compress -Depth 3;"
    "[regex]::Replace($j,'[^\\x00-\\x7F]',{param($m) '\\u{0:x4}' -f [int][char]$m.Value})}\n"
    "function Pf($c){Get-CimInstance ('Win32_PerfFormattedData_'+$c)}\n"
)

GPU_COUNTERS = r"""
$g=@{}
foreach($e in (Pf 'GPUPerformanceCounters_GPUEngine')){if($e.UtilizationPercentage -gt 0 -and $e.Name -match 'luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_\d+_eng_(\d+)_engtype_(.+)$'){$g[$matches[1]+'|'+$matches[3]+'|'+$matches[2]]+=$e.UtilizationPercentage}}
$t=@{}
foreach($k in $g.Keys){$p=$k.Split('|');$id=$p[0]+'|'+$p[1];$v=[math]::Min(100,$g[$k]);if($v -gt $t[$id]){$t[$id]=$v}}
Sx GPUENG ($t.Keys|%{$p=$_.Split('|');[pscustomobject]@{luid=$p[0];type=$p[1];util=$t[$_]}})
Sx GPUMEM ((Pf 'GPUPerformanceCounters_GPUAdapterMemory')|%{if($_.Name -match 'luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_(\d+)'){[pscustomobject]@{luid=$matches[1];phys=[int]$matches[2];ded=$_.DedicatedUsage}}})
"""

GPU_STATIC = r"""
$vr=@{}
Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*'|%{$q=$_.'HardwareInformation.qwMemorySize';if($q -is [byte[]]){$q=[BitConverter]::ToUInt64($q,0)};if($q){$vr[[string]$_.DriverDesc]=[double]$q}}
Sx GPUS (Get-CimInstance Win32_VideoController|%{[pscustomobject]@{name=$_.Name;ram=[double]$_.AdapterRAM;vram=$vr[[string]$_.Name];driver=$_.DriverVersion;date=$(if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')});status=$_.Status;proc=$_.VideoProcessor}})
"""

SAMPLE_SCRIPT = PRELUDE + r"""
$os=Get-CimInstance Win32_OperatingSystem
Sx HOST ([pscustomobject]@{name=$env:COMPUTERNAME;os=$os.Caption;up=[int]((Get-Date)-$os.LastBootUpTime).TotalSeconds;memTotal=[double]$os.TotalVisibleMemorySize*1KB;memFree=[double]$os.FreePhysicalMemory*1KB})
Sx MEM (Pf 'PerfOS_Memory'|select AvailableBytes)
Sx CORES (Pf 'Counters_ProcessorInformation'|select Name,PercentProcessorUtility,PercentProcessorTime,PercentProcessorPerformance)
Sx CPUINFO (Get-CimInstance Win32_Processor|select Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,L2CacheSize,L3CacheSize,VirtualizationFirmwareEnabled)
Sx VOL (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3'|select DeviceID,VolumeName,Size,FreeSpace)
Sx SVC (Get-CimInstance Win32_Service|select Name,DisplayName,State,StartMode)
Sx NET (Pf 'Tcpip_NetworkInterface'|select Name,BytesReceivedPersec,BytesSentPersec)
$lh=Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor
if(!$lh){$lh=Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor}
Sx SENS ($lh|?{$_.SensorType -notin 'Throughput','Factor','Level','SmallData'}|select Name,SensorType,Value,Identifier)
'##FEAT'
if(Test-Path 'C:\Program Files\WireGuard\wireguard.exe'){'WIREGUARD'}
""" + GPU_STATIC + GPU_COUNTERS + "'##END'\n"

GPU_DETAIL_SCRIPT = PRELUDE + r"""
$nv=(Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue).Source
if(!$nv){$nv=@("$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe","$env:SystemRoot\System32\nvidia-smi.exe")|?{Test-Path $_}|select -First 1}
'##NVSMI'
if($nv){& $nv --query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.gr,clocks.max.gr,clocks.mem,clocks.max.mem,driver_version,pstate --format=csv,noheader,nounits 2>$null}
$pu=@{}
foreach($e in (Pf 'GPUPerformanceCounters_GPUEngine')){if($e.UtilizationPercentage -gt 0 -and $e.Name -match '^pid_(\d+)_.*engtype_(.+)$'){$pu[$matches[1]+'|'+$matches[2]]+=$e.UtilizationPercentage}}
$pm=@{}
foreach($e in (Pf 'GPUPerformanceCounters_GPUProcessMemory')){if($e.Name -match '^pid_(\d+)_'){$pm[[int]$matches[1]]+=$e.DedicatedUsage}}
$ids=@{}
foreach($k in $pu.Keys){$p=$k.Split('|');$i=[int]$p[0];$v=[math]::Min(100,$pu[$k]);if($v -gt $ids[$i]){$ids[$i]=$v}}
foreach($k in $pm.Keys){if(-not $ids.ContainsKey($k)){$ids[$k]=0}}
Sx GPUPROC ($ids.Keys|%{[pscustomobject]@{pid=$_;name=(Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName;util=$ids[$_];vram=[double]$pm[$_]}}|sort util,vram -desc|select -First 15)
'##END'
"""

PROCESSES_SCRIPT = PRELUDE + r"""
Sx CORES (Pf 'Counters_ProcessorInformation'|?{$_.Name -ne '_Total'}|select Name)
Sx PROCS (Pf 'PerfProc_Process'|?{$_.Name -ne '_Total' -and $_.Name -ne 'Idle'}|sort PercentProcessorTime -desc|%{[pscustomobject]@{pid=$_.IDProcess;name=($_.Name -replace '#\d+$','');cpu=$_.PercentProcessorTime;ws=$_.WorkingSetPrivate}})
'##END'
"""


def action_script(body: str) -> str:
    return "$ErrorActionPreference='Stop'\ntry{\n" + body + "\n'OK'}catch{'ERROR: '+$_.Exception.Message}"


STARTUP_SCRIPT = PRELUDE + r"""
$ap='\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\'
function Dis($path,$name){try{$v=Get-ItemPropertyValue -LiteralPath $path -Name $name -ErrorAction Stop}catch{$v=$null};[bool]($v -and ($v[0] -band 1))}
function Exe($c){$c=[Environment]::ExpandEnvironmentVariables($c);if($c -match '^\s*"([^"]+)"'){$matches[1]}elseif($c -match '^\s*(.+?\.(exe|bat|cmd|com|lnk|msc))'){$matches[1]}else{($c.Trim() -split ' ')[0]}}
function Pub($c){$p=Exe $c;if($p -and (Test-Path -LiteralPath $p)){$i=(Get-Item -LiteralPath $p).VersionInfo;if($i.CompanyName){$i.CompanyName.Trim()}elseif($i.ProductName){$i.ProductName.Trim()}}}
$o=@()
foreach($r in @(@('hklm','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',('HKLM:'+$ap+'Run'),'All users'),@('hklm32','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run',('HKLM:'+$ap+'Run32'),'All users (32-bit)'),@('hkcu','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',('HKCU:'+$ap+'Run'),'Current user'))){
  $k=Get-ItemProperty -LiteralPath $r[1] -ErrorAction SilentlyContinue
  if(!$k){continue}
  foreach($pr in $k.PSObject.Properties){if($pr.Name -match '^PS'){continue};$o+=[pscustomobject]@{kind=$r[0];name=$pr.Name;cmd=[string]$pr.Value;pub=[string](Pub $pr.Value);scope=$r[3];disabled=(Dis $r[2] $pr.Name);path=''}}
}
foreach($f in @(@('folderUser',"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup",('HKCU:'+$ap+'StartupFolder'),'Current user'),@('folderAll',"$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp",('HKLM:'+$ap+'StartupFolder'),'All users'))){
  if(!(Test-Path -LiteralPath $f[1])){continue}
  foreach($i in (Get-ChildItem -LiteralPath $f[1] -File -Force -ErrorAction SilentlyContinue|?{$_.Name -ne 'desktop.ini'})){
    $c=$i.FullName;if($i.Extension -eq '.lnk'){try{$s=(New-Object -ComObject WScript.Shell).CreateShortcut($i.FullName);$c=('"'+$s.TargetPath+'" '+$s.Arguments).Trim()}catch{}}
    $o+=[pscustomobject]@{kind=$f[0];name=$i.Name;cmd=$c;pub=[string](Pub $c);scope=$f[3];disabled=(Dis $f[2] $i.Name);path=''}
  }
}
foreach($t in (Get-ScheduledTask -ErrorAction SilentlyContinue|?{$_.TaskPath -notlike '\Microsoft\Windows\*' -and ($_.Triggers|?{$_.CimClass.CimClassName -match 'LogonTrigger|BootTrigger'})})){
  $a=$t.Actions|select -First 1;$c=(([string]$a.Execute)+' '+([string]$a.Arguments)).Trim()
  $o+=[pscustomobject]@{kind='task';name=$t.TaskName;cmd=$c;pub=[string](Pub $c);scope=$(if($t.Principal.UserId){[string]$t.Principal.UserId}else{'Task Scheduler'});disabled=($t.State -eq 'Disabled');path=$t.TaskPath}
}
Sx STARTUP $o
'##END'
"""


def set_startup_script(item: StartupItem, disabled: bool) -> str:
    ap = "\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\"
    name = pq(item.name)
    if item.kind == "task":
        verb = "Disable-ScheduledTask" if disabled else "Enable-ScheduledTask"
        return action_script(f"{verb} -TaskName {name} -TaskPath {pq(item.taskPath)} | Out-Null")
    key = {"hklm": "HKLM:" + ap + "Run", "hklm32": "HKLM:" + ap + "Run32", "hkcu": "HKCU:" + ap + "Run",
           "folderUser": "HKCU:" + ap + "StartupFolder", "folderAll": "HKLM:" + ap + "StartupFolder"}[item.kind]
    flag = "$true" if disabled else "$false"
    return action_script(f"""
$k={pq(key)}
if(!(Test-Path -LiteralPath $k)){{New-Item -Path $k -Force|Out-Null}}
$v=New-Object byte[] 12
if({flag}){{$v[0]=3;[BitConverter]::GetBytes([DateTime]::UtcNow.ToFileTime()).CopyTo($v,4)}}else{{$v[0]=2}}
Set-ItemProperty -LiteralPath $k -Name {name} -Value $v -Type Binary
""")


SCHEDULED_TASKS_SCRIPT = PRELUDE + r"""
Sx TASKS (Get-ScheduledTask -ErrorAction SilentlyContinue|?{$_.TaskPath -notlike '\Microsoft\*'}|select @{n='name';e={$_.TaskName}},@{n='path';e={$_.TaskPath}},@{n='state';e={[string]$_.State}})
'##END'
"""

SOFTWARE_SCRIPT = PRELUDE + r"""
$keys='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
Sx SOFT (Get-ItemProperty $keys -ErrorAction SilentlyContinue|?{$_.DisplayName -and -not $_.SystemComponent}|select @{n='name';e={$_.DisplayName}},@{n='version';e={[string]$_.DisplayVersion}},@{n='publisher';e={$_.Publisher}} -Unique)
'##END'
"""

UPDATES_SCRIPT = PRELUDE + r"""
Sx HOTFIX (Get-HotFix -ErrorAction SilentlyContinue|select HotFixID,@{n='date';e={if($_.InstalledOn){$_.InstalledOn.ToString('yyyy-MM-dd')}}})
Sx WU ([pscustomobject]@{state=(Get-Service wuauserv -ErrorAction SilentlyContinue).Status.ToString()})
'##END'
"""


def event_log_script(unit: str) -> str:
    filter_ = unit.strip()
    log_names = "'Application','System'" if not filter_ else pq(filter_)
    return PRELUDE + f"""
Sx EVT (Get-WinEvent -FilterHashtable @{{LogName={log_names};Level=1,2,3}} -MaxEvents 200 -ErrorAction SilentlyContinue|select @{{n='time';e={{$_.TimeCreated.ToString('MM-dd HH:mm:ss')}}}},@{{n='level';e={{$_.LevelDisplayName}}}},@{{n='log';e={{$_.LogName}}}},@{{n='msg';e={{($_.Message -split "`n")[0]}}}})
'##END'
"""


USERS_SCRIPT = PRELUDE + r"""
Sx USR (quser 2>$null | Select-Object -Skip 1 | %{$l=$_.Trim() -replace '\s{2,}',',';$f=$l.Split(',');[pscustomobject]@{name=$f[0];session=$(if($f.Count -gt 1){$f[1]}else{''});state=$(if($f.Count -gt 3){$f[3]}else{''});idle=$(if($f.Count -gt 4){$f[4]}else{''});logon=$(if($f.Count -gt 5){$f[5]}else{''})})
'##END'
"""

WIREGUARD_STATUS_SCRIPT = PRELUDE + r"""
Sx WGSVC (Get-Service -Name 'WireGuardTunnel$*' -ErrorAction SilentlyContinue|select Name,Status)
'##WGDUMP'
$wg=(Get-Command wg.exe -ErrorAction SilentlyContinue).Source
if(!$wg){$wg='C:\Program Files\WireGuard\wg.exe'}
if(Test-Path $wg){& $wg show all dump 2>$null}
'##END'
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("##"):
            current = line[2:].strip()
            out[current] = []
            continue
        if current is not None:
            out[current].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _rows(text: Optional[str]) -> list[dict]:
    if not text:
        return []
    try:
        obj = json.loads(text)
    except Exception:
        return []
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        return [obj]
    return []


def _num(r: dict, k: str) -> Optional[float]:
    v = r.get(k)
    if isinstance(v, (int, float)):
        v = float(v)
    elif isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return None
    else:
        return None
    # A handful of WMI/sensor counters can legitimately come back NaN or +/-Inf (e.g. a percentage
    # computed over a zero denominator) — treat that the same as "no reading" rather than letting
    # "nan"/"inf" leak into a formatted label.
    return v if math.isfinite(v) else None


def _str(r: dict, k: str) -> Optional[str]:
    v = r.get(k)
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    return None


def _flag(r: dict, k: str) -> Optional[bool]:
    v = r.get(k)
    return v if isinstance(v, bool) else None


_NET_SKIP = ["isatap", "teredo", "loopback", "6to4", "vethernet", "-wfp", "-qos", "-virtualbox",
             "-npcap", "filter", "pseudo", "bluetooth", "wan miniport", "tailscale"]


def _parse_network(rows: list[dict]):
    rx = tx = 0.0
    for r in rows:
        n = (_str(r, "Name") or "").lower()
        if any(s in n for s in _NET_SKIP):
            continue
        rx += _num(r, "BytesReceivedPersec") or 0
        tx += _num(r, "BytesSentPersec") or 0
    return rx, tx


def _parse_sensors(rows: list[dict]) -> list[HWSensor]:
    out = []
    for r in rows:
        name, type_, v, id_ = _str(r, "Name"), _str(r, "SensorType"), _num(r, "Value"), _str(r, "Identifier")
        if name and type_ and v is not None and id_:
            out.append(HWSensor(name=name, type=type_, value=v, identifier=id_))
    return out


_LHM_TYPE_NAMES = {"temperature": "Temperature", "fan": "Fan", "power": "Power", "voltage": "Voltage",
                    "clock": "Clock", "load": "Load", "control": "Control", "data": "Data"}


def _lhm_sensors(data: bytes) -> list[HWSensor]:
    try:
        root = json.loads(data)
    except Exception:
        return []
    out: list[HWSensor] = []

    def walk(n: dict):
        sensor_id = n.get("SensorId")
        if sensor_id:
            parts = sensor_id.split("/")
            if len(parts) >= 2:
                type_ = _LHM_TYPE_NAMES.get(parts[-2].lower())
                # LHM's Remote Web Server formats "Value" using the host machine's own locale, so on
                # any non-US-English Windows install this is "0,702 V" / "1,3 W" (comma decimal) —
                # float() rejects that outright, which silently dropped every single sensor on a
                # European-locale box rather than erroring loudly.
                val_str = (n.get("Value") or "").split(" ")[0].replace(",", ".")
                try:
                    v = float(val_str)
                except ValueError:
                    v = None
                # LHM itself sometimes reports a literal "NaN" (e.g. Network Utilization with no
                # active interface data) — float("NaN") parses fine rather than raising, so it needs
                # an explicit isfinite check or it renders as a literal "nan%" in the UI.
                if v is not None and not math.isfinite(v):
                    v = None
                if type_ and v is not None:
                    out.append(HWSensor(name=n.get("Text") or sensor_id, type=type_, value=v, identifier=sensor_id))
        for c in n.get("Children") or []:
            walk(c)

    walk(root)
    return out


def _parse_nvidia(text: Optional[str]) -> list[NVInfo]:
    if not text:
        return []
    out = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        f = raw.split(", ")
        if len(f) < 16:
            continue

        def v(s):
            s = s.strip()
            try:
                return float(s)
            except ValueError:
                return None

        out.append(NVInfo(name=f[1].strip(), util=v(f[2]), mem_util=v(f[3]),
                           mem_used=(v(f[4]) * 1_048_576 if v(f[4]) is not None else None),
                           mem_total=(v(f[5]) * 1_048_576 if v(f[5]) is not None else None),
                           temp=v(f[6]), power=v(f[7]), power_limit=v(f[8]), fan=v(f[9]),
                           clock_gr=v(f[10]), clock_max_gr=v(f[11]), clock_mem=v(f[12]), clock_max_mem=v(f[13]),
                           driver=f[14].strip(), pstate=f[15].strip()))
    return out


def _parse_gpus(controllers: list[dict], engines: list[dict], memory: list[dict], nvidia: list[NVInfo],
                sensors: list[HWSensor]) -> list[GPUAdapter]:
    adapters: list[GPUAdapter] = []
    for i, r in enumerate(controllers):
        name = _str(r, "name")
        if not name:
            continue
        reg, wmi = _num(r, "vram") or 0, _num(r, "ram") or 0
        adapters.append(GPUAdapter(id=f"{i}|{name}", name=name, driver=_str(r, "driver") or "",
                                    driver_date=_str(r, "date") or "",
                                    vram_total=(reg if reg > 0 else (wmi if wmi > 0 else None)),
                                    status=_str(r, "status") or "", processor=_str(r, "proc") or ""))
    real = [i for i, a in enumerate(adapters) if not a.is_virtual]
    if not real:
        real = list(range(len(adapters)))

    luids: list[str] = []
    for m in sorted(memory, key=lambda m: (_num(m, "phys") or 0, _str(m, "luid") or "")):
        l = _str(m, "luid")
        if l and l not in luids:
            luids.append(l)
    for e in engines:
        l = _str(e, "luid")
        if l and l not in luids:
            luids.append(l)

    for n, idx in enumerate(real):
        if n >= len(luids):
            break
        luid = luids[n]
        adapters[idx].luid = luid
        for e in engines:
            if _str(e, "luid") == luid:
                t, u = _str(e, "type"), _num(e, "util")
                if t and u is not None:
                    adapters[idx].engines[t] = u
        segments = [m for m in memory if _str(m, "luid") == luid]
        if segments:
            adapters[idx].vram_used = sum(_num(m, "ded") or 0 for m in segments)

    nv_idx = [i for i in real if "nvidia" in adapters[i].name.lower()]
    for n, idx in enumerate(nv_idx):
        match = None
        if len(nv_idx) == 1:
            match = next((nv for nv in nvidia if nv.name.lower() == adapters[idx].name.lower()), None)
        if match is None and n < len(nvidia):
            match = nvidia[n]
        if match is None:
            continue
        adapters[idx].nvidia = match
        if match.mem_total is not None:
            adapters[idx].vram_total = match.mem_total
        if match.mem_used is not None:
            adapters[idx].vram_used = match.mem_used

    groups: dict[str, list[HWSensor]] = {}
    for s in sensors:
        p = s.identifier.split("/")
        if len(p) >= 2 and p[0].startswith("gpu-"):
            groups.setdefault(f"{p[0]}/{p[1]}", []).append(s)
    used = set()
    for idx in real:
        n = adapters[idx].name.lower()
        vendor = "gpu-nvidia" if "nvidia" in n else ("gpu-amd" if ("amd" in n or "radeon" in n) else ("gpu-intel" if "intel" in n else ""))
        if not vendor:
            continue
        key = next((k for k in sorted(groups.keys()) if k.startswith(vendor) and k not in used), None)
        if key:
            used.add(key)
            adapters[idx].sensors = groups.get(key, [])

    return [adapters[i] for i in real]


def _parse_gpu_processes(rows: list[dict]) -> list[GPUProcess]:
    out = []
    for r in rows:
        pid = _num(r, "pid")
        if pid is None:
            continue
        out.append(GPUProcess(pid=int(pid), name=_str(r, "name") or f"pid {int(pid)}",
                               util=_num(r, "util") or 0, vram=_num(r, "vram") or 0))
    return out


def _parse_startup(rows: list[dict]) -> list[StartupItem]:
    out = []
    for r in rows:
        kind, name = _str(r, "kind"), _str(r, "name")
        if not kind or not name or kind not in ("hklm", "hklm32", "hkcu", "folderUser", "folderAll", "task"):
            continue
        out.append(StartupItem(kind=kind, name=name, command=_str(r, "cmd") or "", publisher=_str(r, "pub") or "",
                                scope=_str(r, "scope") or "", disabled=_flag(r, "disabled") or False,
                                taskPath=_str(r, "path") or ""))
    return out


def _parse_quser(text: str) -> list[LoggedInUser]:
    try:
        obj = json.loads(text) if text.strip() else []
    except Exception:
        return []
    rows = obj if isinstance(obj, list) else ([obj] if isinstance(obj, dict) else [])
    out = []
    for r in rows:
        name = _str(r, "name")
        if not name:
            continue
        out.append(LoggedInUser(id=name + (_str(r, "session") or ""), name=name,
                                 terminal=_str(r, "session") or "", host="", loginTime=_str(r, "logon") or "",
                                 idle=_str(r, "idle") or ""))
    return out


class WindowsSession(MachineSession):
    def __init__(self, machine: Machine):
        super().__init__(machine)
        self._runner = SSHRunner()
        self.has_wireguard = False
        self.wireguard_tunnels = []
        self.wireguard_error: Optional[str] = None

        self.gpus: list[GPUAdapter] = []
        self.sensors: list[HWSensor] = []
        self.gpu_history: dict[str, GPUHistory] = {}
        self.gpu_processes: list[GPUProcess] = []
        self._nvidia_info: list[NVInfo] = []

        self.startup_items: list[StartupItem] = []
        self.startup_loaded = False
        self.scheduled_tasks: list[WinScheduledTask] = []
        self.software: list[InstalledSoftware] = []
        self.software_loaded = False
        self.hotfix_count: Optional[int] = None
        self.hotfix_last_installed: Optional[str] = None
        self.wuauserv_running: Optional[bool] = None
        self.event_log: list[EventLogEntry] = []
        self.users: list[LoggedInUser] = []

        self.cpu_info = WinCPUInfo()
        self.cores: list[CoreLoad] = []
        self.core_history: dict[str, list[float]] = {}
        self.lhm_http_working: Optional[bool] = None

    @property
    def consoleIsAvailable(self):
        return True

    @property
    def consoleUnavailableReason(self):
        return ""

    @property
    def nav_items(self):
        items = set(self.machine.kind.nav_items)
        if self.has_wireguard:
            items.add(NavItem.vpn)
        return sort_nav_items(items)

    def _ssh(self) -> SSHSettings:
        return SSHSettings(host=self.machine.host, port=self.machine.port, user=self.machine.user,
                            password=MachineStore.secret(self.machine.id))

    def _poll(self) -> bool:
        if self.state.kind != ConnectionState.CONNECTED:
            self.state = ConnectionState(ConnectionState.CONNECTING)
        try:
            text = self._runner.ps(SAMPLE_SCRIPT, self._ssh())
        except Exception as e:
            self.state = ConnectionState(ConnectionState.FAILED, str(e))
            return False
        sec = _sections(text)
        host_rows = _rows(sec.get("HOST"))
        if "END" not in sec or not host_rows:
            hint = text.strip()[:160]
            self.state = ConnectionState(ConnectionState.FAILED,
                hint if hint else "Unexpected (empty) reply. Is PowerShell available over SSH on this machine?")
            return False
        h = host_rows[0]
        self.host_name = _str(h, "name") or ""
        self.host_os = _str(h, "os") or "Windows"
        self.host_uptime = _num(h, "up") or 0
        self.mem_total = _num(h, "memTotal") or 0

        mem_rows = _rows(sec.get("MEM"))
        perf = mem_rows[0] if mem_rows else None
        available = (_num(perf, "AvailableBytes") if perf else None) or _num(h, "memFree") or 0
        self.mem_used = max(0, self.mem_total - available)
        self.push(self.mem_history, (self.mem_used / self.mem_total * 100) if self.mem_total > 0 else None)

        core_rows = _rows(sec.get("CORES"))
        total_row = next((r for r in core_rows if _str(r, "Name") == "_Total"), None)
        if total_row:
            t = _num(total_row, "PercentProcessorTime") or 0
            util = _num(total_row, "PercentProcessorUtility")
            if util is None or (util == 0 and t > 0):
                util = t
            self.cpu_percent = min(100, max(0, util))
        self.push(self.cpu_history, self.cpu_percent)

        info_rows = _rows(sec.get("CPUINFO"))
        if info_rows:
            info = info_rows[0]
            self.cpu_info = WinCPUInfo(name=_str(info, "Name") or "", manufacturer=_str(info, "Manufacturer") or "",
                                        cores=int(_num(info, "NumberOfCores") or 0),
                                        logical=int(_num(info, "NumberOfLogicalProcessors") or 0),
                                        maxMHz=_num(info, "MaxClockSpeed") or 0, l2KB=_num(info, "L2CacheSize") or 0,
                                        l3KB=_num(info, "L3CacheSize") or 0,
                                        virtualization=_flag(info, "VirtualizationFirmwareEnabled"))
        cores = []
        for r in core_rows:
            name = _str(r, "Name")
            if not name or name == "_Total":
                continue
            parts = name.split(",")
            if len(parts) != 2:
                continue
            try:
                int(parts[0]); int(parts[1])
            except ValueError:
                continue
            t = _num(r, "PercentProcessorTime") or 0
            util = min(100, max(0, _num(r, "PercentProcessorUtility") if _num(r, "PercentProcessorUtility") is not None else t))
            perf_pct = _num(r, "PercentProcessorPerformance") or 0
            freq = self.cpu_info.maxMHz * perf_pct / 100 if self.cpu_info.maxMHz > 0 and perf_pct > 0 else 0
            cores.append(CoreLoad(name=name, util=util, freqMHz=freq))
        self.cores = sorted(cores, key=lambda c: c.name)
        for c in self.cores:
            self.push(self.core_history.setdefault(c.id, []), c.util, limit=30)

        vol_rows = _rows(sec.get("VOL"))
        storage = []
        for r in vol_rows:
            id_ = _str(r, "DeviceID")
            size = _num(r, "Size")
            if not id_ or not size:
                continue
            free = _num(r, "FreeSpace") or 0
            storage.append(StorageItem(id=id_, title=id_, subtitle=_str(r, "VolumeName") or "",
                                        size=int(size), used=int(size - free)))
        self.storage = sorted(storage, key=lambda s: s.id)

        svc_rows = _rows(sec.get("SVC"))
        services = []
        for r in svc_rows:
            name = _str(r, "Name")
            if not name:
                continue
            state = _str(r, "State") or ""
            services.append(ServiceRow(id=name, name=name, displayName=_str(r, "DisplayName") or name,
                                        state=state, startMode=_str(r, "StartMode") or "",
                                        isRunning=(state == "Running")))
        self.services = sorted(services, key=lambda s: s.displayName.lower())
        self.services_loaded = True

        rx, tx = _parse_network(_rows(sec.get("NET")))
        self.net_rx_rate, self.net_tx_rate = rx, tx

        self.has_wireguard = "WIREGUARD" in (sec.get("FEAT") or "")

        wmi_sensors = _parse_sensors(_rows(sec.get("SENS")))
        http_sensors = self._fetch_lhm_sensors_http()
        self.sensors = http_sensors if http_sensors is not None else wmi_sensors
        self.lhm_http_working = (http_sensors is not None) if self.machine.lhmPort > 0 else None

        self.gpus = _parse_gpus(_rows(sec.get("GPUS")), _rows(sec.get("GPUENG")), _rows(sec.get("GPUMEM")),
                                 self._nvidia_info, self.sensors)
        gpu_utils = [g.util for g in self.gpus if g.util is not None]
        self.gpu_percent = max(gpu_utils) if gpu_utils else None
        for g in self.gpus:
            hist = self.gpu_history.setdefault(g.id, GPUHistory())
            self.push(hist.util, g.util)
            self.push(hist.vram, (g.vram_fraction * 100) if g.vram_fraction is not None else None)

        # Total power: sums every "Power" sensor LibreHardwareMonitor reports (CPU package, GPU,
        # sometimes more) — not true wall power, but the closest estimate available without a
        # physical meter. None (not 0) when no Power sensor is reported at all, so the Dashboard can
        # tell "no data" apart from "genuinely idle".
        power_sensors = [s.value for s in self.sensors if s.type == "Power"]
        self.power_watts = sum(power_sensors) if power_sensors else None

        self.state = ConnectionState(ConnectionState.CONNECTED)
        self.last_refresh = time.time()
        return True

    def _fetch_lhm_sensors_http(self) -> Optional[list[HWSensor]]:
        if self.machine.lhmPort <= 0:
            return None
        try:
            resp = requests.get(f"http://{self.machine.host}:{self.machine.lhmPort}/data.json", timeout=5)
            if resp.status_code != 200:
                return None
            parsed = _lhm_sensors(resp.content)
            return parsed if parsed else None
        except Exception:
            return None

    def _close_backend(self):
        self._runner.close()

    def refresh_services(self):
        self.submit(lambda: self._poll(), "services")

    def refresh_gpu_detail(self):
        def do():
            try:
                t = self._runner.ps(GPU_DETAIL_SCRIPT, self._ssh())
            except Exception:
                return
            sec = _sections(t)
            self._nvidia_info = _parse_nvidia(sec.get("NVSMI"))
            self.gpu_processes = _parse_gpu_processes(_rows(sec.get("GPUPROC")))
            nvidia_names = [i for i, g in enumerate(self.gpus) if "nvidia" in g.name.lower()]
            for idx, gpu_idx in enumerate(nvidia_names):
                if idx >= len(self._nvidia_info):
                    continue
                g = self.gpus[gpu_idx]
                g.nvidia = self._nvidia_info[idx]
                if g.nvidia.mem_total is not None:
                    g.vram_total = g.nvidia.mem_total
                if g.nvidia.mem_used is not None:
                    g.vram_used = g.nvidia.mem_used
        self.submit(do, "gpu")

    def refresh_processes(self):
        def do():
            try:
                t = self._runner.ps(PROCESSES_SCRIPT, self._ssh())
            except Exception:
                return
            sec = _sections(t)
            n = max(1, len(_rows(sec.get("CORES"))))
            out = []
            for r in _rows(sec.get("PROCS")):
                pid, name = _num(r, "pid"), _str(r, "name")
                if pid is None or not name:
                    continue
                out.append(ProcessRow(pid=int(pid), name=name, cpu=min(100, (_num(r, "cpu") or 0) / n),
                                       mem=_num(r, "ws") or 0))
            self.processes = out
        self.submit(do, "processes")

    def service_action(self, s: ServiceRow, verb: str):
        def do():
            cmd = {"start": f"Start-Service -Name {pq(s.name)}",
                   "stop": f"Stop-Service -Name {pq(s.name)} -Force"}.get(verb, f"Restart-Service -Name {pq(s.name)} -Force")
            try:
                out = self._runner.ps(action_script(cmd), self._ssh())
            except Exception as e:
                out = str(e)
            self.perform_action(out, success_contains="OK")
            time.sleep(1)
            self._poll()
        self.submit(do, "services")

    def kill_process(self, p: ProcessRow):
        def do():
            try:
                self._runner.ps(action_script(f"Stop-Process -Id {p.pid} -Force"), self._ssh())
            except Exception:
                pass
            self.refresh_processes()
        self.submit(do, "processes")

    def reboot(self):
        self.submit(lambda: self._runner.ps(action_script("shutdown.exe /r /t 3 /f"), self._ssh()))

    def shutdown(self):
        self.submit(lambda: self._runner.ps(action_script("shutdown.exe /s /t 3 /f"), self._ssh()))

    # -- Startup / Scheduled Tasks / Software / Updates / Event Log / Users / Console --
    def refresh_startup(self):
        def do():
            try:
                t = self._runner.ps(STARTUP_SCRIPT, self._ssh())
            except Exception:
                return
            items = _parse_startup(_rows(_sections(t).get("STARTUP")))
            self.startup_items = sorted(items, key=lambda i: i.name.lower())
            self.startup_loaded = True
        self.submit(do, "startup")

    def set_startup(self, item: StartupItem, disabled: bool):
        def do():
            try:
                self._runner.ps(set_startup_script(item, disabled), self._ssh())
            except Exception:
                pass
            self.refresh_startup()
        self.submit(do, "startup")

    def refresh_scheduled_tasks(self):
        def do():
            try:
                t = self._runner.ps(SCHEDULED_TASKS_SCRIPT, self._ssh())
            except Exception:
                return
            out = []
            for r in _rows(_sections(t).get("TASKS")):
                name = _str(r, "name")
                if not name:
                    continue
                out.append(WinScheduledTask(name=name, path=_str(r, "path") or "", state=_str(r, "state") or ""))
            self.scheduled_tasks = sorted(out, key=lambda t: t.name.lower())
        self.submit(do, "scheduledTasks")

    def scheduled_task_action(self, t: WinScheduledTask, verb: str):
        def do():
            cmd = {
                "run": f"Start-ScheduledTask -TaskName {pq(t.name)} -TaskPath {pq(t.path)}",
                "enable": f"Enable-ScheduledTask -TaskName {pq(t.name)} -TaskPath {pq(t.path)} | Out-Null",
            }.get(verb, f"Disable-ScheduledTask -TaskName {pq(t.name)} -TaskPath {pq(t.path)} | Out-Null")
            try:
                self._runner.ps(action_script(cmd), self._ssh())
            except Exception:
                pass
            self.refresh_scheduled_tasks()
        self.submit(do, "scheduledTasks")

    def refresh_software(self):
        def do():
            try:
                t = self._runner.ps(SOFTWARE_SCRIPT, self._ssh())
            except Exception:
                return
            out = []
            for r in _rows(_sections(t).get("SOFT")):
                name = _str(r, "name")
                if not name:
                    continue
                out.append(InstalledSoftware(name=name, version=_str(r, "version") or "",
                                              publisher=_str(r, "publisher") or ""))
            self.software = sorted(out, key=lambda s: s.name.lower())
            self.software_loaded = True
        self.submit(do, "software")

    def refresh_windows_updates(self):
        def do():
            try:
                t = self._runner.ps(UPDATES_SCRIPT, self._ssh())
            except Exception:
                return
            sec = _sections(t)
            hotfix_rows = _rows(sec.get("HOTFIX"))
            self.hotfix_count = len(hotfix_rows)
            dates = [_str(r, "date") for r in hotfix_rows if _str(r, "date")]
            self.hotfix_last_installed = max(dates) if dates else None
            wu_rows = _rows(sec.get("WU"))
            self.wuauserv_running = (_str(wu_rows[0], "state") == "Running") if wu_rows else None
        self.submit(do, "updates")

    def refresh_event_log(self, filter: str = ""):
        def do():
            try:
                t = self._runner.ps(event_log_script(filter), self._ssh())
            except Exception:
                return
            out = []
            for r in _rows(_sections(t).get("EVT")):
                msg = _str(r, "msg")
                if msg is None:
                    continue
                out.append(EventLogEntry(time=_str(r, "time") or "", level=_str(r, "level") or "",
                                          log=_str(r, "log") or "", message=msg))
            self.event_log = out
        self.submit(do, "eventLog")

    def refresh_users(self):
        def do():
            try:
                t = self._runner.ps(USERS_SCRIPT, self._ssh())
            except Exception:
                return
            self.users = _parse_quser(_sections(t).get("USR", ""))
        self.submit(do, "users")

    def run_console(self, command: str, on_done):
        on_done = self._main_thread(on_done)
        def do():
            try:
                out = self._runner.ps(action_script(command), self._ssh())
            except Exception as e:
                out = str(e)
            on_done(out)
        self.submit(do)

    # -- WireGuard --
    def refresh_wireguard(self):
        def do():
            try:
                t = self._runner.ps(WIREGUARD_STATUS_SCRIPT, self._ssh())
            except Exception:
                return
            sec = _sections(t)
            names = []
            for r in _rows(sec.get("WGSVC")):
                name = _str(r, "Name") or ""
                if name.startswith("WireGuardTunnel$"):
                    names.append(name[len("WireGuardTunnel$"):])
            self.wireguard_tunnels = wg_tunnels(names, sec.get("WGDUMP", ""))
            self.wireguard_error = None
        self.submit(do, "wireguard")

    def add_wireguard_tunnel(self, name: str, config: str, on_done):
        on_done = self._main_thread(on_done)
        def do():
            if not wg_valid_name(name):
                on_done("Use letters, numbers, dashes and underscores only.")
                return
            import base64
            b64 = base64.b64encode(config.encode()).decode()
            script = f"""
$bytes=[Convert]::FromBase64String({pq(b64)})
$path="$env:TEMP\\{name}.conf"
[IO.File]::WriteAllBytes($path,$bytes)
$wg=(Get-Command wireguard.exe -ErrorAction SilentlyContinue).Source
if(!$wg){{$wg='C:\\Program Files\\WireGuard\\wireguard.exe'}}
& $wg /installtunnelservice $path
Remove-Item $path -ErrorAction SilentlyContinue
"""
            try:
                out = self._runner.ps(action_script(script), self._ssh())
            except Exception:
                out = None
            self._refresh_wireguard_sync()
            if out is None:
                on_done("Failed to install the tunnel.")
                return
            on_done(None if self.perform_action(out, success_contains="OK") else self.action_error_text)
        self.submit(do, "wireguard")

    def _refresh_wireguard_sync(self):
        try:
            t = self._runner.ps(WIREGUARD_STATUS_SCRIPT, self._ssh())
        except Exception:
            return
        sec = _sections(t)
        names = []
        for r in _rows(sec.get("WGSVC")):
            name = _str(r, "Name") or ""
            if name.startswith("WireGuardTunnel$"):
                names.append(name[len("WireGuardTunnel$"):])
        self.wireguard_tunnels = wg_tunnels(names, sec.get("WGDUMP", ""))

    def connect_wireguard_tunnel(self, name: str):
        def do():
            if not wg_valid_name(name):
                return
            try:
                self._runner.ps(action_script(f"Start-Service -Name {pq('WireGuardTunnel$' + name)}"), self._ssh())
            except Exception:
                pass
            self._refresh_wireguard_sync()
        self.submit(do, "wireguard")

    def disconnect_wireguard_tunnel(self, name: str):
        def do():
            if not wg_valid_name(name):
                return
            try:
                self._runner.ps(action_script(f"Stop-Service -Name {pq('WireGuardTunnel$' + name)} -Force"), self._ssh())
            except Exception:
                pass
            self._refresh_wireguard_sync()
        self.submit(do, "wireguard")

    def remove_wireguard_tunnel(self, name: str):
        def do():
            if not wg_valid_name(name):
                return
            script = f"""
$wg=(Get-Command wireguard.exe -ErrorAction SilentlyContinue).Source
if(!$wg){{$wg='C:\\Program Files\\WireGuard\\wireguard.exe'}}
& $wg /uninstalltunnelservice {pq(name)}
"""
            try:
                self._runner.ps(action_script(script), self._ssh())
            except Exception:
                pass
            self._refresh_wireguard_sync()
        self.submit(do, "wireguard")
