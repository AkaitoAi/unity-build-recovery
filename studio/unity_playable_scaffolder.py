"""
Unity project scaffolder and SDK stubber.
Wraps recovered assets in a Unity project folder (ProjectVersion, Packages/manifest.json, an
assembly definition) and adds placeholder types for common ad and analytics SDKs. It does not
compile the project, so errors can remain after it runs.
"""

import os
import re
import json
import shutil
from typing import List, Dict, Any

def _discover_editors():
    """Editors installed through Unity Hub, newest version string first."""
    import glob
    found = []
    for exe in glob.glob(r"C:\Program Files\Unity\Hub\Editor\*\Editor\Unity.exe"):
        version = os.path.basename(os.path.dirname(os.path.dirname(exe)))
        found.append((version, exe))
    return sorted(found, reverse=True)


INSTALLED_EDITORS = _discover_editors()

SDK_STUBS_CODE = {
    "GoogleMobileAds": """// Auto-generated SDK Stub for Unity Placeholder
namespace GoogleMobileAds.Api
{
    public class MobileAds
    {
        public static void Initialize(System.Action<object> callback) { callback?.Invoke(null); }
        public static void SetApplicationVolume(float volume) {}
        public static void SetApplicationMuted(bool muted) {}
    }
    public class BannerView { public BannerView(string id, object size, object pos) {} public void LoadAd(object req) {} public void Show() {} public void Hide() {} public void Destroy() {} }
    public class InterstitialAd { public static void Load(string id, object req, System.Action<InterstitialAd, object> cb) { cb?.Invoke(new InterstitialAd(), null); } public void Show() {} public bool CanShowAd() => true; public void Destroy() {} }
    public class RewardedAd { public static void Load(string id, object req, System.Action<RewardedAd, object> cb) { cb?.Invoke(new RewardedAd(), null); } public void Show(System.Action<object> userEarnedRewardCallback) { userEarnedRewardCallback?.Invoke(null); } public bool CanShowAd() => true; public void Destroy() {} }
    public class AdRequest { public class Builder { public AdRequest Build() => new AdRequest(); } }
    public enum AdPosition { Top, Bottom, TopLeft, TopRight, BottomLeft, BottomRight, Center }
    public class AdSize { public static AdSize Banner = new AdSize(); public static AdSize MediumRectangle = new AdSize(); }
}
""",
    "AppLovinMax": """// Auto-generated AppLovin Max Stub for Unity Placeholder
public class MaxSdk
{
    public static void InitializeSdk(string key = "") {}
    public static void SetSdkKey(string key) {}
    public static void SetUserId(string id) {}
    public static void ShowBanner(string adUnitId) {}
    public static void HideBanner(string adUnitId) {}
    public static void ShowInterstitial(string adUnitId) {}
    public static bool IsInterstitialReady(string adUnitId) => true;
    public static void ShowRewardedAd(string adUnitId) {}
    public static bool IsRewardedAdReady(string adUnitId) => true;
    public static void CreateBanner(string adUnitId, object pos) {}
    public class BannerPosition { public const string BottomCenter = "BottomCenter"; public const string TopCenter = "TopCenter"; }
}
""",
    "Firebase": """// Auto-generated Firebase Stub for Unity Placeholder
namespace Firebase
{
    public class FirebaseApp
    {
        public static FirebaseApp DefaultInstance => new FirebaseApp();
        public static System.Threading.Tasks.Task<DependencyStatus> CheckAndFixDependenciesAsync() => System.Threading.Tasks.Task.FromResult(DependencyStatus.Available);
    }
    public enum DependencyStatus { Available, UnavailableOther }
}
namespace Firebase.Analytics
{
    public class FirebaseAnalytics
    {
        public static void LogEvent(string name) {}
        public static void LogEvent(string name, string paramName, object paramValue) {}
        public static void SetUserId(string userId) {}
    }
}
""",
    "Facebook": """// Auto-generated Facebook SDK Stub for Unity Placeholder
namespace Facebook.Unity
{
    public class FB
    {
        public static bool IsInitialized => true;
        public static bool IsLoggedIn => true;
        public static void Init(System.Action onInitComplete = null, string appId = null) { onInitComplete?.Invoke(); }
        public static void ActivateApp() {}
        public static void LogAppEvent(string logEvent, float? valueToSum = null, System.Collections.Generic.Dictionary<string, object> parameters = null) {}
    }
}
""",
    "GameAnalytics": """// Auto-generated GameAnalytics Stub for Unity Placeholder
namespace GameAnalyticsSDK
{
    public class GameAnalytics
    {
        public static void Initialize() {}
        public static void NewProgressionEvent(string status, string world, string level = "") {}
        public static void NewDesignEvent(string eventName, float val = 0) {}
        public static void NewErrorEvent(object severity, string msg) {}
    }
}
"""
}

def detect_matching_installed_unity(target_ver: str):
    """Returns (version, exact). Prefers the exact editor, then the same major.minor line.

    When neither is installed the newest installed editor is returned with exact=False so the
    caller can warn that Unity will upgrade the project on open. Returns ("", False) when no
    editor is installed.
    """
    target = (target_ver or "").strip()
    for ver, _path in INSTALLED_EDITORS:
        if target and ver == target:
            return ver, True
    line = ".".join(target.split(".")[:2]) if re.match(r"^\d+\.\d+", target) else ""
    if line:
        for ver, _path in INSTALLED_EDITORS:
            if ver.startswith(line + "."):
                return ver, False
    if INSTALLED_EDITORS:
        return INSTALLED_EDITORS[0][0], False
    return "", False

def scaffold_unity_project(project_dir: str, detected_unity_ver: str, game_title: str) -> Dict[str, Any]:
    """Scaffolds a Unity project folder around recovered assets. Does not verify compilation."""
    results = {"editor_version": "", "version_exact": False, "packages_added": [], "stubs_created": []}

    assets_dir = os.path.join(project_dir, "Assets")
    project_settings = os.path.join(project_dir, "ProjectSettings")
    packages_dir = os.path.join(project_dir, "Packages")
    scripts_dir = os.path.join(assets_dir, "_Project", "Scripts")
    stubs_dir = os.path.join(scripts_dir, "Stubs")

    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(project_settings, exist_ok=True)
    os.makedirs(packages_dir, exist_ok=True)
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(stubs_dir, exist_ok=True)

    # 1. ProjectVersion.txt
    matched_ver, exact = detect_matching_installed_unity(detected_unity_ver)
    results["editor_version"] = matched_ver
    results["version_exact"] = exact
    pv_path = os.path.join(project_settings, "ProjectVersion.txt")
    if not os.path.exists(pv_path) and matched_ver:  # a version recovered with the assets wins
        with open(pv_path, "w", encoding="utf-8") as f:
            f.write(f"m_EditorVersion: {matched_ver}\nm_EditorVersionWithRevision: {matched_ver}\n")

    # 2. Packages/manifest.json
    manifest_path = os.path.join(packages_dir, "manifest.json")
    editor_major = int(matched_ver.split(".")[0]) if matched_ver[:1].isdigit() else 2022
    UGUI_VERSION = "2.0.0" if editor_major >= 6000 else "1.0.0"
    manifest_data = {
        "dependencies": {
            "com.unity.collab-proxy": "2.4.4",
            "com.unity.feature.development": "1.0.1",
            "com.unity.ide.visualstudio": "2.0.22",
            "com.unity.ide.rider": "3.0.31",
            "com.unity.inputsystem": "1.7.0",
            "com.unity.textmeshpro": "3.0.9",
            "com.unity.timeline": "1.7.6",
            "com.unity.ugui": UGUI_VERSION,
            "com.unity.modules.ai": "1.0.0",
            "com.unity.modules.animation": "1.0.0",
            "com.unity.modules.audio": "1.0.0",
            "com.unity.modules.physics": "1.0.0",
            "com.unity.modules.physics2d": "1.0.0",
            "com.unity.modules.ui": "1.0.0"
        }
    }
    if os.path.exists(manifest_path):
        # Unity or the rip already wrote one: add only the packages it lacks.
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                existing_manifest = json.load(f)
            deps = existing_manifest.setdefault("dependencies", {})
            for key, value in manifest_data["dependencies"].items():
                deps.setdefault(key, value)
            manifest_data = existing_manifest
        except Exception:
            pass
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # 3. Create GameCore.asmdef for clean compilation
    asmdef_path = os.path.join(scripts_dir, "GameCore.asmdef")
    asmdef_data = {
        "name": "GameCore",
        "rootNamespace": "",
        "references": [
            "Unity.TextMeshPro",
            "UnityEngine.UI",
            "Unity.InputSystem"
        ],
        "includePlatforms": [],
        "excludePlatforms": [],
        "allowUnsafeCode": True,
        "overrideReferences": False,
        "precompiledReferences": [],
        "autoReferenced": True,
        "defineConstraints": [],
        "versionDefines": [],
        "noEngineReferences": False
    }
    with open(asmdef_path, "w", encoding="utf-8") as f:
        json.dump(asmdef_data, f, indent=2)

    # 4. Generate all SDK stubs so missing namespaces never break Play mode
    existing = set()
    for walk_root, walk_dirs, walk_files in os.walk(assets_dir):
        if os.path.relpath(walk_root, assets_dir).startswith("_Project"):
            continue
        for entry in walk_dirs + walk_files:
            existing.add(entry.lower())
    for stub_name, stub_code in SDK_STUBS_CODE.items():
        if any(stub_name.lower() in name for name in existing):
            continue  # the game already ships this SDK; a stub would duplicate its types
        stub_file = os.path.join(stubs_dir, f"{stub_name}Stub.cs")
        with open(stub_file, "w", encoding="utf-8") as f:
            f.write(stub_code)
        results["stubs_created"].append(f"{stub_name}Stub.cs")

    return results

def setup_playable_web_runner(output_dir: str, web_dir: str, game_title: str) -> str:
    """Writes a batch file that serves web_dir on 127.0.0.1:8088. Needs Python on PATH.

    The batch file goes in output_dir (beside the Unity project, not inside it) and serves
    the folder named web_dir.
    """
    safe_title = re.sub(r"[^A-Za-z0-9 ._-]", "", game_title or "game")[:60] or "game"
    folder = os.path.basename(os.path.normpath(web_dir))
    runner_bat = os.path.join(output_dir, "Play_Game_Offline.bat")
    # Never "cd" into the game folder or search PATH for python: cmd.exe looks in the current
    # folder first, so a python.exe inside a recovered game would run instead of Python. The
    # interpreter is the studio's own, by full path, and the game folder is only passed to it
    # as the directory to serve.
    python = os.path.join(os.path.dirname(sys.executable), "python.exe")
    if not (os.path.isfile(python) and python.isascii() and not any(c in python for c in '%"^&|<>!')):
        python = ""
    interpreter = f'"{python}"' if python else '"%SystemRoot%\\py.exe" -3'
    code = (
        "@echo off\r\n"
        f"title Playing {safe_title}\r\n"
        'cd /d "%SystemRoot%"\r\n'
        "echo Serving on http://127.0.0.1:8088 (close this window to stop)\r\n"
        'start "" "http://127.0.0.1:8088"\r\n'
        f'{interpreter} -m http.server 8088 --bind 127.0.0.1 --directory "%~dp0{folder}"\r\n'
        "pause\r\n"
    )
    with open(runner_bat, "w", encoding="ascii", newline="") as f:
        f.write(code)
    return runner_bat
