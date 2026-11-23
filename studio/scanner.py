"""
Scanner module for Unity & Game Recovery Studio.
Inspects APK, XAPK, ZIP containers, or folder builds completely offline.
Identifies Engine Type (Unity IL2CPP, Unity Mono, Capacitor/Web, Godot, Native), Unity Version, ABIs, and Package Metadata.
"""

import os
import re
import json
import zipfile
import io
import hashlib
from typing import Dict, Any, List

from paths import TOOLS_ROOT
SEVEN_ZIP = os.path.join(TOOLS_ROOT, "7-Zip", "7z.exe")

def calculate_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()

def inspect_game(package_path: str) -> Dict[str, Any]:
    info = {
        "path": package_path,
        "name": os.path.splitext(os.path.basename(package_path))[0],
        "size_mb": round(os.path.getsize(package_path) / (1024 * 1024), 2) if os.path.isfile(package_path) else 0,
        "sha256": calculate_sha256(package_path) if os.path.isfile(package_path) else "",
        "format": "Unknown",
        "engine": "Unknown",
        "backend": "Unknown",
        "unity_version": "N/A",
        "app_id": "",
        "app_name": "",
        "abis": [],
        "has_il2cpp": False,
        "has_mono": False,
        "has_web_assets": False,
        "web_files_count": 0,
        "il2cpp_so_path": "",
        "metadata_path": "",
        "inner_apks": [],
        "total_files": 0
    }

    if not os.path.exists(package_path):
        info["error"] = "Path does not exist"
        return info

    ext = os.path.splitext(package_path)[1].lower()

    if os.path.isdir(package_path):
        info["format"] = "Directory / Build Folder"
        inspect_directory(package_path, info)
    elif ext in [".xapk", ".zip", ".apk"]:
        inspect_archive_package(package_path, ext, info)

    return info

def inspect_archive_package(archive_path: str, ext: str, info: Dict[str, Any]):
    try:
        with zipfile.ZipFile(archive_path, 'r') as z:
            names = z.namelist()
            info["total_files"] = len(names)

            if ext == ".xapk" or any(n.endswith(".apk") for n in names):
                info["format"] = "XAPK / Split APKs Container"
                apk_names = [n for n in names if n.endswith(".apk")]
                info["inner_apks"] = apk_names

                # Check manifest.json if present
                if "manifest.json" in names:
                    try:
                        m_data = json.loads(z.read("manifest.json").decode("utf-8", errors="ignore"))
                        info["app_id"] = m_data.get("package_name", "")
                        info["app_name"] = m_data.get("name", "")
                        info["app_version"] = m_data.get("version_name", "")
                    except Exception:
                        pass

                # Inspect inside each nested APK
                for apk in apk_names:
                    try:
                        apk_data = z.read(apk)
                        with zipfile.ZipFile(io.BytesIO(apk_data), 'r') as sub_z:
                            sub_names = sub_z.namelist()
                            analyze_file_list(sub_names, sub_z, info)
                    except Exception:
                        pass
            else:
                info["format"] = "Android APK"
                analyze_file_list(names, z, info)

    except Exception as e:
        info["error"] = str(e)

def analyze_file_list(names: List[str], z_obj: zipfile.ZipFile, info: Dict[str, Any]):
    # 1. Detect ABIs
    abis = set(info.get("abis", []))
    for n in names:
        m = re.search(r"lib/(arm64-v8a|armeabi-v7a|x86_64|x86)/", n)
        if m:
            abis.add(m.group(1))
    info["abis"] = sorted(list(abis))

    # 2. Check for Unity IL2CPP
    for n in names:
        if "libil2cpp.so" in n:
            info["has_il2cpp"] = True
            info["engine"] = "Unity"
            info["backend"] = "IL2CPP (AOT Native)"
            info["il2cpp_so_path"] = n
        if "global-metadata.dat" in n:
            info["metadata_path"] = n

    # 3. Check for Unity Mono
    for n in names:
        if "Assembly-CSharp.dll" in n:
            info["has_mono"] = True
            info["engine"] = "Unity"
            info["backend"] = "Mono (Managed Bytecode)"

    # 4. Check for Unity Version
    if info["engine"] == "Unity" and info["unity_version"] == "N/A":
        for n in names:
            if n.endswith("unity default resources") or n.endswith("boot.config"):
                try:
                    data = z_obj.read(n)
                    m = re.search(rb"(\d{4}\.\d+\.\d+[a-z0-9]*)", data[:4096])
                    if m:
                        info["unity_version"] = m.group(1).decode("ascii", errors="ignore")
                        break
                except Exception:
                    pass

    # 5. Check for Capacitor / Cordova / Web Game
    if "assets/capacitor.config.json" in names or "assets/public/index.html" in names or "assets/www/index.html" in names:
        info["engine"] = "Capacitor / HTML5 Hybrid"
        info["backend"] = "JavaScript / TypeScript Web Engine"
        info["has_web_assets"] = True
        web_files = [n for n in names if n.startswith("assets/public") or n.startswith("assets/www") or n.endswith(".js")]
        info["web_files_count"] = max(info.get("web_files_count", 0), len(web_files))
        if "assets/capacitor.config.json" in names:
            try:
                cfg = json.loads(z_obj.read("assets/capacitor.config.json").decode("utf-8", errors="ignore"))
                info["app_id"] = cfg.get("appId", info.get("app_id", ""))
                info["app_name"] = cfg.get("appName", info.get("app_name", ""))
            except Exception:
                pass

    if info["engine"] == "Unknown":
        if any(n.endswith(".dex") for n in names):
            info["engine"] = "Native Android (Java/Kotlin)"
            info["backend"] = "Dalvik / ART Bytecode"

def inspect_directory(dir_path: str, info: Dict[str, Any]):
    for root, dirs, files in os.walk(dir_path):
        for f in files:
            p = os.path.join(root, f)
            if f == "libil2cpp.so":
                info["has_il2cpp"] = True
                info["engine"] = "Unity"
                info["backend"] = "IL2CPP"
                info["il2cpp_so_path"] = p
            elif f == "global-metadata.dat":
                info["metadata_path"] = p
            elif f == "Assembly-CSharp.dll":
                info["has_mono"] = True
                info["engine"] = "Unity"
                info["backend"] = "Mono"
            elif f == "capacitor.config.json":
                info["engine"] = "Capacitor / Web"
                info["backend"] = "Web"
            elif f == "unity default resources":
                try:
                    with open(p, "rb") as udr:
                        m = re.search(rb"(\d{4}\.\d+\.\d+[a-z0-9]*)", udr.read(4096))
                        if m:
                            info["unity_version"] = m.group(1).decode("ascii", errors="ignore")
                except Exception:
                    pass
