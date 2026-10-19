import Foundation

// MARK: - TrueNAS API models (decoded with .convertFromSnakeCase)

private struct TNSystemInfo: Decodable, Sendable {
    var version: String
    var hostname: String
    var uptimeSeconds: Double?
    var physmem: Int64?
    var cores: Int?
    var loadavg: [Double]?
}

private struct TNPool: Decodable, Sendable, Identifiable {
    struct Scan: Decodable, Sendable { var function: String?; var state: String?; var percentage: Double?; var errors: Int? }
    var id: Int
    var name: String
    var status: String
    var healthy: Bool
    var size: Int64?
    var allocated: Int64?
    var scan: Scan?
    var scrubSummary: String {
        guard let scan, let fn = scan.function, let state = scan.state else { return "" }
        if state == "SCANNING" { return "\(fn.capitalized) \(Int(scan.percentage ?? 0))%" }
        return "\(fn.capitalized) \(state.lowercased())" + ((scan.errors ?? 0) > 0 ? " · \(scan.errors!) errors" : "")
    }
}

private struct TNService: Decodable, Sendable, Identifiable {
    var id: Int
    var service: String
    var state: String
    var isRunning: Bool { state == "RUNNING" }
}

private struct TNAlert: Decodable, Sendable, Identifiable {
    var uuid: String
    var level: String
    var formatted: String?
    var dismissed: Bool?
    var id: String { uuid }
    static let levels = ["INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    var severity: Int { Self.levels.firstIndex(of: level) ?? 0 }
    var asAlertItem: AlertItem {
        AlertItem(id: uuid, level: severity >= 4 ? .critical : (severity >= 2 ? .warning : .info),
                  title: formatted ?? "Alert", detail: level)
    }
}

struct TNApp: Decodable, Sendable, Identifiable {
    var name: String
    var state: String
    var upgradeAvailable: Bool?
    var humanVersion: String?
    var id: String { name }
    var isRunning: Bool { state == "RUNNING" }
}

struct TNVM: Decodable, Sendable, Identifiable {
    struct Status: Decodable, Sendable {
        var state: String
        var pid: Int?
        init(state: String, pid: Int? = nil) { self.state = state; self.pid = pid }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: Keys.self)
            state = (try? c.decode(String.self, forKey: .state)) ?? "UNKNOWN"
            pid = try? c.decode(Int.self, forKey: .pid)
        }
        enum Keys: String, CodingKey { case state, pid }
    }
    struct Device: Sendable { var dtype: String; var path: String? }
    var id: Int
    var name: String
    var vcpus: Int?
    var cores: Int?
    var threads: Int?
    var memory: Int64?      // MiB
    var autostart: Bool?
    var status: Status
    var devices: [Device] = []
    var isRunning: Bool { status.state == "RUNNING" }
    var totalCPUs: Int { (vcpus ?? 1) * (cores ?? 1) * (threads ?? 1) }

    enum Keys: String, CodingKey { case id, name, vcpus, cores, threads, memory, autostart, status, devices }
    private struct RawDevice: Decodable { struct Attrs: Decodable { var path: String? }; var dtype: String?; var attributes: Attrs? }
    private struct FailableBox<T: Decodable>: Decodable { let value: T?; init(from decoder: Decoder) throws { value = try? T(from: decoder) } }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        status = (try? c.decode(Status.self, forKey: .status)) ?? Status(state: "UNKNOWN")
        vcpus = try? c.decode(Int.self, forKey: .vcpus)
        cores = try? c.decode(Int.self, forKey: .cores)
        threads = try? c.decode(Int.self, forKey: .threads)
        memory = try? c.decode(Int64.self, forKey: .memory)
        autostart = try? c.decode(Bool.self, forKey: .autostart)
        if let raw = try? c.decode([FailableBox<RawDevice>].self, forKey: .devices) {
            devices = raw.compactMap(\.value).map { Device(dtype: $0.dtype ?? "", path: $0.attributes?.path) }
        }
    }
}

// MARK: - VM live stats (sampled over SSH, same technique as the old TrueNAS/Debian apps)

struct TNProcSample: Sendable {
    struct Net: Sendable { var name: String; var rx: Double; var tx: Double }
    var cpuTicks: Double?
    var rssKiB: Double?
    var readBytes: Double?
    var writeBytes: Double?
    var nets: [Net] = []
    var timestamp = Date()
}

enum TNProcParser {
    static let ticksPerSecond = 100.0
    static func parse(_ text: String) -> [Int: TNProcSample] {
        var result: [Int: TNProcSample] = [:]
        var pid: Int?
        var cur = TNProcSample()
        func flush() { if let pid { result[pid] = cur } }
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = String(raw)
            if line.hasPrefix("PID ") {
                flush(); pid = Int(line.dropFirst(4).trimmingCharacters(in: .whitespaces)); cur = TNProcSample()
            } else if line.hasPrefix("STAT "), let close = line.lastIndex(of: ")") {
                let f = line[line.index(after: close)...].split(separator: " ")
                if f.count > 12, let u = Double(f[11]), let s = Double(f[12]) { cur.cpuTicks = u + s }
            } else {
                let t = line.split(separator: " ")
                switch t.first {
                case "RSS": cur.rssKiB = t.count > 1 ? Double(t[1]) : nil
                case "IO" where t.count > 2:
                    if t[1] == "read_bytes:" { cur.readBytes = Double(t[2]) }
                    if t[1] == "write_bytes:" { cur.writeBytes = Double(t[2]) }
                case "NET" where t.count > 3:
                    cur.nets.append(.init(name: String(t[1]), rx: Double(t[2]) ?? 0, tx: Double(t[3]) ?? 0))
                default: break
                }
            }
        }
        flush()
        return result
    }
}

struct TNVMLive: Sendable {
    struct DiskUse: Identifiable, Sendable { var name: String; var capacity: Double; var allocation: Double; var id: String { name } }
    static let historyLength = 60
    var vcpus = 1
    var cpu: Double?
    var memUsed: Double?
    var memTotal: Double?
    var rxRate: Double?
    var txRate: Double?
    var diskReadRate: Double?
    var diskWriteRate: Double?
    var disks: [DiskUse] = []
    var nics: [String] = []
    var cpuHistory: [Double] = []
    var memHistory: [Double] = []
    var rxHistory: [Double] = []
    var txHistory: [Double] = []
    var readHistory: [Double] = []
    var writeHistory: [Double] = []

    mutating func update(from now: TNProcSample, previous: TNProcSample?, vm: TNVM) {
        vcpus = max(vm.totalCPUs, 1)
        nics = now.nets.map(\.name)
        memTotal = vm.memory.map { Double($0) * 1_048_576 }
        memUsed = now.rssKiB.map { $0 * 1024 }
        if let prev = previous {
            let dt = now.timestamp.timeIntervalSince(prev.timestamp)
            if dt > 0.5 {
                if let c1 = now.cpuTicks, let c0 = prev.cpuTicks {
                    cpu = min(100, max(0, (c1 - c0) / TNProcParser.ticksPerSecond / (dt * Double(vcpus)) * 100))
                }
                func rate(_ a: Double?, _ b: Double?) -> Double? { guard let a, let b else { return nil }; return max(0, a - b) / dt }
                txRate = rate(now.nets.reduce(0) { $0 + $1.rx }, prev.nets.reduce(0) { $0 + $1.rx })
                rxRate = rate(now.nets.reduce(0) { $0 + $1.tx }, prev.nets.reduce(0) { $0 + $1.tx })
                diskReadRate = rate(now.readBytes, prev.readBytes)
                diskWriteRate = rate(now.writeBytes, prev.writeBytes)
            }
        }
        func push(_ arr: inout [Double], _ v: Double?) {
            guard let v else { return }
            arr.append(v)
            if arr.count > Self.historyLength { arr.removeFirst(arr.count - Self.historyLength) }
        }
        push(&cpuHistory, cpu)
        push(&memHistory, memUsed.flatMap { u in memTotal.map { u / max($0, 1) * 100 } })
        push(&rxHistory, rxRate); push(&txHistory, txRate)
        push(&readHistory, diskReadRate); push(&writeHistory, diskWriteRate)
    }
}

// MARK: - Extras: datasets, shares, network, data protection, users (raw-JSON, tolerant of schema drift)

struct TNDataset: Sendable, Identifiable { var id: String; var name: String; var type: String; var used: Int64; var available: Int64; var mountpoint: String }
struct TNSnapshotRow: Sendable, Identifiable { var id: String; var name: String; var dataset: String; var used: Int64 }
struct TNShare: Sendable, Identifiable { var id: String; var name: String; var path: String; var comment: String; var enabled: Bool; var kind: String }
struct TNInterface: Sendable, Identifiable { var id: String; var name: String; var addresses: [String]; var linkState: String }
struct TNProtectionTask: Sendable, Identifiable { var id: String; var kind: String; var summary: String; var enabled: Bool }
struct TNUserAccount: Sendable, Identifiable { var id: Int; var username: String; var fullName: String; var locked: Bool; var uid: Int }

/// A VPN tunnel running as a plain Docker container (not a registered TrueNAS "App") — it's a thin
/// wrapper around linuxserver/wireguard that `wg-quick up`s whatever config you hand it, so the same
/// container also shows up on the regular Containers page for logs/restart/etc.
struct TNVPNContainer: Sendable, Identifiable {
    var name: String
    var containerName: String
    var running: Bool
    var tunnel: WireGuardTunnel?
    var id: String { name }
}

private extension Dictionary where Key == String, Value == Any {
    func tnNum(_ k: String) -> Double? {
        if let n = self[k] as? NSNumber { return n.doubleValue }
        if let d = self[k] as? [String: Any] { return (d["parsed"] as? NSNumber)?.doubleValue }
        return nil
    }
    func tnStr(_ k: String) -> String? {
        if let s = self[k] as? String { return s }
        if let d = self[k] as? [String: Any] { return d["parsed"] as? String }
        return nil
    }
    func tnBool(_ k: String) -> Bool? { (self[k] as? NSNumber)?.boolValue }
}

/// Manages a TrueNAS SCALE machine over JSON-RPC-over-WebSocket. SSH (optional) adds VM live stats,
/// Docker containers, and a console; the fan controller (optional) talks plain HTTP.
@MainActor @Observable
final class TrueNASSession: MachineSession, ConsoleHosting {
    private var client: TrueNASClient?
    private var cpuRT: Double?
    private var memRT: Double?
    private var rawAlerts: [TNAlert] = []
    private var activeAlerts: [TNAlert] { rawAlerts.filter { $0.dismissed != true } }

    var apps: [TNApp] = []
    var vms: [TNVM] = []
    var vmLive: [Int: TNVMLive] = [:]
    var vmStatsError: String?
    private let vmCollector = SSHRunner()
    private var rapl = RAPLSampler()
    private var lastPowerSample = Date.distantPast
    private var vmStatsTask: Task<Void, Never>?
    private var previousSamples: [Int: TNProcSample] = [:]
    private var vmDisks: [Int: [TNVMLive.DiskUse]] = [:]
    private var lastZvolFetch = Date.distantPast

    var containers: [DockerContainer] = []
    var containersLoaded = false
    var containersError: String?
    private let containerCollector = SSHRunner()
    private var containersTask: Task<Void, Never>?

    var fanStatus: FanStatus?
    var fanConfig: FanConfig?
    var fanError: String?
    var fanRPMHistory: [Double] = []
    var fanDutyHistory: [Double] = []
    var fanTempHistory: [Double] = []
    private var fanTask: Task<Void, Never>?

    var installStatus: String?
    var installRunning = false
    var installFailed = false

    var datasets: [TNDataset] = []
    var snapshots: [TNSnapshotRow] = []
    var shares: [TNShare] = []
    var interfaces: [TNInterface] = []
    var protectionTasks: [TNProtectionTask] = []
    var userAccounts: [TNUserAccount] = []
    var updateStatus: String?
    var updateVersion: String?

    var consoleIsAvailable: Bool { isSSHConfigured }
    var consoleUnavailableReason: String { "Enable SSH in this machine's Edit sheet to use the console." }
    private let consoleRunner = SSHRunner()

    var vpnContainers: [TNVPNContainer] = []
    var vpnError: String?
    private static let vpnPrefix = "ix-homelab-vpn-"

    override var navItems: [NavItem] {
        var items = Set(machine.kind.navItems)
        if isSSHConfigured { items.insert(.vpn) }
        return sortNavItems(items)
    }

    private var secret: String { MachineStore.secret(for: machine.id) }

    private var config: TrueNASConnectionConfig {
        TrueNASConnectionConfig(host: machine.host, port: machine.port, useTLS: machine.useTLS, allowSelfSigned: machine.allowSelfSigned,
                                auth: machine.usePassword ? .password(user: machine.user, password: secret) : .apiKey(secret))
    }
    private var sshSettings: SSHSettings {
        SSHSettings(host: machine.host, port: machine.sshPort, user: machine.sshUser, password: MachineStore.sshPassword(for: machine.id), useSudo: machine.sshSudo)
    }
    var isSSHConfigured: Bool { machine.sshEnabled && !machine.sshUser.isEmpty }

    override func poll() async -> Bool {
        if client == nil {
            state = .connecting
            let c = TrueNASClient()
            await c.setHandlers(
                onRealtime: { [weak self] rt in Task { @MainActor in self?.apply(rt) } },
                onClose: { [weak self] _ in Task { @MainActor in self?.client = nil; if self?.state == .connected { self?.state = .failed("Connection lost") } } })
            do { try await c.connect(config) }
            catch { state = .failed(error.localizedDescription); return false }
            client = c
        }
        return await refreshAll()
    }

    private func samplePower() async {
        guard let out = try? await vmCollector.sh(RAPLSampler.script, sshSettings, sudo: true, merge: true) else { return }
        if let w = rapl.watts(from: out) { powerWatts = w }
        else if Double(out.trimmingCharacters(in: .whitespacesAndNewlines)) == nil { powerWatts = nil }
    }

    private func apply(_ rt: TNRealtime) {
        if let c = rt.cpu { cpuRT = c; cpuPercent = c; push(&cpuHistory, c) }
        if let m = rt.memoryUsed {
            memRT = m
            if memTotal > 0 { memUsed = memTotal * m; push(&memHistory, m * 100) }
        }
    }

    private func refreshAll() async -> Bool {
        guard let c = client else { return false }
        async let i: TNSystemInfo? = try? await c.call("system.info")
        async let p: [TNPool]? = try? await c.call("pool.query")
        async let s: [TNService]? = try? await c.call("service.query")
        async let al: [TNAlert]? = try? await c.call("alert.list")
        async let a: [TNApp]? = try? await c.call("app.query")
        async let v: [TNVM]? = try? await c.call("vm.query")
        let (info, pools, services, alerts, apps, vms) = await (i, p, s, al, a, v)
        guard let info else { state = .failed("Connection lost"); client = nil; return false }

        hostName = info.hostname
        hostOS = "TrueNAS " + info.version
        hostUptime = info.uptimeSeconds ?? 0
        if cpuRT == nil, let load = info.loadavg?.first, let cores = info.cores, cores > 0 {
            cpuPercent = min(100, load / Double(cores) * 100)
        }
        if let total = info.physmem, total > 0 {
            memTotal = Double(total)
            if let m = memRT { memUsed = memTotal * m }
        }

        if let pools {
            storage = pools.map { p in
                StorageItem(id: p.name, title: p.name, subtitle: p.status, size: p.size ?? 0, used: p.allocated ?? 0,
                           healthy: p.healthy, statusText: p.scrubSummary)
            }
        }
        if let services {
            self.services = services.map { ServiceRow(id: "\($0.id)", name: $0.service, displayName: $0.service, state: $0.state, isRunning: $0.isRunning) }
                .sorted { $0.name < $1.name }
            servicesLoaded = true
        }
        if let apps { self.apps = apps.sorted { $0.name < $1.name } }
        if let vms { self.vms = vms.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending } }
        if let alerts { rawAlerts = alerts; extraAlerts = activeAlerts.map(\.asAlertItem) }

        if isSSHConfigured, Date.now.timeIntervalSince(lastPowerSample) > 10 {
            lastPowerSample = .now
            // Off the poll path: an unreachable SSH must not hold up the API refresh.
            Task { [weak self] in await self?.samplePower() }
        }

        state = .connected
        lastRefresh = .now
        return true
    }

    override func closeBackend() async {
        rapl.reset()
        let c = client
        client = nil
        await c?.disconnect()
        vmStatsTask?.cancel(); containersTask?.cancel(); fanTask?.cancel()
        await vmCollector.close(); await containerCollector.close(); await consoleRunner.close()
    }

    override func refreshServices() async { _ = await refreshAll() }

    override func serviceAction(_ s: ServiceRow, verb: String) async {
        guard let c = client else { return }
        do { try await c.callRaw("service.control", [verb, s.name, [String: Any]()]); actionError = nil }
        catch { actionError = error.localizedDescription }
        try? await Task.sleep(for: .seconds(1.5))
        _ = await refreshAll()
    }

    func scrub(_ item: StorageItem, action: String) async {
        guard let c = client else { return }
        do {
            do { try await c.callRaw("pool.scrub.scrub", [item.name, action]) }
            catch TrueNASError.rpc(let code, _) where code == -32601 {
                try await c.callRaw("pool.scrub", [item.id, action])
            }
            actionError = nil
        } catch { actionError = error.localizedDescription }
        try? await Task.sleep(for: .seconds(1.5))
        _ = await refreshAll()
    }

    func dismissAlert(_ a: AlertItem) async {
        guard let c = client else { return }
        do { try await c.callRaw("alert.dismiss", [a.id]); rawAlerts.removeAll { $0.id == a.id } }
        catch { actionError = error.localizedDescription }
    }

    override func reboot() async {
        guard let c = client else { return }
        try? await c.callRaw("system.reboot", ["Rebooted from HomeLab Management", [String: Any]()])
    }
    override func shutdown() async {
        guard let c = client else { return }
        try? await c.callRaw("system.shutdown", ["Shut down from HomeLab Management", [String: Any]()])
    }

    // MARK: Apps

    func appAction(_ a: TNApp, action: String) async {
        guard let c = client else { return }
        do { try await c.callRaw("app.\(action)", [a.name]); actionError = nil }
        catch { actionError = error.localizedDescription }
        try? await Task.sleep(for: .seconds(3))
        _ = await refreshAll()
    }

    func installCustomApp(name: String, yaml: String) async -> Bool {
        guard let c = client else { return false }
        installRunning = true; installFailed = false
        installStatus = "Submitting…"
        defer { installRunning = false }
        do {
            let params: [String: Any] = ["custom_app": true, "app_name": name, "custom_compose_config_string": yaml]
            let data = try await c.callRaw("app.create", [params])
            guard let jobID = (try? JSONSerialization.jsonObject(with: data, options: .fragmentsAllowed)) as? Int else {
                installStatus = "TrueNAS accepted the request but returned no job."; installFailed = true; return false
            }
            for _ in 0..<400 {
                try await Task.sleep(for: .seconds(1.5))
                let d = try await c.callRaw("core.get_jobs", [[["id", "=", jobID]]])
                guard let job = (try? JSONSerialization.jsonObject(with: d) as? [[String: Any]])?.first else { continue }
                let state = job["state"] as? String ?? ""
                if let desc = (job["progress"] as? [String: Any])?["description"] as? String, !desc.isEmpty { installStatus = desc }
                switch state {
                case "SUCCESS": installStatus = "Installed."; _ = await refreshAll(); return true
                case "FAILED", "ABORTED": installFailed = true; installStatus = (job["error"] as? String) ?? "Installation \(state.lowercased())."; return false
                default: continue
                }
            }
            installFailed = true; installStatus = "Timed out waiting for TrueNAS. Check the Apps page."
            return false
        } catch { installFailed = true; installStatus = error.localizedDescription; return false }
    }

    // MARK: VMs

    func vmAction(_ v: TNVM, action: String) async {
        guard let c = client else { return }
        do {
            switch action {
            case "stop": try await c.callRaw("vm.stop", [v.id, ["force": false]])
            case "poweroff": try await c.callRaw("vm.poweroff", [v.id])
            default: try await c.callRaw("vm.\(action)", [v.id])
            }
            actionError = nil
        } catch { actionError = error.localizedDescription }
        try? await Task.sleep(for: .seconds(action == "poweroff" ? 2 : 3))
        _ = await refreshAll()
    }

    func setVMStatsActive(_ active: Bool) {
        vmStatsTask?.cancel(); vmStatsTask = nil
        guard active, isSSHConfigured else {
            if !active { Task { await vmCollector.close() } }
            return
        }
        vmStatsTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.sampleVMs()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    private func sampleVMs() async {
        if Date().timeIntervalSince(lastZvolFetch) > 60 { await refreshVMDisks() }
        let running = vms.filter { $0.isRunning && $0.status.pid != nil }
        guard !running.isEmpty else { vmStatsError = nil; return }
        let pids = running.compactMap(\.status.pid)
        let script = """
        for p in \(pids.map(String.init).joined(separator: " ")); do
          [ -d /proc/$p ] || continue
          echo "PID $p"
          echo "STAT $(cat /proc/$p/stat)"
          awk '/^VmRSS/{print "RSS",$2}' /proc/$p/status
          awk '/^(read_bytes|write_bytes)/{print "IO",$1,$2}' /proc/$p/io
          for i in $(cat /proc/$p/fdinfo/* 2>/dev/null | awk '/^iff:/{print $2}' | sort -u); do
            echo "NET $i $(cat /sys/class/net/$i/statistics/rx_bytes) $(cat /sys/class/net/$i/statistics/tx_bytes)"
          done
        done
        """
        do {
            let text = try await vmCollector.sh(script, sshSettings, sudo: true, merge: true)
            let samples = TNProcParser.parse(text)
            guard !samples.isEmpty else {
                let detail = text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300)
                vmStatsError = detail.isEmpty ? "No process data returned for the running VM(s)." : "On the NAS: \(detail)"
                return
            }
            vmStatsError = nil
            for vm in running {
                guard let pid = vm.status.pid, let sample = samples[pid] else { continue }
                var live = vmLive[vm.id] ?? TNVMLive()
                live.update(from: sample, previous: previousSamples[vm.id], vm: vm)
                live.disks = vmDisks[vm.id] ?? []
                vmLive[vm.id] = live
                previousSamples[vm.id] = sample
            }
        } catch {
            previousSamples = [:]
            vmStatsError = error.localizedDescription
        }
    }

    private static func json(_ data: Data) -> Any? { try? JSONSerialization.jsonObject(with: data) }
    private static func number(_ v: Any?) -> Double? {
        if let n = v as? NSNumber { return n.doubleValue }
        if let d = v as? [String: Any] { return (d["parsed"] as? NSNumber)?.doubleValue ?? (d["rawvalue"] as? String).flatMap(Double.init) }
        return nil
    }

    private func refreshVMDisks() async {
        guard let c = client else { return }
        lastZvolFetch = .now
        guard let data = try? await c.callRaw("vm.device.query", [[["dtype", "=", "DISK"]]]),
              let devices = Self.json(data) as? [[String: Any]] else { return }
        var usage: [String: (capacity: Double, used: Double)] = [:]
        var result: [Int: [TNVMLive.DiskUse]] = [:]
        for dev in devices {
            let vmID = (dev["vm"] as? Int) ?? ((dev["vm"] as? [String: Any])?["id"] as? Int)
            guard let vmID, let path = (dev["attributes"] as? [String: Any])?["path"] as? String else { continue }
            let prefix = "/dev/zvol/"
            let id = path.hasPrefix(prefix) ? (String(path.dropFirst(prefix.count)).removingPercentEncoding ?? "") : ""
            if !id.isEmpty, usage[id] == nil,
               let d = try? await c.callRaw("pool.dataset.query", [[["id", "=", id]]]),
               let ds = (Self.json(d) as? [[String: Any]])?.first,
               let size = Self.number(ds["volsize"]) {
                usage[id] = (size, Self.number(ds["referenced"]) ?? Self.number(ds["used"]) ?? 0)
            }
            let name = id.isEmpty ? (path.split(separator: "/").last.map(String.init) ?? path) : id
            let u = usage[id]
            result[vmID, default: []].append(.init(name: name, capacity: u?.capacity ?? 0, allocation: u?.used ?? 0))
        }
        vmDisks = result
    }

    // MARK: Containers

    func setContainersActive(_ active: Bool) {
        containersTask?.cancel(); containersTask = nil
        guard active, isSSHConfigured else { return }
        containersTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.loadContainers()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    func loadContainers() async {
        do {
            let text = try await containerCollector.sh(DockerParser.sampleScript, sshSettings, sudo: true, merge: true)
            let parsed = DockerParser.parse(text)
            if parsed.isEmpty, !text.contains("{") {
                let detail = text.replacingOccurrences(of: "---STATS---", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !detail.isEmpty { containersError = "On the NAS: \(detail.prefix(300))"; return }
            }
            containers = parsed
            containersError = nil
            containersLoaded = true
        } catch { containersError = error.localizedDescription }
    }

    private static func safeName(_ s: String) -> Bool { s.range(of: #"^[A-Za-z0-9][A-Za-z0-9_.-]*$"#, options: .regularExpression) != nil }

    func containerAction(_ c: DockerContainer, verb: String) async {
        guard ["start", "stop", "restart"].contains(verb), Self.safeName(c.name) else { return }
        do {
            let out = try await containerCollector.sh("docker \(verb) \(c.name)", sshSettings, sudo: true, merge: true)
            if !out.contains(c.name) { actionError = "docker \(verb): \(out.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))" }
        } catch { actionError = error.localizedDescription }
        await loadContainers()
    }

    func containerLogs(_ c: DockerContainer) async -> String {
        guard Self.safeName(c.name) else { return "Invalid container name." }
        do {
            let out = try await containerCollector.sh("docker logs --tail 300 \(c.name)", sshSettings, sudo: true, merge: true)
            return out.isEmpty ? "(no output)" : out
        } catch { return error.localizedDescription }
    }

    // MARK: Fan controller

    private var fanClient: FanClient? {
        guard !machine.host.isEmpty, let url = URL(string: "http://\(machine.host):\(machine.fanPort)/") else { return nil }
        return FanClient(base: url, password: MachineStore.fanPassword(for: machine.id))
    }

    func setFanActive(_ active: Bool) {
        fanTask?.cancel(); fanTask = nil
        guard active else { return }
        fanTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pollFan()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    private func pollFan() async {
        guard let client = fanClient else { fanError = "Set the NAS address first."; return }
        do {
            if fanConfig == nil { fanConfig = try await client.config() }
            let s = try await client.status()
            fanStatus = s
            fanError = nil
            func push(_ a: inout [Double], _ v: Double?) {
                guard let v else { return }
                a.append(v); if a.count > 60 { a.removeFirst(a.count - 60) }
            }
            push(&fanRPMHistory, s.rpm.map(Double.init)); push(&fanDutyHistory, s.pwmPct); push(&fanTempHistory, s.ctlTemp)
        } catch {
            fanError = "Fan controller (\(machine.host):\(machine.fanPort)): \(error.localizedDescription)"
        }
    }

    func saveFan(_ config: FanConfig) async -> Bool {
        guard let client = fanClient else { return false }
        do {
            fanConfig = try await client.save(config)
            fanError = nil
            await pollFan()
            return true
        } catch {
            actionError = "Couldn't save fan settings: \(error.localizedDescription)"
            return false
        }
    }

    // MARK: Datasets & Snapshots

    func refreshDatasets() async {
        guard let c = client else { return }
        if let d = try? await c.callRaw("pool.dataset.query"), let rows = Self.json(d) as? [[String: Any]] {
            datasets = rows.compactMap { r in
                guard let name = r.tnStr("name") ?? r.tnStr("id") else { return nil }
                return TNDataset(id: name, name: name, type: r.tnStr("type") ?? "", used: Int64(r.tnNum("used") ?? 0),
                                 available: Int64(r.tnNum("available") ?? 0), mountpoint: r.tnStr("mountpoint") ?? "")
            }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        }
        if let d = try? await c.callRaw("zfs.snapshot.query"), let rows = Self.json(d) as? [[String: Any]] {
            snapshots = rows.compactMap { r in
                guard let name = r.tnStr("name") ?? r.tnStr("id") else { return nil }
                return TNSnapshotRow(id: name, name: name, dataset: r.tnStr("dataset") ?? "", used: Int64(r.tnNum("used") ?? 0))
            }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedDescending }
        }
    }

    // MARK: Shares

    func refreshShares() async {
        guard let c = client else { return }
        var all: [TNShare] = []
        if let d = try? await c.callRaw("sharing.smb.query"), let rows = Self.json(d) as? [[String: Any]] {
            all += rows.enumerated().map { i, r in
                TNShare(id: "smb-\(i)", name: r.tnStr("name") ?? "", path: r.tnStr("path") ?? "",
                        comment: r.tnStr("comment") ?? "", enabled: r.tnBool("enabled") ?? true, kind: "SMB")
            }
        }
        if let d = try? await c.callRaw("sharing.nfs.query"), let rows = Self.json(d) as? [[String: Any]] {
            all += rows.enumerated().map { i, r in
                let paths = (r["path"] as? [String]) ?? (r["paths"] as? [String]) ?? []
                return TNShare(id: "nfs-\(i)", name: paths.first ?? "", path: paths.joined(separator: ", "),
                               comment: r.tnStr("comment") ?? "", enabled: r.tnBool("enabled") ?? true, kind: "NFS")
            }
        }
        shares = all
    }

    // MARK: Network

    func refreshNetwork() async {
        guard let c = client, let d = try? await c.callRaw("interface.query"), let rows = Self.json(d) as? [[String: Any]] else { return }
        interfaces = rows.compactMap { r in
            guard let name = r.tnStr("name") ?? r.tnStr("id") else { return nil }
            let state = r["state"] as? [String: Any]
            let aliases = (state?["aliases"] as? [[String: Any]])?.compactMap { $0["address"] as? String } ?? []
            return TNInterface(id: name, name: name, addresses: aliases, linkState: (state?["link_state"] as? String) ?? "")
        }
    }

    // MARK: Data Protection (snapshot tasks, replication, rsync, cloud sync, cron)

    func refreshDataProtection() async {
        guard let c = client else { return }
        var tasks: [TNProtectionTask] = []
        if let d = try? await c.callRaw("pool.snapshottask.query"), let rows = Self.json(d) as? [[String: Any]] {
            tasks += rows.enumerated().map { i, r in
                TNProtectionTask(id: "snap-\(i)", kind: "Snapshot Task", summary: r.tnStr("dataset") ?? "–", enabled: r.tnBool("enabled") ?? true)
            }
        }
        if let d = try? await c.callRaw("replication.query"), let rows = Self.json(d) as? [[String: Any]] {
            tasks += rows.enumerated().map { i, r in
                TNProtectionTask(id: "repl-\(i)", kind: "Replication", summary: r.tnStr("name") ?? "–", enabled: r.tnBool("enabled") ?? true)
            }
        }
        if let d = try? await c.callRaw("rsynctask.query"), let rows = Self.json(d) as? [[String: Any]] {
            tasks += rows.enumerated().map { i, r in
                TNProtectionTask(id: "rsync-\(i)", kind: "Rsync", summary: r.tnStr("remotehost") ?? "–", enabled: r.tnBool("enabled") ?? true)
            }
        }
        if let d = try? await c.callRaw("cloudsync.query"), let rows = Self.json(d) as? [[String: Any]] {
            tasks += rows.enumerated().map { i, r in
                TNProtectionTask(id: "cloud-\(i)", kind: "Cloud Sync", summary: r.tnStr("description") ?? "–", enabled: r.tnBool("enabled") ?? true)
            }
        }
        if let d = try? await c.callRaw("cronjob.query"), let rows = Self.json(d) as? [[String: Any]] {
            tasks += rows.enumerated().map { i, r in
                TNProtectionTask(id: "cron-\(i)", kind: "Cron Job", summary: r.tnStr("description") ?? r.tnStr("command") ?? "–", enabled: r.tnBool("enabled") ?? true)
            }
        }
        protectionTasks = tasks
    }

    // MARK: Updates

    func refreshUpdateStatus() async {
        // update.check_available was removed in newer SCALE; update.status is shaped {code, status:{new_version}, error}.
        guard let c = client, let d = try? await c.callRaw("update.status"), let obj = Self.json(d) as? [String: Any] else { return }
        let code = obj.tnStr("code") ?? ""
        let labels = ["NORMAL": "Up to date", "REBOOT_REQUIRED": "Reboot required to finish an update",
                      "HAS_UPDATE": "Update available", "UPDATE_AVAILABLE": "Update available", "ERROR": "Error checking for updates"]
        var status = labels[code] ?? code
        let nv = (obj["status"] as? [String: Any])?["new_version"]
        if let d = nv as? [String: Any] { updateVersion = d.tnStr("version") ?? d.tnStr("train") }
        else { updateVersion = nv as? String }
        if let err = obj["error"] as? String, !err.isEmpty { status += status.isEmpty ? err : " — \(err)" }
        updateStatus = status
    }

    // MARK: Users & Groups

    func refreshUserAccounts() async {
        guard let c = client, let d = try? await c.callRaw("user.query"), let rows = Self.json(d) as? [[String: Any]] else { return }
        userAccounts = rows.compactMap { r in
            guard let uid = (r["uid"] as? NSNumber)?.intValue, let username = r.tnStr("username") else { return nil }
            return TNUserAccount(id: uid, username: username, fullName: r.tnStr("full_name") ?? "", locked: r.tnBool("locked") ?? false, uid: uid)
        }.sorted { $0.username.localizedStandardCompare($1.username) == .orderedAscending }
    }

    // MARK: Console

    func runConsole(_ command: String) async -> String {
        guard isSSHConfigured else { return consoleUnavailableReason }
        return (try? await consoleRunner.sh(command, sshSettings, sudo: machine.sshSudo, merge: true)) ?? "Command failed."
    }

    // MARK: VPN (containerized WireGuard — a plain `docker run`, not a registered TrueNAS App, so it
    // needs no dataset/volume layout assumptions and shows up on the Containers page too)

    func refreshVPN() async {
        guard isSSHConfigured else { return }
        await loadContainers()
        var result: [TNVPNContainer] = []
        for c in containers where c.name.hasPrefix(Self.vpnPrefix) {
            let name = String(c.name.dropFirst(Self.vpnPrefix.count))
            var tunnel: WireGuardTunnel?
            if c.isRunning, let dump = try? await containerCollector.sh("docker exec \(c.name) wg show all dump", sshSettings, sudo: true, merge: true) {
                tunnel = WireGuardParser.tunnels(confNames: ["wg0"], dump: dump).first
            }
            result.append(TNVPNContainer(name: name, containerName: c.name, running: c.isRunning, tunnel: tunnel))
        }
        vpnContainers = result.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        vpnError = nil
    }

    func addVPNTunnel(name: String, config: String) async -> String? {
        guard WireGuardParser.isValidName(name) else { return "Use letters, numbers, dashes and underscores only." }
        guard isSSHConfigured else { return "Enable SSH on this machine first (Edit sheet)." }
        let cname = Self.vpnPrefix + name
        let dir = "/tmp/homelab-vpn-\(name)"
        let b64 = Data(config.utf8).base64EncodedString()
        let script = """
        mkdir -p \(dir)/wg_confs
        echo \(b64) | base64 -d > \(dir)/wg_confs/wg0.conf
        chmod 600 \(dir)/wg_confs/wg0.conf
        docker rm -f \(cname) >/dev/null 2>&1
        docker run -d --name \(cname) --cap-add NET_ADMIN --cap-add SYS_MODULE \
          --sysctl net.ipv4.conf.all.src_valid_mark=1 -v \(dir):/config --restart unless-stopped \
          lscr.io/linuxserver/wireguard:latest
        """
        let out = (try? await containerCollector.sh(script, sshSettings, sudo: true, merge: true)) ?? "Failed to start the container."
        await refreshVPN()
        return out.lowercased().contains("error") || out.lowercased().contains("cannot") ? out : nil
    }

    func startVPNTunnel(_ name: String) async {
        _ = try? await containerCollector.sh("docker start \(Self.vpnPrefix + name)", sshSettings, sudo: true, merge: true)
        await refreshVPN()
    }

    func stopVPNTunnel(_ name: String) async {
        _ = try? await containerCollector.sh("docker stop \(Self.vpnPrefix + name)", sshSettings, sudo: true, merge: true)
        await refreshVPN()
    }

    func removeVPNTunnel(_ name: String) async {
        let script = "docker rm -f \(Self.vpnPrefix + name); rm -rf /tmp/homelab-vpn-\(name)"
        _ = try? await containerCollector.sh(script, sshSettings, sudo: true, merge: true)
        await refreshVPN()
    }
}

private extension StorageItem {
    var name: String { id }
}
