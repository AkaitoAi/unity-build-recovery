"""REA (Reverse Engineer Anything) support for game recovery.

REA (https://github.com/morluto/rea, npm package rea-agents) is a command-line toolkit and MCP
server for static reverse engineering: native binaries (through Ghidra), .NET assemblies, IL2CPP
metadata, APKs, and JavaScript or Electron apps. The studio uses it in Agent Recovery: an agent
that gets REA can inspect the parts of a game the fixed pipeline does not reach.

The studio only uses the REA that is already installed under the tool root
(rea-runtime/node_modules/rea-agents). Installing it goes through the same approval step as
every other download. REA is connected to an agent for that one run only (Claude Code gets an
--mcp-config file, Codex gets -c overrides), so no agent's own settings are changed. Other agents
are told to run REA from the shell.
"""

import glob
import json
import os
import shutil

from paths import TOOLS_ROOT

PACKAGE_DIR = os.path.join(TOOLS_ROOT, "rea-runtime", "node_modules", "rea-agents")


def _find_node():
    """node.exe: a portable copy under the tool root first, then PATH (never the current folder)."""
    local = sorted(glob.glob(os.path.join(TOOLS_ROOT, "node-*", "**", "node.exe"), recursive=True), reverse=True)
    if local:
        return local[0]
    here = os.path.normcase(os.path.abspath(os.getcwd()))
    entries = [p for p in os.environ.get("PATH", "").split(os.pathsep)
               if p and os.path.normcase(os.path.abspath(p)) != here]
    return shutil.which("node", path=os.pathsep.join(entries))


def find_rea():
    """{'node', 'script', 'version'} when REA and Node.js are both installed, else None."""
    script = os.path.join(PACKAGE_DIR, "scripts", "rea.mjs")
    if not os.path.isfile(script):
        return None
    node = _find_node()
    if not node:
        return None
    version = ""
    try:
        with open(os.path.join(PACKAGE_DIR, "package.json"), "r", encoding="utf-8") as handle:
            version = str(json.load(handle).get("version", ""))
    except Exception:
        pass
    return {"node": node, "script": script, "version": version}


def status() -> dict:
    rea = find_rea()
    return {"installed": bool(rea), "version": rea["version"] if rea else "",
            "package_present": os.path.isdir(PACKAGE_DIR), "node_found": bool(_find_node())}


def environment() -> dict:
    """Variables REA's native analysis needs, when the matching tool is in the tool root."""
    env = {}
    ghidra = os.path.join(TOOLS_ROOT, "ghidra")
    if os.path.isdir(ghidra):
        env["GHIDRA_INSTALL_DIR"] = ghidra
    if os.environ.get("JAVA_HOME"):
        env["JAVA_HOME"] = os.environ["JAVA_HOME"]
    return env


def command_line(rea: dict, command: str = "") -> str:
    return f'"{rea["node"]}" "{rea["script"]}" {command}'.rstrip()


def mcp_config(rea: dict) -> dict:
    """An MCP config file body that starts REA's server (Claude Code --mcp-config)."""
    return {"mcpServers": {"rea": {"command": rea["node"], "args": [rea["script"], "mcp"], "env": environment()}}}


def codex_overrides(rea: dict) -> list:
    """Codex -c overrides that add REA for one run. JSON strings are valid TOML basic strings."""
    return ["-c", f"mcp_servers.rea.command={json.dumps(rea['node'])}",
            "-c", f"mcp_servers.rea.args={json.dumps([rea['script'], 'mcp'])}"]
