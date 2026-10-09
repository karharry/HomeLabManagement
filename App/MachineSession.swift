import Foundation
import Observation
import WidgetKit

enum ConnectionState: Equatable {
    case disconnected, connecting, connected
    case failed(String)
}

enum NavItem: String, Identifiable, Hashable, CaseIterable {
    case dashboard = "Dashboard", storage = "Storage", processes = "Processes", services = "Services", alerts = "Alerts"
    case apps = "Apps", vms = "VMs", containers = "Containers", fan = "Fan Control"
    case pihole = "Pi-hole", pikvm = "PiKVM", tailscale = "Tailscale"
    case cpu = "CPU", gpu = "GPU", sensors = "Sensors"
    case power = "Power & Thermal"
    case console = "Console", logs = "Logs", users = "Users", updates = "Updates"
    case startup = "Startup Items", scheduledTasks = "Scheduled Tasks", software = "Installed Software"
    case datasets = "Datasets & Snapshots", shares = "Shares", network = "Network", dataProtection = "Data Protection"
    case raspberryPi = "Raspberry Pi"
    case vpn = "VPN"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .dashboard: "gauge.with.dots.needle.67percent"
        case .storage: "internaldrive"
        case .processes: "list.number"
        case .services: "switch.2"
        case .alerts: "bell"
        case .apps: "square.grid.2x2"
        case .vms: "display"
        case .containers: "shippingbox"
        case .fan: "fan"
        case .pihole: "shield.lefthalf.filled"
        case .pikvm: "keyboard"
        case .tailscale: "network"
        case .cpu: "cpu"
        case .gpu: "display"
        case .sensors: "thermometer.medium"
        case .power: "bolt.heart"
        case .console: "terminal"
        case .logs: "doc.text.magnifyingglass"
        case .users: "person.2"
        case .updates: "arrow.down.circle"
        case .startup: "power"
        case .scheduledTasks: "clock.arrow.circlepath"
        case .software: "shippingbox.fill"
        case .datasets: "folder.badge.gearshape"
        case .shares: "externaldrive.connected.to.line.below"
        case .network: "network"
        case .dataProtection: "lock.shield"
        case .raspberryPi: "cpu"
        case .vpn: "lock.shield.fill"
        }
    }
    var urlName: String { rawValue.lowercased().replacingOccurrences(of: " ", with: "") }
}

/// Generalized storage unit: a ZFS pool, a Windows volume, or a Linux/macOS filesystem.
struct StorageItem: Sendable, Identifiable, Hashable {
    var id: String
    var title: String
    var subtitle: String = ""
    var size: Int64
    var used: Int64
    var healthy: Bool = true
    var statusText: String = ""
    var usedFraction: Double { size > 0 ? Double(used) / Double(size) : 0 }
}

/// Generalized control-plane service: a systemd unit, a Windows service, a launchd job, or a TrueNAS service/app.
struct ServiceRow: Sendable, Identifiable, Hashable {
    var id: String
    var name: String
    var displayName: String
    var state: String
    var startMode: String = ""
    var isRunning: Bool
    var canControl: Bool = true
}

struct ProcessRow: Sendable, Identifiable, Hashable {
    var pid: Int
    var name: String
    var user: String = ""
    var cpu: Double
    var mem: Double
    var id: Int { pid }
}

struct AlertItem: Identifiable, Sendable, Hashable {
    enum Level: Int, Sendable, Comparable {
        case info, warning, critical
        static func < (a: Level, b: Level) -> Bool { a.rawValue < b.rawValue }
    }
    var id: String
    var level: Level
    var title: String
    var detail: String
}

/// Live state and polling for one managed machine. Concrete subclasses (TrueNASSession, LinuxSession,
/// WindowsSession, MacSession) implement the kind-specific transport and parsing; this base class holds
/// the state every page reads and the alerts/snapshot logic that's the same for every kind.
@MainActor @Observable
class MachineSession: Identifiable {
    static let historyLength = 90

    var machine: Machine
    /// The selected machine polls quickly; the others poll slowly in the background.
    var isForeground = false

    var id: UUID { machine.id }
    var kind: MachineKind { machine.kind }

    // Live state, filled in by `poll()`
    var state: ConnectionState = .disconnected
    var hostName = ""
    var hostOS = ""
    var hostUptime: TimeInterval = 0
    var cpuPercent: Double?
    var cpuHistory: [Double] = []
    var memUsed: Double = 0
    var memTotal: Double = 0
    var memHistory: [Double] = []
    var storage: [StorageItem] = []
    /// nil where the kind doesn't report it yet (shown as "–" rather than omitted, so overview cards stay uniform).
    var gpuPercent: Double?
    var netRxRate: Double?     // bytes/sec
    var netTxRate: Double?     // bytes/sec
    var extraAlerts: [AlertItem] = []
    var lastRefresh: Date?
    var actionError: String?

    // Loaded on demand
    var services: [ServiceRow] = []
    var servicesLoaded = false
    var processes: [ProcessRow] = []

    private var supervisor: Task<Void, Never>?

    init(machine: Machine) { self.machine = machine }

    var displayName: String { machine.displayName }
    /// Overridden by subclasses that add feature-gated pages (e.g. Linux's Pi-hole/PiKVM/Tailscale/Containers).
    var navItems: [NavItem] { machine.kind.navItems }
    var isOnline: Bool { state == .connected }
    var isConfigured: Bool { machine.isValid }
    var memUsedFraction: Double { memTotal > 0 ? memUsed / memTotal : 0 }
    var netTotalRate: Double? {
        guard netRxRate != nil || netTxRate != nil else { return nil }
        return (netRxRate ?? 0) + (netTxRate ?? 0)
    }

    var alerts: [AlertItem] {
        var a = extraAlerts
        if case .failed(let m) = state, lastRefresh != nil {
            a.append(.init(id: "offline", level: .critical, title: "Can't reach \(displayName)", detail: m))
        }
        for item in storage where item.usedFraction >= 0.85 {
            a.append(.init(id: "storage-\(item.id)", level: item.usedFraction >= 0.95 ? .critical : .warning,
                           title: "\(item.title) is \(Int(item.usedFraction * 100))% full",
                           detail: "\(Fmt.bytes(item.size - item.used)) free"))
        }
        for item in storage where !item.healthy {
            a.append(.init(id: "unhealthy-\(item.id)", level: .critical, title: "\(item.title): \(item.statusText)", detail: item.subtitle))
        }
        if memTotal > 0, memUsedFraction >= 0.92 {
            a.append(.init(id: "mem", level: .warning, title: "Memory is \(Int(memUsedFraction * 100))% used", detail: "\(Fmt.bytes(memTotal - memUsed)) available"))
        }
        return a.sorted { $0.level > $1.level }
    }

    var snapshot: MachineSnapshot {
        MachineSnapshot(id: id, kind: kind, updated: lastRefresh ?? .distantPast, online: isOnline, name: displayName,
                        os: hostOS, uptime: hostUptime, cpu: cpuPercent, memory: memTotal > 0 ? memUsedFraction : nil,
                        storage: storage.map { StorageStat(title: $0.title, size: $0.size, used: $0.used) },
                        alertCount: alerts.count, isSelected: isForeground)
    }

    // MARK: Lifecycle (driven by the base class; subclasses only implement `poll()`)

    func start() {
        supervisor?.cancel()
        guard isConfigured else { state = .disconnected; return }
        supervisor = Task { [weak self] in
            var failures = 0
            while !Task.isCancelled {
                guard let self else { return }
                let ok = await self.poll()
                if Task.isCancelled { return }
                failures = ok ? 0 : failures + 1
                let base = self.isForeground ? 4.0 : 15.0
                try? await Task.sleep(for: .seconds(ok ? base : min(Double(failures) * 5 + base, 60)))
            }
        }
    }

    func stop() {
        supervisor?.cancel(); supervisor = nil
        Task { await closeBackend() }
        state = .disconnected
    }

    func forgetHostKey() { UserDefaults.standard.removeObject(forKey: "sshHostKey.\(machine.host):\(effectiveSSHPort)") }
    var effectiveSSHPort: Int { kind == .truenas ? machine.sshPort : machine.port }

    func push(_ arr: inout [Double], _ v: Double?, limit: Int = historyLength) {
        guard let v else { return }
        arr.append(v)
        if arr.count > limit { arr.removeFirst(arr.count - limit) }
    }

    @discardableResult
    func performAction(_ out: String, successContains: String? = nil) -> Bool {
        let trimmed = out.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("ERROR:") { actionError = String(trimmed.dropFirst(6)).trimmingCharacters(in: .whitespaces); return false }
        if let needle = successContains, !trimmed.contains(needle) { actionError = trimmed.isEmpty ? "The machine didn't confirm the action." : trimmed; return false }
        actionError = nil
        return true
    }

    // MARK: Overridden by subclasses

    /// One sample. Returns false if the machine couldn't be reached. Must set `state`/`lastRefresh`.
    func poll() async -> Bool { false }
    func closeBackend() async {}
    func refreshServices() async {}
    func refreshProcesses() async {}
    func serviceAction(_ s: ServiceRow, verb: String) async {}
    func killProcess(_ p: ProcessRow) async {}
    func reboot() async {}
    func shutdown() async {}
}

extension MachineSession {
    /// Builds the right concrete session for a machine's kind.
    static func make(for machine: Machine) -> MachineSession {
        switch machine.kind {
        case .truenas: TrueNASSession(machine: machine)
        case .debian, .raspios, .arch: LinuxSession(machine: machine)
        case .windows: WindowsSession(machine: machine)
        case .macos: MacSession(machine: machine)
        }
    }
}
