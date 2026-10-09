import SwiftUI

struct ProcessesPage: View {
    @Environment(MachineSession.self) private var s

    var body: some View {
        Table(s.processes.sorted { $0.cpu > $1.cpu }) {
            TableColumn("PID") { Text("\($0.pid)").monospacedDigit() }.width(60)
            TableColumn("Name", value: \.name)
            TableColumn("User", value: \.user)
            TableColumn("CPU") { Text(Fmt.percent($0.cpu)).monospacedDigit() }.width(70)
            TableColumn("Memory") { Text(Fmt.bytes($0.mem)).monospacedDigit() }.width(90)
            TableColumn("") { p in
                Button("Kill") { Task { await s.killProcess(p) } }.buttonStyle(.bordered).controlSize(.small)
            }.width(60)
        }
        .navigationTitle("Processes")
        .task { await every(3) { await s.refreshProcesses() } }
    }
}
