import SwiftUI

struct MacStartupPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let m = session as? MacSession {
            Group {
                if !m.startupLoaded {
                    ProgressView("Loading startup items…")
                } else if m.startupItems.isEmpty {
                    ContentUnavailableView("No startup items found", systemImage: "power")
                } else {
                    List(m.startupItems) { item in
                        HStack {
                            Image(systemName: item.disabled ? "pause.circle" : "checkmark.circle").foregroundStyle(item.disabled ? Color.secondary : Color.green)
                            VStack(alignment: .leading) {
                                Text(item.label).font(.body).lineLimit(1)
                                Text("\(item.domain == "system" ? "System" : "User") · \(item.scope)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer()
                            Button(item.disabled ? "Enable" : "Disable") { Task { await m.setStartupEnabled(item, enabled: item.disabled) } }
                                .controlSize(.small)
                        }
                    }
                }
            }
            .navigationTitle("Startup Items")
            .task { await m.refreshStartup() }
        } else { ProgressView() }
    }
}

struct MacSoftwarePage: View {
    @Environment(MachineSession.self) private var session
    @State private var filter = ""

    var body: some View {
        if let m = session as? MacSession {
            let visible = filter.isEmpty ? m.software : m.software.filter { $0.name.localizedCaseInsensitiveContains(filter) }
            Group {
                if !m.softwareLoaded {
                    ProgressView("Loading installed software…")
                } else {
                    Table(visible) {
                        TableColumn("Name", value: \.name)
                        TableColumn("Version", value: \.version)
                        TableColumn("Source", value: \.publisher)
                    }
                }
            }
            .navigationTitle("Installed Software")
            .searchable(text: $filter)
            .task { await m.refreshSoftware() }
        } else { ProgressView() }
    }
}
