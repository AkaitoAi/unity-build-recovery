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
    },
    {
        "name": "ILSpy", "repo": "icsharpcode/ILSpy",
        "marker": os.path.join(TOOLS_ROOT, "ILSpy", "ILSpy.exe"),
        "role": "Decompiles managed .NET assemblies",
        "newest_dir_prefix": "ilspy",
    },
    {
        "name": "Ghidra", "repo": "NationalSecurityAgency/ghidra",
        "marker": os.path.join(TOOLS_ROOT, "ghidra"),
        "role": "Disassembles and decompiles native binaries",
        "newest_dir_prefix": "ghidra",
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
        for entry in os.listdir(TOOLS_ROOT):
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


def _newest_release(repo):
    """Newest published build including pre-releases, with its Windows download."""
    releases = _api(f"https://api.github.com/repos/{repo}/releases?per_page=20")
    if not releases:
        raise RuntimeError("no releases published")
    releases.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    newest = releases[0]

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

    assets = [a for a in newest.get("assets", []) if score(a) > 0]
    assets.sort(key=score, reverse=True)
    return {
        "tag": newest.get("tag_name") or "?",
        "published": (newest.get("published_at") or "")[:10],
        "url": newest.get("html_url") or f"https://github.com/{repo}/releases",
        "prerelease": bool(newest.get("prerelease")),
        "download": assets[0]["browser_download_url"] if assets else "",
        "download_name": assets[0]["name"] if assets else "",
        "sha256": (assets[0].get("digest") or "").removeprefix("sha256:").lower() if assets else "",
    }


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
                latest = _newest_npm(tool["npm"]) if tool.get("npm") else _newest_release(tool["repo"])
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
                          "download": latest.get("download", "")})
            try:
                published = datetime.date.fromisoformat(latest["published"])
            except Exception:
                published = None
            if installed_date is None:
                entry["state"] = "missing"
            elif tool.get("npm"):
                def parts(text):
                    return [int(n) for n in re.findall(r"\d+", text or "")][:3]
                if parts(latest.get("npm_version")) > parts(installed_text):
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
            entry["can_install"] = (bool(latest.get("download")) or bool(tool.get("npm")))  and entry["state"] in ("update", "missing")

        results.append(entry)

    _save_cache(cache)
    return results


# ----------------------------------------------------------------------------- installing

def _install_npm(tool, report):
    """Updates a tool that ships as an npm package, in its own folder."""
    import subprocess
    folder = os.path.join(TOOLS_ROOT, "rea-runtime")
    node_dir = None
    for entry in sorted(os.listdir(TOOLS_ROOT), reverse=True):
        if entry.lower().startswith("node-"):
            for root, _dirs, files in os.walk(os.path.join(TOOLS_ROOT, entry)):
                if "npm.cmd" in files:
                    node_dir = root
                    break
        if node_dir:
            break
    npm = os.path.join(node_dir, "npm.cmd") if node_dir else "npm"

    report(10, f"Installing {tool['npm']} with npm (this can take a few minutes)...")
    result = subprocess.run(
        [npm, "install", f"{tool['npm']}@latest", "--engine-strict=false", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=folder, capture_output=True, text=True, errors="replace", timeout=2700)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError("npm failed: " + " | ".join(tail))

    report(95, "Reading the installed version...")
    package = os.path.join(folder, "node_modules", tool["npm"], "package.json")
    version = json.load(open(package, encoding="utf-8")).get("version", "?") if os.path.exists(package) else "?"
    report(100, f"{tool['name']} {version} installed")
    return {"tool": tool["name"], "version": version, "path": folder, "size_mb": 0}


def install(name, progress=None):
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

    # Some tools are published to npm rather than as a release asset.
    if tool.get("npm"):
        return _install_npm(tool, report)

    report(2, f"Looking up the newest {tool['name']}...")
    latest = _newest_release(tool["repo"])
    url = latest.get("download")
    if not url:
        raise RuntimeError(f"{tool['name']} {latest['tag']} publishes no Windows download; "
                           f"install it by hand from {latest['url']}")

    safe_tag = re.sub(r"[^0-9A-Za-z._-]", "_", latest["tag"])
    report(6, f"Downloading {latest['download_name']} ...")
    request = urllib.request.Request(url, headers={"User-Agent": "aether-recon-studio"})
    chunks = []
    with urllib.request.urlopen(request, timeout=900) as response:
        total = int(response.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = response.read(262144)
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
            if total:
                report(6 + int(got / total * 74), f"Downloading {got / 1e6:.1f} of {total / 1e6:.1f} MB")
            else:
                report(40, f"Downloading {got / 1e6:.1f} MB")
    blob = b"".join(chunks)

    expected = latest.get("sha256", "")
    if not expected:
        raise RuntimeError(f"{tool['name']} {latest['tag']} publishes no SHA-256 digest, so the download "
                           f"cannot be verified; install it by hand from {latest['url']}")
    import hashlib
    if hashlib.sha256(blob).hexdigest() != expected:
        raise RuntimeError(f"{latest['download_name']} failed SHA-256 verification; nothing was installed")

    report(84, "Installing...")
    if latest["download_name"].lower().endswith(".zip"):
        target = os.path.join(TOOLS_ROOT, f"{tool['name'].lower()}-{safe_tag}")
        os.makedirs(target, exist_ok=True)
        import io
        base = os.path.realpath(target)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for member in archive.namelist():
                if not os.path.realpath(os.path.join(base, member)).startswith(base + os.sep):
                    raise RuntimeError(f"unsafe path in archive: {member}")
            archive.extractall(target)
    else:
        folder = tool.get("newest_file_in") or os.path.dirname(tool["marker"]) or TOOLS_ROOT
        if not os.path.isdir(folder):
            folder = TOOLS_ROOT
        os.makedirs(folder, exist_ok=True)
        root, extension = os.path.splitext(latest["download_name"])
        target = os.path.join(folder, f"{tool['name']}-{safe_tag}{extension or '.exe'}")
        open(target, "wb").write(blob)

    cache = _load_cache()
    cache.pop(tool["repo"], None)
    _save_cache(cache)
    report(100, f"{tool['name']} {latest['tag']} installed")
    return {"tool": tool["name"], "version": latest["tag"], "path": target,
            "size_mb": round(len(blob) / 1e6, 1)}
