import Foundation

// MARK: - Snapshot shared with the widget extension

struct StorageStat: Codable, Sendable, Identifiable, Hashable {
    var title: String
    var size: Int64
    var used: Int64
    var id: String { title }
    var usedFraction: Double { size > 0 ? Double(used) / Double(size) : 0 }
}

struct MachineSnapshot: Codable, Sendable, Identifiable {
    var id: UUID
    var kind: MachineKind
    var updated: Date
    var online: Bool
    var name: String
    var os: String
    var uptime: TimeInterval
    var cpu: Double?
    var memory: Double?        // 0...1
    var storage: [StorageStat]
    var alertCount: Int
    var isSelected: Bool

    static let placeholder = MachineSnapshot(
        id: UUID(), kind: .truenas, updated: .now, online: true, name: "tank", os: "TrueNAS-25.04", uptime: 86400 * 12,
        cpu: 18, memory: 0.46,
        storage: [StorageStat(title: "tank", size: 40_000_000_000_000, used: 23_000_000_000_000)],
        alertCount: 0, isSelected: true)
}

// MARK: - Shared store (App Group container)

enum SharedStore {
    static let groupID = Bundle.main.object(forInfoDictionaryKey: "AppGroupID") as? String
        ?? "group.com.karharry.homelabmanagement"

    private static var fileURL: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: groupID)?
            .appendingPathComponent("machines.json")
    }

    static func save(_ machines: [MachineSnapshot]) {
        guard let url = fileURL, let data = try? JSONEncoder().encode(machines) else { return }
        try? data.write(to: url, options: .atomic)
    }

    static func load() -> [MachineSnapshot] {
        guard let url = fileURL, let data = try? Data(contentsOf: url) else { return [] }
        return (try? JSONDecoder().decode([MachineSnapshot].self, from: data)) ?? []
    }
}

// MARK: - Formatting

enum Fmt {
    private static let byteFormatter: ByteCountFormatter = {
        let f = ByteCountFormatter()
        f.countStyle = .binary
        f.allowsNonnumericFormatting = false
        return f
    }()
    static func bytes(_ n: Int64) -> String { byteFormatter.string(fromByteCount: n) }
    static func bytes(_ n: Double) -> String { byteFormatter.string(fromByteCount: Int64(n.isFinite ? n : 0)) }
    static func uptime(_ s: TimeInterval) -> String {
        let d = Int(s) / 86400, h = Int(s) % 86400 / 3600, m = Int(s) % 3600 / 60
        if d > 0 { return "\(d)d \(h)h" }
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }
    static func rate(_ bytesPerSecond: Double?) -> String {
        guard let b = bytesPerSecond else { return "–" }
        return byteFormatter.string(fromByteCount: Int64(b)) + "/s"
    }
    static func percent(_ f: Double?) -> String {
        guard let f else { return "–" }
        return "\(Int(f.rounded()))%"
    }
    static func mhz(_ m: Double?) -> String {
        guard let m, m > 0 else { return "–" }
        return m >= 1000 ? String(format: "%.2f GHz", m / 1000) : "\(Int(m)) MHz"
    }
    static func temp(_ c: Double?) -> String {
        guard let c else { return "–" }
        return "\(Int(c.rounded()))°C"
    }
    static func count(_ n: Double?) -> String {
        guard let n else { return "–" }
        return n.formatted(.number.precision(.fractionLength(0)))
    }
}
