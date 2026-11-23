"""
Version checking and in-place updating for the bundled reverse-engineering tools.

Compares what is installed against the newest build published on each tool's GitHub project,
and can download and install that build.

Two things worth knowing about the results:

* Being up to date is not the same as being able to read a given game. A tool at its newest
  release can still refuse a package built with a newer engine than itself, which shows up as
  the asset rip or code recovery stage producing nothing.
* Pre-releases count. Cpp2IL's newest stable is from 2022 while its pre-release line is years
  ahead and is the only one that reads recent IL2CPP metadata, so checking stable builds alone
  reports it as current when it cannot open a modern game at all.

GitHub allows 60 unauthenticated API calls an hour. Results are cached on disk so a burst of
checks does not leave the panel blank, and a rate-limited check says so rather than claiming
the tool is offline.
"""

import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile

from paths import TOOLS_ROOT
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(APP_DIR, "update_cache.json")
CACHE_SECONDS = 6 * 3600
TIMEOUT = 25

TOOLS = [
    {
        "name": "AssetRipper", "repo": "AssetRipper/AssetRipper",
        "marker": os.path.join(TOOLS_ROOT, "AssetRipper", "AssetRipper.GUI.Free.exe"),
        "stamp": os.path.join(TOOLS_ROOT, "AssetRipper", "compile_time.txt"),
        "role": "Rips Unity assets, scenes and materials",
    },
    {
        "name": "GDRE Tools", "repo": "GDRETools/gdsdecomp",
        "marker": os.path.join(TOOLS_ROOT, "gdre_tools", "gdre_tools.exe"),
        "role": "Recovers Godot projects from PCK, EXE and APK files",
        "asset_pattern": r"^GDRE_tools-v[\d.]+-windows\.zip$",
        "stable_only": True,
    },
    {
        "name": "Il2CppDumper", "repo": "Perfare/Il2CppDumper",
        "marker": os.path.join(TOOLS_ROOT, "Il2CppDumper", "Il2CppDumper.exe"),
        "role": "Recovers C# type information from IL2CPP builds",
    },
    {
        "name": "Cpp2IL", "repo": "SamboyCoding/Cpp2IL",
        "marker": os.path.join(TOOLS_ROOT, "Cpp2IL"),
        "role": "IL2CPP recovery: dummy DLLs, and ISIL dumps for ARM64",
        "newest_file_in": os.path.join(TOOLS_ROOT, "Cpp2IL"),
    },
    {
        "name": "JADX", "repo": "skylot/jadx",
        "marker": os.path.join(TOOLS_ROOT, "jadx"),
        "role": "Decompiles Android DEX to Java",
        "newest_dir_prefix": "jadx",
        "asset_pattern": r"^jadx-\d[^/]*\.zip$",  # the command-line build, not jadx-gui
    },
    {
        "name": "ILSpy", "repo": "icsharpcode/ILSpy",
        "marker": os.path.join(TOOLS_ROOT, "ILSpy", "ILSpy.exe"),
        "role": "Decompiles managed .NET assemblies",
        "newest_dir_prefix": "ilspy",
        "no_install": True,
    },
    {
        "name": "Ghidra", "repo": "NationalSecurityAgency/ghidra",
        "marker": os.path.join(TOOLS_ROOT, "ghidra"),
        "role": "Disassembles and decompiles native binaries",
        "newest_dir_prefix": "ghidra",
        "no_install": True,
    },
    {
        "name": "REA", "repo": "morluto/rea",
        "marker": os.path.join(TOOLS_ROOT, "rea-runtime", "node_modules", "rea-agents"),
        "package_json": os.path.join(TOOLS_ROOT, "rea-runtime", "node_modules", "rea-agents", "package.json"),
        "role": "Reverse-engineering MCP server shared by the agents",
        "npm": "rea-agents",
    },
]


# ----------------------------------------------------------------------------- cache

def _load_cache():
    try:
        return json.load(open(CACHE_PATH, encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache):
    try:
        json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"), indent=1)
    except Exception:
        pass


# ----------------------------------------------------------------------------- installed side

def _installed(tool):
    """What is installed: a date to compare on, and text to show."""
    package_json = tool.get("package_json")
    if package_json and os.path.exists(package_json):
        try:
            version = json.load(open(package_json, encoding="utf-8")).get("version")
            if version:
                when = datetime.date.fromtimestamp(os.path.getmtime(package_json))
                return when, f"v{version}"
        except Exception:
            pass

    stamp = tool.get("stamp")
    if stamp and os.path.exists(stamp):
        try:
            text = open(stamp, encoding="utf-8", errors="replace").read().strip()
            return datetime.datetime.strptime(text, "%a %b %d %H:%M:%S UTC %Y").date(), text
        except Exception:
            pass

    # A folder that may hold several builds: use the newest thing in it.
    folder = tool.get("newest_file_in")
    if folder and os.path.isdir(folder):
        newest = None
        for entry in os.listdir(folder):
            full = os.path.join(folder, entry)
            if os.path.isfile(full):
                when = os.path.getmtime(full)
                if newest is None or when > newest[0]:
                    newest = (when, entry)
        if newest:
            return datetime.date.fromtimestamp(newest[0]), f"{newest[1]}"

    prefix = tool.get("newest_dir_prefix")
    if prefix:
        newest = None
        for entry in (os.listdir(TOOLS_ROOT) if os.path.isdir(TOOLS_ROOT) else []):
            if entry.lower().startswith(prefix) and os.path.isdir(os.path.join(TOOLS_ROOT, entry)):
                when = os.path.getmtime(os.path.join(TOOLS_ROOT, entry))
                if newest is None or when > newest[0]:
                    newest = (when, entry)
        if newest:
            return datetime.date.fromtimestamp(newest[0]), newest[1]

    marker = tool.get("marker")
    if marker and os.path.exists(marker):
        when = datetime.date.fromtimestamp(os.path.getmtime(marker))
        return when, when.isoformat() + " (file date)"
    return None, "not installed"


# ----------------------------------------------------------------------------- published side

def _api(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "aether-recon-studio", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _newest_npm(package):
    """The newest version actually published to npm for this package."""
    request = urllib.request.Request(f"https://registry.npmjs.org/{package}/latest",
                                     headers={"User-Agent": "aether-recon-studio"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    version = data.get("version") or "?"
    return {
        "tag": f"v{version}", "published": "", "prerelease": False,
        "url": f"https://www.npmjs.com/package/{package}",
        "download": "", "download_name": "", "npm_version": version,
    }


def _newest_release(repo, asset_pattern=None, stable_only=False):
    """Newest published build, with its Windows download.

    Pre-releases count unless stable_only is set. A release without a matching download is
    skipped in favour of the next newest one that has it.
    """
    releases = _api(f"https://api.github.com/repos/{repo}/releases?per_page=20")
    if not releases:
        raise RuntimeError("no releases published")
    releases.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    if stable_only:
        releases = [r for r in releases if not r.get("prerelease") and not r.get("draft")] or releases

    def score(asset):
        name = asset.get("name", "").lower()
        if "arm64" in name or "linux" in name or "osx" in name or "mac" in name:
            return -1
        points = 0
        if "win" in name:
            points += 3
        if name.endswith(".zip"):
            points += 2
        if name.endswith(".exe"):
            points += 1
        return points

    import re as _re

    def matching(release):
        found = [a for a in release.get("assets", [])
                 if score(a) > 0 and (not asset_pattern or _re.match(asset_pattern, a.get("name", "")))]
        found.sort(key=score, reverse=True)
        return found

    newest = releases[0]
    assets = matching(newest)
    if not assets:
        for candidate in releases[1:]:
            found = matching(candidate)
            if found:
                newest, assets = candidate, found
                break
    return {
        "tag": newest.get("tag_name") or "?",
        "published": (newest.get("published_at") or "")[:10],
        "url": newest.get("html_url") or f"https://github.com/{repo}/releases",
        "prerelease": bool(newest.get("prerelease")),
        "download": assets[0]["browser_download_url"] if assets else "",
        "download_name": assets[0]["name"] if assets else "",
        "size_mb": round(assets[0].get("size", 0) / 1e6, 1) if assets else 0,
        "sha256": (assets[0].get("digest") or "").removeprefix("sha256:").lower() if assets else "",
    }


def _fixed_location(tool):
    """True when the pipeline reads the tool from one fixed folder and ignores newer installs."""
    return not (tool.get("npm") or tool.get("newest_file_in") or tool.get("newest_dir_prefix"))


def check_updates(force=False):
    """One entry per tool: what is installed, what is current, and whether to update."""
    cache = _load_cache()
    now = time.time()
    results = []

    for tool in TOOLS:
        installed_date, installed_text = _installed(tool)
        entry = {
            "name": tool["name"], "role": tool["role"], "installed": installed_text,
            "repo": tool["repo"], "latest": "", "published": "", "prerelease": False,
            "url": f"https://github.com/{tool['repo']}/releases", "download": "",
            "state": "unknown", "note": "", "can_install": False,
            "sha256": "", "size_mb": 0,
        }
        if installed_date is None:
            entry["state"] = "missing"
            entry["note"] = "Not found in the tools folder."

        cached = cache.get(tool["repo"])
        fresh = cached and not force and (now - cached.get("checked_at", 0)) < CACHE_SECONDS
        latest = None
        if fresh:
            latest = cached["release"]
        else:
            try:
                latest = _newest_npm(tool["npm"]) if tool.get("npm") else _newest_release(tool["repo"], tool.get("asset_pattern"), tool.get("stable_only", False))
                cache[tool["repo"]] = {"checked_at": now, "release": latest}
            except urllib.error.HTTPError as error:
                if error.code in (403, 429) and cached:
                    latest = cached["release"]
                    entry["note"] = "GitHub's hourly limit was reached; showing the last check. "
                elif error.code in (403, 429):
                    entry["state"] = "ratelimited"
                    entry["note"] = ("GitHub allows 60 checks an hour without an account and that "
                                     "is used up. Try again later.")
                else:
                    entry["state"] = "offline"
                    entry["note"] = f"GitHub returned HTTP {error.code}."
            except Exception as error:
                if cached:
                    latest = cached["release"]
                    entry["note"] = "Could not reach GitHub; showing the last check. "
                else:
                    entry["state"] = "offline"
                    entry["note"] = f"Could not reach GitHub ({type(error).__name__})."

        if latest:
            entry.update({"latest": latest["tag"], "published": latest["published"],
                          "url": latest["url"], "prerelease": latest.get("prerelease", False),
                          "download": latest.get("download", ""),
                          "sha256": latest.get("sha256", ""), "size_mb": latest.get("size_mb", 0)})
            try:
                published = datetime.date.fromisoformat(latest["published"])
            except Exception:
                published = None
            if installed_date is None:
                entry["state"] = "missing"
            elif tool.get("npm"):
                def parts(text):
                    return [int(n) for n in re.findall(r"\d+", text or "")][:3]
                if not (installed_text or "").startswith("v") or parts(latest.get("npm_version")) > parts(installed_text):
                    entry["state"] = "update"
                    entry["note"] += f"npm has {latest['tag']}, newer than what is installed."
                else:
                    entry["state"] = "current"
                    entry["note"] += f"Installed build matches the newest published build ({latest['tag']})."
            elif published and published > installed_date:
                kind = "Pre-release" if latest.get("prerelease") else "Release"
                entry["state"] = "update"
                entry["note"] += f"{kind} {latest['tag']} ({latest['published']}) is newer than what is installed."
            else:
                entry["state"] = "current"
                entry["note"] += f"Installed build matches the newest release ({latest['tag']})."
            downloadable = bool(tool.get("npm")) or (bool(latest.get("download")) and bool(latest.get("sha256")))
            entry["can_install"] = downloadable and entry["state"] in ("update", "missing")
            if not downloadable and entry["state"] in ("update", "missing") and latest.get("download"):
                entry["note"] += " The release publishes no SHA-256, so the studio will not download it; install it by hand."
            if tool.get("no_install"):
                entry["can_install"] = False
                entry["note"] += " Update this tool by hand; the studio reads it from a fixed folder."
            elif entry["can_install"] and _fixed_location(tool) and entry["state"] == "update":
                # The pipeline reads this tool from one fixed folder, so a side-by-side install
                # would never be used. Replace the folder by hand.
                entry["can_install"] = False
                entry["note"] += " Replace the tool folder by hand; the studio reads it from a fixed path."

        results.append(entry)

    _save_cache(cache)
    return results


# ----------------------------------------------------------------------------- installing

def _install_npm(tool, report, approved=None):
    """Updates a tool that ships as an npm package, in its own folder."""
    import subprocess
    import shutil
    folder = os.path.join(TOOLS_ROOT, "rea-runtime")
    node_dir = None
    for entry in sorted(os.listdir(TOOLS_ROOT) if os.path.isdir(TOOLS_ROOT) else [], reverse=True):
        if entry.lower().startswith("node-"):
            for root, _dirs, files in os.walk(os.path.join(TOOLS_ROOT, entry)):
                if "npm.cmd" in files:
                    node_dir = root
                    break
        if node_dir:
            break
    npm = os.path.join(node_dir, "npm.cmd") if node_dir else (shutil.which("npm") or "")
    if not npm:
        raise RuntimeError("npm was not found. Install Node.js or put it in a node-* folder under the tool root.")
    os.makedirs(folder, exist_ok=True)
    backup = {}
    for name in ("package.json", "package-lock.json"):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            backup[name] = open(path, "rb").read()

    pinned = (approved or {}).get("version", "").lstrip("v")
    if not re.match(r"^\d+\.\d+\.\d+([-+.][0-9A-Za-z.]+)?$", pinned):
        raise RuntimeError("Nothing was installed: the npm version to install was not approved.")
    report(10, f"Installing {tool['npm']}@{pinned} with npm (this can take a few minutes)...")
    result = subprocess.run(
        [npm, "install", f"{tool['npm']}@{pinned}", "--engine-strict=false", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=folder, capture_output=True, text=True, errors="replace", timeout=2700)
    if result.returncode != 0:
        for name, data in backup.items():  # keep the manifest of the working install
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write(data)
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError("npm failed: " + " | ".join(tail))

    report(95, "Reading the installed version...")
    package = os.path.join(folder, "node_modules", tool["npm"], "package.json")
    version = json.load(open(package, encoding="utf-8")).get("version", "?") if os.path.exists(package) else "?"
    report(100, f"{tool['name']} {version} installed")
    return {"tool": tool["name"], "version": version, "path": folder, "size_mb": 0}


def install(name, progress=None, approved=None):
    """Downloads and installs the newest build of one tool.

    `progress` is called with (percent, message) so the interface can follow along. Archives
    go into their own versioned folder next to the existing one, and single executables are
    saved alongside the current build rather than over it, so a working tool is never replaced
    by one that turns out not to read your game.
    """
    def report(percent, message):
        if progress:
            progress(percent, message)

    tool = next((t for t in TOOLS if t["name"].lower() == name.lower()), None)
    if not tool:
        raise ValueError(f"unknown tool: {name}")

    approved = approved or {}
    if not approved.get("version"):
        raise RuntimeError("Nothing was installed: the download has to be approved first.")

    # Some tools are published to npm rather than as a release asset.
    if tool.get("npm"):
        return _install_npm(tool, report, approved)

    report(2, f"Looking up the newest {tool['name']}...")
    latest = _newest_release(tool["repo"], tool.get("asset_pattern"), tool.get("stable_only", False))
    if latest["tag"] == approved.get("version") and latest.get("sha256", "") != str(approved.get("sha256", "")).lower():
        raise RuntimeError(f"The checksum of {latest['tag']} no longer matches the one you approved. "
                           "Nothing was installed; check again and approve it again.")
    if latest["tag"] != approved.get("version"):
        raise RuntimeError(f"The newest release is now {latest['tag']}, not the {approved.get('version')} you approved. "
                           "Nothing was installed; check again and approve the new one.")
    url = latest.get("download")
    if url and not re.match(r"^https://(github\.com|objects\.githubusercontent\.com)/", url):
        raise RuntimeError("The download address is not an https GitHub address; nothing was installed.")
    if not url:
        raise RuntimeError(f"{tool['name']} {latest['tag']} publishes no Windows download; "
                           f"install it by hand from {latest['url']}")

    safe_tag = re.sub(r"[^0-9A-Za-z._-]", "_", latest["tag"])
    report(6, f"Downloading {latest['download_name']} ...")
    request = urllib.request.Request(url, headers={"User-Agent": "aether-recon-studio"})
    chunks = []
    began = time.time()
    with urllib.request.urlopen(request, timeout=900) as response:
        total = int(response.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = response.read(262144)
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
            speed = got / max(time.time() - began, 0.001)
            if total:
                left = (total - got) / max(speed, 1)
                report(6 + int(got / total * 74),
                       f"Downloading {latest['download_name']}: {got / 1e6:.1f} of {total / 1e6:.1f} MB "
                       f"({int(got / total * 100)}%) at {speed / 1e6:.1f} MB/s, about {int(left)} s left")
            else:
                report(40, f"Downloading {latest['download_name']}: {got / 1e6:.1f} MB at {speed / 1e6:.1f} MB/s")
    blob = b"".join(chunks)
    report(81, "Download finished. Checking the SHA-256...")

    expected = latest.get("sha256", "")
    if not expected:
        raise RuntimeError(f"{tool['name']} {latest['tag']} publishes no SHA-256 digest, so the download "
                           f"cannot be verified; install it by hand from {latest['url']}")
    import hashlib
    if hashlib.sha256(blob).hexdigest() != expected:
        raise RuntimeError(f"{latest['download_name']} failed SHA-256 verification; nothing was installed")

    report(84, "Installing...")
    if latest["download_name"].lower().endswith(".zip"):
        if _fixed_location(tool):
            target = os.path.dirname(tool["marker"])  # the folder the pipeline actually reads
            if os.path.isdir(target) and os.listdir(target):
                raise RuntimeError(f"{target} already has files; replace it by hand.")
        else:
            target = os.path.join(TOOLS_ROOT, f"{tool['name'].lower()}-{safe_tag}")
        import io
        import shutil
        partial = target + ".partial"
        shutil.rmtree(partial, ignore_errors=True)
        os.makedirs(partial)
        try:
            base = os.path.realpath(partial)
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for member in archive.namelist():
                    if not os.path.realpath(os.path.join(base, member)).startswith(base + os.sep):
                        raise RuntimeError(f"unsafe path in archive: {member}")
                archive.extractall(partial)
            marker_name = os.path.basename(tool.get("marker", "")).lower()
            if _fixed_location(tool) and marker_name:
                # Some releases nest the tool one or two folders deep: use the folder that holds it.
                for walk_root, _dirs, walk_files in os.walk(partial):
                    if marker_name in [f.lower() for f in walk_files]:
                        if os.path.realpath(walk_root) != os.path.realpath(partial):
                            flat = partial + ".flat"
                            os.replace(walk_root, flat)
                            shutil.rmtree(partial, ignore_errors=True)
                            partial = flat
                        break
            entries = os.listdir(partial)
            if len(entries) == 1 and os.path.isdir(os.path.join(partial, entries[0])) and not _fixed_location(tool):
                inner = os.path.join(partial, entries[0])  # release zips wrap everything in one folder
                flat = partial + ".flat"
                os.replace(inner, flat)
                os.rmdir(partial)
                partial = flat
            marker = tool.get("marker", "")
            if _fixed_location(tool) and marker and not os.path.exists(
                    os.path.join(partial, os.path.basename(marker))):
                raise RuntimeError(f"The download does not contain {os.path.basename(marker)}; nothing was installed.")
            if os.path.isdir(target) and not os.listdir(target):
                os.rmdir(target)
            os.replace(partial, target)
        except Exception:
            shutil.rmtree(partial, ignore_errors=True)
            shutil.rmtree(partial.replace(".flat", ""), ignore_errors=True)
            raise
    else:
        folder = tool.get("newest_file_in") or os.path.dirname(tool["marker"]) or TOOLS_ROOT
        os.makedirs(folder, exist_ok=True)
        root, extension = os.path.splitext(latest["download_name"])
        target = os.path.join(folder, f"{tool['name']}-{safe_tag}{extension or '.exe'}")
        with open(target + ".part", "wb") as handle:
            handle.write(blob)
        os.replace(target + ".part", target)

    cache = _load_cache()
    cache.pop(tool["repo"], None)
    _save_cache(cache)
    report(100, f"{tool['name']} {latest['tag']} installed")
    return {"tool": tool["name"], "version": latest["tag"], "path": target,
            "size_mb": round(len(blob) / 1e6, 1)}
