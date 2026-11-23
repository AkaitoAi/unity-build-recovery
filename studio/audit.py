"""The studio's own static audit of a recovered project.

This is the check the studio does itself, so the numbers do not depend on an agent grading its
own work. It only reads files: it never runs, imports or loads game code, and never uses the
network. It cannot tell you that a project compiles, that a scene loads or that the game plays;
only Unity or Godot can. What it measures is what can be proven from the files:

  * references that point at nothing (missing scripts, unresolved asset GUIDs)
  * C# that cannot compile for a visible reason (duplicate types, unbalanced braces, decompiler
    error markers)
  * a project that cannot start (no enabled build scene, scene file missing)
  * packages the code needs but the manifest does not list
  * online SDKs the game uses
  * for IL2CPP games, the classes whose logic is not in the project at all

Results go to reports/AUDIT_<target>.json, reports/AUDIT.json (the latest, with a before/after
comparison when both 05_project and 06_playable have been audited) and reports/AUDIT.md.
"""

import datetime
import json
import os
import re
import time

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_CODE_BYTES = 5 * 1024 * 1024
YAML_EXTENSIONS = {".unity", ".prefab", ".mat", ".asset", ".controller", ".anim", ".overridecontroller",
                   ".playable", ".mixer", ".lighting", ".flare", ".physicmaterial", ".renderTexture".lower()}

SCRIPT_REF = re.compile(r"m_Script: \{fileID: (-?\d+)(?:, guid: ([0-9a-f]{32}), type: (\d+))?\}")
ASSET_REF = re.compile(r"\{fileID: -?\d+, guid: ([0-9a-f]{32}), type: ([0-3])\}")
META_GUID = re.compile(r"^guid: ([0-9a-f]{32})", re.M)
ERROR_MARKERS = re.compile(
    r"(?i)(/\*\s*error\b|//\s*error\b|could not (?:be )?decompil|decompilation (?:failed|error)|"
    r"failed to decompile|//\s*decompil\w+ (?:failed|error))")
STRIP = re.compile(r'//[^\n]*|/\*.*?\*/|@"(?:[^"]|"")*"|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])\'', re.S)
NAMESPACE_LINE = re.compile(r"^\s*namespace\s+([\w.]+)\s*(\{|;)?")
TYPE_LINE = re.compile(r"^\s*((?:\[[^\]]*\]\s*)*)((?:(?:public|internal|private|protected|static|sealed|abstract|"
                       r"partial|unsafe|readonly|new)\s+)*)(class|struct|enum|interface)\s+([A-Za-z_]\w*)")
USING_LINE = re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.M)

PACKAGE_FOR_NAMESPACE = {
    "TMPro": "com.unity.textmeshpro",
    "UnityEngine.UI": "com.unity.ugui",
    "UnityEngine.EventSystems": "com.unity.ugui",
    "UnityEngine.InputSystem": "com.unity.inputsystem",
    "Unity.Cinemachine": "com.unity.cinemachine",
    "Cinemachine": "com.unity.cinemachine",
    "UnityEngine.Rendering.Universal": "com.unity.render-pipelines.universal",
    "UnityEngine.Purchasing": "com.unity.purchasing",
    "UnityEngine.Timeline": "com.unity.timeline",
    "UnityEngine.Localization": "com.unity.localization",
    "Unity.Netcode": "com.unity.netcode.gameobjects",
}

SDK_PATTERNS = {
    "Firebase": r"\bFirebase\b|Firebase\.",
    "Google Mobile Ads": r"GoogleMobileAds",
    "AppLovin MAX": r"\bMaxSdk\b|AppLovin",
    "IronSource / LevelPlay": r"IronSource|LevelPlay",
    "Unity IAP": r"UnityEngine\.Purchasing|IAPButton|IStoreController",
    "Facebook": r"\bFB\.|Facebook\.Unity",
    "Photon": r"\bPhoton\.|PhotonNetwork",
    "PlayFab": r"PlayFab",
    "GameAnalytics": r"GameAnalytics",
    "Adjust": r"com\.adjust|\bAdjust\.",
    "AppsFlyer": r"AppsFlyer",
    "Unity Gaming Services": r"Unity\.Services\.|UnityServices\.",
}
SDK_REGEX = {name: re.compile(p) for name, p in SDK_PATTERNS.items()}


class Deadline:
    def __init__(self, seconds):
        self.end = time.time() + seconds
        self.hit = False

    def check(self):
        if time.time() > self.end:
            self.hit = True
        return self.hit


# ----------------------------------------------------------------------------- helpers

def _read_text(path, limit=MAX_FILE_BYTES):
    try:
        if os.path.getsize(path) > limit:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError:
        return None


def _walk_files(root, skip_dirs=()):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not os.path.islink(os.path.join(current, d))]
        for name in files:
            yield os.path.join(current, name)


def _strip_code(text):
    return STRIP.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _types_and_balance(stripped):
    """Top-level types as (namespace, name, partial) and the final brace depth."""
    depth = 0
    namespaces = []          # (name, depth inside it)
    pending = None
    file_scoped = ""
    found = []
    for line in stripped.splitlines():
        base_depth = namespaces[-1][1] if namespaces else 0
        match = NAMESPACE_LINE.match(line)
        if match:
            name, tail = match.group(1), match.group(2)
            if tail == ";":
                file_scoped = name
            elif tail == "{":
                namespaces.append((name, depth + 1))
            else:
                pending = name
        elif depth == base_depth:
            type_match = TYPE_LINE.match(line)
            if type_match:
                prefix = ".".join(n for n, _d in namespaces) or file_scoped
                found.append((prefix, type_match.group(4), "partial" in type_match.group(2).split()))
        opened, closed = line.count("{"), line.count("}")
        if pending and opened and not match:
            namespaces.append((pending, depth + 1))
            pending = None
        depth += opened - closed
        while namespaces and depth < namespaces[-1][1]:
            namespaces.pop()
    return found, depth


def _known_guids(root):
    guids = {}
    for path in _walk_files(root):
        if path.endswith(".meta"):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    head = handle.read(400)
            except OSError:
                continue
            match = META_GUID.search(head)
            if match:
                guids[match.group(1)] = path[:-5]
    return guids


# ----------------------------------------------------------------------------- Unity audit

def _audit_unity(project, work_folder, deadline):
    results = {}
    assets = os.path.join(project, "Assets")
    if not os.path.isdir(assets):
        return {"error": "No Assets folder in the project."}

    counts = {}
    for path in _walk_files(assets):
        ext = os.path.splitext(path)[1].lower()
        if ext != ".meta":
            counts[ext] = counts.get(ext, 0) + 1
    results["inventory"] = {
        "scenes": counts.get(".unity", 0), "prefabs": counts.get(".prefab", 0), "scripts": counts.get(".cs", 0),
        "textures": sum(counts.get(e, 0) for e in (".png", ".jpg", ".jpeg", ".tga", ".psd", ".exr", ".tif")),
        "audio": sum(counts.get(e, 0) for e in (".wav", ".mp3", ".ogg", ".aif", ".aiff")),
        "materials": counts.get(".mat", 0), "animations": counts.get(".anim", 0), "shaders": counts.get(".shader", 0),
    }

    guids = _known_guids(project)

    # references inside scenes, prefabs and other YAML assets
    lost_scripts = {}          # file -> count of m_Script with fileID 0
    unresolved_scripts = {}    # guid -> count
    unresolved_assets = {}     # guid -> count
    binary_yaml = 0
    for path in _walk_files(assets):
        if deadline.check():
            break
        if os.path.splitext(path)[1].lower() not in YAML_EXTENSIONS:
            continue
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                continue
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                first = handle.readline()
                if not first.startswith("%YAML"):
                    binary_yaml += 1
                    continue
                rel = os.path.relpath(path, project)
                for line in handle:
                    if "m_Script:" in line:
                        m = SCRIPT_REF.search(line)
                        if m:
                            if m.group(1) == "0" and not m.group(2):
                                lost_scripts[rel] = lost_scripts.get(rel, 0) + 1
                            elif m.group(2) and m.group(2) not in guids and not m.group(2).startswith("0000000000000000"):
                                unresolved_scripts[m.group(2)] = unresolved_scripts.get(m.group(2), 0) + 1
                    elif "guid:" in line:  # script references were handled above and are not counted twice
                        for m in ASSET_REF.finditer(line):
                            g, kind = m.group(1), m.group(2)
                            if kind in ("2", "3") and g not in guids and not g.startswith("0000000000000000"):
                                unresolved_assets[g] = unresolved_assets.get(g, 0) + 1
        except OSError:
            continue
    results["missing_scripts"] = {
        "count": sum(lost_scripts.values()),
        "files_affected": len(lost_scripts),
        "worst": sorted(lost_scripts.items(), key=lambda kv: -kv[1])[:15],
        "note": "m_Script entries with no script at all (a lost MonoBehaviour).",
    }
    results["unresolved_script_guids"] = {
        "count": sum(unresolved_scripts.values()), "distinct": len(unresolved_scripts),
        "sample": sorted(unresolved_scripts.items(), key=lambda kv: -kv[1])[:10],
        "note": "Scripts referenced by GUID that are not in the project. Many are Unity package scripts "
                "(UI, TextMeshPro) that live in a package, so this number can include false alarms.",
    }
    results["unresolved_asset_guids"] = {
        "count": sum(unresolved_assets.values()), "distinct": len(unresolved_assets),
        "sample": sorted(unresolved_assets.items(), key=lambda kv: -kv[1])[:10],
        "note": "Project assets referenced by GUID that are not in the project; may include package assets.",
    }
    results["binary_yaml_files"] = binary_yaml

    # C# checks
    type_sites = {}
    all_type_names = set()
    unbalanced, error_files, used_namespaces, sdk_hits = [], [], {}, {}
    cs_total = 0
    for path in _walk_files(assets):
        if deadline.check():
            break
        if not path.lower().endswith(".cs"):
            continue
        cs_total += 1
        text = _read_text(path, MAX_CODE_BYTES)
        if text is None:
            continue
        rel = os.path.relpath(path, project)
        if ERROR_MARKERS.search(text):
            error_files.append(rel)
        stripped = _strip_code(text)
        found, final_depth = _types_and_balance(stripped)
        if final_depth != 0:
            unbalanced.append(rel)
        for namespace, name, partial in found:
            all_type_names.add(name)
            if not partial:
                type_sites.setdefault((namespace, name), []).append(rel)
        for used in USING_LINE.findall(text):
            used_namespaces.setdefault(used, 0)
            used_namespaces[used] += 1
        # The studio's own placeholder stubs name these SDKs on purpose; they are listed separately.
        if os.sep + "Stubs" + os.sep not in rel:
            for sdk, regex in SDK_REGEX.items():
                if regex.search(text):
                    sdk_hits.setdefault(sdk, []).append(rel)
    duplicates = {f"{ns + '.' if ns else ''}{name}": sites for (ns, name), sites in type_sites.items() if len(sites) > 1}
    results["duplicate_types"] = {"count": len(duplicates), "list": [{"type": k, "files": v[:4]} for k, v in sorted(duplicates.items())[:25]]}
    results["syntax"] = {"unbalanced_braces": len(unbalanced), "unbalanced_files": unbalanced[:15],
                         "decompiler_error_markers": len(error_files), "marker_files": error_files[:15],
                         "scripts_read": cs_total}

    # build settings
    settings = os.path.join(project, "ProjectSettings", "EditorBuildSettings.asset")
    scenes_listed, scenes_missing, enabled = [], [], []
    text = _read_text(settings) if os.path.isfile(settings) else None
    for m in re.finditer(r"-\s*enabled:\s*(\d)\s*\n\s*path:\s*(.*)", text or ""):
        path = m.group(2).strip()
        scenes_listed.append(path)
        if m.group(1) == "1":
            enabled.append(path)
        if path and not os.path.isfile(os.path.join(project, path)):
            scenes_missing.append(path)
    results["build_settings"] = {
        "file_present": os.path.isfile(settings), "scenes_listed": len(scenes_listed), "enabled": len(enabled),
        "missing_scene_files": scenes_missing[:10], "main_scene": enabled[0] if enabled else "",
        "ok": bool(enabled) and not scenes_missing,
    }

    # packages
    manifest = os.path.join(project, "Packages", "manifest.json")
    deps = {}
    try:
        with open(manifest, "r", encoding="utf-8") as handle:
            deps = json.load(handle).get("dependencies", {})
    except (OSError, ValueError):
        pass
    needed = {}
    for namespace, package in PACKAGE_FOR_NAMESPACE.items():
        if any(u == namespace or u.startswith(namespace + ".") for u in used_namespaces):
            needed[package] = namespace
    missing_packages = {p: ns for p, ns in needed.items() if p not in deps}
    results["packages"] = {"manifest_present": os.path.isfile(manifest), "listed": len(deps),
                           "needed_but_missing": [{"package": p, "namespace": ns} for p, ns in sorted(missing_packages.items())]}

    # online SDKs
    plugin_dirs = []
    plugins = os.path.join(assets, "Plugins")
    if os.path.isdir(plugins):
        plugin_dirs = sorted(os.listdir(plugins))[:40]
    stub_dir = os.path.join(assets, "_Project", "Scripts", "Stubs")
    stubs = sorted(os.listdir(stub_dir)) if os.path.isdir(stub_dir) else []
    results["online_sdks"] = {"used": [{"sdk": s, "files": len(f), "example": f[0]} for s, f in sorted(sdk_hits.items())],
                              "plugin_folders": plugin_dirs, "studio_stubs": stubs}

    # IL2CPP: classes with methods in dump.cs and no source in the project
    dump = None
    for candidate in (os.path.join(work_folder, "03_scripts", "dump.cs"), os.path.join(work_folder, "03_il2cpp", "dump.cs")):
        if os.path.isfile(candidate):
            dump = candidate
            break
    if dump:
        results["logic_gap"] = _logic_gap(dump, all_type_names, deadline)
    return results


def _logic_gap(dump_path, source_types, deadline):
    images, classes = [], []
    current = None
    image_re = re.compile(r"^// Image (\d+): (\S+) - (\d+)")
    class_re = re.compile(r"^(?:(?:public|internal|private|protected|static|sealed|abstract|partial)\s+)*"
                          r"(class|struct)\s+(\S+).*// TypeDefIndex: (\d+)")
    try:
        with open(dump_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                m = image_re.match(line)
                if m:
                    images.append((int(m.group(3)), m.group(2)))
                    continue
                m = class_re.match(line)
                if m:
                    current = {"name": m.group(2), "index": int(m.group(3)), "methods": 0}
                    classes.append(current)
                elif current is not None and line.startswith("\t// RVA:"):
                    current["methods"] += 1
                if deadline.check():
                    break
    except OSError:
        return {"error": "dump.cs could not be read"}
    images.sort()

    def image_of(index):
        name = ""
        for start, image in images:
            if start <= index:
                name = image
            else:
                break
        return name

    game = [c for c in classes if image_of(c["index"]).startswith("Assembly-CSharp")
            and c["methods"] > 0 and not c["name"].startswith("<")]
    gap = [c for c in game if re.split(r"[./]", c["name"])[-1].split("<")[0] not in source_types]
    return {
        "game_classes_with_methods": len(game), "classes_without_source": len(gap),
        "methods_without_bodies": sum(c["methods"] for c in gap),
        "largest": [{"class": c["name"], "methods": c["methods"]} for c in sorted(gap, key=lambda c: -c["methods"])[:30]],
        "note": "IL2CPP keeps names and signatures only, so these classes have no recovered logic.",
    }


# ----------------------------------------------------------------------------- Godot audit

def _audit_godot(project, work_folder, deadline):
    results = {}
    settings = os.path.join(project, "project.godot")
    text = _read_text(settings)
    if text is None:
        return {"error": "No project.godot in the project."}
    counts = {}
    undecompiled = 0
    for path in _walk_files(project, skip_dirs=(".godot", ".import")):
        ext = os.path.splitext(path)[1].lower()
        counts[ext] = counts.get(ext, 0) + 1
        if ext == ".gdc":
            # GDRE keeps a copy of each bytecode file in .autoconverted; it only counts as left
            # undecompiled when no .gd source exists for it next to it or at the project path.
            plain = os.path.splitext(path)[0] + ".gd"
            relocated = plain.replace(os.sep + ".autoconverted" + os.sep, os.sep)
            if not (os.path.exists(plain) or os.path.exists(relocated)):
                undecompiled += 1
    counts[".gdc"] = undecompiled
    results["inventory"] = {"scenes": counts.get(".tscn", 0), "resources": counts.get(".tres", 0),
                            "scripts_gd": counts.get(".gd", 0), "scripts_cs": counts.get(".cs", 0),
                            "undecompiled_gdc": counts.get(".gdc", 0),
                            "textures": sum(counts.get(e, 0) for e in (".png", ".jpg", ".jpeg", ".webp", ".svg")),
                            "audio": sum(counts.get(e, 0) for e in (".wav", ".mp3", ".ogg"))}
    main = re.search(r'run/main_scene\s*=\s*"([^"]+)"', text)
    main_scene = main.group(1) if main else ""

    def exists(res_path):
        return os.path.exists(os.path.join(project, res_path.replace("res://", "").replace("/", os.sep)))

    results["build_settings"] = {"main_scene": main_scene, "main_scene_exists": bool(main_scene) and exists(main_scene),
                                 "ok": bool(main_scene) and exists(main_scene)}
    ref = re.compile(r'res://[^"\'\s)\]},]+')
    missing = {}
    syntax_files = []
    for path in _walk_files(project, skip_dirs=(".godot", ".import")):
        if deadline.check():
            break
        if os.path.splitext(path)[1].lower() not in (".tscn", ".tres", ".gd", ".cfg", ".godot", ".gdextension"):
            continue
        body = _read_text(path, MAX_CODE_BYTES)
        if body is None:
            continue
        rel = os.path.relpath(path, project)
        if ERROR_MARKERS.search(body):
            syntax_files.append(rel)
        for found in set(ref.findall(body)):
            target = found.split("::")[0]
            if not exists(target):
                missing.setdefault(target, set()).add(rel)
    results["missing_resources"] = {"count": len(missing), "list": [{"path": k, "used_by": sorted(v)[:3]} for k, v in sorted(missing.items())[:25]]}
    autoload_missing = []
    in_autoload = False
    for line in text.splitlines():
        if line.strip().startswith("["):
            in_autoload = line.strip() == "[autoload]"
            continue
        if in_autoload and "=" in line:
            target = line.split("=", 1)[1].strip().strip('"').lstrip("*")
            if target.startswith("res://") and not exists(target):
                autoload_missing.append(target)
    results["autoloads"] = {"missing": autoload_missing}
    results["syntax"] = {"decompiler_error_markers": len(syntax_files), "marker_files": syntax_files[:15],
                         "undecompiled_gdc": counts.get(".gdc", 0)}
    natives = [os.path.relpath(p, project) for p in _walk_files(project, skip_dirs=(".godot", ".import"))
               if p.lower().endswith((".dll", ".so", ".gdextension"))]
    results["native_extensions"] = {"count": len(natives), "list": natives[:15]}
    log = os.path.join(project, "gdre_export.log")
    log_text = _read_text(log) or ""
    problems = [l.strip() for l in log_text.splitlines() if l.strip().startswith(("ERROR", "WARNING"))]
    distinct = {}
    for line in problems:
        distinct[line] = distinct.get(line, 0) + 1
    results["recovery_log"] = {"errors": sum(1 for l in problems if l.startswith("ERROR")),
                               "warnings": sum(1 for l in problems if l.startswith("WARNING")),
                               "distinct": sorted(distinct.items(), key=lambda kv: -kv[1])[:10]}
    return results


# ----------------------------------------------------------------------------- summary, files

def _summary(engine, results):
    """The numbers that decide the level. Unresolved GUIDs are reported but not counted as defects:
    they include package scripts that cannot be resolved from the files alone."""
    if "error" in results:
        return {"level": "not audited", "findings": {}, "total": 0}
    findings = {}
    if engine == "Godot":
        findings["missing_resources"] = results["missing_resources"]["count"]
        findings["main_scene_missing"] = 0 if results["build_settings"]["ok"] else 1
        findings["missing_autoloads"] = len(results["autoloads"]["missing"])
        findings["undecompiled_scripts"] = results["syntax"]["undecompiled_gdc"]
        findings["decompiler_error_markers"] = results["syntax"]["decompiler_error_markers"]
    else:
        findings["missing_scripts"] = results["missing_scripts"]["count"]
        findings["duplicate_types"] = results["duplicate_types"]["count"]
        findings["unbalanced_braces"] = results["syntax"]["unbalanced_braces"]
        findings["decompiler_error_markers"] = results["syntax"]["decompiler_error_markers"]
        findings["build_scene_problem"] = 0 if results["build_settings"]["ok"] else 1
        findings["missing_packages"] = len(results["packages"]["needed_but_missing"])
    total = sum(findings.values())
    return {"level": "L1 audit clean" if total == 0 else "L1 audit partial", "findings": findings, "total": total}


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def compare(before, after):
    """Per-finding change between two audits (negative is better)."""
    if not before or not after:
        return None
    keys = sorted(set(before["summary"]["findings"]) | set(after["summary"]["findings"]))
    rows = []
    for key in keys:
        b, a = before["summary"]["findings"].get(key, 0), after["summary"]["findings"].get(key, 0)
        rows.append({"finding": key, "before": b, "after": a, "change": a - b})
    return {"before_target": before["target"], "after_target": after["target"], "rows": rows,
            "before_total": before["summary"]["total"], "after_total": after["summary"]["total"]}


def _markdown(report, comparison):
    lines = [f"# Audit: {report['target']}", "", f"- Engine: {report['engine']}", f"- Date: {report['created']}",
             f"- **Level: {report['summary']['level']}**", "",
             "This audit only reads files. It cannot show that the project compiles, that a scene loads, or that "
             "the game plays; open the project in the editor to check those.", ""]
    if report.get("partial"):
        lines += ["**The audit stopped at its time limit, so some counts are incomplete.**", ""]
    if "error" in report["results"]:
        lines.append(f"Not audited: {report['results']['error']}")
    else:
        lines += ["## Findings", "", "| Finding | Count |", "|---|---|"]
        lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in report["summary"]["findings"].items()]
        lines.append("")
        for name, data in report["results"].items():
            if isinstance(data, dict):
                lines += [f"## {name.replace('_', ' ')}", "", "```json", json.dumps(data, indent=1)[:3000], "```", ""]
    if comparison:
        lines += ["## Before and after", "", f"{comparison['before_target']} -> {comparison['after_target']}", "",
                  "| Finding | Before | After | Change |", "|---|---|---|---|"]
        lines += [f"| {r['finding'].replace('_', ' ')} | {r['before']} | {r['after']} | {r['change']:+d} |" for r in comparison["rows"]]
    return "\n".join(lines) + "\n"


def run_audit(work_folder, target="05_project", engine=None, seconds=900):
    """Audits work_folder/target and writes the report files. Returns the report dict."""
    project = os.path.join(work_folder, target)
    reports = os.path.join(work_folder, "reports")
    os.makedirs(reports, exist_ok=True)
    if engine is None:
        marker = _load(os.path.join(work_folder, ".run.json")) or {}
        engine = marker.get("engine") or ("Godot" if os.path.exists(os.path.join(project, "project.godot")) else "Unity")
    deadline = Deadline(seconds)
    if not os.path.isdir(project):
        results = {"error": f"{target} does not exist."}
    elif engine == "Godot":
        results = _audit_godot(project, work_folder, deadline)
    else:
        results = _audit_unity(project, work_folder, deadline)
    report = {"schema": 1, "engine": engine, "target": target, "created": datetime.datetime.now().isoformat(timespec="seconds"),
              "partial": deadline.hit, "results": results, "summary": _summary(engine, results)}
    with open(os.path.join(reports, f"AUDIT_{target}.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)
    before = _load(os.path.join(reports, "AUDIT_05_project.json"))
    after = _load(os.path.join(reports, "AUDIT_06_playable.json"))
    comparison = compare(before, after)
    latest = dict(report, comparison=comparison)
    with open(os.path.join(reports, "AUDIT.json"), "w", encoding="utf-8") as handle:
        json.dump(latest, handle, indent=1)
    with open(os.path.join(reports, "AUDIT.md"), "w", encoding="utf-8") as handle:
        handle.write(_markdown(report, comparison))
    return latest


def load_latest(work_folder):
    return _load(os.path.join(work_folder, "reports", "AUDIT.json"))
