"""
Unity Playable Scaffolder & AutoSDKStubber.
Ensures that any recovered game project (Unity IL2CPP/Mono or Web/Capacitor) opens in Unity Editor
with ZERO compilation errors and enters Playable condition.
"""

import os
import re
import json
import shutil
from typing import List, Dict, Any

INSTALLED_EDITORS = [
    ("6000.3.21f1", r"C:\Program Files\Unity\Hub\Editor\6000.3.21f1\Editor\Unity.exe"),
    ("2022.3.62f3", r"C:\Program Files\Unity\Hub\Editor\2022.3.62f3\Editor\Unity.exe"),
    ("6000.0.60f1", r"C:\Program Files\Unity\Hub\Editor\6000.0.60f1\Editor\Unity.exe"),
    ("6000.3.0f1", r"C:\Program Files\Unity\Hub\Editor\6000.3.0f1\Editor\Unity.exe")
]

SDK_STUBS_CODE = {
    "GoogleMobileAds": """// Auto-generated SDK Stub for Unity Zero-Error Compilation
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
    "AppLovinMax": """// Auto-generated AppLovin Max Stub for Unity Zero-Error Compilation
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
    "Firebase": """// Auto-generated Firebase Stub for Unity Zero-Error Compilation
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
    "Facebook": """// Auto-generated Facebook SDK Stub for Unity Zero-Error Compilation
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
    "GameAnalytics": """// Auto-generated GameAnalytics Stub for Unity Zero-Error Compilation
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

def detect_matching_installed_unity(target_ver: str) -> str:
    """Finds the best matching installed Unity editor version."""
    for ver, path in INSTALLED_EDITORS:
        if os.path.exists(path):
            if target_ver and target_ver in ver:
                return ver
    # Default to 2022.3.62f3 or first existing
    for ver, path in INSTALLED_EDITORS:
        if os.path.exists(path):
            return ver
    return "2022.3.62f3"

def scaffold_unity_project(project_dir: str, detected_unity_ver: str, game_title: str) -> Dict[str, Any]:
    """Scaffolds a clean, compile-ready Unity project structure."""
    results = {"editor_version": "", "packages_added": [], "stubs_created": []}

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
    matched_ver = detect_matching_installed_unity(detected_unity_ver)
    results["editor_version"] = matched_ver
    pv_path = os.path.join(project_settings, "ProjectVersion.txt")
    with open(pv_path, "w", encoding="utf-8") as f:
        f.write(f"m_EditorVersion: {matched_ver}\nm_EditorVersionWithRevision: {matched_ver}\n")

    # 2. Packages/manifest.json
    manifest_path = os.path.join(packages_dir, "manifest.json")
    manifest_data = {
        "dependencies": {
            "com.unity.collab-proxy": "2.4.4",
            "com.unity.feature.development": "1.0.1",
            "com.unity.ide.visualstudio": "2.0.22",
            "com.unity.ide.rider": "3.0.31",
            "com.unity.inputsystem": "1.7.0",
            "com.unity.textmeshpro": "3.0.9",
            "com.unity.timeline": "1.7.6",
            "com.unity.ugui": "2.0.0",
            "com.unity.modules.ai": "1.0.0",
            "com.unity.modules.animation": "1.0.0",
            "com.unity.modules.audio": "1.0.0",
            "com.unity.modules.physics": "1.0.0",
            "com.unity.modules.physics2d": "1.0.0",
            "com.unity.modules.ui": "1.0.0"
        }
    }
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
    for stub_name, stub_code in SDK_STUBS_CODE.items():
        stub_file = os.path.join(stubs_dir, f"{stub_name}Stub.cs")
        with open(stub_file, "w", encoding="utf-8") as f:
            f.write(stub_code)
        results["stubs_created"].append(f"{stub_name}Stub.cs")

    return results

def setup_playable_web_runner(project_dir: str, web_assets_dir: str, game_title: str) -> str:
    """Sets up an instant 1-click local web runner so HTML5/Capacitor games are instantly playable."""
    runner_bat = os.path.join(project_dir, "Play_Game_Offline.bat")
    py_server_code = f"""@echo off
title 🎮 Playing: {game_title} (Offline Web Server)
color 0A
cd /d "%~dp0"
echo ===============================================================================
echo        🎮 LAUNCHING {game_title.upper()} (100%% OFFLINE RUNNER)
echo ===============================================================================
echo.
echo Starting local offline game server on http://localhost:8088...
start "" "http://localhost:8088"
python -m http.server 8088 --bind 127.0.0.1
pause
"""
    with open(runner_bat, "w", encoding="utf-8") as f:
        f.write(py_server_code)
    return runner_bat
