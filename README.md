# HomeLab Management — Windows

> Looking for macOS? See **[HomeLabManagement-Mac](https://github.com/karharry/HomeLabManagement-Mac)**.

A Windows app for monitoring and managing a home
lab: TrueNAS SCALE, Linux (Debian/Ubuntu, Raspberry Pi OS, Arch), Windows, Mac,
and OPNsense machines, all from one sidebar.

> **Project status:** This is a personal tool I built for my own home lab and
> am sharing as-is, in case it's useful to someone else. I have a full-time
> job with long hours and frequent travel, so please don't expect support,
> frequent updates, or timely responses to issues or pull requests. It works
> for my own setup; your mileage may vary. Forks and PRs are welcome if you
> want to extend it yourself, but I can't commit to maintaining it actively.

The macOS version lives in its own repo: [HomeLabManagement-Mac](https://github.com/karharry/HomeLabManagement-Mac).

## Features

- **Unified dashboard** across every machine kind, with a sidebar switcher and an
  "All Machines" overview grid
- **TrueNAS**: pools, datasets & snapshots, shares, network, apps, VMs (with live
  per-VM stats), containers, services, fan control (custom curve editor), data
  protection (snapshot tasks/replication/rsync/cloud sync/cron), users, console
- **Linux**: CPU/memory/storage, processes, services, scheduled tasks, logs,
  updates (apt/pacman), users, console — plus auto-detected extras: Pi-hole,
  PiKVM, Tailscale, Raspberry Pi telemetry, Docker containers
- **Windows**: CPU (per-core), GPU (multi-adapter, NVIDIA-aware), hardware
  sensors (via LibreHardwareMonitor), startup items, scheduled tasks, installed
  software, updates, event log, users, console
- **macOS** (as a managed target): power & thermal, startup items, installed
  software, updates (softwareupdate + Homebrew), logs, users, console
- **OPNsense**: CPU/memory/storage, network
  interfaces, services, DHCP leases, static DHCP/DNS host management
  (Dnsmasq), firmware update status — managed over OPNsense's native REST API
  rather than SSH
- **VPN**: WireGuard tunnel management (add/connect/disconnect) on Linux,
  Windows, and macOS targets, plus a containerized option for TrueNAS and
  ProtonVPN CLI assist on Linux — works with any provider that exports a
  standard WireGuard config (ProtonVPN, Mullvad, etc.)
- **RDP / Screen Sharing** quick-connect for Windows and Mac targets
- **Desktop widgets** on macOS (WidgetKit: a selected-machine status widget and
  an all-machines overview widget); the Windows build shows the same
  at-a-glance status from a **system tray icon** instead, since Windows has no
  WidgetKit equivalent
- **Open at Login** / **Open Minimized** options on both platforms

## Installation

Download `HomeLabManagement.exe` from the [Releases](../../releases) page and
run it — it's a single portable file with no installer and no extra runtime
to install. Copy it anywhere (including a USB stick or another PC) and it
keeps working; a `settings.json` file is created next to it on first use to
hold your machine list and credentials.

## Building from source

Requires Python 3.11+. From the [`Windows/`](Windows) directory:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py        # run from source
.\build.ps1                          # or build dist\HomeLabManagement.exe
```

See [`Windows/README.md`](Windows/README.md) for more on the Windows build's
architecture.

## How it works

Most machine kinds are managed over SSH (POSIX shell for Linux/macOS,
PowerShell for Windows), with small scripts collecting system state in a
single round trip. TrueNAS is managed natively over its JSON-RPC-over-WebSocket
API, with SSH as an optional add-on for VM stats, containers, and the console.
Machine configuration (host, port, kind) and credentials are stored locally —
in the macOS Keychain + standard app preferences on the Mac build, in a
`settings.json` file beside the exe on the Windows build (there's no Windows
equivalent of the Keychain, so this is a deliberate, simpler trade-off for a
portable single-file app) — nothing is sent anywhere except directly to the
machines you add.

## License

MIT — see [LICENSE](LICENSE).
