<#
.SYNOPSIS
  Save a PNG of the rehearsal VM's console without opening vmconnect.
.DESCRIPTION
  Asks Hyper-V's virtualization WMI service (root\virtualization\v2, Msvm_VirtualSystemManagementService
  .GetVirtualSystemThumbnailImage) for a thumbnail of the VM's display and writes it as a PNG. The service returns
  RGB565 pixels (2 bytes each, row-major, WidthPixels x HeightPixels); the script copies them into a 16-bit bitmap
  and saves it. Works unelevated for members of Hyper-V Administrators, on a running VM only. This is how the
  2026-10-09 run watched the unattended install ("Installing Omarchy" at 18 s, the login screen under 2 min).
.EXAMPLE
  .\vm-shot.ps1                       # D:\HyperV\aces-rehearsal\shots\aces-rehearsal-<timestamp>.png
  .\vm-shot.ps1 -Width 1920 -Height 1080 -Out C:\tmp\console.png
#>
param(
  [string]$Name = 'aces-rehearsal',
  [string]$Root = 'D:\HyperV\aces-rehearsal',
  [string]$Out,
  [int]$Width = 1024,
  [int]$Height = 576
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
if (-not $Out) {
  $dir = Join-Path $Root 'shots'
  New-Item -ItemType Directory -Force $dir | Out-Null
  $Out = Join-Path $dir ('{0}-{1:yyyyMMdd-HHmmss}.png' -f $Name, (Get-Date))
}
$vm = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem -Filter "ElementName='$Name'"
if (-not $vm) { throw "no VM named $Name" }
$vssd = Get-CimAssociatedInstance -InputObject $vm -ResultClassName Msvm_VirtualSystemSettingData |
  Where-Object { $_.VirtualSystemType -eq 'Microsoft:Hyper-V:System:Realized' } | Select-Object -First 1
$svc = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_VirtualSystemManagementService
$r = Invoke-CimMethod -InputObject $svc -MethodName GetVirtualSystemThumbnailImage -Arguments @{
  TargetSystem = $vssd; WidthPixels = [uint16]$Width; HeightPixels = [uint16]$Height }
if ($r.ReturnValue -ne 0) { throw "GetVirtualSystemThumbnailImage returned $($r.ReturnValue) (is the VM running?)" }
$bytes = [byte[]]$r.ImageData
if (-not $bytes -or $bytes.Length -lt $Width * $Height * 2) { throw "short image: $($bytes.Length) bytes for ${Width}x${Height}" }
$bmp = New-Object System.Drawing.Bitmap $Width, $Height, ([System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
$rect = New-Object System.Drawing.Rectangle 0, 0, $Width, $Height
$data = $bmp.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly, $bmp.PixelFormat)
try {
  $rowBytes = $Width * 2
  for ($y = 0; $y -lt $Height; $y++) {
    [System.Runtime.InteropServices.Marshal]::Copy($bytes, $y * $rowBytes, [IntPtr]::Add($data.Scan0, $y * $data.Stride), $rowBytes)
  }
} finally { $bmp.UnlockBits($data) }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Host "saved $Out (${Width}x${Height}; VM $Name state $($vm.EnabledState))"
