# Unity Build Recovery

Tools for exporting Unity and Godot project data from builds you own or have permission to recover. Windows x64.

> **Status: alpha, tested by its author.** The recovery pipeline has been run against small synthetic packages and a few real builds, not a wide range of games. Treat every recovered project as a starting point.

## Aether Recon Studio (main tool)

A local window that recovers an APK, XAPK, ZIP, PCK or EXE into a Unity or Godot project, audits the result, and can hand the project to an AI coding agent you choose.

**Quick start**

1. Install Python 3.10 or later (with Tk) and Edge or Chrome.
2. Clone this repository and install **7-Zip** so that `7z.exe` is at `<tools folder>\7-Zip\7z.exe` (see [tool folders](docs/studio.md#tool-folders)).
3. Run `studio\Run_Recovery_Studio.bat`. The window opens on the **Recover** tab; the **Before you start** card shows which tools are in place.
4. Choose a game package and an empty workspace folder, press **Inspect package**, then **Run full recovery**. When a game needs AssetRipper, Cpp2IL or GDRE Tools, the studio shows the exact file and SHA-256 and downloads it only after you approve.
5. Read `reports\RECOVERY_REPORT.md` in the workspace. Open `05_project` in the matching Unity or Godot editor.

**What a run does:** intake (hash and copy), unpack, asset rip, code recovery, rebuild the Unity project, audit. A Godot run is intake, GDRE recovery, audit. Details: [docs/studio.md](docs/studio.md).

**Reference**
- [Studio reference](docs/studio.md): engines, requirements, tool folders, launching, stages, progress display, configuration
- [Agent recovery and Craft Anything](docs/agents.md)
- [Studio audit](docs/audit.md): what the audit checks and what its levels mean
- [Security notes](docs/security.md) and [SECURITY.md](SECURITY.md)
- [PowerShell CLI](docs/cli.md): the older batch tool (`Recover.ps1`), also on the [`legacy-cli`](https://github.com/AkaitoAi/unity-build-recovery/tree/legacy-cli) branch
- [Changelog](CHANGELOG.md), [Contributing](CONTRIBUTING.md), [Third-party tools](THIRD_PARTY_NOTICES.md)

## Limitations and risks

- **Not tested broadly.** Both tools have been used only on the author's machine. The audit, the agent prompt, the launch nonce, run locks, agent tracking, workspace rules, tool approval and download checks have unit tests (`python -m unittest discover tests`). The recovery pipeline itself (7-Zip, AssetRipper, GDRE, the page) is checked by hand against small synthetic packages, not by automated tests, and has not been run against a wide range of real games.
- **Recovered projects are starting points.** Native IL2CPP method bodies are not recovered, only type and method layout. Merging recovered code into scenes and prefabs, and gameplay testing, are your work. The Studio's scaffold is not compiled or verified, and its placeholder SDK stubs (ads, Firebase, Facebook, GameAnalytics) return fixed values, so behavior differs from the real SDKs.
- **Il2CppDumper 6.7.46, which the CLI uses, cannot read the newest IL2CPP metadata versions.** The IL2CPP stage then fails. The Studio also tries Cpp2IL, which handles more builds.
- **Untrusted input.** Treat every input build as untrusted. Neither tool runs the input's executables, but AssetRipper, JADX and the decompilers parse it. With `-VerifyInUnity`, Unity can run recovered scripts, including editor scripts, during import. Use that flag only for builds you trust.
- **Third-party tools** keep their own licenses. Keep builds and recovery outputs private.
- **Use only on builds you own or may legally recover.** You are responsible for that decision.

Closing the studio window stops the studio and anything it started.

## License

The code in this repository is MIT-licensed. Downloaded tools keep their upstream licenses.
