import SwiftUI

struct DashboardPage: View {
    @Environment(MachineSession.self) private var s
    @State private var confirm: PowerAction?
    enum PowerAction: String, Identifiable { case reboot = "Restart", shutdown = "Shut Down"; var id: String { rawValue } }

    private let columns = [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                if case .failed(let m) = s.state, s.lastRefresh == nil {
                    ContentUnavailableView("Can't reach \(s.displayName)", systemImage: "wifi.exclamationmark", description: Text(m))
                } else {
                    LazyVGrid(columns: columns, spacing: 12) {
                        MeterCard(title: "CPU", value: s.cpuPercent, caption: "")
                        MeterCard(title: "Memory", value: s.memTotal > 0 ? s.memUsedFraction * 100 : nil,
                                  caption: "\(Fmt.bytes(s.memUsed)) of \(Fmt.bytes(s.memTotal))")
                        if s.gpuPercent != nil {
                            MeterCard(title: "GPU", value: s.gpuPercent, caption: "")
                        }
                        Card(title: s.kind == .truenas ? "Pools" : "Storage") {
                            ForEach(s.storage.prefix(3)) { item in
                                VStack(alignment: .leading, spacing: 2) {
                                    HStack {
                                        Text(item.title).font(.caption.weight(.medium)).lineLimit(1)
                                        Spacer()
                                        Text("\(Int(item.usedFraction * 100))%").font(.caption2).foregroundStyle(.secondary)
                                    }
                                    UsageBar(fraction: item.usedFraction)
                                }
                            }
                            if s.storage.isEmpty { Text("No data yet").font(.caption).foregroundStyle(.secondary) }
                        }
                        Card(title: "CPU history") { Sparkline(values: s.cpuHistory, maxValue: 100) }
                        Card(title: "Memory history") { Sparkline(values: s.memHistory, maxValue: 100) }
                        Card(title: "Alerts") {
                            if s.alerts.isEmpty {
                                Label("All clear", systemImage: "checkmark.seal").foregroundStyle(.secondary)
                            } else {
                                Text("\(s.alerts.count) item(s) need attention").foregroundStyle(.orange)
                                ForEach(s.alerts.prefix(2)) { Text($0.title).font(.caption).lineLimit(1) }
                            }
                        }
                    }
                    HStack {
                        Button("Restart…") { confirm = .reboot }
                        Button("Shut Down…") { confirm = .shutdown }
                    }
                }
            }.padding(20)
        }
        .navigationTitle("Dashboard")
        .confirmationDialog("\(confirm?.rawValue ?? "") \(s.displayName)?", isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } }), titleVisibility: .visible) {
            Button(confirm?.rawValue ?? "", role: .destructive) {
                let c = confirm; confirm = nil
                Task { c == .reboot ? await s.reboot() : await s.shutdown() }
            }
        } message: { Text("Everything running on the machine will be closed. Unsaved work can be lost.") }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(s.displayName).font(.largeTitle.bold())
            Text("\(s.hostOS.isEmpty ? s.kind.label : s.hostOS) · up \(Fmt.uptime(s.hostUptime))").foregroundStyle(.secondary)
        }
    }
}
