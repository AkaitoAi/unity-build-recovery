"""Agent-driven recovery: find the AI coding agents installed on this PC and start one.

Two kinds are known. Command-line agents get a one-line first message that points at the task
file. Desktop apps and editors (the Codex desktop app, Cursor, Windsurf, VS Code, Antigravity) are
opened on the workspace folder, and you paste the prompt into their agent panel yourself.

The studio writes the game's recovery prompt to AGENT_TASK.md in the workspace (see
agent_prompt.py), copies the same prompt to the clipboard, and opens the agent you choose in the
workspace. The prompt tells the agent to work through every step without asking questions. That is
a request, not a permission: the agent still asks before it runs commands, as it normally does,
and the studio never turns on an auto-approve mode.

What the agent reads (the game files, tool output) is untrusted. The task file says so, but an
agent can still be fooled by text inside a hostile build, so keep its permission prompts on.
Whatever the agent sees goes to that agent's provider.
"""

import json
import os
import shutil
import subprocess
import time

import rea as rea_support
from agent_prompt import build_prompt
from paths import TOOLS_ROOT

FIRST_MESSAGE = ("Read AGENT_TASK.md in the current folder and complete every step in it "
                 "without asking me any questions.")

# id, display name, executable, how to pass the first message (None: the flags are not known
# here, so the studio opens the agent empty and shows the line to paste).
KNOWN_AGENTS = [
    {"id": "claude", "name": "Claude Code", "exe": "claude", "prompt_args": []},
    {"id": "codex", "name": "OpenAI Codex CLI", "exe": "codex", "prompt_args": []},
    {"id": "gemini", "name": "Gemini CLI", "exe": "gemini", "prompt_args": ["-i"]},
    {"id": "copilot", "name": "GitHub Copilot CLI", "exe": "copilot", "prompt_args": None},
    {"id": "opencode", "name": "opencode", "exe": "opencode", "prompt_args": None},
    {"id": "grok", "name": "Grok Build", "exe": "grok", "prompt_args": None},
    {"id": "qwen", "name": "Qwen Code", "exe": "qwen", "prompt_args": None},
    {"id": "goose", "name": "goose", "exe": "goose", "prompt_args": None},
    {"id": "aider", "name": "aider", "exe": "aider", "prompt_args": None},
]

LOCAL = os.environ.get("LOCALAPPDATA", "")
PROGRAM_FILES = os.environ.get("ProgramFiles", r"C:\Program Files")

# Desktop apps and editors. "exes" are names looked up on PATH; "paths" are default install
# locations. "launch" is the argument list after the executable; {workspace} is the folder.
DESKTOP_AGENTS = [
    {"id": "codex-app", "name": "OpenAI Codex (desktop app)", "kind": "desktop",
     "exes": ["codex"], "paths": [], "launch": ["app", "{workspace}"], "appx": "OpenAI.Codex"},
    {"id": "cursor", "name": "Cursor (desktop)", "kind": "desktop", "exes": ["cursor"],
     "paths": [os.path.join(LOCAL, "Programs", "cursor", "Cursor.exe")], "launch": ["{workspace}"]},
    {"id": "windsurf", "name": "Windsurf (desktop)", "kind": "desktop", "exes": ["windsurf"],
     "paths": [os.path.join(LOCAL, "Programs", "Windsurf", "Windsurf.exe")], "launch": ["{workspace}"]},
    {"id": "vscode", "name": "VS Code (desktop)", "kind": "desktop", "exes": ["code"],
     "paths": [os.path.join(LOCAL, "Programs", "Microsoft VS Code", "Code.exe"),
               os.path.join(PROGRAM_FILES, "Microsoft VS Code", "Code.exe")], "launch": ["{workspace}"]},
    {"id": "antigravity", "name": "Antigravity (desktop)", "kind": "desktop", "exes": ["antigravity"],
     "paths": [os.path.join(LOCAL, "Programs", "antigravity", "Antigravity.exe")], "launch": ["{workspace}"]},
]

_by_id = {a["id"]: a for a in KNOWN_AGENTS + DESKTOP_AGENTS}


def _appx_installed(package: str) -> bool:
    """True when the Microsoft Store package is installed for this user."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-AppxPackage -Name '{package}' | Measure-Object).Count"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        return (result.stdout or "").strip() not in ("", "0")
    except Exception:
        return False


def _find_desktop(agent: dict) -> str:
    """Path of the executable that opens this desktop app, or '' when it is not installed."""
    if agent.get("appx") and not _appx_installed(agent["appx"]):
        return ""
    for name in agent["exes"]:
        found = _which(name)
        if found:
            return found
    for path in agent["paths"]:
        if os.path.isfile(path):
            return path
    return ""


def _which(name: str):
    """Looks a program up on PATH only. Windows also searches the current folder first, which
    would let a file named claude.exe in the studio's folder be started instead of the real agent."""
    here = os.path.normcase(os.path.abspath(os.getcwd()))
    entries = [p for p in os.environ.get("PATH", "").split(os.pathsep)
               if p and os.path.normcase(os.path.abspath(p)) != here]
    return shutil.which(name, path=os.pathsep.join(entries))


def _version(path: str) -> str:
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, errors="replace",
                                stdin=subprocess.DEVNULL, timeout=15)
        text = (result.stdout or result.stderr or "").strip().splitlines()
        return text[0][:80] if text else ""
    except Exception:
        return ""


def detect() -> list:
    """Every known agent, marked installed or not. The page lists installed ones first."""
    from concurrent.futures import ThreadPoolExecutor
    paths = {a["id"]: _which(a["exe"]) for a in KNOWN_AGENTS}
    with ThreadPoolExecutor(max_workers=len(KNOWN_AGENTS) + len(DESKTOP_AGENTS)) as pool:
        desktop_paths = {a["id"]: pool.submit(_find_desktop, a) for a in DESKTOP_AGENTS}
        versions = {aid: pool.submit(_version, p) for aid, p in paths.items() if p}
        found = []
        for agent in KNOWN_AGENTS:
            path = paths[agent["id"]]
            found.append({
                "id": agent["id"], "name": agent["name"], "installed": bool(path), "kind": "cli",
                "path": path or "", "version": versions[agent["id"]].result() if path else "",
                "can_start_with_task": agent["prompt_args"] is not None,
            })
        for agent in DESKTOP_AGENTS:
            path = desktop_paths[agent["id"]].result()
            found.append({
                "id": agent["id"], "name": agent["name"], "installed": bool(path), "kind": "desktop",
                "path": path, "version": "", "can_start_with_task": False,
            })
    found.sort(key=lambda a: (not a["installed"], a["name"]))
    return found


def build_task(source: str, work_folder: str, info: dict, rea=None, rea_via_mcp: bool = False) -> str:
    """The recovery prompt for this game."""
    return build_prompt(source, work_folder, info, rea, rea_via_mcp)


def _copy_to_clipboard(text: str) -> bool:
    """Puts text on the Windows clipboard so the user can paste it into an agent panel."""
    try:
        # The text travels in an environment variable, which Windows stores as UTF-16, so paths
        # with non-English letters arrive intact and no byte-order mark is added.
        env = dict(os.environ, STUDIO_CLIP_TEXT=text)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Set-Clipboard -Value $env:STUDIO_CLIP_TEXT"],
                       env=env, timeout=20, check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------- tracking
# The studio cannot sandbox an agent, but it can know one is working in a workspace and keep its own
# stages, audits and installs from changing that folder underneath it. A command-line agent is
# tracked by its process; a desktop app cannot be watched, so it holds the workspace until the user
# says it has finished. Lock order: server.PIPELINE_LOCK first, then TRACK_LOCK.

import threading

TRACK_LOCK = threading.Lock()
TRACKED = {}        # workspace key -> {"agent", "pid", "started", "proc", "config", "workspace", "manual"}


def _key(work_folder: str) -> str:
    return os.path.normcase(os.path.realpath(work_folder))


def _drop(key: str) -> None:
    entry = TRACKED.pop(key, None)
    if entry and entry.get("config"):
        try:
            os.remove(entry["config"])
        except OSError:
            pass


def _purge() -> None:
    for key in [k for k, e in TRACKED.items() if e.get("proc") is not None and e["proc"].poll() is not None]:
        _drop(key)


def active(work_folder: str = "") -> list:
    """Agents the studio started that are still working. With a folder, only that workspace's."""
    with TRACK_LOCK:
        _purge()
        wanted = _key(work_folder) if work_folder else ""
        return [{"agent": e["agent"], "pid": e["pid"], "started": e["started"], "workspace": e["workspace"],
                 "manual": e["proc"] is None}
                for k, e in TRACKED.items() if not wanted or k == wanted]


def reserve(work_folder: str, name: str = "") -> bool:
    """Claims the workspace before the agent starts. False when an agent already holds it."""
    with TRACK_LOCK:
        _purge()
        key = _key(work_folder)
        if key in TRACKED:
            return False
        TRACKED[key] = {"agent": name, "pid": None, "started": time.time(), "proc": None, "config": "",
                        "workspace": work_folder}
        return True


def release(work_folder: str) -> bool:
    """The user says the agent has finished (or the launch failed)."""
    with TRACK_LOCK:
        key = _key(work_folder)
        found = key in TRACKED
        _drop(key)
        return found


def _register(work_folder: str, name: str, proc, config: str = "") -> None:
    with TRACK_LOCK:
        TRACKED[_key(work_folder)] = {"agent": name, "pid": getattr(proc, "pid", None), "started": time.time(),
                                      "proc": proc, "config": config, "workspace": work_folder}


_SHELL_UNSAFE = set('&|^%<>"')


def check_agent(agent_id: str) -> None:
    """Raises ValueError unless this is a known agent that is installed."""
    agent = _by_id.get(agent_id)
    if not agent:
        raise ValueError("Unknown agent.")
    path = _find_desktop(agent) if agent.get("kind") == "desktop" else _which(agent["exe"])
    if not path:
        raise ValueError(f"{agent['name']} is not installed (or not on PATH).")


def launch(agent_id: str, source: str, work_folder: str, info: dict, use_rea: bool = False,
           copy_prompt: bool = True) -> dict:
    """Writes AGENT_TASK.md and opens the chosen agent (console window or desktop app).

    The caller reserves the workspace first (reserve); this registers the running agent, or
    releases the reservation if the start fails."""
    try:
        return _launch(agent_id, source, work_folder, info, use_rea, copy_prompt)
    except BaseException:
        release(work_folder)
        try:
            os.remove(os.path.join(work_folder, ".rea-mcp.json"))
        except OSError:
            pass
        raise


def _launch(agent_id, source, work_folder, info, use_rea, copy_prompt):
    agent = _by_id.get(agent_id)
    if not agent:
        raise ValueError("Unknown agent.")
    desktop = agent.get("kind") == "desktop"
    path = _find_desktop(agent) if desktop else _which(agent["exe"])
    if not path:
        raise ValueError(f"{agent['name']} is not installed (or not on PATH).")
    if path.lower().endswith((".cmd", ".bat")) and any(c in _SHELL_UNSAFE for c in work_folder):
        raise ValueError(f"{agent['name']} starts through a .cmd shim, and the workspace path contains a character "
                         'cmd.exe treats specially (& | ^ % < > "). Choose a workspace with a plainer path.')
    os.makedirs(work_folder, exist_ok=True)
    task_path = os.path.join(work_folder, "AGENT_TASK.md")
    rea = rea_support.find_rea() if use_rea else None
    if use_rea and not rea:
        raise ValueError("REA is not installed here (it needs the rea-agents package and Node.js).")
    # REA is connected as MCP tools for Claude Code and Codex CLI, for this run only; every other
    # agent is told to run it from the shell.
    via_mcp = bool(rea) and not desktop and agent["id"] in ("claude", "codex")
    prompt = build_task(source, work_folder, info, rea, via_mcp)
    with open(task_path, "w", encoding="utf-8") as handle:
        handle.write(prompt)

    pasted = True
    config = ""
    if desktop:
        args = [path] + [part.replace("{workspace}", work_folder) for part in agent["launch"]]
        subprocess.Popen(args, cwd=work_folder, close_fds=True)
        _register(work_folder, agent["name"], None)       # held until the user says it has finished
    else:
        args = [path]
        if via_mcp and agent["id"] == "claude":
            config = os.path.join(work_folder, ".rea-mcp.json")
            with open(config, "w", encoding="utf-8") as handle:
                json.dump(rea_support.mcp_config(rea), handle)
            # --mcp-config takes several values, so the prompt goes first.
            args += [FIRST_MESSAGE, "--mcp-config", config]
            pasted = False
        elif via_mcp and agent["id"] == "codex":
            args += rea_support.codex_overrides(rea) + [FIRST_MESSAGE]
            pasted = False
        elif agent["prompt_args"] is not None:
            args += agent["prompt_args"] + [FIRST_MESSAGE]
            pasted = False  # flags for the rest are unknown here: open it empty, user pastes the line
        proc = subprocess.Popen(args, cwd=work_folder, creationflags=subprocess.CREATE_NEW_CONSOLE,
                                stdin=None, stdout=None, stderr=None)
        _register(work_folder, agent["name"], proc, config)   # the MCP config is removed when it exits
    # By default the prompt for this game goes on the clipboard, so it can be pasted into any agent
    # that did not take the first message by itself. If the prompt were too large for the clipboard
    # route, the path of the task file is copied instead. The page lets the user turn this off.
    fallback = f'Read "{task_path}" and complete every step in it without asking me any questions.'
    clipboard_text = prompt if len(prompt) < 30000 else fallback
    copied = _copy_to_clipboard(clipboard_text) if copy_prompt else False
    return {"agent": agent["name"], "task_file": task_path, "workspace": work_folder,
            "paste_this": clipboard_text if pasted else "",
            "copy_text": clipboard_text, "copy_chars": len(clipboard_text),
            "copy_is_prompt": clipboard_text is prompt, "copied": copied,
            "rea": ("mcp" if via_mcp else "cli") if rea else "",
            "started_at": time.time()}
