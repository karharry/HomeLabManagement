import Foundation

/// The six kinds of machine the app can manage, mirroring the combined Windows tool's `KINDS`.
/// Lives in Shared because the widget extension also needs it (for per-kind icon/accent).
enum MachineKind: String, Codable, CaseIterable, Sendable, Identifiable {
    case truenas, debian, raspios, arch, windows, macos
    var id: String { rawValue }

    var label: String {
        switch self {
        case .truenas: "TrueNAS"
        case .debian: "Debian / Ubuntu"
        case .raspios: "Raspberry Pi OS"
        case .arch: "Arch Linux"
        case .windows: "Windows"
        case .macos: "macOS"
        }
    }
    var icon: String {
        switch self {
        case .truenas: "server.rack"
        case .debian, .arch: "terminal"
        case .raspios: "cpu"
        case .windows: "pc"
        case .macos: "desktopcomputer"
        }
    }
    /// SSH is the only transport for these; TrueNAS instead talks JSON-RPC over WebSocket by default.
    var isPosixShell: Bool { self != .truenas && self != .windows }
    var defaultPort: Int {
        switch self {
        case .truenas: 443
        default: 22
        }
    }
}
