import Foundation

struct PiholeError: LocalizedError {
    var errorDescription: String?
    init(_ m: String) { errorDescription = m }
}

/// Pi-hole v6 REST API client. v6 dropped the unauthenticated `pihole -c -j` stats dump, so stats and local DNS
/// records need the web admin password: POST /api/auth returns a session id (sent back as X-FTL-SID) and a CSRF
/// token (X-FTL-CSRF on state-changing calls). Built from Pi-hole's API docs, not verified against a live box —
/// errors carry the HTTP status so a wrong guess is visible rather than silently empty.
actor PiholeClient {
    private let base: String
    private let password: String
    private var sid: String?
    private var csrf: String?

    init(host: String, password: String) {
        base = "http://\(host)/api"
        self.password = password
    }

    func get(_ path: String) async throws -> [String: Any] { try await request("GET", path, nil) }
    func patch(_ path: String, _ body: [String: Any]) async throws { _ = try await request("PATCH", path, body) }

    private func login() async throws {
        guard let url = URL(string: "\(base)/auth") else { throw PiholeError("Bad Pi-hole address.") }
        var req = URLRequest(url: url, timeoutInterval: 10)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["password": password])
        let (data, resp) = try await send(req)
        guard (resp as? HTTPURLResponse)?.statusCode ?? 0 < 400 else { throw PiholeError("Pi-hole login failed — check the web password.") }
        let session = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["session"] as? [String: Any]
        guard session?["valid"] as? Bool != false, let s = session?["sid"] as? String else { throw PiholeError("Pi-hole rejected that password.") }
        sid = s
        csrf = session?["csrf"] as? String
    }

    private func request(_ method: String, _ path: String, _ body: [String: Any]?, retry: Bool = true) async throws -> [String: Any] {
        if sid == nil { try await login() }
        guard let url = URL(string: "\(base)/\(path)") else { throw PiholeError("Bad Pi-hole path.") }
        var req = URLRequest(url: url, timeoutInterval: 10)
        req.httpMethod = method
        req.setValue(sid, forHTTPHeaderField: "X-FTL-SID")
        if method != "GET", let csrf { req.setValue(csrf, forHTTPHeaderField: "X-FTL-CSRF") }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await send(req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401, retry { sid = nil; csrf = nil; return try await request(method, path, body, retry: false) }
        guard code < 400 else { throw PiholeError("Pi-hole returned HTTP \(code) for \(path).") }
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    private func send(_ req: URLRequest) async throws -> (Data, URLResponse) {
        do { return try await URLSession.shared.data(for: req) }
        catch { throw PiholeError("Couldn't reach Pi-hole: \(error.localizedDescription)") }
    }
}
