import SwiftUI
import WidgetKit

struct Entry: TimelineEntry {
    let date: Date
    let machines: [MachineSnapshot]
}

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> Entry { Entry(date: .now, machines: [.placeholder]) }
    func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) {
        completion(Entry(date: .now, machines: context.isPreview ? [.placeholder] : SharedStore.load()))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
        let e = Entry(date: .now, machines: SharedStore.load())
        completion(Timeline(entries: [e], policy: .after(.now.addingTimeInterval(15 * 60))))
    }
}

private extension MachineSnapshot {
    var isStale: Bool { !online || Date().timeIntervalSince(updated) > 10 * 60 }
    var healthy: Bool { online && alertCount == 0 }
    var statusColor: Color { isStale ? .red : (healthy ? .green : .orange) }
}

struct EmptyState: View {
    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: "server.rack").font(.title)
            Text("No data yet.\nOpen HomeLab Management and keep it running.").font(.caption).multilineTextAlignment(.center)
        }.foregroundStyle(.secondary)
    }
}

struct Gauge2: View {
    let title: String, value: Double?
    var body: some View {
        VStack(spacing: 2) {
            Gauge(value: min(max((value ?? 0) / 100, 0), 1)) {
                Text(title)
            } currentValueLabel: {
                Text(Fmt.percent(value)).font(.caption.bold())
            }.gaugeStyle(.accessoryCircularCapacity).tint(.accentColor)
            Text(title).font(.caption2).foregroundStyle(.secondary)
        }
    }
}

struct StorageBar: View {
    let item: StorageStat
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(item.title).font(.caption.bold()).lineLimit(1)
                Spacer()
                Text("\(Int(item.usedFraction * 100))%").font(.caption2).foregroundStyle(.secondary)
            }
            ProgressView(value: item.usedFraction).tint(item.usedFraction > 0.85 ? .orange : .accentColor)
            Text("\(Fmt.bytes(item.used)) / \(Fmt.bytes(item.size))").font(.system(size: 9)).foregroundStyle(.secondary)
        }
    }
}

struct StatusView: View {
    @Environment(\.widgetFamily) private var family
    let entry: Entry

    var body: some View {
        if let s = entry.machines.first {
            if family == .systemSmall { small(s) } else { medium(s) }
        } else { EmptyState() }
    }

    private func header(_ s: MachineSnapshot) -> some View {
        HStack(spacing: 6) {
            Image(systemName: s.kind.icon).font(.caption)
            Circle().fill(s.statusColor).frame(width: 9, height: 9)
            Text(s.name).font(.headline).lineLimit(1)
            Spacer()
        }
    }

    private func small(_ s: MachineSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            header(s)
            HStack { Gauge2(title: "CPU", value: s.cpu); Gauge2(title: "RAM", value: s.memory.map { $0 * 100 }) }
                .frame(maxWidth: .infinity)
            Text(s.isStale ? "Offline" : (s.alertCount > 0 ? "\(s.alertCount) alert(s)" : "Up \(Fmt.uptime(s.uptime))"))
                .font(.caption2).foregroundStyle(s.alertCount > 0 ? .orange : .secondary)
        }
    }

    private func medium(_ s: MachineSnapshot) -> some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                header(s)
                HStack { Gauge2(title: "CPU", value: s.cpu); Gauge2(title: "RAM", value: s.memory.map { $0 * 100 }) }
                Text(s.isStale ? "Offline" : "Up \(Fmt.uptime(s.uptime))").font(.caption2).foregroundStyle(.secondary)
            }.frame(maxWidth: 170)
            VStack(alignment: .leading, spacing: 6) {
                ForEach(s.storage.prefix(3)) { StorageBar(item: $0) }
                Spacer(minLength: 0)
                if s.alertCount > 0 { Label("\(s.alertCount)", systemImage: "exclamationmark.triangle.fill").font(.caption2).foregroundStyle(.orange) }
            }
        }
    }
}

struct AllMachinesView: View {
    @Environment(\.widgetFamily) private var family
    let entry: Entry

    var body: some View {
        if entry.machines.isEmpty { EmptyState() } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("HomeLab machines").font(.headline)
                ForEach(entry.machines.prefix(family == .systemLarge ? 8 : 3)) { m in
                    Link(destination: URL(string: "homelabmanagement://machine/\(m.id.uuidString)")!) {
                        HStack(spacing: 8) {
                            Image(systemName: m.kind.icon).font(.caption2)
                            Circle().fill(m.statusColor).frame(width: 8, height: 8)
                            Text(m.name).font(.caption.bold()).lineLimit(1).frame(width: 80, alignment: .leading)
                            bar("CPU", m.cpu)
                            bar("RAM", m.memory.map { $0 * 100 })
                        }
                    }
                }
                Spacer(minLength: 0)
            }
        }
    }

    private func bar(_ title: String, _ v: Double?) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("\(title) \(Fmt.percent(v))").font(.system(size: 9)).foregroundStyle(.secondary)
            ProgressView(value: min(max((v ?? 0) / 100, 0), 1)).tint((v ?? 0) > 90 ? .orange : .accentColor)
        }.frame(maxWidth: .infinity)
    }
}

struct StatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "HomeLabStatus", provider: Provider()) { entry in
            StatusView(entry: entry).containerBackground(.fill.tertiary, for: .widget)
                .widgetURL(entry.machines.first.flatMap { URL(string: "homelabmanagement://machine/\($0.id.uuidString)") })
        }
        .configurationDisplayName("Selected Machine")
        .description("CPU, memory and storage of the machine selected in the app.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct AllMachinesWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "HomeLabAll", provider: Provider()) { entry in
            AllMachinesView(entry: entry).containerBackground(.fill.tertiary, for: .widget)
                .widgetURL(URL(string: "homelabmanagement://overview"))
        }
        .configurationDisplayName("All Machines")
        .description("Every managed machine at a glance; click one to open it.")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}

@main
struct HomeLabWidgetBundle: WidgetBundle {
    var body: some Widget {
        StatusWidget()
        AllMachinesWidget()
    }
}
