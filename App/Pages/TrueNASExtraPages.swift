import SwiftUI

struct TrueNASDatasetsPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let tn = session as? TrueNASSession {
            List {
                Section("Datasets") {
                    if tn.datasets.isEmpty { Text("No data yet").foregroundStyle(.secondary) }
                    ForEach(tn.datasets) { d in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(d.name).font(.body)
                                Text([d.type, d.mountpoint].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(Fmt.bytes(d.used)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Section("Snapshots (most recent first)") {
                    if tn.snapshots.isEmpty { Text("No snapshots").foregroundStyle(.secondary) }
                    ForEach(tn.snapshots.prefix(200)) { s in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(s.name).font(.system(size: 12, design: .monospaced)).lineLimit(1)
                                Text(s.dataset).font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(Fmt.bytes(s.used)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("Datasets & Snapshots")
            .task { await tn.refreshDatasets() }
        } else { ProgressView() }
    }
}

struct TrueNASSharesPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let tn = session as? TrueNASSession {
            Group {
                if tn.shares.isEmpty {
                    ContentUnavailableView("No shares configured", systemImage: "externaldrive.connected.to.line.below")
                } else {
                    List(tn.shares) { s in
                        HStack {
                            Circle().fill(s.enabled ? .green : .gray).frame(width: 8, height: 8)
                            VStack(alignment: .leading) {
                                HStack {
                                    Text(s.name.isEmpty ? s.path : s.name).font(.body)
                                    Text(s.kind).font(.caption2).padding(.horizontal, 6).padding(.vertical, 1).background(.blue.opacity(0.15), in: Capsule()).foregroundStyle(.blue)
                                }
                                Text([s.path, s.comment].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Shares")
            .task { await tn.refreshShares() }
        } else { ProgressView() }
    }
}

struct TrueNASNetworkPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let tn = session as? TrueNASSession {
            List(tn.interfaces) { i in
                HStack {
                    Circle().fill(i.linkState.uppercased() == "LINK_STATE_UP" ? .green : .gray).frame(width: 8, height: 8)
                    VStack(alignment: .leading) {
                        Text(i.name).font(.body)
                        Text(i.addresses.isEmpty ? "No address" : i.addresses.joined(separator: ", ")).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Network")
            .task { await tn.refreshNetwork() }
        } else { ProgressView() }
    }
}

struct TrueNASDataProtectionPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let tn = session as? TrueNASSession {
            Group {
                if tn.protectionTasks.isEmpty {
                    ContentUnavailableView("Nothing configured", systemImage: "lock.shield", description: Text("No snapshot tasks, replication, rsync, cloud sync or cron jobs found."))
                } else {
                    List {
                        ForEach(Dictionary(grouping: tn.protectionTasks, by: \.kind).sorted { $0.key < $1.key }, id: \.key) { kind, items in
                            Section(kind) {
                                ForEach(items) { t in
                                    HStack {
                                        Circle().fill(t.enabled ? .green : .gray).frame(width: 8, height: 8)
                                        Text(t.summary).lineLimit(2)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Data Protection")
            .task { await tn.refreshDataProtection() }
        } else { ProgressView() }
    }
}

struct TrueNASUpdatesPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let tn = session as? TrueNASSession {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 12) {
                        Card(title: "Status") { Text(tn.updateStatus ?? "–").font(.system(size: 20, weight: .semibold, design: .rounded)) }
                        if let v = tn.updateVersion { Card(title: "Available version") { Text(v).font(.system(size: 18, weight: .semibold, design: .rounded)) } }
                    }
                    Text("Installing updates remotely isn't offered here — use the TrueNAS web UI for update installs, which may require a reboot.")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(20)
            }
            .navigationTitle("Updates")
            .task { await tn.refreshUpdateStatus() }
        } else { ProgressView() }
    }
}

struct TrueNASUsersPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let tn = session as? TrueNASSession {
            List(tn.userAccounts) { u in
                HStack {
                    Image(systemName: u.locked ? "lock.fill" : "person.crop.circle").foregroundStyle(u.locked ? Color.red : Color.secondary)
                    VStack(alignment: .leading) {
                        Text(u.username).font(.body)
                        Text([u.fullName, "uid \(u.uid)"].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Users & Groups")
            .task { await tn.refreshUserAccounts() }
        } else { ProgressView() }
    }
}
