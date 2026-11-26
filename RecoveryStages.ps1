function Invoke-RecoveryTool($Executable, [string[]]$Arguments, $Directory, $LogName) {
    # Every argument is a flag, simple value, or absolute file path without trailing backslashes.
    $commandLine = ($Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $stdin = Join-Path $Directory 'stdin.txt'
    if (!(Test-Path -LiteralPath $stdin)) { [IO.File]::WriteAllText($stdin, '') }
    $process = Start-Process -FilePath $Executable -ArgumentList $commandLine -WorkingDirectory $Directory -WindowStyle Hidden -PassThru -RedirectStandardInput $stdin -RedirectStandardOutput (Join-Path $Directory "$LogName.log") -RedirectStandardError (Join-Path $Directory "$LogName-errors.log")
    # Windows PowerShell 5.1 reports an empty ExitCode unless the handle is opened while the process runs.
    $null = $process.Handle
    try {
        if (!$process.WaitForExit($TimeoutMinutes * 60000)) { throw "$LogName timed out." }
        if ($process.ExitCode -ne 0) { throw "$LogName exited with code $($process.ExitCode)." }
    }
    finally {
        if (!$process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        $process.Dispose()
    }
}

function Assert-SafeArchive([string]$Path, [long]$MaxBytes = 40GB, [int]$MaxEntries = 500000) {
    # Rejects zip-slip paths and archives that expand beyond what a build could plausibly need.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Path)
    try {
        if ($archive.Entries.Count -gt $MaxEntries) { throw "unsafe archive: $($archive.Entries.Count) entries." }
        $total = 0L
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName
            if ($name -match '(^|[\\/])\.\.([\\/]|$)' -or [IO.Path]::IsPathRooted($name) -or $name -match '^[A-Za-z]:') {
                throw "unsafe archive: entry path '$name' leaves the extraction folder."
            }
            $total += $entry.Length
            if ($total -gt $MaxBytes) { throw 'unsafe archive: it expands past the size limit.' }
        }
    }
    finally { $archive.Dispose() }
}

function Invoke-RecoveryStage($Name, $Action) {
    Write-Host "Running $Name"
    try {
        $output = Invoke-WithRetry {
            $directory = Join-Path $job "$Name-$([guid]::NewGuid())"
            New-Item -ItemType Directory -Path $directory | Out-Null
            & $Action $directory
        }
        $result.stages[$Name] = @{ status = 'complete'; output = $output }
        return $output
    }
    catch {
        $result.stages[$Name] = @{ status = 'failed'; error = $_.Exception.Message }
        Write-Warning "$Name failed. $($_.Exception.Message)"
    }
}

function Get-BuildInfo($Source, $Extension) {
    $files = @(Get-ChildItem -LiteralPath $Source -Recurse -File -Force)
    $dex = @($files | Where-Object { $_.Name -match '^classes\d*\.dex$' })
    $native = @($files | Where-Object Name -In 'GameAssembly.dll','libil2cpp.so')
    $metadata = @($files | Where-Object Name -EQ 'global-metadata.dat')
    $apks = @($files | Where-Object Extension -EQ '.apk')
    $bundles = @($files | Where-Object { $_.Name -eq 'data.unity3d' -or $_.Extension -in '.bundle','.unity3d','.ab','.assetbundle' })
    $managed = @($files | Where-Object {
        $_.Extension -eq '.dll' -and $_.Directory.Name -eq 'Managed' -and
        # Engine and framework assemblies are not game code; decompiling them wastes time.
        $_.BaseName -notmatch '^(mscorlib|netstandard|System(\..+)?|Mono\..+|Microsoft\..+|UnityEngine(\..+)?|Unity\..+)$'
    })
    $platform = 'Asset'
    if ($dex.Count -or $Extension -in '.apk','.xapk','.obb' -or ($native | Where-Object Name -EQ 'libil2cpp.so')) {
        $platform = 'Android'
    }
    elseif (($native | Where-Object Name -EQ 'GameAssembly.dll') -or
        ($files | Where-Object Name -EQ 'UnityPlayer.dll') -or (Split-Path -Leaf $Source) -like '*_Data') {
        $platform = 'Windows'
    }
    elseif (Test-Path -LiteralPath $Source -PathType Container) {
        $platform = 'Unpacked data'
    }
    $backend = 'Unknown'
    if ($native.Count -or $metadata.Count) { $backend = 'IL2CPP' }
    elseif ($managed.Count) { $backend = 'Mono' }

    @{
        Platform = $platform
        Backend = $backend
        Dex = $dex
        Native = $native
        Metadata = $metadata
        Managed = $managed
        Apks = $apks
        Bundles = $bundles
    }
}

function Invoke-ReaRecovery($Build, $Apks, $Node, $Directory, [int]$MaxBundles = 25) {
    # Static, read-only REA checks (github.com/morluto/rea). REA never runs the input.
    $rea = Get-Rea $Node $Directory
    $checks = @()
    if ($Build.Backend -eq 'IL2CPP' -and $Build.Metadata.Count) {
        $metadata = $Build.Metadata | Sort-Object FullName | Select-Object -First 1
        $extra = @()
        if ($Build.Native.Count) {
            $binary = $Build.Native | Sort-Object @{Expression = { $_.Directory.Name -ne 'arm64-v8a' }}, FullName | Select-Object -First 1
            $extra = @('--binary', $binary.FullName, '--ghidra-seeds', (Join-Path $Directory 'il2cpp-ghidra-seeds.txt'))
        }
        $checks += ,@('il2cpp-metadata', 'inspect-il2cpp-metadata', $metadata.FullName, $extra)
    }
    $index = 0
    foreach ($assembly in $Build.Managed | Where-Object BaseName -Like 'Assembly-CSharp*') {
        $index++
        $checks += ,@("managed-members-$index", 'inspect-managed-members', $assembly.FullName, @())
    }
    $index = 0
    foreach ($apk in $Apks) {
        $index++
        $checks += ,@("apk-signature-$index", 'inspect-apk-signature', $apk.FullName, @())
    }
    $index = 0
    foreach ($bundle in $Build.Bundles | Sort-Object Length -Descending | Select-Object -First $MaxBundles) {
        $index++
        $checks += ,@("bundle-$index", 'inspect-artifact', $bundle.FullName, @('--integrity-policy', 'record-and-continue'))
    }
    if (!$checks.Count) { throw 'REA: nothing in this input for REA to inspect.' }

    $report = @()
    $failed = @()
    foreach ($check in $checks) {
        $name, $command, $target, $extra = $check
        $evidence = Join-Path $Directory "$name.evidence.json"
        try {
            Invoke-RecoveryTool $Node (@($rea, $command, $target) + $extra + @('--detail', 'summary', '--evidence_output', $evidence, '--json')) $Directory "rea-$name"
            $status = 'complete'
        }
        catch {
            $status = 'failed'
            $failed += "${name}: $($_.Exception.Message)"
        }
        $report += [pscustomobject]@{ check = $name; command = $command; target = $target; status = $status
                                      summary = (Join-Path $Directory "rea-$name.log"); evidence = $evidence }
    }
    ConvertTo-Json -InputObject $report -Depth 3 | Set-Content -LiteralPath (Join-Path $Directory 'rea-index.json') -Encoding UTF8
    if ($failed.Count) { throw ("REA: $($failed.Count) of $($checks.Count) checks failed. " + ($failed -join '; ')) }
    return $Directory
}

function Invoke-MonoRecovery($Assemblies, $Directory) {
    $ilspy = Get-ILSpy $Directory
    # The SDK is in the cache after setup. Use that host for the tool shim on later runs.
    $sdk = Get-ChildItem -LiteralPath $toolRoot -Directory -Filter 'dotnet-*' |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'dotnet.exe') -PathType Leaf } |
        Sort-Object { try { [version]($_.Name -replace '^dotnet-', '') } catch { [version]'0.0' } } -Descending |
        Select-Object -First 1
    if (!$sdk) { throw 'The .NET SDK is missing from the tool cache.' }
    $env:DOTNET_ROOT = $sdk.FullName
    $env:DOTNET_ROOT_X64 = $sdk.FullName
    $index = 0
    $failed = @()
    foreach ($assembly in $Assemblies) {
        $index++
        $output = Join-Path $Directory ('{0:D3}-{1}' -f $index, $assembly.BaseName)
        try {
            Invoke-RecoveryTool $ilspy @('-p','-o',$output,'-r',$assembly.DirectoryName,$assembly.FullName) $Directory "ilspy-$index"
        }
        catch { $failed += "$($assembly.Name): $($_.Exception.Message)" }
    }
    if ($failed.Count) { throw ($failed -join '; ') }
    return $Directory
}

function Invoke-Il2CppRecovery($Build, $Directory) {
    if (!$Build.Native.Count -or !$Build.Metadata.Count) { throw 'IL2CPP requires both a native binary and global-metadata.dat.' }
    $dumper = Install-ZipTool 'Il2CppDumper-6.7.46' 'https://github.com/Perfare/Il2CppDumper/releases/download/v6.7.46/Il2CppDumper-win-v6.7.46.zip' 'F5FC60DFC5C034C1FF3FC651514416EBD47658A63493207734537FD25FA2DEA2' 'Il2CppDumper.exe'
    # Il2CppDumper reads config from its own directory. Isolate it across runs.
    $privateTool = Join-Path $Directory 'tool'
    Copy-Item -LiteralPath (Split-Path -Parent $dumper) -Destination $privateTool -Recurse
    $configPath = Join-Path $privateTool 'config.json'
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $config.RequireAnyKey = $false
    $config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    $binary = $Build.Native | Sort-Object @{Expression = { $_.Directory.Name -ne 'arm64-v8a' }}, FullName | Select-Object -First 1
    $metadata = $Build.Metadata | Sort-Object FullName | Select-Object -First 1
    $output = Join-Path $Directory 'metadata'
    New-Item -ItemType Directory -Path $output | Out-Null
    Invoke-RecoveryTool (Join-Path $privateTool 'Il2CppDumper.exe') @($binary.FullName,$metadata.FullName,$output) $Directory 'il2cpp'
    if (!(Test-Path -LiteralPath (Join-Path $output 'dump.cs') -PathType Leaf)) { throw 'Il2CppDumper produced no dump.cs.' }
    return $output
}

function Invoke-AndroidRecovery($Dex, $Directory) {
    $java = Install-ZipTool 'Temurin-21.0.8+9' 'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.8%2B9/OpenJDK21U-jre_x64_windows_hotspot_21.0.8_9.zip' '238d74ec4ec9422d416fa98805ba375eecd8bc8f971bd0c61a21051a4fe42db8' 'jdk-21.0.8+9-jre\bin\java.exe'
    $launcher = Install-ZipTool 'jadx-1.5.6' 'https://github.com/skylot/jadx/releases/download/v1.5.6/jadx-1.5.6.zip' '545ea2be9c242511bc145755cf4bda2485ade42966e096f8b4d3da2a230e8974' 'bin\jadx.bat'
    $library = Join-Path (Split-Path -Parent (Split-Path -Parent $launcher)) 'lib'
    $output = Join-Path $Directory 'java'
    Invoke-RecoveryTool $java (@('-Xmx4g','-cp',(Join-Path $library '*'),'jadx.cli.JadxCLI','-d',$output) + @($Dex.FullName)) $Directory 'jadx'
    return $output
}

function Invoke-UnityVerification($Project, $Version, $Directory) {
    if ($Version -notmatch '^\d+\.\d+\.\d+[a-z]\d+$') { throw 'Could not identify a matching Unity Editor version.' }
    $editor = Join-Path $env:ProgramFiles "Unity\Hub\Editor\$Version\Editor\Unity.exe"
    if (!(Test-Path -LiteralPath $editor -PathType Leaf)) {
        throw "Unity $Version was not found in the default Hub install directory."
    }
    $log = Join-Path $Directory 'Unity.log'
    Invoke-RecoveryTool $editor @('-batchmode','-nographics','-quit','-projectPath',$Project,'-logFile',$log) $Directory 'unity'
    if (!(Test-Path -LiteralPath $log -PathType Leaf) -or
        (Select-String -LiteralPath $log -Pattern 'error CS\d+|Scripts have compiler errors|Aborting batchmode' -Quiet)) {
        throw 'Unity did not finish a clean batch import. Check Unity.log.'
    }
    return $log
}
