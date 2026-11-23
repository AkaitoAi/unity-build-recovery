"""
AETHER RECON STUDIO - High Performance Local Backend Server
Runs locally on loopback port 5899 (only the tool-update checker contacts GitHub and npm).
Provides REST endpoints for scanning, recovery pipeline execution, AI script classification, and Unity Editor launching.
"""

import os
import sys
import json
import time
import urllib.parse
import threading
import subprocess
import secrets
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from paths import TOOLS_ROOT
import craft
import agents
import rea as rea_support
import tool_needs
from scanner import inspect_game
from script_ai_selector import scan_and_classify_directory, export_selected_scripts, ScriptItem
from recovery_engine import RecoveryPipeline
from unity_playable_scaffolder import scaffold_unity_project, INSTALLED_EDITORS
from tool_updates import check_updates, install
import tool_updates

PORT = 5899
SESSION_TOKEN = secrets.token_urlsafe(24)
MAX_BODY_BYTES = 1_000_000

# Live view of a pipeline run, so the page can show which tool is working right now.
# The run happens inside one long request, so without this the console stayed silent
# until the whole thing had finished.
# Progress of a tool install, so the page can show a bar while it downloads.
INSTALL_STATE = {"running": False, "tool": "", "percent": 0, "message": "", "result": None, "error": "",
                 "started_at": 0.0, "finished_at": 0.0}
INSTALL_LOCK = threading.Lock()


def install_set(**fields):
    with INSTALL_LOCK:
        INSTALL_STATE.update(fields)


def install_snapshot():
    with INSTALL_LOCK:
        return dict(INSTALL_STATE)


def run_install(name, approved=None):
    """Installs one tool in the background so the request can return at once."""
    def progress(percent, message):
        install_set(percent=percent, message=message)

    install_set(running=True, tool=name, percent=0, message="Starting...", result=None, error="",
                started_at=time.time(), finished_at=0.0)
    try:
        outcome = install(name, progress, approved)
        install_set(running=False, percent=100, message=outcome["tool"] + " " + outcome["version"] + " installed",
                    result=outcome, finished_at=time.time())
    except Exception as exc:
        install_set(running=False, percent=0, message="", error=f"{type(exc).__name__}: {exc}",
                    finished_at=time.time())

PIPELINE_STATE = {
    "running": False,
    "stage": "",
    "tool": "",
    "log": [],
    "results": [],
    "status": "idle",
    "message": "",
    "plan": [],            # names of the stages this run will go through, in order
    "stage_index": 0,      # 1-based number of the stage running now
    "stage_total": 0,
    "started_at": 0.0,
    "stage_started_at": 0.0,
    "finished_at": 0.0,
}
PIPELINE_LOCK = threading.Lock()


def _inside(child: str, root: str) -> bool:
    child = os.path.normcase(os.path.realpath(child))
    root = os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([child, root]) == root
    except ValueError:  # different drives
        return False


def _protected_folders():
    """Folders a workspace must not be, or sit inside: the studio, its tools, Windows and personal folders."""
    home = os.path.expanduser("~")
    names = [os.environ.get(k, "") for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")]
    exact = [home, os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", ""),
             os.path.join(home, "Desktop"), os.path.join(home, "Documents"), os.path.join(home, "Downloads")]
    return [p for p in names if p], [p for p in exact if p]


def resolve_work(path, must_exist: bool = False) -> str:
    """Validates a work folder from the page. Raises ValueError with a message for the user."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Choose a workspace folder first.")
    path = path.strip()
    if not os.path.isabs(path):
        raise ValueError("The workspace must be a full path such as D:\\Recovery\\MyGame.")
    real = os.path.realpath(path)
    if os.path.dirname(real) == real:
        raise ValueError("Do not use a drive root as the workspace.")
    for protected in (TOOLS_ROOT, APP_DIR):
        if _inside(real, protected) or _inside(protected, real):
            raise ValueError("The workspace cannot be the studio or tools folder, or a folder that contains them.")
    trees, exact = _protected_folders()
    if any(_inside(real, p) for p in trees) or any(os.path.normcase(real) == os.path.normcase(os.path.realpath(p)) for p in exact):
        raise ValueError("Choose a new subfolder, not a Windows, Program Files or personal folder itself.")
    if must_exist and not os.path.isdir(real):
        raise ValueError("That workspace folder does not exist yet. Run recovery first.")
    if os.path.isdir(real):
        entries = os.listdir(real)
        studio_made = os.path.exists(os.path.join(real, ".run.json")) or os.path.isdir(os.path.join(real, "00_original"))
        if entries and not studio_made:
            raise ValueError("That folder already has other files in it. Choose an empty folder, "
                             "or one this studio created.")
    return real


def AR_SCRIPTS(work_folder: str) -> str:
    """Scripts AssetRipper recovered; the scaffold stage already copied them into the project."""
    return os.path.join(work_folder, "02_assetripper", "ExportedProject", "Assets", "Scripts")


def acquire_run() -> bool:
    with PIPELINE_LOCK:
        if PIPELINE_STATE["running"] or install_snapshot()["running"] or craft.is_running():
            return False
        PIPELINE_STATE["running"] = True
        return True


def release_run() -> None:
    with PIPELINE_LOCK:
        PIPELINE_STATE["running"] = False

# Names of the bundled tools, matched against log lines to show the one in use.
PIPELINE_TOOLS = (
    "AssetRipper", "Il2CppDumper", "Cpp2IL", "JADX", "jadx", "7-Zip", "7z",
    "Ghidra", "ILSpy", "apktool", "Mono.Cecil", "FFmpeg",
)


def pipeline_log(message: str) -> None:
    """Receives every line the recovery engine logs and keeps the live view current."""
    text = str(message)
    with PIPELINE_LOCK:
        PIPELINE_STATE["log"].append(text)
        if len(PIPELINE_STATE["log"]) > 500:
            del PIPELINE_STATE["log"][:-500]
        for name in PIPELINE_TOOLS:
            if name in text:
                PIPELINE_STATE["tool"] = "7-Zip" if name == "7z" else ("JADX" if name == "jadx" else name)
                break
    try:
        print(text)
    except UnicodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def pipeline_set(**fields) -> None:
    with PIPELINE_LOCK:
        PIPELINE_STATE.update(fields)


def pipeline_snapshot(after: int = 0) -> dict:
    """The current state, with only the log lines the page has not seen yet."""
    with PIPELINE_LOCK:
        total = len(PIPELINE_STATE["log"])
        start = max(0, min(after, total))
        return {
            "running": PIPELINE_STATE["running"],
            "stage": PIPELINE_STATE["stage"],
            "tool": PIPELINE_STATE["tool"],
            "status": PIPELINE_STATE["status"],
            "message": PIPELINE_STATE["message"],
            "results": list(PIPELINE_STATE["results"]),
            "log": PIPELINE_STATE["log"][start:],
            "log_total": total,
            "plan": list(PIPELINE_STATE["plan"]),
            "stage_index": PIPELINE_STATE["stage_index"],
            "stage_total": PIPELINE_STATE["stage_total"],
            "started_at": PIPELINE_STATE["started_at"],
            "stage_started_at": PIPELINE_STATE["stage_started_at"],
            "finished_at": PIPELINE_STATE["finished_at"],
            "last_line": PIPELINE_STATE["log"][-1] if PIPELINE_STATE["log"] else "",
        }


def activity_snapshot() -> dict:
    """Everything that is running or just finished, in one answer for the page's activity bar."""
    return {"now": time.time(), "pipeline": pipeline_snapshot(10 ** 9),
            "install": install_snapshot(), "craft": craft.snapshot(10 ** 9)}

class StudioRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def end_headers(self):
        # Without this the browser keeps serving a cached copy of the page, so edits to the
        # interface appear not to take effect until a manual reload.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        # Silent standard HTTP request logging to keep console clean
        pass

    def _request_allowed(self):
        """Blocks DNS rebinding (bad Host) and cross-site pages (foreign Origin)."""
        allowed_hosts = (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        if self.headers.get("Host", "") not in allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return False
        if urllib.parse.urlparse(self.path).path.startswith("/api/"):
            supplied = self.headers.get("X-Studio-Token", "")
            if not secrets.compare_digest(supplied, SESSION_TOKEN):
                return False
        return True

    def _serve_index(self):
        """Serves the page with this launch's token so only the page can call the API."""
        with open(os.path.join(WEB_DIR, "index.html"), "r", encoding="utf-8") as handle:
            page = handle.read()
        snippet = ("<script>(function(){var t=" + json.dumps(SESSION_TOKEN) + ";var f=window.fetch;"
                   "window.fetch=function(u,o){o=o||{};o.headers=Object.assign({},o.headers||{},"
                   "{'X-Studio-Token':t});return f(u,o);};})();</script>")
        page = page.replace("<head>", "<head>" + snippet, 1)
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _deny(self):
        self.send_response(403)
        self.end_headers()

    def do_GET(self):
        if not self._request_allowed():
            return self._deny()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._serve_index()
        elif path == "/api/tools/status":
            self.handle_tools_status()
        elif path == "/api/scripts/read":
            self.handle_script_read(parsed.query)
        elif path == "/api/tools/install/status":
            self.send_json(install_snapshot())
        elif path == "/api/tools/updates":
            try:
                query = urllib.parse.parse_qs(parsed.query)
                self.send_json({"tools": check_updates(force=query.get("force", ["0"])[0] == "1")})
            except Exception as exc:
                self.send_json({"tools": [], "error": f"{type(exc).__name__}: {exc}"})
        elif path == "/api/activity":
            self.send_json(activity_snapshot())
        elif path == "/api/agents":
            self.send_json({"agents": agents.detect(), "rea": rea_support.status()})
        elif path == "/api/craft/agents":
            query = urllib.parse.parse_qs(parsed.query)
            self.send_json(craft.list_agents(query.get("cli", [""])[0]))
        elif path == "/api/craft/doctor":
            query = urllib.parse.parse_qs(parsed.query)
            self.send_json(craft.doctor(query.get("cli", [""])[0]))
        elif path == "/api/craft/status":
            try:
                after = int(urllib.parse.parse_qs(parsed.query).get("after", ["0"])[0])
            except ValueError:
                after = 0
            self.send_json(craft.snapshot(after))
        elif path == "/api/pipeline/status":
            try:
                after = int(urllib.parse.parse_qs(parsed.query).get("after", ["0"])[0])
            except ValueError:
                after = 0
            self.send_json(pipeline_snapshot(after))
        elif path.startswith("/api/browse"):
            # The Browse buttons call this with a plain fetch(), which sends GET. It was only
            # routed under do_POST, so every Browse click answered 404 and did nothing.
            self.handle_browse(parsed.query)
        else:
            super().do_GET()

    def do_POST(self):
        if not self._request_allowed():
            return self._deny()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            self.send_response(413)
            self.end_headers()
            return
        post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            body = json.loads(post_data.decode("utf-8"))
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        try:
            self._dispatch_post(path, parsed, body)
        except ValueError as exc:
            self.send_json({"status": "error", "message": str(exc), "stages": [], "failed": []}, status=400)
        except Exception as exc:
            try:
                self.send_json({"status": "error", "message": f"{type(exc).__name__}: {exc}",
                                "stages": [], "failed": []}, status=500)
            except Exception:
                pass

    def _dispatch_post(self, path, parsed, body):
        if path == "/api/inspect":
            self.handle_inspect(body)
        elif path == "/api/pipeline/start":
            self.handle_pipeline_start(body)
        elif path == "/api/pipeline/stage":
            self.handle_pipeline_stage(body)
        elif path == "/api/scripts/scan":
            self.handle_scripts_scan(body)
        elif path == "/api/scripts/export":
            self.handle_scripts_export(body)
        elif path == "/api/scripts/auto-playable-preset":
            self.handle_auto_playable_preset(body)
        elif path == "/api/tools/install":
            name = str(body.get("tool", ""))
            if name not in [tool["name"] for tool in tool_updates.TOOLS]:
                raise ValueError("Unknown tool.")
            with PIPELINE_LOCK:
                busy = install_snapshot()["running"] or PIPELINE_STATE["running"]
                if not busy:
                    install_set(running=True, tool=name, percent=0, message="Starting...", error="",
                                started_at=time.time(), finished_at=0.0)
            if busy:
                self.send_json({"status": "busy", "message": "A recovery run or install is already running."}, status=409)
            else:
                approved = {"version": str(body.get("version", "")), "sha256": str(body.get("sha256", "")).lower()}
                try:
                    threading.Thread(target=run_install, args=(name, approved), daemon=True).start()
                except Exception:
                    install_set(running=False)
                    raise
                self.send_json({"status": "started", "tool": name})
        elif path == "/api/agents/launch":
            self.handle_agent_launch(body)
        elif path == "/api/craft/run":
            self.handle_craft_run(body)
        elif path == "/api/craft/stop":
            self.send_json({"stopped": craft.stop()})
        elif path == "/api/open-unity":
            self.handle_open_unity(body)
        elif path.startswith("/api/browse"):
            self.handle_browse(parsed.query)
        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def handle_inspect(self, body):
        pkg_path = body.get("path", "")
        if not isinstance(pkg_path, str) or not pkg_path.strip():
            raise ValueError("Choose a package first.")
        info = inspect_game(pkg_path)
        self.send_json(info)

    def handle_pipeline_start(self, body):
        """Runs every stage, reporting the live tool and what each stage actually produced.

        The stage methods return True unconditionally, so a run where AssetRipper and
        Il2CppDumper both failed still reported success. Each stage is now judged on whether
        it wrote any files. The engine's log is piped into PIPELINE_STATE so the page can
        show which tool is working while the run is still going.
        """
        src = str(body.get("source", "")).strip()
        dest = resolve_work(body.get("destination", ""))
        if not src or not os.path.isfile(src) and not os.path.isdir(src):
            raise ValueError("The package file does not exist.")
        if os.path.isdir(src):
            self.send_json({"status": "error", "stages": [], "failed": [],
                            "message": "Folders are not supported. Choose an APK, XAPK or ZIP, "
                                       "or run Recover.ps1 on a Windows build folder."}, status=400)
            return

        info = inspect_game(src)
        missing = tool_needs.missing_tools(info)
        if missing:
            self.send_json({"status": "needs_tools", "engine": info.get("engine", ""), "tools": missing,
                            "message": "Some tools this game needs are not installed.",
                            "stages": [], "failed": []}, status=409)
            return
        if not acquire_run():
            self.send_json({"status": "busy", "message": "A recovery run or install is already in progress.",
                            "stages": [], "failed": []}, status=409)
            return
        pipeline_set(running=True, stage="Inspecting package", tool="", status="running",
                     message="", results=[], log=[], plan=[], stage_index=0, stage_total=0,
                     started_at=time.time(), stage_started_at=time.time(), finished_at=0.0)
        try:
            pipeline = RecoveryPipeline(src, dest, logger=pipeline_log)
            pipeline.claim_workspace(info)
            if info.get("engine") not in ("Unity", "Godot"):
                pipeline_log("WARNING: the engine was not recognised as Unity or Godot, so the Unity steps "
                             "run on a best-effort basis and the result may be incomplete.")

            if info.get("engine") == "Godot":
                stages = [
                    ("Intake", pipeline.run_stage_0_intake, [pipeline.dir_00_original]),
                    ("Recover Godot project", pipeline.run_stage_godot_recover, [pipeline.dir_05_project]),
                ]
            else:
                stages = [
                    ("Intake", pipeline.run_stage_0_intake, [pipeline.dir_00_original]),
                    ("Unpack container", pipeline.run_stage_1_extract, [pipeline.dir_01_extracted]),
                    ("Asset rip", pipeline.run_stage_2_assets, [pipeline.dir_02_assetripper]),
                    ("Code recovery", pipeline.run_stage_3_code_recovery,
                     [pipeline.dir_03_il2cpp, pipeline.dir_03_scripts, pipeline.dir_04_jadx]),
                    ("Rebuild Unity project", pipeline.run_stage_4_scaffold_project, [pipeline.dir_05_project]),
                ]

            pipeline_set(plan=[name for name, _run, _folders in stages], stage_total=len(stages))
            for position, (name, run, folders) in enumerate(stages, start=1):
                pipeline_set(stage=name, tool="", stage_index=position, stage_started_at=time.time())
                before = pipeline.count_files(*folders)
                started = time.time() - 1
                try:
                    outcome = run(info)
                except Exception as exc:
                    pipeline.record(name, False, f"{type(exc).__name__}: {exc}")
                    pipeline_set(results=list(pipeline.results))
                    continue
                after = pipeline.count_files(*folders)
                produced = after - before
                fresh = pipeline.newest_mtime(*folders) >= started
                where = ", ".join(os.path.basename(f) for f in folders)
                # A stage that says it failed (for example Godot recovery without project.godot)
                # is a failure even when it wrote some files.
                ok = after > 0 and (produced > 0 or fresh) and outcome is not False
                pipeline.record(name, ok,
                                (f"{produced} new file(s) in {where}" if produced > 0
                                 else f"{after} file(s) refreshed in {where}") if ok
                                else f"nothing written to {where}")
                pipeline_set(results=list(pipeline.results))

            pipeline_set(stage="Writing report", tool="")
            pipeline.generate_report(info)

            failed = [r["stage"] for r in pipeline.results if not r["ok"]]
            if failed:
                message = (f"{len(failed)} of {len(pipeline.results)} stages produced nothing: "
                           + ", ".join(failed)
                           + ". The rebuilt project is incomplete - see the recovery report.")
            else:
                message = "All stages produced output."
            status = "partial" if failed else "complete"
            pipeline_set(stage="Finished", tool="", status=status, message=message, finished_at=time.time())
            release_run()
            self.send_json({
                "status": status,
                "message": message,
                "stages": pipeline.results,
                "failed": failed,
            })
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            pipeline_set(stage="Failed", tool="", status="error", message=message, finished_at=time.time())
            release_run()
            self.send_json({"status": "error", "message": message, "stages": [], "failed": []})

    def handle_pipeline_stage(self, body):
        src = str(body.get("source", "")).strip()
        dest = resolve_work(body.get("destination", ""))
        stage = body.get("stage", 1)
        if stage not in (1, 2, 3, 4):
            raise ValueError("stage must be 1 to 4")
        if not src or not os.path.isfile(src):
            raise ValueError("The package file does not exist.")
        if inspect_game(src).get("engine") == "Godot":
            raise ValueError("Godot games recover in one step. Use the full recovery button.")
        if not acquire_run():
            self.send_json({"status": "busy", "message": "A recovery run or install is already in progress."}, status=409)
            return
        try:
            pipeline_set(running=True, stage=f"Stage {stage}", tool="", status="running", message="", results=[], log=[],
                         plan=[f"Stage {stage}"], stage_index=1, stage_total=1,
                         started_at=time.time(), stage_started_at=time.time(), finished_at=0.0)
            info = inspect_game(src)
            pipeline = RecoveryPipeline(src, dest, logger=pipeline_log)
            pipeline.create_directories()
            pipeline.claim_workspace(info)
            folders = {1: [pipeline.dir_00_original, pipeline.dir_01_extracted],
                       2: [pipeline.dir_02_assetripper],
                       3: [pipeline.dir_03_il2cpp, pipeline.dir_03_scripts, pipeline.dir_04_jadx],
                       4: [pipeline.dir_05_project]}[stage]
            before = pipeline.count_files(*folders)
            started = time.time() - 1
            if stage == 1:
                pipeline.run_stage_0_intake(info)
                pipeline.run_stage_1_extract(info)
            elif stage == 2:
                pipeline.run_stage_2_assets(info)
            elif stage == 3:
                pipeline.run_stage_3_code_recovery(info)
            else:
                pipeline.run_stage_4_scaffold_project(info)
                pipeline.generate_report(info)
            after = pipeline.count_files(*folders)
            ok = after > 0 and (after > before or pipeline.newest_mtime(*folders) >= started)
        finally:
            release_run()
        self.send_json({"status": "complete" if ok else "empty", "stage": stage,
                        "message": "" if ok else "The stage wrote no files. Check the log."})

    def handle_scripts_scan(self, body):
        work_folder = resolve_work(body.get("work_folder", ""), must_exist=True)
        # AssetRipper's recovered C# is the real script set for Mono builds; 03_scripts holds
        # the IL2CPP dump and web files.
        scripts_dir = os.path.join(work_folder, "02_assetripper", "ExportedProject", "Assets", "Scripts")
        if not os.path.isdir(scripts_dir) or not any(files for _r, _d, files in os.walk(scripts_dir)):
            scripts_dir = os.path.join(work_folder, "03_scripts")
        if not os.path.exists(scripts_dir):
            scripts_dir = os.path.join(work_folder, "01_extracted")

        items = scan_and_classify_directory(scripts_dir)
        results = [item.to_dict() for item in items]
        self.send_json(results)

    def handle_scripts_export(self, body):
        work_folder = resolve_work(body.get("work_folder", ""), must_exist=True)
        scripts = body.get("scripts", [])
        if not isinstance(scripts, list):
            raise ValueError("scripts must be a list")
        target_dir = os.path.join(work_folder, "05_project", "Assets", "_Project", "Scripts")

        root = work_folder
        items = []
        for s in scripts:
            if not isinstance(s, dict):
                continue
            source = os.path.realpath(str(s.get("path", "")))
            if not _inside(source, root) or not os.path.isfile(source):
                continue  # only files inside the work folder can be copied
            item = ScriptItem(source, work_folder)
            item.category = str(s.get("category", "Core Gameplay"))
            item.filename = os.path.basename(str(s.get("filename") or source))
            item.is_selected = True
            items.append(item)

        exp, skp = export_selected_scripts(items, target_dir, [AR_SCRIPTS(work_folder)])
        self.send_json({"status": "exported", "exported": exp, "skipped": skp, "target": target_dir})

    def handle_auto_playable_preset(self, body):
        work_folder = resolve_work(body.get("work_folder", ""), must_exist=True)
        proj_dir = os.path.join(work_folder, "05_project")
        if RecoveryPipeline.workspace_engine(work_folder) == "Godot" or os.path.exists(os.path.join(proj_dir, "project.godot")):
            raise ValueError("This is a Godot workspace. The Unity scaffold does not apply to it.")
        scripts_dir = AR_SCRIPTS(work_folder)
        if not os.path.isdir(scripts_dir) or not any(files for _r, _d, files in os.walk(scripts_dir)):
            scripts_dir = os.path.join(work_folder, "03_scripts")

        # 1. Classify and auto-export gameplay only
        items = scan_and_classify_directory(scripts_dir)
        target_scripts_dir = os.path.join(proj_dir, "Assets", "_Project", "Scripts")

        gameplay_items = []
        for item in items:
            if item.category in ["Core Gameplay", "UI & Presentation", "Game Systems & Audio"]:
                item.is_selected = True
                gameplay_items.append(item)
            else:
                item.is_selected = False

        export_selected_scripts(gameplay_items, target_scripts_dir, [AR_SCRIPTS(work_folder)])

        # 2. Scaffold Unity project and generate missing SDK stubs
        res = scaffold_unity_project(proj_dir, body.get("unity_version", ""), "RecoveredGame")
        self.send_json({"status": "playable_ready", "results": res, "gameplay_scripts_exported": len(gameplay_items)})

    def handle_agent_launch(self, body):
        if body.get("confirm_send") is not True:
            raise ValueError("Confirm that the agent will read the package and its output and send it to its provider.")
        src = str(body.get("source", "")).strip()
        if not src or not os.path.exists(src):
            raise ValueError("The package does not exist.")
        work_folder = resolve_work(body.get("destination", ""))
        with PIPELINE_LOCK:
            if PIPELINE_STATE["running"] or install_snapshot()["running"] or craft.is_running():
                self.send_json({"status": "busy", "message": "Another run or install is in progress."}, status=409)
                return
        info = inspect_game(src)
        use_rea = body.get("use_rea") is True
        missing = tool_needs.missing_tools(info, extra=["REA"] if use_rea else ())
        if missing:
            self.send_json({"status": "needs_tools", "engine": info.get("engine", ""), "tools": missing,
                            "message": "Some tools are not installed."}, status=409)
            return
        agents.check_agent(str(body.get("agent", "")))
        RecoveryPipeline(src, work_folder).claim_workspace(info)
        result = agents.launch(str(body.get("agent", "")), src, work_folder, info, use_rea=use_rea)
        self.send_json({"status": "started", **result})

    def handle_craft_run(self, body):
        if body.get("confirm_send") is not True:
            raise ValueError("Confirm that your project files and task will be sent to the chosen agent's provider.")
        work_folder = resolve_work(body.get("work_folder", ""), must_exist=True)
        rounds = body.get("rounds", 2)
        if isinstance(rounds, bool) or not isinstance(rounds, int):
            raise ValueError("Rounds must be a whole number.")
        with PIPELINE_LOCK:
            if PIPELINE_STATE["running"] or install_snapshot()["running"] or craft.is_running():
                self.send_json({"status": "busy", "message": "Another run or install is in progress."}, status=409)
                return
            # Claim the slot before the copy starts so a second click cannot start another run.
            craft.STATE.update(running=True, status="starting", message="", folder="", log=[])
        try:
            folder = craft.start(work_folder, str(body.get("task", "")), str(body.get("backend", "")),
                                 str(body.get("cli", "")), rounds)
        except Exception:
            craft.STATE.update(running=False, status="idle")
            raise
        self.send_json({"status": "started", "folder": folder})

    def handle_open_unity(self, body):
        """Opens 05_project in the matching Unity editor. No batch file is ever run: a recovered
        project can contain one, and this button should not execute anything it holds."""
        import re as _re
        work_folder = resolve_work(body.get("work_folder", ""), must_exist=True)
        proj_dir = os.path.join(work_folder, "05_project")
        if RecoveryPipeline.workspace_engine(work_folder) == "Godot":
            raise ValueError("This is a Godot workspace; there is no Unity project to open. Open 05_project in the Godot editor.")
        version_file = os.path.join(proj_dir, "ProjectSettings", "ProjectVersion.txt")
        if not os.path.isfile(version_file):
            raise ValueError("There is no Unity project in 05_project (a Godot workspace has none).")
        with open(version_file, "r", encoding="utf-8", errors="ignore") as handle:
            match = _re.search(r"m_EditorVersion:\s*(\S+)", handle.read())
        version = match.group(1) if match else ""
        line = ".".join(version.split(".")[:2])
        exe = next((path for ver, path in INSTALLED_EDITORS if ver == version), None) \
            or next((path for ver, path in INSTALLED_EDITORS if line and ver.startswith(line + ".")), None)
        if not exe:
            raise ValueError(f"Unity {version or '(unknown version)'} is not installed. Install it with Unity Hub, "
                             "or open the folder yourself in another version (Unity will upgrade the project).")
        subprocess.Popen([exe, "-projectPath", proj_dir])
        self.send_json({"status": "launched", "exe": exe, "version": version})

    def handle_browse(self, query):
        """Browse File / Browse Folder.

        The picker runs as a separate process (pick_path.py). Opening a Tk dialog directly in
        this request would fail: requests are served on worker threads and Tk has to own the
        thread it runs on. Keeping it outside also means the studio stays usable while the
        picker is on screen.
        """
        qs = urllib.parse.parse_qs(query)
        browse_type = qs.get("type", ["file"])[0]
        if browse_type not in ("file", "folder"):
            browse_type = "file"

        # sys.executable is pythonw.exe when the studio was started by desktop_shell.py, and
        # pythonw has no stdout for the picker to answer on. Use python.exe and hide its console.
        python = sys.executable
        console = os.path.join(os.path.dirname(python), "python.exe")
        if os.path.exists(console):
            python = console

        try:
            done = subprocess.run(
                [python, os.path.join(APP_DIR, "pick_path.py"), browse_type],
                capture_output=True,
                text=True,
                timeout=900,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            self.send_json({"path": (done.stdout or "").strip()})
        except subprocess.TimeoutExpired:
            self.send_json({"path": "", "error": "The file picker was left open too long."})
        except Exception as exc:
            self.send_json({"path": "", "error": str(exc)})

    def handle_script_read(self, query):
        qs = urllib.parse.parse_qs(query)
        f_path = qs.get("path", [""])[0]
        work = qs.get("work_folder", [""])[0]
        real = os.path.realpath(f_path) if f_path else ""
        try:
            root = resolve_work(work, must_exist=True)
        except ValueError:
            root = ""
        if not root or not real or not _inside(real, root) \
                or os.path.splitext(real)[1].lower() not in (".cs", ".txt", ".json", ".shader", ".md", ".js", ".ts", ".java", ".html"):
            f_path = ""
        if f_path and os.path.isfile(f_path):
            try:
                with open(f_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(60000)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
                return
            except Exception:
                pass
        self.send_response(404)
        self.end_headers()

    def handle_tools_status(self):
        tools = [
            {"name": "AssetRipper 2.x", "role": "Mesh, Texture & Scene Ripper", "path": os.path.join(TOOLS_ROOT, "AssetRipper", "AssetRipper.GUI.Free.exe")},
            {"name": "AssetStudio Mod (CLI/GUI)", "role": "Asset Bundle & CAB Inspector", "path": os.path.join(TOOLS_ROOT, "AssetStudioModCLI", "AssetStudioModCLI_net9_win64", "AssetStudioModCLI.exe")},
            {"name": "GDRE Tools (Godot)", "role": "Godot Project Recovery", "path": os.path.join(TOOLS_ROOT, "gdre_tools", "gdre_tools.exe")},
            {"name": "Il2CppDumper", "role": "IL2CPP Symbol & DummyDll Dumper", "path": os.path.join(TOOLS_ROOT, "Il2CppDumper", "Il2CppDumper.exe")},
            {"name": "Cpp2IL", "role": "AOT Binary to C# Pseudocode", "path": os.path.join(TOOLS_ROOT, "Cpp2IL", "Cpp2IL.exe")},
            {"name": "ILSpy (GUI)", "role": "Managed C# Assembly Decompiler", "path": os.path.join(TOOLS_ROOT, "ILSpy", "ILSpy.exe")},
            {"name": "Mono.Cecil", "role": "IL Bytecode Inspector & Patching", "path": os.path.join(TOOLS_ROOT, "Mono.Cecil", "lib", "net40", "Mono.Cecil.dll")},
            {"name": "Ghidra 12.1.4 (NSA)", "role": "Deep Native Disassembler/Decompiler", "path": os.path.join(TOOLS_ROOT, "ghidra", "support", "analyzeHeadless.bat")},
            {"name": "JADX 1.5.6", "role": "Android DEX / Java Decompiler", "path": os.path.join(TOOLS_ROOT, "jadx", "bin", "jadx.bat")},
            {"name": "7-Zip 26.05 (x64)", "role": "High Speed Multi-Thread Unpacker", "path": os.path.join(TOOLS_ROOT, "7-Zip", "7z.exe")},
            {"name": "LLVM Clang & Objdump", "role": "Native Binary Disassembly Toolchain", "path": os.path.join(TOOLS_ROOT, "llvm", "bin", "llvm-objdump.exe")},
            {"name": "FFmpeg Studio", "role": "Audio / Video Codec Transcoder", "path": os.path.join(TOOLS_ROOT, "ffmpeg", "bin", "ffmpeg.exe")},
            {"name": "Python 3.12 (UnityPy)", "role": "Asset Serialization Scripting", "path": os.path.join(TOOLS_ROOT, "pyenv", "Scripts", "python.exe")},
            {"name": ".NET 8 SDK", "role": "C# Execution & Tooling Runtime", "path": os.path.join(TOOLS_ROOT, "dotnet8", "dotnet.exe")},
            {"name": "Android NDK & Clang", "role": "Native C/C++ Build Toolchain", "path": os.path.join(TOOLS_ROOT, "android-sdk", "ndk")},
            {"name": "Android SDK (aapt2/adb)", "role": "Android Manifest & APK Parsing", "path": os.path.join(TOOLS_ROOT, "android-sdk", "platform-tools", "adb.exe")},
            {"name": "Unity Editor (Host)", "role": "Active Matching Editor Target", "path": INSTALLED_EDITORS[0][1] if INSTALLED_EDITORS else r"C:\Program Files\Unity\Hub\Editor"}
        ]
        out = []
        for t in tools:
            p = t.get("path", "")
            exists = os.path.exists(p)
            out.append({
                "name": t["name"],
                "role": t.get("role", "Tool Engine"),
                "status": "PASS  ✓" if exists else "FAIL  ✗",
                "path": p
            })
        self.send_json(out)

def launch_studio(open_window=True):
    # Start HTTP server
    server = ThreadingHTTPServer(("127.0.0.1", PORT), StudioRequestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[AETHER RECON] Server running at http://127.0.0.1:{PORT}")

    # Launch in standalone App Window Mode
    app_url = f"http://127.0.0.1:{PORT}"
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ]
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    ]

    if open_window:
        profile_root = os.path.join(
            os.environ.get("LOCALAPPDATA", APP_DIR), "AetherReconStudio", "WindowProfile")
        launched = False
        for p in chrome_paths + edge_paths:
            if os.path.exists(p):
                profile = os.path.join(profile_root, os.path.splitext(os.path.basename(p))[0])
                os.makedirs(profile, exist_ok=True)
                sentinel = os.path.join(profile, "First Run")
                if not os.path.exists(sentinel):
                    open(sentinel, "w").close()
                cmd = [p, f"--app={app_url}", f"--user-data-dir={profile}",
                       "--window-size=1260,820", "--window-position=60,40",
                       "--no-first-run", "--no-default-browser-check"]
                subprocess.Popen(cmd)
                launched = True
                break

        if not launched:
            import webbrowser
            webbrowser.open(app_url)

    # Keep main thread alive
    while True:
        time.sleep(1)

if __name__ == "__main__":
    # desktop_shell.py passes --server-only: it opens and owns the window itself.
    launch_studio(open_window="--server-only" not in sys.argv)
