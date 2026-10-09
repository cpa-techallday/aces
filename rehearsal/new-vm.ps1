<#
.SYNOPSIS
  Create the Gen2 Hyper-V VM "aces-rehearsal" that installs Omarchy by itself from the ISO plus the cidata drive.
.DESCRIPTION
  4 vCPU, 8 GB static memory, an 80 GiB dynamic VHDX under D:\HyperV\aces-rehearsal\vm (cidata/user_configuration.json
  carries the partition table for exactly this size and names it /dev/sda), network on HA-External, Secure Boot OFF:
  the Omarchy ISO boots an unsigned GRUB and kernel (omarchy-iso plans/consumer-secure-boot.md, "Secure Boot must be
  disabled today"; the README's Proxmox example enrols no keys). OS disk at SCSI 0:0, ISO DVD at 0:1, cidata at 0:2
  (a second DVD for cidata.iso, a second disk for cidata.vhdx), console 1920x1080 (Set-VMVideo; a warning if refused).
  A failure part-way removes the half-built VM again (the VHDX is kept), so a re-run starts clean.
  Boot order is disk first, then the ISO, as the Omarchy manual prescribes: the empty disk falls through to the ISO on
  the first boot and the installed system boots from disk afterwards. -DvdFirst puts the ISO first, but then every
  reboot boots the installer again, finds cidata again and reinstalls, so use it only to watch a single install.
  Idempotent: an existing VM of that name is reported and left alone. Members of Hyper-V Administrators should need no
  elevation (Get-VM and New-VHD ran unelevated here; New-VM has not been run). -WhatIf prints the plan only.
.EXAMPLE
  .\new-vm.ps1 -WhatIf
  .\new-vm.ps1 -Start
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [string]$Name = 'aces-rehearsal',
  [string]$Root = 'D:\HyperV\aces-rehearsal',
  [string]$Switch = 'HA-External',
  [string]$Iso,
  [string]$Cidata,
  [switch]$DvdFirst,
  [switch]$Start
)
$ErrorActionPreference = 'Stop'
$diskBytes = 80GB

$existing = Get-VM -Name $Name -ErrorAction SilentlyContinue
if ($existing) {
  Write-Host "VM $Name already exists (state $($existing.State)); nothing changed. Reset steps: rehearsal/README.md"
  return
}
if (-not $Iso) {
  $Iso = Get-ChildItem -Path (Join-Path $Root 'iso') -Filter 'omarchy-*.iso' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
}
if (-not $Cidata) {
  $Cidata = 'cidata.vhdx', 'cidata.iso' | ForEach-Object { Get-Item -LiteralPath (Join-Path $Root "cidata\$_") -ErrorAction SilentlyContinue } |
    Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
}
$missing = @()
if (-not $Iso -or -not (Test-Path -LiteralPath $Iso)) { $missing += 'the Omarchy ISO (get-iso.ps1)'; if (-not $Iso) { $Iso = Join-Path $Root 'iso\omarchy-<version>.iso' } }
if (-not $Cidata -or -not (Test-Path -LiteralPath $Cidata)) { $missing += 'the cidata drive (make-cidata.ps1)'; if (-not $Cidata) { $Cidata = Join-Path $Root 'cidata\cidata.iso' } }
if (-not (Get-VMSwitch -Name $Switch -ErrorAction SilentlyContinue)) { $missing += "the virtual switch $Switch" }
if ($missing) {
  if ($WhatIfPreference) { Write-Warning "missing, so a real run would stop here: $($missing -join '; ')" }
  else { throw "missing: $($missing -join '; ')" }
}

$vmDir = Join-Path $Root 'vm'
$vhd = Join-Path $vmDir "$Name.vhdx"
$cidataKind = if ($Cidata -like '*.iso') { 'second DVD drive' } else { 'second SCSI disk' }
$order = if ($DvdFirst) { 'DVD, then disk (reinstalls on every reboot while cidata is attached)' } else { 'disk, then DVD' }

if ($PSCmdlet.ShouldProcess($vhd, 'New-VHD 80 GiB dynamic, 512-byte logical sectors')) {
  New-Item -ItemType Directory -Force $vmDir | Out-Null
  if (Test-Path -LiteralPath $vhd) { Write-Warning "reusing the existing $vhd (delete it first for a fresh install)" }
  else { New-VHD -Path $vhd -SizeBytes $diskBytes -Dynamic -LogicalSectorSizeBytes 512 | Out-Null }
}
$plan = "New-VM gen 2, 4 vCPU, 8 GB static, switch $Switch, Secure Boot off, no automatic checkpoints; " +
  "ISO $Iso as DVD (SCSI 0:1), $Cidata as $cidataKind (SCSI 0:2); boot order $order"
if ($PSCmdlet.ShouldProcess($Name, $plan)) {
  New-VM -Name $Name -Generation 2 -MemoryStartupBytes 8GB -VHDPath $vhd -Path $vmDir -SwitchName $Switch | Out-Null
  try {
    Set-VMMemory -VMName $Name -DynamicMemoryEnabled $false -StartupBytes 8GB
    Set-VMProcessor -VMName $Name -Count 4
    Set-VMFirmware -VMName $Name -EnableSecureBoot Off
    Set-VM -Name $Name -AutomaticCheckpointsEnabled $false -CheckpointType Standard -AutomaticStartAction Nothing -AutomaticStopAction ShutDown
    Add-VMDvdDrive -VMName $Name -ControllerNumber 0 -ControllerLocation 1 -Path $Iso
    if ($Cidata -like '*.iso') { Add-VMDvdDrive -VMName $Name -ControllerNumber 0 -ControllerLocation 2 -Path $Cidata }
    else { Add-VMHardDiskDrive -VMName $Name -ControllerType SCSI -ControllerNumber 0 -ControllerLocation 2 -Path $Cidata }
    $hdd = Get-VMHardDiskDrive -VMName $Name -ControllerNumber 0 -ControllerLocation 0
    $dvd = Get-VMDvdDrive -VMName $Name -ControllerNumber 0 -ControllerLocation 1
    # -BootOrder replaces the whole list: PXE and the cidata drive drop out of it.
    if ($DvdFirst) { Set-VMFirmware -VMName $Name -BootOrder $dvd, $hdd } else { Set-VMFirmware -VMName $Name -BootOrder $hdd, $dvd }
  } catch {
    Remove-VM -Name $Name -Force   # a half-built VM would make the next run skip it; the VHDX is kept and reused
    throw
  }
  # Users report the synthetic display stays at 1024x768 without this (omarchy discussion #445); not fatal.
  try { Set-VMVideo -VMName $Name -HorizontalResolution 1920 -VerticalResolution 1080 -ResolutionType Single }
  catch { Write-Warning "Set-VMVideo failed ($($_.Exception.Message)); the console keeps the default resolution" }
  Get-VMFirmware -VMName $Name | Select-Object SecureBoot, @{ n = 'BootOrder'; e = { ($_.BootOrder | ForEach-Object { $_.BootType }) -join ', ' } } |
    Format-List | Out-String | Write-Host
}
if ($Start -and $PSCmdlet.ShouldProcess($Name, 'Start-VM')) { Start-VM -Name $Name }
Write-Host "next: Start-VM $Name; vmconnect.exe localhost $Name  (the install runs unattended and reboots into Omarchy)"
