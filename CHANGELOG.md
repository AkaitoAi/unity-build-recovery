# Changelog

## Unreleased
- PowerShell CLI: new REA stage (IL2CPP metadata with method addresses and Ghidra seeds, managed members, APK signatures, UnityFS bundles). It reuses the toolkit's `rea-runtime` or installs `rea-agents` 6.10.1 into `.tools`, is skipped without Node.js, and can be turned off with `-SkipRea`.
- PowerShell CLI: fix tool runs being reported as failed with an empty exit code on Windows PowerShell 5.1.

## 0.2.0
- Aether Recon Studio becomes the main tool; the old PowerShell CLI stays at the repo root and on the `legacy-cli` branch.
- Godot support (GDRE Tools), Craft Anything, agent recovery with per-game prompts, REA support.
- Studio audit with before/after comparison.
- Launch nonce and cookie, owned run lock, agent tracking, approval for every tool download.
- Logging to `%LOCALAPPDATA%\AetherReconStudio\logs`, in-app dialogs, Cancel button, About box, accessibility fixes.
