import Citadel
import Foundation
import NIO
import NIOSSH

struct SSHSettings: Sendable, Equatable {
    var host: String
    var port: Int
    var user: String
    var password: String
    var useSudo: Bool = false
}

enum SSHError: LocalizedError {
    case hostKeyChanged, failed(String)
    var errorDescription: String? {
        switch self {
        case .hostKeyChanged: "The SSH host key changed since it was first trusted. Use “Forget pinned host key” in the machine's settings if this is expected."
        case .failed(let m): m
        }
    }
}

/// Trust-on-first-use host key pinning, shared by every SSH-based machine kind.
private final class PinnedHostKey: NIOSSHClientServerAuthenticationDelegate, @unchecked Sendable {
    let id: String
    init(id: String) { self.id = id }
    func validateHostKey(hostKey: NIOSSHPublicKey, validationCompletePromise: EventLoopPromise<Void>) {
        let key = String(openSSHPublicKey: hostKey)
        let storeKey = "sshHostKey.\(id)"
        if let pinned = UserDefaults.standard.string(forKey: storeKey) {
            pinned == key ? validationCompletePromise.succeed(()) : validationCompletePromise.fail(SSHError.hostKeyChanged)
        } else {
            UserDefaults.standard.set(key, forKey: storeKey)
            validationCompletePromise.succeed(())
        }
    }
}

/// One SSH connection, reused across calls to the same machine. Supports POSIX shell (Linux/macOS targets)
/// and PowerShell (Windows targets) execution.
actor SSHRunner {
    private var client: SSHClient?
    private var connectedTo: SSHSettings?

    /// Runs a POSIX shell script and returns its output. The exit status is swallowed so failures come
    /// back as readable text. With `merge` stderr is included in the output.
    func sh(_ script: String, _ s: SSHSettings, sudo: Bool = false, merge: Bool = false,
            maxBytes: Int = 4 * 1024 * 1024) async throws -> String {
        if connectedTo != s { await close() }
        do {
            let c = try await ensureClient(s)
            let b64 = Data(script.utf8).base64EncodedString()
            let run = "sh -c \"$(printf %s \(b64) | base64 -d)\""
            let command = (sudo && s.useSudo && s.user != "root")
                ? "printf '%s\\n' \(Self.quote(s.password)) | sudo -S -p '' \(run)"
                : run
            let out = try await c.executeCommand("(\(command)) \(merge ? "2>&1" : "2>/dev/null"); true", maxResponseSize: maxBytes)
            return String(buffer: out)
        } catch {
            await close()
            throw Self.friendly(error, user: s.user)
        }
    }

    /// Runs a PowerShell script via a compressed `-EncodedCommand`, so quoting never matters.
    func ps(_ script: String, _ s: SSHSettings, maxBytes: Int = 8 * 1024 * 1024) async throws -> String {
        if connectedTo != s { await close() }
        let command = try Self.psCommand(for: script)
        do {
            let c = try await ensureClient(s)
            let out = try await c.executeCommand(command, maxResponseSize: maxBytes)
            return String(buffer: out)
        } catch {
            await close()
            throw Self.friendly(error, user: s.user)
        }
    }

    func close() async {
        try? await client?.close()
        client = nil
        connectedTo = nil
    }

    private func ensureClient(_ s: SSHSettings) async throws -> SSHClient {
        if let client { return client }
        let settings = SSHClientSettings(
            host: s.host, port: s.port,
            authenticationMethod: { .passwordBased(username: s.user, password: s.password) },
            hostKeyValidator: .custom(PinnedHostKey(id: "\(s.host):\(s.port)")))
        let c = try await SSHClient.connect(to: settings)
        client = c
        connectedTo = s
        return c
    }

    /// cmd.exe limits a command line to 8191 characters, so the script is deflated first.
    static func psCommand(for script: String) throws -> String {
        let deflated = try (Data(script.utf8) as NSData).compressed(using: .zlib) as Data
        let bootstrap = "$ProgressPreference='SilentlyContinue';"
            + "$b=[Convert]::FromBase64String('\(deflated.base64EncodedString())');"
            + "$m=New-Object IO.MemoryStream(,$b);"
            + "$d=New-Object IO.Compression.DeflateStream($m,[IO.Compression.CompressionMode]::Decompress);"
            + "$r=New-Object IO.StreamReader($d,[Text.Encoding]::UTF8);"
            + "try{[Console]::OutputEncoding=[Text.Encoding]::UTF8}catch{};"
            + "try{Invoke-Expression $r.ReadToEnd()}catch{};exit 0"
        let encoded = Data(bootstrap.utf16.flatMap { [UInt8($0 & 0xFF), UInt8($0 >> 8)] }).base64EncodedString()
        let cmd = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand \(encoded)"
        guard cmd.count < 8000 else { throw SSHError.failed("Internal error: the script is too large to send (\(cmd.count) characters).") }
        return cmd
    }

    private static func friendly(_ error: Error, user: String) -> Error {
        if error is SSHError { return error }
        switch error as? SSHClientError {
        case .allAuthenticationOptionsFailed?:
            return SSHError.failed("SSH login was rejected for “\(user)”. Check the user name and password.")
        case .unsupportedPasswordAuthentication?:
            return SSHError.failed("The SSH server doesn't accept password authentication.")
        case .channelCreationFailed?:
            return SSHError.failed("Couldn't open an SSH session on the machine.")
        case nil:
            if let io = error as? IOError {
                return SSHError.failed("Network error talking to the machine over SSH: \(io.description) (errno \(io.errnoCode)).")
            }
            if let std = error as? TTYSTDError {
                return SSHError.failed("The machine reported an error: \(String(buffer: std.message).trimmingCharacters(in: .whitespacesAndNewlines))")
            }
            return SSHError.failed("SSH error: \(String(describing: error))")
        default:
            return error
        }
    }

    /// POSIX shell single-quoted string literal.
    static func quote(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }
    /// PowerShell single-quoted string literal.
    static func pq(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "''") + "'" }
}
