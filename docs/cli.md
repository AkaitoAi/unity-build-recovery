# PowerShell CLI

The original command-line tool. It does not share code, tool folders or report formats with the studio.

## Quick start

Clone the repository (Git must be installed):

```powershell
git clone https://github.com/AkaitoAi/unity-build-recovery.git
Set-Location .\unity-build-recovery
```

To update an existing clone, run `git pull --ff-only`.

If PowerShell blocks the script, start it with `powershell -ExecutionPolicy Bypass -File .\Recover.ps1 …` or unblock all four scripts (`Recover.ps1`, `ToolSetup.ps1`, `RecoveryStages.ps1`, `Rebuild-WithCraft.ps1`) with `Unblock-File` if you downloaded them as a ZIP.

Replace the example paths with your own.

```powershell
# Windows player build folder
.\Recover.ps1 -InputPath 'D:\Builds\MyGame' -WorkRoot 'D:\Recovery'

# Android APK
.\Recover.ps1 -InputPath 'D:\Builds\MyGame.apk' -WorkRoot 'D:\Recovery'

# Several inputs in one run
.\Recover.ps1 -InputPath @('D:\Builds\MyGame', 'D:\Builds\MyGame.apk') -WorkRoot 'D:\Recovery'
```

The script prints the `run-…` folder it created under `-WorkRoot`. Open `recovery-report.json` there. Each input has a `project` path to the exported Unity project and a `stages` object with the output path or error of each stage.

## Parameters

| Parameter | Meaning | Default |
| --- | --- | --- |
| `-InputPath` | One or more builds. A Windows IL2CPP build must be the whole folder so both `GameAssembly.dll` and the player data are found. | required |
| `-WorkRoot` | Where `run-…` folders go. Keep it outside the input folders. | `recovery-output` beside the script |
| `-Attempts` | Tries per stage, 1 to 10. Failures that repeat identically (timeouts, unsafe archives, missing prerequisites) are not retried. | 3 |
| `-TimeoutMinutes` | Time limit per tool run, 1 to 240. | 120 |
| `-VerifyInUnity` | After export, imports the project in the matching Unity Editor. Needs that exact editor installed and licensed under the default Unity Hub folder. | off |

The exit code is 0 when every input finishes and 1 when any input is partial or failed, after the whole batch has run.

## What it does

The script copies and hashes each input, rejects archives with unsafe paths or an oversized expansion, expands supported archives, and detects Android DEX, managed assemblies, and IL2CPP binaries and metadata. It downloads tools into the local `.tools` cache when a detected input needs them (the first run needs internet access).

| Detected input | Automatic stages |
| --- | --- |
| Any supported input | AssetRipper project export and AssetStudioMod asset inventory |
| Mono managed assemblies | .NET 8 SDK and ilspycmd decompile each game assembly to a C# project. Engine and framework assemblies are skipped. |
| IL2CPP | Il2CppDumper for metadata, type and method layout, and dummy assemblies. For Android builds with several CPU architectures, it uses the ARM64 libraries. |
| Android DEX | JADX and Eclipse Temurin Java, with separate Java source output |

ZIP downloads are checked against published checksums. The .NET SDK comes from Microsoft's .NET 8 release feed, and ilspycmd comes from NuGet without a checksum. The runner never launches the input's executables.

The runner keeps attempt logs and partial output, continues with independent stages and later inputs, and records the build detection, the exported Unity version, stage results and paths in `recovery-report.json`.
