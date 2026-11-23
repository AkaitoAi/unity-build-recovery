"""The studio's own static audit of a recovered project.

This is the check the studio does itself, so the numbers do not depend on an agent grading its
own work. It only reads files: it never runs, imports or loads game code, and never uses the
network. It cannot tell you that a project compiles, that a scene loads or that the game plays;
only Unity or Godot can. What it measures is what can be proven from the files:

  * references that point at nothing (lost scripts, unresolved asset GUIDs)
  * C# that cannot compile for a visible reason (duplicate types, unbalanced braces, decompiler
    error markers)
  * a project that cannot start (no enabled build scene, scene file missing or empty)
  * packages the code needs but the manifest does not list
  * online SDKs the game uses
  * for IL2CPP games, the classes whose logic is not in the project at all

The level is deliberately hard to earn. "L1 audit clean" means every check ran over every file,
found nothing, and found no caveat. A scan that hit its time limit, skipped files it could not
read, or found no scripts to audit is never reported as clean.

Results go to reports/STUDIO_AUDIT_<target>.json, reports/STUDIO_AUDIT.json (the latest, with a
before/after comparison when both 05_project and 06_playable have been audited) and
reports/STUDIO_AUDIT.md. The names are the studio's own: an agent writes AGENT_AUDIT.* instead,
and the studio ignores any file that does not carry its schema. A saved audit records a
fingerprint of the files it read, so the page can say when the project has changed since.
"""

import datetime
import hashlib
import json
import os
import re
import time

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_CODE_BYTES = 5 * 1024 * 1024
MAX_FINGERPRINT_FILES = 100000
SCHEMA = 1
YAML_EXTENSIONS = {".unity", ".prefab", ".mat", ".asset", ".controller", ".anim", ".overridecontroller",
                   ".playable", ".mixer", ".lighting", ".flare", ".physicmaterial", ".rendertexture"}

SCRIPT_REF = re.compile(r"m_Script: \{fileID: (-?\d+)(?:, guid: ([0-9a-f]{32}), type: (\d+))?\}")
ASSET_REF = re.compile(r"\{fileID: -?\d+, guid: ([0-9a-f]{32}), type: ([0-3])\}")
META_GUID = re.compile(r"^guid: ([0-9a-f]{32})", re.M)
# Decompilers leave comments such as "/* Error: ... */" or "// Could not decompile ...". Ordinary
# comments that merely start with the word "error" are not markers.
ERROR_MARKERS = re.compile(
    r"(/\*\s*(?:error|failed)\b|//\s*(?:could not|unable to|failed to)\s+(?:be\s+)?(?:decompil|decod)\w*|"
    r"decompilation (?:failed|error)|ilspy[^\n]{0,40}(?:failed|error))", re.I)
ERROR_MARKER_UPPER = re.compile(r"//\s*ERROR:")
STRIP = re.compile(r'//[^\n]*|/\*.*?\*/|@"(?:[^"]|"")*"|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])\'', re.S)
NAMESPACE_LINE = re.compile(r"^\s*namespace\s+([\w.]+)\s*(\{|;)?")
TYPE_LINE = re.compile(r"^\s*((?:\[[^\]]*\]\s*)*)((?:(?:public|internal|private|protected|static|sealed|abstract|"
                       r"partial|unsafe|readonly|new)\s+)*)(class|struct|enum|interface)\s+([A-Za-z_]\w*)\s*(<[^>]*>)?")
ANY_TYPE = re.compile(r"\b(?:class|struct|enum|interface)\s+([A-Za-z_]\w*)")
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


class Scan:
    """The time limit, plus a count of every file the audit could not read."""

    def __init__(self, seconds):
        self.end = time.time() + seconds
        self.hit = False
        self.skipped = {}

    def check(self):
        if time.time() > self.end:
            self.hit = True
        return self.hit

    def skip(self, kind):
        self.skipped[kind] = self.skipped.get(kind, 0) + 1


# ----------------------------------------------------------------------------- helpers

def _is_link(path):
    """A symlink or a Windows junction (which os.path.islink does not report)."""
    if os.path.islink(path):
        return True
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def _read_text(path, limit, scan):
    try:
        if os.path.getsize(path) > limit:
            scan.skip("oversize_file")
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError:
        scan.skip("unreadable_file")
        return None


def _walk_files(root, skip_dirs=()):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not _is_link(os.path.join(current, d))]
        for name in files:
            yield os.path.join(current, name)


def _strip_code(text):
    return STRIP.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _has_error_marker(text):
    return bool(ERROR_MARKERS.search(text) or ERROR_MARKER_UPPER.search(text))


def _types_and_balance(stripped):
    """Top-level types as (namespace, name, partial) and the final brace depth. A generic type's
    name carries its arity (Event`1), so Event<T> and Event<T1,T2> are different types."""
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
                name = type_match.group(4)
                if type_match.group(5):
                    name += "`" + str(type_match.group(5).count(",") + 1)
                found.append((prefix, name, "partial" in type_match.group(2).split()))
        opened, closed = line.count("{"), line.count("}")
        if pending and opened and not match:
            namespaces.append((pending, depth + 1))
            pending = None
        depth += opened - closed
        while namespaces and depth < namespaces[-1][1]:
            namespaces.pop()
    return found, depth


def _known_guids(root):
    """GUIDs of assets that exist. A .meta file whose asset is gone does not count."""
    guids = {}
    for path in _walk_files(root):
        if path.endswith(".meta"):
            asset = path[:-5]
            if not os.path.exists(asset):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    head = handle.read(400)
            except OSError:
                continue
            match = META_GUID.search(head)
            if match:
                guids[match.group(1)] = asset
    return guids


def _scene_is_real(path):
    """A scene file the editor could load: YAML text that holds at least one GameObject."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            if not handle.readline().startswith("%YAML"):
                return False
            for line in handle:
                if line.startswith("GameObject:"):
                    return True
    except OSError:
        return False
    return False


def tree_fingerprint(project):
    """A hash of every file's path, size and modification time, to tell when a project changed."""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(_walk_files(project)):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        digest.update(f"{os.path.relpath(path, project)}|{stat.st_size}|{int(stat.st_mtime)}\n".encode("utf-8", "replace"))
        count += 1
        if count > MAX_FINGERPRINT_FILES:
            return None
    return {"files": count, "sha256": digest.hexdigest()}


# ----------------------------------------------------------------------------- Unity audit

def _audit_unity(project, work_folder, scan):
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

    lost_scripts = {}          # file -> count of m_Script with fileID 0
    unresolved_scripts = {}    # guid -> count
    unresolved_assets = {}     # guid -> count
    for path in _walk_files(assets):
        if scan.check():
            break
        if os.path.splitext(path)[1].lower() not in YAML_EXTENSIONS:
            continue
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                scan.skip("oversize_file")
                continue
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                first = handle.readline()
                if not first.startswith("%YAML"):
                    scan.skip("binary_yaml_file")      # a binary-serialized scene cannot be read here
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
            scan.skip("unreadable_file")
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
                "(UI, TextMeshPro) that live in a package, so some are false alarms. They stop the level "
                "from being plain 'clean' and are reported as a caveat.",
    }
    results["unresolved_asset_guids"] = {
        "count": sum(unresolved_assets.values()), "distinct": len(unresolved_assets),
        "sample": sorted(unresolved_assets.items(), key=lambda kv: -kv[1])[:10],
        "note": "Project assets referenced by GUID that are not in the project; may include package assets.",
    }

    # C# checks
    type_sites = {}
    all_type_names = set()
    unbalanced, error_files, used_namespaces, sdk_hits = [], [], {}, {}
    cs_total = 0
    for path in _walk_files(assets):
        if scan.check():
            break
        if not path.lower().endswith(".cs"):
            continue
        cs_total += 1
        text = _read_text(path, MAX_CODE_BYTES, scan)
        if text is None:
            continue
        rel = os.path.relpath(path, project)
        if _has_error_marker(text):
            error_files.append(rel)
        stripped = _strip_code(text)
        found, final_depth = _types_and_balance(stripped)
        if final_depth != 0:
            unbalanced.append(rel)
        for namespace, name, partial in found:
            if not partial:
                type_sites.setdefault((namespace, name), []).append(rel)
        all_type_names.update(ANY_TYPE.findall(stripped))     # nested types count as source too
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

    # build settings: at least one enabled scene, every listed scene exists and is a real scene
    settings = os.path.join(project, "ProjectSettings", "EditorBuildSettings.asset")
    scenes_listed, scenes_missing, scenes_empty, enabled = [], [], [], []
    text = _read_text(settings, MAX_FILE_BYTES, scan) if os.path.isfile(settings) else None
    for m in re.finditer(r"-\s*enabled:\s*(\d)\s*\n\s*path:\s*(.*)", text or ""):
        path = m.group(2).strip()
        scenes_listed.append(path)
        if m.group(1) == "1":
            enabled.append(path)
        full = os.path.join(project, path)
        if path and not os.path.isfile(full):
            scenes_missing.append(path)
        elif path and not _scene_is_real(full):
            scenes_empty.append(path)
    results["build_settings"] = {
        "file_present": os.path.isfile(settings), "scenes_listed": len(scenes_listed), "enabled": len(enabled),
        "missing_scene_files": scenes_missing[:10], "empty_or_unreadable_scenes": scenes_empty[:10],
        "main_scene": enabled[0] if enabled else "",
        "ok": bool(enabled) and not scenes_missing and not scenes_empty,
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
    for candidate in (os.path.join(work_folder, "03_scripts", "dump.cs"), os.path.join(work_folder, "03_il2cpp", "dump.cs")):
        if os.path.isfile(candidate):
            results["logic_gap"] = _logic_gap(candidate, all_type_names, scan)
            break
    return results


def _logic_gap(dump_path, source_types, scan):
    images, classes = [], []
    current = None
    image_re = re.compile(r"^// Image (\d+): (\S+) - (\d+)")
    type_re = re.compile(r"^(?:(?:public|internal|private|protected|static|sealed|abstract|partial)\s+)*"
                         r"(class|struct|interface|enum)\s+(\S+).*// TypeDefIndex: (\d+)")
    try:
        with open(dump_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                m = image_re.match(line)
                if m:
                    images.append((int(m.group(3)), m.group(2)))
                    continue
                m = type_re.match(line)
                if m:
                    if m.group(1) in ("class", "struct"):
                        current = {"name": m.group(2), "index": int(m.group(3)), "methods": 0}
                        classes.append(current)
                    else:
                        current = None   # interface and enum members are never credited to a class
                elif current is not None and line.startswith("\t// RVA:") and not line.startswith("\t// RVA: -1"):
                    current["methods"] += 1
                if scan.check():
                    break
    except OSError:
        scan.skip("unreadable_file")
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

def _godot_uid_map(project, scan):
    """uid://... -> res:// path, read from the header of each scene and resource."""
    uids = {}
    pattern = re.compile(r'uid="(uid://[^"]+)"')
    for path in _walk_files(project, skip_dirs=(".godot", ".import")):
        if os.path.splitext(path)[1].lower() in (".tscn", ".tres", ".gd"):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    head = handle.read(600)
            except OSError:
                scan.skip("unreadable_file")
                continue
            m = pattern.search(head)
            if m:
                uids[m.group(1)] = "res://" + os.path.relpath(path, project).replace(os.sep, "/")
    return uids


def _audit_godot(project, work_folder, scan):
    results = {}
    settings = os.path.join(project, "project.godot")
    text = _read_text(settings, MAX_FILE_BYTES, scan)
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
    uids = _godot_uid_map(project, scan)

    def resolve(res_path):
        return uids.get(res_path, "") if res_path.startswith("uid://") else res_path

    def exists(res_path):
        resolved = resolve(res_path)
        return bool(resolved) and os.path.exists(os.path.join(project, resolved.replace("res://", "").replace("/", os.sep)))

    main = re.search(r'run/main_scene\s*=\s*"([^"]+)"', text)
    main_scene = main.group(1) if main else ""
    results["build_settings"] = {"main_scene": main_scene, "main_scene_resolved": resolve(main_scene),
                                 "main_scene_exists": bool(main_scene) and exists(main_scene),
                                 "ok": bool(main_scene) and exists(main_scene)}
    ref = re.compile(r'res://[^"\'\s)\]},]+')
    missing = {}
    syntax_files = []
    for path in _walk_files(project, skip_dirs=(".godot", ".import")):
        if scan.check():
            break
        if os.path.splitext(path)[1].lower() not in (".tscn", ".tres", ".gd", ".cfg", ".godot", ".gdextension"):
            continue
        body = _read_text(path, MAX_CODE_BYTES, scan)
        if body is None:
            continue
        rel = os.path.relpath(path, project)
        if _has_error_marker(body):
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
            if (target.startswith("res://") or target.startswith("uid://")) and not exists(target):
                autoload_missing.append(target)
    results["autoloads"] = {"missing": autoload_missing}
    results["syntax"] = {"decompiler_error_markers": len(syntax_files), "marker_files": syntax_files[:15],
                         "undecompiled_gdc": counts.get(".gdc", 0)}
    natives = [os.path.relpath(p, project) for p in _walk_files(project, skip_dirs=(".godot", ".import"))
               if p.lower().endswith((".dll", ".so", ".gdextension"))]
    results["native_extensions"] = {"count": len(natives), "list": natives[:15]}
    log_text = _read_text(os.path.join(project, "gdre_export.log"), MAX_FILE_BYTES, scan) if os.path.exists(os.path.join(project, "gdre_export.log")) else ""
    problems = [l.strip() for l in (log_text or "").splitlines() if l.strip().startswith(("ERROR", "WARNING"))]
    distinct = {}
    for line in problems:
        distinct[line] = distinct.get(line, 0) + 1
    results["recovery_log"] = {"errors": sum(1 for l in problems if l.startswith("ERROR")),
                               "warnings": sum(1 for l in problems if l.startswith("WARNING")),
                               "distinct": sorted(distinct.items(), key=lambda kv: -kv[1])[:10]}
    return results


# ----------------------------------------------------------------------------- summary, files

def _summary(engine, results):
    """The numbers and the level. Defects are counted; caveats are things the files cannot settle
    (unresolved script GUIDs may be package scripts; IL2CPP logic is simply absent). A level of
    'clean' needs a complete scan, something to audit, no defects and no caveats."""
    if "error" in results:
        return {"level": "not audited", "findings": {}, "total": 0, "caveats": {}, "scan_complete": False, "skipped": {}}
    scan = results.get("scan", {})
    skipped = dict(scan.get("skipped", {}))
    scan_complete = not scan.get("partial") and not sum(skipped.values())
    findings, caveats = {}, {}
    if engine == "Godot":
        findings["missing_resources"] = results["missing_resources"]["count"]
        findings["main_scene_missing"] = 0 if results["build_settings"]["ok"] else 1
        findings["missing_autoloads"] = len(results["autoloads"]["missing"])
        findings["undecompiled_scripts"] = results["syntax"]["undecompiled_gdc"]
        findings["decompiler_error_markers"] = results["syntax"]["decompiler_error_markers"]
        inventory = results["inventory"]
        nothing = not (inventory["scripts_gd"] or inventory["scripts_cs"] or inventory["scenes"])
    else:
        findings["missing_scripts"] = results["missing_scripts"]["count"]
        findings["duplicate_types"] = results["duplicate_types"]["count"]
        findings["unbalanced_braces"] = results["syntax"]["unbalanced_braces"]
        findings["decompiler_error_markers"] = results["syntax"]["decompiler_error_markers"]
        findings["build_scene_problem"] = 0 if results["build_settings"]["ok"] else 1
        findings["missing_packages"] = len(results["packages"]["needed_but_missing"])
        caveats["unresolved_script_refs"] = results["unresolved_script_guids"]["count"]
        if "logic_gap" in results and "classes_without_source" in results["logic_gap"]:
            caveats["il2cpp_classes_without_source"] = results["logic_gap"]["classes_without_source"]
        nothing = results["inventory"]["scripts"] == 0
    total = sum(findings.values())
    if total:
        level = "L1 audit partial"
    elif not scan_complete:
        level = "L1 audit incomplete"
    elif nothing:
        level = "L0 extracted (nothing to audit)"
    elif any(caveats.values()):
        level = "L1 audit clean with caveats"
    else:
        level = "L1 audit clean"
    return {"level": level, "findings": findings, "total": total, "caveats": caveats,
            "scan_complete": scan_complete, "skipped": skipped}


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _valid(report):
    """Only a file with the studio's schema counts; anything else (an agent's file, a damaged or
    hand-edited one) is ignored instead of trusted or crashed on."""
    return (isinstance(report, dict) and report.get("schema") == SCHEMA and isinstance(report.get("target"), str)
            and isinstance(report.get("summary"), dict) and isinstance(report["summary"].get("findings"), dict)
            and isinstance(report["summary"].get("total"), int) and isinstance(report.get("results"), dict))


def _load_valid(path):
    report = _load(path)
    return report if _valid(report) else None


def compare(before, after):
    """Per-finding change between two audits (negative is better)."""
    if not (_valid(before) and _valid(after)):
        return None
    keys = sorted(set(before["summary"]["findings"]) | set(after["summary"]["findings"]))
    rows = []
    for key in keys:
        b, a = before["summary"]["findings"].get(key, 0), after["summary"]["findings"].get(key, 0)
        rows.append({"finding": key, "before": b, "after": a, "change": a - b})
    return {"before_target": before["target"], "after_target": after["target"], "rows": rows,
            "before_total": before["summary"]["total"], "after_total": after["summary"]["total"],
            "before_level": before["summary"]["level"], "after_level": after["summary"]["level"]}


def _markdown(report, comparison):
    s = report["summary"]
    lines = [f"# Studio audit: {report['target']}", "", f"- Engine: {report['engine']}", f"- Date: {report['created']}",
             f"- **Level: {s['level']}**", "",
             "This audit only reads files. It cannot show that the project compiles, that a scene loads, or that "
             "the game plays; open the project in the editor to check those.", ""]
    if not s.get("scan_complete", True):
        lines += ["**The scan was not complete** (it hit its time limit or skipped files it could not read: "
                  f"{s.get('skipped') or 'time limit'}), so the counts below may be too low.", ""]
    if "error" in report["results"]:
        lines.append(f"Not audited: {report['results']['error']}")
    else:
        lines += ["## Findings", "", "| Finding | Count |", "|---|---|"]
        lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in s["findings"].items()]
        if s.get("caveats"):
            lines += ["", "## Caveats (not counted as defects)", "", "| Caveat | Count |", "|---|---|"]
            lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in s["caveats"].items()]
        lines.append("")
        for name, data in report["results"].items():
            if isinstance(data, dict):
                lines += [f"## {name.replace('_', ' ')}", "", "```json", json.dumps(data, indent=1)[:3000], "```", ""]
    if comparison:
        lines += ["## Before and after", "", f"{comparison['before_target']} ({comparison['before_level']}) -> "
                  f"{comparison['after_target']} ({comparison['after_level']})", "",
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
        engine = (marker.get("engine") if isinstance(marker, dict) else "") or (
            "Godot" if os.path.exists(os.path.join(project, "project.godot")) else "Unity")
    scan = Scan(seconds)
    if not os.path.isdir(project):
        results = {"error": f"{target} does not exist."}
    elif engine == "Godot":
        results = _audit_godot(project, work_folder, scan)
    else:
        results = _audit_unity(project, work_folder, scan)
    results["scan"] = {"partial": scan.hit, "skipped": scan.skipped}
    report = {"schema": SCHEMA, "engine": engine, "target": target,
              "created": datetime.datetime.now().isoformat(timespec="seconds"),
              "partial": scan.hit, "results": results, "summary": _summary(engine, results),
              "fingerprint": tree_fingerprint(project) if os.path.isdir(project) else None}
    with open(os.path.join(reports, f"STUDIO_AUDIT_{target}.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)
    comparison = compare(_load_valid(os.path.join(reports, "STUDIO_AUDIT_05_project.json")),
                         _load_valid(os.path.join(reports, "STUDIO_AUDIT_06_playable.json")))
    latest = dict(report, comparison=comparison)
    with open(os.path.join(reports, "STUDIO_AUDIT.json"), "w", encoding="utf-8") as handle:
        json.dump(latest, handle, indent=1)
    with open(os.path.join(reports, "STUDIO_AUDIT.md"), "w", encoding="utf-8") as handle:
        handle.write(_markdown(report, comparison))
    return latest


def load_latest(work_folder):
    """The latest studio audit, or None. 'stale' is true when the audited files have changed since."""
    report = _load_valid(os.path.join(work_folder, "reports", "STUDIO_AUDIT.json"))
    if not report:
        return None
    report["stale"] = False
    saved = report.get("fingerprint")
    project = os.path.join(work_folder, report["target"])
    if saved and os.path.isdir(project):
        current = tree_fingerprint(project)
        report["stale"] = bool(current) and current["sha256"] != saved.get("sha256")
    return report
