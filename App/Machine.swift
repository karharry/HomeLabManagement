import Foundation

enum RemoteDesktopProtocol { case rdp, vnc }

extension MachineKind {
    /// Windows gets RDP, macOS gets Screen Sharing (VNC) — both are first-class OS features needing no
    /// extra setup. Linux/TrueNAS are skipped by default since they're typically headless; nothing stops
    /// you from opening Screen Sharing's vnc:// manually if a box happens to run a desktop + VNC server.
    var remoteDesktopProtocol: RemoteDesktopProtocol? {
        switch self {
        case .windows: .rdp
        case .macos: .vnc
        default: nil
        }
    }

    /// Pages shown for this machine kind, in order (data, not a fixed enum — mirrors core.py's per-kind nav list).
    var navItems: [NavItem] {
        switch self {
        case .truenas:
            [.dashboard, .storage, .datasets, .shares, .network, .apps, .vms, .containers, .services,
             .dataProtection, .fan, .updates, .users, .console, .alerts]
        case .windows:
            [.dashboard, .storage, .cpu, .gpu, .sensors, .processes, .services, .startup, .scheduledTasks,
             .software, .updates, .logs, .users, .console, .alerts]
        case .macos:
            [.dashboard, .storage, .power, .processes, .services, .startup, .software, .updates,
             .logs, .users, .console, .alerts]
        default:
            [.dashboard, .storage, .processes, .services, .scheduledTasks, .updates, .logs, .users, .console, .alerts]
        }
    }
}

/// One machine the app can connect to. Carries the superset of fields every kind needs;
/// only the fields relevant to `kind` are read. Secrets live in the Keychain, keyed by `id`.
struct Machine: Codable, Identifiable, Hashable, Sendable {
    var id = UUID()
    var kind: MachineKind = .debian
    var name = ""
    var host = ""
    var port = 22
    var user = ""

    // TrueNAS
    var useTLS = true
    var allowSelfSigned = true
    var usePassword = false        // false = API key (stored as the primary secret)
    var sshEnabled = false
    var sshPort = 22
    var sshUser = ""
    var sshSudo = true
    var fanPort = 9595

    // Linux / Windows / macOS
    var useSudo = true

    // Windows: LibreHardwareMonitor/OpenHardwareMonitor "Remote Web Server" port (0 = not configured, try WMI only)
    var lhmPort = 0

    var webUrl = ""

    /// Explicit memberwise-style init, since the custom `init(from:)` below suppresses Swift's
    /// automatic one.
    init() {}

    /// Decodes leniently field-by-field so adding a new property here can never invalidate
    /// machines saved by an older build — a missing key just falls back to that field's default
    /// instead of failing the whole array's decode (which is what silently "lost" everyone's
    /// machines when `lhmPort` was added without this).
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        kind = try c.decodeIfPresent(MachineKind.self, forKey: .kind) ?? .debian
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        host = try c.decodeIfPresent(String.self, forKey: .host) ?? ""
        port = try c.decodeIfPresent(Int.self, forKey: .port) ?? 22
        user = try c.decodeIfPresent(String.self, forKey: .user) ?? ""
        useTLS = try c.decodeIfPresent(Bool.self, forKey: .useTLS) ?? true
        allowSelfSigned = try c.decodeIfPresent(Bool.self, forKey: .allowSelfSigned) ?? true
        usePassword = try c.decodeIfPresent(Bool.self, forKey: .usePassword) ?? false
        sshEnabled = try c.decodeIfPresent(Bool.self, forKey: .sshEnabled) ?? false
        sshPort = try c.decodeIfPresent(Int.self, forKey: .sshPort) ?? 22
        sshUser = try c.decodeIfPresent(String.self, forKey: .sshUser) ?? ""
        sshSudo = try c.decodeIfPresent(Bool.self, forKey: .sshSudo) ?? true
        fanPort = try c.decodeIfPresent(Int.self, forKey: .fanPort) ?? 9595
        useSudo = try c.decodeIfPresent(Bool.self, forKey: .useSudo) ?? true
        lhmPort = try c.decodeIfPresent(Int.self, forKey: .lhmPort) ?? 0
        webUrl = try c.decodeIfPresent(String.self, forKey: .webUrl) ?? ""
    }

    var displayName: String {
        let n = name.trimmingCharacters(in: .whitespaces)
        return n.isEmpty ? (host.isEmpty ? kind.label : host) : n
    }
    var pinKey: String { "\(host):\(kind == .truenas ? port : port)" }
    var isValid: Bool {
        guard !host.trimmingCharacters(in: .whitespaces).isEmpty else { return false }
        if kind == .truenas { return true }
        return !user.trimmingCharacters(in: .whitespaces).isEmpty
    }
}

enum MachineStore {
    private static let key = "machines.v1"
    private static let selectedKey = "selectedMachine"

    static func load() -> [Machine] {
        guard let data = UserDefaults.standard.data(forKey: key) else { return [] }
        return (try? JSONDecoder().decode([Machine].self, from: data)) ?? []
    }

    static func save(_ machines: [Machine]) {
        if let data = try? JSONEncoder().encode(machines) { UserDefaults.standard.set(data, forKey: key) }
    }

    /// Primary secret: TrueNAS API key/password, or the SSH password for every other kind.
    static func secret(for id: UUID) -> String { Keychain.get("secret.\(id.uuidString)") }
    static func setSecret(_ v: String, for id: UUID) { Keychain.set(v, for: "secret.\(id.uuidString)") }

    /// TrueNAS's secondary SSH password (used for VM stats / containers / fan control alongside the API).
    static func sshPassword(for id: UUID) -> String { Keychain.get("ssh.\(id.uuidString)") }
    static func setSSHPassword(_ v: String, for id: UUID) { Keychain.set(v, for: "ssh.\(id.uuidString)") }

    static func fanPassword(for id: UUID) -> String { Keychain.get("fan.\(id.uuidString)") }
    static func setFanPassword(_ v: String, for id: UUID) { Keychain.set(v, for: "fan.\(id.uuidString)") }

    /// ProtonVPN CLI credentials (Linux only — stored independently of the machine's own login,
    /// since it authenticates a separate account on the remote machine's `protonvpn-cli`).
    static func protonUser(for id: UUID) -> String { Keychain.get("protonuser.\(id.uuidString)") }
    static func setProtonUser(_ v: String, for id: UUID) { Keychain.set(v, for: "protonuser.\(id.uuidString)") }
    static func protonPassword(for id: UUID) -> String { Keychain.get("proton.\(id.uuidString)") }
    static func setProtonPassword(_ v: String, for id: UUID) { Keychain.set(v, for: "proton.\(id.uuidString)") }

    static func forgetSecrets(for id: UUID) {
        Keychain.remove("secret.\(id.uuidString)")
        Keychain.remove("ssh.\(id.uuidString)")
        Keychain.remove("fan.\(id.uuidString)")
        Keychain.remove("protonuser.\(id.uuidString)")
        Keychain.remove("proton.\(id.uuidString)")
    }

    static var selectedID: UUID? {
        get { UserDefaults.standard.string(forKey: selectedKey).flatMap(UUID.init) }
        set { UserDefaults.standard.set(newValue?.uuidString, forKey: selectedKey) }
    }
}
