import Foundation

/// Manages a Debian/Ubuntu, Raspberry Pi OS, or Arch Linux machine over SSH + POSIX shell.
/// (Kind-specific extras — Pi-hole, PiKVM, Tailscale, Raspberry Pi telemetry, Arch/apt update
/// handling — belong to a later phase; this covers the shared core: host/CPU/mem/storage/services/processes.)
@MainActor @Observable
final class LinuxSession: MachineSession, CPUHosting, GPUHosting, ContainerHosting, ConsoleHosting, UserHosting, LogHosting, UpdateHosting, TailscaleHosting, WireGuardHosting {
    private let runner = SSHRunner()
    private var prevCPU: (busy: Double, total: Double)?
    private var prevNet: (rx: Double, tx: Double, time: Date)?

    // Feature-gated extras, detected on each poll
    private(set) var hasDocker = false
    private(set) var hasPihole = false
    private(set) var hasPiKVM = false
    private(set) var hasTailscale = false
    private(set) var hasRaspberryPi = false
    private(set) var hasWireGuard = false
    private(set) var hasProtonVPNCLI = false
    private(set) var hasNvidia = false
    private(set) var hasLibvirt = false

    // CPU / GPU pages (shared with Windows)
    var cpuInfo = WinCPUInfo()
    var cores: [CoreLoad] = []
    var coreHistory: [String: [Double]] = [:]
    var gpus: [GPUAdapter] = []
    var gpuHistory: [String: GPUHistory] = [:]
    var gpuProcesses: [GPUProcess] = []   // no per-process GPU query on Linux
    private var prevCores: [String: (busy: Double, total: Double)] = [:]

    // Power (Intel RAPL + NVIDIA board power), sampled every ~10 s because RAPL needs sudo
    private var rapl = RAPLSampler()
    private var lastPowerSample = Date.distantPast
    private var powerUnavailable = false

    // libvirt VMs
    var vms: [LinuxVM] = []
    var vmsError: String?
    private var vmsTask: Task<Void, Never>?

    // Pi-hole v6 API (used when a web password is set) + Local DNS
    private var piholeClient: PiholeClient?
    var localDNSRecords: [PiholeDNSRecord] = []
    var localDNSError: String?
    var protonStatus: String?
    var protonBusy = false

    override var navItems: [NavItem] {
        var items = Set(machine.kind.navItems)
        items.insert(.cpu)
        if hasNvidia { items.insert(.gpu) }
        if hasDocker { items.insert(.containers) }
        if hasPihole { items.formUnion([.pihole, .localDNS]) }
        if hasPiKVM { items.insert(.pikvm) }
        if hasTailscale { items.insert(.tailscale) }
        if hasRaspberryPi { items.insert(.raspberryPi) }
        if hasWireGuard || hasProtonVPNCLI { items.insert(.vpn) }
        if hasLibvirt { items.insert(.vms) }
        return sortNavItems(items)
    }

    // Users / Logs / Updates / Scheduled Tasks / Raspberry Pi
    var users: [LoggedInUser] = []
    var logLines: [String] = []
    var updates: [UpdateItem] = []
    var updatesChecked = false
    var cronJobs: [String] = []
    var timers: [String] = []
    var piTemp: Double?
    var piThrottled: [String] = []
    var piClockMHz: Double?

    var consoleIsAvailable: Bool { true }
    var consoleUnavailableReason: String { "" }

    // Pi-hole
    var piholeStatus: PiholeStatus?
    var piholeError: String?

    // PiKVM
    var pikvmRootRW: Bool?
    var pikvmActive: Bool?

    // Tailscale
    var tailscaleStatus: TailscaleStatus?
    var tailscaleError: String?

    // WireGuard
    var wireguardTunnels: [WireGuardTunnel] = []
    var wireguardError: String?

    // Containers
    var containers: [DockerContainer] = []
    var containersLoaded = false
    var containersError: String?
    private let containerCollector = SSHRunner()
    private var containersTask: Task<Void, Never>?

    private var ssh: SSHSettings {
        SSHSettings(host: machine.host, port: machine.port, user: machine.user, password: MachineStore.secret(for: machine.id), useSudo: machine.useSudo)
    }

    override func poll() async -> Bool {
        if state != .connected { state = .connecting }
        let text: String
        do { text = try await runner.sh(LinuxScripts.sample, ssh) }
        catch { state = .failed(error.localizedDescription); return false }
        let sec = LinuxParse.sections(text)
        guard sec["END"] != nil, let hostLines = sec["HOST"], hostLines.count >= 6 else {
            state = .failed("Unexpected reply from the machine. Is a POSIX shell available over SSH?")
            return false
        }
        let h = LinuxParse.host(hostLines)
        hostName = h.hostname
        hostOS = h.os
        hostUptime = h.uptime

        let mem = LinuxParse.mem(sec["MEM"] ?? [])
        memTotal = mem.total; memUsed = mem.used
        push(&memHistory, memTotal > 0 ? mem.used / mem.total * 100 : nil)

        storage = LinuxParse.filesystems(sec["DF"] ?? []).map {
            StorageItem(id: $0.mount, title: $0.mount, subtitle: $0.device, size: $0.size, used: $0.used)
        } + LinuxParse.zpools(sec["ZPOOL"] ?? []).map {
            StorageItem(id: "zfs-\($0.name)", title: "ZFS \($0.name)", subtitle: $0.health, size: $0.size, used: $0.allocated, healthy: $0.health == "ONLINE", statusText: $0.health)
        }

        services = LinuxParse.services(sec["SVC"] ?? []).sorted { $0.name < $1.name }
        servicesLoaded = true
        extraAlerts = services.filter { $0.state == "failed" }.map {
            AlertItem(id: "svc-\($0.name)", level: .critical, title: "Service failed: \($0.name)", detail: "")
        }

        let cpuName = sec["CPUINFO"]?.first ?? ""
        let nproc = Int(hostLines[5].trimmingCharacters(in: .whitespaces)) ?? 1
        let mhz = Double(sec["CPUINFO"]?.dropFirst().first?.trimmingCharacters(in: .whitespaces) ?? "") ?? 0
        cpuInfo = WinCPUInfo(name: cpuName.trimmingCharacters(in: .whitespaces), cores: nproc, logical: nproc, maxMHz: mhz,
                             virtualization: (sec["FEAT"] ?? []).contains("VTX"))
        let coreTicks = LinuxParse.perCoreTicks(sec["STAT"] ?? [])
        cores = coreTicks.keys.sorted { (Int($0.dropFirst(3)) ?? 0) < (Int($1.dropFirst(3)) ?? 0) }.map { name in
            let t = coreTicks[name]!
            var util = 0.0
            if let p = prevCores[name], t.total > p.total { util = min(100, max(0, (t.busy - p.busy) / (t.total - p.total) * 100)) }
            return CoreLoad(name: name, util: util, freqMHz: mhz)
        }
        prevCores = coreTicks
        for c in cores { push(&coreHistory[c.id, default: []], c.util, limit: 30) }

        if let t = LinuxParse.cpuTicks(sec["STAT"] ?? []) {
            if let p = prevCPU, t.total > p.total { cpuPercent = min(100, max(0, (t.busy - p.busy) / (t.total - p.total) * 100)) }
            prevCPU = t
        }
        push(&cpuHistory, cpuPercent)

        let n = LinuxParse.net(sec["NET"] ?? [])
        let now = Date.now
        if let p = prevNet {
            let dt = now.timeIntervalSince(p.time)
            if dt > 0.5 { netRxRate = max(0, n.rx - p.rx) / dt; netTxRate = max(0, n.tx - p.tx) / dt }
        }
        prevNet = (n.rx, n.tx, now)

        let feat = Set(sec["FEAT"] ?? [])
        hasDocker = feat.contains("DOCKER")
        hasPihole = feat.contains("PIHOLE")
        hasPiKVM = feat.contains("PIKVM")
        hasTailscale = feat.contains("TAILSCALE")
        hasRaspberryPi = feat.contains("RASPI")
        hasWireGuard = feat.contains("WIREGUARD")
        hasProtonVPNCLI = feat.contains("PROTONVPN")
        hasNvidia = feat.contains("NVIDIA")
        hasLibvirt = feat.contains("LIBVIRT")

        state = .connected
        lastRefresh = .now
        if hasNvidia, let t = try? await runner.sh(LinuxScripts.gpuUtil, ssh) {
            gpuPercent = t.split(whereSeparator: \.isNewline).compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }.max()
        }
        if !powerUnavailable, Date.now.timeIntervalSince(lastPowerSample) > 10 {
            lastPowerSample = .now
            await samplePower()
        }
        return true
    }

    private func samplePower() async {
        guard let out = try? await runner.sh(LinuxScripts.power, ssh, sudo: true, merge: true) else { return }
        let parts = out.components(separatedBy: "---GPU---")
        let cpu = rapl.watts(from: parts.first ?? "")
        let gpu = parts.count > 1 ? Double(parts[1].trimmingCharacters(in: .whitespacesAndNewlines)) : nil
        // No counter, no GPU, or sudo refused: stop asking, so a bad sudo password isn't retried every 10 s.
        if Double((parts.first ?? "").trimmingCharacters(in: .whitespacesAndNewlines)) == nil, gpu == nil { powerUnavailable = true }
        powerWatts = (cpu == nil && gpu == nil) ? nil : (cpu ?? 0) + (gpu ?? 0)
    }

    func refreshGPUDetail() async {
        guard let t = try? await runner.sh(LinuxScripts.gpuDetail, ssh) else { return }
        let info = NVInfo.parse(t)
        gpus = info.enumerated().map { i, n in
            GPUAdapter(id: "\(i)|\(n.name)", name: n.name, vramTotal: n.memTotal, vramUsed: n.memUsed, nvidia: n)
        }
        for g in gpus {
            push(&gpuHistory[g.id, default: GPUHistory()].util, g.util)
            push(&gpuHistory[g.id, default: GPUHistory()].vram, g.vramFraction.map { $0 * 100 })
        }
        gpuPercent = gpus.compactMap(\.util).max()
    }

    override func closeBackend() async {
        await runner.close(); prevCPU = nil; prevNet = nil; prevCores = [:]; rapl.reset(); powerUnavailable = false
        vmsTask?.cancel(); piholeClient = nil
        containersTask?.cancel()
        await containerCollector.close()
    }

    // MARK: Pi-hole

    private var piholePassword: String { MachineStore.piholePassword(for: machine.id) }

    private func piholeAPI() -> PiholeClient {
        if let c = piholeClient { return c }
        let c = PiholeClient(host: machine.host, password: piholePassword)
        piholeClient = c
        return c
    }

    /// Enabled/disabled always comes from SSH (works on every version). Query stats come from `pihole -c -j` on v5,
    /// or from the v6 REST API when a web admin password is configured (v6 removed the JSON dump).
    func refreshPihole() async {
        var status = (try? await runner.sh(LinuxScripts.piholeStatus, ssh)).flatMap(LinuxParse.pihole)
        if !piholePassword.isEmpty {
            do {
                let api = piholeAPI()
                let summary = try await api.get("stats/summary")
                let q = summary["queries"] as? [String: Any] ?? [:]
                let g = summary["gravity"] as? [String: Any] ?? [:]
                var enabled = status?.enabled ?? true
                if let b = try? await api.get("dns/blocking"), let v = b["blocking"] { enabled = "\(v)".lowercased() == "enabled" }
                var s = status ?? PiholeStatus(enabled: enabled)
                s.queriesToday = (q["total"] as? NSNumber)?.intValue ?? s.queriesToday
                s.blockedToday = (q["blocked"] as? NSNumber)?.intValue ?? s.blockedToday
                s.percentBlocked = (q["percent_blocked"] as? NSNumber)?.doubleValue ?? s.percentBlocked
                s.domainsOnBlocklist = (g["domains_being_blocked"] as? NSNumber)?.intValue ?? s.domainsOnBlocklist
                status = s
                piholeError = nil
            } catch { piholeError = error.localizedDescription }
        } else {
            piholeError = status == nil ? "Couldn't read Pi-hole stats over SSH. If this is Pi-hole v6, add its web admin password in the Edit sheet — v6 needs the API for stats." : nil
        }
        piholeStatus = status
    }

    // MARK: Pi-hole Local DNS (v5: /etc/pihole/custom.list over SSH; v6: config/dns/hosts API when a password is set)

    private func piholeHosts() async throws -> [String] {
        let data = try await piholeAPI().get("config/dns/hosts")
        for path in [["config", "dns", "hosts"], ["dns", "hosts"], ["hosts"]] {
            var node: Any? = data
            for key in path { node = (node as? [String: Any])?[key] }
            if let list = node as? [Any] { return list.map { "\($0)" } }
        }
        return []
    }

    func refreshLocalDNS() async {
        if !piholePassword.isEmpty {
            do { localDNSRecords = LinuxParse.localDNS(try await piholeHosts().joined(separator: "\n")); localDNSError = nil }
            catch { localDNSError = error.localizedDescription }
            return
        }
        do { localDNSRecords = LinuxParse.localDNS(try await runner.sh("cat /etc/pihole/custom.list 2>/dev/null", ssh, sudo: true, merge: true)); localDNSError = nil }
        catch { localDNSError = error.localizedDescription }
    }

    /// Returns an error message, or nil on success.
    func addLocalDNS(domain: String, ip: String) async -> String? {
        let d = domain.trimmingCharacters(in: .whitespaces), i = ip.trimmingCharacters(in: .whitespaces)
        guard !d.isEmpty, !i.isEmpty else { return "Enter both a domain and an IP address." }
        if !piholePassword.isEmpty {
            do {
                var hosts = try await piholeHosts().filter { !$0.hasSuffix(" \(d)") }
                hosts.append("\(i) \(d)")
                try await piholeAPI().patch("config", ["config": ["dns": ["hosts": hosts]]])
            } catch { return error.localizedDescription }
        } else {
            let out = (try? await runner.sh("pihole -a hostrecord \(SSHRunner.quote(d)) \(SSHRunner.quote(i))", ssh, sudo: true, merge: true)) ?? "Failed to run pihole."
            if out.lowercased().contains("error") { return out }
        }
        await refreshLocalDNS()
        return nil
    }

    func removeLocalDNS(_ r: PiholeDNSRecord) async -> String? {
        if !piholePassword.isEmpty {
            do {
                let hosts = try await piholeHosts().filter { !$0.hasSuffix(" \(r.domain)") }
                try await piholeAPI().patch("config", ["config": ["dns": ["hosts": hosts]]])
            } catch { return error.localizedDescription }
        } else {
            let out = (try? await runner.sh("pihole -a hostrecord \(SSHRunner.quote(r.domain))", ssh, sudo: true, merge: true)) ?? "Failed to run pihole."
            if out.lowercased().contains("error") { return out }
        }
        await refreshLocalDNS()
        return nil
    }

    // MARK: VMs (libvirt/QEMU)

    func setVMsActive(_ active: Bool) {
        vmsTask?.cancel(); vmsTask = nil
        guard active, hasLibvirt else { return }
        vmsTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.loadVMs()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    func loadVMs() async {
        do { vms = LinuxParse.vms(try await runner.sh(LinuxScripts.vms, ssh, sudo: true, merge: true)); vmsError = nil }
        catch { vmsError = error.localizedDescription }
    }

    func vmAction(_ v: LinuxVM, action: String) async {
        let verb = ["start": "start", "restart": "reboot", "stop": "shutdown", "poweroff": "destroy"][action] ?? action
        _ = try? await runner.sh("virsh -c qemu:///system \(verb) \(SSHRunner.quote(v.name))", ssh, sudo: true, merge: true)
        try? await Task.sleep(for: .seconds(2))
        await loadVMs()
    }

    func piholeSetEnabled(_ enabled: Bool, duration: String? = nil) async {
        let cmd = enabled ? "pihole enable" : "pihole disable\(duration.map { " \($0)" } ?? "")"
        _ = try? await runner.sh(cmd, ssh, sudo: true, merge: true)
        try? await Task.sleep(for: .seconds(1))
        await refreshPihole()
    }

    // MARK: PiKVM

    func refreshPiKVM() async {
        guard let t = try? await runner.sh(LinuxScripts.pikvmStatus, ssh) else { return }
        let lines = t.split(separator: "\n").map(String.init)
        pikvmActive = lines.first == "active"
        pikvmRootRW = lines.count > 1 ? lines[1] == "rw" : nil
    }

    func pikvmSetWritable(_ writable: Bool) async {
        _ = try? await runner.sh(writable ? "rw" : "ro", ssh, sudo: true, merge: true)
        try? await Task.sleep(for: .seconds(1))
        await refreshPiKVM()
    }

    func pikvmRestart() async {
        _ = try? await runner.sh("systemctl restart kvmd", ssh, sudo: true, merge: true)
        try? await Task.sleep(for: .seconds(1))
        await refreshPiKVM()
    }

    // MARK: Tailscale

    func refreshTailscale() async {
        guard let t = try? await runner.sh("tailscale status --json", ssh) else { return }
        if let s = TailscaleParser.parse(t) { tailscaleStatus = s; tailscaleError = nil }
        else { tailscaleError = "Couldn't read Tailscale status." }
    }

    // MARK: Raspberry Pi

    func refreshRaspberryPi() async {
        guard let t = try? await runner.sh(LinuxScripts.raspi, ssh) else { return }
        let lines = t.split(separator: "\n").map(String.init)
        guard lines.count >= 3 else { return }
        let tempStr = lines[0].split(separator: "=").last?.replacingOccurrences(of: "'C", with: "").trimmingCharacters(in: .whitespaces)
        piTemp = tempStr.flatMap(Double.init)
        if let hz = lines[1].split(separator: "=").last.flatMap({ Double($0) }) { piClockMHz = hz / 1_000_000 }
        if let hex = lines[2].split(separator: "=").last?.replacingOccurrences(of: "0x", with: "") {
            piThrottled = LinuxParse.throttleFlags(hex)
        }
    }

    // MARK: WireGuard

    func refreshWireGuard() async {
        guard let t = try? await runner.sh(LinuxScripts.wireguardStatus, ssh, sudo: true, merge: true) else { return }
        let parts = t.components(separatedBy: "---DUMP---")
        let confNames = (parts.first ?? "").split(separator: "\n").map(String.init)
        wireguardTunnels = WireGuardParser.tunnels(confNames: confNames, dump: parts.count > 1 ? parts[1] : "")
        wireguardError = nil
    }

    func addWireGuardTunnel(name: String, config: String) async -> String? {
        guard WireGuardParser.isValidName(name) else { return "Use letters, numbers, dashes and underscores only." }
        let b64 = Data(config.utf8).base64EncodedString()
        let script = """
        echo \(b64) | base64 -d > /tmp/wg-\(name).conf
        install -m 600 /tmp/wg-\(name).conf /etc/wireguard/\(name).conf
        rm -f /tmp/wg-\(name).conf
        """
        let out = (try? await runner.sh(script, ssh, sudo: true, merge: true)) ?? "Failed to write the config."
        await refreshWireGuard()
        return out.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : (out.lowercased().contains("error") || out.lowercased().contains("permission") ? out : nil)
    }

    func connectWireGuardTunnel(_ name: String) async {
        guard WireGuardParser.isValidName(name) else { return }
        let out = (try? await runner.sh("wg-quick up \(name)", ssh, sudo: true, merge: true)) ?? ""
        if out.lowercased().contains("error") { wireguardError = out }
        await refreshWireGuard()
    }

    func disconnectWireGuardTunnel(_ name: String) async {
        guard WireGuardParser.isValidName(name) else { return }
        _ = try? await runner.sh("wg-quick down \(name)", ssh, sudo: true, merge: true)
        await refreshWireGuard()
    }

    func removeWireGuardTunnel(_ name: String) async {
        guard WireGuardParser.isValidName(name) else { return }
        _ = try? await runner.sh("wg-quick down \(name) 2>/dev/null; rm -f /etc/wireguard/\(name).conf", ssh, sudo: true, merge: true)
        await refreshWireGuard()
    }

    // MARK: ProtonVPN CLI (best-effort — depends on the classic `protonvpn-cli` tool being installed;
    // the raw command output is always surfaced so you can see exactly what it did if something
    // about your installed version doesn't match)

    func protonLogin(username: String, password: String) async {
        protonBusy = true
        defer { protonBusy = false }
        MachineStore.setProtonUser(username, for: machine.id)
        MachineStore.setProtonPassword(password, for: machine.id)
        let script = "printf '%s\\n' \(SSHRunner.quote(password)) | protonvpn-cli login \(SSHRunner.quote(username))"
        protonStatus = (try? await runner.sh(script, ssh, sudo: false, merge: true)) ?? "Failed to run protonvpn-cli."
    }

    func protonConnectFastest() async {
        protonBusy = true
        defer { protonBusy = false }
        protonStatus = (try? await runner.sh("protonvpn-cli c -f", ssh, sudo: true, merge: true)) ?? "Failed to run protonvpn-cli."
    }

    func protonDisconnect() async {
        protonBusy = true
        defer { protonBusy = false }
        protonStatus = (try? await runner.sh("protonvpn-cli d", ssh, sudo: true, merge: true)) ?? "Failed to run protonvpn-cli."
    }

    func protonRefreshStatus() async {
        protonStatus = (try? await runner.sh("protonvpn-cli status", ssh, sudo: false, merge: true)) ?? protonStatus
    }

    // MARK: Users / Logs / Updates / Console / Scheduled Tasks

    func refreshUsers() async {
        guard let t = try? await runner.sh("who", ssh) else { return }
        users = LinuxParse.who(t)
    }

    func refreshLogs(filter: String) async {
        let unit = filter.trimmingCharacters(in: .whitespaces)
        let cmd = unit.isEmpty ? "journalctl -n 300 --no-pager" : "journalctl -u \(SSHRunner.quote(unit)) -n 300 --no-pager"
        let t = (try? await runner.sh(cmd, ssh, sudo: true, merge: true)) ?? "Couldn't load logs."
        logLines = t.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
    }

    func refreshUpdates() async {
        let cmd = machine.kind == .arch ? "pacman -Qu 2>/dev/null" : "apt list --upgradable 2>/dev/null | tail -n +2"
        guard let t = try? await runner.sh(cmd, ssh) else { return }
        updates = machine.kind == .arch ? LinuxParse.pacmanUpdates(t) : LinuxParse.aptUpdates(t)
        updatesChecked = true
    }

    func upgradeAll() async -> String {
        let cmd = machine.kind == .arch
            ? "pacman -Syu --noconfirm"
            : "export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get -y upgrade"
        let out = (try? await runner.sh(cmd, ssh, sudo: true, merge: true)) ?? "Failed to run."
        await refreshUpdates()
        return out
    }

    func refreshScheduledTasks() async {
        guard let t = try? await runner.sh(LinuxScripts.scheduled, ssh, sudo: true, merge: true) else { return }
        let parts = t.components(separatedBy: "---TIMERS---")
        cronJobs = (parts.first ?? "").split(separator: "\n").map(String.init).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty && !$0.hasPrefix("#") }
        timers = parts.count > 1 ? parts[1].split(separator: "\n").map(String.init).filter { !$0.isEmpty } : []
    }

    func runConsole(_ command: String) async -> String {
        (try? await runner.sh(command, ssh, sudo: true, merge: true)) ?? "Command failed."
    }

    // MARK: Containers

    func setContainersActive(_ active: Bool) {
        containersTask?.cancel(); containersTask = nil
        guard active, hasDocker else { return }
        containersTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.loadContainers()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    func loadContainers() async {
        do {
            let text = try await containerCollector.sh(DockerParser.sampleScript, ssh, sudo: true, merge: true)
            containers = DockerParser.parse(text)
            containersError = nil
            containersLoaded = true
        } catch { containersError = error.localizedDescription }
    }

    func containerAction(_ c: DockerContainer, verb: String) async {
        guard ["start", "stop", "restart"].contains(verb) else { return }
        _ = try? await containerCollector.sh("docker \(verb) \(SSHRunner.quote(c.name))", ssh, sudo: true, merge: true)
        await loadContainers()
    }

    func containerLogs(_ c: DockerContainer) async -> String {
        (try? await containerCollector.sh("docker logs --tail 300 \(SSHRunner.quote(c.name))", ssh, sudo: true, merge: true)) ?? "Couldn't load logs."
    }

    override func refreshServices() async { _ = await poll() }

    override func refreshProcesses() async {
        guard let t = try? await runner.sh(LinuxScripts.processes, ssh) else { return }
        processes = LinuxParse.processes(t)
    }

    override func serviceAction(_ s: ServiceRow, verb: String) async {
        let out = try? await runner.sh("systemctl \(verb) \(SSHRunner.quote(s.name + ".service"))", ssh, sudo: true, merge: true)
        if let out, !out.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { actionError = out }
        try? await Task.sleep(for: .seconds(1))
        _ = await poll()
    }

    override func killProcess(_ p: ProcessRow) async {
        _ = try? await runner.sh("kill -9 \(p.pid)", ssh, sudo: true, merge: true)
        await refreshProcesses()
    }

    override func reboot() async { _ = try? await runner.sh("systemctl reboot", ssh, sudo: true) }
    override func shutdown() async { _ = try? await runner.sh("systemctl poweroff", ssh, sudo: true) }
}

// MARK: - Remote script + parser

private enum LinuxScripts {
    static let sample = """
    echo '##HOST'
    hostname
    ( . /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" ) || echo Linux
    uname -r
    uname -m
    cut -d' ' -f1 /proc/uptime
    nproc
    echo '##CPUINFO'
    awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo
    awk -F': ' '/^cpu MHz/{print $2; exit}' /proc/cpuinfo
    echo '##STAT'
    grep '^cpu' /proc/stat
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
    systemctl list-unit-files 2>/dev/null | grep -q '^kvmd\\.service' && echo PIKVM
    command -v tailscale >/dev/null 2>&1 && echo TAILSCALE
    command -v vcgencmd >/dev/null 2>&1 && echo RASPI
    command -v wg >/dev/null 2>&1 && echo WIREGUARD
    command -v protonvpn-cli >/dev/null 2>&1 && echo PROTONVPN
    command -v nvidia-smi >/dev/null 2>&1 && echo NVIDIA
    command -v virsh >/dev/null 2>&1 && echo LIBVIRT
    grep -qE 'vmx|svm' /proc/cpuinfo 2>/dev/null && echo VTX
    echo '##END'
    """

    static let gpuUtil = "nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null"

    static let gpuDetail = """
    nvidia-smi --query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.gr,clocks.max.gr,clocks.mem,clocks.max.mem,driver_version,pstate --format=csv,noheader,nounits 2>/dev/null
    """

    static let power = RAPLSampler.script + """

    echo '---GPU---'
    nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits 2>/dev/null | head -1
    """

    /// virsh needs root for the system-wide qemu:///system connection most KVM hosts use.
    static let vms = """
    for vm in $(virsh -c qemu:///system list --all --name 2>/dev/null); do
      [ -z "$vm" ] && continue
      info="$(virsh -c qemu:///system dominfo "$vm" 2>/dev/null)"
      state=$(echo "$info" | awk -F': +' '/^State:/{print $2}')
      autostart=$(echo "$info" | awk -F': +' '/^Autostart:/{print $2}')
      vcpus=$(echo "$info" | awk -F': +' '/^CPU\\(s\\):/{print $2}')
      maxmem=$(echo "$info" | awk -F': +' '/^Max memory:/{print $2}')
      echo "VM|$vm|$state|$autostart|$vcpus|$maxmem"
    done
    """

    static let processes = """
    ps -eo pid,user:20,pcpu,pmem,comm --sort=-pcpu --no-headers | head -60
    """

    /// Plain-text status (works on every Pi-hole version) plus the legacy chronometer JSON for extra
    /// stats where it's still available (Pi-hole v6 replaced it with an authenticated REST API, so the
    /// JSON half may come back empty — the plain-text enabled/disabled read still works either way).
    static let piholeStatus = """
    pihole status 2>/dev/null
    echo '---JSON---'
    pihole -c -j 2>/dev/null
    """

    /// Line 1: kvmd service state. Line 2: whether / is mounted read-write (PiKVM ships `rw`/`ro` helpers).
    static let pikvmStatus = """
    systemctl is-active kvmd 2>/dev/null
    mount | awk '$3==\"/\"{if($0~/\\(rw/)print \"rw\"; else print \"ro\"}'
    """

    static let raspi = """
    vcgencmd measure_temp
    vcgencmd measure_clock arm
    vcgencmd get_throttled
    """

    static let scheduled = """
    crontab -l 2>/dev/null
    echo '---TIMERS---'
    systemctl list-timers --all --no-legend --no-pager 2>/dev/null
    """

    static let wireguardStatus = """
    ls /etc/wireguard/*.conf 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\\.conf$//'
    echo '---DUMP---'
    wg show all dump 2>/dev/null
    """
}

struct LinuxHost { var hostname = ""; var os = ""; var uptime: TimeInterval = 0 }
struct LinuxMem { var total = 0.0; var used = 0.0 }
struct LinuxFS { var device: String; var size: Int64; var used: Int64; var mount: String }
struct LinuxZPool { var name: String; var size: Int64; var allocated: Int64; var health: String }

struct PiholeStatus: Sendable {
    var enabled: Bool
    var queriesToday: Int?
    var blockedToday: Int?
    var percentBlocked: Double?
    var domainsOnBlocklist: Int?
}


struct PiholeDNSRecord: Sendable, Identifiable, Hashable {
    var ip: String
    var domain: String
    var id: String { domain }
}

struct LinuxVM: Sendable, Identifiable, Hashable {
    var name: String
    var state: String
    var autostart: Bool
    var vcpus: Int
    var memoryMiB: Int?
    var id: String { name }
    var isRunning: Bool { state == "running" }
}

enum LinuxParse {
    /// /proc/stat lines like "cpu0 103 0 55 …" (the aggregate "cpu " line is handled by `cpuTicks`).
    static func perCoreTicks(_ l: [String]) -> [String: (busy: Double, total: Double)] {
        var out: [String: (busy: Double, total: Double)] = [:]
        for line in l {
            let f = line.split(separator: " ", omittingEmptySubsequences: true)
            guard let name = f.first, name.hasPrefix("cpu"), name != "cpu", name.dropFirst(3).allSatisfy(\.isNumber) else { continue }
            let n = f.dropFirst().compactMap { Double($0) }
            guard n.count >= 4 else { continue }
            let idle = n[3] + (n.count > 4 ? n[4] : 0)
            let total = n.prefix(8).reduce(0, +)
            out[String(name)] = (total - idle, total)
        }
        return out
    }

    /// One "IP domain" pair per line, '#' comments allowed.
    static func localDNS(_ text: String) -> [PiholeDNSRecord] {
        text.split(separator: "\n").compactMap { raw in
            let line = raw.trimmingCharacters(in: .whitespaces)
            let p = line.split(separator: " ", omittingEmptySubsequences: true)
            guard !line.hasPrefix("#"), p.count >= 2 else { return nil }
            return PiholeDNSRecord(ip: String(p[0]), domain: String(p[1]))
        }.sorted { $0.domain.localizedCaseInsensitiveCompare($1.domain) == .orderedAscending }
    }

    static func vms(_ text: String) -> [LinuxVM] {
        text.split(separator: "\n").compactMap { raw in
            let p = raw.components(separatedBy: "|")
            guard p.count >= 6, p[0] == "VM" else { return nil }
            let kib = Int(p[5].filter(\.isNumber))   // dominfo reports KiB
            return LinuxVM(name: p[1].trimmingCharacters(in: .whitespaces), state: p[2].trimmingCharacters(in: .whitespaces).lowercased(),
                           autostart: ["enable", "yes"].contains(p[3].trimmingCharacters(in: .whitespaces).lowercased()),
                           vcpus: Int(p[4].trimmingCharacters(in: .whitespaces)) ?? 1, memoryMiB: kib.map { $0 / 1024 })
        }.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    static func sections(_ text: String) -> [String: [String]] {
        var out: [String: [String]] = [:]
        var current: String?
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            if line.hasPrefix("##") { current = String(line.dropFirst(2)); out[current!] = []; continue }
            if let c = current { out[c, default: []].append(line) }
        }
        return out
    }

    static func host(_ l: [String]) -> LinuxHost {
        var h = LinuxHost()
        guard l.count >= 5 else { return h }
        h.hostname = l[0]; h.os = l[1]
        h.uptime = Double(l[4]) ?? 0
        return h
    }

    static func cpuTicks(_ l: [String]) -> (busy: Double, total: Double)? {
        guard let line = l.first(where: { $0.hasPrefix("cpu ") }) else { return nil }
        let f = line.split(separator: " ").dropFirst().compactMap { Double($0) }
        guard f.count >= 4 else { return nil }
        let idle = f[3] + (f.count > 4 ? f[4] : 0)
        let total = f.prefix(8).reduce(0, +)
        return (total - idle, total)
    }

    static func mem(_ l: [String]) -> LinuxMem {
        var total = 0.0, available = 0.0
        for line in l {
            let p = line.split(separator: ":")
            guard p.count == 2, let kb = Double(p[1].split(separator: " ").first ?? "") else { continue }
            if p[0] == "MemTotal" { total = kb * 1024 }
            if p[0] == "MemAvailable" { available = kb * 1024 }
        }
        return LinuxMem(total: total, used: max(0, total - available))
    }

    /// Sums rx/tx bytes over physical-looking interfaces (skips loopback, veth, docker, bridges, VPN taps).
    static func net(_ l: [String]) -> (rx: Double, tx: Double) {
        var rx = 0.0, tx = 0.0
        for line in l {
            let p = line.split(separator: ":")
            guard p.count == 2 else { continue }
            let name = p[0].trimmingCharacters(in: .whitespaces)
            if name == "lo" || ["veth", "docker", "br-", "virbr", "tap", "tailscale", "wg"].contains(where: { name.hasPrefix($0) }) { continue }
            let f = p[1].split(separator: " ").compactMap { Double($0) }
            if f.count >= 9 { rx += f[0]; tx += f[8] }
        }
        return (rx, tx)
    }

    static func filesystems(_ l: [String]) -> [LinuxFS] {
        var seen = Set<String>()
        return l.compactMap { line in
            let f = line.split(separator: " ", maxSplits: 6, omittingEmptySubsequences: true).map(String.init)
            guard f.count >= 7, let size = Int64(f[2]), let used = Int64(f[3]), size > 0 else { return nil }
            if f[0].hasPrefix("/dev/loop") || f[6].hasPrefix("/var/lib/docker") || f[6].hasPrefix("/snap") || f[1] == "zfs" { return nil }
            guard seen.insert(f[0] + f[6]).inserted else { return nil }
            return LinuxFS(device: f[0], size: size, used: used, mount: f[6])
        }
    }

    static func zpools(_ l: [String]) -> [LinuxZPool] {
        l.compactMap { line in
            let f = line.components(separatedBy: "\t")
            guard f.count >= 5, let size = Int64(f[1]), let alloc = Int64(f[2]) else { return nil }
            return LinuxZPool(name: f[0], size: size, allocated: alloc, health: f[4])
        }
    }

    static func services(_ svc: [String]) -> [ServiceRow] {
        svc.compactMap { line in
            var f = line.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
            if f.first == "●" || f.first == "*" { f.removeFirst() }
            guard f.count >= 4, f[0].hasSuffix(".service") else { return nil }
            let name = String(f[0].dropLast(8))
            if name.contains("@") && f[2] != "failed" { return nil }
            return ServiceRow(id: name, name: name, displayName: name, state: f[2], isRunning: f[2] == "active" && f[3] == "running")
        }
    }

    static func processes(_ text: String) -> [ProcessRow] {
        text.split(separator: "\n").compactMap { raw in
            let f = raw.split(separator: " ", maxSplits: 4, omittingEmptySubsequences: true)
            guard f.count == 5, let pid = Int(f[0]), let cpu = Double(f[2]), let mem = Double(f[3]) else { return nil }
            return ProcessRow(pid: pid, name: String(f[4]), user: String(f[1]), cpu: cpu, mem: mem)
        }
    }

    static func pihole(_ text: String) -> PiholeStatus? {
        let parts = text.components(separatedBy: "---JSON---")
        let statusText = parts.first ?? ""
        guard statusText.localizedCaseInsensitiveContains("enabled") || statusText.localizedCaseInsensitiveContains("disabled") else { return nil }
        var s = PiholeStatus(enabled: !statusText.localizedCaseInsensitiveContains("disabled"))
        if parts.count > 1, let data = parts[1].trimmingCharacters(in: .whitespacesAndNewlines).data(using: .utf8),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            s.queriesToday = (obj["dns_queries_today"] as? NSNumber)?.intValue
            s.blockedToday = (obj["ads_blocked_today"] as? NSNumber)?.intValue
            s.percentBlocked = (obj["ads_percentage_today"] as? NSNumber)?.doubleValue
            s.domainsOnBlocklist = (obj["domains_being_blocked"] as? NSNumber)?.intValue
        }
        return s
    }

    /// `vcgencmd get_throttled`'s bitmask: low 4 bits are current state, high 4 (in the second nibble
    /// group) are "has happened since boot". We only surface the currently-active bits.
    static func throttleFlags(_ hex: String) -> [String] {
        guard let v = UInt32(hex, radix: 16) else { return [] }
        var flags: [String] = []
        if v & 0x1 != 0 { flags.append("Under-voltage") }
        if v & 0x2 != 0 { flags.append("ARM frequency capped") }
        if v & 0x4 != 0 { flags.append("Currently throttled") }
        if v & 0x8 != 0 { flags.append("Soft temperature limit active") }
        return flags
    }

    static func who(_ text: String) -> [LoggedInUser] {
        text.split(separator: "\n").compactMap { raw in
            let f = raw.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
            guard f.count >= 2 else { return nil }
            let host = f.first(where: { $0.hasPrefix("(") })?.trimmingCharacters(in: CharacterSet(charactersIn: "()")) ?? ""
            let dateFields = f.dropFirst(2).filter { !$0.hasPrefix("(") }
            return LoggedInUser(id: "\(f[0])-\(f[1])", name: f[0], terminal: f[1], host: host, loginTime: dateFields.joined(separator: " "), idle: "")
        }
    }

    static func aptUpdates(_ text: String) -> [UpdateItem] {
        text.split(separator: "\n").compactMap { raw in
            let line = String(raw)
            guard let slash = line.firstIndex(of: "/") else { return nil }
            let name = String(line[..<slash])
            let rest = line[line.index(after: slash)...].split(separator: " ")
            guard rest.count >= 2 else { return nil }
            let source = String(rest[0])
            return UpdateItem(id: name, name: name, newVersion: String(rest[1]), isSecurity: source.localizedCaseInsensitiveContains("security"))
        }
    }

    static func pacmanUpdates(_ text: String) -> [UpdateItem] {
        text.split(separator: "\n").compactMap { raw in
            let f = raw.split(separator: " ")
            guard f.count >= 4 else { return nil }
            return UpdateItem(id: String(f[0]), name: String(f[0]), newVersion: String(f[3]), isSecurity: false)
        }
    }
}
