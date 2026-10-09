import SwiftUI

struct SettingsPage: View {
    @AppStorage("openAtLogin") private var openAtLogin = false
    @AppStorage("openMinimized") private var openMinimized = false
    @State private var loginItemError: String?

    var body: some View {
        Form {
            Section {
                Toggle("Open at Login", isOn: $openAtLogin)
                    .onChange(of: openAtLogin) { _, new in
                        if !LoginItem.setEnabled(new) {
                            loginItemError = "Couldn't update the Login Items setting. You can add or remove it directly in System Settings \u{2192} General \u{2192} Login Items."
                            openAtLogin = LoginItem.isEnabled
                        } else {
                            loginItemError = nil
                        }
                    }
                Toggle("Open Minimized", isOn: $openMinimized)
                Text("When on, HomeLab Management starts minimized to the Dock instead of opening a window right away.")
                    .font(.caption).foregroundStyle(.secondary)
                if let loginItemError {
                    Text(loginItemError).font(.caption).foregroundStyle(.orange)
                }
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(width: 420, height: 220)
        .onAppear { openAtLogin = LoginItem.isEnabled }
    }
}
