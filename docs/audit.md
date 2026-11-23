# Studio audit

## Studio audit

The studio audits the recovered project itself, so the numbers do not depend on an agent grading its own work. It runs as the last stage of every Unity and Godot recovery and on demand from the **Studio audit** card on the Recover tab (target `05_project`, or `06_playable` after an agent has repaired a copy). It only reads files: it never runs or loads game code and never uses the network.

| For Unity it counts | For Godot it counts |
| --- | --- |
| scripts with no script behind them (lost MonoBehaviours), duplicate C# types, files with unbalanced braces, decompiler error markers, a missing or empty build-scene list, packages the code uses but the manifest lacks | missing `res://` files, a missing main scene, missing autoloads, bytecode scripts left undecompiled, decompiler error markers |

It also reports, without counting them as defects: script and asset references by GUID that are not in the project (many are Unity package scripts, so they can be false alarms), the online SDKs the code uses, and for IL2CPP games the classes that have methods but no recovered source. The level is hard to earn:

| Level | Meaning |
| --- | --- |
| `L1 audit clean` | every check ran over every file, found nothing, and there is no caveat |
| `L1 audit clean with caveats` | no defects, but unresolved script GUIDs or IL2CPP classes with no source remain |
| `L1 audit partial` | at least one counted defect |
| `L1 audit incomplete` | no defect was seen, but the scan hit its 15-minute limit or skipped files it could not read (a file over the size limit, a binary-serialized scene) |
| `L0 extracted (nothing to audit)` | no scripts (Unity) or no scenes and scripts (Godot) to audit |

A build scene must be YAML text that holds a GameObject, and a `.meta` file only counts when its asset exists. **This is not a compile check:** only Unity or Godot can show that a project compiles, loads or plays.

Results go to `reports\STUDIO_AUDIT.json`, `STUDIO_AUDIT.md`, and one file per target (`STUDIO_AUDIT_05_project.json`, `STUDIO_AUDIT_06_playable.json`). When both exist, the card shows a before and after table. The recovery report links the level. The studio trusts only files that carry its own schema: an agent's `AGENT_AUDIT.*` files and any damaged or hand-edited file are ignored. Each saved audit records a fingerprint of the files it read, so the card says "the project has changed since this audit" when 05_project or 06_playable is edited afterwards. The fingerprint stops accidental staleness, not a deliberate forgery: an agent working in the workspace could still edit these files, so treat the numbers as trustworthy only when you ran the audit yourself. The audit and the prompt have unit tests: `python -m unittest discover tests`.
