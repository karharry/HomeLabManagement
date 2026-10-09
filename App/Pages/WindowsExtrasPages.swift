import SwiftUI

struct WindowsCPUPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let w = session as? WindowsSession {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(w.cpuInfo.name.isEmpty ? "Processor" : w.cpuInfo.name).font(.title.bold())
                        Text(subtitle(w.cpuInfo)).foregroundStyle(.secondary)
                    }
                    Grid(alignment: .top, horizontalSpacing: 12, verticalSpacing: 12) {
                        GridRow {
                            MeterCard(title: "Utilization", value: w.cpuPercent, caption: "")
                            Card(title: "Cache") {
                                StatRow(label: "L2", value: w.cpuInfo.l2KB > 0 ? Fmt.bytes(w.cpuInfo.l2KB * 1024) : "–")
                                StatRow(label: "L3", value: w.cpuInfo.l3KB > 0 ? Fmt.bytes(w.cpuInfo.l3KB * 1024) : "–")
                            }
                            Card(title: "Virtualization") {
                                Text(w.cpuInfo.virtualization == true ? "Enabled" : (w.cpuInfo.virtualization == false ? "Disabled" : "–"))
                                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                            }
                        }
                    }
                    Card(title: "Utilization history") { Sparkline(values: w.cpuHistory, maxValue: 100, height: 100) }
                    Card(title: "Logical processors") {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 10)], spacing: 10) {
                            ForEach(Array(w.cores.enumerated()), id: \.element.id) { i, c in
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack { Text("CPU \(i)").font(.caption.weight(.medium)); Spacer(); Text(Fmt.percent(c.util)).font(.caption).monospacedDigit() }
                                    Sparkline(values: w.coreHistory[c.id] ?? [], maxValue: 100, height: 28)
                                    Text(Fmt.mhz(c.freqMHz)).font(.caption2).foregroundStyle(.secondary)
                                }
                                .padding(8).background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
                            }
                        }
                        if w.cores.isEmpty { Text("No data yet").foregroundStyle(.secondary) }
                    }
                }.padding(20)
            }
            .navigationTitle("CPU")
        } else { ProgressView() }
    }

    private func subtitle(_ i: WinCPUInfo) -> String {
        var p: [String] = []
        if i.cores > 0 { p.append("\(i.cores) cores, \(i.logical) threads") }
        if i.maxMHz > 0 { p.append("base \(Fmt.mhz(i.maxMHz))") }
        return p.joined(separator: " · ")
    }
}

struct WindowsGPUPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let w = session as? WindowsSession {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if w.gpus.isEmpty {
                        ContentUnavailableView("No GPU found", systemImage: "display",
                            description: Text("Windows didn't report a display adapter, or the GPU performance counters aren't available (Windows 10 1709+ with a WDDM 2.0+ driver is needed)."))
                    }
                    ForEach(w.gpus) { adapter(w, $0) }
                    if !w.gpuProcesses.isEmpty {
                        Card(title: "Top GPU processes") {
                            Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 4) {
                                GridRow { Text("Process").foregroundStyle(.secondary); Text("PID").foregroundStyle(.secondary)
                                    Text("GPU").foregroundStyle(.secondary); Text("VRAM").foregroundStyle(.secondary) }.font(.caption)
                                ForEach(w.gpuProcesses) { p in
                                    GridRow { Text(p.name); Text("\(p.pid)").monospacedDigit().foregroundStyle(.secondary)
                                        Text(Fmt.percent(p.util)).monospacedDigit(); Text(Fmt.bytes(p.vram)).monospacedDigit() }
                                }
                            }
                        }
                    }
                }.padding(20)
            }
            .navigationTitle("GPU")
            .task { await every(3) { await w.refreshGPUDetail() } }
        } else { ProgressView() }
    }

    private func adapter(_ w: WindowsSession, _ g: GPUAdapter) -> some View {
        let h = w.gpuHistory[g.id] ?? GPUHistory()
        return VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(g.name).font(.title.bold())
                Text([g.vramTotal.map { "\(Fmt.bytes($0)) VRAM" }, g.driver.isEmpty ? nil : "driver \(g.driver)", g.driverDate.isEmpty ? nil : g.driverDate]
                    .compactMap { $0 }.joined(separator: " · ")).foregroundStyle(.secondary)
            }
            Grid(alignment: .top, horizontalSpacing: 12, verticalSpacing: 12) {
                GridRow {
                    MeterCard(title: "Utilization", value: g.util, caption: g.nvidia.map { "pstate \($0.pstate)" } ?? "busiest engine")
                    MeterCard(title: "Video memory", value: g.vramFraction.map { $0 * 100 },
                              caption: g.vramUsed.map { "\(Fmt.bytes($0))\(g.vramTotal.map { " of \(Fmt.bytes($0))" } ?? "")" } ?? "–")
                    Card(title: "Temperature") {
                        Text(Fmt.temp(g.temp)).font(.system(size: 30, weight: .semibold, design: .rounded))
                        Text(g.temp == nil ? "No sensor exposed" : "GPU core").font(.caption).foregroundStyle(.secondary)
                    }
                    Card(title: "Power") {
                        Text(g.power.map { String(format: "%.0f W", $0) } ?? "–").font(.system(size: 30, weight: .semibold, design: .rounded))
                        Text(g.nvidia?.powerLimit.map { String(format: "limit %.0f W", $0) } ?? (g.power == nil ? "NVIDIA / LibreHardwareMonitor" : "board power")).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            HStack(alignment: .top, spacing: 12) {
                Card(title: "Utilization") { Sparkline(values: h.util, maxValue: 100, height: 110) }
                Card(title: "Video memory") { Sparkline(values: h.vram, maxValue: 100, height: 110) }
            }
            HStack(alignment: .top, spacing: 12) {
                Card(title: "Engines") {
                    if g.engines.isEmpty { Text("Idle").foregroundStyle(.secondary) }
                    ForEach(g.engines.sorted { $0.value > $1.value }, id: \.key) { e in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack { Text(engineName(e.key)).font(.callout); Spacer(); Text(Fmt.percent(e.value)).monospacedDigit().font(.callout) }
                            UsageBar(fraction: e.value / 100)
                        }
                    }
                }
                Card(title: "Details") {
                    if let n = g.nvidia {
                        StatRow(label: "Core clock", value: "\(Fmt.mhz(n.clockGr))\(n.clockMaxGr.map { " / \(Fmt.mhz($0))" } ?? "")")
                        StatRow(label: "Memory clock", value: "\(Fmt.mhz(n.clockMem))\(n.clockMaxMem.map { " / \(Fmt.mhz($0))" } ?? "")")
                        StatRow(label: "Fan", value: n.fan.map { "\(Int($0))%" } ?? "–")
                        StatRow(label: "Memory controller", value: Fmt.percent(n.memUtil))
                    }
                    if !g.processor.isEmpty { StatRow(label: "Chip", value: g.processor) }
                    if !g.status.isEmpty { StatRow(label: "Status", value: g.status) }
                    ForEach(g.sensors.filter { ["Clock", "Fan", "Voltage"].contains($0.type) }) { StatRow(label: $0.name, value: $0.formatted) }
                    if g.nvidia == nil && g.sensors.isEmpty {
                        Text("For clocks, fan speed and power on NVIDIA cards, make sure nvidia-smi is installed. For AMD/Intel, run LibreHardwareMonitor.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func engineName(_ t: String) -> String {
        switch t {
        case "3D": "3D"
        case "VideoDecode": "Video decode"
        case "VideoEncode": "Video encode"
        case "VideoProcessing": "Video processing"
        case "Copy": "Copy"
        default: t.replacingOccurrences(of: "_", with: " ")
        }
    }
}

struct WindowsSensorsPage: View {
    @Environment(MachineSession.self) private var session

    var body: some View {
        if let w = session as? WindowsSession {
            Group {
                if w.sensors.isEmpty {
                    ContentUnavailableView("No sensors found", systemImage: "thermometer.medium",
                        description: Text(emptyDescription(w)))
                } else {
                    List {
                        if w.machine.lhmPort > 0 {
                            Section {
                                Label(w.lhmHTTPWorking == true ? "Reading via LibreHardwareMonitor's Remote Web Server" : "Remote Web Server configured but unreachable — showing WMI data if any",
                                      systemImage: w.lhmHTTPWorking == true ? "checkmark.circle" : "exclamationmark.triangle")
                                    .font(.caption).foregroundStyle(w.lhmHTTPWorking == true ? Color.secondary : Color.orange)
                            }
                        }
                        ForEach(groups(w), id: \.0) { group, items in
                            Section(group) {
                                ForEach(items) { s in StatRow(label: s.name, value: s.formatted) }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Sensors")
        } else { ProgressView() }
    }

    private func emptyDescription(_ w: WindowsSession) -> String {
        if w.machine.lhmPort > 0 {
            return w.lhmHTTPWorking == true
                ? "LibreHardwareMonitor is reachable but isn't reporting any sensors yet."
                : "Couldn't reach LibreHardwareMonitor's Remote Web Server at port \(w.machine.lhmPort). Make sure it's checked in LibreHardwareMonitor's Options menu and the app is still running, then check this machine's Edit sheet for the right port."
        }
        return "Windows doesn't expose temperatures, fan speeds or voltages on its own. In LibreHardwareMonitor, check \u{201C}Remote Web Server\u{201D} under Options and set its port in this machine's Edit sheet \u{2014} that's more reliable than the WMI path this app also tries automatically."
    }

    private func groups(_ w: WindowsSession) -> [(String, [HWSensor])] {
        Dictionary(grouping: w.sensors) { $0.type }
            .sorted { $0.key < $1.key }
            .map { ($0.key, $0.value.sorted { $0.name < $1.name }) }
    }
}
