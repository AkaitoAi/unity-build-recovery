<#
  Drive AssetRipper 2.x headless (it is a local web server) so exports can be scripted.
  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File assetripper-headless.ps1 -InputPath "D:\Game\Game_Data" -OutputPath "D:\Recovery\AR_Out"
    ... -Mode PrimaryContent   # export only primary content instead of a full Unity project
  InputPath may be a file (bundle/assets/apk-extracted file) or a folder (the *_Data folder, or an extracted APK folder).
#>
param(
  [Parameter(Mandatory)][string]$InputPath,
  [Parameter(Mandatory)][string]$OutputPath,
  [ValidateSet('UnityProject','PrimaryContent')][string]$Mode = 'UnityProject',
  [int]$Port = 0,
  [int]$ExportTimeoutMin = 120,
  [string]$AssetRipperExe = ''
)
$ErrorActionPreference = 'Stop'
$exe  = if ($AssetRipperExe) { $AssetRipperExe } else { Join-Path $env:RE_TOOLS 'AssetRipper\AssetRipper.GUI.Free.exe' }
if (!(Test-Path -LiteralPath $exe)) { throw "AssetRipper not found at $exe" }
if ($Port -eq 0) {
  $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  $probe.Start(); $Port = $probe.LocalEndpoint.Port; $probe.Stop()
}
$base = "http://127.0.0.1:$Port"
$InputPath  = (Resolve-Path -LiteralPath $InputPath).Path
New-Item -ItemType Directory -Force $OutputPath | Out-Null
$OutputPath = (Resolve-Path -LiteralPath $OutputPath).Path

$p = Start-Process $exe -ArgumentList '--headless','--port',$Port -PassThru -WindowStyle Hidden
try {
  $ready = $false
  for ($i = 0; $i -lt 40 -and !$ready; $i++) {
    if ($p.HasExited) { throw "AssetRipper exited with code $($p.ExitCode)." }
    try { Invoke-WebRequest "$base/" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready = $true } catch { Start-Sleep -Milliseconds 500 }
  }
  if (!$ready) { throw 'AssetRipper did not start.' }
  Invoke-WebRequest "$base/Reset" -Method Post -UseBasicParsing -MaximumRedirection 5 | Out-Null
  $loadEp = if (Test-Path -LiteralPath $InputPath -PathType Container) { '/LoadFolder' } else { '/LoadFile' }
  Write-Host "Loading $InputPath via $loadEp"
  Invoke-WebRequest "$base$loadEp" -Method Post -Body @{ path = $InputPath } -UseBasicParsing -TimeoutSec 1200 -MaximumRedirection 5 | Out-Null
  Write-Host "Exporting ($Mode) to $OutputPath"
  Invoke-WebRequest "$base/Export/$Mode" -Method Post -Body @{ path = $OutputPath } -UseBasicParsing -TimeoutSec ($ExportTimeoutMin * 60) -MaximumRedirection 5 | Out-Null
  $n = (Get-ChildItem -LiteralPath $OutputPath -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
  Write-Host "Done. $n files in $OutputPath"
}
finally {
  if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force }
}
