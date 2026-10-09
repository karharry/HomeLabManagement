import SwiftUI

struct ContentView: View {
    @Environment(AppModel.self) private var model
    @State private var selection: NavItem? = .dashboard
    @State private var sheet: MachineSheet?

    var body: some View {
        NavigationSplitView {
            sidebar
        } detail: {
            if let s = model.selected {
                detail(s)
                    .environment(s)
                    .id(s.id)                      // fresh view state per machine
                    .navigationSubtitle(s.displayName)
                    .toolbar {
                        ToolbarItem { Button { s.start() } label: { Label("Refresh", systemImage: "arrow.clockwise") } }
                        if let proto = s.kind.remoteDesktopProtocol {
                            ToolbarItem {
                                Button { RemoteDesktop.connect(to: s.machine) } label: {
                                    Label(proto == .rdp ? "Connect (RDP)" : "Connect (Screen Sharing)", systemImage: "display")
                                }
                            }
                        }
                    }
                    .alert("Action failed", isPresented: Binding(get: { s.actionError != nil }, set: { if !$0 { s.actionError = nil } })) {
                        Button("OK") {}
                    } message: { Text(s.actionError ?? "") }
            } else {
                OverviewPage()
            }
        }
        .sheet(item: $sheet) { sh in
            switch sh { case .add: MachineEditor(existing: nil); case .edit(let m): MachineEditor(existing: m) }
        }
        .onOpenURL { url in
            if url.host() == "machine", let id = UUID(uuidString: url.lastPathComponent) { model.select(id) }
            else if url.host() == "overview" { model.select(nil) }
            else if let n = NavItem.allCases.first(where: { $0.urlName == url.host() }) { selection = n }
        }
    }

    private var sidebar: some View {
        List {
            Section {
                Button { model.select(nil) } label: {
                    HStack { Image(systemName: "square.grid.2x2"); Text("All Machines") }
                }.buttonStyle(.plain)
            }
            ForEach(model.orderedSessions) { s in
                Section {
                    Button { model.select(s.id) } label: {
                        HStack {
                            Image(systemName: s.kind.icon)
                            Text(s.displayName).lineLimit(1)
                            Spacer()
                            StatusDot(state: s.state)
                        }
                    }.buttonStyle(.plain)
                    .contextMenu {
                        Button("Edit…") { sheet = .edit(s.machine) }
                        if let proto = s.kind.remoteDesktopProtocol {
                            Button(proto == .rdp ? "Connect via RDP" : "Connect via Screen Sharing") { RemoteDesktop.connect(to: s.machine) }
                        }
                        Divider()
                        Button("Move Up") { model.moveUp(s.id) }.disabled(!model.canMoveUp(s.id))
                        Button("Move Down") { model.moveDown(s.id) }.disabled(!model.canMoveDown(s.id))
                        Divider()
                        Button("Remove", role: .destructive) { model.remove(s.id) }
                    }
                    if model.selectedID == s.id {
                        ForEach(s.navItems) { item in
                            Button { selection = item } label: {
                                Label(item.rawValue, systemImage: item.icon)
                                    .badge(badgeCount(item, s))
                            }
                            .buttonStyle(.plain)
                            .padding(.leading, 12)
                            .listRowBackground(selection == item ? Color.accentColor.opacity(0.18) : Color.clear)
                        }
                    }
                }
            }
            .onMove(perform: model.move)
            Section {
                Button { sheet = .add } label: { Label("Add Machine…", systemImage: "plus") }
            }
        }
        .navigationSplitViewColumnWidth(min: 220, ideal: 240)
        .navigationTitle("HomeLab")
    }

    /// Item counts shown as sidebar badges, so you can see at a glance without opening the page.
    private func badgeCount(_ item: NavItem, _ s: MachineSession) -> Int {
        switch item {
        case .alerts: s.alerts.count
        case .services: s.services.count
        case .containers:
            if let c = s as? any ContainerHosting { c.containers.count }
            else if let tn = s as? TrueNASSession { tn.containers.count }
            else { 0 }
        case .apps: (s as? TrueNASSession)?.apps.count ?? 0
        case .vms: (s as? TrueNASSession)?.vms.count ?? 0
        case .software:
            if let w = s as? WindowsSession { w.software.count }
            else if let m = s as? MacSession { m.software.count }
            else { 0 }
        case .updates: (s as? any UpdateHosting)?.updates.count ?? 0
        default: 0
        }
    }

    @ViewBuilder
    private func detail(_ s: MachineSession) -> some View {
        switch selection ?? .dashboard {
        case .dashboard: DashboardPage()
        case .storage: StoragePage()
        case .processes: ProcessesPage()
        case .services: ServicesPage()
        case .alerts: AlertsPage()
        case .apps: TrueNASAppsPage()
        case .vms: TrueNASVMsPage()
        case .containers:
            if s.kind == .truenas { TrueNASContainersPage() } else { ContainersPage() }
        case .fan: TrueNASFanPage()
        case .pihole: PiholePage()
        case .pikvm: PiKVMPage()
        case .tailscale: TailscalePage()
        case .cpu: WindowsCPUPage()
        case .gpu: WindowsGPUPage()
        case .sensors: WindowsSensorsPage()
        case .power: MacPowerPage()
        case .console: ConsolePage()
        case .users:
            if s.kind == .truenas { TrueNASUsersPage() } else { UsersPage() }
        case .logs:
            if s.kind == .windows { WindowsEventLogPage() } else { LogsPage() }
        case .updates:
            if s.kind == .windows { WindowsUpdatesPage() }
            else if s.kind == .truenas { TrueNASUpdatesPage() }
            else { UpdatesPage() }
        case .raspberryPi: RaspberryPiPage()
        case .scheduledTasks:
            if s.kind == .windows { WindowsScheduledTasksPage() } else { LinuxScheduledTasksPage() }
        case .startup:
            if s.kind == .windows { WindowsStartupPage() } else { MacStartupPage() }
        case .software:
            if s.kind == .windows { WindowsSoftwarePage() } else { MacSoftwarePage() }
        case .datasets: TrueNASDatasetsPage()
        case .shares: TrueNASSharesPage()
        case .network: TrueNASNetworkPage()
        case .dataProtection: TrueNASDataProtectionPage()
        case .vpn:
            if s.kind == .truenas { TrueNASVPNPage() } else { VPNPage() }
        }
    }
}
