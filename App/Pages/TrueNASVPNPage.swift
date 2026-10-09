import SwiftUI

struct TrueNASVPNPage: View {
    @Environment(MachineSession.self) private var session
    @State private var adding = false
    @State private var pending: String?

    var body: some View {
        if let tn = session as? TrueNASSession {
            Group {
                if tn.vpnContainers.isEmpty {
                    ContentUnavailableView("No tunnels configured", systemImage: "lock.shield",
                        description: Text("Add a WireGuard config \u{2014} your own, or one exported from a VPN provider like ProtonVPN or Mullvad. This runs as a plain Docker container (linuxserver/wireguard), not a registered TrueNAS App, so it also shows up on the Containers page."))
                } else {
                    List {
                        if let e = tn.vpnError {
                            Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption)
                        }
                        ForEach(tn.vpnContainers) { c in row(c, tn) }
                    }
                }
            }
            .navigationTitle("VPN")
            .toolbar {
                ToolbarItem { Button { adding = true } label: { Label("Add Tunnel", systemImage: "plus") } }
            }
            .task { await every(10) { await tn.refreshVPN() } }
            .sheet(isPresented: $adding) { AddTrueNASVPNSheet(session: tn) }
            .confirmationDialog("Remove \u{201C}\(pending ?? "")\u{201D}?",
                                isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
                                titleVisibility: .visible) {
                Button("Remove", role: .destructive) {
                    if let p = pending { Task { await tn.removeVPNTunnel(p) } }
                    pending = nil
                }
            } message: { Text("This stops and deletes the container and its config. It can't be undone.") }
        } else { ProgressView() }
    }

    private func row(_ c: TNVPNContainer, _ tn: TrueNASSession) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Circle().fill(c.running ? .green : .gray).frame(width: 8, height: 8)
                Text(c.name).font(.body.weight(.medium))
                Spacer()
                if c.running {
                    Button("Stop") { Task { await tn.stopVPNTunnel(c.name) } }
                } else {
                    Button("Start") { Task { await tn.startVPNTunnel(c.name) } }
                }
                Menu {
                    Button("Remove", role: .destructive) { pending = c.name }
                } label: { Image(systemName: "ellipsis.circle") }.menuIndicator(.hidden).fixedSize()
            }.controlSize(.small)
            if let t = c.tunnel {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 2) {
                    GridRow {
                        Text("Endpoint").font(.caption).foregroundStyle(.secondary)
                        Text(t.endpoint.isEmpty ? "–" : t.endpoint).font(.caption.monospaced())
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
            } else if c.running {
                Text("Container is running; waiting for tunnel stats\u{2026}").font(.caption).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 4)
    }
}

struct AddTrueNASVPNSheet: View {
    let session: TrueNASSession
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var config = ""
    @State private var importing = false
    @State private var error: String?
    @State private var saving = false

    private var nameValid: Bool { WireGuardParser.isValidName(name) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Add VPN Tunnel").font(.title3.bold())
            Text("Paste a WireGuard config \u{2014} your own, or exported from a VPN provider's dashboard. This starts a small container (linuxserver/wireguard) that brings the tunnel up; it needs SSH enabled on this machine.")
                .font(.caption).foregroundStyle(.secondary)
            TextField("Tunnel name", text: $name, prompt: Text("e.g. protonvpn")).textFieldStyle(.roundedBorder)
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
        .fileImporter(isPresented: $importing, allowedContentTypes: [.plainText, .text]) { result in
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
        error = await session.addVPNTunnel(name: name, config: config)
        saving = false
        if error == nil { dismiss() }
    }
}
