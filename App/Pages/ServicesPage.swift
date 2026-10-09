import SwiftUI

struct ServicesPage: View {
    @Environment(MachineSession.self) private var s
    @State private var filter = ""

    private var filtered: [ServiceRow] {
        filter.isEmpty ? s.services : s.services.filter { $0.displayName.localizedCaseInsensitiveContains(filter) }
    }

    var body: some View {
        Group {
            if !s.servicesLoaded {
                ProgressView("Loading services…")
            } else {
                List(filtered) { svc in
                    HStack {
                        Circle().fill(svc.isRunning ? .green : .gray).frame(width: 8, height: 8)
                        VStack(alignment: .leading) {
                            Text(svc.displayName).font(.body)
                            Text([svc.state, svc.startMode].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if svc.canControl {
                            Menu {
                                Button("Start") { Task { await s.serviceAction(svc, verb: "start") } }
                                Button("Stop") { Task { await s.serviceAction(svc, verb: "stop") } }
                                Button("Restart") { Task { await s.serviceAction(svc, verb: "restart") } }
                            } label: { Image(systemName: "ellipsis.circle") }.menuIndicator(.hidden).fixedSize()
                        }
                    }
                }
                .searchable(text: $filter, placement: .toolbar)
            }
        }
        .navigationTitle("Services")
        .task { await s.refreshServices() }
        .alert("Action failed", isPresented: Binding(get: { s.actionError != nil }, set: { if !$0 { s.actionError = nil } })) {
            Button("OK") {}
        } message: { Text(s.actionError ?? "") }
    }
}
