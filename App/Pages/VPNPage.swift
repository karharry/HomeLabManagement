import SwiftUI
import UniformTypeIdentifiers

struct VPNPage: View {
    @Environment(MachineSession.self) private var session
    @State private var adding = false
    @State private var pending: (name: String, verb: String)?

    var body: some View {
        if let v = session as? any WireGuardHosting {
            List {
                if let l = session as? LinuxSession, l.hasProtonVPNCLI {
                    Section("ProtonVPN CLI") { ProtonVPNCard(session: l) }
                }
                Section(v.wireguardTunnels.isEmpty ? "" : "WireGuard Tunnels") {
                    if v.wireguardTunnels.isEmpty {
                        ContentUnavailableView("No tunnels configured", systemImage: "lock.shield",
                            description: Text("Add a WireGuard config \u{2014} your own, or one exported from a VPN provider like ProtonVPN or Mullvad (Settings \u{2192} Downloads \u{2192} WireGuard configuration on their dashboard)."))
                    } else {
                        if let e = v.wireguardError {
                            Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption)
                        }
                        ForEach(v.wireguardTunnels) { t in row(t, v) }
                    }
                }
            }
            .navigationTitle("VPN")
            .toolbar {
                ToolbarItem { Button { adding = true } label: { Label("Add Tunnel", systemImage: "plus") } }
            }
            .task { await every(10) { await v.refreshWireGuard() } }
            .sheet(isPresented: $adding) { AddTunnelSheet(host: v) }
            .confirmationDialog("Remove \u{201C}\(pending?.name ?? "")\u{201D}?",
                                isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
                                titleVisibility: .visible) {
                Button("Remove", role: .destructive) {
                    if let p = pending { Task { await v.removeWireGuardTunnel(p.name) } }
                    pending = nil
                }
            } message: { Text("This deletes the tunnel's configuration from the machine. It can't be undone — re-add it from the config file if you need it again.") }
        } else { ProgressView() }
    }

    private func row(_ t: WireGuardTunnel, _ v: any WireGuardHosting) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Circle().fill(t.isUp ? .green : .gray).frame(width: 8, height: 8)
                Text(t.name).font(.body.weight(.medium))
                Spacer()
                if t.isUp {
                    Button("Disconnect") { Task { await v.disconnectWireGuardTunnel(t.name) } }
                } else {
                    Button("Connect") { Task { await v.connectWireGuardTunnel(t.name) } }
                }
                Menu {
                    Button("Remove", role: .destructive) { pending = (t.name, "remove") }
                } label: { Image(systemName: "ellipsis.circle") }.menuIndicator(.hidden).fixedSize()
            }.controlSize(.small)
            if t.isUp {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 2) {
                    GridRow {
                        Text("Endpoint").font(.caption).foregroundStyle(.secondary)
                        Text(t.endpoint.isEmpty ? "–" : t.endpoint).font(.caption.monospaced())
                    }
                    GridRow {
                        Text("Allowed IPs").font(.caption).foregroundStyle(.secondary)
                        Text(t.allowedIPs.isEmpty ? "–" : t.allowedIPs).font(.caption.monospaced())
                    }
                    GridRow {
                        Text("Handshake").font(.caption).foregroundStyle(.secondary)
                        Text(t.latestHandshake).font(.caption)
                    }
                    GridRow {
                        Text("Transfer").font(.caption).foregroundStyle(.secondary)
                        Text("\u{2193} \(Fmt.bytes(t.rxBytes)) · \u{2191} \(Fmt.bytes(t.txBytes))").font(.caption)
                    }
                }
            }
        }.padding(.vertical, 4)
    }
}

struct AddTunnelSheet: View {
    let host: any WireGuardHosting
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var config = ""
    @State private var importing = false
    @State private var error: String?
    @State private var saving = false

    private var nameValid: Bool { WireGuardParser.isValidName(name) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Add WireGuard Tunnel").font(.title3.bold())
            Text("Paste a WireGuard config \u{2014} your own, or exported from a VPN provider's dashboard (ProtonVPN, Mullvad, and most others all publish standard WireGuard `.conf` files for manual setup).")
                .font(.caption).foregroundStyle(.secondary)
            TextField("Tunnel name", text: $name, prompt: Text("e.g. protonvpn or home"))
                .textFieldStyle(.roundedBorder)
            if !name.isEmpty && !nameValid {
                Text("Letters, numbers, dashes and underscores only.").font(.caption).foregroundStyle(.orange)
            }
            HStack {
                Text("Config (.conf)").font(.subheadline)
                Spacer()
                Button("Open file…") { importing = true }.controlSize(.small)
            }
            TextEditor(text: $config)
                .font(.system(size: 12, design: .monospaced)).scrollContentBackground(.hidden)
                .padding(6).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                .frame(minHeight: 220).autocorrectionDisabled()
            if let error { Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.disabled(saving)
                Button("Add") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(!nameValid || config.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || saving)
                if saving { ProgressView().controlSize(.small) }
            }
        }
        .padding(20).frame(width: 560)
        .fileImporter(isPresented: $importing, allowedContentTypes: [UTType(filenameExtension: "conf") ?? .plainText, .plainText, .text]) { result in
            guard case .success(let url) = result, url.startAccessingSecurityScopedResource() else { return }
            defer { url.stopAccessingSecurityScopedResource() }
            if let t = try? String(contentsOf: url, encoding: .utf8) {
                config = t
                if name.isEmpty {
                    let base = url.deletingPathExtension().lastPathComponent
                        .replacingOccurrences(of: "[^A-Za-z0-9_-]+", with: "-", options: .regularExpression)
                    if !base.isEmpty { name = String(base.prefix(40)) }
                }
            }
        }
    }

    private func save() async {
        saving = true
        error = await host.addWireGuardTunnel(name: name, config: config)
        saving = false
        if error == nil { dismiss() }
    }
}

/// Drives the classic `protonvpn-cli` tool over SSH. This is best-effort: ProtonVPN only ships an
/// official CLI on Linux (not macOS/Windows), and its exact flags can vary by installed version, so
/// the raw output of every command is always shown rather than parsed/hidden, so you can tell
/// immediately if something about your install doesn't match what's being sent.
struct ProtonVPNCard: View {
    let session: LinuxSession
    @State private var username = ""
    @State private var password = ""
    @State private var showLogin = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if showLogin || (username.isEmpty && MachineStore.protonUser(for: session.machine.id).isEmpty) {
                TextField("ProtonVPN username", text: $username).textFieldStyle(.roundedBorder)
                SecureField("ProtonVPN password", text: $password).textFieldStyle(.roundedBorder)
                Button("Log In") {
                    Task { await session.protonLogin(username: username, password: password); showLogin = false }
                }.disabled(username.isEmpty || password.isEmpty || session.protonBusy)
            } else {
                HStack {
                    Button("Connect (Fastest)") { Task { await session.protonConnectFastest() } }
                    Button("Disconnect") { Task { await session.protonDisconnect() } }
                    Button("Refresh Status") { Task { await session.protonRefreshStatus() } }
                    Spacer()
                    Button("Change Login") { showLogin = true }.buttonStyle(.borderless)
                    if session.protonBusy { ProgressView().controlSize(.small) }
                }
            }
            if let status = session.protonStatus {
                ScrollView {
                    Text(status).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }.frame(maxHeight: 160).padding(8).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .onAppear { username = MachineStore.protonUser(for: session.machine.id) }
        .task { if session.protonStatus == nil { await session.protonRefreshStatus() } }
    }
}
