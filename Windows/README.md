# HomeLab Management — Windows

A PySide6 (Qt) port of the macOS app (now in [HomeLabManagement-Mac](https://github.com/karharry/HomeLabManagement-Mac)), built independently per
platform but covering the same machine kinds and pages. See the
[top-level README](../README.md) for the feature list and installation
instructions — this file covers the Windows build specifically.

## Running from source

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

`settings.json` is created next to wherever you run it from (or next to the built exe) on first
use. It holds your machine list; passwords and API secrets are stored in **Windows Credential
Manager** (older plain-text secrets are moved there automatically on first launch). That means a
copied `settings.json` alone no longer carries credentials - use File → Export/Import Machines and
re-enter passwords on the new PC. Next to it the app writes `history.db` (7 days of CPU/memory/network
samples) and `homelab.log`.

## Building

```powershell
.uild.ps1
```

Runs the tests, then produces `dist\HomeLabManagement\HomeLabManagement.exe` (one-folder build, starts
fast) and `dist\HomeLabManagement-<version>-win64.zip`. Dependencies are pinned in `requirements.lock`;
refresh it with `pip freeze > requirements.lock` after changing `requirements.txt`. Bump the version in
`version.py`; set `GITHUB_REPO` there to enable File → Check for Updates. CI (`.github/workflows/windows.yml`)
builds the same zip on every push. Tests: `python -m pytest tests`.

## Architecture

Mirrors the Swift app's structure rather than redesigning it:

- `core.py` — `Machine`/`MachineKind` models, the local settings store, shared
  formatting helpers
- `ssh_runner.py` — a paramiko-based SSH transport with trust-on-first-use
  host key pinning, shared by every SSH-managed machine kind
- `truenas_client.py` — a synchronous JSON-RPC-over-WebSocket client for
  TrueNAS SCALE's middleware API
- `truenas.py`, `linux_session.py`, `windows_session.py`, `macos_session.py`,
  `opnsense_session.py` — one session class per managed machine kind; the
  SSH-based ones each own the remote script(s) they send and the parser for
  what comes back, while OPNsense talks to its native REST API instead
  (`opnsense_client.py`, API key/secret over HTTP Basic auth)
- `docker_.py`, `wireguard_.py`, `tailscale_.py`, `fan_client.py`,
  `pihole_client.py` — parsers/clients shared across machine kinds; the
  Pi-hole client talks to v6's REST API (session-token auth) for live query
  stats, since v6 removed the unauthenticated stats dump older versions
  exposed over SSH
- `machine_session.py` — the base class every session inherits: a dedicated
  background thread per machine (polling + on-demand actions), communicating
  back to the Qt main thread via signals
- `app_model.py` — owns the machine list and one session per machine
- `tls_pin.py` - trust-on-first-use pinning of self-signed HTTPS/WSS certificates (OPNsense, TrueNAS)
- `history.py`, `wol.py`, `update_check.py` - sample history (SQLite), Wake-on-LAN, GitHub release check
- `main_window.py`, `overview_page.py`, `machine_editor.py`,
  `settings_dialog.py`, `tray.py` — the window shell, the "All Machines" grid,
  the add/edit dialog, preferences, and the system tray icon
- `pages_*.py` — one module per feature area (dashboard/storage/processes,
  TrueNAS apps/VMs/fan/VPN, Linux Pi-hole/PiKVM/Tailscale, Windows CPU/GPU/
  sensors, macOS power, generic WireGuard VPN, …)
- `theme.py` — dark/light theming that follows the Windows system setting
  live, since Qt has no SwiftUI-style automatic appearance

Each machine kind's data layer (scripts + parsers) was checked against
realistic sample output for every parser, and every page was exercised
offscreen for every machine kind, before this was tested against real
hardware.
