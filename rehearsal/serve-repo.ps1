<#
.SYNOPSIS
  Serve the local [aces] package repo to the rehearsal VM over HTTP on the LAN (python -m http.server, background).
.DESCRIPTION
  Builds a web root at D:\HyperV\aces-rehearsal\serve\www from repo\x86_64 and serves it on 0.0.0.0:<port> (8930, or
  the next free port). The web root, not repo\ itself, is served because repo-add runs in the Linux build container
  and leaves aces.db and aces.files as WSL symlinks (reparse tag 0xA000001D) that Windows programs cannot open, so a
  plain http.server answers 404 for the aces.db pacman asks for. The web root gets real copies instead.
  Signed mode: keys\aces-pkgs.pub.asc is published (at /aces-pkgs.pub.asc) only when every package has a .sig beside
  it; aces-install.sh then uses SigLevel = Required DatabaseOptional. Otherwise nothing key-related is published and the
  VM uses SigLevel = Optional TrustAll. The secret key and the GnuPG home are never copied.
  Re-running while the server is up re-syncs the web root (pick up a rebuild) and prints the URLs again.
  The VM reaches this host on the switch's host vNIC; Windows Firewall must allow inbound TCP to this python.exe
  on that network's profile (see README).
.EXAMPLE
  .\serve-repo.ps1 -WhatIf
  .\serve-repo.ps1
  .\serve-repo.ps1 -Stop
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [int]$Port = 8930,
  [string]$Switch = 'HA-External',
  [string]$Root = 'D:\HyperV\aces-rehearsal',
  [switch]$Stop
)
$ErrorActionPreference = 'Stop'
$aces = Split-Path -Parent $PSScriptRoot
$src = Join-Path $aces 'repo\x86_64'
$state = Join-Path $Root 'serve'
$www = Join-Path $state 'www'
$pidFile = Join-Path $state 'http.pid'

# The server from the pid file, if that pid is still a python process. Get-Process rather than Get-CimInstance: loading
# the CIM module under -WhatIf prints a page of "Set Alias" what-if lines.
function Get-Server {
  $id = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $id) { return $null }
  $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
  if ($p -and $p.ProcessName -like 'python*') { return $p }
  return $null
}
$running = Get-Server
if ($Stop) {
  if (-not $running) { Write-Host 'no repo server was running'; return }
  if ($PSCmdlet.ShouldProcess("pid $($running.Id)", 'stop the repo server')) {
    Stop-Process -Id $running.Id; Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    Write-Host "stopped pid $($running.Id)"
  }
  return
}

$hostIp = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "vEthernet ($Switch)" -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1 -ExpandProperty IPAddress
if (-not $hostIp) { throw "no IPv4 address on 'vEthernet ($Switch)'; is the switch shared with the host (AllowManagementOS)?" }
if ($running) { $Port = [int](Get-Content -LiteralPath (Join-Path $state 'port') | Select-Object -First 1) }
else {
  function Test-Free([int]$p) { $l = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Any, $p); try { $l.Start(); $true } catch { $false } finally { $l.Stop() } }
  $first = $Port
  while (-not (Test-Free $Port)) { $Port++; if ($Port -gt $first + 20) { throw "no free port in $first..$($first + 20)" } }
}
$base = "http://${hostIp}:$Port"

# What to publish: packages and their .sig files, the db/files archives, and readable copies of the WSL symlinks.
if (-not (Test-Path -LiteralPath $src)) { throw "no package repo at $src (run bin/build first)" }
$want = @{}
foreach ($f in Get-ChildItem -LiteralPath $src -Force | Where-Object { -not $_.PSIsContainer }) {
  if ($f.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    if ($f.Name -notmatch '^(.+\.(db|files))(\.sig)?$') { Write-Warning "skipping link $($f.Name)"; continue }
    $target = Join-Path $src ($Matches[1] + '.tar.zst' + $Matches[3])
    if (Test-Path -LiteralPath $target) { $want[$f.Name] = $target }
  } elseif ($f.Name -match '\.(pkg|db|files)\.tar\.zst(\.sig)?$') { $want[$f.Name] = $f.FullName }
}
$pkgs = @(Get-ChildItem -LiteralPath $src -File | Where-Object { $_.Name -like '*.pkg.tar.zst' })
$unsigned = @($pkgs | Where-Object { -not (Test-Path -LiteralPath ($_.FullName + '.sig')) })
$keyFile = Join-Path $aces 'keys\aces-pkgs.pub.asc'
$signed = (Test-Path -LiteralPath $keyFile) -and $pkgs.Count -gt 0 -and $unsigned.Count -eq 0
$mode = if ($signed) { 'signed: SigLevel = Required DatabaseOptional' }
  elseif (Test-Path -LiteralPath $keyFile) { "unsigned: the key exists but $($unsigned.Count) of $($pkgs.Count) packages have no .sig, so it is not published" }
  else { 'unsigned: no keys\aces-pkgs.pub.asc yet' }

if ($PSCmdlet.ShouldProcess($www, "sync $($want.Count) repo files, aces-install.sh$(if ($signed) { ' and the public key' })")) {
  $dst = Join-Path $www 'x86_64'
  New-Item -ItemType Directory -Force $dst | Out-Null
  foreach ($name in $want.Keys) {
    $s = Get-Item -LiteralPath $want[$name]; $d = Get-Item -LiteralPath (Join-Path $dst $name) -ErrorAction SilentlyContinue
    if (-not $d -or $d.Length -ne $s.Length -or $d.LastWriteTimeUtc -ne $s.LastWriteTimeUtc) { Copy-Item -LiteralPath $s.FullName -Destination (Join-Path $dst $name) -Force }
  }
  Get-ChildItem -LiteralPath $dst -File | Where-Object { -not $want.ContainsKey($_.Name) } | Remove-Item -Force
  $sh = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'post-install\aces-install.sh'))
  [IO.File]::WriteAllText((Join-Path $www 'aces-install.sh'), ($sh -replace "`r`n", "`n"))
  $pub = Join-Path $www 'aces-pkgs.pub.asc'
  if ($signed) { Copy-Item -LiteralPath $keyFile -Destination $pub -Force } else { Remove-Item -LiteralPath $pub -ErrorAction SilentlyContinue }
  if (Get-ChildItem -LiteralPath $www -Recurse -File | Where-Object { $_.Name -match '\.sec\.asc$|secring|private-keys' }) { throw 'secret key material in the web root; refusing to serve' }
}

if (-not $running -and $PSCmdlet.ShouldProcess("0.0.0.0:$Port", "start python -m http.server for $www")) {
  $py = (Get-Command python -ErrorAction Stop).Source
  $p = Start-Process -FilePath $py -ArgumentList "-u -m http.server $Port --bind 0.0.0.0 --directory `"$www`"" -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $state 'http.log') -RedirectStandardError (Join-Path $state 'access.log')
  Set-Content -LiteralPath $pidFile -Value $p.Id
  Set-Content -LiteralPath (Join-Path $state 'port') -Value $Port
  $ok = $false
  foreach ($i in 1..20) { Start-Sleep -Milliseconds 250; & curl.exe -fs -o NUL "http://127.0.0.1:$Port/x86_64/aces.db"; if ($LASTEXITCODE -eq 0) { $ok = $true; break } }
  if (-not $ok) { throw "the server did not answer on port $Port; see $state\http.log and access.log" }
  Write-Host "started pid $($p.Id) with $py"
}
if (-not $WhatIfPreference) { Set-Content -LiteralPath (Join-Path $state 'url') -Value $base }
$fpr = if ($signed) { (Get-Content -LiteralPath (Join-Path $aces 'keys\FINGERPRINT') | Select-Object -First 1).Trim() }
$envPrefix = if ($fpr) { "ACES_KEY_FPR=$fpr " } else { '' }
Write-Host "[aces] repo  $base/x86_64  ($mode)"
if ($fpr) { Write-Host "key         $base/aces-pkgs.pub.asc  fingerprint $fpr" }
Write-Host "in the VM    curl -fsSLo ~/aces-install.sh $base/aces-install.sh && ${envPrefix}bash ~/aces-install.sh $base"
Write-Host "from here    .\validate.ps1 -Install    (stop the server: .\serve-repo.ps1 -Stop)"
