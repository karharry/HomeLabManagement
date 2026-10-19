import XCTest
@testable import HomeLabManagement

@MainActor
final class ParserTests: XCTestCase {
    func testPerCoreTicksSkipsAggregateLine() {
        let t = LinuxParse.perCoreTicks(["cpu  100 0 50 800 50 0 0 0", "cpu0 40 0 20 400 40 0 0 0", "cpu1 60 0 30 400 10 0 0 0"])
        XCTAssertEqual(Set(t.keys), ["cpu0", "cpu1"])
        XCTAssertEqual(t["cpu0"]?.total, 500)
        XCTAssertEqual(t["cpu0"]?.busy, 60)   // total 500 − idle (400 + 40)
    }

    func testLocalDNSIgnoresCommentsAndSorts() {
        let r = LinuxParse.localDNS("# comment\n192.168.1.10 nas.lan\n\n192.168.1.2 a.lan extra\nbroken\n")
        XCTAssertEqual(r.map(\.domain), ["a.lan", "nas.lan"])
        XCTAssertEqual(r.first?.ip, "192.168.1.2")
    }

    func testVMsParsesDominfoLines() {
        let v = LinuxParse.vms("VM|haos|running|enable|2|4194304 KiB\nVM|old|shut off|disable|1|1048576 KiB\nnoise\n")
        XCTAssertEqual(v.map(\.name), ["haos", "old"])
        XCTAssertTrue(v[0].isRunning && v[0].autostart)
        XCTAssertEqual(v[0].memoryMiB, 4096)
        XCTAssertFalse(v[1].isRunning)
    }

    func testOPNsenseUptimeFormats() {
        XCTAssertEqual(OPNsenseSession.uptimeSeconds("0+19:22:24"), 69744)
        XCTAssertEqual(OPNsenseSession.uptimeSeconds("3 days, 02:14:05"), 267245)
        XCTAssertEqual(OPNsenseSession.uptimeSeconds("02:14:05"), 8045)
    }

    func testOPNsenseActivitySummaryFromTopHeaders() {
        let s = OPNsenseSession.activitySummary(["headers": [
            "last pid: 11190;  load averages: 0.27, 0.30, 0.30  up 3 days, 02:14:05    23:43:39",
            "CPU:  0.0% user,  0.0% nice,  0.2% system,  1.2% interrupt, 98.6% idle"]])
        XCTAssertEqual(s.uptime, 267245)
        XCTAssertEqual(s.cpu ?? -1, 1.4, accuracy: 0.001)
    }
}
