import SwiftUI

struct StoragePage: View {
    @Environment(MachineSession.self) private var s

    var body: some View {
        List {
            Section(s.kind == .truenas ? "Pools" : "Storage") {
                ForEach(s.storage) { item in
                    StorageRow(item: item, session: s)
                }
                if s.storage.isEmpty { Text("No data yet").foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("Storage")
        .alert("Action failed", isPresented: Binding(get: { s.actionError != nil }, set: { if !$0 { s.actionError = nil } })) {
            Button("OK") {}
        } message: { Text(s.actionError ?? "") }
    }
}

private struct StorageRow: View {
    let item: StorageItem
    let session: MachineSession

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            header
            UsageBar(fraction: item.usedFraction)
            if !item.statusText.isEmpty {
                Text(item.statusText).font(.caption2).foregroundStyle(item.healthy ? Color.secondary : Color.red)
            }
            if let tn = session as? TrueNASSession {
                scrubButtons(tn)
            }
        }.padding(.vertical, 3)
    }

    private var header: some View {
        let usedStr = Fmt.bytes(item.used)
        let sizeStr = Fmt.bytes(item.size)
        return HStack {
            Text(item.title).font(.body.weight(.medium))
            if !item.subtitle.isEmpty { Text(item.subtitle).font(.caption).foregroundStyle(.secondary) }
            Spacer()
            Text("\(usedStr) / \(sizeStr)").font(.caption).foregroundStyle(.secondary)
        }
    }

    private func scrubButtons(_ tn: TrueNASSession) -> some View {
        HStack {
            Button("Scrub") { Task { await tn.scrub(item, action: "START") } }
            Button("Stop Scrub") { Task { await tn.scrub(item, action: "STOP") } }
        }.font(.caption).buttonStyle(.bordered).controlSize(.small)
    }
}
