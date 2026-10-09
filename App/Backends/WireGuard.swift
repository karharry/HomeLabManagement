import Foundation

/// One configured tunnel (an `/etc/wireguard/<name>.conf`-style file). Covers both hand-written
/// WireGuard configs and ones exported from a VPN provider's dashboard — ProtonVPN, Mullvad etc. all
/// publish standard WireGuard `.conf` files, so importing one here works the same way.
struct WireGuardTunnel: Sendable, Identifiable, Hashable {
    var name: String
    var isUp: Bool
    var publicKey: String = ""
    var endpoint: String = ""
    var allowedIPs: String = ""
    var latestHandshake: String = ""
    var rxBytes: Int64 = 0
    var txBytes: Int64 = 0
    var id: String { name }
}

/// Conformed by every session kind that can manage WireGuard tunnels over SSH (Linux, macOS).
@MainActor
protocol WireGuardHosting: AnyObject {
    var hasWireGuard: Bool { get }
    var wireguardTunnels: [WireGuardTunnel] { get }
    var wireguardError: String? { get }
    func refreshWireGuard() async
    /// Returns an error message, or nil on success.
    func addWireGuardTunnel(name: String, config: String) async -> String?
    func connectWireGuardTunnel(_ name: String) async
    func disconnectWireGuardTunnel(_ name: String) async
    func removeWireGuardTunnel(_ name: String) async
}

enum WireGuardParser {
    /// A safe tunnel/interface name: letters, numbers, dashes, underscores only.
    static func isValidName(_ name: String) -> Bool {
        !name.isEmpty && name.count <= 40 && name.range(of: #"^[A-Za-z0-9_-]+$"#, options: .regularExpression) != nil
    }

    /// Parses `wg show all dump` (tab-separated; interface lines have 5 fields, peer lines 9) together
    /// with the list of configured `.conf` names, so tunnels that are configured but not currently up
    /// still show up (just with `isUp: false`).
    static func tunnels(confNames: [String], dump: String) -> [WireGuardTunnel] {
        var up: [String: WireGuardTunnel] = [:]
        for line in dump.split(separator: "\n") {
            let f = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
            guard let iface = f.first, !iface.isEmpty else { continue }
            if f.count == 5 {
                up[iface] = WireGuardTunnel(name: iface, isUp: true, publicKey: f[2])
            } else if f.count == 9, var t = up[iface] {
                t.endpoint = f[3] == "(none)" ? "" : f[3]
                t.allowedIPs = f[4]
                t.latestHandshake = (Int(f[5]) ?? 0) > 0 ? relativeTime(f[5]) : "Never"
                t.rxBytes = Int64(f[6]) ?? 0
                t.txBytes = Int64(f[7]) ?? 0
                up[iface] = t
            }
        }
        var all = confNames.map { up[$0] ?? WireGuardTunnel(name: $0, isUp: false) }
        for (name, t) in up where !confNames.contains(name) { all.append(t) }
        return all.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    private static func relativeTime(_ unixSeconds: String) -> String {
        guard let t = Double(unixSeconds), t > 0 else { return "Never" }
        let seconds = Date().timeIntervalSince1970 - t
        if seconds < 60 { return "just now" }
        if seconds < 3600 { return "\(Int(seconds / 60))m ago" }
        if seconds < 86400 { return "\(Int(seconds / 3600))h ago" }
        return "\(Int(seconds / 86400))d ago"
    }
}
