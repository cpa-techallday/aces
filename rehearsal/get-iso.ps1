<#
.SYNOPSIS
  Download the latest Omarchy ISO and its .sha256 sidecar into D:\HyperV\aces-rehearsal\iso and verify it.
.DESCRIPTION
  The omarchy-iso README points at the ISO link on omarchy.org and says every published ISO has a .sha256 beside it at
  the same URL. This script takes the newest https://iso.omarchy.org/omarchy-<version>.iso link on omarchy.org (or
  -Version), reads the sidecar, and skips the download when a local copy already matches it. The download goes to
  <name>.partial with curl.exe (resumable: re-run after an interruption) and is renamed only after Get-FileHash matches.
  -WhatIf resolves the URL, size and checksum and reports what it would do; it writes nothing.
.EXAMPLE
  .\get-iso.ps1 -WhatIf
  .\get-iso.ps1 -Version 4.0.4
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [string]$Version,
  [string]$Root = 'D:\HyperV\aces-rehearsal',
  [string]$IndexUrl = 'https://omarchy.org/'
)
$ErrorActionPreference = 'Stop'
$curl = (Get-Command curl.exe -ErrorAction Stop).Source   # the real curl, not the PowerShell alias
$dir = Join-Path $Root 'iso'

if ($Version) {
  $url = "https://iso.omarchy.org/omarchy-$Version.iso"
} else {
  $html = (& $curl -fsSL --max-time 60 $IndexUrl) -join "`n"
  if ($LASTEXITCODE) { throw "could not read $IndexUrl (curl exit $LASTEXITCODE); pass -Version" }
  $links = @([regex]::Matches($html, 'https://iso\.omarchy\.org/omarchy-(\d+(?:\.\d+){1,3})\.iso(?![.\w])') |
      Sort-Object { [version]$_.Groups[1].Value } -Descending)
  if ($links.Count -eq 0) { throw "no https://iso.omarchy.org/omarchy-<version>.iso link on $IndexUrl; pass -Version" }
  $url = $links[0].Value
}
$name = Split-Path $url -Leaf
$iso = Join-Path $dir $name

$sidecar = ((& $curl -fsSL --max-time 60 "$url.sha256") -join "`n").Trim()
if ($LASTEXITCODE -or $sidecar -notmatch '^([0-9a-fA-F]{64})\s+\*?(\S+)$') { throw "could not read a checksum from $url.sha256" }
$want = $Matches[1].ToLower()
if ($Matches[2] -ne $name) { throw "$url.sha256 names $($Matches[2]), not $name" }
$head = (& $curl -fsSIL --max-time 60 $url) -join "`n"
if ($LASTEXITCODE) { throw "HEAD $url failed (curl exit $LASTEXITCODE)" }
$size = [int64](([regex]::Matches($head, '(?im)^content-length:\s*(\d+)') | Select-Object -Last 1).Groups[1].Value)
$free = [IO.DriveInfo]::new((Split-Path -Qualifier $Root)).AvailableFreeSpace
Write-Host ("ISO      {0}  ({1:N2} GiB; {2:N0} GiB free on {3})" -f $url, ($size / 1GB), ($free / 1GB), (Split-Path -Qualifier $Root))
Write-Host "SHA-256  $want  (from $url.sha256)"
if ($size -gt $free) { throw "not enough free space on $(Split-Path -Qualifier $Root) for $name" }

function Save-Sidecar { [IO.File]::WriteAllText("$iso.sha256", "$sidecar`n") }   # same bytes as the published sidecar

if (Test-Path -LiteralPath $iso) {
  Write-Host "found $iso, hashing it (about a minute for 6 GB)"
  $have = (Get-FileHash -LiteralPath $iso -Algorithm SHA256).Hash.ToLower()
  if ($have -eq $want) {
    if (-not (Test-Path -LiteralPath "$iso.sha256") -and $PSCmdlet.ShouldProcess("$iso.sha256", 'write the sidecar')) { Save-Sidecar }
    Write-Host "already present and valid: $iso"
    return
  }
  Write-Warning "$iso does not match the published checksum (local $have); it will be replaced"
}

if ($PSCmdlet.ShouldProcess($iso, "download $url ($size bytes) via $name.partial, verify SHA-256, keep the .sha256 beside it")) {
  New-Item -ItemType Directory -Force $dir | Out-Null
  $partial = "$iso.partial"
  & $curl -fL --retry 5 --retry-delay 5 -C - -o $partial $url
  if ($LASTEXITCODE) { throw "download failed (curl exit $LASTEXITCODE); re-run to resume $partial" }
  Write-Host 'verifying SHA-256'
  $have = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLower()
  if ($have -ne $want) {
    Remove-Item -LiteralPath $partial
    throw "checksum mismatch: got $have, expected $want; deleted the download, re-run to fetch it again"
  }
  Move-Item -LiteralPath $partial -Destination $iso -Force
  Save-Sidecar
  Write-Host "verified: $iso"
}
