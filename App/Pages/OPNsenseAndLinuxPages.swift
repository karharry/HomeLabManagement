import SwiftUI

// MARK: - OPNsense

struct OPNsenseNetworkPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let o = session as? OPNsenseSession {
            Group {
                if o.interfaces.isEmpty {
                    ContentUnavailableView("No interfaces reported", systemImage: "network")
                } else {
                    List(o.interfaces) { i in
                        HStack {
                            Circle().fill(i.isUp ? .green : .gray).frame(width: 8, height: 8)
                            VStack(alignment: .leading) {
                                Text("\(i.name) (\(i.device))").font(.body.weight(.medium))
                                Text("\(i.status) · \(i.addresses.isEmpty ? "No address" : i.addresses.joined(separator: ", "))")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Network")
        } else { ProgressView() }
    }
}

struct OPNsenseDHCPPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let o = session as? OPNsenseSession {
            Group {
                if o.dhcpLeases.isEmpty {
                    ContentUnavailableView("No active DHCP leases", systemImage: "list.bullet.rectangle")
                } else {
                    List(o.dhcpLeases) { l in
                        VStack(alignment: .leading) {
                            Text("\(l.ip)  —  \(l.hostname)").font(.body.weight(.medium))
                            Text([l.mac, l.interface, l.state].filter { !$0.isEmpty }.joined(separator: " · "))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("DHCP Leases")
            .task { await every(20) { await o.refreshDHCPLeases() } }
        } else { ProgressView() }
    }
}

/// Services > Dnsmasq DNS & DHCP > Hosts: static DHCP reservations that double as local DNS entries.
struct OPNsenseStaticHostsPage: View {
    @Environment(MachineSession.self) private var session
    @State private var host = ""
    @State private var domain = ""
    @State private var ip = ""
    @State private var mac = ""
    @State private var descr = ""
    @State private var error: String?
    @State private var busy = false
    @State private var removing: OPNDnsmasqHost?

    var body: some View {
        if let o = session as? OPNsenseSession {
            VStack(spacing: 0) {
                if let e = error ?? o.dnsmasqHostsError {
                    Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption).padding(8)
                }
                List {
                    ForEach(o.dnsmasqHosts) { h in
                        HStack {
                            VStack(alignment: .leading) {
                                Text("\(h.fqdn)  →  \(h.ip)").font(.body.weight(.medium))
                                let bits = [h.mac, h.descr].filter { !$0.isEmpty }
                                if !bits.isEmpty { Text(bits.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary) }
                            }
                            Spacer()
                            Button("Remove") { removing = h }.controlSize(.small)
                        }
                    }
                    if o.dnsmasqHosts.isEmpty { Text("No static hosts configured.").foregroundStyle(.secondary) }
                }
                Divider()
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        TextField("Hostname, e.g. nas", text: $host)
                        TextField("Domain (optional)", text: $domain)
                        TextField("IP, e.g. 192.168.1.10", text: $ip)
                    }
                    HStack {
                        TextField("MAC (optional), aa:bb:cc:dd:ee:ff", text: $mac)
                        TextField("Description (optional)", text: $descr)
                        Button("Add") {
                            Task {
                                busy = true
                                error = await o.addDnsmasqHost(host: host, ip: ip, mac: mac, domain: domain, descr: descr)
                                if error == nil { host = ""; domain = ""; ip = ""; mac = ""; descr = "" }
                                busy = false
                            }
                        }.disabled(busy)
                    }
                    Text("A MAC address binds this entry as a DHCP reservation (same device always gets this IP); leave it blank for a DNS-only host entry.")
                        .font(.caption).foregroundStyle(.secondary)
                }.textFieldStyle(.roundedBorder).padding(12)
            }
            .navigationTitle("Static Hosts")
            .task { await o.refreshDnsmasqHosts() }
            .confirmationDialog("Remove \u{201C}\(removing?.fqdn ?? "")\u{201D}?", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }), titleVisibility: .visible) {
                Button("Remove", role: .destructive) {
                    if let h = removing { Task { error = await o.removeDnsmasqHost(h) } }
                    removing = nil
                }
            } message: { Text("This removes the static host entry pointing \(removing?.fqdn ?? "") to \(removing?.ip ?? "").") }
        } else { ProgressView() }
    }
}

// MARK: - Pi-hole Local DNS (Linux)

struct LocalDNSPage: View {
    @Environment(MachineSession.self) private var session
    @State private var domain = ""
    @State private var ip = ""
    @State private var error: String?
    @State private var busy = false
    @State private var removing: PiholeDNSRecord?

    var body: some View {
        if let l = session as? LinuxSession {
            VStack(spacing: 0) {
                if let s = l.piholeStatus {
                    HStack(spacing: 12) {
                        Card(title: "Blocking") { Text(s.enabled ? "Enabled" : "Disabled").font(.headline).foregroundStyle(s.enabled ? .green : .red) }
                        Card(title: "Queries today") { Text(s.queriesToday.map(String.init) ?? "–").font(.headline) }
                        Card(title: "Blocked today") { Text(s.blockedToday.map(String.init) ?? "–").font(.headline).foregroundStyle(.orange) }
                    }.padding(12).frame(height: 90)
                }
                if let e = error ?? l.localDNSError {
                    Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption).padding(8)
                }
                List {
                    ForEach(l.localDNSRecords) { r in
                        HStack {
                            Text("\(r.domain)  →  \(r.ip)")
                            Spacer()
                            Button("Remove") { removing = r }.controlSize(.small)
                        }
                    }
                    if l.localDNSRecords.isEmpty { Text("No local DNS records configured.").foregroundStyle(.secondary) }
                }
                Divider()
                HStack {
                    TextField("Domain, e.g. nas.lan", text: $domain)
                    TextField("IP address, e.g. 192.168.1.10", text: $ip)
                    Button("Add") {
                        Task {
                            busy = true
                            error = await l.addLocalDNS(domain: domain, ip: ip)
                            if error == nil { domain = ""; ip = "" }
                            busy = false
                        }
                    }.disabled(busy)
                }.textFieldStyle(.roundedBorder).padding(12)
            }
            .navigationTitle("Local DNS")
            .task { await every(15) { await l.refreshPihole(); await l.refreshLocalDNS() } }
            .confirmationDialog("Remove \u{201C}\(removing?.domain ?? "")\u{201D}?", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }), titleVisibility: .visible) {
                Button("Remove", role: .destructive) {
                    if let r = removing { Task { error = await l.removeLocalDNS(r) } }
                    removing = nil
                }
            } message: { Text("This removes the local DNS record pointing \(removing?.domain ?? "") to \(removing?.ip ?? "").") }
        } else { ProgressView() }
    }
}

// MARK: - libvirt VMs (Linux)

struct LinuxVMsPage: View {
    @Environment(MachineSession.self) private var session
    @State private var poweroff: LinuxVM?

    var body: some View {
        if let l = session as? LinuxSession {
            List {
                if let e = l.vmsError { Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption) }
                ForEach(l.vms) { v in
                    HStack {
                        Circle().fill(color(v.state)).frame(width: 8, height: 8)
                        VStack(alignment: .leading) {
                            Text(v.name).font(.body.weight(.medium))
                            Text("\(v.state.capitalized) · \(v.vcpus) vCPU · \(v.memoryMiB.map { "\($0) MiB" } ?? "–")\(v.autostart ? " · autostart" : "")")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        HStack {
                            if v.isRunning {
                                Button("Restart") { Task { await l.vmAction(v, action: "restart") } }
                                Button("Stop") { Task { await l.vmAction(v, action: "stop") } }
                                Button("Force Off") { poweroff = v }
                            } else {
                                Button("Start") { Task { await l.vmAction(v, action: "start") } }
                            }
                        }.controlSize(.small)
                    }.padding(.vertical, 2)
                }
                if l.vms.isEmpty && l.vmsError == nil { Text("No libvirt VMs found on this system.").foregroundStyle(.secondary) }
            }
            .navigationTitle("VMs")
            .onAppear { l.setVMsActive(true) }
            .onDisappear { l.setVMsActive(false) }
            .confirmationDialog("Force off \u{201C}\(poweroff?.name ?? "")\u{201D}?", isPresented: Binding(get: { poweroff != nil }, set: { if !$0 { poweroff = nil } }), titleVisibility: .visible) {
                Button("Force Off", role: .destructive) {
                    if let v = poweroff { Task { await l.vmAction(v, action: "poweroff") } }
                    poweroff = nil
                }
            } message: { Text("This is the equivalent of pulling the plug — unsaved state in the VM will be lost.") }
        } else { ProgressView() }
    }

    private func color(_ state: String) -> Color {
        switch state { case "running": .green; case "paused": .orange; case "shut off": .gray; default: .red }
    }
}
