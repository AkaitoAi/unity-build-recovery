"""
AI Script Selector & Semantic Classifier Module.
Analyzes recovered decompiled C# (or JS/TS) scripts 100% offline.
Categorizes scripts into Core Gameplay, UI, Systems, Plugins, Ads/Telemetry, and Obfuscated Internals.
Allows filtering, search, and 1-click export to target Unity project.
"""

import os
import re
import shutil
from typing import List, Dict, Any, Tuple

# Semantic rule dictionaries
CATEGORY_RULES = {
    "Ad & Tracking SDKs": {
        "keywords": [
            "admob", "googlemobileads", "applovin", "maxsdk", "ironsource", "unityads",
            "firebase", "crashlytics", "analytics", "gameanalytics", "facebook", "fb",
            "adjust", "appsflyer", "moloco", "pangle", "bytedance", "vungle", "mintegral",
            "chartboost", "singular", "kochava", "tapjoy", "adcolony", "inmobi", "fyber",
            "branch", "attribution", "telemetry", "tracking", "omidsession", "mraid"
        ],
        "default_action": "Ignore",
        "default_selected": False,
        "badge": "🗑️"
    },
    "Internal / Compiler Generated": {
        "keywords": [
            "<privateimplementationdetails>", "<>c", "<>f__anonymoustype", "closure",
            "costura", "costura.", "dummyassembly", "_privateimplementation", "assembly-csharp-firstpass"
        ],
        "default_action": "Ignore",
        "default_selected": False,
        "badge": "⚙️"
    },
    "Core Gameplay": {
        "keywords": [
            "player", "enemy", "character", "hero", "level", "stage", "puzzle", "sort",
            "toy", "board", "grid", "tile", "block", "item", "inventory", "score",
            "coin", "gem", "currency", "weapon", "spawner", "spawn", "gamemanager",
            "match", "win", "lose", "gameover", "complete", "mission", "quest",
            "collect", "pickup", "drop", "drag", "touch", "click", "interact",
            "movement", "motor", "health", "damage", "combat", "obstacle", "rule"
        ],
        "default_action": "Keep",
        "default_selected": True,
        "badge": "🎮"
    },
    "UI & Presentation": {
        "keywords": [
            "ui", "menu", "canvas", "panel", "screen", "view", "window", "dialog",
            "popup", "button", "text", "image", "icon", "slider", "toggle", "hud",
            "shop", "store", "leaderboard", "pause", "resume", "settingsui", "victoryui",
            "failureui", "rewardui", "tooltip", "navigation", "animationui", "feedback"
        ],
        "default_action": "Keep",
        "default_selected": True,
        "badge": "🎯"
    },
    "Game Systems & Audio": {
        "keywords": [
            "audio", "sound", "music", "sfx", "camera", "cam", "follow", "cinemachine",
            "input", "controller", "physics", "collision", "trigger", "save", "data",
            "storage", "preference", "scenemanager", "sceneloader", "statemachine", "state",
            "event", "observer", "pool", "objectpool", "singleton", "utility", "helper"
        ],
        "default_action": "Keep",
        "default_selected": True,
        "badge": "🧩"
    },
    "Plugins & Store Packages": {
        "keywords": [
            "dotween", "leantween", "invector", "rcc", "realisticcarcontroller", "easyroads",
            "astar", "pathfinding", "spine", "textmeshpro", "tmp", "odin", "sirenix",
            "demigiant", "naughtyattributes", "animancer", "rewired", "photon", "mirror"
        ],
        "default_action": "Keep",
        "default_selected": True,
        "badge": "📦"
    }
}

class ScriptItem:
    def __init__(self, filepath: str, root_dir: str):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.relpath = os.path.relpath(filepath, root_dir)
        self.name_no_ext = os.path.splitext(self.filename)[0]
        self.ext = os.path.splitext(self.filename)[1].lower()
        self.category = "Core Gameplay"
        self.confidence = 50
        self.lines = 0
        self.methods_count = 0
        self.namespace = ""
        self.base_classes = []
        self.is_selected = True
        self.badge = "🎮"
        self.recommendation = "Keep"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name_no_ext,
            "filename": self.filename,
            "path": self.filepath,
            "relpath": self.relpath,
            "category": self.category,
            "confidence": f"{self.confidence}%",
            "lines": self.lines,
            "methods": self.methods_count,
            "namespace": self.namespace,
            "base_classes": ", ".join(self.base_classes),
            "is_selected": self.is_selected,
            "badge": self.badge,
            "recommendation": self.recommendation
        }

def analyze_script(item: ScriptItem) -> ScriptItem:
    try:
        with open(item.filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        lines = content.splitlines()
        item.lines = len(lines)

        # Parse namespace
        ns_match = re.search(r"namespace\s+([\w\.]+)", content)
        if ns_match:
            item.namespace = ns_match.group(1)

        # Parse class inheritance
        cls_match = re.search(r"class\s+\w+\s*:\s*([\w\s,]+)\{?", content)
        if cls_match:
            bases = [b.strip() for b in cls_match.group(1).split(",")]
            item.base_classes = [b for b in bases if b and not b.startswith("//")]

        # Count methods
        methods = re.findall(r"(public|private|protected|internal)\s+(static\s+)?(void|bool|int|string|float|Task|IEnumerator|\w+)\s+\w+\s*\(", content)
        item.methods_count = len(methods)

        # Score categories
        lower_content = (item.filename + " " + item.namespace + " " + content[:2048]).lower()

        scores = {}
        for cat, rule in CATEGORY_RULES.items():
            score = 0
            for kw in rule["keywords"]:
                if kw in item.filename.lower():
                    score += 15
                if kw in item.namespace.lower():
                    score += 12
                if kw in lower_content:
                    score += 3
            scores[cat] = score

        # Determine highest scoring category
        best_cat = "Core Gameplay"
        best_score = 0
        for cat, score in scores.items():
            if score > best_score:
                best_score = score
                best_cat = cat

        if best_score == 0:
            # Fallback heuristic: MonoBehaviour default to Core Gameplay
            if any("monobehaviour" in b.lower() for b in item.base_classes):
                best_cat = "Core Gameplay"
                best_score = 10
            else:
                best_cat = "Game Systems & Audio"
                best_score = 5

        item.category = best_cat
        rule = CATEGORY_RULES.get(best_cat, CATEGORY_RULES["Core Gameplay"])
        item.badge = rule["badge"]
        item.recommendation = rule["default_action"]
        item.is_selected = rule["default_selected"]
        item.confidence = min(98, 40 + best_score * 2)

    except Exception:
        item.category = "Core Gameplay"
        item.confidence = 40

    return item

def scan_and_classify_directory(scripts_dir: str) -> List[ScriptItem]:
    """Scans a directory of recovered scripts and runs offline semantic classification."""
    results = []
    if not os.path.exists(scripts_dir):
        return results

    supported_exts = {".cs", ".js", ".ts", ".java"}

    for root, dirs, files in os.walk(scripts_dir):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in supported_exts:
                full_path = os.path.join(root, f)
                item = ScriptItem(full_path, scripts_dir)
                analyze_script(item)
                results.append(item)

    # Sort: Core Gameplay first, then UI, then Systems
    category_order = {
        "Core Gameplay": 0,
        "UI & Presentation": 1,
        "Game Systems & Audio": 2,
        "Plugins & Store Packages": 3,
        "Internal / Compiler Generated": 4,
        "Ad & Tracking SDKs": 5
    }
    results.sort(key=lambda x: (category_order.get(x.category, 99), x.filename.lower()))
    return results

def export_selected_scripts(items: List[ScriptItem], target_scripts_dir: str) -> Tuple[int, int]:
    """Exports all selected scripts into target directory."""
    os.makedirs(target_scripts_dir, exist_ok=True)
    exported = 0
    skipped = 0

    for item in items:
        if not item.is_selected:
            skipped += 1
            continue

        cat_folder = re.sub(r"[^\w\s-]", "", item.category).strip().replace(" ", "_")
        dest_folder = os.path.join(target_scripts_dir, cat_folder)
        os.makedirs(dest_folder, exist_ok=True)

        dest_file = os.path.join(dest_folder, item.filename)
        shutil.copy2(item.filepath, dest_file)
        exported += 1

    return exported, skipped
