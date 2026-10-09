import SwiftUI

// MARK: - Console

struct ConsolePage: View {
    @Environment(MachineSession.self) private var session
    @State private var command = ""
    @State private var output = ""
    @State private var running = false
    @State private var history: [String] = []

    var body: some View {
        if let c = session as? any ConsoleHosting {
            VStack(alignment: .leading, spacing: 10) {
                if !c.consoleIsAvailable {
                    ContentUnavailableView("Console unavailable", systemImage: "terminal", description: Text(c.consoleUnavailableReason))
                } else {
                    ScrollView {
                        Text(output.isEmpty ? "Run a command to see its output here." : output)
                            .font(.system(size: 12, design: .monospaced)).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }.padding(10).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                    HStack {
                        TextField("Command", text: $command, prompt: Text("e.g. uptime")).textFieldStyle(.roundedBorder)
                            .onSubmit { Task { await run(c) } }
                        Button("Run") { Task { await run(c) } }.disabled(command.trimmingCharacters(in: .whitespaces).isEmpty || running)
                        if running { ProgressView().controlSize(.small) }
                    }
                    if !history.isEmpty {
                        HStack {
                            Text("Recent:").font(.caption).foregroundStyle(.secondary)
                            ForEach(history.suffix(5), id: \.self) { h in
                                Button(h) { command = h }.buttonStyle(.bordered).controlSize(.mini)
                            }
                        }
                    }
                }
            }.padding(16)
                .navigationTitle("Console")
        } else { ProgressView() }
    }

    private func run(_ c: any ConsoleHosting) async {
        let cmd = command.trimmingCharacters(in: .whitespaces)
        guard !cmd.isEmpty else { return }
        running = true
        output = await c.runConsole(cmd)
        if !history.contains(cmd) { history.append(cmd) }
        running = false
    }
}

// MARK: - Users

struct UsersPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let u = session as? any UserHosting {
            Group {
                if u.users.isEmpty {
                    ContentUnavailableView("No active sessions", systemImage: "person.2", description: Text("Nobody appears to be logged in right now."))
                } else {
                    List(u.users) { row in
                        HStack {
                            Image(systemName: "person.crop.circle").foregroundStyle(.secondary)
                            VStack(alignment: .leading) {
                                Text(row.name).font(.body.weight(.medium))
                                Text([row.terminal, row.host, row.loginTime].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Users")
            .task { await every(30) { await u.refreshUsers() } }
        } else { ProgressView() }
    }
}

// MARK: - Logs

struct LogsPage: View {
    @Environment(MachineSession.self) private var session
    @State private var filter = ""

    var body: some View {
        if let l = session as? any LogHosting {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    TextField("Filter (unit/service name, optional)", text: $filter).textFieldStyle(.roundedBorder)
                    Button("Reload") { Task { await l.refreshLogs(filter: filter) } }
                }
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 1) {
                        ForEach(Array(l.logLines.enumerated()), id: \.offset) { _, line in
                            Text(line).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }.padding(8).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
            }.padding(16)
                .navigationTitle("Logs")
                .task { await l.refreshLogs(filter: filter) }
        } else { ProgressView() }
    }
}

// MARK: - Updates

struct UpdatesPage: View {
    @Environment(MachineSession.self) private var session
    @State private var upgrading = false
    @State private var result: String?

    var body: some View {
        if let u = session as? any UpdateHosting {
            Group {
                if !u.updatesChecked {
                    ProgressView("Checking for updates…")
                } else if u.updates.isEmpty {
                    ContentUnavailableView("Up to date", systemImage: "checkmark.seal", description: Text("No updates are pending."))
                } else {
                    List {
                        if let result { Text(result).font(.caption.monospaced()).textSelection(.enabled).lineLimit(10) }
                        ForEach(groups(u.updates), id: \.0) { source, items in
                            Section(source) {
                                ForEach(items) { item in
                                    HStack {
                                        VStack(alignment: .leading) {
                                            Text(item.name).font(.body)
                                            Text(item.newVersion).font(.caption).foregroundStyle(.secondary)
                                        }
                                        Spacer()
                                        if item.isSecurity { Text("security").font(.caption2).padding(.horizontal, 6).padding(.vertical, 2).background(.red.opacity(0.15), in: Capsule()).foregroundStyle(.red) }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Updates")
            .toolbar {
                ToolbarItem {
                    Button("Upgrade All") { Task { upgrading = true; result = await u.upgradeAll(); upgrading = false } }
                        .disabled(upgrading || u.updates.isEmpty)
                }
            }
            .task { await u.refreshUpdates() }
        } else { ProgressView() }
    }

    private func groups(_ updates: [UpdateItem]) -> [(String, [UpdateItem])] {
        let dict = Dictionary(grouping: updates) { $0.source.isEmpty ? "System" : $0.source }
        return dict.sorted { $0.key < $1.key }.map { ($0.key, $0.value.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }) }
    }
}
