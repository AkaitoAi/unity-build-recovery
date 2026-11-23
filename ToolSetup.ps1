function Install-ZipTool($Name, $Url, $Hash, $Entry, $Algorithm = 'SHA256') {
    $destination = Join-Path $toolRoot $Name
    $executable = Join-Path $destination $Entry
    if (Test-Path -LiteralPath $executable -PathType Leaf) { return $executable }

    Invoke-WithRetry {
        $staging = Join-Path $toolRoot ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Force -Path $staging | Out-Null
        $archive = Join-Path $staging 'tool.zip'
        $payload = Join-Path $staging 'payload'
        try {
            Invoke-WebRequest $Url -OutFile $archive -UseBasicParsing -TimeoutSec 900
            if ((Get-FileHash -LiteralPath $archive -Algorithm $Algorithm).Hash -ne $Hash) {
                throw "$Name download failed checksum verification."
            }
            Expand-Archive -LiteralPath $archive -DestinationPath $payload
            if (!(Test-Path -LiteralPath (Join-Path $payload $Entry) -PathType Leaf)) {
                # Some releases wrap every file in one top-level directory.
                $folders = @(Get-ChildItem -LiteralPath $payload -Directory)
                if ($folders.Count -ne 1 -or !(Test-Path -LiteralPath (Join-Path $folders[0].FullName $Entry) -PathType Leaf)) {
                    throw "$Name did not contain $Entry."
                }
                $payload = $folders[0].FullName
            }
            try { [IO.Directory]::Move($payload, $destination) }
            catch { if (!(Test-Path -LiteralPath $executable -PathType Leaf)) { throw } }
            return $executable
        }
        finally { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
}

function Get-AssetRipper {
    Install-ZipTool 'AssetRipper-2.0.0' 'https://github.com/AssetRipper/AssetRipper/releases/download/2.0.0/AssetRipper_win_x64.zip' '9a7ef0e7c5c3ea5b90b4e6d855e2d98d5f7ec8c3f9e26fccbc194c6a7b01baf7' 'AssetRipper.GUI.Free.exe'
}

function Get-DotNet {
    $metadata = Invoke-WithRetry {
        Invoke-RestMethod 'https://builds.dotnet.microsoft.com/dotnet/release-metadata/8.0/releases.json' -TimeoutSec 45
    }
    $sdk = $metadata.releases | ForEach-Object { $_.sdks } |
        Where-Object version -EQ $metadata.'latest-sdk' | Select-Object -First 1
    $asset = $sdk.files | Where-Object { $_.rid -eq 'win-x64' -and $_.name -like '*.zip' } | Select-Object -First 1
    if ($asset.hash -notmatch '^[a-fA-F0-9]{128}$') { throw 'Missing .NET SDK SHA-512 digest.' }
    Install-ZipTool "dotnet-$($sdk.version)" $asset.url $asset.hash 'dotnet.exe' 'SHA512'
}

function Get-ILSpy($AttemptRoot) {
    $destination = Join-Path $toolRoot 'ilspycmd-9.1.0.7988'
    $executable = Join-Path $destination 'ilspycmd.exe'
    if (Test-Path -LiteralPath $executable -PathType Leaf) { return $executable }

    $sdk = Get-DotNet
    $env:DOTNET_ROOT = Split-Path -Parent $sdk
    $env:DOTNET_ROOT_X64 = $env:DOTNET_ROOT
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
    $env:DOTNET_CLI_HOME = Join-Path $toolRoot 'dotnet-home'
    $env:NUGET_PACKAGES = Join-Path $toolRoot 'nuget-packages'
    $config = Join-Path $AttemptRoot 'NuGet.config'
    '<configuration><packageSources><clear/><add key="nuget.org" value="https://api.nuget.org/v3/index.json"/></packageSources></configuration>' |
        Set-Content -LiteralPath $config
    Invoke-WithRetry {
        $staging = Join-Path $toolRoot ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $staging | Out-Null
        try {
            Invoke-RecoveryTool $sdk @('tool','install','ilspycmd','--version','9.1.0.7988','--tool-path',$staging,'--configfile',$config) $AttemptRoot 'ilspy-install'
            if (!(Test-Path -LiteralPath (Join-Path $staging 'ilspycmd.exe') -PathType Leaf)) { throw 'ILSpy installation did not produce its launcher.' }
            try { [IO.Directory]::Move($staging, $destination) }
            catch { if (!(Test-Path -LiteralPath $executable -PathType Leaf)) { throw } }
        }
        finally { if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force } }
    }
    return $executable
}
