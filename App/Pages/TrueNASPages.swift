import SwiftUI
import UniformTypeIdentifiers

private func statusColor(_ s: String) -> Color {
    switch s { case "ONLINE", "RUNNING": .green; case "DEGRADED", "DEPLOYING": .orange; case "STOPPED": .gray; default: .red }
}

// MARK: - Apps

struct TrueNASAppsPage: View {
    @Environment(MachineSession.self) private var session
    @State private var installing = false

    var body: some View {
        if let tn = session as? TrueNASSession {
            Group {
                if tn.apps.isEmpty {
                    ContentUnavailableView("No apps", systemImage: "square.grid.2x2", description: Text("No apps installed, or this TrueNAS version doesn't support them."))
                } else {
                    List(tn.apps) { a in
                        HStack {
                            Circle().fill(statusColor(a.state)).frame(width: 8, height: 8)
                            VStack(alignment: .leading) {
                                Text(a.name).font(.body)
                                Text("\(a.state.capitalized)\(a.humanVersion.map { " · \($0)" } ?? "")\(a.upgradeAvailable == true ? " · update available" : "")")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if a.isRunning {
                                Button("Restart") { Task { await tn.appAction(a, action: "redeploy") } }
                                Button("Stop") { Task { await tn.appAction(a, action: "stop") } }
                            } else if a.state == "STOPPED" {
                                Button("Start") { Task { await tn.appAction(a, action: "start") } }
                            }
                        }.controlSize(.small)
                    }
                }
            }
            .navigationTitle("Apps")
            .toolbar { ToolbarItem { Button { installing = true } label: { Label("Add App", systemImage: "plus") } } }
            .sheet(isPresented: $installing) { InstallAppSheet(session: tn) }
        } else { ProgressView() }
    }
}

struct InstallAppSheet: View {
    let session: TrueNASSession
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var yaml = ""
    @State private var importing = false

    private static let example = """
    services:
      web:
        image: nginx:latest
        restart: unless-stopped
        ports:
          - "8088:80"
    """

    private var nameValid: Bool {
        name.range(of: #"^[a-z]([-a-z0-9]*[a-z0-9])?$"#, options: .regularExpression) != nil && name.count <= 40
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Install custom app").font(.title3.bold())
            Text("Paste a Docker Compose file, the same as TrueNAS's \u{201C}Install via YAML\u{201D}.").font(.caption).foregroundStyle(.secondary)
            TextField("Application name", text: $name, prompt: Text("my-app")).textFieldStyle(.roundedBorder)
            if !name.isEmpty && !nameValid {
                Text("Use lowercase letters, numbers and hyphens, starting with a letter.").font(.caption).foregroundStyle(.orange)
            }
            HStack {
                Text("Compose YAML").font(.subheadline)
                Spacer()
                Button("Insert example") { yaml = Self.example }.controlSize(.small)
                Button("Open file…") { importing = true }.controlSize(.small)
            }
            TextEditor(text: $yaml)
                .font(.system(size: 12, design: .monospaced)).scrollContentBackground(.hidden)
                .padding(6).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                .frame(minHeight: 260).autocorrectionDisabled()
            if let s = session.installStatus {
                HStack(alignment: .top) {
                    if session.installRunning { ProgressView().controlSize(.small) }
                    Text(s).font(.caption).foregroundStyle(session.installFailed ? .red : .secondary).textSelection(.enabled)
                }
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.disabled(session.installRunning)
                Button("Install") { Task { if await session.installCustomApp(name: name, yaml: yaml) { dismiss() } } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(!nameValid || yaml.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || session.installRunning)
            }
        }
        .padding(20).frame(width: 640)
        .fileImporter(isPresented: $importing, allowedContentTypes: [.yaml, .plainText, .text]) { result in
            guard case .success(let url) = result, url.startAccessingSecurityScopedResource() else { return }
            defer { url.stopAccessingSecurityScopedResource() }
            if let t = try? String(contentsOf: url, encoding: .utf8) {
                yaml = t
                if name.isEmpty {
                    let base = url.deletingPathExtension().lastPathComponent.lowercased()
                        .replacingOccurrences(of: "[^a-z0-9]+", with: "-", options: .regularExpression)
                        .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
                    if base.first?.isLetter == true { name = String(base.prefix(40)) }
                }
            }
        }
        .onAppear { session.installStatus = nil; session.installFailed = false }
    }
}

// MARK: - VMs

struct TrueNASVMsPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let tn = session as? TrueNASSession {
            Group {
                if tn.vms.isEmpty {
                    ContentUnavailableView("No virtual machines", systemImage: "display", description: Text("No VMs are configured on this system."))
                } else {
                    ScrollView {
                        VStack(spacing: 12) {
                            if let e = tn.vmStatsError {
                                Label(e, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            ForEach(tn.vms) { TNVMRow(vm: $0, session: tn) }
                        }.padding(16)
                    }
                }
            }
            .navigationTitle("Virtual Machines")
            .onAppear { tn.setVMStatsActive(true) }
            .onDisappear { tn.setVMStatsActive(false) }
        } else { ProgressView() }
    }
}

struct TNVMRow: View {
    let vm: TNVM
    let session: TrueNASSession
    @State private var expanded = true
    @State private var pending: (title: String, action: String)?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Button { withAnimation(.snappy) { expanded.toggle() } } label: {
                    Image(systemName: "chevron.right").rotationEffect(.degrees(expanded ? 90 : 0)).frame(width: 14)
                }.buttonStyle(.plain)
                Circle().fill(vm.isRunning ? .green : .gray).frame(width: 8, height: 8)
                VStack(alignment: .leading) {
                    Text(vm.name).font(.headline)
                    Text("\(vm.status.state.capitalized) · \(vm.totalCPUs) vCPU · \(Fmt.bytes((vm.memory ?? 0) * 1_048_576))\(vm.autostart == true ? " · autostart" : "")")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if vm.isRunning {
                    Button("Restart") { pending = ("Restart", "restart") }
                    Button("Stop") { pending = ("Stop", "stop") }
                    Button("Power Off", role: .destructive) { pending = ("Power Off", "poweroff") }
                } else {
                    Button("Start") { Task { await session.vmAction(vm, action: "start") } }
                }
            }.controlSize(.small)
            if expanded { TNVMMetrics(vm: vm, session: session) }
        }
        .confirmationDialog("\(pending?.title ?? "") \u{201C}\(vm.name)\u{201D}?",
                            isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
                            titleVisibility: .visible) {
            Button(pending?.title ?? "", role: .destructive) {
                if let p = pending { Task { await session.vmAction(vm, action: p.action) } }
                pending = nil
            }
        } message: { Text("Anything relying on this VM will be interrupted.") }
        .padding(14)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct TNVMMetrics: View {
    let vm: TNVM
    let session: TrueNASSession

    var body: some View {
        if !session.isSSHConfigured {
            Text("Live performance needs SSH access to the NAS. Enable it in the machine's Edit sheet.")
                .font(.caption).foregroundStyle(.secondary)
        } else if let l = session.vmLive[vm.id], vm.isRunning {
            VStack(alignment: .leading, spacing: 10) {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), spacing: 10)], spacing: 10) {
                    TNTile("CPU", Fmt.percent(l.cpu), "\(l.vcpus) vCPU", l.cpuHistory, fixedMax: 100, tint: .blue)
                    TNTile("Memory", l.memUsed.map { Fmt.bytes(Int64($0)) } ?? "–",
                         "of \(l.memTotal.map { Fmt.bytes(Int64($0)) } ?? "–") allocated", l.memHistory, fixedMax: 100, tint: .orange)
                    TNTile("Network in", Fmt.rate(l.rxRate), l.nics.joined(separator: ", "), l.rxHistory, tint: .green)
                    TNTile("Network out", Fmt.rate(l.txRate), l.nics.joined(separator: ", "), l.txHistory, tint: .teal)
                    TNTile("Disk read", Fmt.rate(l.diskReadRate), "", l.readHistory, tint: .purple)
                    TNTile("Disk write", Fmt.rate(l.diskWriteRate), "", l.writeHistory, tint: .pink)
                }
                if !l.disks.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Disk usage").font(.caption).foregroundStyle(.secondary)
                        ForEach(l.disks) { d in
                            HStack {
                                Text(d.name).font(.caption.monospaced()).lineLimit(1).truncationMode(.head).frame(width: 190, alignment: .leading)
                                ProgressView(value: d.capacity > 0 ? min(d.allocation / d.capacity, 1) : 0)
                                Text(d.capacity > 0 ? "\(Fmt.bytes(Int64(d.allocation))) / \(Fmt.bytes(Int64(d.capacity)))" : "size unavailable")
                                    .font(.caption).foregroundStyle(.secondary).frame(width: 150, alignment: .trailing)
                            }
                        }
                    }
                }
            }
        } else if vm.isRunning {
            HStack { ProgressView().controlSize(.small); Text("Collecting stats…").font(.caption).foregroundStyle(.secondary) }
        } else {
            Text("VM is stopped.").font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct TNTile: View {
    let title: String, value: String, caption: String, history: [Double]
    var fixedMax: Double?
    var tint: Color
    init(_ title: String, _ value: String, _ caption: String, _ history: [Double], fixedMax: Double? = nil, tint: Color) {
        self.title = title; self.value = value; self.caption = caption; self.history = history
        self.fixedMax = fixedMax; self.tint = tint
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.system(size: 20, weight: .semibold, design: .rounded))
            Sparkline(values: history, maxValue: fixedMax, height: 34).tint(tint)
            Text(caption.isEmpty ? " " : caption).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(.quaternary))
    }
}

// MARK: - Containers

struct TrueNASContainersPage: View {
    @Environment(MachineSession.self) private var session
    @AppStorage("showTrueNASContainers") private var showManaged = false
    @State private var pending: (c: DockerContainer, verb: String)?
    @State private var logsFor: DockerContainer?

    var body: some View {
        if let tn = session as? TrueNASSession {
            let visible = tn.containers.filter { showManaged || $0.truenasApp == nil }
            Group {
                if !tn.isSSHConfigured {
                    ContentUnavailableView("SSH required", systemImage: "terminal",
                        description: Text("Containers outside TrueNAS's app system are listed over SSH. Enable it in the machine's Edit sheet."))
                } else {
                    List {
                        if let e = tn.containersError {
                            Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption)
                        }
                        ForEach(visible) { c in row(c, tn) }
                        if visible.isEmpty && tn.containersError == nil {
                            if tn.containersLoaded {
                                Text(showManaged ? "No containers." : "No containers outside TrueNAS apps.").foregroundStyle(.secondary)
                            } else {
                                HStack { ProgressView().controlSize(.small); Text("Loading containers…").foregroundStyle(.secondary) }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Containers")
            .toolbar {
                ToolbarItem { Toggle(isOn: $showManaged) { Label("TrueNAS apps", systemImage: "eye") }.toggleStyle(.button) }
            }
            .onAppear { tn.setContainersActive(true) }
            .onDisappear { tn.setContainersActive(false) }
            .sheet(item: $logsFor) { c in TNLogsSheet(container: c, session: tn) }
            .confirmationDialog("\(pending?.verb.capitalized ?? "") \u{201C}\(pending?.c.name ?? "")\u{201D}?",
                                isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
                                titleVisibility: .visible) {
                Button(pending?.verb.capitalized ?? "", role: .destructive) {
                    if let p = pending { Task { await tn.containerAction(p.c, verb: p.verb) } }
                    pending = nil
                }
            } message: { Text("Anything relying on this container will be interrupted.") }
        } else { ProgressView() }
    }

    private func row(_ c: DockerContainer, _ tn: TrueNASSession) -> some View {
        HStack(alignment: .top) {
            Circle().fill(c.isRunning ? .green : (c.state == "restarting" ? .orange : .gray)).frame(width: 8, height: 8).padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(c.name).font(.body.weight(.medium))
                    if let app = c.truenasApp {
                        Text("TrueNAS app: \(app)").font(.caption2).padding(.horizontal, 6).padding(.vertical, 1)
                            .background(.blue.opacity(0.15), in: Capsule()).foregroundStyle(.blue)
                    }
                }
                Text(c.image).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Text(c.status + (c.ports.isEmpty ? "" : " · \(c.ports)")).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                if let cpu = c.cpu, c.isRunning {
                    Text("CPU \(cpu) · RAM \(c.memory ?? "–")").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            Spacer()
            HStack {
                Button("Logs") { logsFor = c }
                if c.isRunning {
                    Button("Restart") { pending = (c, "restart") }
                    Button("Stop") { pending = (c, "stop") }
                } else {
                    Button("Start") { Task { await tn.containerAction(c, verb: "start") } }
                }
            }.controlSize(.small)
        }.padding(.vertical, 4)
    }
}

struct TNLogsSheet: View {
    let container: DockerContainer
    let session: TrueNASSession
    @Environment(\.dismiss) private var dismiss
    @State private var text = "Loading…"

    var body: some View {
        VStack(alignment: .leading) {
            HStack {
                Text("Logs · \(container.name)").font(.headline)
                Spacer()
                Button("Refresh") { Task { await load() } }
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            ScrollView {
                Text(text).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.padding(8).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
        }
        .padding(16).frame(width: 720, height: 460)
        .task { await load() }
    }

    private func load() async { text = await session.containerLogs(container) }
}
