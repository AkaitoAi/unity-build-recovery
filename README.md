# Unity Build Recovery

An unattended Windows x64 CLI for exporting Unity project data from your own builds or builds you have permission to recover. Run in PowerShell 5.1 or newer.

## Quick start

Open PowerShell and clone the repository with Git.

```powershell
git clone https://github.com/AkaitoAi/unity-build-recovery.git
Set-Location .\unity-build-recovery
```

If you already cloned it, open that folder and run `git pull --ff-only`.

Replace the example paths with your own build and output paths. Choose the command that matches your input.

```powershell
# Windows player build
.\Recover.ps1 -InputPath 'D:\Builds\MyGame' -WorkRoot 'D:\Recovery'

# Android APK
.\Recover.ps1 -InputPath 'D:\Builds\MyGame.apk' -WorkRoot 'D:\Recovery'

# Multiple inputs in a single run
.\Recover.ps1 -InputPath @('D:\Builds\MyGame', 'D:\Builds\MyGame.apk') -WorkRoot 'D:\Recovery'
```

Open `recovery-report.json` in the `run-…` folder printed by the script. The `project` field gives the path to the exported Unity project. The `stages` field lists the output path or error for each stage.

## Supported inputs and tools

Pass a Windows player build directory, a Unity player data directory, a Unity asset or bundle, or a ZIP-based APK, XAPK, OBB, or ZIP. For Windows IL2CPP, pass the whole build directory so recovery can find both GameAssembly.dll and the player data. Keep the work directory outside input folders.

The script copies and hashes each input, expands supported archives, and detects Android DEX, managed assemblies, and IL2CPP binaries and metadata. It downloads tools into the local .tools cache as their stages become applicable.

| Detected input | Automatic stages |
| --- | --- |
| Any supported input | AssetRipper project export and AssetStudioMod asset inventory |
| Mono managed assemblies | .NET 8 SDK and ilspycmd for C# project output per assembly |
| IL2CPP | Il2CppDumper for metadata, type/method layout, and dummy assemblies. When Android provides multiple ABIs, the runner prefers ARM64. |
| Android DEX | JADX and Eclipse Temurin Java for separate Java source output |

The runner checks downloaded ZIPs against published checksums. The .NET SDK comes from Microsoft's .NET 8 release feed and ilspycmd comes from NuGet. The runner never launches the input build.

Each applicable stage gets up to three attempts by default. Change the limit with -Attempts and the processing timeout with -TimeoutMinutes. The runner keeps attempt logs and partial output, continues with independent stages and later inputs, and records build detection, the exported Unity version, stage results, and paths in recovery-report.json. A partial or failed input sets exit code 1 after the batch finishes.

For a trusted build, add -VerifyInUnity to run a batch import using an already installed, licensed matching Unity Editor in the default Unity Hub directory. Unity can load recovered code during import. The report records a failure if the matching editor is unavailable or batch verification fails. Install and activate Unity separately.

Decompiled source and metadata stay beside the export for review. Native IL2CPP method bodies, merging recovered code into scenes and prefabs, gameplay reconstruction, and gameplay testing require separate work.

## Optional gameplay reconstruction with Craft Anything

Use this after recovery when you have an exported Unity project and a specific missing system to implement. Install the [Craft Anything CLI](https://github.com/storytold/craft-anything#get-started), sign in to its supported coding agent, and run `craft-anything-cli doctor`. The step can prompt for decisions while rebuilding.

```powershell
.\Rebuild-WithCraft.ps1 -ReportPath 'D:\Recovery\run-EXAMPLE\recovery-report.json' -InputNumber 1 -Task 'Implement a pause menu with Resume and Quit. It should toggle with Escape and restore the previous time scale when closed.'
```

Choose the report entry with `-InputNumber` (one-based, defaults to 1). A custom CLI path can be supplied with `-CraftCli`. The helper copies `Assets`, `Packages`, and `ProjectSettings` into a fresh `craft-unity-*` folder next to the report and asks Craft Anything to edit that copy. The project files and prompt can be processed by your configured coding-agent provider.

Review the generated changes in the matching Unity Editor and test the requested behavior in Play Mode before bringing changes back into the recovered project. Craft Anything is an early-alpha tool and generated gameplay code needs review.

## Aether Recon Studio (desktop UI, experimental)

The `studio/` folder holds a separate Windows tool: a local Python UI for guided recovery of APK/XAPK and Windows Unity builds. It has an input scanner, a recovery pipeline (unpack, AssetRipper, Il2CppDumper/Cpp2IL, JADX), Ghidra symbol import, a script classifier that picks gameplay scripts, a tool-update checker and a scaffold generator that builds a Unity project from the results. It does not call `Recover.ps1`, share its `.tools` cache or use its report format. Use it only on builds you own or have permission to recover, like the CLI.

Requirements: Windows x64, Python 3.10+ with Tk (the python.org installer includes it), and Edge or Chrome for the window. No pip packages are needed.

Tool root: the studio looks for tools in `RE_TOOLS`, else the folder above `studio/` if it contains `7-Zip`, else `<repo>\.tools`. Folder names it expects there:

| Folder | Needed for |
| --- | --- |
| `7-Zip\7z.exe` | Unpacking (required) |
| `AssetRipper\AssetRipper.GUI.Free.exe` | Asset export |
| `AssetStudioModCLI\AssetStudioModCLI_net9_win64\` | Asset inventory |
| `Il2CppDumper\`, `Cpp2IL\` | IL2CPP recovery |
| `jadx\bin\jadx.bat` and a JDK 21+ under `C:\Program Files\Eclipse Adoptium` | Java source |
| `ghidra\`, `android-sdk\ndk\<version>` | Symbol import (optional) |
| `ILSpy\`, `llvm\`, `ffmpeg\` | Optional |

The Tools tab shows PASS or FAIL for each. `studio\env.ps1` is only for your own PowerShell sessions: dot-source it to put these tools on PATH. The launcher does not need it.

```powershell
.\studio\Run_Recovery_Studio.bat
```

The launcher starts the server on `127.0.0.1:5899` and opens it in a plain app window. It hides the console, so if nothing appears, run `python studio\desktop_shell.py` in a terminal to see errors. Logs go to `studio\studio_server.log`.

Unity editor paths and a default Unity version (2022.3.62f3, 6000.3.21f1) are set in `unity_playable_scaffolder.py` and `server.py`. Edit them if your editors differ. The server rejects requests from other web pages, and tool downloads are installed only when the release publishes a SHA-256 digest that matches. It has been tested only on the author's machine.

This repository's code is MIT-licensed. Downloaded tools retain their upstream licenses. Keep builds and recovery outputs private.
