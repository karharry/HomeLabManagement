import AppKit
import ServiceManagement

/// Minimizes the main window right after launch when "Open Minimized" is on, and nothing else —
/// closing the window already keeps the app running in the Dock (standard macOS app lifecycle),
/// so there's no separate "quit on close" behavior to manage here.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        guard UserDefaults.standard.bool(forKey: "openMinimized") else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            NSApp.windows.forEach { $0.miniaturize(nil) }
        }
    }
}

enum LoginItem {
    static var isEnabled: Bool { SMAppService.mainApp.status == .enabled }

    @discardableResult
    static func setEnabled(_ enabled: Bool) -> Bool {
        do {
            if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            return true
        } catch {
            return false
        }
    }
}
