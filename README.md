# Unity Build Recovery

An unattended Windows x64 CLI for exporting Unity project data from your own builds or builds you have permission to recover. Run in PowerShell 5.1 or newer.

    .\Recover.ps1 -InputPath @('C:\Builds\MyGame_Data', 'C:\Builds\MyGame.apk') -WorkRoot 'C:\Recovery'

Pass a Unity player data directory, a Unity asset or bundle, or a ZIP-based APK, XAPK, OBB, or ZIP. Keep tool and work directories outside input folders.

The first run downloads [AssetRipper 2.0.0](https://github.com/AssetRipper/AssetRipper/releases/tag/2.0.0) and checks the archive against the release asset's SHA-256 digest. Later runs reuse that installation. The script copies each input, hashes the copies, expands supported archives, and attempts to export a Unity project. It records per-input outcomes in recovery-report.json and keeps AssetRipper logs in each attempt folder. It never launches the input build.

An export error triggers up to three attempts by default. Set -Attempts to change that limit. After exhausting the attempts, the runner moves on to the next input. A run containing a failed input exits with code 1. Supply failed inputs again to retry them in a fresh run.

The workflow verifies the exported project structure. Unity Editor import, compilation, native IL2CPP method recovery, and gameplay reconstruction require separate work.

This repository's code is MIT-licensed. Downloaded tools retain their upstream licenses. Keep builds and recovery outputs private.
