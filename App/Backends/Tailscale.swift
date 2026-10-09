import Foundation

/// Shared by every kind that can run the `tailscale` CLI (Linux, macOS).
struct TailscalePeer: Sendable, Identifiable, Hashable {
    var id: String
    var hostName: String
    var ip: String
    var os: String
    var online: Bool
    var isSelf: Bool
    var isExitNode: Bool
}

struct TailscaleStatus: Sendable {
    var backendState: String
    var peers: [TailscalePeer]
    var isRunning: Bool { backendState == "Running" }
}

/// Conformed by every session kind that can run the `tailscale` CLI (Linux, macOS).
@MainActor
protocol TailscaleHosting: AnyObject {
    var tailscaleStatus: TailscaleStatus? { get }
    var tailscaleError: String? { get }
    func refreshTailscale() async
}

enum TailscaleParser {
    static func parse(_ text: String) -> TailscaleStatus? {
        guard let data = text.data(using: .utf8), let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        let backendState = obj["BackendState"] as? String ?? "Unknown"
        var peers: [TailscalePeer] = []
        func peer(_ id: String, _ p: [String: Any], isSelf: Bool) -> TailscalePeer {
            TailscalePeer(id: id, hostName: p["HostName"] as? String ?? id,
                         ip: (p["TailscaleIPs"] as? [String])?.first ?? "",
                         os: p["OS"] as? String ?? "", online: isSelf ? true : (p["Online"] as? Bool ?? false),
                         isSelf: isSelf, isExitNode: p["ExitNode"] as? Bool ?? false)
        }
        if let selfP = obj["Self"] as? [String: Any] { peers.append(peer(selfP["ID"] as? String ?? "self", selfP, isSelf: true)) }
        if let peerMap = obj["Peer"] as? [String: [String: Any]] {
            for (id, p) in peerMap { peers.append(peer(id, p, isSelf: false)) }
        }
        peers.sort { $0.isSelf != $1.isSelf ? $0.isSelf : $0.hostName.localizedStandardCompare($1.hostName) == .orderedAscending }
        return TailscaleStatus(backendState: backendState, peers: peers)
    }
}
