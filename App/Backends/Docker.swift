import Foundation

/// Conformed by every session kind that lists plain Docker containers over SSH (Linux, macOS — TrueNAS
/// has its own richer page that also distinguishes TrueNAS-managed app containers).
@MainActor
protocol ContainerHosting: AnyObject {
    var containers: [DockerContainer] { get }
    var containersLoaded: Bool { get }
    var containersError: String? { get }
    func setContainersActive(_ active: Bool)
    func containerAction(_ c: DockerContainer, verb: String) async
    func containerLogs(_ c: DockerContainer) async -> String
}

/// Conformed by every session kind that offers an ad-hoc command console (Linux, Windows, macOS,
/// and TrueNAS when SSH is configured).
@MainActor
protocol ConsoleHosting: AnyObject {
    var consoleIsAvailable: Bool { get }
    var consoleUnavailableReason: String { get }
    func runConsole(_ command: String) async -> String
}

struct LoggedInUser: Sendable, Identifiable, Hashable {
    var id: String
    var name: String
    var terminal: String
    var host: String
    var loginTime: String
    var idle: String
}

/// Conformed by every session kind that can list currently logged-in users.
@MainActor
protocol UserHosting: AnyObject {
    var users: [LoggedInUser] { get }
    func refreshUsers() async
}

/// Conformed by every session kind that offers raw log tailing with simple filters.
@MainActor
protocol LogHosting: AnyObject {
    var logLines: [String] { get }
    func refreshLogs(filter: String) async
}

struct UpdateItem: Sendable, Identifiable, Hashable {
    var id: String
    var name: String
    var newVersion: String
    var isSecurity: Bool
    var source: String = ""   // e.g. "Homebrew" — blank means "the OS itself" (apt/pacman/softwareupdate)
}

/// Conformed by every session kind that can list and apply pending OS updates.
@MainActor
protocol UpdateHosting: AnyObject {
    var updates: [UpdateItem] { get }
    var updatesChecked: Bool { get }
    func refreshUpdates() async
    func upgradeAll() async -> String
}

/// Shared by every kind that lists Docker containers over SSH (TrueNAS, Linux, macOS).
struct DockerContainer: Identifiable, Equatable, Sendable {
    var id: String
    var name: String
    var image: String
    var state: String
    var status: String
    var ports: String
    var labels: [String: String] = [:]
    var cpu: String?
    var memory: String?

    var isRunning: Bool { state == "running" }
    /// Containers created by TrueNAS's Apps service use compose projects named "ix-<app>".
    var truenasApp: String? {
        guard let p = labels["com.docker.compose.project"], p.hasPrefix("ix-") else { return nil }
        return String(p.dropFirst(3))
    }
}

enum DockerParser {
    static let sampleScript = """
    docker ps -a --format '{{json .}}'
    echo ---STATS---
    docker stats --no-stream --format '{{json .}}'
    """

    static func parse(_ text: String) -> [DockerContainer] {
        var containers: [DockerContainer] = []
        var stats: [String: (cpu: String, mem: String)] = [:]
        var inStats = false
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = String(raw)
            if line == "---STATS---" { inStats = true; continue }
            guard line.hasPrefix("{"), let obj = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any] else { continue }
            if inStats {
                if let n = obj["Name"] as? String {
                    stats[n] = (obj["CPUPerc"] as? String ?? "", (obj["MemUsage"] as? String ?? "").components(separatedBy: " / ").first ?? "")
                }
            } else {
                var labels: [String: String] = [:]
                for kv in (obj["Labels"] as? String ?? "").split(separator: ",") {
                    if let eq = kv.firstIndex(of: "=") { labels[String(kv[..<eq])] = String(kv[kv.index(after: eq)...]) }
                }
                containers.append(DockerContainer(
                    id: obj["ID"] as? String ?? UUID().uuidString,
                    name: obj["Names"] as? String ?? "?",
                    image: obj["Image"] as? String ?? "",
                    state: obj["State"] as? String ?? "",
                    status: obj["Status"] as? String ?? "",
                    ports: obj["Ports"] as? String ?? "",
                    labels: labels))
            }
        }
        for i in containers.indices {
            if let s = stats[containers[i].name] { containers[i].cpu = s.cpu; containers[i].memory = s.mem }
        }
        return containers.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
}
