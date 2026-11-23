# Unity Build Recovery

Tools for exporting Unity project data from builds you own or have permission to recover. It runs on Windows x64.

This repository has two parts:

| | Aether Recon Studio (`studio/`) | PowerShell CLI (`Recover.ps1`) |
| --- | --- | --- |
| What it is | A local desktop-window UI | A batch command line |
| Inputs | APK, XAPK, ZIP | Windows build folders, APK, XAPK, OBB, ZIP, loose Unity assets |
| Tools | You install them (see [Tool folders](#tool-folders)) | Downloads and checksums them into `.tools` |
| Output | A work folder per game, a Markdown report, a Unity project folder | A `run-…` folder with `recovery-report.json` |
| Pick it when | You want to inspect, pick scripts and rebuild a project step by step | You want an unattended run, a Windows build folder, or several inputs at once |

The two do not share code, tool folders or report formats. The earlier CLI-only version of this repository is kept on the [`legacy-cli`](https://github.com/AkaitoAi/unity-build-recovery/tree/legacy-cli) branch.

## Limitations and risks

- **Not tested broadly.** Both tools have been used only on the author's machine. No automated tests exist.
- **Recovered projects are starting points.** Native IL2CPP method bodies are not recovered, only type and method layout. Merging recovered code into scenes and prefabs, and gameplay testing, are your work. The Studio's scaffold is not compiled or verified, and its placeholder SDK stubs (ads, Firebase, Facebook, GameAnalytics) return fixed values, so behavior differs from the real SDKs.
- **Il2CppDumper 6.7.46, which the CLI uses, cannot read the newest IL2CPP metadata versions.** The IL2CPP stage then fails. The Studio also tries Cpp2IL, which handles more builds.
- **Untrusted input.** Treat every input build as untrusted. Neither tool runs the input's executables, but AssetRipper, JADX and the decompilers parse it. With `-VerifyInUnity`, Unity can run recovered scripts, including editor scripts, during import. Use that flag only for builds you trust.
- **Third-party tools** keep their own licenses. Keep builds and recovery outputs private.
- **Use only on builds you own or may legally recover.** You are responsible for that decision.

## Aether Recon Studio

A Python UI that scans an APK, XAPK or ZIP, unpacks it, runs the recovery tools in order, lets you pick which recovered scripts to keep, and builds a Unity project folder. Folders are not supported: for a Windows build folder use `Recover.ps1`.

### Requirements

- Windows x64.
- Python 3.10 or later with Tk (the python.org installer includes it). No pip packages are needed.
- Microsoft Edge or Google Chrome. The UI opens in an app-style window of whichever is installed.
- The recovery tools, in the folders listed below.

### Tool folders

The studio finds its tools in this order: the `RE_TOOLS` environment variable, then the folder above `studio/` if it contains `7-Zip`, then `<repo>\.tools`. Create these folders there (download links are on each project's releases page):

| Folder | Tool | Needed for |
| --- | --- | --- |
| `7-Zip\7z.exe` | 7-Zip | Unpacking (required) |
| `AssetRipper\AssetRipper.GUI.Free.exe` | AssetRipper 2.x | Assets, scenes, materials |
| `Il2CppDumper\` | Il2CppDumper | IL2CPP type layout |
| `Cpp2IL\` | Cpp2IL | IL2CPP fallback and ARM64 dumps |
| `jadx\bin\jadx.bat` | JADX, plus a JDK 21 or later under `C:\Program Files\Eclipse Adoptium` | Java source from Android DEX |
| `ghidra\`, `android-sdk\ndk\<version>` | Ghidra, Android NDK | Symbol import (optional) |

The Tools tab shows PASS or FAIL for each folder. The CLI's `.tools` folder uses versioned names such as `AssetRipper-2.0.0`, which the studio does not look for. To use the CLI's tools anyway, point `RE_TOOLS` at a folder that has the plain names above.

`studio\env.ps1` is optional. Dot-source it in your own PowerShell session if you want these tools on PATH. The launcher does not use it.

### Launch

```powershell
.\studio\Run_Recovery_Studio.bat
```

The launcher starts a server on `127.0.0.1:5899` and opens it in an Edge or Chrome app window. It hides the console. If nothing appears, run `python studio\desktop_shell.py` in a terminal for launcher errors. Server output goes to `studio\studio_server.log`. The studio treats any program already listening on port 5899 as itself, so close that program first if the window shows something else.

### What it does, in order

1. Intake: copies the package to `00_original`, hashes it.
2. Unpack: 7-Zip into `01_extracted`, including nested APKs in an XAPK.
3. Asset rip: AssetRipper headless export into `02_assetripper`.
4. Code recovery: Il2CppDumper or Cpp2IL for IL2CPP into `03_il2cpp`, JADX for Java into `04_jadx`, and the readable C# into `03_scripts`.
5. Project folder: copies the ripped assets and project settings into `05_project`, adds a package manifest and an assembly definition, and adds placeholder SDK stubs only for SDKs the game does not already ship.

A stage counts as failed when it writes no files. The report lists each stage's result.

### Security notes

The server accepts only requests that come from its own page: it checks the Host and Origin headers and a token generated at each launch, and it limits file reads and script copies to the work folder. Tool installs from the Tools tab must match the SHA-256 digest published with the GitHub release, and zip paths are checked before extraction. REA, which installs through npm, has no digest check; the install runs with `--ignore-scripts`. A digest from the same release page detects a corrupted download, not a compromised release.

### Configuration

Unity editors are found under `C:\Program Files\Unity\Hub\Editor`. The studio picks the editor that matches the game's Unity version exactly, then the same major.minor line, and warns when it has to use another one, because Unity upgrades the project on open.

## PowerShell CLI

Run it in PowerShell 5.1 or later. It needs no Python.

### Quick start

Clone the repository (Git must be installed):

```powershell
git clone https://github.com/AkaitoAi/unity-build-recovery.git
Set-Location .\unity-build-recovery
```

To update an existing clone, run `git pull --ff-only`.

If PowerShell blocks the script, start it with `powershell -ExecutionPolicy Bypass -File .\Recover.ps1 …` or unblock it with `Unblock-File`.

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

### Parameters

| Parameter | Meaning | Default |
| --- | --- | --- |
| `-InputPath` | One or more builds. A Windows IL2CPP build must be the whole folder so both `GameAssembly.dll` and the player data are found. | required |
| `-WorkRoot` | Where `run-…` folders go. Keep it outside the input folders. | `recovery-output` beside the script |
| `-Attempts` | Tries per stage, 1 to 10. Failures that repeat identically (timeouts, unsafe archives, missing prerequisites) are not retried. | 3 |
| `-TimeoutMinutes` | Time limit per tool run, 1 to 240. | 120 |
| `-VerifyInUnity` | After export, imports the project in the matching Unity Editor. Needs that exact editor installed and licensed under the default Unity Hub folder. | off |

The exit code is 0 when every input finishes and 1 when any input is partial or failed, after the whole batch has run.

### What it does

The script copies and hashes each input, rejects archives with unsafe paths or an oversized expansion, expands supported archives, and detects Android DEX, managed assemblies, and IL2CPP binaries and metadata. It downloads tools into the local `.tools` cache when a detected input needs them (the first run needs internet access).

| Detected input | Automatic stages |
| --- | --- |
| Any supported input | AssetRipper project export and AssetStudioMod asset inventory |
| Mono managed assemblies | .NET 8 SDK and ilspycmd decompile each game assembly to a C# project. Engine and framework assemblies are skipped. |
| IL2CPP | Il2CppDumper for metadata, type and method layout, and dummy assemblies. For Android builds with several CPU architectures, it uses the ARM64 libraries. |
| Android DEX | JADX and Eclipse Temurin Java, with separate Java source output |

ZIP downloads are checked against published checksums. The .NET SDK comes from Microsoft's .NET 8 release feed, and ilspycmd comes from NuGet without a checksum. The runner never launches the input's executables.

The runner keeps attempt logs and partial output, continues with independent stages and later inputs, and records the build detection, the exported Unity version, stage results and paths in `recovery-report.json`.

## Optional gameplay reconstruction with Craft Anything

Use this after recovery, when you have an exported Unity project and one missing system to implement. Install the [Craft Anything CLI](https://github.com/storytold/craft-anything#get-started), sign in to its supported coding agent, and run `craft-anything-cli doctor`. The step can prompt for decisions while it rebuilds, so it is not unattended.

```powershell
.\Rebuild-WithCraft.ps1 -ReportPath 'D:\Recovery\run-EXAMPLE\recovery-report.json' -InputNumber 1 -Task 'Implement a pause menu with Resume and Quit. It should toggle with Escape and restore the previous time scale when closed.'
```

Replace `run-EXAMPLE` with your run folder. `-InputNumber` is the one-based position of the input in the report (default 1). Use `-CraftCli` to set a custom CLI path. The helper copies `Assets`, `Packages` and `ProjectSettings` into a new `craft-unity-*` folder next to the report and asks Craft Anything to edit that copy. Your project files and prompt are sent to your coding-agent provider, so do not use it on a build you must keep private.

Review the generated changes in the matching Unity Editor and test the requested behavior in Play Mode before bringing changes back into the recovered project. Craft Anything's own repository describes it as early alpha.

## License

The code in this repository is MIT-licensed. Downloaded tools keep their upstream licenses.
