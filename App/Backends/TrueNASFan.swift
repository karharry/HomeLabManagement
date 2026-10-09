import Foundation

/// Client for the optional `fanctl` container some TrueNAS boxes run on a dedicated port.
struct FanStatus: Decodable, Equatable, Sendable {
    struct Temp: Decodable, Equatable, Sendable { var kind: String; var temp: Double }
    var temps: [String: Temp] = [:]
    var ctlTemp: Double?
    var pwmPct: Double?
    var rpm: Int?
    var error: String?
    var src: String?
}

struct FanConfig: Codable, Equatable, Sendable {
    var mode: String
    var manualPct: Double
    var minPct: Double
    var cpuOffset: Double
    var hysteresis: Double
    var curve: [[Double]]
    var interval: Int?

    enum CodingKeys: String, CodingKey {
        case mode, hysteresis, curve, interval
        case manualPct = "manual_pct", minPct = "min_pct", cpuOffset = "cpu_offset"
    }

    var normalized: FanConfig {
        var c = self
        c.curve = curve.sorted { $0[0] < $1[0] }
        return c
    }
}

struct FanClient: Sendable {
    let base: URL
    let password: String
    private static let session = URLSession(configuration: {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 15
        return c
    }())

    private func send(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = method
        req.httpBody = body
        if !password.isEmpty {
            req.setValue("Basic " + Data("admin:\(password)".utf8).base64EncodedString(), forHTTPHeaderField: "Authorization")
        }
        let (data, resp) = try await Self.session.data(for: req)
        if let http = resp as? HTTPURLResponse, http.statusCode >= 400 {
            if http.statusCode == 401 { throw TrueNASError.auth("The fan controller needs a password.") }
            let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            throw TrueNASError.rpc(code: http.statusCode, message: msg ?? "Fan controller returned HTTP \(http.statusCode).")
        }
        return data
    }

    func status() async throws -> FanStatus {
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(FanStatus.self, from: try await send("api/status"))
    }
    func config() async throws -> FanConfig {
        try JSONDecoder().decode(FanConfig.self, from: try await send("api/config"))
    }
    func save(_ c: FanConfig) async throws -> FanConfig {
        let body = try JSONEncoder().encode(c.normalized)
        return try JSONDecoder().decode(FanConfig.self, from: try await send("api/config", method: "POST", body: body))
    }
}
