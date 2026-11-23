"""Single definition of the tool root shared by every studio module.

Resolution order:
  1. RE_TOOLS environment variable (set by env.ps1 or by you).
  2. The folder above studio/, when it already holds the toolkit (it contains 7-Zip).
  3. <repo>/.tools. The CLI also installs there, but under versioned folder names
     (AssetRipper-2.0.0), so tools installed by the CLI are not found by the studio.
"""
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve_tools_root():
    override = os.environ.get("RE_TOOLS", "").strip()
    if override:
        return os.path.abspath(override)
    parent = os.path.dirname(APP_DIR)
    if os.path.isdir(os.path.join(parent, "7-Zip")):
        return parent
    return os.path.join(parent, ".tools")


TOOLS_ROOT = resolve_tools_root()
try:
    os.makedirs(TOOLS_ROOT, exist_ok=True)  # a fresh clone has no .tools folder yet
except OSError:
    pass
