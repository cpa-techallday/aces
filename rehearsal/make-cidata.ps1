<#
.SYNOPSIS
  Build the cidata drive that makes the Omarchy ISO install itself into the rehearsal VM.
.DESCRIPTION
  Stages user_configuration.json, user_credentials.json and authorized_keys (the committed comment template plus this
  workstation's PUBLIC key) and writes them to D:\HyperV\aces-rehearsal\cidata as either
    - cidata.vhdx (default): 64 MB dynamic VHDX, MBR, one FAT32 partition labelled cidata. Mount-VHD, Initialize-Disk,
      New-Partition and Format-Volume need an ELEVATED shell (New-VHD alone works for Hyper-V Administrators); or
    - cidata.iso (-Iso, and automatically when the shell is not elevated): ISO9660 + Joliet volume labelled cidata,
      built with oscdimg.exe when the Windows ADK is installed, else with the IMAPI2 image builder Windows ships.
      No elevation needed, and the VM then has exactly one disk (/dev/sda), which the config names.
  The Omarchy installer accepts any filesystem labelled cidata or CIDATA (omarchy-cidata-load).
.EXAMPLE
  .\make-cidata.ps1 -WhatIf
  .\make-cidata.ps1 -Iso
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [string]$Root = 'D:\HyperV\aces-rehearsal',
  [string]$PublicKey = (Join-Path $env:USERPROFILE '.ssh\id_ed25519.pub'),
  [switch]$Iso
)
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'cidata'
$out = Join-Path $Root 'cidata'
$stage = Join-Path $out 'stage'
$files = 'user_configuration.json', 'user_credentials.json', 'authorized_keys'

# Inputs: both JSON files parse, the partition table fits the 80 GiB disk new-vm.ps1 creates, a public key exists.
$cfg = Get-Content -Raw (Join-Path $src 'user_configuration.json') | ConvertFrom-Json
$main = $cfg.disk_config.device_modifications[0].partitions[1]
$end = [int64]$main.start.value + [int64]$main.size.value + 1MB
if ($end -ne 80GB) { throw "user_configuration.json partitions end at $end bytes, but new-vm.ps1 creates an 80 GiB disk" }
$null = Get-Content -Raw (Join-Path $src 'user_credentials.json') | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $PublicKey)) { throw "no public key at $PublicKey (pass -PublicKey <file.pub>)" }
$keys = @(Get-Content -LiteralPath $PublicKey | Where-Object { $_ -match '^(ssh-|ecdsa-|sk-)' })
if ($keys.Count -eq 0) { throw "$PublicKey contains no public key line (never pass a private key)" }
Write-Host "cidata: hostname $($cfg.hostname), disk $($cfg.disk_config.device_modifications[0].device), $($keys.Count) key(s) from $PublicKey"

$elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $Iso -and -not $elevated) {
  if ($WhatIfPreference) { Write-Warning 'not elevated: a real VHDX run needs an elevated shell (or use -Iso)' }
  else { Write-Warning 'not elevated: Mount-VHD/Format-Volume need admin, so building the ISO variant instead'; $Iso = $true }
}

if ($PSCmdlet.ShouldProcess($stage, 'stage the cidata files (authorized_keys = template + public key)')) {
  New-Item -ItemType Directory -Force $stage | Out-Null
  foreach ($f in $files[0..1]) { Copy-Item -LiteralPath (Join-Path $src $f) -Destination (Join-Path $stage $f) -Force }
  $ak = @(Get-Content (Join-Path $src 'authorized_keys')) + $keys
  [IO.File]::WriteAllText((Join-Path $stage 'authorized_keys'), (($ak -join "`n") + "`n"))
}

if (-not $Iso) {
  $vhd = Join-Path $out 'cidata.vhdx'
  if ($PSCmdlet.ShouldProcess($vhd, 'New-VHD 64 MB dynamic, Mount-VHD, Initialize-Disk MBR, New-Partition, Format-Volume FAT32 label cidata, copy, Dismount-VHD')) {
    if (Test-Path -LiteralPath $vhd) { Dismount-VHD -Path $vhd -ErrorAction SilentlyContinue; Remove-Item -LiteralPath $vhd -Force }
    New-VHD -Path $vhd -SizeBytes 64MB -Dynamic | Out-Null
    try {
      $disk = Mount-VHD -Path $vhd -Passthru | Get-Disk
      Initialize-Disk -Number $disk.Number -PartitionStyle MBR
      $part = New-Partition -DiskNumber $disk.Number -UseMaximumSize -MbrType FAT32 -AssignDriveLetter
      Format-Volume -Partition $part -FileSystem FAT32 -NewFileSystemLabel cidata -Confirm:$false | Out-Null
      $letter = (Get-Partition -DiskNumber $disk.Number -PartitionNumber $part.PartitionNumber).DriveLetter
      if (-not $letter) { throw 'the cidata partition got no drive letter' }
      foreach ($f in $files) { Copy-Item -LiteralPath (Join-Path $stage $f) -Destination "${letter}:\" -Force }
      Get-ChildItem "${letter}:\" | Format-Table Name, Length -AutoSize | Out-String | Write-Host
    } finally { Dismount-VHD -Path $vhd }
    Write-Host "cidata drive: $vhd (attach as a second SCSI disk; new-vm.ps1 does)"
  }
  return
}

$isoPath = Join-Path $out 'cidata.iso'
$oscdimg = Get-Command oscdimg.exe -ErrorAction SilentlyContinue
$tool = if ($oscdimg) { 'oscdimg' } else { 'IMAPI2 (built into Windows)' }
if ($PSCmdlet.ShouldProcess($isoPath, "write an ISO9660+Joliet image labelled cidata with $tool")) {
  if ($oscdimg) {
    & $oscdimg.Source -j1 -lcidata $stage $isoPath
    if ($LASTEXITCODE -ne 0) { throw "oscdimg failed with exit code $LASTEXITCODE" }
  } else {
    if (-not ('CidataIso' -as [type])) {
      Add-Type -TypeDefinition @'
using System; using System.IO; using System.Runtime.InteropServices; using System.Runtime.InteropServices.ComTypes;
public static class CidataIso {
  public static void Save(object image, string path) {
    IStream s = (IStream)image; byte[] buf = new byte[65536]; IntPtr got = Marshal.AllocHGlobal(4);
    try { using (FileStream f = File.Create(path)) { while (true) {
      s.Read(buf, buf.Length, got); int n = Marshal.ReadInt32(got); if (n <= 0) break; f.Write(buf, 0, n); } } }
    finally { Marshal.FreeHGlobal(got); }
  }
}
'@
    }
    $fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
    $fsi.FileSystemsToCreate = 3      # ISO9660 (1) + Joliet (2): Linux reads the long Joliet names
    $fsi.VolumeName = 'cidata'
    $fsi.Root.AddTree($stage, $false)  # the folder's contents at the image root
    [CidataIso]::Save($fsi.CreateResultImage().ImageStream, $isoPath)
  }
  Write-Host "cidata drive: $isoPath ($((Get-Item -LiteralPath $isoPath).Length) bytes; attach as a second DVD drive; new-vm.ps1 does)"
}
