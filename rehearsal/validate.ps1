<#
.SYNOPSIS
  From the host: find the rehearsal VM, optionally run aces-install.sh in it over ssh, fetch and summarise its report.
.DESCRIPTION
  The VM's address comes from, in order: -VmIp; Hyper-V's KVP data (only when the guest runs hv_kvp_daemon, which a
  stock Omarchy install does not); the repo server's access log (the VM shows up there once it has fetched anything
  from serve-repo.ps1); name lookups for aces-rehearsal, aces-rehearsal.local and aces-rehearsal.mshome.net. The first
  candidate that answers on TCP 22 is used; the rig (192.168.1.65) is never probed.
  -Install copies the served aces-install.sh into the VM and runs it with ssh -t, so sudo can ask for the rehearsal
  password. Then the report (~/aces-rehearsal-report.txt) is copied to D:\HyperV\aces-rehearsal\reports and summarised.
  When no address answers, the manual steps are printed instead. SSH uses this workstation's key (the one
  make-cidata.ps1 put on the cidata drive) and its own known_hosts under D:\HyperV\aces-rehearsal\ssh, so a reinstall
  never trips the host-key check in ~/.ssh/known_hosts.
  Exit status: 0 report all PASS, 1 report has FAIL or STOP lines, 2 no VM reached or no report yet.
.EXAMPLE
  .\validate.ps1 -VmIp 192.168.1.80 -Install
  .\validate.ps1
#>
[CmdletBinding()]
param(
  [string]$VmIp,
  [string]$Name = 'aces-rehearsal',
  [string]$User = 'aces',
  [string]$Identity = (Join-Path $env:USERPROFILE '.ssh\id_ed25519'),
  [string]$Root = 'D:\HyperV\aces-rehearsal',
  [switch]$Install
)
$ErrorActionPreference = 'Stop'
$aces = Split-Path -Parent $PSScriptRoot
$serve = Join-Path $Root 'serve'
$base = Get-Content -LiteralPath (Join-Path $serve 'url') -ErrorAction SilentlyContinue | Select-Object -First 1
$rig = '192.168.1.65'

function Test-Ssh([string]$ip) {
  $c = [Net.Sockets.TcpClient]::new()
  try { $c.ConnectAsync($ip, 22).Wait(2000) -and $c.Connected } catch { $false } finally { $c.Dispose() }
}
$cands = @()
if ($VmIp) { $cands += $VmIp }
$cands += @((Get-VMNetworkAdapter -VMName $Name -ErrorAction SilentlyContinue).IPAddresses | Where-Object { $_ -match '^\d+(\.\d+){3}$' })
$log = Join-Path $serve 'access.log'
if (Test-Path -LiteralPath $log) {
  $own = @((Get-NetIPAddress -AddressFamily IPv4).IPAddress) + '127.0.0.1'
  # Get-Content, not Select-String -LiteralPath: the running server holds the log open for writing
  $cands += @(Get-Content -LiteralPath $log | Select-String -Pattern '^(\d+\.\d+\.\d+\.\d+) .*"GET /(x86_64/aces\.db|aces-install\.sh)' |
      ForEach-Object { $_.Matches[0].Groups[1].Value } | Where-Object { $own -notcontains $_ } | Select-Object -Last 1)
}
foreach ($n in $Name, "$Name.local", "$Name.mshome.net") {
  $cands += @(Resolve-DnsName -Name $n -Type A -QuickTimeout -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress } |
      Select-Object -First 1 -ExpandProperty IPAddress)
}
$cands = @($cands | Where-Object { $_ -and $_ -ne $rig -and $_ -notlike '169.254.*' } | Select-Object -Unique)
$ip = $cands | Where-Object { Test-Ssh $_ } | Select-Object -First 1
if (-not $ip) {
  $u = if ($base) { $base } else { 'http://HOST:PORT (start .\serve-repo.ps1)' }
  Write-Host "No address for $Name answered on port 22 (tried: $(if ($cands) { $cands -join ', ' } else { 'none found' }))."
  Write-Host "  1. Start-VM $Name; vmconnect.exe localhost $Name   The unattended install reboots into the Omarchy login."
  Write-Host "  2. Log in as $User (rehearsal password: rehearsal/README.md), open a terminal (Super+Return): ip -4 -brief address"
  Write-Host "  3. In that terminal: curl -fsSLo ~/aces-install.sh $u/aces-install.sh && bash ~/aces-install.sh $u"
  Write-Host "     or from here:    .\validate.ps1 -VmIp <address> -Install"
  Write-Host "  4. Fetch the report: .\validate.ps1 -VmIp <address>"
  exit 2
}
Write-Host "VM $Name answers on ${ip}:22"

$sshDir = Join-Path $Root 'ssh'
New-Item -ItemType Directory -Force $sshDir | Out-Null
$opts = @('-i', $Identity, '-o', 'StrictHostKeyChecking=accept-new', '-o', ('UserKnownHostsFile=' + ((Join-Path $sshDir 'known_hosts') -replace '\\', '/')), '-o', 'ConnectTimeout=10')
if ($Install) {
  if (-not $base) { throw 'no repo URL recorded: run .\serve-repo.ps1 first' }
  & scp.exe @opts (Join-Path $serve 'www\aces-install.sh') "${User}@${ip}:aces-install.sh"
  if ($LASTEXITCODE) { throw "scp to $ip failed (exit $LASTEXITCODE)" }
  $prefix = ''
  if (Test-Path -LiteralPath (Join-Path $serve 'www\aces-pkgs.pub.asc')) {
    $prefix = 'ACES_KEY_FPR=' + (Get-Content -LiteralPath (Join-Path $aces 'keys\FINGERPRINT') | Select-Object -First 1).Trim() + ' '
  }
  # A login shell, as a terminal in the VM would be: Arch's bash reads no rc file for a plain ssh command, so without
  # -l the environment Omarchy's ~/.bashrc sets up (OMARCHY_PATH, PATH) is missing and `omarchy update` aborts.
  & ssh.exe -t @opts "${User}@${ip}" "bash -lc '${prefix}bash ~/aces-install.sh $base'"
  Write-Host "aces-install.sh exited with $LASTEXITCODE"
}

$reports = Join-Path $Root 'reports'
New-Item -ItemType Directory -Force $reports | Out-Null
$dest = Join-Path $reports ('aces-rehearsal-report-{0:yyyyMMdd-HHmmss}.txt' -f (Get-Date))
& scp.exe @opts "${User}@${ip}:aces-rehearsal-report.txt" $dest
if ($LASTEXITCODE) { Write-Warning "no report on $ip yet: run with -Install, or run aces-install.sh in the VM"; exit 2 }
Get-Content -LiteralPath $dest | Write-Host
$bad = @(Select-String -LiteralPath $dest -Pattern '^(FAIL|STOP) ').Count
$good = @(Select-String -LiteralPath $dest -Pattern '^PASS ').Count
Write-Host "report saved: $dest  ($good passed, $bad failed or stopped)"
if ($bad) { exit 1 }
