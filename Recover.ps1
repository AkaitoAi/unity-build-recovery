#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string[]]$InputPath,
    [string]$WorkRoot = (Join-Path $PSScriptRoot 'recovery-output'),
    [ValidateRange(1,10)][int]$Attempts = 3,
    [ValidateRange(1,240)][int]$TimeoutMinutes = 120,
    [switch]$VerifyInUnity
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Invoke-WithRetry([scriptblock]$Operation) {
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try { return (& $Operation) }
        catch {
            if ($attempt -eq $Attempts) { throw }
            # These failures repeat identically, so retrying only multiplies the wait.
            if ($_.Exception.Message -match '^[\w-]+ timed out\.$|requires both|produced no dump|Could not identify|was not found in the default|contains a symbolic link|checksum verification|unsafe archive') { throw }
            Write-Warning "Attempt $attempt failed. $($_.Exception.Message)"
            Start-Sleep -Seconds (5 * $attempt)
        }
    }
}

$toolRoot = Join-Path $PSScriptRoot '.tools'
. (Join-Path $PSScriptRoot 'ToolSetup.ps1')
. (Join-Path $PSScriptRoot 'RecoveryStages.ps1')

function Export-Project([string]$Source, [string]$AttemptRoot, [string]$exe) {
    $socket = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $socket.Start()
    try { $port = $socket.LocalEndpoint.Port }
    finally { $socket.Stop() }
    $base = "http://127.0.0.1:$port"
    $output = Join-Path $AttemptRoot 'export'
    New-Item -ItemType Directory -Path $output | Out-Null
    $start = @{
        FilePath = $exe
        ArgumentList = '--headless','--port',$port
        WorkingDirectory = $AttemptRoot
        RedirectStandardOutput = (Join-Path $AttemptRoot 'assetripper.log')
        RedirectStandardError = (Join-Path $AttemptRoot 'assetripper-errors.log')
        WindowStyle = 'Hidden'
        PassThru = $true
    }
    $server = Start-Process @start
    try {
        $ready = $false
        for ($poll = 0; $poll -lt 40; $poll++) {
            if ($server.HasExited) { throw "AssetRipper exited with code $($server.ExitCode)." }
            try {
                Invoke-WebRequest "$base/openapi.json" -UseBasicParsing -TimeoutSec 2 | Out-Null
                $ready = $true
                break
            }
            catch { Start-Sleep -Milliseconds 500 }
        }
        if (!$ready) { throw 'AssetRipper did not start.' }
        $endpoint = if (Test-Path -LiteralPath $Source -PathType Container) { 'LoadFolder' } else { 'LoadFile' }
        Invoke-WebRequest "$base/$endpoint" -Method Post -Body @{path = $Source} -UseBasicParsing -TimeoutSec ($TimeoutMinutes * 60) | Out-Null
        Invoke-WebRequest "$base/Export/UnityProject" -Method Post -Body @{path = $output} -UseBasicParsing -TimeoutSec ($TimeoutMinutes * 60) | Out-Null
        $project = Join-Path $output 'ExportedProject'
        if (!(Test-Path -LiteralPath (Join-Path $project 'ProjectSettings\ProjectVersion.txt') -PathType Leaf) -or
            !(Test-Path -LiteralPath (Join-Path $project 'Assets') -PathType Container)) {
            throw 'The export did not produce a Unity project. Check the attempt logs.'
        }
        return $project
    }
    finally {
        if (!$server.HasExited) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
        $server.Dispose()
    }
}

$WorkRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($WorkRoot)
$run = Join-Path $WorkRoot ('run-' + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $run | Out-Null
Write-Host "Run folder: $run"
$results = @()
foreach ($path in $InputPath) {
    $job = Join-Path $run ('{0:D3}' -f ($results.Count + 1))
    New-Item -ItemType Directory -Path $job | Out-Null
    $result = [ordered]@{ input = $path; status = 'failed'; project = $null; error = $null; build = $null; stages = [ordered]@{} }
    try {
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer) {
            $prefix = $item.FullName.TrimEnd('\') + '\'
            if ($run.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
                $toolRoot.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Use input directories outside the tool cache and work directories.'
            }
        }
        $entries = @($item)
        if ($item.PSIsContainer) { $entries += @(Get-ChildItem -LiteralPath $item.FullName -Recurse -Force) }
        if ($entries | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
            throw 'The input contains a symbolic link or junction.'
        }

        $snapshot = Join-Path $job 'original'
        New-Item -ItemType Directory -Path $snapshot | Out-Null
        Copy-Item -LiteralPath $item.FullName -Destination $snapshot -Recurse -Force
        $source = Join-Path $snapshot $item.Name
        Get-ChildItem -LiteralPath $snapshot -Recurse -File -Force | ForEach-Object {
            [pscustomobject]@{ Path = $_.FullName; Bytes = $_.Length; SHA256 = (Get-FileHash -LiteralPath $_.FullName).Hash }
        } | Export-Csv -LiteralPath (Join-Path $job 'inventory.csv') -NoTypeInformation -Encoding UTF8

        if (!$item.PSIsContainer -and $item.Extension -in '.apk','.xapk','.obb','.zip') {
            $zip = Join-Path $job 'input.zip'
            Copy-Item -LiteralPath $source -Destination $zip
            $source = Join-Path $job 'extracted'
            Assert-SafeArchive $zip
            Expand-Archive -LiteralPath $zip -DestinationPath $source
            if ($item.Extension -eq '.xapk') {
                $parts = @(Get-ChildItem -LiteralPath $source -File -Recurse | Where-Object Extension -In '.apk','.obb')
                foreach ($part in $parts) {
                    Copy-Item -LiteralPath $part.FullName -Destination $zip -Force
                    Assert-SafeArchive $zip
                    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $source ([guid]::NewGuid().ToString()))
                }
            }
            Remove-Item -LiteralPath $zip
        }

        $build = Get-BuildInfo $source $item.Extension
        $result.build = @{
            platform = $build.Platform
            backend = $build.Backend
            managedAssemblies = $build.Managed.Count
            dexFiles = $build.Dex.Count
            nativeBinaries = @($build.Native | ForEach-Object FullName)
            metadataFiles = $build.Metadata.Count
        }
        Write-Host "$($item.Name): $($build.Platform), $($build.Backend)"
        $result.project = Invoke-RecoveryStage 'asset-export' {
            param($directory)
            Export-Project $source $directory (Get-AssetRipper)
        }
        $null = Invoke-RecoveryStage 'asset-inventory' {
            param($directory)
            $studio = Install-ZipTool 'AssetStudioMod-0.19.0' 'https://github.com/aelurum/AssetStudioMod/releases/download/v0.19.0/AssetStudioModCLI_net472_win32_64.zip' 'cad5b9e2084dda85007012f94c6d3ad1944ba153796c4e80027792d0ba7be285' 'AssetStudioModCLI.exe'
            Invoke-RecoveryTool $studio @($source,'-m','info') $directory 'assetstudio'
            Join-Path $directory 'assetstudio.log'
        }
        if ($build.Backend -eq 'Mono') {
            $null = Invoke-RecoveryStage 'mono' { param($directory) Invoke-MonoRecovery $build.Managed $directory }
        }
        if ($build.Backend -eq 'IL2CPP') {
            $null = Invoke-RecoveryStage 'il2cpp' { param($directory) Invoke-Il2CppRecovery $build $directory }
        }
        if ($build.Dex.Count) {
            $null = Invoke-RecoveryStage 'android' { param($directory) Invoke-AndroidRecovery $build.Dex $directory }
        }
        if ($result.project) {
            $versionFile = Join-Path $result.project 'ProjectSettings\ProjectVersion.txt'
            $match = Select-String -LiteralPath $versionFile -Pattern '^m_EditorVersion:\s*(\S+)' | Select-Object -First 1
            if ($match) { $result.build.unityVersion = $match.Matches[0].Groups[1].Value }
            if ($VerifyInUnity) {
                $null = Invoke-RecoveryStage 'unity-import' {
                    param($directory)
                    Invoke-UnityVerification $result.project $result.build.unityVersion $directory
                }
            }
            $result.status = 'exported'
        }
        if ($result.project -and $result.stages.Values.status -contains 'failed') { $result.status = 'partial' }
    }
    catch {
        $result.error = $_.Exception.Message
        Write-Warning "$($result.input): $($result.error)"
    }
    $results += [pscustomobject]$result
    ConvertTo-Json -InputObject $results -Depth 4 | Set-Content -LiteralPath (Join-Path $run 'recovery-report.json') -Encoding UTF8
}
if ($results.status -contains 'failed' -or $results.status -contains 'partial') { exit 1 }
