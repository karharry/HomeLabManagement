import SwiftUI

struct RaspberryPiPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let l = session as? LinuxSession {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 12) {
                        Card(title: "Temperature") {
                            Text(l.piTemp.map { String(format: "%.1f°C", $0) } ?? "–").font(.system(size: 24, weight: .semibold, design: .rounded))
                                .foregroundStyle((l.piTemp ?? 0) >= 75 ? .orange : .primary)
                        }
                        Card(title: "ARM clock") {
                            Text(l.piClockMHz.map { Fmt.mhz($0) } ?? "–").font(.system(size: 24, weight: .semibold, design: .rounded))
                        }
                    }
                    Card(title: "Throttling") {
                        if l.piThrottled.isEmpty {
                            Label("Nothing currently active", systemImage: "checkmark.seal").foregroundStyle(.secondary)
                        } else {
                            ForEach(l.piThrottled, id: \.self) { f in
                                Label(f, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                            }
                        }
                    }
                }.padding(20)
            }
            .navigationTitle("Raspberry Pi")
            .task { await every(10) { await l.refreshRaspberryPi() } }
        } else { ProgressView() }
    }
}

struct LinuxScheduledTasksPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let l = session as? LinuxSession {
            List {
                Section("Cron jobs") {
                    if l.cronJobs.isEmpty { Text("No crontab entries for this user.").foregroundStyle(.secondary) }
                    ForEach(l.cronJobs, id: \.self) { Text($0).font(.system(size: 12, design: .monospaced)) }
                }
                Section("systemd timers") {
                    if l.timers.isEmpty { Text("No active timers.").foregroundStyle(.secondary) }
                    ForEach(l.timers, id: \.self) { Text($0).font(.system(size: 12, design: .monospaced)).lineLimit(1) }
                }
            }
            .navigationTitle("Scheduled Tasks")
            .task { await l.refreshScheduledTasks() }
        } else { ProgressView() }
    }
}
