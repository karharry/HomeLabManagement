import Foundation

/// Package power from Intel RAPL's cumulative energy counter (delta / time = watts). Not wall power, just the
/// closest estimate without a meter. The counter is root-only on most distros, so callers run it with sudo.
struct RAPLSampler {
    static let script = "cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null"
    private var prev: (energy: Double, time: Date)?

    /// nil when there's no counter (AMD/ARM/VM) or this is the first sample.
    mutating func watts(from output: String) -> Double? {
        guard let energy = Double(output.trimmingCharacters(in: .whitespacesAndNewlines)) else { prev = nil; return nil }
        let now = Date.now
        defer { prev = (energy, now) }
        guard let p = prev, energy >= p.energy else { return nil }
        let dt = now.timeIntervalSince(p.time)
        return dt > 0.5 ? (energy - p.energy) / 1_000_000 / dt : nil
    }

    mutating func reset() { prev = nil }
}
