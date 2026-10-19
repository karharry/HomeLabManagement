import Foundation
import Observation
import WidgetKit

@MainActor @Observable
final class AppModel {
    private(set) var machines: [Machine] = MachineStore.load()
    private(set) var sessions: [UUID: MachineSession] = [:]
    var selectedID: UUID? = MachineStore.selectedID
    private var publisher: Task<Void, Never>?

    init() {
        for m in machines { sessions[m.id] = MachineSession.make(for: m) }
        if selectedID == nil || sessions[selectedID!] == nil { selectedID = machines.first?.id }
        markForeground()
    }

    var selected: MachineSession? { selectedID.flatMap { sessions[$0] } }
    var orderedSessions: [MachineSession] { machines.compactMap { sessions[$0.id] } }

    func startAll() {
        for s in sessions.values { s.start() }
        guard publisher == nil else { return }
        publisher = Task { [weak self] in
            while !Task.isCancelled {
                self?.publish()
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    /// nil selects the "All Machines" overview.
    func select(_ id: UUID?) {
        guard id != selectedID else { return }
        selectedID = id
        MachineStore.selectedID = id
        markForeground()
        if let id { sessions[id]?.start() }
        publish()
    }

    private func markForeground() {
        for s in sessions.values { s.isForeground = (s.id == selectedID) }
    }

    /// Adds a new machine, or updates an existing one (reconnecting it if its settings changed).
    func upsert(_ machine: Machine, secret: String, sshPassword: String = "", fanPassword: String = "", piholePassword: String? = nil) {
        MachineStore.setSecret(secret, for: machine.id)
        if !sshPassword.isEmpty || machine.sshEnabled { MachineStore.setSSHPassword(sshPassword, for: machine.id) }
        if !fanPassword.isEmpty { MachineStore.setFanPassword(fanPassword, for: machine.id) }
        if let piholePassword { MachineStore.setPiholePassword(piholePassword, for: machine.id) }
        if let i = machines.firstIndex(where: { $0.id == machine.id }) {
            let oldKind = machines[i].kind
            machines[i] = machine
            if oldKind == machine.kind {
                let s = sessions[machine.id]!
                s.stop()
                s.machine = machine
                s.start()
            } else {
                sessions[machine.id]?.stop()
                let s = MachineSession.make(for: machine)
                sessions[machine.id] = s
                s.start()
            }
        } else {
            machines.append(machine)
            let s = MachineSession.make(for: machine)
            sessions[machine.id] = s
            s.start()
            selectedID = machine.id
            MachineStore.selectedID = machine.id
            markForeground()
        }
        MachineStore.save(machines)
        publish()
    }

    func remove(_ id: UUID) {
        sessions[id]?.stop()
        sessions[id] = nil
        machines.removeAll { $0.id == id }
        MachineStore.forgetSecrets(for: id)
        MachineStore.save(machines)
        if selectedID == id {
            selectedID = machines.first?.id
            MachineStore.selectedID = selectedID
            markForeground()
        }
        publish()
    }

    func move(from: IndexSet, to: Int) {
        machines.move(fromOffsets: from, toOffset: to)
        MachineStore.save(machines)
    }

    func canMoveUp(_ id: UUID) -> Bool { (machines.firstIndex { $0.id == id } ?? 0) > 0 }
    func canMoveDown(_ id: UUID) -> Bool { (machines.firstIndex { $0.id == id } ?? machines.count - 1) < machines.count - 1 }

    func moveUp(_ id: UUID) {
        guard let i = machines.firstIndex(where: { $0.id == id }), i > 0 else { return }
        machines.swapAt(i, i - 1)
        MachineStore.save(machines)
    }

    func moveDown(_ id: UUID) {
        guard let i = machines.firstIndex(where: { $0.id == id }), i < machines.count - 1 else { return }
        machines.swapAt(i, i + 1)
        MachineStore.save(machines)
    }

    func publish() {
        let snaps = orderedSessions.map(\.snapshot)
        SharedStore.save(snaps.filter(\.isSelected) + snaps.filter { !$0.isSelected })
        WidgetCenter.shared.reloadAllTimelines()
    }
}
