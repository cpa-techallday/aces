# Omarchy rehearsal VM (Hyper-V on DARKNESS)

Purpose: install stock Omarchy 4 unattended into a throwaway Hyper-V VM, then add the local `[aces]` repo and install
the ACES packages over it, so the overlay (VISION §2.1: stock ISO plus one bootstrap), the `aces-omarchy` hooks and the
`aces-fabric` plugin are exercised before anyone touches the real dual boot (VISION §9). It stands in for the libvirt VM
named in the day-30 acceptance line (VISION §11.3). Everything large lives under `D:\HyperV\aces-rehearsal`
(`iso\`, `cidata\`, `vm\`, `serve\`, `ssh\`, `reports\`); C: has about 41 GB free.

## Step order

Run from `C:\Users\shedm\aces-os\rehearsal` in Windows PowerShell 5.1 (CurrentUser policy here is RemoteSigned, so
local scripts run). Every script except `validate.ps1` takes `-WhatIf`.

| # | Step | Elevation | Expected time | Tested here |
|---|---|---|---|---|
| 1 | `.\get-iso.ps1` downloads the newest ISO linked on omarchy.org (today `omarchy-4.0.4.iso`, 6,185,304,064 bytes) and its `.sha256`, verifies, skips a valid copy | none | measured 2026-10-09: 3.5 min for the download (about 28 MB/s) plus the hash | ran for real; the ISO verified against the published sha256 on the first try. First attempt died on curl's progress meter (fixed, see the run notes) |
| 2 | `.\make-cidata.ps1` builds the cidata drive | VHDX (default): **elevated** (Mount-VHD failed here unelevated: "A required privilege is not held by the client", 0x80070522; New-VHD worked). ISO (`-Iso`, and the automatic fallback when not elevated): none | ISO 0.7 s (measured) | ISO built with `-Iso` and accepted by the real installer (no wizard appeared). VHDX path: `-WhatIf` only |
| 3 | `.\new-vm.ps1` creates the VM (skips an existing one) | none for a member of Hyper-V Administrators: New-VM, Set-VMFirmware, Add-VMDvdDrive and Checkpoint-VM all ran unelevated here | seconds | `-Start` created and booted the VM; `Get-VMFirmware` showed Secure Boot off, boot order disk then DVD |
| 4 | `Start-VM aces-rehearsal; vmconnect.exe localhost aces-rehearsal` (or `.\vm-shot.ps1` for a PNG of the console without vmconnect) | none | measured: power-on to "Installing Omarchy" 18 s, to the Omarchy login screen under 2 min (the ISO installs from its bundled package mirror) | ran unattended; watched with `vm-shot.ps1` (Hyper-V WMI thumbnails), no vmconnect needed |
| 5 | `Checkpoint-VM -Name aces-rehearsal -SnapshotName omarchy-clean` once the VM has rebooted into Omarchy | none | seconds | taken; a second checkpoint `aces-installed` was taken after step 7 |
| 6 | `.\serve-repo.ps1` serves `repo\x86_64` on `http://192.168.1.171:8930` | none (this python.exe already has inbound Allow rules, TCP and UDP, Private and Public; the HA-External vNIC is Public) | about 1 s | served the signed repo (16 packages, key published); the VM fetched `aces.db` through the Windows firewall without any rule change |
| 7 | `.\validate.ps1 -VmIp <vm address> -Install` copies `aces-install.sh` in and runs it in a login shell over `ssh -t` (sudo asks for the rehearsal password up to three times unless the VM has the unattended sudo rule below), then fetches the report | none | measured: `omarchy update -y` plus `aces-os` with all its dependencies in 75 s on this LAN (the VM clock, which is on Brisbane time, says 13:15:11 to 13:16:26) | third attempt passed 13/13 (the first two found the sudo and `OMARCHY_PATH` problems, both fixed); report in `reports/2026-10-09-aces-rehearsal-report.txt` |
| 8 | `.\validate.ps1` again any time: fetches `~/aces-rehearsal-report.txt`, exit 0 all PASS, 1 any FAIL, 2 no VM or no report | none | seconds | exit 0 on the passing run, 1 on the stopped ones |

Without SSH, step 7 can be done in the VM console instead: log in as `aces`, open a terminal with Super+Return (vmconnect
passes the Windows key only in full screen unless Hyper-V Settings > Keyboard says otherwise), and run the line
`serve-repo.ps1` prints: `curl -fsSLo ~/aces-install.sh http://192.168.1.171:8930/aces-install.sh && bash ~/aces-install.sh http://192.168.1.171:8930`.
`validate.ps1` then finds the VM's address in the server's access log.

## What the kit is built on (read 2026-10-09)

- **ISO:** the omacom/omarchy-iso README says the link is on omarchy.org and a `.sha256` sits beside every ISO. The
  page links `https://iso.omarchy.org/omarchy-4.0.4.iso` (Last-Modified 15 Sep 2026); its sidecar is `sha256sum`
  format: `ddeded2758c48318d201dfdac905ecb28f570441883f0c052ea3cd5d05acf92d  omarchy-4.0.4.iso`.
- **cidata format:** read from omarchy-iso at commit `8b17699` (8 Oct 2026): `configs/airootfs/root/configurator`
  (writes the two JSON files), `omarchy-cidata-load` (copies `user_configuration.json`, `user_credentials.json` and the
  optional `user_full_name.txt`, `user_email_address.txt`, `user_encrypt_installation.txt`, `authorized_keys`,
  `tailscale_authkey`, `defer-provisioning` from any filesystem labelled `cidata` or `CIDATA`), the orchestrator, and
  `test/integration.d/base-test.sh` (the project's own unattended cidata build). `cidata/*.json` are the configurator's
  output shape with this VM's values: user `aces`, host `aces-rehearsal`, `Australia/Brisbane`, `us` keyboard, no
  `disk_encryption` block (an encrypted install would stop at the LUKS prompt), kernel `linux-omarchy`, `/dev/sda` wiped
  with the configurator's layout for an 80 GiB disk (1 MiB gap, 2 GiB ESP, btrfs to 1 MiB before the end). The loader
  has not changed since 9 Aug; the `input_method` keys are newer than 4.0.4 and ignored by older ISOs.
- **No post-install hook exists in that format.** The orchestrator installs only Omarchy's own package list; archinstall's
  `packages` and `custom_commands` keys are not acted on, and nothing on the drive is executed. So the ACES step is the
  SSH or console step above. This answers the question in VISION Appendix C: the cidata flow takes no extra packages;
  they need an installer patch or a post-install step like this one.
- **Secure Boot off:** omarchy-iso `plans/consumer-secure-boot.md` (state August 2026) says the ISO boots an unsigned
  GRUB and kernel, so "Secure Boot must be disabled today"; the README's Proxmox example enrols no keys.
- **Boot order disk first, not DVD first** (a deliberate departure from the brief): the Omarchy manual says the disk goes
  first on purpose, so the empty disk falls through to the ISO once and the installed system boots from disk. With the
  DVD first, every reboot would boot the ISO, find cidata again and reinstall. `-DvdFirst` exists to watch one install.
- **Hyper-V drivers:** `linux-omarchy` 7.2.8 (omacom/omarchy-pkgs HEAD) builds `HYPERV_STORAGE`, `HYPERV_NET`,
  `DRM_HYPERV`, `HYPERV_KEYBOARD` and `HID_HYPERV_MOUSE` as modules. The live ISO boots `linux-t2` (not checked).
  Omarchy's manual lists no Hyper-V guide; users report earlier releases (2.x to 3.x) working (omarchy discussion #445)
  with the console stuck at 1024x768 unless `Set-VMVideo` sets a size (new-vm.ps1 does) and with no audio. Not verified.
- **Omarchy guards pacman:** `00-omarchy-update-guard.hook` aborts a direct `pacman -Syu` ("omarchy update" is the path),
  and `omarchy refresh pacman` rewrites `/etc/pacman.conf`, then runs `pre-refresh-pacman` hooks. So `aces-install.sh`
  writes `/etc/pacman.d/aces.conf`, includes it above `[omarchy]` (Omarchy's own sample hook pattern) and installs a
  `pre-refresh-pacman.d/aces-repo` hook that restores the include; it runs `omarchy update -y` and then
  `pacman -S --needed aces-os`. `ACES_UPDATE=none` is the brief's literal `pacman -Sy aces-os` (a partial upgrade).

## Credentials (throwaway, this VM only)

User `aces`, password **`aces-rehearsal-only`** (also root's), stored as a SHA-512 crypt hash in
`cidata/user_credentials.json`. Never reuse it, and never put this VM on an untrusted network. SSH logs in with this
workstation's existing public key `%USERPROFILE%\.ssh\id_ed25519.pub`: `make-cidata.ps1` appends it to the committed
comment-only `cidata/authorized_keys` on the drive it builds (pass `-PublicKey` for another; private keys are refused).
The install enables `sshd` and opens port 22 in ufw only because `authorized_keys` is present.

## Signed and unsigned repo

`serve-repo.ps1` publishes `keys\aces-pkgs.pub.asc` (and the in-VM line carries `ACES_KEY_FPR` from `keys\FINGERPRINT`)
only when every `*.pkg.tar.zst` has a `.sig`; then `aces-install.sh` checks the fingerprint, runs `pacman-key --init`
(idempotent), `--add`, `--lsign-key` and uses `SigLevel = Required DatabaseOptional`. Otherwise no key is served and it
uses `Optional TrustAll`. On 2026-10-09 the key existed but 0 of 14 packages were signed, so the server ran unsigned.
The secret key and `keys\gnupg` are never copied; the server refuses to start if such a file appears in its web root.

## Tests run here (2026-10-09) and results

- PowerShell parser: all five scripts parse with no errors. `bash -n post-install/aces-install.sh`: OK.
- `cidata/*.json` load with Python's `json`; the partition table ends exactly 1 MiB before 80 GiB.
- cidata ISO (IMAPI2, 61,440 bytes) in an Arch container: `blkid` LABEL=cidata TYPE=iso9660; the real
  `omarchy-cidata-load` exited 0 and copied the three files byte-identical; `authorized_keys` yields 1 ed25519 key; the
  password hash re-derives with `openssl passwd -6`.
- `serve-repo.ps1`: `aces.db` served (sha256 equal to `aces.db.tar.zst`) on 127.0.0.1 and 192.168.1.171; a plain
  `http.server` on `repo\` answers 404 for `aces.db`, because `repo-add` leaves `aces.db`/`aces.files` as WSL symlinks
  (reparse tag 0xA000001D) that Windows programs cannot open; hence the web root of real copies. The key and the secret
  key path answer 404. Re-run while up re-syncs; `-Stop` stops it.
- `aces-install.sh` end to end in an Arch container against the served repo (`ACES_UPDATE=none`, `--offline` doctor,
  `aces-base aces-creative aces-consoles aces-omarchy`, an `omarchy` stub that runs upstream `omarchy-plugin-validate`,
  an Omarchy-shaped `pacman.conf`): 10 PASS, 1 FAIL; a second run was idempotent; pacman's repo order became aces,
  omarchy, core, extra; the pre-refresh hook put a removed include back.
- Signed mode with a throwaway key (not the ACES key): a wrong `ACES_KEY_FPR` stops before pacman is touched; the right
  one imports the key and installs the signed package; an unsigned package is refused ("failed retrieving file
  '...pkg.tar.zst.sig' ... 404").
- Still not tested: the VHDX cidata path, oscdimg (not installed), `-DvdFirst`.

## The real run (2026-10-09, afternoon)

Omarchy 4.0.4 installed itself from the cidata ISO with no wizard (power-on to "Installing Omarchy" in 18 s, login
screen in under 2 min: the ISO installs from its own package mirror). SSH as `aces` worked keyless; the installer puts
`authorized_keys` in the user's home only, enables `sshd` and opens 22 in ufw (omarchy-iso `configure_ssh_access`), so
root has no key and sudo needs the rehearsal password. `omarchy update -y` found only two packages to upgrade. The
ACES step then passed **13 PASS, 0 FAIL** on the third attempt (`reports/2026-10-09-aces-rehearsal-report.txt`): all
16 packages installed from the signed repo with `SigLevel = Required DatabaseOptional`, `aces doctor` reached every
rig endpoint from the VM, `aces-omarchy-setup` linked the hooks and the skills into `~/.claude`, `~/.codex`,
`~/.pi/agent` and `~/.agents` and copied the plugin, and **`omarchy plugin validate` passed on the real Omarchy host for
both the copied and the packaged plugin** (the symlink failure predicted below is gone since `aces-omarchy` copies the
plugin). `aces fabric refresh` from the VM read the rig's `sim-lab-4` mode and five lanes; `aces-gendesign selftest` and
`aces-voxel fea --self-test` passed in the engineering venv (Python 3.14.7). A second checkpoint, `aces-installed`,
was taken afterwards. Three things the VM found, all fixed in this kit:

- `get-iso.ps1` died on the first stderr line of curl's progress meter: under `$ErrorActionPreference = 'Stop'`,
  PowerShell 5.1 turns native stderr into terminating error records whenever stderr is not a console. The download now
  runs under `Continue`, drops the meter when stderr is redirected, and lets the exit code decide.
- `aces-install.sh` opened with `sudo -v`, which prompts even on a NOPASSWD host when any other rule for the user (the
  stock `%wheel` line) lacks NOPASSWD (sudoers `verifypw=all`); with nobody at the keyboard the run stalls. It now
  tries `sudo -n true` first.
- `omarchy update -y` aborted with `/usr/bin/omarchy-update-dev: line 7: OMARCHY_PATH: unbound variable`: a plain ssh
  command on Arch's bash reads no rc file, so the environment Omarchy's `~/.bashrc` sets (via
  `/usr/share/omarchy/default/bash/env-bootstrap`) was missing. `validate.ps1` now runs the script in a login shell
  (`bash -lc`), and `aces-install.sh` sources `env-bootstrap` itself when `OMARCHY_PATH` is unset, so the console path
  and any other caller work too.

Unattended sudo for this throwaway VM (what the passing run used; the `omarchy-clean` checkpoint predates it): as
`aces`, once, with the rehearsal password: `sudo tee /etc/sudoers.d/90-aces-rehearsal` with the two lines
`aces ALL=(ALL) NOPASSWD: ALL` and `Defaults:aces verifypw = any`, then `sudo chmod 440` it. Without that rule step 7
needs a person to type the password at the `ssh -t` prompts, as the table says.

Still unverified: Hyprland and omarchy-shell on Hyper-V's synthetic display, and therefore the bar widget rendering live
(nobody has logged into the desktop session; `omarchy plugin validate` and `qmllint` against Omarchy's own `qs.Commons`
and `qs.Ui` modules are the checks that ran). `qmllint` found two real defects in the plugin, fixed in `aces-omarchy`
0.1.0-3: `Service.qml` declared a `state` property, shadowing Item's built-in one, and the `Process.exited` handler
named a parameter type the shell cannot resolve. The remaining warning classes (`unqualified`, `missing-property` on the
untyped `bar` object) occur in Omarchy's first-party plugins in the same numbers.

## Known problems to expect

- With the VHDX cidata the VM has two SCSI disks; the config names `/dev/sda`, which should be the OS disk at LUN 0, but
  Linux does not guarantee sd names. The ISO variant leaves one disk and no ambiguity.
- On HA-External the VM is a LAN host: `aces doctor` makes read-only GETs to the rig, and the ACES post-boot and
  post-update hooks run `aces fabric refresh`, which also POSTs a tool call to rig-state `:8010`. Use
  `ACES_DOCTOR_ARGS=--offline` and point `~/.config/aces/aces.toml` elsewhere if the rig must not be contacted.
- Whether Hyprland and omarchy-shell come up on Hyper-V's synthetic display is unverified; the SSH path does not need them.

## Reset

- Re-run the ACES overlay on a clean Omarchy: `Restore-VMCheckpoint -VMName aces-rehearsal -Name omarchy-clean -Confirm:$false`.
- Start over: `Stop-VM aces-rehearsal -TurnOff; Remove-VM aces-rehearsal -Force; Remove-Item -Recurse D:\HyperV\aces-rehearsal\vm, D:\HyperV\aces-rehearsal\ssh`
  then steps 3 to 5, and step 2 first if `cidata/` changed (the ISO stays). Rebuild the cidata drive only while no
  running VM has it attached.
- Stop serving: `.\serve-repo.ps1 -Stop`. Remove everything: delete `D:\HyperV\aces-rehearsal`.
