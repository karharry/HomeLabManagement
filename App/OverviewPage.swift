import SwiftUI

/// "All machines" grid, shown when nothing is selected in the sidebar — mirrors the combined
/// Windows tool's OverviewPage.
struct OverviewPage: View {
    @Environment(AppModel.self) private var model
    @State private var sheet: MachineSheet?

    var body: some View {
        ScrollView {
            if model.orderedSessions.isEmpty {
                VStack(spacing: 12) {
                    ContentUnavailableView("No machines yet", systemImage: "server.rack",
                        description: Text("Add a TrueNAS, Linux, Windows, or Mac machine to get started."))
                    Button("Add Machine…") { sheet = .add }
                }.padding(40)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: 14)], alignment: .leading, spacing: 14) {
                    ForEach(model.orderedSessions) { s in
                        MachineCard(session: s) { model.select(s.id) }
                    }
                }.padding(20)
            }
        }
        .navigationTitle("All Machines")
        .toolbar {
            ToolbarItem { Button { sheet = .add } label: { Label("Add Machine", systemImage: "plus") } }
        }
        .sheet(item: $sheet) { sh in
            switch sh { case .add: MachineEditor(existing: nil); case .edit(let m): MachineEditor(existing: m) }
        }
    }
}

struct MachineCard: View {
    @Bindable var session: MachineSession
    var onOpen: () -> Void
    @Environment(AppModel.self) private var model
    @State private var editing = false

    /// Every card reserves the same number of stat pills and storage rows — regardless of how much
    /// data a given machine actually reports — so grid rows stay the same height.
    private static let storageSlots = 2

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            VStack(spacing: 6) {
                HStack(spacing: 8) {
                    statPill("CPU", Fmt.percent(session.cpuPercent))
                    statPill("RAM", Fmt.percent(session.memTotal > 0 ? session.memUsedFraction * 100 : nil))
                    statPill("GPU", Fmt.percent(session.gpuPercent))
                }
                HStack(spacing: 8) {
                    statPill("Net", Fmt.rate(session.netTotalRate))
                    statPill("Alerts", "\(session.alerts.count)", color: session.alerts.isEmpty ? .secondary : .orange)
                }
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(session.kind == .truenas ? "Pools" : "Storage").font(.caption2).foregroundStyle(.secondary)
                ForEach(0..<Self.storageSlots, id: \.self) { i in
                    if i < session.storage.count {
                        let item = session.storage[i]
                        VStack(alignment: .leading, spacing: 2) {
                            HStack { Text(item.title).font(.caption).lineLimit(1); Spacer(); Text("\(Int(item.usedFraction * 100))%").font(.caption2).foregroundStyle(.secondary) }
                            UsageBar(fraction: item.usedFraction)
                        }
                    } else {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack { Text(" ").font(.caption); Spacer() }
                            UsageBar(fraction: 0).opacity(0)
                        }
                    }
                }
            }
            Spacer(minLength: 0)
            Text(session.isOnline ? "Up \(Fmt.uptime(session.hostUptime))" : (session.isConfigured ? "Offline" : "Needs a password — Edit…"))
                .font(.caption2).foregroundStyle(.secondary)
            Button("Open") { onOpen() }.buttonStyle(.bordered).controlSize(.small)
        }
        .padding(14)
        .frame(height: 272, alignment: .top)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
        .sheet(isPresented: $editing) { MachineEditor(existing: session.machine) }
    }

    private var header: some View {
        HStack {
            Image(systemName: session.kind.icon)
            StatusDot(state: session.state)
            Text(session.displayName).font(.headline).lineLimit(1)
            Spacer()
            Menu {
                Button("Edit…") { editing = true }
                Button("Remove", role: .destructive) { model.remove(session.id) }
            } label: { Image(systemName: "ellipsis.circle") }.menuIndicator(.hidden).fixedSize()
        }
    }

    private func statPill(_ label: String, _ value: String, color: Color = .accentColor) -> some View {
        VStack(spacing: 1) {
            Text(value).font(.callout.bold()).foregroundStyle(color).lineLimit(1).minimumScaleFactor(0.7)
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity)
    }
}
