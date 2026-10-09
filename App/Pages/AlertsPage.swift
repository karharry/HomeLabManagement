import SwiftUI

struct AlertsPage: View {
    @Environment(MachineSession.self) private var s

    var body: some View {
        Group {
            if s.alerts.isEmpty {
                ContentUnavailableView("All clear", systemImage: "checkmark.seal", description: Text("Nothing needs attention on \(s.displayName)."))
            } else {
                List(s.alerts) { a in
                    HStack(alignment: .top) {
                        Image(systemName: a.level == .critical ? "xmark.octagon.fill" : (a.level == .warning ? "exclamationmark.triangle.fill" : "info.circle.fill"))
                            .foregroundStyle(a.level == .critical ? .red : (a.level == .warning ? .orange : .blue))
                        VStack(alignment: .leading) {
                            Text(a.title).font(.body.weight(.medium))
                            if !a.detail.isEmpty { Text(a.detail).font(.caption).foregroundStyle(.secondary) }
                        }
                        Spacer()
                        if s.kind == .truenas, let tn = s as? TrueNASSession {
                            Button("Dismiss") { Task { await tn.dismissAlert(a) } }.buttonStyle(.bordered).controlSize(.small)
                        }
                    }.padding(.vertical, 2)
                }
            }
        }
        .navigationTitle("Alerts")
    }
}
