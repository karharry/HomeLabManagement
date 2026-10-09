import AppKit

/// Opens a machine's native remote-desktop session using whatever the OS already provides —
/// Microsoft's RDP client (via a generated .rdp file, the standard way to hand off to whichever one
/// is installed) for Windows, and Screen Sharing (built into every Mac, registers `vnc://`) for macOS.
enum RemoteDesktop {
    static func connect(to machine: Machine) {
        switch machine.kind.remoteDesktopProtocol {
        case .rdp: openRDP(host: machine.host, user: machine.user)
        case .vnc: openVNC(host: machine.host)
        case nil: break
        }
    }

    private static func openRDP(host: String, user: String) {
        let content = """
        full address:s:\(host):3389
        username:s:\(user)
        prompt for credentials:i:1
        authentication level:i:2
        """
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).rdp")
        try? content.write(to: url, atomically: true, encoding: .utf8)
        NSWorkspace.shared.open(url)
    }

    private static func openVNC(host: String) {
        guard let url = URL(string: "vnc://\(host)") else { return }
        NSWorkspace.shared.open(url)
    }
}
