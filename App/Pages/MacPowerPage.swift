import SwiftUI

struct MacPowerPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let m = session as? MacSession {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 12) {
                        Card(title: "Power source") {
                            Text(m.powerSource ?? "–").font(.system(size: 22, weight: .semibold, design: .rounded))
                        }
                        Card(title: "CPU speed limit") {
                            if let v = m.thermalCPUSpeedLimit {
                                Text("\(Int(v))%").font(.system(size: 22, weight: .semibold, design: .rounded)).foregroundStyle(v < 100 ? .orange : .primary)
                            } else {
                                Text("100%").font(.system(size: 22, weight: .semibold, design: .rounded))
                                Text("No throttling recorded").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Card(title: "Scheduler limit") {
                            if let v = m.thermalSchedulerLimit {
                                Text("\(Int(v))%").font(.system(size: 22, weight: .semibold, design: .rounded)).foregroundStyle(v < 100 ? .orange : .primary)
                            } else {
                                Text("100%").font(.system(size: 22, weight: .semibold, design: .rounded))
                            }
                        }
                    }
                    if m.hasBattery {
                        HStack(spacing: 12) {
                            Card(title: "Battery") {
                                Text(Fmt.percent(m.batteryPercent)).font(.system(size: 22, weight: .semibold, design: .rounded))
                                Text(m.batteryCharging ? "Charging" : "Not charging").font(.caption).foregroundStyle(.secondary)
                            }
                            Card(title: "Cycle count") {
                                Text(m.batteryCycles.map { "\($0)" } ?? "–").font(.system(size: 22, weight: .semibold, design: .rounded))
                            }
                            Card(title: "Battery health") {
                                Text(m.batteryHealthPercent.map { Fmt.percent($0) } ?? "–").font(.system(size: 22, weight: .semibold, design: .rounded))
                                Text("max vs. design capacity").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    } else {
                        Text("No battery — this looks like a desktop Mac.").font(.caption).foregroundStyle(.secondary)
                    }
                    Text("Detailed component temperatures (CPU/SSD/fans) need `powermetrics`, which requires elevated access and can be slow to sample over SSH, so it isn't polled here. The speed/scheduler limits above come from macOS's own thermal pressure tracking.")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(20)
            }
            .navigationTitle("Power & Thermal")
            .task { await every(15) { await m.refreshPowerThermal() } }
        } else { ProgressView() }
    }
}
