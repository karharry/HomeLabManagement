import Charts
import SwiftUI

/// Runs `work` repeatedly until the surrounding `.task` is cancelled (when the page goes away).
func every(_ seconds: Double, _ work: () async -> Void) async {
    while !Task.isCancelled {
        await work()
        try? await Task.sleep(for: .seconds(seconds))
    }
}

struct Card<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !title.isEmpty { Text(title).font(.subheadline).foregroundStyle(.secondary) }
            content
        }
        .padding(14).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct MeterCard: View {
    let title: String, value: Double?, caption: String
    var body: some View {
        Card(title: title) {
            Text(Fmt.percent(value)).font(.system(size: 30, weight: .semibold, design: .rounded))
            UsageBar(fraction: (value ?? 0) / 100)
            Text(caption).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        }
    }
}

struct Sparkline: View {
    let values: [Double]
    var maxValue: Double? = nil
    var height: CGFloat = 50
    var body: some View {
        Chart(Array(values.enumerated()), id: \.offset) { i, v in
            AreaMark(x: .value("t", i), y: .value("v", v)).foregroundStyle(.tint.opacity(0.2))
            LineMark(x: .value("t", i), y: .value("v", v)).foregroundStyle(.tint)
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: 0...max(maxValue ?? (values.max() ?? 1) * 1.2, 1))
        .frame(height: height)
    }
}

struct UsageBar: View {
    let fraction: Double
    var body: some View {
        ProgressView(value: min(max(fraction, 0), 1)).tint(fraction >= 0.95 ? .red : (fraction >= 0.85 ? .orange : .accentColor))
    }
}

struct StatRow: View {
    let label: String, value: String
    var body: some View {
        HStack { Text(label).foregroundStyle(.secondary); Spacer(); Text(value).monospacedDigit().multilineTextAlignment(.trailing) }
            .font(.callout)
    }
}

struct StatusDot: View {
    let state: ConnectionState
    var body: some View { Circle().fill(color).frame(width: 8, height: 8) }
    private var color: Color {
        switch state { case .connected: .green; case .connecting: .yellow; case .disconnected: .gray; case .failed: .red }
    }
}
