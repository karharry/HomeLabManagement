import Charts
import SwiftUI

struct CurvePoint: Identifiable, Equatable {
    var id = UUID()
    var temp: Double
    var pct: Double
}

struct TrueNASFanPage: View {
    @Environment(MachineSession.self) private var session
    @State private var draft: FanConfig?
    @State private var points: [CurvePoint] = []
    @State private var dragging: UUID?
    @State private var saving = false

    private var assembled: FanConfig? {
        guard var d = draft else { return nil }
        d.curve = points.map { [$0.temp, $0.pct] }
        return d.normalized
    }

    var body: some View {
        if let tn = session as? TrueNASSession {
            content(tn)
        } else { ProgressView() }
    }

    @ViewBuilder
    private func content(_ tn: TrueNASSession) -> some View {
        let dirty = assembled != nil && assembled != tn.fanConfig?.normalized
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let e = tn.fanError {
                    Label(e, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange).font(.callout)
                }
                if let e = tn.fanStatus?.error {
                    Label("\(e) — the controller forces the fan to 100%.", systemImage: "exclamationmark.octagon.fill")
                        .foregroundStyle(.red).font(.callout)
                }
                readings(tn)
                if draft != nil {
                    modeCard(tn, dirty: dirty)
                    curveCard(tn, dirty: dirty)
                    tuningCard(tn, dirty: dirty)
                } else if tn.fanError == nil {
                    HStack { ProgressView().controlSize(.small); Text("Connecting to the fan controller…").foregroundStyle(.secondary) }
                }
                temperatures(tn)
            }.padding(20)
        }
        .navigationTitle("Fan Control")
        .onAppear { tn.setFanActive(true); syncDraft(tn, force: true, dirty: dirty) }
        .onDisappear { tn.setFanActive(false) }
        .onChange(of: tn.fanConfig) { syncDraft(tn, force: false, dirty: dirty) }
    }

    private func syncDraft(_ tn: TrueNASSession, force: Bool, dirty: Bool) {
        guard let c = tn.fanConfig, force || !dirty else { return }
        draft = c
        points = c.normalized.curve.map { CurvePoint(temp: $0[0], pct: $0[1]) }
    }

    private func readings(_ tn: TrueNASSession) -> some View {
        let s = tn.fanStatus
        return LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 3), spacing: 12) {
            TNTile("Fan speed", s?.rpm.map { "\($0) RPM" } ?? "–", "", tn.fanRPMHistory, tint: .blue)
            TNTile("PWM duty", s?.pwmPct.map { "\(Int($0))%" } ?? "–", tn.fanConfig?.mode == "manual" ? "manual" : "automatic curve", tn.fanDutyHistory, fixedMax: 100, tint: .orange)
            TNTile("Control temp", s?.ctlTemp.map { String(format: "%.1f°C", $0) } ?? "–", s?.src.map { "from \($0)" } ?? "", tn.fanTempHistory, tint: .red)
        }
    }

    private func modeCard(_ tn: TrueNASSession, dirty: Bool) -> some View {
        Card(title: "Mode") {
            Picker("Mode", selection: Binding(get: { draft?.mode ?? "auto" }, set: { draft?.mode = $0; Task { await save(tn) } })) {
                Text("Automatic (curve)").tag("auto")
                Text("Manual").tag("manual")
            }.pickerStyle(.segmented).labelsHidden()
            if draft?.mode == "manual" {
                HStack {
                    Slider(value: Binding(get: { draft?.manualPct ?? 0 }, set: { draft?.manualPct = $0 }), in: 0...100, step: 1) { editing in
                        if !editing { Task { await save(tn) } }
                    }
                    Text("\(Int(draft?.manualPct ?? 0))%").monospacedDigit().frame(width: 48, alignment: .trailing)
                }
                if (draft?.manualPct ?? 100) < 20 {
                    Label("Very low speeds can overheat the drives.", systemImage: "thermometer.high").font(.caption).foregroundStyle(.orange)
                }
            }
        }
    }

    private func curveCard(_ tn: TrueNASSession, dirty: Bool) -> some View {
        Card(title: "Fan curve (drag points to edit)") {
            curveChart(tn).frame(height: 240)
            VStack(spacing: 6) {
                ForEach($points) { $p in
                    HStack {
                        Text("At").foregroundStyle(.secondary)
                        TextField("°C", value: $p.temp, format: .number.precision(.fractionLength(0...1))).frame(width: 60)
                        Text("°C →").foregroundStyle(.secondary)
                        TextField("%", value: $p.pct, format: .number.precision(.fractionLength(0...1))).frame(width: 60)
                        Text("%").foregroundStyle(.secondary)
                        Button(role: .destructive) { points.removeAll { $0.id == p.id } } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless).disabled(points.count <= 2).padding(.leading, 6)
                        Spacer(minLength: 0)
                    }
                }
                HStack {
                    Button { let l = points.map(\.temp).max() ?? 50; points.append(CurvePoint(temp: l + 5, pct: 100)) } label: { Label("Add point", systemImage: "plus.circle") }
                        .buttonStyle(.borderless)
                    Spacer()
                }
            }.textFieldStyle(.roundedBorder).controlSize(.small)
            saveBar(tn, dirty: dirty)
        }
    }

    private func curveChart(_ tn: TrueNASSession) -> some View {
        let lo = min(20, (points.map(\.temp).min() ?? 20) - 2), hi = max(70, (points.map(\.temp).max() ?? 70) + 5)
        let sorted = points.sorted { $0.temp < $1.temp }
        return Chart {
            ForEach(sorted) { p in
                LineMark(x: .value("Temp", p.temp), y: .value("Duty", p.pct)).foregroundStyle(.blue)
                PointMark(x: .value("Temp", p.temp), y: .value("Duty", p.pct)).foregroundStyle(.blue).symbolSize(dragging == p.id ? 160 : 80)
            }
            if let t = tn.fanStatus?.ctlTemp {
                RuleMark(x: .value("Now", t)).foregroundStyle(.red).lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                    .annotation(position: .top, alignment: .leading) { Text(String(format: "%.0f°C now", t)).font(.caption2).foregroundStyle(.red) }
            }
        }
        .chartXScale(domain: lo...hi)
        .chartYScale(domain: 0...105)
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 8)) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Int(d))°") } } } }
        .chartYAxis { AxisMarks(values: [0, 25, 50, 75, 100]) { v in AxisGridLine(); AxisValueLabel { if let d = v.as(Double.self) { Text("\(Int(d))%") } } } }
        .chartOverlay { proxy in
            GeometryReader { geo in
                Rectangle().fill(.clear).contentShape(Rectangle())
                    .gesture(DragGesture(minimumDistance: 0)
                        .onChanged { drag in
                            guard let frame = proxy.plotFrame else { return }
                            let origin = geo[frame].origin
                            let loc = CGPoint(x: drag.location.x - origin.x, y: drag.location.y - origin.y)
                            if dragging == nil {
                                let start = CGPoint(x: drag.startLocation.x - origin.x, y: drag.startLocation.y - origin.y)
                                dragging = points.min { dist($0, proxy, start) < dist($1, proxy, start) }
                                    .flatMap { dist($0, proxy, start) < 28 ? $0.id : nil }
                            }
                            guard let id = dragging, let i = points.firstIndex(where: { $0.id == id }),
                                  let t: Double = proxy.value(atX: loc.x), let p: Double = proxy.value(atY: loc.y) else { return }
                            points[i].temp = min(max(t.rounded(), lo), hi)
                            points[i].pct = min(max(p.rounded(), 0), 100)
                        }
                        .onEnded { _ in dragging = nil })
            }
        }
    }

    private func dist(_ p: CurvePoint, _ proxy: ChartProxy, _ at: CGPoint) -> CGFloat {
        guard let pos = proxy.position(for: (x: p.temp, y: p.pct)) else { return .infinity }
        return hypot(pos.x - at.x, pos.y - at.y)
    }

    private func tuningCard(_ tn: TrueNASSession, dirty: Bool) -> some View {
        Card(title: "Tuning") {
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                tuningRow("Minimum speed (auto)", "%", Binding(get: { draft?.minPct ?? 0 }, set: { draft?.minPct = $0 }), 0...100)
                tuningRow("CPU offset (CPU temp minus this)", "°C", Binding(get: { draft?.cpuOffset ?? 0 }, set: { draft?.cpuOffset = $0 }), 0...40)
                tuningRow("Hysteresis", "°C", Binding(get: { draft?.hysteresis ?? 0 }, set: { draft?.hysteresis = $0 }), 0...10)
            }
            saveBar(tn, dirty: dirty)
        }
    }

    private func tuningRow(_ title: String, _ unit: String, _ value: Binding<Double>, _ range: ClosedRange<Double>) -> some View {
        GridRow {
            Text(title)
            TextField("", value: value, format: .number.precision(.fractionLength(0...1))).frame(width: 70).textFieldStyle(.roundedBorder)
            Text(unit).foregroundStyle(.secondary)
            Stepper("", value: value, in: range).labelsHidden()
        }
    }

    private func saveBar(_ tn: TrueNASSession, dirty: Bool) -> some View {
        HStack {
            Button("Revert") { syncDraft(tn, force: true, dirty: dirty) }.disabled(!dirty || saving)
            Button("Save") { Task { await save(tn) } }.buttonStyle(.borderedProminent).disabled(!dirty || saving)
            if saving { ProgressView().controlSize(.small) }
            if dirty { Text("Unsaved changes").font(.caption).foregroundStyle(.orange) }
            Spacer()
        }
    }

    private func temperatures(_ tn: TrueNASSession) -> some View {
        Card(title: "Temperatures") {
            let s = tn.fanStatus
            ForEach((s?.temps ?? [:]).sorted { $0.value.temp > $1.value.temp }, id: \.key) { key, t in
                HStack {
                    Image(systemName: icon(t.kind)).frame(width: 18).foregroundStyle(.secondary)
                    Text(label(key, t.kind))
                    if key == s?.src { Text("controlling").font(.caption2).padding(.horizontal, 6).padding(.vertical, 2).background(.red.opacity(0.15), in: Capsule()).foregroundStyle(.red) }
                    Spacer()
                    Text(String(format: "%.1f°C", t.temp)).monospacedDigit()
                }
            }
            if s == nil { Text("No data yet").foregroundStyle(.secondary) }
        }
    }

    private func icon(_ kind: String) -> String {
        switch kind { case "nvme": "memorychip"; case "coretemp": "cpu"; default: "internaldrive" }
    }
    private func label(_ key: String, _ kind: String) -> String {
        let hw = key.split(separator: ":").last.map(String.init) ?? key
        switch kind { case "nvme": return "NVMe (\(hw))"; case "coretemp": return "CPU package"; default: return "Drive (\(hw))" }
    }

    private func save(_ tn: TrueNASSession) async {
        guard let c = assembled else { return }
        saving = true
        let ok = await tn.saveFan(c)
        saving = false
        if ok { syncDraft(tn, force: true, dirty: false) }
    }
}
