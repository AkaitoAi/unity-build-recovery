"""Which tools an engine needs, which of them are missing, and what a download would involve.

The studio never downloads anything by itself. When tools are missing, the server answers a
recovery request with "needs_tools" and the details below, and the page asks the user before
it starts any download. Downloads go through tool_updates.install, which requires a SHA-256
digest published with the release and checks it before anything is installed.
"""

import os

import tool_updates
from paths import TOOLS_ROOT
from scanner import SEVEN_ZIP


def _tool(name: str):
    return next((t for t in tool_updates.TOOLS if t["name"] == name), None)


def required_tools(info: dict) -> list:
    """Tool names an engine needs. A "manual" tool cannot be downloaded by the studio."""
    engine = info.get("engine", "")
    if engine == "Godot":
        return [{"tool": "GDRE Tools"}]
    if engine == "Unity":
        needed = [{"tool": "7-Zip", "manual": True}, {"tool": "AssetRipper"}]
        if info.get("has_il2cpp") and not _il2cpp_tool_present():
            # Either one is enough: Il2CppDumper, or Cpp2IL (which handles newer metadata).
            needed.append({"tool": "Cpp2IL"})
        return needed
    return []


def _il2cpp_tool_present() -> bool:
    if os.path.exists(os.path.join(TOOLS_ROOT, "Il2CppDumper", "Il2CppDumper.exe")):
        return True
    folder = os.path.join(TOOLS_ROOT, "Cpp2IL")
    return os.path.isdir(folder) and any(f.lower().endswith(".exe") for f in os.listdir(folder))


def _present(name: str) -> bool:
    if name == "7-Zip":
        return os.path.exists(SEVEN_ZIP)
    tool = _tool(name)
    return bool(tool and os.path.exists(tool["marker"]))


def describe(name: str) -> dict:
    """What the user is asked to approve for one tool: source, version, size, checksum."""
    tool = _tool(name)
    base = {"name": name, "manual": False, "can_install": False, "url": "", "release_url": "",
            "version": "", "size_mb": 0, "sha256": "", "note": ""}
    if not tool:
        base.update(manual=True, note="Unknown tool.")
        return base
    base["role"] = tool.get("role", "")
    try:
        latest = tool_updates._newest_release(tool["repo"], tool.get("asset_pattern"), tool.get("stable_only", False))
    except Exception as exc:
        base["note"] = f"Could not look up the release ({type(exc).__name__}). Install it by hand."
        base["release_url"] = f"https://github.com/{tool['repo']}/releases"
        return base
    base.update(version=latest["tag"], url=latest.get("download", ""), release_url=latest["url"],
                size_mb=latest.get("size_mb", 0), sha256=latest.get("sha256", ""),
                download_name=latest.get("download_name", ""))
    if not latest.get("download"):
        base["note"] = "The release has no Windows download. Install it by hand."
    elif not latest.get("sha256"):
        base["note"] = "The release publishes no SHA-256, so the download cannot be verified. Install it by hand."
    else:
        base["can_install"] = True
    return base


def missing_tools(info: dict) -> list:
    """Details for every required tool that is not installed yet."""
    out = []
    for need in required_tools(info):
        name = need["tool"]
        if _present(name):
            continue
        if need.get("manual"):
            out.append({"name": name, "manual": True, "can_install": False, "url": "https://www.7-zip.org/",
                        "note": f"Install 7-Zip so that 7z.exe is at {SEVEN_ZIP}.", "release_url": "",
                        "version": "", "size_mb": 0, "sha256": ""})
        else:
            out.append(describe(name))
    return out
