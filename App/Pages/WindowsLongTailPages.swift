import SwiftUI

struct WindowsStartupPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let w = session as? WindowsSession {
            Group {
                if !w.startupLoaded {
                    ProgressView("Loading startup items…")
                } else {
                    List(w.startupItems) { item in
                        HStack {
                            Image(systemName: item.disabled ? "pause.circle" : "checkmark.circle").foregroundStyle(item.disabled ? Color.secondary : Color.green)
                            VStack(alignment: .leading) {
                                Text(item.name).font(.body)
                                Text("\(item.sourceLabel) · \(item.scope)\(item.publisher.isEmpty ? "" : " · \(item.publisher)")").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button(item.disabled ? "Enable" : "Disable") { Task { await w.setStartup(item, disabled: !item.disabled) } }
                                .controlSize(.small)
                        }
                    }
                }
            }
            .navigationTitle("Startup Items")
            .task { await w.refreshStartup() }
        } else { ProgressView() }
    }
}

struct WindowsScheduledTasksPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let w = session as? WindowsSession {
            List(w.scheduledTasks) { t in
                HStack {
                    Circle().fill(t.state == "Ready" || t.state == "Running" ? .green : .gray).frame(width: 8, height: 8)
                    VStack(alignment: .leading) {
                        Text(t.name).font(.body)
                        Text(t.path).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Menu {
                        Button("Run Now") { Task { await w.scheduledTaskAction(t, verb: "run") } }
                        if t.state == "Disabled" { Button("Enable") { Task { await w.scheduledTaskAction(t, verb: "enable") } } }
                        else { Button("Disable") { Task { await w.scheduledTaskAction(t, verb: "disable") } } }
                    } label: { Image(systemName: "ellipsis.circle") }.menuIndicator(.hidden).fixedSize()
                }
            }
            .navigationTitle("Scheduled Tasks")
            .task { await w.refreshScheduledTasks() }
        } else { ProgressView() }
    }
}

struct WindowsSoftwarePage: View {
    @Environment(MachineSession.self) private var session
    @State private var filter = ""

    var body: some View {
        if let w = session as? WindowsSession {
            let visible = filter.isEmpty ? w.software : w.software.filter { $0.name.localizedCaseInsensitiveContains(filter) }
            Group {
                if !w.softwareLoaded {
                    ProgressView("Loading installed software…")
                } else {
                    Table(visible) {
                        TableColumn("Name", value: \.name)
                        TableColumn("Version", value: \.version)
                        TableColumn("Publisher", value: \.publisher)
                    }
                }
            }
            .navigationTitle("Installed Software")
            .searchable(text: $filter)
            .task { await w.refreshSoftware() }
        } else { ProgressView() }
    }
}

struct WindowsUpdatesPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let w = session as? WindowsSession {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 12) {
                        Card(title: "Installed hotfixes") {
                            Text(w.hotfixCount.map { "\($0)" } ?? "–").font(.system(size: 24, weight: .semibold, design: .rounded))
                        }
                        Card(title: "Most recent") { Text(w.hotfixLastInstalled ?? "–").font(.system(size: 18, weight: .semibold, design: .rounded)) }
                        Card(title: "Windows Update service") {
                            Text(w.wuauservRunning == true ? "Running" : "Stopped")
                                .font(.system(size: 20, weight: .semibold, design: .rounded))
                                .foregroundStyle(w.wuauservRunning == true ? .green : .secondary)
                        }
                    }
                    Text("Installing updates remotely over SSH isn't offered here — use Windows Update in Settings on the machine itself, or your usual management tooling.")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(20)
            }
            .navigationTitle("Updates")
            .task { await w.refreshWindowsUpdates() }
        } else { ProgressView() }
    }
}

struct WindowsEventLogPage: View {
    @Environment(MachineSession.self) private var session
    @State private var logName = ""

    var body: some View {
        if let w = session as? WindowsSession {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    TextField("Log name (default: Application, System)", text: $logName).textFieldStyle(.roundedBorder)
                    Button("Reload") { Task { await w.refreshEventLog(filter: logName) } }
                }
                List(w.eventLog) { e in
                    HStack(alignment: .top) {
                        Image(systemName: e.level == "Error" ? "xmark.octagon.fill" : (e.level == "Warning" ? "exclamationmark.triangle.fill" : "info.circle.fill"))
                            .foregroundStyle(e.level == "Error" ? .red : (e.level == "Warning" ? .orange : .blue))
                        VStack(alignment: .leading) {
                            Text(e.message).font(.body).lineLimit(2)
                            Text("\(e.time) · \(e.log)").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }.padding(16)
                .navigationTitle("Event Log")
                .task { await w.refreshEventLog(filter: logName) }
        } else { ProgressView() }
    }
}
