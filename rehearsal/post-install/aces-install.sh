#!/usr/bin/env bash
# ACES rehearsal, inside the Omarchy VM. Run as the desktop user from an interactive terminal (sudo asks on the tty):
#   bash aces-install.sh http://HOST:PORT            (HOST:PORT as printed by rehearsal/serve-repo.ps1)
# Adds the [aces] repo the way Omarchy takes custom repos (an /etc/pacman.d snippet included above [omarchy], plus a
# pre-refresh-pacman hook that puts the include back after `omarchy refresh pacman`), installs the ACES packages,
# exercises the overlay and writes ~/aces-rehearsal-report.txt (full output in ~/aces-rehearsal.log).
# Exit status 0 only when every step passed.
# Environment:
#   ACES_PKGS         packages to install (default aces-os)
#   ACES_UPDATE       omarchy (default): `omarchy update -y` first, then `pacman -S`; Omarchy blocks a direct -Syu
#                     pacman: `pacman -Syu` with OMARCHY_ALLOW_DIRECT_PACMAN=1 (bypasses Omarchy's update path)
#                     none: `pacman -Sy` only, a partial upgrade (quick, unsupported on Arch)
#   ACES_KEY_FPR      expected signing-key fingerprint; checked when the server publishes a key
#   ACES_DOCTOR_ARGS  extra arguments for `aces doctor` (e.g. --offline)
set -uo pipefail
url="${1:-${ACES_REPO_URL:-}}"; url="${url%/}"
[[ -n $url ]] || { echo "usage: bash aces-install.sh http://HOST:PORT" >&2; exit 2; }
[[ $EUID -ne 0 ]] || { echo "run this as the desktop user, not root; it calls sudo where needed" >&2; exit 2; }
pkgs="${ACES_PKGS:-aces-os}"; update="${ACES_UPDATE:-omarchy}"
# Omarchy's tools need OMARCHY_PATH (omarchy-update-dev runs under set -u and aborts without it). A login shell gets it
# from ~/.bashrc, which sources Omarchy's env-bootstrap before its interactive guard; a plain ssh command on Arch's bash
# reads no rc file at all, so do the same here when it is missing.
if [[ -z ${OMARCHY_PATH:-} && -r /usr/share/omarchy/default/bash/env-bootstrap ]]; then
  set +u; source /usr/share/omarchy/default/bash/env-bootstrap; set -u
fi
cfg="${XDG_CONFIG_HOME:-$HOME/.config}"
report="$HOME/aces-rehearsal-report.txt"; log="$HOME/aces-rehearsal.log"; tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0; : >"$report"; : >"$log"

note() { printf '%s\n' "$*" | tee -a "$report"; }
run() {  # run <label> <command...>: output to the terminal and the log; the verdict and the last lines to the report
  local label="$1" rc; shift
  printf '\n== %s\n$ %s\n' "$label" "$*" | tee -a "$log"
  : >"$tmp/out"
  "$@" 2>&1 | tee -a "$log" "$tmp/out"; rc=${PIPESTATUS[0]}
  if (( rc == 0 )); then note "PASS  $label"; else note "FAIL  $label (exit $rc)"; fails=$((fails + 1)); fi
  tail -n "${TAIL:-15}" "$tmp/out" | sed 's/^/      /' >>"$report"
  return "$rc"
}
stop() { note "STOP  $*"; note "summary: $(grep -c '^PASS' "$report") passed, $fails failed, stopped early"; exit 1; }

note "ACES rehearsal report  $(date -Is)"
note "host     $(uname -n)  kernel $(uname -r)"
note "omarchy  $(pacman -Q omarchy 2>/dev/null || echo 'not installed')"
note "repo     $url/x86_64  packages: $pkgs  update: $update"
# Unattended hosts (NOPASSWD) first: `sudo -v` alone prompts there too when any other rule for the user, such as the
# stock %wheel line, lacks NOPASSWD (sudoers verifypw=all); a prompt with nobody at the keyboard just fails the run.
sudo -n true 2>/dev/null || sudo -v || stop "sudo is needed for the repo setup and pacman"

run "the [aces] repo answers" curl -fsS -o /dev/null -w '%{http_code}, %{size_download} bytes\n' "$url/x86_64/aces.db" ||
  stop "the repo is not reachable from here; is serve-repo.ps1 running, and does the host firewall let this VM in?"

siglevel="Optional TrustAll"; want="${ACES_KEY_FPR:-}"; want="${want// /}"; want="${want^^}"
if curl -fsS -o "$tmp/key.asc" "$url/aces-pkgs.pub.asc" 2>/dev/null; then
  fpr="$(gpg --with-colons --show-keys "$tmp/key.asc" 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')"
  [[ -n $fpr ]] || stop "the server published a key with no readable fingerprint"
  [[ -z $want || $want == "$fpr" ]] || stop "the served key is $fpr, not the expected $want"
  # --init is idempotent: it creates the local master key (needed to lsign) only when the keyring has none
  run "trust the ACES signing key $fpr (local signature)" \
    sudo sh -c 'pacman-key --init && pacman-key --add "$1" && pacman-key --lsign-key "$2"' _ "$tmp/key.asc" "$fpr" ||
    stop "could not import the key"
  siglevel="Required DatabaseOptional"
  note "key      $fpr$([[ -n $want ]] && echo ' (matches ACES_KEY_FPR)' || echo ' (ACES_KEY_FPR not set: not cross-checked)')"
fi
note "siglevel $siglevel"

repo_conf=/etc/pacman.d/aces.conf; marker="Include = $repo_conf"
add_repo() {
  printf '# ACES rehearsal repo, written by aces-install.sh\n[aces]\nSigLevel = %s\nServer = %s/x86_64\n' "$siglevel" "$url" |
    sudo tee "$repo_conf" >/dev/null || return 1
  if grep -qxF "$marker" /etc/pacman.conf; then :
  elif grep -q '^\[omarchy\]' /etc/pacman.conf; then sudo sed -i "0,/^\[omarchy\]/s||$marker\n\n[omarchy]|" /etc/pacman.conf
  else printf '\n%s\n' "$marker" | sudo tee -a /etc/pacman.conf >/dev/null; fi
  grep -n -F "$marker" /etc/pacman.conf && cat "$repo_conf"
}
run "add [aces]: $repo_conf, included from /etc/pacman.conf" add_repo || stop "could not configure pacman"
mkdir -p "$cfg/omarchy/hooks/pre-refresh-pacman.d"
cat >"$cfg/omarchy/hooks/pre-refresh-pacman.d/aces-repo" <<'HOOK'
#!/bin/bash
# Written by the ACES rehearsal (aces-install.sh). `omarchy refresh pacman` rewrites /etc/pacman.conf from the channel
# template, then runs this hook: put the [aces] include back above [omarchy], as Omarchy's own sample hook does.
CONF=/etc/pacman.conf
MARKER="Include = /etc/pacman.d/aces.conf"
[[ -r /etc/pacman.d/aces.conf ]] || exit 0
grep -qxF "$MARKER" "$CONF" && exit 0
sudo sed -i "0,/^\[omarchy\]/s||$MARKER\n\n[omarchy]|" "$CONF"
HOOK
chmod 755 "$cfg/omarchy/hooks/pre-refresh-pacman.d/aces-repo"
note "hook     $cfg/omarchy/hooks/pre-refresh-pacman.d/aces-repo"

# shellcheck disable=SC2086  # $pkgs is a list of package names
if [[ $update == omarchy ]] && command -v omarchy >/dev/null 2>&1; then
  run "omarchy update -y (snapshot, full upgrade, migrations; syncs [aces] too)" omarchy update -y || stop "omarchy update failed"
  run "install $pkgs" sudo pacman -S --needed --noconfirm $pkgs || stop "package install failed"
elif [[ $update == none ]]; then
  run "install $pkgs with pacman -Sy (partial upgrade)" sudo pacman -Sy --needed --noconfirm $pkgs || stop "package install failed"
else
  run "install $pkgs with pacman -Syu" sudo env OMARCHY_ALLOW_DIRECT_PACMAN=1 pacman -Syu --needed --noconfirm $pkgs ||
    stop "package install failed"
fi

run "packages in [aces]" pacman -Sl aces
run "aces --version" aces --version
# shellcheck disable=SC2086
run "aces doctor ${ACES_DOCTOR_ARGS:-}" aces doctor ${ACES_DOCTOR_ARGS:-}
run "aces-omarchy-setup" aces-omarchy-setup
run "ACES hooks linked" ls -l "$cfg/omarchy/hooks/post-boot.d" "$cfg/omarchy/hooks/post-update.d"
run "omarchy plugin validate on the linked plugin" omarchy plugin validate "$cfg/omarchy/plugins/aces-fabric"
run "omarchy plugin validate on the packaged copy" omarchy plugin validate /usr/share/aces/omarchy/plugins/aces-fabric
run "aces-creative-labels" aces-creative-labels
if pacman -Q aces-engineering >/dev/null 2>&1; then  # the golden path from the ACES boot: computed part, verified, in both ledgers
  TAIL=25 run "aces golden run --demo" aces golden run --demo --progress lines
  run "aces golden verify latest" aces golden verify latest
else
  note "SKIP  aces golden (aces-engineering not installed)"
fi
[[ -d /usr/lib/modules/$(uname -r) ]] || note "note     no /usr/lib/modules/$(uname -r): the kernel was replaced, reboot before using the desktop"
note ""
note "summary: $(grep -c '^PASS' "$report") passed, $fails failed"
(( fails == 0 ))
