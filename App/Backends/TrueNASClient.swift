import Foundation

enum TrueNASError: LocalizedError {
    case notConnected, timeout, badResponse
    case auth(String)
    case rpc(code: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .notConnected: "Not connected to TrueNAS."
        case .timeout: "The request timed out."
        case .badResponse: "Unexpected response from TrueNAS."
        case .auth(let m): "Authentication failed: \(m)"
        case .rpc(_, let m): m
        }
    }
}

struct TrueNASConnectionConfig: Sendable, Equatable {
    enum Auth: Sendable, Equatable {
        case apiKey(String)
        case password(user: String, password: String)
    }
    var host: String
    var port: Int
    var useTLS: Bool
    var allowSelfSigned: Bool
    var auth: Auth

    var url: URL? {
        URL(string: "\(useTLS ? "wss" : "ws")://\(host):\(port)/api/current")
    }
}

final class TrustDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    let allowSelfSigned: Bool
    init(allowSelfSigned: Bool) { self.allowSelfSigned = allowSelfSigned }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if allowSelfSigned,
           challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}

struct TNRealtime: Sendable {
    var cpu: Double?
    var memoryUsed: Double?
}

/// JSON-RPC 2.0 over WebSocket client for the TrueNAS SCALE middleware (`/api/current`, 24.10+).
actor TrueNASClient {
    private var task: URLSessionWebSocketTask?
    private var session: URLSession?
    private var nextID = 1
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private var receiveTask: Task<Void, Never>?

    private var onRealtime: (@Sendable (TNRealtime) -> Void)?
    private var onClose: (@Sendable (Error?) -> Void)?

    func setHandlers(onRealtime: @escaping @Sendable (TNRealtime) -> Void,
                     onClose: @escaping @Sendable (Error?) -> Void) {
        self.onRealtime = onRealtime
        self.onClose = onClose
    }

    func connect(_ config: TrueNASConnectionConfig) async throws {
        guard let url = config.url else { throw TrueNASError.badResponse }
        let s = URLSession(configuration: .ephemeral,
                           delegate: TrustDelegate(allowSelfSigned: config.allowSelfSigned),
                           delegateQueue: nil)
        let t = s.webSocketTask(with: url)
        t.maximumMessageSize = 64 * 1024 * 1024
        session = s
        task = t
        t.resume()
        receiveTask = Task { [weak self] in await self?.receiveLoop(t) }

        let ok: Bool
        switch config.auth {
        case .apiKey(let key):
            ok = try await call("auth.login_with_api_key", [key], as: Bool.self)
        case .password(let user, let pw):
            ok = try await call("auth.login", [user, pw], as: Bool.self)
        }
        guard ok else { throw TrueNASError.auth("Credentials rejected") }
        _ = try? await callRaw("core.subscribe", ["reporting.realtime"])
    }

    func disconnect() {
        receiveTask?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        session?.invalidateAndCancel()
        task = nil
        session = nil
        failAll(TrueNASError.notConnected)
    }

    func call<T: Decodable>(_ method: String, _ params: [Any] = [], as: T.Type = T.self) async throws -> T {
        let data = try await callRaw(method, params)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do { return try decoder.decode(T.self, from: data) }
        catch { throw TrueNASError.badResponse }
    }

    @discardableResult
    func callRaw(_ method: String, _ params: [Any] = []) async throws -> Data {
        guard let task else { throw TrueNASError.notConnected }
        let id = nextID
        nextID += 1
        let payload: [String: Any] = ["jsonrpc": "2.0", "id": id, "method": method, "params": params]
        let body = try JSONSerialization.data(withJSONObject: payload)
        let text = String(decoding: body, as: UTF8.self)

        return try await withCheckedThrowingContinuation { cont in
            pending[id] = cont
            Task { [weak self] in
                do { try await task.send(.string(text)) }
                catch { await self?.resolve(id, .failure(error)) }
            }
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(45))
                await self?.resolve(id, .failure(TrueNASError.timeout))
            }
        }
    }

    private func resolve(_ id: Int, _ result: Result<Data, Error>) {
        guard let cont = pending.removeValue(forKey: id) else { return }
        cont.resume(with: result)
    }

    private func failAll(_ error: Error) {
        let all = pending
        pending.removeAll()
        for (_, c) in all { c.resume(throwing: error) }
    }

    private func receiveLoop(_ t: URLSessionWebSocketTask) async {
        do {
            while !Task.isCancelled {
                let msg = try await t.receive()
                let data: Data
                switch msg {
                case .string(let s): data = Data(s.utf8)
                case .data(let d): data = d
                @unknown default: continue
                }
                handle(data)
            }
        } catch {
            guard task === t else { return }
            failAll(error)
            onClose?(error)
        }
    }

    private func handle(_ data: Data) {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        if let id = obj["id"] as? Int {
            if let err = obj["error"] as? [String: Any] {
                let code = err["code"] as? Int ?? 0
                var message = err["message"] as? String ?? "Unknown error"
                if let extra = (err["data"] as? [String: Any])?["reason"] as? String { message = extra }
                resolve(id, .failure(TrueNASError.rpc(code: code, message: message)))
            } else {
                let result = obj["result"] ?? NSNull()
                if let d = try? JSONSerialization.data(withJSONObject: result, options: .fragmentsAllowed) {
                    resolve(id, .success(d))
                } else {
                    resolve(id, .failure(TrueNASError.badResponse))
                }
            }
        } else if obj["method"] as? String == "collection_update",
                  let params = obj["params"] as? [String: Any],
                  params["collection"] as? String == "reporting.realtime",
                  let fields = params["fields"] as? [String: Any] {
            var rt = TNRealtime()
            if let cpu = (fields["cpu"] as? [String: Any])?["cpu"] as? [String: Any],
               let u = (cpu["usage"] as? NSNumber)?.doubleValue { rt.cpu = u }
            if let mem = fields["memory"] as? [String: Any],
               let total = (mem["physical_memory_total"] as? NSNumber)?.doubleValue, total > 0,
               let avail = (mem["physical_memory_available"] as? NSNumber)?.doubleValue {
                rt.memoryUsed = 1 - avail / total
            }
            onRealtime?(rt)
        }
    }
}
