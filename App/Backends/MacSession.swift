import Foundation

/// Manages a remote Mac over SSH ("Remote Login") using stock BSD tools. (Power/thermal, launchd
/// startup items, Homebrew updates etc. are a later phase; this covers host/CPU/mem/storage/services/processes.)
@MainActor @Observable
final class MacSession: MachineSession, ContainerHosting, ConsoleHosting, UserHosting, LogHosting, UpdateHosting, TailscaleHosting, WireGuardHosting {
    private let runner = SSHRunner()
    private var prevNet: (rx: Double, tx: Double, time: Date)?
    private(set) var hasDocker = false
    private(set) var hasTailscale = false
    private(set) var hasWireGuard = false

    var tailscaleStatus: TailscaleStatus?
    var tailscaleError: String?

    var wireguardTunnels: [WireGuardTunnel] = []
    var wireguardError: String?

    var containers: [DockerContainer] = []
    var containersLoaded = false
    var containersError: String?
    private let containerCollector = SSHRunner()
    private var containersTask: Task<Void, Never>?

    var startupItems: [MacStartupItem] = []
    var startupLoaded = false
    var software: [InstalledSoftware] = []
    var softwareLoaded = false
    var updates: [UpdateItem] = []
    var updatesChecked = false
    var brewOutdatedCount = 0
    var users: [LoggedInUser] = []
    var logLines: [String] = []

    var consoleIsAvailable: Bool { true }
    var consoleUnavailableReason: String { "" }

    override var navItems: [NavItem] {
        var items = Set(machine.kind.navItems)
        if hasDocker { items.insert(.containers) }
        if hasTailscale { items.insert(.tailscale) }
        if hasWireGuard { items.insert(.vpn) }
        return sortNavItems(items)
    }

    private var ssh: SSHSettings {
        SSHSettings(host: machine.host, port: machine.port, user: machine.user, password: MachineStore.secret(for: machine.id), useSudo: machine.useSudo)
    }

    override func poll() async -> Bool {
        if state != .connected { state = .connecting }
        let text: String
        do { text = try await runner.sh(MacScripts.sample, ssh) }
        catch { state = .failed(error.localizedDescription); return false }
        let sec = MacParse.sections(text)
        guard sec["END"] != nil, let hostLines = sec["HOST"], hostLines.count >= 5 else {
            state = .failed("Unexpected reply from the Mac. Is Remote Login enabled (System Settings > General > Sharing)?")
            return false
        }
        hostName = hostLines[0]
        hostOS = "\(hostLines[1]) \(hostLines[2])"
        hostUptime = Double(hostLines[3]) ?? 0

        let pageSize = Double((sec["PAGESIZE"]?.first).flatMap { Int($0) } ?? 4096)
        let (used, total) = MacParse.memory(sec["VMSTAT"] ?? [], pageSize: pageSize, memBytes: Double(hostLines[4]) ?? 0)
        memUsed = used; memTotal = total
        push(&memHistory, total > 0 ? used / total * 100 : nil)

        if let cpu = MacParse.cpu(sec["TOP"] ?? []) { cpuPercent = cpu }
        push(&cpuHistory, cpuPercent)

        storage = MacParse.filesystems(sec["DF"] ?? []).map {
            StorageItem(id: $0.mount, title: $0.mount, subtitle: $0.device, size: $0.size, used: $0.used)
        }

        services = MacParse.launchd(sec["LAUNCHD"] ?? []).sorted { $0.name < $1.name }
        servicesLoaded = true

        let n = MacParse.net(sec["NETSTAT"] ?? [])
        let now = Date.now
        if let p = prevNet {
            let dt = now.timeIntervalSince(p.time)
            if dt > 0.5 { netRxRate = max(0, n.rx - p.rx) / dt; netTxRate = max(0, n.tx - p.tx) / dt }
        }
        prevNet = (n.rx, n.tx, now)

        let feat = Set(sec["FEAT"] ?? [])
        hasDocker = feat.contains("DOCKER")
        hasTailscale = feat.contains("TAILSCALE")
        hasWireGuard = feat.contains("WIREGUARD")

        state = .connected
        lastRefresh = .now
        return true
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
            let text = try await containerCollector.sh(DockerParser.sampleScript, ssh, sudo: machine.useSudo, merge: true)
            containers = DockerParser.parse(text)
            containersError = nil
            containersLoaded = true
        } catch { containersError = error.localizedDescription }
    }

    func containerAction(_ c: DockerContainer, verb: String) async {
        guard ["start", "stop", "restart"].contains(verb) else { return }
        _ = try? await containerCollector.sh("docker \(verb) \(SSHRunner.quote(c.name))", ssh, sudo: machine.useSudo, merge: true)
        await loadContainers()
    }

    func containerLogs(_ c: DockerContainer) async -> String {
        (try? await containerCollector.sh("docker logs --tail 300 \(SSHRunner.quote(c.name))", ssh, sudo: machine.useSudo, merge: true)) ?? "Couldn't load logs."
    }

    override func closeBackend() async {
        await runner.close(); prevNet = nil
        containersTask?.cancel()
        await containerCollector.close()
    }

    // MARK: Power & Thermal

    var powerSource: String?
    var thermalCPUSpeedLimit: Double?
    var thermalSchedulerLimit: Double?
    var hasBattery = false
    var batteryPercent: Double?
    var batteryCharging = false
    var batteryCycles: Int?
    var batteryHealthPercent: Double?

    func refreshPowerThermal() async {
        guard let t = try? await runner.sh(MacScripts.power, ssh) else { return }
        let sec = MacParse.sections(t)
        let therm = sec["THERM"] ?? []
        thermalCPUSpeedLimit = MacParse.kv(therm, "CPU_Speed_Limit").flatMap(Double.init)
        thermalSchedulerLimit = MacParse.kv(therm, "CPU_Scheduler_Limit").flatMap(Double.init)
        powerSource = (sec["PS"] ?? []).first.flatMap { line in
            line.range(of: "'([^']+)'", options: .regularExpression).map { String(line[$0]).trimmingCharacters(in: CharacterSet(charactersIn: "'")) }
        }
        let batt = sec["BATT"] ?? []
        let maxCap = MacParse.kv(batt, "MaxCapacity").flatMap(Double.init) ?? 0
        hasBattery = maxCap > 0
        if hasBattery {
            let curCap = MacParse.kv(batt, "CurrentCapacity").flatMap(Double.init) ?? 0
            batteryPercent = min(100, curCap / maxCap * 100)
            batteryCharging = MacParse.kv(batt, "IsCharging") == "Yes"
            batteryCycles = MacParse.kv(batt, "CycleCount").flatMap(Int.init)
            if let design = MacParse.kv(batt, "DesignCapacity").flatMap(Double.init), design > 0 {
                batteryHealthPercent = min(100, maxCap / design * 100)
            }
        }
    }

    override func refreshServices() async { _ = await poll() }

    override func refreshProcesses() async {
        guard let t = try? await runner.sh(MacScripts.processes, ssh) else { return }
        processes = MacParse.processes(t)
    }

    override func serviceAction(_ s: ServiceRow, verb: String) async {
        let cmd: String
        switch verb {
        case "stop": cmd = "launchctl kickstart -k gui/$(id -u) \(SSHRunner.quote(s.name)) 2>&1 || launchctl stop \(SSHRunner.quote(s.name))"
        default: cmd = "launchctl kickstart -k gui/$(id -u) \(SSHRunner.quote(s.name))"
        }
        let out = try? await runner.sh(cmd, ssh, sudo: true, merge: true)
        if let out, out.lowercased().contains("error") { actionError = out }
        try? await Task.sleep(for: .seconds(1))
        _ = await poll()
    }

    override func killProcess(_ p: ProcessRow) async {
        _ = try? await runner.sh("kill -9 \(p.pid)", ssh, sudo: true, merge: true)
        await refreshProcesses()
    }

    override func reboot() async { _ = try? await runner.sh("shutdown -r now", ssh, sudo: true) }
    override func shutdown() async { _ = try? await runner.sh("shutdown -h now", ssh, sudo: true) }

    // MARK: Startup Items

    func refreshStartup() async {
        guard let t = try? await runner.sh(MacScripts.startup, ssh, sudo: true, merge: true) else { return }
        startupItems = MacParse.startupItems(t)
        startupLoaded = true
    }

    func setStartupEnabled(_ item: MacStartupItem, enabled: Bool) async {
        let cmd = enabled
            ? "launchctl enable \(item.domain)/\(SSHRunner.quote(item.label))"
            : "launchctl disable \(item.domain)/\(SSHRunner.quote(item.label))"
        _ = try? await runner.sh(cmd, ssh, sudo: true, merge: true)
        await refreshStartup()
    }

    // MARK: Installed Software

    func refreshSoftware() async {
        guard let t = try? await runner.sh(MacScripts.software, ssh) else { return }
        software = MacParse.software(t)
        softwareLoaded = true
    }

    // MARK: Updates (softwareupdate + Homebrew)

    func refreshUpdates() async {
        guard let t = try? await runner.sh(MacScripts.updates, ssh) else { return }
        let parts = t.components(separatedBy: "---BREW---")
        let brewItems = MacParse.brewOutdated(parts.count > 1 ? parts[1] : "")
        updates = MacParse.softwareUpdates(parts.first ?? "") + brewItems
        brewOutdatedCount = brewItems.count
        updatesChecked = true
    }

    func upgradeAll() async -> String {
        let out = (try? await runner.sh("brew upgrade 2>&1", ssh, sudo: false, merge: true)) ?? "Homebrew isn't installed, or the upgrade failed."
        await refreshUpdates()
        return "Note: macOS system updates still need \u{201C}softwareupdate -i -a\u{201D} run manually (it can require a restart). This ran \u{201C}brew upgrade\u{201D} for Homebrew packages:\n\n\(out)"
    }

    // MARK: Logs

    func refreshLogs(filter: String) async {
        let pred = filter.trimmingCharacters(in: .whitespaces)
        let cmd = pred.isEmpty
            ? "log show --last 15m --style compact --predicate 'messageType == 16 OR messageType == 17' 2>/dev/null | tail -300"
            : "log show --last 1h --style compact --predicate \(SSHRunner.quote("process == \"\(pred)\"")) 2>/dev/null | tail -300"
        let t = (try? await runner.sh(cmd, ssh, sudo: true, merge: true)) ?? "Couldn't load logs."
        logLines = t.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
    }

    // MARK: Tailscale

    func refreshTailscale() async {
        guard let t = try? await runner.sh("tailscale status --json", ssh) else { return }
        if let s = TailscaleParser.parse(t) { tailscaleStatus = s; tailscaleError = nil }
        else { tailscaleError = "Couldn't read Tailscale status." }
    }

    // MARK: WireGuard
    // Homebrew's prefix (and so WireGuard's config directory) differs between Apple Silicon and
    // Intel Macs, so every command resolves it with `brew --prefix` rather than a hardcoded path.

    func refreshWireGuard() async {
        guard let t = try? await runner.sh(MacScripts.wireguardStatus, ssh, sudo: true, merge: true) else { return }
        let parts = t.components(separatedBy: "---DUMP---")
        let confNames = (parts.first ?? "").split(separator: "\n").map(String.init)
        wireguardTunnels = WireGuardParser.tunnels(confNames: confNames, dump: parts.count > 1 ? parts[1] : "")
        wireguardError = nil
    }

    func addWireGuardTunnel(name: String, config: String) async -> String? {
        guard WireGuardParser.isValidName(name) else { return "Use letters, numbers, dashes and underscores only." }
        let b64 = Data(config.utf8).base64EncodedString()
        let script = """
        WGDIR="$(brew --prefix)/etc/wireguard"
        mkdir -p "$WGDIR"
        echo \(b64) | base64 -d > /tmp/wg-\(name).conf
        install -m 600 /tmp/wg-\(name).conf "$WGDIR/\(name).conf"
        rm -f /tmp/wg-\(name).conf
        """
        let out = (try? await runner.sh(script, ssh, sudo: true, merge: true)) ?? "Failed to write the config."
        await refreshWireGuard()
        return out.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : (out.lowercased().contains("error") || out.lowercased().contains("permission") ? out : nil)
    }

    func connectWireGuardTunnel(_ name: String) async {
        guard WireGuardParser.isValidName(name) else { return }
        let out = (try? await runner.sh("wg-quick up \"$(brew --prefix)/etc/wireguard/\(name).conf\"", ssh, sudo: true, merge: true)) ?? ""
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
        _ = try? await runner.sh("wg-quick down \(name) 2>/dev/null; rm -f \"$(brew --prefix)/etc/wireguard/\(name).conf\"", ssh, sudo: true, merge: true)
        await refreshWireGuard()
    }

    // MARK: Users / Console

    func refreshUsers() async {
        guard let t = try? await runner.sh("who", ssh) else { return }
        users = MacParse.who(t)
    }

    func runConsole(_ command: String) async -> String {
        (try? await runner.sh(command, ssh, sudo: true, merge: true)) ?? "Command failed."
    }
}

// MARK: - Remote script + parser

private enum MacScripts {
    static let sample = """
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

    static let processes = """
    ps -eo pid,pcpu,rss,comm -r | tail -n +2 | head -60
    """

    static let power = """
    echo '##THERM'
    pmset -g therm
    echo '##PS'
    pmset -g ps | head -1
    echo '##BATT'
    ioreg -rn AppleSmartBattery
    echo '##END'
    """

    static let startup = """
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

    static let software = """
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

    static let updates = """
    softwareupdate -l 2>&1
    echo '---BREW---'
    command -v brew >/dev/null 2>&1 && brew outdated --json=v2 2>/dev/null
    """

    static let wireguardStatus = """
    WGDIR="$(brew --prefix 2>/dev/null)/etc/wireguard"
    ls "$WGDIR"/*.conf 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\\.conf$//'
    echo '---DUMP---'
    wg show all dump 2>/dev/null
    """
}

private struct MacFS { var device: String; var size: Int64; var used: Int64; var mount: String }

struct MacStartupItem: Sendable, Identifiable, Hashable {
    var label: String
    var domain: String   // "gui/<uid>" or "system"
    var scope: String    // source directory, for display
    var disabled: Bool
    var id: String { domain + label }
}

private enum MacParse {
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

    /// `vm_stat` reports pages; "active + wired + compressed" approximates what Activity Monitor calls used.
    static func memory(_ lines: [String], pageSize: Double, memBytes: Double) -> (used: Double, total: Double) {
        var pages: [String: Double] = [:]
        for line in lines {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let key = String(line[..<colon]).trimmingCharacters(in: .whitespaces)
            let val = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "."))
            pages[key] = Double(val)
        }
        let active = pages["Pages active"] ?? 0
        let wired = pages["Pages wired down"] ?? 0
        let compressed = pages["Pages occupied by compressor"] ?? 0
        let used = (active + wired + compressed) * pageSize
        return (used, memBytes)
    }

    static func cpu(_ lines: [String]) -> Double? {
        guard let line = lines.first else { return nil }
        // "CPU usage: 12.5% user, 5.1% sys, 82.4% idle"
        let idle = line.components(separatedBy: ",").first { $0.contains("idle") }
        guard let idle, let pct = idle.split(separator: "%").first.flatMap({ Double($0.trimmingCharacters(in: .whitespaces)) }) else { return nil }
        return max(0, min(100, 100 - pct))
    }

    /// Local physical disks only: real block devices (`/dev/...`), not `devfs`, `map auto_home`,
    /// Finder-mounted network shares (`//user@host/share`), or CoreDevice's synthetic entries.
    static func filesystems(_ l: [String]) -> [MacFS] {
        l.compactMap { line in
            let f = line.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
            guard f.count >= 9, f[0].hasPrefix("/dev/"), let size1k = Int64(f[1]), let used1k = Int64(f[2]) else { return nil }
            let mount = f[8...].joined(separator: " ")
            if mount.hasPrefix("/System/Volumes") && mount != "/System/Volumes/Data" { return nil }
            if mount == "/Volumes/Recovery" { return nil }
            return MacFS(device: f[0], size: size1k * 1024, used: used1k * 1024, mount: mount)
        }
    }

    /// `netstat -ib` prints one row per address family per interface; only the `<Link#N>` row carries
    /// the real byte counters, always as the last 7 whitespace fields regardless of whether a MAC
    /// address column is present.
    static func net(_ l: [String]) -> (rx: Double, tx: Double) {
        let skip = ["lo", "gif", "stf", "utun", "awdl", "llw", "bridge", "anpi", "ap1", "p2p", "vmnet"]
        var rx = 0.0, tx = 0.0
        for line in l {
            let f = line.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
            guard f.count >= 9, f[2].hasPrefix("<Link") else { continue }
            if skip.contains(where: { f[0].hasPrefix($0) }) { continue }
            guard let ibytes = Double(f[f.count - 5]), let obytes = Double(f[f.count - 2]) else { continue }
            rx += ibytes; tx += obytes
        }
        return (rx, tx)
    }

    static func launchd(_ l: [String]) -> [ServiceRow] {
        l.compactMap { line in
            let f = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
            guard f.count >= 3 else { return nil }
            let label = f[2]
            guard !label.isEmpty, label.localizedCaseInsensitiveCompare("Label") != .orderedSame else { return nil }
            let running = Int(f[0]) != nil && f[0] != "-"
            return ServiceRow(id: label, name: label, displayName: label, state: running ? "running" : "stopped", isRunning: running, canControl: !label.hasPrefix("com.apple."))
        }
    }

    /// Reads a "Key = Value" line, matching both `ioreg`'s `"Key" = Value` and `pmset`'s unquoted `Key = Value`.
    static func kv(_ lines: [String], _ key: String) -> String? {
        for line in lines {
            let t = line.trimmingCharacters(in: .whitespaces)
            guard t.hasPrefix(key) || t.hasPrefix("\"\(key)\"") else { continue }
            guard let eq = t.firstIndex(of: "=") else { continue }
            return String(t[t.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
        }
        return nil
    }

    static func processes(_ text: String) -> [ProcessRow] {
        text.split(separator: "\n").compactMap { raw in
            let f = raw.split(separator: " ", maxSplits: 3, omittingEmptySubsequences: true)
            guard f.count == 4, let pid = Int(f[0]), let cpu = Double(f[1]), let rssKB = Double(f[2]) else { return nil }
            return ProcessRow(pid: pid, name: String(f[3]), cpu: cpu, mem: rssKB * 1024)
        }
    }

    static func startupItems(_ text: String) -> [MacStartupItem] {
        let parts = text.components(separatedBy: "##DISABLED")
        var disabled = Set<String>()
        if parts.count > 1 {
            for line in parts[1].split(separator: "\n") {
                let t = line.trimmingCharacters(in: .whitespaces)
                guard t.hasPrefix("\""), t.contains("=> disabled") else { continue }
                if let end = t.dropFirst().firstIndex(of: "\"") { disabled.insert(String(t[t.index(after: t.startIndex)..<end])) }
            }
        }
        var items: [MacStartupItem] = []
        for line in (parts.first ?? "").split(separator: "\n") {
            let f = line.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            guard f.count >= 3, f[0] == "ITEM" else { continue }
            items.append(MacStartupItem(label: f[2], domain: f[1], scope: f.count > 3 ? f[3] : "", disabled: disabled.contains(f[2])))
        }
        return items.sorted { $0.label.localizedCaseInsensitiveCompare($1.label) == .orderedAscending }
    }

    static func software(_ text: String) -> [InstalledSoftware] {
        text.split(separator: "\n").compactMap { raw in
            let f = raw.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            guard f.count >= 2 else { return nil }
            if f[0] == "APP" { return InstalledSoftware(name: f[1], version: f.count > 2 ? f[2] : "", publisher: "Application") }
            if f[0] == "BREW" {
                let parts = f[1].split(separator: " ", maxSplits: 1)
                return InstalledSoftware(name: String(parts.first ?? ""), version: parts.count > 1 ? String(parts[1]) : "", publisher: "Homebrew")
            }
            return nil
        }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    static func softwareUpdates(_ text: String) -> [UpdateItem] {
        if text.localizedCaseInsensitiveContains("No new software available") { return [] }
        return text.split(separator: "\n").compactMap { raw in
            let t = raw.trimmingCharacters(in: .whitespaces)
            guard t.hasPrefix("* Label:") else { return nil }
            let name = t.replacingOccurrences(of: "* Label:", with: "").trimmingCharacters(in: .whitespaces)
            return UpdateItem(id: name, name: name, newVersion: "", isSecurity: name.localizedCaseInsensitiveContains("security"))
        }
    }

    /// `brew outdated --json=v2`: {"formulae":[{"name","installed_versions":[...],"current_version",...}],"casks":[...]}
    static func brewOutdated(_ text: String) -> [UpdateItem] {
        guard let data = text.trimmingCharacters(in: .whitespacesAndNewlines).data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [] }
        func items(_ key: String) -> [UpdateItem] {
            ((obj[key] as? [[String: Any]]) ?? []).compactMap { r in
                let name = (r["name"] as? String) ?? (r["name"] as? [String])?.first
                guard let name else { return nil }
                let installed = (r["installed_versions"] as? [String])?.joined(separator: ", ") ?? ""
                let current = r["current_version"] as? String ?? ""
                let newVersion = installed.isEmpty ? current : "\(installed) → \(current)"
                return UpdateItem(id: "brew-\(name)", name: name, newVersion: newVersion, isSecurity: false, source: "Homebrew")
            }
        }
        return items("formulae") + items("casks")
    }

    static func who(_ text: String) -> [LoggedInUser] {
        text.split(separator: "\n").compactMap { raw in
            let f = raw.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
            guard f.count >= 2 else { return nil }
            return LoggedInUser(id: "\(f[0])-\(f[1])", name: f[0], terminal: f[1], host: "", loginTime: f.dropFirst(2).joined(separator: " "), idle: "")
        }
    }
}
