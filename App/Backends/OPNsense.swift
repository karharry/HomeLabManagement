import Foundation

struct OPNsenseError: LocalizedError {
    var errorDescription: String?
    init(_ m: String) { errorDescription = m }
}

/// Thin REST client for the OPNsense API. Auth is HTTP Basic with the API key as username and the API secret as
/// password (System > Access > Users > <user> > API keys). Every call is `<module>/<controller>/<command>`.
/// Built from OPNsense's published API docs, not verified against every release: callers read fields defensively
/// (several plausible key names per field) and degrade to "no data" rather than failing.
actor OPNsenseClient {
    private let base: String
    private let auth: String
    private let session: URLSession

    init(host: String, port: Int, key: String, secret: String, useTLS: Bool, allowSelfSigned: Bool) {
        base = "\(useTLS ? "https" : "http")://\(host):\(port)/api"
        auth = "Basic " + Data("\(key):\(secret)".utf8).base64EncodedString()
        session = URLSession(configuration: .ephemeral, delegate: TrustDelegate(allowSelfSigned: allowSelfSigned), delegateQueue: nil)
    }

    func get(_ path: String) async throws -> Any { try await send("GET", path, nil) }
    func post(_ path: String, _ body: [String: Any]? = nil) async throws -> Any { try await send("POST", path, body) }

    private func send(_ method: String, _ path: String, _ body: [String: Any]?) async throws -> Any {
        guard let url = URL(string: "\(base)/\(path)") else { throw OPNsenseError("Bad OPNsense address.") }
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.httpMethod = method
        req.setValue(auth, forHTTPHeaderField: "Authorization")
        if method == "POST" {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body ?? [:])
        }
        let data: Data, resp: URLResponse
        do { (data, resp) = try await session.data(for: req) }
        catch { throw OPNsenseError("Couldn't reach \(url.absoluteString): \(error.localizedDescription)") }
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 { throw OPNsenseError("Authentication failed: check the API key and secret.") }
        if code >= 400 { throw OPNsenseError("OPNsense returned HTTP \(code) for \(path).") }
        return (try? JSONSerialization.jsonObject(with: data)) ?? [String: Any]()
    }
}

struct OPNInterface: Sendable, Identifiable, Hashable {
    var name: String
    var device: String
    var status: String
    var addresses: [String]
    var id: String { device + name }
    var isUp: Bool { ["up", "active", "1"].contains(status.lowercased()) }
}

struct OPNDHCPLease: Sendable, Identifiable, Hashable {
    var ip: String, hostname: String, mac: String, interface: String, state: String
    var id: String { ip + mac }
}

struct OPNDnsmasqHost: Sendable, Identifiable, Hashable {
    var uuid: String, host: String, domain: String, ip: String, mac: String, descr: String
    var id: String { uuid }
    var fqdn: String { domain.isEmpty ? host : "\(host).\(domain)" }
}

/// Manages an OPNsense firewall/router over its REST API (no SSH, so no console).
@MainActor @Observable
final class OPNsenseSession: MachineSession, UpdateHosting {
    var interfaces: [OPNInterface] = []
    var dhcpLeases: [OPNDHCPLease] = []
    var dnsmasqHosts: [OPNDnsmasqHost] = []
    var dnsmasqHostsError: String?
    var updates: [UpdateItem] = []
    var updatesChecked = false
    var firmwareStatus: String?
    private var prevBytes: (rx: Double, tx: Double, time: Date)?

    /// One client (and URLSession) per connection; dropped in `closeBackend` so edited credentials take effect.
    private var cachedClient: OPNsenseClient?
    private var client: OPNsenseClient {
        if let c = cachedClient { return c }
        let c = OPNsenseClient(host: machine.host, port: machine.port, key: machine.user, secret: MachineStore.secret(for: machine.id),
                               useTLS: machine.useTLS, allowSelfSigned: machine.allowSelfSigned)
        cachedClient = c
        return c
    }

    // MARK: Field helpers (defensive: OPNsense field names vary a little by version)

    private static func first(_ d: [String: Any], _ keys: String...) -> Any? {
        for k in keys { if let v = d[k], !(v is NSNull), "\(v)" != "" { return v } }
        return nil
    }
    private static func num(_ d: [String: Any], _ keys: String...) -> Double? {
        for k in keys {
            guard let v = d[k] else { continue }
            if let n = v as? NSNumber { return n.doubleValue }
            if let s = v as? String, let n = Double(s.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "%")).replacingOccurrences(of: ",", with: "")) { return n }
        }
        return nil
    }
    private static func str(_ d: [String: Any], _ keys: String...) -> String {
        for k in keys { if let v = d[k], !(v is NSNull), "\(v)" != "" { return "\(v)" } }
        return ""
    }
    /// Normalizes the shapes OPNsense returns lists in ({"rows": […]}, a bare array, or a dict keyed by id/name).
    private static func rows(_ obj: Any?) -> [[String: Any]] {
        if let d = obj as? [String: Any] {
            if let r = d["rows"] as? [[String: Any]] { return r }
            let nested = d.compactMap { k, v -> [String: Any]? in
                guard var row = v as? [String: Any] else { return nil }
                row["_key"] = k
                return row
            }
            return nested.count == d.count ? nested : []
        }
        return (obj as? [[String: Any]]) ?? []
    }
    private static func running(_ v: Any?) -> Bool {
        if let b = v as? Bool { return b }
        return ["1", "true", "running", "yes"].contains("\(v ?? "")".lowercased())
    }
    /// df-style human sizes like "118G", "512M", "2.1T" → bytes.
    private static func sizeString(_ s: String) -> Double? {
        let t = s.trimmingCharacters(in: .whitespaces)
        guard let last = t.last else { return nil }
        let units: [Character: Double] = ["b": 1, "k": 1024, "m": 1_048_576, "g": 1_073_741_824, "t": 1_099_511_627_776]
        if let u = units[Character(last.lowercased())] { return Double(t.dropLast()).map { $0 * u } }
        return Double(t)
    }
    /// "0+19:22:24" (BSD top), "3 days, 02:14:05" or "02:14:05" → seconds.
    static func uptimeSeconds(_ s: String) -> Double {
        func nums(_ pattern: String) -> [Double]? {
            guard let r = s.range(of: pattern, options: .regularExpression) else { return nil }
            return String(s[r]).split(whereSeparator: { !$0.isNumber }).compactMap { Double($0) }
        }
        if let p = nums(#"\d+\+\d{1,2}:\d{2}:\d{2}"#), p.count == 4 { return p[0] * 86400 + p[1] * 3600 + p[2] * 60 + p[3] }
        var total = 0.0
        if let d = nums(#"\d+\s*day"#)?.first { total += d * 86400 }
        if let p = nums(#"\d{1,2}:\d{2}:\d{2}"#), p.count == 3 { total += p[0] * 3600 + p[1] * 60 + p[2] }
        return total
    }
    /// Newest releases give only `top`'s raw header lines: "… up 0+19:22:24 …" and "CPU: … 98.6% idle".
    static func activitySummary(_ a: [String: Any]) -> (uptime: Double?, cpu: Double?) {
        var up: Double?, cpu: Double?
        for line in (a["headers"] as? [String]) ?? [] {
            if up == nil, let r = line.range(of: #"\bup\s+(.+?)(?:\s+\d{1,2}:\d{2}:\d{2}\s*$|$)"#, options: .regularExpression) {
                up = uptimeSeconds(String(line[r]))
            }
            if cpu == nil, line.trimmingCharacters(in: .whitespaces).hasPrefix("CPU:"),
               let r = line.range(of: #"[\d.]+(?=%\s*idle)"#, options: .regularExpression), let idle = Double(line[r]) {
                cpu = max(0, 100 - idle)
            }
        }
        return (up, cpu)
    }

    // MARK: Poll

    override func poll() async -> Bool {
        if state != .connected { state = .connecting }
        let c = client
        var diag: [String] = []
        func tryGet(_ p: String) async -> Any { do { return try await c.get(p) } catch { diag.append("\(p): \(error.localizedDescription)"); return [String: Any]() } }
        func tryPost(_ p: String) async -> Any { do { return try await c.post(p) } catch { diag.append("\(p): \(error.localizedDescription)"); return [String: Any]() } }

        let info: [String: Any]
        do { info = (try await c.get("diagnostics/system/systemInformation")) as? [String: Any] ?? [:] }
        catch { state = .failed(error.localizedDescription); return false }

        hostName = Self.str(info, "hostname", "name")
        if hostName.isEmpty { hostName = machine.host }
        let version = Self.str(info, "versionstring", "product_version", "version")
        if version.isEmpty, let v = (info["versions"] as? [Any])?.first { hostOS = "\(v)" } else { hostOS = "OPNsense " + version }
        var uptime: Double?
        if let s = info["uptime"] as? String { uptime = Self.uptimeSeconds(s) }
        else if let n = Self.num(info, "uptime", "uptime_seconds") { uptime = n }

        let res = (await tryGet("diagnostics/system/systemResources")) as? [String: Any] ?? [:]
        var cpu = Self.num(res, "cpu", "cpu_usage", "cpu_total", "load")
        if cpu == nil, let d = res["cpu"] as? [String: Any] { cpu = Self.num(d, "total", "used", "usage", "load") }
        if cpu == nil || uptime == nil {
            // systemResources doesn't carry CPU on every version, systemInformation doesn't always carry uptime.
            let act = (await tryGet("diagnostics/activity/getActivity")) as? [String: Any] ?? [:]
            if cpu == nil { cpu = Self.num(act, "cpu", "cpu_usage", "total") }
            if cpu == nil, let d = act["cpu"] as? [String: Any] { cpu = Self.num(d, "total", "used", "usage", "user") }
            if cpu == nil, let idle = Self.rows(act["top"]).first(where: { ["idle", "[idle]"].contains(Self.str($0, "command", "name").lowercased()) }).flatMap({ Self.num($0, "pcpu", "percent") }) {
                cpu = max(0, 100 - idle)
            }
            let s = Self.activitySummary(act)
            cpu = cpu ?? s.cpu
            uptime = uptime ?? s.uptime
            if cpu == nil, !act.isEmpty { diag.append("diagnostics/activity/getActivity: no CPU % found") }
        }
        if let cpu { cpuPercent = min(100, max(0, cpu)) }
        push(&cpuHistory, cpuPercent)
        hostUptime = uptime ?? 0

        let mem = (res["memory"] as? [String: Any]) ?? res
        if let total = Self.num(mem, "total", "memory_total", "total_bytes"), total > 0 {
            memTotal = total
            if let used = Self.num(mem, "used", "memory_used", "used_bytes") { memUsed = used }
            else if let pct = Self.num(mem, "used_pct", "usage", "percent") { memUsed = total * pct / 100 }
            push(&memHistory, memUsed / memTotal * 100)
        } else if let pct = Self.num(mem, "used_pct", "usage", "percent") {
            memTotal = 100; memUsed = pct; push(&memHistory, pct)   // some versions expose only a percentage
        }

        let disk = await tryGet("diagnostics/system/systemDisk")
        var diskRows = Self.rows(disk)
        if diskRows.isEmpty { diskRows = Self.rows((disk as? [String: Any])?["devices"]) }
        let items: [StorageItem] = diskRows.compactMap { row in
            let mount = Self.first(row, "mountpoint", "mounted_on", "mount", "target", "device", "fs", "filesystem", "_key").map { "\($0)" } ?? "disk"
            var size = Self.num(row, "blocks", "size", "total", "1k-blocks", "disk_size") ?? Self.sizeString(Self.str(row, "size", "blocks")) ?? 0
            var used = Self.num(row, "used", "used_bytes") ?? Self.sizeString(Self.str(row, "used"))
            let avail = Self.num(row, "avail", "available", "free") ?? Self.sizeString(Self.str(row, "avail", "available"))
            if used == nil, let avail, size > 0 { used = max(0, size - avail) }
            if used == nil, let pct = Self.num(row, "capacity", "used_pct", "capacity_pct", "pct"), size > 0 { used = size * pct / 100 }
            if size == 0, let used, let avail { size = used + avail }
            return size > 0 ? StorageItem(id: mount, title: mount, size: Int64(size), used: Int64(used ?? 0)) : nil
        }
        if !items.isEmpty { storage = items }
        else { diag.append("diagnostics/system/systemDisk: no usable disk rows found") }

        let svc = Self.rows(await tryPost("core/service/search"))
        if !svc.isEmpty {
            services = svc.map { s in
                let run = Self.running(Self.first(s, "running", "status"))
                return ServiceRow(id: Self.str(s, "id", "name"), name: Self.str(s, "name", "id"), displayName: Self.str(s, "description", "name"),
                                  state: run ? "running" : "stopped", isRunning: run)
            }.sorted { $0.displayName.localizedCaseInsensitiveCompare($1.displayName) == .orderedAscending }
            servicesLoaded = true
        }

        let cfg = Self.rows(await tryGet("diagnostics/interface/getInterfaceConfig"))
        if !cfg.isEmpty {
            interfaces = cfg.map { r in
                let status = Self.str(r, "status")
                return OPNInterface(name: Self.first(r, "description", "_key", "if").map { "\($0)" } ?? "", device: Self.str(r, "if", "_key"),
                                    status: status.isEmpty ? (Self.first(r, "enable") != nil ? "up" : "down") : status,
                                    addresses: [Self.str(r, "ipaddr"), Self.str(r, "ipaddrv6")].filter { !$0.isEmpty && $0.lowercased() != "none" })
            }
        }

        let stats = Self.rows(await tryGet("diagnostics/interface/getInterfaceStatistics"))
        if !stats.isEmpty {
            let rx = stats.reduce(0) { $0 + (Self.num($1, "bytes received", "inbytes", "received_bytes") ?? 0) }
            let tx = stats.reduce(0) { $0 + (Self.num($1, "bytes transmitted", "outbytes", "transmitted_bytes") ?? 0) }
            let now = Date.now
            if let p = prevBytes {
                let dt = now.timeIntervalSince(p.time)
                if dt > 0.5 { netRxRate = max(0, rx - p.rx) / dt; netTxRate = max(0, tx - p.tx) / dt }
            }
            prevBytes = (rx, tx, now)
        }

        // Surfaced as info alerts rather than swallowed: a wrong endpoint guess should be visible and reportable.
        extraAlerts = diag.enumerated().map { AlertItem(id: "diag-\($0.offset)", level: .info, title: "An OPNsense API call didn't return usable data", detail: $0.element) }

        state = .connected
        lastRefresh = .now
        return true
    }

    override func closeBackend() async { prevBytes = nil; cachedClient = nil }

    // MARK: Services / power

    override func refreshServices() async { _ = await poll() }

    override func serviceAction(_ s: ServiceRow, verb: String) async {
        do { _ = try await client.post("core/service/\(verb)/\(s.id)") } catch { actionError = error.localizedDescription }
        try? await Task.sleep(for: .seconds(1))
        _ = await poll()
    }

    override func reboot() async { do { _ = try await client.post("core/system/reboot") } catch { actionError = error.localizedDescription } }
    override func shutdown() async { do { _ = try await client.post("core/system/halt") } catch { actionError = error.localizedDescription } }

    // MARK: DHCP leases — Dnsmasq, legacy ISC or Kea (24.1+) each have their own endpoint; the first that answers wins

    func refreshDHCPLeases() async {
        var errors: [String] = [], rows: [[String: Any]] = [], answered = false
        for path in ["dnsmasq/leases/search", "dhcpv4/leases/search_lease", "kea/leases4/search"] {
            do {
                rows = Self.rows(try await client.get(path))
                answered = true
                if !rows.isEmpty { break }
            } catch { errors.append("\(path): \(error.localizedDescription)") }
        }
        // Only an error if every known endpoint failed; a box with no active leases just shows "none".
        if !answered, !errors.isEmpty { actionError = errors.joined(separator: " / ") }
        dhcpLeases = rows.map {
            let name = Self.str($0, "hostname", "client-hostname")
            return OPNDHCPLease(ip: Self.str($0, "address", "ip", "ip-address"), hostname: name.isEmpty ? "–" : name,
                                mac: Self.str($0, "mac", "hwaddr", "hw-address"), interface: Self.str($0, "if", "interface"), state: Self.str($0, "state", "online"))
        }
    }

    // MARK: Dnsmasq static hosts (double as DHCP reservations and local DNS entries)

    func refreshDnsmasqHosts() async {
        do {
            let rows = Self.rows(try await client.get("dnsmasq/settings/search_host"))
            dnsmasqHosts = rows.map {
                let uuid = Self.str($0, "uuid", "uuidSelector")
                return OPNDnsmasqHost(uuid: uuid.isEmpty ? Self.str($0, "_key") : uuid, host: Self.str($0, "host", "hostname"), domain: Self.str($0, "domain"),
                                      ip: Self.str($0, "ip", "address"), mac: Self.str($0, "hwaddr", "mac", "hw-address"), descr: Self.str($0, "descr", "description"))
            }.sorted { $0.host.localizedCaseInsensitiveCompare($1.host) == .orderedAscending }
            dnsmasqHostsError = nil
        } catch { dnsmasqHostsError = error.localizedDescription }
    }

    /// Returns an error message, or nil on success.
    func addDnsmasqHost(host: String, ip: String, mac: String, domain: String, descr: String) async -> String? {
        let h = host.trimmingCharacters(in: .whitespaces), i = ip.trimmingCharacters(in: .whitespaces)
        guard !h.isEmpty, !i.isEmpty else { return "Enter both a hostname and an IP address." }
        // Field names are a best guess at Dnsmasq's Host model; OPNsense's own "validations" reply is surfaced on rejection.
        let body: [String: Any] = ["host": ["host": h, "domain": domain.trimmingCharacters(in: .whitespaces), "ip": i,
                                            "hwaddr": mac.trimmingCharacters(in: .whitespaces), "descr": descr.trimmingCharacters(in: .whitespaces)]]
        do {
            let r = try await client.post("dnsmasq/settings/add_host", body) as? [String: Any]
            if let result = r?["result"] as? String, !["saved", "ok"].contains(result) { return "OPNsense rejected the host: \(r?["validations"] ?? result)" }
        } catch { return error.localizedDescription }
        await applyDnsmasq()
        return nil
    }

    func removeDnsmasqHost(_ h: OPNDnsmasqHost) async -> String? {
        do { _ = try await client.post("dnsmasq/settings/del_host/\(h.uuid)") } catch { return error.localizedDescription }
        await applyDnsmasq()
        return nil
    }

    /// Model controllers only stage a change; reconfigure applies it to the running service (the web UI's Save).
    private func applyDnsmasq() async {
        _ = try? await client.post("dnsmasq/service/reconfigure")
        await refreshDnsmasqHosts()
    }

    // MARK: Updates (status only — a failed remote firewall update can leave the box unreachable)

    func refreshUpdates() async {
        do {
            let fw = try await client.get("core/firmware/status") as? [String: Any] ?? [:]
            let status = Self.str(fw, "status")
            firmwareStatus = status.isEmpty ? nil : status
            var list: [UpdateItem] = []
            if !["", "none", "none_fetched", "up_to_date", "ok"].contains(status.lowercased()) {
                let v = Self.str(fw, "new_version", "product_version_new")
                list.append(UpdateItem(id: "firmware", name: "OPNsense firmware", newVersion: v.isEmpty ? status : v, isSecurity: false))
            }
            for p in (Self.first(fw, "new_packages", "upgrade_packages") as? [Any]) ?? [] {
                let d = p as? [String: Any]
                let name = d.map { Self.str($0, "name") } ?? "\(p)"
                list.append(UpdateItem(id: "pkg-\(name)", name: name, newVersion: d.map { Self.str($0, "new_version") } ?? "", isSecurity: false, source: "Package"))
            }
            updates = list
            updatesChecked = true
        } catch { actionError = error.localizedDescription }
    }

    func upgradeAll() async -> String {
        "Applying firmware/package updates remotely isn't offered here — a failed or interrupted firewall update can leave the box unreachable. Use System > Firmware in the OPNsense web UI, on-site or over a connection you trust."
    }
}
