import Foundation

// MARK: - GPU / sensor models

struct HWSensor: Sendable, Identifiable, Hashable {
    var name: String
    var type: String       // Temperature, Fan, Power, Clock, Voltage, Load, Control, Data…
    var value: Double
    var identifier: String
    var id: String { identifier }
    var formatted: String {
        switch type {
        case "Temperature": String(format: "%.0f°C", value)
        case "Fan": String(format: "%.0f RPM", value)
        case "Power": String(format: "%.1f W", value)
        case "Clock": Fmt.mhz(value)
        case "Voltage": String(format: "%.3f V", value)
        case "Load", "Control": String(format: "%.0f%%", value)
        case "Data": String(format: "%.1f GB", value)
        default: String(format: "%.1f", value)
        }
    }
}

struct NVInfo: Sendable {
    var name = ""
    var util: Double?
    var memUtil: Double?
    var memUsed: Double?
    var memTotal: Double?
    var temp: Double?
    var power: Double?
    var powerLimit: Double?
    var fan: Double?
    var clockGr: Double?
    var clockMaxGr: Double?
    var clockMem: Double?
    var clockMaxMem: Double?
    var driver = ""
    var pstate = ""
}

struct GPUAdapter: Sendable, Identifiable {
    var id: String
    var name: String
    var driver = ""
    var driverDate = ""
    var vramTotal: Double?
    var status = ""
    var processor = ""
    var luid: String?
    var engines: [String: Double] = [:]
    var vramUsed: Double?
    var nvidia: NVInfo?
    var sensors: [HWSensor] = []

    var util: Double? { nvidia?.util ?? engines.values.max() ?? (luid == nil ? nil : 0) }
    var vramFraction: Double? { guard let t = vramTotal, t > 0, let u = vramUsed else { return nil }; return min(1, u / t) }
    var temp: Double? { nvidia?.temp ?? sensors.first { $0.type == "Temperature" && ($0.name.contains("Core") || $0.name.contains("GPU")) }?.value ?? sensors.first { $0.type == "Temperature" }?.value }
    var power: Double? { nvidia?.power ?? sensors.first { $0.type == "Power" }?.value }
    var isVirtual: Bool {
        ["Basic Display", "Basic Render", "Remote Display", "Hyper-V", "Virtual Display", "Parsec", "Indirect"].contains { name.localizedCaseInsensitiveContains($0) }
    }
}

extension NVInfo {
    /// Parses `nvidia-smi --query-gpu=… --format=csv,noheader,nounits` output (shared with Linux hosts).
    static func parse(_ text: String?) -> [NVInfo] { WinParse.nvidia(text) }
}

struct GPUProcess: Sendable, Identifiable {
    var pid: Int
    var name: String
    var util: Double
    var vram: Double
    var id: Int { pid }
}

struct GPUHistory: Sendable { var util: [Double] = []; var vram: [Double] = [] }

struct StartupItem: Sendable, Identifiable, Hashable {
    enum Kind: String, Sendable { case hklm, hklm32, hkcu, folderUser, folderAll, task }
    var kind: Kind
    var name: String
    var command: String
    var publisher: String
    var scope: String
    var disabled: Bool
    var taskPath: String
    var id: String { "\(kind.rawValue)|\(taskPath)|\(name)" }
    var sourceLabel: String {
        switch kind {
        case .hklm, .hklm32, .hkcu: "Registry"
        case .folderUser, .folderAll: "Startup folder"
        case .task: "Scheduled task"
        }
    }
}

struct WinScheduledTask: Sendable, Identifiable, Hashable {
    var name: String
    var path: String
    var state: String
    var id: String { path + name }
}

struct InstalledSoftware: Sendable, Identifiable, Hashable {
    var name: String
    var version: String
    var publisher: String
    var id: String { name }
}

struct EventLogEntry: Sendable, Identifiable, Hashable {
    var id = UUID()
    var time: String
    var level: String
    var log: String
    var message: String
}

struct CoreLoad: Sendable, Identifiable {
    var name: String
    var util: Double = 0
    var freqMHz: Double = 0
    var id: String { name }
}

struct WinCPUInfo: Sendable {
    var name = ""
    var manufacturer = ""
    var cores = 0
    var logical = 0
    var maxMHz: Double = 0
    var l2KB: Double = 0
    var l3KB: Double = 0
    var virtualization: Bool?
}

/// What the CPU / GPU pages read, so Linux hosts can reuse the same pages as Windows.
@MainActor protocol CPUHosting: AnyObject {
    var cpuInfo: WinCPUInfo { get }
    var cores: [CoreLoad] { get }
    var coreHistory: [String: [Double]] { get }
}

@MainActor protocol GPUHosting: AnyObject {
    var gpus: [GPUAdapter] { get }
    var gpuHistory: [String: GPUHistory] { get }
    var gpuProcesses: [GPUProcess] { get }
    func refreshGPUDetail() async
}

/// Manages a Windows machine over SSH + PowerShell.
@MainActor @Observable
final class WindowsSession: MachineSession, CPUHosting, GPUHosting, ConsoleHosting, UserHosting, WireGuardHosting {
    private let runner = SSHRunner()
    private(set) var hasWireGuard = false
    var wireguardTunnels: [WireGuardTunnel] = []
    var wireguardError: String?

    override var navItems: [NavItem] {
        var items = Set(machine.kind.navItems)
        if hasWireGuard { items.insert(.vpn) }
        return sortNavItems(items)
    }

    var gpus: [GPUAdapter] = []
    var sensors: [HWSensor] = []
    var gpuHistory: [String: GPUHistory] = [:]
    var gpuProcesses: [GPUProcess] = []
    private var nvidiaInfo: [NVInfo] = []

    var startupItems: [StartupItem] = []
    var startupLoaded = false
    var scheduledTasks: [WinScheduledTask] = []
    var software: [InstalledSoftware] = []
    var softwareLoaded = false
    var hotfixCount: Int?
    var hotfixLastInstalled: String?
    var wuauservRunning: Bool?
    var eventLog: [EventLogEntry] = []
    var users: [LoggedInUser] = []

    var cpuInfo = WinCPUInfo()
    var cores: [CoreLoad] = []
    var coreHistory: [String: [Double]] = [:]
    /// nil = LibreHardwareMonitor HTTP not configured; true/false once a fetch has been tried.
    var lhmHTTPWorking: Bool?

    var consoleIsAvailable: Bool { true }
    var consoleUnavailableReason: String { "" }

    private var ssh: SSHSettings {
        SSHSettings(host: machine.host, port: machine.port, user: machine.user, password: MachineStore.secret(for: machine.id))
    }

    override func poll() async -> Bool {
        if state != .connected { state = .connecting }
        let text: String
        do { text = try await runner.ps(WinScripts.sample, ssh) }
        catch { state = .failed(error.localizedDescription); return false }
        let sec = WinParse.sections(text)
        guard sec["END"] != nil, let hostRow = WinParse.rows(sec["HOST"]).first else {
            let hint = text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(160)
            state = .failed(hint.isEmpty ? "Unexpected (empty) reply. Is PowerShell available over SSH on this machine?" : "Unexpected reply from the machine: \(hint)")
            return false
        }
        hostName = hostRow.str("name") ?? ""
        hostOS = hostRow.str("os") ?? "Windows"
        hostUptime = hostRow.num("up") ?? 0
        memTotal = hostRow.num("memTotal") ?? 0

        let perf = WinParse.rows(sec["MEM"]).first
        let available = perf?.num("AvailableBytes") ?? hostRow.num("memFree") ?? 0
        memUsed = max(0, memTotal - available)
        push(&memHistory, memTotal > 0 ? memUsed / memTotal * 100 : nil)

        let coreRows = WinParse.rows(sec["CORES"])
        if let total = coreRows.first(where: { $0.str("Name") == "_Total" }) {
            let time = total.num("PercentProcessorTime") ?? 0
            let util = total.num("PercentProcessorUtility") ?? time
            cpuPercent = min(100, max(0, util == 0 && time > 0 ? time : util))
        }
        push(&cpuHistory, cpuPercent)

        if let info = WinParse.rows(sec["CPUINFO"]).first {
            cpuInfo = WinCPUInfo(name: info.str("Name") ?? "", manufacturer: info.str("Manufacturer") ?? "",
                                 cores: Int(info.num("NumberOfCores") ?? 0), logical: Int(info.num("NumberOfLogicalProcessors") ?? 0),
                                 maxMHz: info.num("MaxClockSpeed") ?? 0, l2KB: info.num("L2CacheSize") ?? 0,
                                 l3KB: info.num("L3CacheSize") ?? 0, virtualization: info.flag("VirtualizationFirmwareEnabled"))
        }
        cores = coreRows.compactMap { r -> CoreLoad? in
            guard let name = r.str("Name"), name != "_Total" else { return nil }
            let parts = name.split(separator: ",")
            guard parts.count == 2, Int(parts[0]) != nil, Int(parts[1]) != nil else { return nil }
            let time = r.num("PercentProcessorTime") ?? 0
            let util = min(100, max(0, r.num("PercentProcessorUtility") ?? time))
            let perfPct = r.num("PercentProcessorPerformance") ?? 0
            let freq = cpuInfo.maxMHz > 0 && perfPct > 0 ? cpuInfo.maxMHz * perfPct / 100 : 0
            return CoreLoad(name: name, util: util, freqMHz: freq)
        }.sorted { $0.name < $1.name }
        for c in cores { push(&coreHistory[c.id, default: []], c.util, limit: 30) }

        storage = WinParse.rows(sec["VOL"]).compactMap { r in
            guard let id = r.str("DeviceID"), let size = r.num("Size"), size > 0 else { return nil }
            let free = r.num("FreeSpace") ?? 0
            let label = r.str("VolumeName") ?? ""
            return StorageItem(id: id, title: id, subtitle: label, size: Int64(size), used: Int64(size - free))
        }.sorted { $0.id < $1.id }

        services = WinParse.rows(sec["SVC"]).compactMap { r in
            guard let name = r.str("Name") else { return nil }
            let state = r.str("State") ?? ""
            return ServiceRow(id: name, name: name, displayName: r.str("DisplayName") ?? name, state: state, startMode: r.str("StartMode") ?? "", isRunning: state == "Running")
        }.sorted { $0.displayName.localizedStandardCompare($1.displayName) == .orderedAscending }
        servicesLoaded = true

        let net = WinParse.network(WinParse.rows(sec["NET"]))
        netRxRate = net.rx; netTxRate = net.tx

        hasWireGuard = (sec["FEAT"] ?? "").contains("WIREGUARD")

        let wmiSensors = WinParse.sensors(WinParse.rows(sec["SENS"]))
        let httpSensors = await fetchLHMSensorsOverHTTP()
        sensors = httpSensors ?? wmiSensors
        // Sum of every Power sensor (CPU package, GPU…): not wall power, but the closest estimate without a meter.
        let watts = sensors.filter { $0.type == "Power" }.map(\.value)
        powerWatts = watts.isEmpty ? nil : watts.reduce(0, +)
        lhmHTTPWorking = machine.lhmPort > 0 ? (httpSensors != nil) : nil

        gpus = WinParse.gpus(controllers: WinParse.rows(sec["GPUS"]), engines: WinParse.rows(sec["GPUENG"]),
                             memory: WinParse.rows(sec["GPUMEM"]), nvidia: nvidiaInfo, sensors: sensors)
        gpuPercent = gpus.compactMap(\.util).max()
        for g in gpus {
            push(&gpuHistory[g.id, default: GPUHistory()].util, g.util)
            push(&gpuHistory[g.id, default: GPUHistory()].vram, g.vramFraction.map { $0 * 100 })
        }

        state = .connected
        lastRefresh = .now
        return true
    }

    override func closeBackend() async { await runner.close() }

    override func refreshServices() async { _ = await poll() }

    /// LibreHardwareMonitor/OpenHardwareMonitor's "Remote Web Server" JSON tree — more reliable than WMI,
    /// since the WMI provider only registers when the app is launched elevated and that namespace isn't
    /// always visible to a process running in a different session (such as one started over SSH).
    private func fetchLHMSensorsOverHTTP() async -> [HWSensor]? {
        guard machine.lhmPort > 0, let url = URL(string: "http://\(machine.host):\(machine.lhmPort)/data.json") else { return nil }
        do {
            var req = URLRequest(url: url)
            req.timeoutInterval = 5
            let (data, response) = try await URLSession.shared.data(for: req)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            let parsed = WinParse.lhmSensors(data)
            return parsed.isEmpty ? nil : parsed
        } catch { return nil }
    }

    func refreshGPUDetail() async {
        guard let t = try? await runner.ps(WinScripts.gpuDetail, ssh) else { return }
        let sec = WinParse.sections(t)
        nvidiaInfo = WinParse.nvidia(sec["NVSMI"])
        gpuProcesses = WinParse.gpuProcesses(WinParse.rows(sec["GPUPROC"]))
        for i in gpus.indices where gpus[i].name.localizedCaseInsensitiveContains("nvidia") {
            let idx = gpus.indices.filter { gpus[$0].name.localizedCaseInsensitiveContains("nvidia") }.firstIndex(of: i) ?? 0
            guard idx < nvidiaInfo.count else { continue }
            gpus[i].nvidia = nvidiaInfo[idx]
            if let tt = nvidiaInfo[idx].memTotal { gpus[i].vramTotal = tt }
            if let u = nvidiaInfo[idx].memUsed { gpus[i].vramUsed = u }
        }
    }

    override func refreshProcesses() async {
        guard let t = try? await runner.ps(WinScripts.processes, ssh) else { return }
        let n = Double(max(1, WinParse.rows(WinParse.sections(t)["CORES"]).count))
        processes = WinParse.rows(WinParse.sections(t)["PROCS"]).compactMap { r in
            guard let pid = r.num("pid"), let name = r.str("name") else { return nil }
            return ProcessRow(pid: Int(pid), name: name, cpu: min(100, (r.num("cpu") ?? 0) / max(n, 1)), mem: r.num("ws") ?? 0)
        }
    }

    override func serviceAction(_ s: ServiceRow, verb: String) async {
        let cmd: String = switch verb {
        case "start": "Start-Service -Name \(SSHRunner.pq(s.name))"
        case "stop": "Stop-Service -Name \(SSHRunner.pq(s.name)) -Force"
        default: "Restart-Service -Name \(SSHRunner.pq(s.name)) -Force"
        }
        let out = try? await runner.ps(WinScripts.action(cmd), ssh)
        performAction(out ?? "", successContains: "OK")
        try? await Task.sleep(for: .seconds(1))
        _ = await poll()
    }

    override func killProcess(_ p: ProcessRow) async {
        _ = try? await runner.ps(WinScripts.action("Stop-Process -Id \(p.pid) -Force"), ssh)
        await refreshProcesses()
    }

    override func reboot() async { _ = try? await runner.ps(WinScripts.action("shutdown.exe /r /t 3 /f"), ssh) }
    override func shutdown() async { _ = try? await runner.ps(WinScripts.action("shutdown.exe /s /t 3 /f"), ssh) }

    // MARK: Startup / Scheduled Tasks / Software / Updates / Event Log / Users / Console

    func refreshStartup() async {
        guard let t = try? await runner.ps(WinScripts.startup, ssh) else { return }
        startupItems = WinParse.startup(WinParse.rows(WinParse.sections(t)["STARTUP"]))
            .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        startupLoaded = true
    }

    func setStartup(_ item: StartupItem, disabled: Bool) async {
        _ = try? await runner.ps(WinScripts.setStartup(item, disabled: disabled), ssh)
        await refreshStartup()
    }

    func refreshScheduledTasks() async {
        guard let t = try? await runner.ps(WinScripts.scheduledTasks, ssh) else { return }
        scheduledTasks = WinParse.rows(WinParse.sections(t)["TASKS"]).compactMap { r in
            guard let name = r.str("name") else { return nil }
            return WinScheduledTask(name: name, path: r.str("path") ?? "", state: r.str("state") ?? "")
        }.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    func scheduledTaskAction(_ t: WinScheduledTask, verb: String) async {
        let cmd: String = switch verb {
        case "run": "Start-ScheduledTask -TaskName \(SSHRunner.pq(t.name)) -TaskPath \(SSHRunner.pq(t.path))"
        case "enable": "Enable-ScheduledTask -TaskName \(SSHRunner.pq(t.name)) -TaskPath \(SSHRunner.pq(t.path)) | Out-Null"
        default: "Disable-ScheduledTask -TaskName \(SSHRunner.pq(t.name)) -TaskPath \(SSHRunner.pq(t.path)) | Out-Null"
        }
        _ = try? await runner.ps(WinScripts.action(cmd), ssh)
        await refreshScheduledTasks()
    }

    func refreshSoftware() async {
        guard let t = try? await runner.ps(WinScripts.software, ssh) else { return }
        software = WinParse.rows(WinParse.sections(t)["SOFT"]).compactMap { r in
            guard let name = r.str("name"), !name.isEmpty else { return nil }
            return InstalledSoftware(name: name, version: r.str("version") ?? "", publisher: r.str("publisher") ?? "")
        }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        softwareLoaded = true
    }

    func refreshWindowsUpdates() async {
        guard let t = try? await runner.ps(WinScripts.updates, ssh) else { return }
        let sec = WinParse.sections(t)
        hotfixCount = WinParse.rows(sec["HOTFIX"]).count
        hotfixLastInstalled = WinParse.rows(sec["HOTFIX"]).compactMap { $0.str("date") }.max()
        wuauservRunning = WinParse.rows(sec["WU"]).first?.str("state") == "Running"
    }

    func refreshEventLog(filter: String) async {
        guard let t = try? await runner.ps(WinScripts.eventLog(unit: filter), ssh) else { return }
        eventLog = WinParse.rows(WinParse.sections(t)["EVT"]).compactMap { r in
            guard let msg = r.str("msg") else { return nil }
            return EventLogEntry(time: r.str("time") ?? "", level: r.str("level") ?? "", log: r.str("log") ?? "", message: msg)
        }
    }

    func refreshUsers() async {
        guard let t = try? await runner.ps(WinScripts.users, ssh) else { return }
        users = WinParse.quser(WinParse.sections(t)["USR"] ?? "")
    }

    func runConsole(_ command: String) async -> String {
        (try? await runner.ps(WinScripts.action(command), ssh)) ?? "Command failed."
    }

    // MARK: WireGuard
    // The official Windows client installs each tunnel as its own service named
    // "WireGuardTunnel$<name>", managed via wireguard.exe, and bundles the same wg.exe used on
    // Linux/macOS for live status — so the dump format and parser are identical across platforms.

    func refreshWireGuard() async {
        guard let t = try? await runner.ps(WinScripts.wireguardStatus, ssh) else { return }
        let sec = WinParse.sections(t)
        let names = WinParse.rows(sec["WGSVC"]).compactMap { r -> String? in
            guard let name = r.str("Name"), name.hasPrefix("WireGuardTunnel$") else { return nil }
            return String(name.dropFirst("WireGuardTunnel$".count))
        }
        wireguardTunnels = WireGuardParser.tunnels(confNames: names, dump: sec["WGDUMP"] ?? "")
        wireguardError = nil
    }

    func addWireGuardTunnel(name: String, config: String) async -> String? {
        guard WireGuardParser.isValidName(name) else { return "Use letters, numbers, dashes and underscores only." }
        let b64 = Data(config.utf8).base64EncodedString()
        let script = """
        $bytes=[Convert]::FromBase64String(\(SSHRunner.pq(b64)))
        $path="$env:TEMP\\\(name).conf"
        [IO.File]::WriteAllBytes($path,$bytes)
        $wg=(Get-Command wireguard.exe -ErrorAction SilentlyContinue).Source
        if(!$wg){$wg='C:\\Program Files\\WireGuard\\wireguard.exe'}
        & $wg /installtunnelservice $path
        Remove-Item $path -ErrorAction SilentlyContinue
        """
        let out = try? await runner.ps(WinScripts.action(script), ssh)
        await refreshWireGuard()
        guard let out else { return "Failed to install the tunnel." }
        return performAction(out, successContains: "OK") ? nil : actionError
    }

    func connectWireGuardTunnel(_ name: String) async {
        guard WireGuardParser.isValidName(name) else { return }
        _ = try? await runner.ps(WinScripts.action("Start-Service -Name \(SSHRunner.pq("WireGuardTunnel$\(name)"))"), ssh)
        await refreshWireGuard()
    }

    func disconnectWireGuardTunnel(_ name: String) async {
        guard WireGuardParser.isValidName(name) else { return }
        _ = try? await runner.ps(WinScripts.action("Stop-Service -Name \(SSHRunner.pq("WireGuardTunnel$\(name)")) -Force"), ssh)
        await refreshWireGuard()
    }

    func removeWireGuardTunnel(_ name: String) async {
        guard WireGuardParser.isValidName(name) else { return }
        let script = """
        $wg=(Get-Command wireguard.exe -ErrorAction SilentlyContinue).Source
        if(!$wg){$wg='C:\\Program Files\\WireGuard\\wireguard.exe'}
        & $wg /uninstalltunnelservice \(SSHRunner.pq(name))
        """
        _ = try? await runner.ps(WinScripts.action(script), ssh)
        await refreshWireGuard()
    }
}

// MARK: - Remote script + parser (PowerShell 5.1 compatible)

private enum WinScripts {
    static let prelude = #"""
    $ErrorActionPreference='SilentlyContinue'
    function Sx($n,$o){'##'+$n;$j=ConvertTo-Json -InputObject @($o) -Compress -Depth 3;[regex]::Replace($j,'[^\x00-\x7F]',{param($m) '\u{0:x4}' -f [int][char]$m.Value})}
    function Pf($c){Get-CimInstance ('Win32_PerfFormattedData_'+$c)}
    """#

    /// GPU engine utilisation (busiest engine per type, per adapter LUID) and adapter memory.
    static let gpuCounters = #"""
    $g=@{}
    foreach($e in (Pf 'GPUPerformanceCounters_GPUEngine')){if($e.UtilizationPercentage -gt 0 -and $e.Name -match 'luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_\d+_eng_(\d+)_engtype_(.+)$'){$g[$matches[1]+'|'+$matches[3]+'|'+$matches[2]]+=$e.UtilizationPercentage}}
    $t=@{}
    foreach($k in $g.Keys){$p=$k.Split('|');$id=$p[0]+'|'+$p[1];$v=[math]::Min(100,$g[$k]);if($v -gt $t[$id]){$t[$id]=$v}}
    Sx GPUENG ($t.Keys|%{$p=$_.Split('|');[pscustomobject]@{luid=$p[0];type=$p[1];util=$t[$_]}})
    Sx GPUMEM ((Pf 'GPUPerformanceCounters_GPUAdapterMemory')|%{if($_.Name -match 'luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_(\d+)'){[pscustomobject]@{luid=$matches[1];phys=[int]$matches[2];ded=$_.DedicatedUsage}}})
    """#

    /// Adapter details. VRAM comes from the registry because Win32_VideoController caps it at 4 GB.
    static let gpuStatic = #"""
    $vr=@{}
    Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*'|%{$q=$_.'HardwareInformation.qwMemorySize';if($q -is [byte[]]){$q=[BitConverter]::ToUInt64($q,0)};if($q){$vr[[string]$_.DriverDesc]=[double]$q}}
    Sx GPUS (Get-CimInstance Win32_VideoController|%{[pscustomobject]@{name=$_.Name;ram=[double]$_.AdapterRAM;vram=$vr[[string]$_.Name];driver=$_.DriverVersion;date=$(if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')});status=$_.Status;proc=$_.VideoProcessor}})
    """#

    static let sample = prelude + "\n" + #"""
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
    """# + "\n" + gpuStatic + "\n" + gpuCounters + "\n" + #"""
    '##END'
    """#

    /// nvidia-smi (when installed) and per-process GPU usage; only run while the GPU page is open.
    static let gpuDetail = prelude + "\n" + #"""
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
    """#

    static let processes = prelude + "\n" + #"""
    Sx CORES (Pf 'Counters_ProcessorInformation'|?{$_.Name -ne '_Total'}|select Name)
    Sx PROCS (Pf 'PerfProc_Process'|?{$_.Name -ne '_Total' -and $_.Name -ne 'Idle'}|sort PercentProcessorTime -desc|%{[pscustomobject]@{pid=$_.IDProcess;name=($_.Name -replace '#\d+$','');cpu=$_.PercentProcessorTime;ws=$_.WorkingSetPrivate}})
    '##END'
    """#

    static func action(_ body: String) -> String {
        #"""
        $ErrorActionPreference='Stop'
        try{
        """# + "\n" + body + "\n" + #"""
        'OK'}catch{'ERROR: '+$_.Exception.Message}
        """#
    }

    /// Everything that runs at sign-in: Run keys, Startup folders and logon/boot scheduled tasks, with their enabled state.
    static let startup = prelude + "\n" + #"""
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
    """#

    static func setStartup(_ item: StartupItem, disabled: Bool) -> String {
        let ap = #"\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\"#
        let name = SSHRunner.pq(item.name)
        if item.kind == .task {
            let verb = disabled ? "Disable-ScheduledTask" : "Enable-ScheduledTask"
            return action("\(verb) -TaskName \(name) -TaskPath \(SSHRunner.pq(item.taskPath)) | Out-Null")
        }
        let key: String = switch item.kind {
        case .hklm: "HKLM:" + ap + "Run"
        case .hklm32: "HKLM:" + ap + "Run32"
        case .hkcu: "HKCU:" + ap + "Run"
        case .folderUser: "HKCU:" + ap + "StartupFolder"
        case .folderAll: "HKLM:" + ap + "StartupFolder"
        case .task: ""
        }
        return action("""
        $k=\(SSHRunner.pq(key))
        if(!(Test-Path -LiteralPath $k)){New-Item -Path $k -Force|Out-Null}
        $v=New-Object byte[] 12
        if(\(disabled ? "$true" : "$false")){$v[0]=3;[BitConverter]::GetBytes([DateTime]::UtcNow.ToFileTime()).CopyTo($v,4)}else{$v[0]=2}
        Set-ItemProperty -LiteralPath $k -Name \(name) -Value $v -Type Binary
        """)
    }

    static let scheduledTasks = prelude + "\n" + #"""
    Sx TASKS (Get-ScheduledTask -ErrorAction SilentlyContinue|?{$_.TaskPath -notlike '\Microsoft\*'}|select @{n='name';e={$_.TaskName}},@{n='path';e={$_.TaskPath}},@{n='state';e={[string]$_.State}})
    '##END'
    """#

    static let software = prelude + "\n" + #"""
    $keys='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    Sx SOFT (Get-ItemProperty $keys -ErrorAction SilentlyContinue|?{$_.DisplayName -and -not $_.SystemComponent}|select @{n='name';e={$_.DisplayName}},@{n='version';e={[string]$_.DisplayVersion}},@{n='publisher';e={$_.Publisher}} -Unique)
    '##END'
    """#

    static let updates = prelude + "\n" + #"""
    Sx HOTFIX (Get-HotFix -ErrorAction SilentlyContinue|select HotFixID,@{n='date';e={if($_.InstalledOn){$_.InstalledOn.ToString('yyyy-MM-dd')}}})
    Sx WU ([pscustomobject]@{state=(Get-Service wuauserv -ErrorAction SilentlyContinue).Status.ToString()})
    '##END'
    """#

    static func eventLog(unit: String) -> String {
        let filter = unit.trimmingCharacters(in: .whitespaces)
        let logNames = filter.isEmpty ? "'Application','System'" : SSHRunner.pq(filter)
        return prelude + "\n" + """
        Sx EVT (Get-WinEvent -FilterHashtable @{LogName=\(logNames);Level=1,2,3} -MaxEvents 200 -ErrorAction SilentlyContinue|select @{n='time';e={$_.TimeCreated.ToString('MM-dd HH:mm:ss')}},@{n='level';e={$_.LevelDisplayName}},@{n='log';e={$_.LogName}},@{n='msg';e={($_.Message -split "`n")[0]}})
        '##END'
        """
    }

    static let users = prelude + "\n" + #"""
    Sx USR (quser 2>$null | Select-Object -Skip 1 | %{$l=$_.Trim() -replace '\s{2,}',',';$f=$l.Split(',');[pscustomobject]@{name=$f[0];session=$(if($f.Count -gt 1){$f[1]}else{''});state=$(if($f.Count -gt 3){$f[3]}else{''});idle=$(if($f.Count -gt 4){$f[4]}else{''});logon=$(if($f.Count -gt 5){$f[5]}else{''})})
    '##END'
    """#

    static let wireguardStatus = prelude + "\n" + #"""
    Sx WGSVC (Get-Service -Name 'WireGuardTunnel$*' -ErrorAction SilentlyContinue|select Name,Status)
    '##WGDUMP'
    $wg=(Get-Command wg.exe -ErrorAction SilentlyContinue).Source
    if(!$wg){$wg='C:\Program Files\WireGuard\wg.exe'}
    if(Test-Path $wg){& $wg show all dump 2>$null}
    '##END'
    """#
}

private typealias WinRow = [String: Any]

private extension Dictionary where Key == String, Value == Any {
    func num(_ k: String) -> Double? {
        if let n = self[k] as? NSNumber { return n.doubleValue }
        if let s = self[k] as? String { return Double(s) }
        return nil
    }
    func str(_ k: String) -> String? {
        if let s = self[k] as? String { return s }
        if let n = self[k] as? NSNumber { return n.stringValue }
        return nil
    }
    func flag(_ k: String) -> Bool? { (self[k] as? NSNumber)?.boolValue }
}

private enum WinParse {
    static func sections(_ text: String) -> [String: String] {
        var out: [String: [String]] = [:]
        var current: String?
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = String(raw)
            if line.hasPrefix("##") { current = String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces); out[current!] = []; continue }
            if let c = current { out[c, default: []].append(line) }
        }
        return out.mapValues { $0.joined(separator: "\n") }
    }

    /// Physical-looking adapters only; Windows counters list every filter driver/tunnel as an adapter too.
    static func network(_ rows: [WinRow]) -> (rx: Double, tx: Double) {
        let skip = ["isatap", "teredo", "loopback", "6to4", "vethernet", "-wfp", "-qos", "-virtualbox", "-npcap", "filter", "pseudo", "bluetooth", "wan miniport", "tailscale"]
        var rx = 0.0, tx = 0.0
        for r in rows {
            let n = (r.str("Name") ?? "").lowercased()
            if skip.contains(where: { n.contains($0) }) { continue }
            rx += r.num("BytesReceivedPersec") ?? 0
            tx += r.num("BytesSentPersec") ?? 0
        }
        return (rx, tx)
    }

    static func rows(_ text: String?) -> [WinRow] {
        guard let text, let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) else { return [] }
        if let arr = obj as? [Any] { return arr.compactMap { $0 as? WinRow } }
        if let one = obj as? WinRow { return [one] }
        return []
    }

    static func sensors(_ rows: [WinRow]) -> [HWSensor] {
        rows.compactMap { r in
            guard let name = r.str("Name"), let type = r.str("SensorType"), let v = r.num("Value"), let id = r.str("Identifier") else { return nil }
            return HWSensor(name: name, type: type, value: v, identifier: id)
        }
    }

    /// LibreHardwareMonitor's `data.json`: a tree of {Text, Children} nodes where leaves additionally carry
    /// {Value, SensorId}. SensorId looks like "/amdcpu/0/temperature/2" — the segment before the trailing
    /// index is the sensor type, the same vocabulary the WMI path uses.
    private struct LHMNode: Decodable { var Text: String?; var Value: String?; var SensorId: String?; var Children: [LHMNode]? }

    static func lhmSensors(_ data: Data) -> [HWSensor] {
        guard let root = try? JSONDecoder().decode(LHMNode.self, from: data) else { return [] }
        var out: [HWSensor] = []
        func typeName(_ segment: String) -> String? {
            switch segment.lowercased() {
            case "temperature": "Temperature"
            case "fan": "Fan"
            case "power": "Power"
            case "voltage": "Voltage"
            case "clock": "Clock"
            case "load": "Load"
            case "control": "Control"
            case "data": "Data"
            default: nil
            }
        }
        func walk(_ n: LHMNode) {
            if let id = n.SensorId, !id.isEmpty {
                let parts = id.split(separator: "/").map(String.init)
                if parts.count >= 2, let type = typeName(parts[parts.count - 2]),
                   let v = (n.Value ?? "").split(separator: " ").first.flatMap({ Double($0.replacingOccurrences(of: ",", with: ".")) }), v.isFinite {
                    out.append(HWSensor(name: n.Text ?? id, type: type, value: v, identifier: id))
                }
            }
            for c in n.Children ?? [] { walk(c) }
        }
        walk(root)
        return out
    }

    static func nvidia(_ text: String?) -> [NVInfo] {
        guard let text else { return [] }
        func v(_ s: String) -> Double? { Double(s.trimmingCharacters(in: .whitespaces)) }
        return text.split(whereSeparator: \.isNewline).compactMap { raw in
            let f = raw.components(separatedBy: ", ")
            guard f.count >= 16 else { return nil }
            return NVInfo(name: f[1].trimmingCharacters(in: .whitespaces), util: v(f[2]), memUtil: v(f[3]),
                          memUsed: v(f[4]).map { $0 * 1_048_576 }, memTotal: v(f[5]).map { $0 * 1_048_576 },
                          temp: v(f[6]), power: v(f[7]), powerLimit: v(f[8]), fan: v(f[9]),
                          clockGr: v(f[10]), clockMaxGr: v(f[11]), clockMem: v(f[12]), clockMaxMem: v(f[13]),
                          driver: f[14].trimmingCharacters(in: .whitespaces), pstate: f[15].trimmingCharacters(in: .whitespaces))
        }
    }

    /// Builds the adapter list from the static controller info, the live counters and (optionally) nvidia-smi/sensors.
    static func gpus(controllers: [WinRow], engines: [WinRow], memory: [WinRow], nvidia: [NVInfo], sensors: [HWSensor]) -> [GPUAdapter] {
        var adapters: [GPUAdapter] = controllers.enumerated().compactMap { i, r in
            guard let name = r.str("name") else { return nil }
            var g = GPUAdapter(id: "\(i)|\(name)", name: name)
            g.driver = r.str("driver") ?? ""
            g.driverDate = r.str("date") ?? ""
            let reg = r.num("vram") ?? 0, wmi = r.num("ram") ?? 0
            g.vramTotal = reg > 0 ? reg : (wmi > 0 ? wmi : nil)
            g.status = r.str("status") ?? ""
            g.processor = r.str("proc") ?? ""
            return g
        }
        var real = adapters.indices.filter { !adapters[$0].isVirtual }
        if real.isEmpty { real = Array(adapters.indices) }

        var luids: [String] = []
        for m in memory.sorted(by: { ($0.num("phys") ?? 0, $0.str("luid") ?? "") < ($1.num("phys") ?? 0, $1.str("luid") ?? "") }) {
            if let l = m.str("luid"), !luids.contains(l) { luids.append(l) }
        }
        for l in engines.compactMap({ $0.str("luid") }) where !luids.contains(l) { luids.append(l) }
        for (n, idx) in real.enumerated() where n < luids.count {
            let luid = luids[n]
            adapters[idx].luid = luid
            for e in engines where e.str("luid") == luid {
                if let t = e.str("type"), let u = e.num("util") { adapters[idx].engines[t] = u }
            }
            let segments = memory.filter { $0.str("luid") == luid }
            if !segments.isEmpty { adapters[idx].vramUsed = segments.reduce(0.0) { $0 + ($1.num("ded") ?? 0) } }
        }

        let nvIdx = real.filter { adapters[$0].name.localizedCaseInsensitiveContains("nvidia") }
        for (n, idx) in nvIdx.enumerated() {
            let match = nvidia.first { $0.name.caseInsensitiveCompare(adapters[idx].name) == .orderedSame && nvIdx.count == 1 }
                ?? (n < nvidia.count ? nvidia[n] : nil)
            guard let nv = match else { continue }
            adapters[idx].nvidia = nv
            if let tt = nv.memTotal { adapters[idx].vramTotal = tt }
            if let u = nv.memUsed { adapters[idx].vramUsed = u }
        }

        var groups: [String: [HWSensor]] = [:]
        for s in sensors {
            let p = s.identifier.split(separator: "/")
            if p.count >= 2, p[0].hasPrefix("gpu-") { groups["\(p[0])/\(p[1])", default: []].append(s) }
        }
        var used = Set<String>()
        for idx in real {
            let n = adapters[idx].name.lowercased()
            let vendor = n.contains("nvidia") ? "gpu-nvidia" : (n.contains("amd") || n.contains("radeon") ? "gpu-amd" : (n.contains("intel") ? "gpu-intel" : ""))
            guard let key = groups.keys.sorted().first(where: { $0.hasPrefix(vendor) && !used.contains($0) }), !vendor.isEmpty else { continue }
            used.insert(key)
            adapters[idx].sensors = groups[key] ?? []
        }
        return adapters.enumerated().filter { real.contains($0.offset) }.map(\.element)
    }

    static func gpuProcesses(_ rows: [WinRow]) -> [GPUProcess] {
        rows.compactMap { r in
            guard let pid = r.num("pid") else { return nil }
            return GPUProcess(pid: Int(pid), name: r.str("name") ?? "pid \(Int(pid))", util: r.num("util") ?? 0, vram: r.num("vram") ?? 0)
        }
    }

    static func startup(_ rows: [WinRow]) -> [StartupItem] {
        rows.compactMap { r in
            guard let k = r.str("kind"), let kind = StartupItem.Kind(rawValue: k), let name = r.str("name") else { return nil }
            return StartupItem(kind: kind, name: name, command: r.str("cmd") ?? "", publisher: r.str("pub") ?? "",
                               scope: r.str("scope") ?? "", disabled: r.flag("disabled") ?? false, taskPath: r.str("path") ?? "")
        }
    }

    static func quser(_ text: String) -> [LoggedInUser] {
        guard let data = text.data(using: .utf8), let obj = try? JSONSerialization.jsonObject(with: data) else { return [] }
        let rows: [WinRow] = (obj as? [Any])?.compactMap { $0 as? WinRow } ?? ((obj as? WinRow).map { [$0] } ?? [])
        return rows.compactMap { r in
            guard let name = r.str("name"), !name.isEmpty else { return nil }
            return LoggedInUser(id: name + (r.str("session") ?? ""), name: name, terminal: r.str("session") ?? "",
                                host: "", loginTime: r.str("logon") ?? "", idle: r.str("idle") ?? "")
        }
    }
}
