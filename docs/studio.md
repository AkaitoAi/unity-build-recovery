# Aether Recon Studio

Reference for the studio. The short version is in the [README](../README.md).

## Supported engines

| Engine | Input | What you get back | Tool |
| --- | --- | --- | --- |
| Unity (Mono and IL2CPP) | APK, XAPK, ZIP | Assets, scenes, C# for Mono; type and method layout (not method bodies) for IL2CPP; a Unity project folder to finish by hand | AssetRipper, ILSpyCmd, Il2CppDumper, Cpp2IL, JADX |
| Godot | APK, PCK, or an EXE with an embedded PCK | A Godot project folder (`project.godot`, scenes, resources, scripts decompiled where the version is supported) | GDRE Tools |

Other engines are not recovered yet. The studio detects Godot by a `.pck` file, `libgodot` or an embedded `GDPC` footer. An encrypted Godot game needs its encryption key, and the studio does not take a key yet, so it reports the failure and stops.

**Missing tools.** The studio never downloads anything on its own. A Unity IL2CPP game needs either Il2CppDumper or Cpp2IL. If a game needs a tool that is not installed, the recovery stops before it creates anything and shows the tool, version, size, download address and SHA-256. Press OK to let the studio download exactly that version, check the SHA-256 and install it (if a newer release appears before the install starts, the install is refused and you are asked again); press Cancel and nothing is downloaded. Tools whose release publishes no SHA-256 (for example Il2CppDumper) are not downloaded; you install them by hand. 7-Zip is always installed by hand.

A Python UI that scans an APK, XAPK or ZIP, unpacks it, runs the recovery tools in order, lets you pick which recovered scripts to keep, and builds a Unity project folder. Folders are not supported: for a Windows build folder use `Recover.ps1`.

## Requirements

- Windows x64.
- Python 3.10 or later with Tk (the python.org installer includes it). No pip packages are needed. The launcher uses `pyenv\Scripts\pythonw.exe` in the folder above `studio\` if present, then `pythonw` on PATH, then the `pyw` launcher.
- Microsoft Edge or Google Chrome. The UI opens in an app-style window of whichever is installed.
- The recovery tools, in the folders listed below.

## Tool folders

The Tools tab shows ILSpyCmd separately from the ILSpy GUI. When a Unity Mono recovery needs ILSpyCmd, Studio checks for it and displays installation instructions if it is missing. Install it with `dotnet tool install --global ilspycmd` or use `--tool-path "<tools root>\ILSpy"` in place of `--global`. Current ILSpyCmd packages require .NET SDK 10.0.100 or newer to install. The Code Recovery stage reads game DLLs from extracted `Managed` folders and writes C# projects to `03_scripts/mono`.

The studio checks `RE_TOOLS`, then the repository and its parent for the shared toolkit, then `<repo>\.tools`. Create these folders there (download links are on each project's releases page):

| Folder | Tool | Needed for |
| --- | --- | --- |
| `7-Zip\7z.exe` (copy the whole 7-Zip folder: `7z.dll` must sit beside it) | 7-Zip | Unpacking Unity packages (required, installed by hand) |
| `AssetRipper\AssetRipper.GUI.Free.exe` | AssetRipper 2.x | Assets, scenes, materials |
| `Il2CppDumper\` | Il2CppDumper | IL2CPP type layout |
| `Cpp2IL\` | Cpp2IL | IL2CPP fallback and ARM64 dumps |
| `jadx\bin\jadx.bat` | JADX, plus a JDK 21 or later under `C:\Program Files\Eclipse Adoptium` | Java source from Android DEX |
| `ghidra\`, `android-sdk\ndk\<version>` | Ghidra, Android NDK | Symbol import (optional) |

The Tools tab shows PASS or FAIL for these folders and for a few optional extras (AssetStudioMod, ffmpeg, llvm, a Python environment, .NET, Mono.Cecil, the Android SDK), which show FAIL on a normal install. The CLI's `.tools` folder uses versioned names such as `AssetRipper-2.0.0`, which the studio does not look for. To use the CLI's tools anyway, point `RE_TOOLS` at a folder that has the plain names above. Ghidra and ILSpy cannot be updated from that tab; replace their folders by hand.

`studio\env.ps1` is optional. Dot-source it in your own PowerShell session if you want these tools on PATH. The launcher does not use it.

## Launch

```powershell
.\studio\Run_Recovery_Studio.bat
```

The launcher starts a server on `127.0.0.1:5899` and opens it in an Edge or Chrome app window. It hides the console. If nothing appears, run `python studio\desktop_shell.py` in a terminal for launcher errors. The studio keeps a rotating log in `%LOCALAPPDATA%\AetherReconStudio\logs` (`studio.log`; `server-console.log` holds start-up errors). Attach the end of `studio.log` to a bug report. The studio treats any program already listening on port 5899 as itself, so close that program first if the window shows something else. **Closing the window stops the studio and everything it started** (a running recovery, install, or Craft Anything run). Agents you opened keep their own console windows. If the studio is already running and you start the launcher again, it opens a second window on the running server.

## What it does, in order

1. Intake: copies the package to `00_original`, hashes it.
2. Unpack: 7-Zip into `01_extracted`, including nested APKs in an XAPK.
3. Asset rip: AssetRipper headless export into `02_assetripper`.
4. Code recovery: Il2CppDumper or Cpp2IL for IL2CPP into `03_il2cpp` (its `dump.cs` is copied to `03_scripts`), and JADX for Java into `04_jadx`.
5. Project folder: copies the ripped assets and project settings into `05_project`, adds a package manifest and an assembly definition, and adds placeholder SDK stubs only for SDKs the game does not already ship.
6. Audit: reads the rebuilt project and writes the studio audit (see [Studio audit](audit.md)). Godot games run three stages: intake, GDRE recovery, audit.

A stage counts as failed when it writes or refreshes no files. The report lists each stage's result.

Use a new, empty workspace folder for each game. The studio records the package hash in `.run.json` and refuses to run a different package in the same folder. Re-running the same package in its workspace is fine.

The Scripts tab lists the C# that AssetRipper recovered. Stage 5 (Rebuild Unity project) copies those files into the project, so "Export Selected" only adds scripts from elsewhere (it skips files already in the project, `dump.cs` and unselected ones) and renames name clashes instead of overwriting.


## Progress display

A bar at the bottom of every tab shows what is running, with elapsed time:

- **Recovery:** the current stage, "step 3 of 6" (Unity; a Godot run has 3), the tool working (AssetRipper, JADX, GDRE), its last output line, and a bar that fills as stages finish. The Recovery tab also lists every stage with a mark: waiting, working (with its own timer), done with the files it wrote, or failed with the reason. The list stays after the run until the next one starts.
- **Downloads and installs:** the tool, megabytes downloaded of the total, percent, speed and time left, then the checksum and install steps. An npm install (REA) has no percentage, so it shows elapsed time.
- **Craft Anything:** a moving bar, its last output line and elapsed time.

The external tools do not report a percentage while they work (an AssetRipper export can take many minutes), so a stage shows elapsed time and the tool's last line instead of a made-up percentage. A finished task stays on the bar for 15 seconds. Agent launches show no bar: the agent runs in its own window.


## Configuration

Unity editors are found under `C:\Program Files\Unity\Hub\Editor`. The studio picks the editor that matches the game's Unity version exactly, then the same major.minor line, and warns when it has to use another one, because Unity upgrades the project on open.
