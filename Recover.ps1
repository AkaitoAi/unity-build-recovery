#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string[]]$InputPath,
    [string]$WorkRoot = (Join-Path $PSScriptRoot 'recovery-output'),
    [ValidateRange(1,10)][int]$Attempts = 3
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Invoke-WithRetry([scriptblock]$Action) {
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try { return (& $Action) }
        catch {
            if ($attempt -eq $Attempts) { throw }
            Write-Warning "Attempt $attempt failed. $($_.Exception.Message)"
            Start-Sleep -Seconds (5 * $attempt)
        }
    }
}

$toolRoot = Join-Path $PSScriptRoot '.tools'
$version = '2.0.0'
$toolDirectory = Join-Path $toolRoot "AssetRipper-$version"
$exe = Join-Path $toolDirectory 'AssetRipper.GUI.Free.exe'
if (!(Test-Path -LiteralPath $exe -PathType Leaf)) {
    Invoke-WithRetry {
        $staging = Join-Path $toolRoot ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Force -Path $staging | Out-Null
        $name = 'AssetRipper_win_x64.zip'
        $archive = Join-Path $staging $name
        $payload = Join-Path $staging 'payload'
        $release = Invoke-RestMethod "https://api.github.com/repos/AssetRipper/AssetRipper/releases/tags/$version" -TimeoutSec 45
        $asset = $release.assets | Where-Object name -EQ $name | Select-Object -First 1
        if ($asset.digest -notmatch '^sha256:([a-fA-F0-9]{64})$') { throw 'Missing upstream SHA-256 digest.' }
        $expected = $Matches[1]
        Invoke-WebRequest "https://github.com/AssetRipper/AssetRipper/releases/download/$version/$name" -OutFile $archive -UseBasicParsing -TimeoutSec 600
        if ((Get-FileHash -LiteralPath $archive).Hash -ne $expected) { throw 'Release checksum mismatch.' }
        Expand-Archive -LiteralPath $archive -DestinationPath $payload
        if (!(Test-Path -LiteralPath (Join-Path $payload 'AssetRipper.GUI.Free.exe') -PathType Leaf)) {
            throw 'The release did not contain the expected executable.'
        }
        try { [IO.Directory]::Move($payload, $toolDirectory) }
        catch { if (!(Test-Path -LiteralPath $exe -PathType Leaf)) { throw } }
        Remove-Item -LiteralPath $archive
    }
}

function Export-Project([string]$Source, [string]$AttemptRoot) {
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
        Invoke-WebRequest "$base/$endpoint" -Method Post -Body @{path = $Source} -UseBasicParsing -TimeoutSec 7200 | Out-Null
        Invoke-WebRequest "$base/Export/UnityProject" -Method Post -Body @{path = $output} -UseBasicParsing -TimeoutSec 7200 | Out-Null
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
    $result = [ordered]@{ input = $path; status = 'failed'; project = $null; error = $null }
    try {
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer) {
            $prefix = $item.FullName.TrimEnd('\') + '\'
            if ($run.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
                $exe.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
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
            Expand-Archive -LiteralPath $zip -DestinationPath $source
            if ($item.Extension -eq '.xapk') {
                $parts = @(Get-ChildItem -LiteralPath $source -File -Recurse | Where-Object Extension -In '.apk','.obb')
                foreach ($part in $parts) {
                    Copy-Item -LiteralPath $part.FullName -Destination $zip -Force
                    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $source ([guid]::NewGuid().ToString()))
                }
            }
            Remove-Item -LiteralPath $zip
        }

        $result.project = Invoke-WithRetry {
            $attemptRoot = Join-Path $job ('attempt-' + [guid]::NewGuid())
            New-Item -ItemType Directory -Path $attemptRoot | Out-Null
            Export-Project -Source $source -AttemptRoot $attemptRoot
        }
        $result.status = 'exported'
    }
    catch {
        $result.error = $_.Exception.Message
        Write-Warning "$($result.input): $($result.error)"
    }
    $results += [pscustomobject]$result
    ConvertTo-Json -InputObject $results -Depth 4 | Set-Content -LiteralPath (Join-Path $run 'recovery-report.json') -Encoding UTF8
}
if ($results.status -contains 'failed') { exit 1 }
