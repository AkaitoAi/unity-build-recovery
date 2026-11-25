"""Single definition of the tool root shared by every studio module.

Resolution order:
  1. RE_TOOLS environment variable (set by env.ps1 or by you).
  2. The folder above studio/, when it already holds the toolkit (it contains 7-Zip).
  3. The workspace containing the repository, when it holds the shared toolkit.
  4. <repo>/.tools. The CLI also installs there, but under versioned folder names
     (AssetRipper-2.0.0), so tools installed by the CLI are not found by the studio.
"""
import os
import shutil
from pathlib import Path

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve_tools_root():
    override = os.environ.get("RE_TOOLS", "").strip()
    if override:
        return os.path.abspath(override)
    parent = os.path.dirname(APP_DIR)
    if os.path.isdir(os.path.join(parent, "7-Zip")):
        return parent
    shared = os.path.dirname(parent)
    if os.path.isfile(os.path.join(shared, "7-Zip", "7z.exe")):
        return shared
    return os.path.join(parent, ".tools")


TOOLS_ROOT = resolve_tools_root()
try:
    os.makedirs(TOOLS_ROOT, exist_ok=True)  # a fresh clone has no .tools folder yet
except OSError:
    pass


def find_ilspycmd():
    candidates = [Path(TOOLS_ROOT) / "ILSpy" / "ilspycmd.exe"]
    on_path = shutil.which("ilspycmd")
    if on_path:
        candidates.append(Path(on_path))
    candidates.append(Path.home() / ".dotnet" / "tools" / "ilspycmd.exe")
    for candidate in candidates:
        # Use the native executable directly, without passing input paths through cmd.exe.
        native = list(candidate.parent.glob(
            ".store/ilspycmd/*/ilspycmd.win-x64/*/tools/any/win-x64/ilspycmd.exe"
        ))
        if native:
            return str(max(native, key=lambda path: tuple(
                int(part) for part in path.parts[-5].split("-")[0].split(".")
            )))
        if candidate.is_file() and candidate.suffix.lower() not in {".cmd", ".bat"}:
            return str(candidate)
    return None


# Where the server leaves the one-use nonce its launcher opens the page with. It sits in this
# user's profile, so another Windows account cannot read it.
NONCE_PATH = os.environ.get("STUDIO_NONCE_FILE") or os.path.join(
    os.environ.get("LOCALAPPDATA", APP_DIR), "AetherReconStudio", "launch-nonce.txt")

# Server log folder (rotating studio.log). Kept out of the source tree.
LOG_DIR = os.environ.get("STUDIO_LOG_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", APP_DIR), "AetherReconStudio", "logs")


def studio_version() -> str:
    """The version in studio/VERSION, or 'dev' when the file is missing."""
    try:
        with open(os.path.join(APP_DIR, "VERSION"), "r", encoding="utf-8") as handle:
            return handle.read().strip() or "dev"
    except OSError:
        return "dev"
