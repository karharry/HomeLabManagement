import SwiftUI

// MARK: - Pi-hole

struct PiholePage: View {
    @Environment(MachineSession.self) private var session
    @State private var disabling = false

    var body: some View {
        if let l = session as? LinuxSession {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let e = l.piholeError {
                        Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.callout)
                    }
                    if let s = l.piholeStatus {
                        HStack(spacing: 12) {
                            Card(title: "Blocking") {
                                Text(s.enabled ? "Enabled" : "Disabled").font(.system(size: 24, weight: .semibold, design: .rounded))
                                    .foregroundStyle(s.enabled ? .green : .red)
                                HStack {
                                    if s.enabled {
                                        Menu("Disable") {
                                            Button("For 30 seconds") { Task { await toggle(l, enable: false, duration: "30s") } }
                                            Button("For 5 minutes") { Task { await toggle(l, enable: false, duration: "5m") } }
                                            Button("Indefinitely") { Task { await toggle(l, enable: false, duration: nil) } }
                                        }
                                    } else {
                                        Button("Enable") { Task { await toggle(l, enable: true, duration: nil) } }
                                    }
                                }.controlSize(.small)
                            }
                            if let q = s.queriesToday { Card(title: "Queries today") { Text("\(q)").font(.system(size: 24, weight: .semibold, design: .rounded)) } }
                            if let b = s.blockedToday { Card(title: "Blocked today") { Text("\(b)").font(.system(size: 24, weight: .semibold, design: .rounded)).foregroundStyle(.orange) } }
                            if let p = s.percentBlocked { Card(title: "% blocked") { Text(String(format: "%.1f%%", p)).font(.system(size: 24, weight: .semibold, design: .rounded)) } }
                        }
                        if let d = s.domainsOnBlocklist {
                            Text("\(d) domains on the blocklist").font(.caption).foregroundStyle(.secondary)
                        }
                    } else if l.piholeError == nil {
                        ProgressView("Loading Pi-hole status…")
                    }
                    if let url = URL(string: session.machine.webUrl.isEmpty ? "http://\(session.machine.host)/admin/" : session.machine.webUrl) {
                        Link("Open Pi-hole admin UI", destination: url)
                    }
                }.padding(20)
            }
            .navigationTitle("Pi-hole")
            .task { await every(15) { await l.refreshPihole() } }
        } else { ProgressView() }
    }

    private func toggle(_ l: LinuxSession, enable: Bool, duration: String?) async {
        disabling = true
        await l.piholeSetEnabled(enable, duration: duration)
        disabling = false
    }
}

// MARK: - PiKVM

struct PiKVMPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let l = session as? LinuxSession {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 12) {
                        Card(title: "kvmd service") {
                            Text(l.pikvmActive == true ? "Active" : (l.pikvmActive == false ? "Inactive" : "–"))
                                .font(.system(size: 24, weight: .semibold, design: .rounded))
                                .foregroundStyle(l.pikvmActive == true ? .green : .red)
                            Button("Restart kvmd") { Task { await l.pikvmRestart() } }.controlSize(.small)
                        }
                        Card(title: "Root filesystem") {
                            Text(l.pikvmRootRW == true ? "Read-write" : (l.pikvmRootRW == false ? "Read-only" : "–"))
                                .font(.system(size: 24, weight: .semibold, design: .rounded))
                                .foregroundStyle(l.pikvmRootRW == true ? .orange : .secondary)
                            HStack {
                                Button("Remount rw") { Task { await l.pikvmSetWritable(true) } }
                                Button("Remount ro") { Task { await l.pikvmSetWritable(false) } }
                            }.controlSize(.small)
                        }
                    }
                    Text("PiKVM normally runs with a read-only root filesystem to protect the SD card; switch to read-write only while making changes, then back to read-only.")
                        .font(.caption).foregroundStyle(.secondary)
                    if !session.machine.webUrl.isEmpty, let url = URL(string: session.machine.webUrl) {
                        Link("Open PiKVM web UI", destination: url)
                    } else if let url = URL(string: "https://\(session.machine.host)/") {
                        Link("Open PiKVM web UI", destination: url)
                    }
                }.padding(20)
            }
            .navigationTitle("PiKVM")
            .task { await every(10) { await l.refreshPiKVM() } }
        } else { ProgressView() }
    }
}

// MARK: - Tailscale

struct TailscalePage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let l = session as? any TailscaleHosting {
            Group {
                if let e = l.tailscaleError {
                    ContentUnavailableView("Couldn't read Tailscale status", systemImage: "network.slash", description: Text(e))
                } else if let s = l.tailscaleStatus {
                    List {
                        Section {
                            HStack {
                                Circle().fill(s.isRunning ? .green : .gray).frame(width: 8, height: 8)
                                Text(s.isRunning ? "Running" : s.backendState)
                            }
                        }
                        Section("Peers") {
                            ForEach(s.peers) { p in
                                HStack {
                                    Circle().fill(p.online ? .green : .gray).frame(width: 8, height: 8)
                                    VStack(alignment: .leading) {
                                        HStack {
                                            Text(p.hostName).font(.body.weight(p.isSelf ? .semibold : .regular))
                                            if p.isSelf { Text("this machine").font(.caption2).foregroundStyle(.secondary) }
                                            if p.isExitNode { Text("exit node").font(.caption2).padding(.horizontal, 6).background(.blue.opacity(0.15), in: Capsule()).foregroundStyle(.blue) }
                                        }
                                        Text("\(p.ip)\(p.os.isEmpty ? "" : " · \(p.os)")").font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                }
                            }
                        }
                    }
                } else {
                    ProgressView("Loading Tailscale status…")
                }
            }
            .navigationTitle("Tailscale")
            .toolbar {
                ToolbarItem {
                    Link(destination: URL(string: "https://login.tailscale.com/admin/machines")!) { Label("Admin Console", systemImage: "arrow.up.right.square") }
                }
            }
            .task { await every(20) { await l.refreshTailscale() } }
        } else { ProgressView() }
    }
}

// MARK: - Containers (Linux)

struct ContainersPage: View {
    @Environment(MachineSession.self) private var session
    @State private var pending: (c: DockerContainer, verb: String)?
    @State private var logsFor: DockerContainer?

    var body: some View {
        if let l = session as? any ContainerHosting {
            List {
                if let e = l.containersError {
                    Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption)
                }
                ForEach(l.containers) { c in row(c, l) }
                if l.containers.isEmpty && l.containersError == nil {
                    if l.containersLoaded { Text("No containers.").foregroundStyle(.secondary) }
                    else { HStack { ProgressView().controlSize(.small); Text("Loading containers…").foregroundStyle(.secondary) } }
                }
            }
            .navigationTitle("Containers")
            .onAppear { l.setContainersActive(true) }
            .onDisappear { l.setContainersActive(false) }
            .sheet(item: $logsFor) { c in ContainerLogsSheet(container: c, loadLogs: { await l.containerLogs(c) }) }
            .confirmationDialog("\(pending?.verb.capitalized ?? "") \u{201C}\(pending?.c.name ?? "")\u{201D}?",
                                isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
                                titleVisibility: .visible) {
                Button(pending?.verb.capitalized ?? "", role: .destructive) {
                    if let p = pending { Task { await l.containerAction(p.c, verb: p.verb) } }
                    pending = nil
                }
            } message: { Text("Anything relying on this container will be interrupted.") }
        } else { ProgressView() }
    }

    private func row(_ c: DockerContainer, _ l: any ContainerHosting) -> some View {
        HStack(alignment: .top) {
            Circle().fill(c.isRunning ? .green : (c.state == "restarting" ? .orange : .gray)).frame(width: 8, height: 8).padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(c.name).font(.body.weight(.medium))
                Text(c.image).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Text(c.status + (c.ports.isEmpty ? "" : " · \(c.ports)")).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                if let cpu = c.cpu, c.isRunning { Text("CPU \(cpu) · RAM \(c.memory ?? "–")").font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
            }
            Spacer()
            HStack {
                Button("Logs") { logsFor = c }
                if c.isRunning {
                    Button("Restart") { pending = (c, "restart") }
                    Button("Stop") { pending = (c, "stop") }
                } else {
                    Button("Start") { Task { await l.containerAction(c, verb: "start") } }
                }
            }.controlSize(.small)
        }.padding(.vertical, 4)
    }
}

struct ContainerLogsSheet: View {
    let container: DockerContainer
    let loadLogs: () async -> String
    @Environment(\.dismiss) private var dismiss
    @State private var text = "Loading…"

    var body: some View {
        VStack(alignment: .leading) {
            HStack {
                Text("Logs · \(container.name)").font(.headline)
                Spacer()
                Button("Refresh") { Task { text = await loadLogs() } }
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            ScrollView {
                Text(text).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.padding(8).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
        }
        .padding(16).frame(width: 720, height: 460)
        .task { text = await loadLogs() }
    }
}
