# vphone-web

A self-hosted web platform for running **multiple virtual iPhones** at once in the
browser — user accounts + RBAC, per-user device assignment, live screen streaming
(WebRTC with an MJPEG/WebSocket fallback), touch & hardware-key input, IPA/TIPA
install, an in-browser **root shell (xterm.js)** and a **file browser**, on top of
the `vphone-cli` engine (Apple Virtualization.framework, PV=3 research VMs).

> This repository contains **only the web backbone** (`web/`). The virtual-iPhone
> engine, firmware pipeline and all its toolchain come from the `vphone-cli` engine,
> pulled in as a **git submodule** (`vendor/vphone-cli`). You build the engine and create
> your own base VM locally — no multi-GB VM images are shipped here.

```
Browser ──WebRTC/MJPEG video──┐         ┌── unix socket (frames + input) ──┐
   │  (touch/keys over WebSocket)        │                                   │
   ▼                                     ▼                                   ▼
FastAPI backend (web/app) ── 1 process per VM ──►  vphone-cli boot --server
   ├─ auth + RBAC (SQLite)                             ├─ boots iOS VM (headless)
   ├─ vm_manager: cp -c clone, start/stop              ├─ JPEG stream + touch/keys
   ├─ aiortc: JPEG → WebRTC                            └─ vphoned (vsock): shell / files / IPA
   └─ static frontend (SSH, Files, Apps, Logs…)
```

---

## Requirements

- **macOS 15+ (Sequoia)** on Apple Silicon — required for PV=3 virtualization.
- **SIP + AMFI disabled** (needed for the private Virtualization entitlements) —
  see the vphone-cli README for the exact recovery-mode steps.
- **Xcode + command-line tools**, Homebrew, Python 3.11+.
- Enough **fast** disk. **Strongly recommended: keep VM images on an internal SSD**,
  not a slow external/USB disk — iOS first-boot does thousands of small writes and
  is impractically slow on slow storage.

---

## 1. Clone (recursive)

The vphone-cli engine and all its nested submodules are pulled via `--recursive`:

```sh
git clone --recursive https://github.com/<you>/vphone-web.git
cd vphone-web
# already cloned without --recursive? then:
git submodule update --init --recursive
```

This creates `vendor/vphone-cli/` with the full engine + toolchain.

## 2. Build the vphone-cli engine

```sh
cd vendor/vphone-cli
make setup_tools     # brew deps, build toolchain, create its Python venv
make build           # build + sign the vphone-cli binary
make bundle          # app bundle (bundles the IPA signing cert) — recommended
cd ../..
```

## 3. Create a base VM (the "golden image" that gets cloned)

Each web VM is an instant copy-on-write clone of a **base VM directory**. Build one
with vphone-cli's automated pipeline (downloads IPSW + cloudOS, restores, patches,
installs CFW, first boot). Example for a jailbreak base:

```sh
cd vendor/vphone-cli
make setup_machine JB=1 NON_INTERACTIVE=1 SUDO_PASSWORD='<your-sudo-pw>' \
  IPHONE_VERSION=26.1 IPHONE_DEVICE=iPhone17,3 \
  CLOUDOS_SOURCE='<cloudOS ipsw url or path>'
cd ../..
```

See **`vendor/vphone-cli/README.md`** for the full firmware matrix, supported
iOS/cloudOS builds, variants (regular / dev / jb / exp), and how to resolve IPSW
URLs. When it finishes, the base lives at `vendor/vphone-cli/vm/` (contains
`Disk.img`, `nvram.bin`, `SEPStorage`, `config.plist`, `AVPBooter*.bin`,
`.vphoned.signed`).

> **Tip — one base per iOS version.** Keep each version's base in its own folder
> (`vm/`, `vm-26.5/`, `vm-27/`, …) and point the matching `VPHONE_BASE_VM*` env var
> at it. The web only offers versions whose `Disk.img` actually exists.

> **Boot cleanly.** Always **Stop** a VM from the web UI (or Ctrl-C the process) and
> let it finish shutting down before copying/promoting a base — a hard kill leaves
> the APFS volume dirty and the next boot does a slow fsck replay.

## 4. Configure & run the web

```sh
cp .env.example .env          # then edit paths (see below)
make web_setup                # create .venv, install web deps
make web_seed_admin USER=admin PASS='choose-a-strong-password'
make web_run                  # serves http://127.0.0.1:8080
```

Edit `.env` so the paths point at what you built — key vars:

| Var | Meaning |
|-----|---------|
| `VPHONE_BIN` | Path to the built vphone-cli binary (default points into the submodule) |
| `VPHONE_BASE_VM` | Base VM dir for the default iOS version (e.g. `vendor/vphone-cli/vm`) |
| `VPHONE_BASE_VM_265`, `VPHONE_BASE_VM_27` | Bases for other iOS versions (optional) |
| `VPHONE_VMS_DIR` | Where per-VM clones are stored (created at runtime) |
| `VPHONE_RAM_BUDGET_MB` | Max total RAM across running VMs before new starts are refused |
| `VPHONE_PROTECTED_ADMIN` | Username that can't be demoted/deleted (optional lockout guard) |

Open **http://127.0.0.1:8080**, sign in as the admin, then:

1. **Admin → Create VM** — pick an iOS version; it clones the base instantly.
2. **Admin → assign** the VM to one or more users.
3. As a user: **Start** → **Open** → interact; drag on the screen for touch, use the
   home/power/volume buttons, or the **SSH**/**Files** tabs. Drop an `.ipa`/`.tipa`
   on the install zone to install.

Admin-only **logs** dashboard live-tails every VM at `/logs`.

---

## 5. (Optional) Expose it with a Cloudflare Tunnel

The server binds `127.0.0.1` only. To reach it from the internet **without opening a
port**, use a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).
You need a domain on Cloudflare.

```sh
brew install cloudflared
cloudflared tunnel login                      # authorize your domain (opens browser)
cloudflared tunnel create vphone              # creates a tunnel + credentials json
cloudflared tunnel route dns vphone phone.example.com   # your hostname
```

Create `~/.cloudflared/config.yml` (replace the id + hostname with **yours**):

```yaml
tunnel: <YOUR-TUNNEL-ID>
credentials-file: /Users/<you>/.cloudflared/<YOUR-TUNNEL-ID>.json
ingress:
  - hostname: phone.example.com
    service: http://localhost:8080
  - service: http_status:404
```

Run it:

```sh
cloudflared tunnel run vphone
```

Notes:
- Live video uses **WebRTC** locally and automatically falls back to
  **MJPEG-over-WebSocket** through the tunnel (Cloudflare can't proxy WebRTC UDP).
- IPA upload uses chunked transfer to bypass proxy body-size limits.
- If you serve subdomains (e.g. `logs.phone.example.com`), set
  `VPHONE_COOKIE_DOMAIN=.example.com` in `.env` so the session cookie is shared.
- **Never commit** your `~/.cloudflared/*.json` credentials or `cert.pem`.

### Auto-start on boot (macOS LaunchAgent, optional)

Run the web + tunnel automatically after a reboot/power-restore by creating two
per-user LaunchAgents (`~/Library/LaunchAgents/…`) that `RunAtLoad` + `KeepAlive`:
one execs `make web_run` (or uvicorn directly) from this repo, the other execs
`cloudflared tunnel run vphone`. Web VMs need a **GUI login session** (WindowServer),
so use a **LaunchAgent** (per-user), not a system LaunchDaemon; enable macOS
Automatic Login for a fully unattended restore.

---

## What is / isn't in this repo

- **In:** the web platform (`web/app`, `web/static`), `requirements.txt`, `Makefile`,
  `.env.example`, and the **vphone-cli submodule pointer** (`.gitmodules`).
- **Out (gitignored):** built binaries, VM images (`vm*/`, `vms/`, `*.img`), the
  SQLite DB and session secret (`web/data/`), logs, `.env`, and any Cloudflare
  credentials. Every user builds the engine and creates their own VMs.

## Security

- First admin is created by `make web_seed_admin`; passwords are bcrypt-hashed.
- Session cookies are signed (itsdangerous) with a per-install random key at
  `web/data/secret.key` (auto-generated, gitignored).
- Per-user access expiry and per-user start/stop permissions are enforced server-side.
- The guest daemon runs as **root** inside the (jailbroken research) VM — the SSH and
  Files tabs therefore have full access **inside the VM**. Only expose the platform to
  people you trust, behind authentication.

## Credits

Built on [**vphone-cli**](https://github.com/Lakr233/vphone-cli) by
[Lakr233](https://github.com/Lakr233). This repo adds the web platform layer on top.
