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

## Requirements and per-machine setup

The app itself needs nothing beyond the exe. Each machine you add needs a little
setup on **that machine**; everything below is optional unless marked required.

### All SSH-managed machines (Linux, Windows, macOS)

- **An SSH server reachable from the PC running the app (required)**, with password
  authentication enabled (`PasswordAuthentication yes` in `sshd_config`). Host keys are
  pinned on first connect.
- **sudo**: Linux/macOS actions that need root (services, updates, Docker, VMs, WireGuard,
  etc.) use `sudo` with the SSH password. The user must be allowed to sudo; untick
  "Use sudo" in the machine editor if you log in as root.

### Windows machines

- **OpenSSH Server** installed and running: *Settings → Apps → Optional features → OpenSSH
  Server*, then start the `sshd` service and set it to Automatic.
- **[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)**
  for temperatures, fan speeds, clocks and power (Sensors/CPU/GPU/Power pages). Run it on the
  target PC, then either:
  - turn on *Options → Remote Web Server* (default port 8085) and enter that port in the
    machine editor (recommended), or
  - leave the port at 0 and let the app read its WMI sensors (LibreHardwareMonitor must be running).
- **NVIDIA GPUs**: extra detail (utilisation, temperature, power) comes from `nvidia-smi.exe`,
  which ships with the NVIDIA driver.
- **[WireGuard for Windows](https://www.wireguard.com/install/)** (installed in
  `C:\Program Files\WireGuard`) if you want to manage tunnels on that PC.
- **Remote Desktop** enabled on the target for the RDP quick-connect button (the app launches
  the built-in `mstsc.exe`).

### Linux machines (Debian/Ubuntu, Raspberry Pi OS, Arch)

Extra pages appear automatically when the matching tool is found on the host:

| Page | Needs on the host |
|---|---|
| Containers | `docker` |
| GPU | `nvidia-smi` (NVIDIA driver) |
| VMs | `virsh` (libvirt/KVM) |
| Pi-hole | `pihole`; for Pi-hole v6 also enter the web password in the machine editor |
| PiKVM | the `kvmd` service |
| Tailscale | `tailscale` |
| Raspberry Pi telemetry | `vcgencmd` |
| VPN | `wg-quick` (WireGuard tools); optionally `protonvpn-cli` |
| Updates | `apt` or `pacman` |

### TrueNAS SCALE

- **API access (required)**: the app connects to the middleware over WebSocket. Use a TrueNAS
  user/password, or better an **API key** (*Credentials → Local Users → API Keys*). Self-signed
  certificates are pinned on first connect.
- **SSH (optional)**: needed for VM stats, Containers (Docker) and the Console. Enable the SSH
  service in TrueNAS with password login for the chosen user, who also needs sudo.
- **Fan control (optional)**: the Fan page talks to a separate, self-hosted **`fanctl`**
  container on the NAS, which is not part of this repo or of TrueNAS. It is a small Python
  controller with a web UI that reads drive, NVMe and CPU temperatures and sets the fan PWM
  from a custom curve. It serves `/api/status` and `/api/config` on port **9595** (the app's
  default; change it in the machine editor if you map a different port). The stock container
  has no authentication, so leave the password blank and keep the port LAN-only; the app
  sends HTTP Basic auth (user `admin`) only if you enter a password.

  The original setup targets an **ASUSTOR AS5404T**, whose ITE IT8625E fan chip isn't
  supported by TrueNAS's built-in `it87` driver. That needs:
  - the out-of-tree [`asustor-platform-driver`](https://github.com/mafredri/asustor-platform-driver)
    kernel module (`asustor_it87`), built against your TrueNAS kernel headers and reloaded
    after TrueNAS kernel updates;
  - the `fanctl` image, run `--privileged` (it writes `/sys/class/hwmon/.../pwm1`) with a
    data volume for `config.json`;
  - a post-init script that reloads the driver and starts the container at boot.

  Other hardware needs its own driver and a controller exposing the same API, otherwise the
  Fan page shows an error.
- **VPN**: the containerised WireGuard option needs SSH plus Docker on the NAS.

### OPNsense

- Create an **API key and secret** under *System → Access → Users → (your user)*. No SSH
  needed; the app uses OPNsense's REST API over HTTP(S). Tick "Allow self-signed certificate"
  if you use OPNsense's default certificate.

### macOS machines (as a managed target)

- Enable **Remote Login** in *System Settings → General → Sharing*.
- Optional: `docker`, `tailscale`, and Homebrew `wireguard-tools` for the matching pages and
  update checks.

### Wake-on-LAN

Enter the target's MAC address in the machine editor. Wake-on-LAN must be enabled in the
target's BIOS/OS, and the app's PC must be on the same LAN.

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
