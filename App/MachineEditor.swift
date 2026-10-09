import SwiftUI

enum MachineSheet: Identifiable, Hashable {
    case add, edit(Machine)
    var id: String { switch self { case .add: "add"; case .edit(let m): m.id.uuidString } }
}

struct MachineEditor: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let existing: Machine?

    @State private var machine: Machine
    @State private var secret = ""
    @State private var sshPassword = ""
    @State private var fanPassword = ""
    @State private var testResult: String?
    @State private var testing = false

    init(existing: Machine?) {
        self.existing = existing
        _machine = State(initialValue: existing ?? Machine())
        if let existing {
            _secret = State(initialValue: MachineStore.secret(for: existing.id))
            _fanPassword = State(initialValue: MachineStore.fanPassword(for: existing.id))
        }
    }

    var body: some View {
        Form {
            Section {
                Picker("Kind", selection: $machine.kind) {
                    ForEach(MachineKind.allCases) { k in Label(k.label, systemImage: k.icon).tag(k) }
                }
                .onChange(of: machine.kind) { _, new in if machine.port == 0 || existing == nil { machine.port = new.defaultPort } }
                TextField("Name", text: $machine.name, prompt: Text("Optional — defaults to the host"))
                TextField("Host", text: $machine.host, prompt: Text("192.168.1.10 or nas.local"))
                TextField("Port", value: $machine.port, formatter: NumberFormatter())
            }

            if machine.kind == .truenas {
                Section("TrueNAS API") {
                    Toggle("Use TLS (wss://)", isOn: $machine.useTLS)
                    Toggle("Allow self-signed certificate", isOn: $machine.allowSelfSigned)
                    Toggle("Log in with username + password", isOn: $machine.usePassword)
                    if machine.usePassword {
                        TextField("Username", text: $machine.user)
                        SecureField("Password", text: $secret)
                    } else {
                        SecureField("API Key", text: $secret)
                    }
                }
                Section("SSH (optional — for VMs and containers)") {
                    Toggle("Enable SSH", isOn: $machine.sshEnabled)
                    if machine.sshEnabled {
                        TextField("SSH Port", value: $machine.sshPort, formatter: NumberFormatter())
                        TextField("SSH User", text: $machine.sshUser)
                        SecureField("SSH Password", text: $sshPassword)
                        Toggle("Use sudo", isOn: $machine.sshSudo)
                    }
                }
                Section("Fan Control (optional — needs a \u{2018}fanctl\u{2019} container)") {
                    TextField("Fan Controller Port", value: $machine.fanPort, formatter: NumberFormatter())
                    SecureField("Fan Controller Password", text: $fanPassword)
                }
            } else {
                Section("Login") {
                    TextField("User", text: $machine.user)
                    SecureField("Password", text: $secret)
                    if machine.kind != .windows { Toggle("Use sudo for privileged actions", isOn: $machine.useSudo) }
                }
                Section {
                    Text(setupHint).font(.caption).foregroundStyle(.secondary)
                }
                if machine.kind == .windows {
                    Section("Hardware sensors (optional)") {
                        TextField("LibreHardwareMonitor Port", value: $machine.lhmPort, formatter: NumberFormatter(), prompt: Text("0 = off"))
                        Text("In LibreHardwareMonitor: Options \u{2192} check \u{201C}Remote Web Server\u{201D} (default port 8085). Falls back to WMI if left at 0.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }

            if machine.kind == .raspios || machine.kind == .arch || machine.kind == .debian {
                Section("Web UI (optional — Pi-hole / PiKVM admin link)") {
                    TextField("URL", text: $machine.webUrl, prompt: Text("https://192.168.1.20/"))
                }
            }

            Section {
                HStack {
                    Button("Test Connection") { Task { await test() } }.disabled(testing || !machine.isValid)
                    if testing { ProgressView().controlSize(.small) }
                    if let testResult { Text(testResult).font(.caption).foregroundStyle(testResult.hasPrefix("OK") ? .green : .red) }
                }
                if existing != nil {
                    Button("Forget pinned SSH host key", role: .destructive) {
                        UserDefaults.standard.removeObject(forKey: "sshHostKey.\(machine.host):\(machine.kind == .truenas ? machine.sshPort : machine.port)")
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 460, height: 520)
        .navigationTitle(existing == nil ? "Add Machine" : "Edit Machine")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") {
                    model.upsert(machine, secret: secret, sshPassword: sshPassword, fanPassword: fanPassword)
                    dismiss()
                }.disabled(!machine.isValid)
            }
        }
    }

    private var setupHint: String {
        switch machine.kind {
        case .windows: "Needs OpenSSH Server installed and running (Settings > Apps > Optional Features), with password authentication enabled."
        case .macos: "Enable Remote Login in System Settings > General > Sharing on the target Mac."
        default: "Needs an SSH server reachable with a password (PasswordAuthentication yes in sshd_config)."
        }
    }

    private func test() async {
        testing = true; testResult = nil
        defer { testing = false }
        var m = machine
        m.id = existing?.id ?? UUID()
        MachineStore.setSecret(secret, for: m.id)
        if m.sshEnabled { MachineStore.setSSHPassword(sshPassword, for: m.id) }
        let s = MachineSession.make(for: m)
        let ok = await s.poll()
        testResult = ok ? "OK — connected." : (s.state.failureMessage ?? "Couldn't connect.")
        await s.closeBackend()
        if existing == nil { MachineStore.forgetSecrets(for: m.id) }
    }
}

private extension ConnectionState {
    var failureMessage: String? { if case .failed(let m) = self { m } else { nil } }
}
