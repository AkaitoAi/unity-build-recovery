"""
Recovery Engine for Unity Recovery Studio.
Executes the full recovery pipeline completely offline:
- Stage 0: Intake & SHA256 record
- Stage 1: Extraction via 7-Zip (with nested split APK unpacking)
- Stage 2: Asset Ripping (AssetRipper / Web extractor)
- Stage 3: Code Dumping (Il2CppDumper / Cpp2IL / Web assets)
- Stage 4: Project scaffolding and SDK stubs (the result is not compiled or verified)
- Stage 5: RECOVERY_REPORT.md generation
"""

import os
import re
import json
import time
import shutil
import subprocess
import datetime
from typing import Callable, Optional
from scanner import inspect_game, TOOLS_ROOT, SEVEN_ZIP
from unity_playable_scaffolder import scaffold_unity_project, setup_playable_web_runner, INSTALLED_EDITORS

class RecoveryPipeline:
    def __init__(self, input_file: str, work_folder: str, logger: Optional[Callable[[str], None]] = None):
        self.input_file = input_file
        self.work_folder = work_folder
        self.logger = logger or (lambda msg: print(msg))
        self.is_running = False
        # What each stage really produced. Every stage method returns True whatever happens,
        # so the only trustworthy signal is whether files appeared on disk.
        self.results = []

        # Output subdirectories
        self.dir_00_original = os.path.join(work_folder, "00_original")
        self.dir_01_extracted = os.path.join(work_folder, "01_extracted")
        self.dir_02_assetripper = os.path.join(work_folder, "02_assetripper")
        self.dir_03_scripts = os.path.join(work_folder, "03_scripts")
        self.dir_03_il2cpp = os.path.join(work_folder, "03_il2cpp")
        self.dir_04_jadx = os.path.join(work_folder, "04_jadx")
        self.dir_05_project = os.path.join(work_folder, "05_project")
        self.dir_reports = os.path.join(work_folder, "reports")

    def log(self, message: str):
        self.logger(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {message}")

    @staticmethod
    def count_files(*folders) -> int:
        """How many files exist under the given folders, at any depth."""
        total = 0
        for folder in folders:
            for _root, _dirs, files in os.walk(folder):
                total += len(files)
        return total

    @staticmethod
    def newest_mtime(*folders) -> float:
        """Modification time of the newest file under the folders, or 0 when there are none."""
        newest = 0.0
        for folder in folders:
            for root, _dirs, files in os.walk(folder):
                for name in files:
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(root, name)))
                    except OSError:
                        pass
        return newest

    def claim_workspace(self, info: dict) -> None:
        """Refuses to mix two different games in one work folder.

        Stages reuse and overwrite what is already in the folder, so a second game would
        inherit the first game's extracted files, dump.cs and dummy assemblies.
        """
        os.makedirs(self.work_folder, exist_ok=True)
        marker = os.path.join(self.work_folder, ".run.json")
        sha = str(info.get("sha256") or "")
        if os.path.exists(marker):
            try:
                with open(marker, "r", encoding="utf-8") as handle:
                    previous = json.load(handle).get("sha256", "")
            except Exception:
                previous = ""
            if previous and sha and previous != sha:
                raise ValueError("This work folder already holds a different package. "
                                 "Choose an empty folder for each game.")
            return
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump({"sha256": sha, "name": info.get("name", "")}, handle)

    def record(self, stage: str, ok: bool, detail: str = "") -> bool:
        """Records the real outcome of a stage and says so in the console."""
        ok = bool(ok)
        self.results.append({"stage": stage, "ok": ok, "detail": detail})
        self.log(("[OK] " if ok else "[FAILED] ") + stage + (f" - {detail}" if detail else ""))
        return ok

    def create_directories(self):
        for d in [
            self.dir_00_original, self.dir_01_extracted, self.dir_02_assetripper,
            self.dir_03_scripts, self.dir_03_il2cpp, self.dir_04_jadx,
            self.dir_05_project, self.dir_reports
        ]:
            os.makedirs(d, exist_ok=True)

    def run_stage_0_intake(self, info: dict) -> bool:
        self.log(f"--- [STAGE 0] Intake & Verification ---")
        if os.path.isdir(self.input_file):
            raise ValueError("Folders are not supported here. Give the Studio an APK, XAPK or ZIP, "
                             "or use Recover.ps1 for Windows build folders.")
        self.create_directories()

        dest = os.path.join(self.dir_00_original, os.path.basename(self.input_file))
        if not os.path.exists(dest):
            self.log(f"Copying game package to 00_original...")
            shutil.copy2(self.input_file, dest)
        else:
            os.utime(dest, None)  # mark it as checked on this run
            self.log("Original copy already present in 00_original.")

        self.log(f"Game: {info.get('name')}")
        self.log(f"Engine: {info.get('engine')} ({info.get('backend')})")
        self.log(f"SHA256: {info.get('sha256')}")
        self.log(f"Size: {info.get('size_mb')} MB")
        return True

    def run_stage_godot_recover(self, info: dict) -> bool:
        """Recovers a Godot project from a PCK, EXE or APK with GDRE Tools into 05_project."""
        self.log("--- [GODOT] Project recovery with GDRE Tools ---")
        exe = os.path.join(TOOLS_ROOT, "gdre_tools", "gdre_tools.exe")
        if not os.path.exists(exe):
            raise FileNotFoundError(f"GDRE Tools not found at {exe}.")
        source = os.path.join(self.dir_00_original, os.path.basename(self.input_file))
        if not os.path.exists(source):
            raise FileNotFoundError("Run the intake stage first: the original copy is missing.")
        cmd = [exe, "--headless", f"--recover={source}", f"--output={self.dir_05_project}"]
        self.log("Executing: " + " ".join(cmd))
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                 stdin=subprocess.DEVNULL, timeout=3600, cwd=self.work_folder)
        except subprocess.TimeoutExpired:
            self.log("  [GDRE] timed out after 60 minutes.")
            return False
        tail = [line.strip() for line in (res.stdout or "").splitlines() if line.strip()][-25:]
        for line in tail:
            self.log(f"  [GDRE] {line}")
        if res.returncode != 0:
            self.log(f"  [GDRE] exited with code {res.returncode}: {(res.stderr or '').strip()[:300]}")
        if not os.path.exists(os.path.join(self.dir_05_project, "project.godot")):
            self.log("  No project.godot was written. If the log mentions encryption, the game is "
                     "encrypted and needs its key, which the studio does not take yet.")
            return False
        self.log("Recovered a Godot project. Open 05_project in the Godot editor version named in the log.")
        return True

    def run_stage_1_extract(self, info: dict) -> bool:
        self.log(f"--- [STAGE 1] Unpacking Package with 7-Zip ---")
        if not os.path.exists(SEVEN_ZIP):
            raise FileNotFoundError(f"7-Zip not found at {SEVEN_ZIP}. Install it there or set RE_TOOLS.")
        cmd = [SEVEN_ZIP, "x", self.input_file, f"-o{self.dir_01_extracted}", "-y"]
        self.log(f"Executing: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                             stdin=subprocess.DEVNULL, timeout=3600)
        if res.returncode != 0:
            self.log(f"  7-Zip exited with code {res.returncode}: "
                     f"{(res.stderr or res.stdout or '').strip()[:200]}")

        # If XAPK or split APKs, unpack inner APKs as well
        for root, dirs, files in os.walk(self.dir_01_extracted):
            for f in files:
                if f.endswith(".apk") and not f.startswith("extracted_"):
                    apk_path = os.path.join(root, f)
                    out_sub = os.path.join(root, "unpacked_" + os.path.splitext(f)[0])
                    self.log(f"Unpacking nested APK: {f} -> {out_sub}")
                    sub_cmd = [SEVEN_ZIP, "x", apk_path, f"-o{out_sub}", "-y"]
                    sub_res = subprocess.run(sub_cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=3600)
                    if sub_res.returncode != 0:
                        self.log(f"  7-Zip exited with code {sub_res.returncode} for {f}")

        if res.returncode == 0:
            now = time.time()  # a re-run over identical files still counts as a fresh extraction
            for root, _dirs, files in os.walk(self.dir_01_extracted):
                for name in files:
                    try:
                        os.utime(os.path.join(root, name), (now, now))
                    except OSError:
                        pass

        count = sum(len(files) for _, _, files in os.walk(self.dir_01_extracted))
        self.log(f"Stage 1 Complete. Extracted {count} files to 01_extracted.")
        return True

    def find_il2cpp_inputs(self):
        """Locates libil2cpp.so and global-metadata.dat among the extracted files."""
        so_path = metadata_path = None
        for root, _dirs, files in os.walk(self.dir_01_extracted):
            for f in files:
                if f == "libil2cpp.so":
                    if "arm64" in root or not so_path:
                        so_path = os.path.join(root, f)
                elif f == "global-metadata.dat":
                    metadata_path = os.path.join(root, f)
        return so_path, metadata_path

    def _find_dummy_dir(self):
        """The folder of generated assemblies, wherever the tool chose to put it.

        Il2CppDumper makes a DummyDll subfolder; Cpp2IL writes them into the output root.
        The one that counts is whichever folder holds the game's own Assembly-CSharp.dll.
        """
        best = None
        best_count = 0
        for root, _dirs, files in os.walk(self.dir_03_il2cpp):
            dlls = [f for f in files if f.endswith(".dll")]
            if not dlls:
                continue
            if "Assembly-CSharp.dll" in dlls:
                return root
            if len(dlls) > best_count:
                best, best_count = root, len(dlls)
        return best

    def generate_dummy_dlls(self, info):
        """Recovers type information from the IL2CPP binary as DummyDll assemblies.

        Without these, every script AssetRipper exports is an empty "Dummy class": the class
        names, fields and serialized inspector values live in the IL2CPP binary and metadata,
        not in the serialized assets. Tries Il2CppDumper, then any Cpp2IL build present,
        newest filename first, because support for a game depends on its metadata version.
        """
        so_path, metadata_path = self.find_il2cpp_inputs()
        if not (so_path and metadata_path):
            return None

        attempts = []
        dumper = os.path.join(TOOLS_ROOT, "Il2CppDumper", "Il2CppDumper.exe")
        if os.path.exists(dumper):
            attempts.append(("Il2CppDumper", [dumper, so_path, metadata_path, self.dir_03_il2cpp]))

        # Newest build first, by file date: which Cpp2IL can read a game depends on its
        # metadata version, and only recent builds handle Unity 6 era games.
        cpp_dir = os.path.join(TOOLS_ROOT, "Cpp2IL")
        if os.path.isdir(cpp_dir):
            game_path = os.path.dirname(metadata_path)
            while True:
                parent = os.path.dirname(game_path)
                if parent == game_path:
                    game_path = os.path.dirname(os.path.dirname(metadata_path))
                    break
                if os.path.isdir(os.path.join(parent, "assets")):
                    game_path = parent
                    break
                game_path = parent
            exes = [f for f in os.listdir(cpp_dir) if f.lower().endswith(".exe")]
            exes.sort(key=lambda f: os.path.getmtime(os.path.join(cpp_dir, f)), reverse=True)
            for exe in exes:
                full = os.path.join(cpp_dir, exe)
                # Cpp2IL rejects the force options unless all three are given together,
                # which it reports only as "Invalid force option configuration".
                raw_version = str(info.get("unity_version") or "")
                numeric = re.match(r"\d+(?:\.\d+)*", raw_version)
                unity_version = numeric.group(0) if numeric else "6000.3.0"
                common = [full,
                          "--game-path", game_path,
                          "--force-binary-path", so_path,
                          "--force-metadata-path", metadata_path,
                          "--force-unity-version", unity_version]
                # The 2022.1 pre-release line renamed these options; try it first, then the
                # older spelling, so whichever build is present is driven correctly.
                attempts.append((exe + " (current options)",
                                 common + ["--output-as", "dummydll", "--output-to", self.dir_03_il2cpp]))
                attempts.append((exe + " (legacy options)",
                                 common + ["--just-give-me-dlls-asap-dammit", "--output-root", self.dir_03_il2cpp]))

        for name, cmd in attempts:
            self.log(f"Recovering type information with {name}...")
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                        cwd=self.dir_03_il2cpp, timeout=2400, input="\n")
            except Exception as exc:
                self.log(f"  {name} could not run: {exc}")
                continue
            found = self._find_dummy_dir()
            if found:
                count = len([f for f in os.listdir(found) if f.endswith(".dll")])
                self.log(f"  {name} recovered {count} assemblies.")
                return found
            output = ((result.stdout or "") + (result.stderr or "")).replace("", "")
            reasons = [l.strip() for l in output.splitlines()
                       if any(k in l for k in ("nsupported", "Exception", "Fail", "rror"))]
            if reasons:
                for line in reasons[:2]:
                    self.log(f"  [{name}] {line[:170]}")
            else:
                self.log(f"  [{name}] produced no assemblies (exit {result.returncode}).")

        self.log("No installed tool can read this game's metadata version, so exported scripts "
                 "will be empty stubs. A newer Cpp2IL build is usually what fixes this.")
        return None

    def install_dummy_dlls(self, dummy_dir):
        """Puts the recovered assemblies where AssetRipper looks for them: the Managed folder."""
        managed = None
        for root, _dirs, files in os.walk(self.dir_01_extracted):
            if os.path.basename(root).lower() == "managed":
                managed = root
                break
            if "global-metadata.dat" in files:
                managed = os.path.join(os.path.dirname(root), "Managed")
        if not managed:
            self.log("  Could not find a Managed folder to place the assemblies in.")
            return 0
        os.makedirs(managed, exist_ok=True)
        copied = 0
        for f in os.listdir(dummy_dir):
            if f.endswith(".dll"):
                shutil.copy2(os.path.join(dummy_dir, f), os.path.join(managed, f))
                copied += 1
        self.log(f"  Placed {copied} assemblies in Managed/ so AssetRipper can name the scripts.")
        return copied

    def run_stage_2_assets(self, info: dict) -> bool:
        self.log(f"--- [STAGE 2] Asset Recovery ---")

        if info.get("has_web_assets") or "Capacitor" in info.get("engine", ""):
            self.log("Extracting Web/HTML5 Game Assets (public/www assets)...")
            web_found = 0
            for root, dirs, files in os.walk(self.dir_01_extracted):
                if os.path.basename(root) in ["public", "www", "dist", "assets"]:
                    for f in files:
                        src = os.path.join(root, f)
                        rel = os.path.relpath(src, self.dir_01_extracted)
                        dst = os.path.join(self.dir_02_assetripper, rel)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        web_found += 1
            self.log(f"Extracted {web_found} web assets into 02_assetripper.")
            return True

        # AssetRipper reads class names, fields and serialized values from managed
        # assemblies. For an IL2CPP build there are none until they are recovered from the
        # binary, which is why every exported script used to be an empty "Dummy class".
        # This has to happen before the rip, not in stage 3 afterwards.
        dummy_dir = self.generate_dummy_dlls(info)
        if dummy_dir:
            self.install_dummy_dlls(dummy_dir)

        # Unity Game: Use AssetRipper headless script
        ar_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "assetripper-headless.ps1")
        if os.path.exists(ar_script):
            self.log("Launching AssetRipper in headless export mode...")
            cmd = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", ar_script,
                "-InputPath", self.dir_01_extracted,
                "-OutputPath", self.dir_02_assetripper,
                "-ExportTimeoutMin", "30",
                "-AssetRipperExe", os.path.join(TOOLS_ROOT, "AssetRipper", "AssetRipper.GUI.Free.exe")
            ]
            self.log(f"Running AssetRipper on 01_extracted...")
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                     stdin=subprocess.DEVNULL, timeout=2400)
            except subprocess.TimeoutExpired:
                self.log("  [AssetRipper] timed out after 40 minutes.")
                return False
            for line in res.stdout.splitlines():
                if line.strip():
                    self.log(f"  [AssetRipper] {line.strip()}")
            if res.returncode != 0:
                self.log(f"  [AssetRipper] exited with code {res.returncode}: "
                         f"{(res.stderr or '').strip()[:300]}")
        else:
            self.log("AssetRipper headless script not found (scripts/assetripper-headless.ps1); "
                     "the AssetRipper stage did NOT run.")
            return False

        return True

    def generate_isil_dump(self, info):
        """Dumps every method as ARM64 disassembly plus ISIL, a readable instruction form.

        This is the only route to method bodies for an ARM64 build: Cpp2IL's IL recovery
        reconstructs nothing on ARM, and Il2CppDumper cannot read recent metadata at all.
        ISIL resolves call targets to real method names, so the control flow of a method can
        be followed even though it is not C#.
        """
        so_path, metadata_path = self.find_il2cpp_inputs()
        if not (so_path and metadata_path):
            return 0
        cpp_dir = os.path.join(TOOLS_ROOT, "Cpp2IL")
        if not os.path.isdir(cpp_dir):
            return 0
        exes = [f for f in os.listdir(cpp_dir) if f.lower().endswith(".exe")]
        if not exes:
            return 0
        exes.sort(key=lambda f: os.path.getmtime(os.path.join(cpp_dir, f)), reverse=True)

        game_path = os.path.dirname(metadata_path)
        while True:
            parent = os.path.dirname(game_path)
            if parent == game_path:
                game_path = os.path.dirname(os.path.dirname(metadata_path))
                break
            if os.path.isdir(os.path.join(parent, "assets")):
                game_path = parent
                break
            game_path = parent

        raw_version = str(info.get("unity_version") or "")
        numeric = re.match(r"\d+(?:\.\d+)*", raw_version)
        unity_version = numeric.group(0) if numeric else "6000.3.0"
        out_dir = os.path.join(self.dir_03_scripts, "isil")
        os.makedirs(out_dir, exist_ok=True)

        for exe in exes:
            self.log(f"Dumping method bodies as ISIL with {exe} (this takes a minute)...")
            cmd = [os.path.join(cpp_dir, exe),
                   "--game-path", game_path,
                   "--force-binary-path", so_path,
                   "--force-metadata-path", metadata_path,
                   "--force-unity-version", unity_version,
                   "--output-as", "isil",
                   "--output-to", out_dir]
            try:
                subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                               cwd=self.dir_03_scripts, timeout=3600, input="\n")
            except Exception as exc:
                self.log(f"  {exe} could not run: {exc}")
                continue
            produced = self.count_files(out_dir)
            if produced:
                self.log(f"  Dumped {produced} method listings into 03_scripts/isil.")
                # Those listings carry each method's address, which is all Ghidra needs to
                # show the game's own calls by name instead of FUN_0123abcd.
                try:
                    import ghidra_symbols
                    mapped = ghidra_symbols.build(out_dir, os.path.join(self.dir_03_scripts, "ghidra"))
                    if mapped:
                        self.log(f"  Mapped {mapped} method addresses; run "
                                 f"03_scripts/ghidra/ghidra_name_il2cpp_methods.py inside Ghidra "
                                 f"after importing libil2cpp.so.")
                except Exception as exc:
                    self.log(f"  Could not build the Ghidra symbol script: {exc}")
                return produced
        self.log("  No Cpp2IL build could dump this binary.")
        return 0

    def run_jadx(self, info):
        """Decompiles the APK's Java/Kotlin side into 04_jadx.

        A Unity game still carries real Java: the player activity, the ad and analytics SDKs,
        billing, and any native plugin the game ships. None of that is in the IL2CPP binary,
        so without this step that whole half of the app is missing. The folder was created by
        every run but nothing ever wrote to it.
        """
        apks = []
        for folder, _dirs, files in os.walk(self.dir_01_extracted):
            for name in files:
                if name.lower().endswith(".apk"):
                    apks.append(os.path.join(folder, name))
        if not apks:
            original = self.input_file
            if original.lower().endswith(".apk"):
                apks = [original]
        if not apks:
            self.log("No APK found to decompile with JADX.")
            return 0

        # Newest JADX wins: the folders are jadx, jadx-1.5.7 and so on.
        candidates = []
        for entry in (os.listdir(TOOLS_ROOT) if os.path.isdir(TOOLS_ROOT) else []):
            if entry.lower().startswith("jadx"):
                for sub in (os.path.join(TOOLS_ROOT, entry, "bin", "jadx.bat"),
                            os.path.join(TOOLS_ROOT, entry, "jadx.bat")):
                    if os.path.exists(sub):
                        candidates.append(sub)
                        break
        if not candidates:
            self.log("JADX not found in the tools folder.")
            return 0
        candidates.sort(key=os.path.getmtime, reverse=True)
        jadx = candidates[0]

        # The base APK holds the game's own Java; the split configs are resources only.
        apks.sort(key=os.path.getsize, reverse=True)
        target = apks[0]
        self.log(f"Decompiling {os.path.basename(target)} with {os.path.basename(os.path.dirname(os.path.dirname(jadx)))}...")
        try:
            subprocess.run([jadx, "-d", self.dir_04_jadx, "--no-res", target],
                           capture_output=True, text=True, errors="replace", timeout=3600)
        except Exception as exc:
            self.log(f"  JADX could not run: {exc}")
            return 0
        produced = self.count_files(self.dir_04_jadx)
        self.log(f"  JADX wrote {produced} file(s) into 04_jadx."
                 if produced else "  JADX produced nothing.")
        return produced

    def run_stage_3_code_recovery(self, info: dict) -> bool:
        self.log(f"--- [STAGE 3] Code Recovery & Disassembly ---")

        so_path = None
        metadata_path = None

        for root, dirs, files in os.walk(self.dir_01_extracted):
            for f in files:
                if f == "libil2cpp.so":
                    if "arm64" in root or not so_path:
                        so_path = os.path.join(root, f)
                elif f == "global-metadata.dat":
                    metadata_path = os.path.join(root, f)

        if so_path and metadata_path:
            self.log(f"Found IL2CPP Binary: {so_path}")
            self.log(f"Found Metadata: {metadata_path}")
            dumper_exe = os.path.join(TOOLS_ROOT, "Il2CppDumper", "Il2CppDumper.exe")
            if os.path.exists(dumper_exe):
                if os.path.exists(os.path.join(self.dir_03_il2cpp, "dump.cs")):
                    self.log("Il2CppDumper output already present; not running it again.")
                else:
                    self.log(f"Running Il2CppDumper...")
                    cmd = [dumper_exe, so_path, metadata_path, self.dir_03_il2cpp]
                    # stdin is supplied because Il2CppDumper waits for a key press on exit.
                    res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                         cwd=self.dir_03_il2cpp, input="\n", timeout=1800)
                    for line in res.stdout.splitlines()[:15]:
                        if line.strip():
                            self.log(f"  [Il2CppDumper] {line.strip()}")

                dump_cs = os.path.join(self.dir_03_il2cpp, "dump.cs")
                if os.path.exists(dump_cs):
                    shutil.copy2(dump_cs, os.path.join(self.dir_03_scripts, "dump.cs"))
                    self.log("Generated dump.cs with all type declarations & offsets!")

                dummy_dll_dir = os.path.join(self.dir_03_il2cpp, "DummyDll")
                if os.path.exists(dummy_dll_dir):
                    self.log("Dummy DLLs extracted successfully.")
            else:
                self.log("Il2CppDumper.exe not found.")

            # Il2CppDumper cannot read metadata past v31, and IL recovery does nothing on
            # ARM64, so fall back to an ISIL dump, which does work there.
            if not os.path.exists(os.path.join(self.dir_03_il2cpp, "dump.cs")):
                self.generate_isil_dump(info)

        elif info.get("has_web_assets") or "Capacitor" in info.get("engine", ""):
            self.log("Extracting JavaScript / TypeScript application sources...")
            for root, dirs, files in os.walk(self.dir_01_extracted):
                for f in files:
                    if f.endswith(".js") or f.endswith(".json") or f.endswith(".html"):
                        src = os.path.join(root, f)
                        dst = os.path.join(self.dir_03_scripts, f)
                        shutil.copy2(src, dst)
            self.log(f"Scripts saved into 03_scripts.")


        # The Java half of the APK, whatever the engine is.
        self.run_jadx(info)

        return True

    def run_stage_4_scaffold_project(self, info: dict) -> bool:
        self.log(f"--- [STAGE 4] Building Unity Project Folder ---")

        assets_dir = os.path.join(self.dir_05_project, "Assets")
        os.makedirs(assets_dir, exist_ok=True)

        # 2. Copy Ripped Unity Assets if present.
        # AssetRipper writes a whole Unity project under ExportedProject/, so the assets sit a
        # level deeper than this used to look. With the old path nothing was ever copied and
        # 05_project came out holding only the generated stubs, however well the rip had gone.
        ar_root = os.path.join(self.dir_02_assetripper, "ExportedProject")
        ar_assets = os.path.join(ar_root, "Assets")
        if not os.path.exists(ar_assets):
            ar_root = self.dir_02_assetripper
            ar_assets = os.path.join(ar_root, "Assets")
        if os.path.exists(ar_assets):
            self.log(f"Copying ripped assets from {os.path.basename(ar_root)}/Assets into 05_project/Assets...")
            copied = 0
            for item in os.listdir(ar_assets):
                s = os.path.join(ar_assets, item)
                d = os.path.join(assets_dir, item)
                if os.path.isdir(s) and not os.path.exists(d):
                    shutil.copytree(s, d)
                    copied += 1
                elif os.path.isfile(s):
                    shutil.copy2(s, d)
                    copied += 1
            self.log(f"Copied {copied} top-level asset folders/files into the project.")

            # The ripped ProjectSettings carry the game's quality, graphics and input setup.
            ar_settings = os.path.join(ar_root, "ProjectSettings")
            if os.path.isdir(ar_settings):
                dest_settings = os.path.join(self.dir_05_project, "ProjectSettings")
                os.makedirs(dest_settings, exist_ok=True)
                kept = 0
                for item in os.listdir(ar_settings):
                    s = os.path.join(ar_settings, item)
                    d = os.path.join(dest_settings, item)
                    if os.path.isfile(s) and not os.path.exists(d):
                        shutil.copy2(s, d)
                        kept += 1
                self.log(f"Brought across {kept} ProjectSettings file(s) from the rip.")
        else:
            self.log("No ripped assets found to copy; the project will contain stubs only.")

        # 3. Scaffold the project folder (runs after the copy so it can see the game's own SDKs)
        scaffold_results = scaffold_unity_project(
            project_dir=self.dir_05_project,
            detected_unity_ver=info.get("unity_version", ""),
            game_title=info.get("name", "RecoveredGame")
        )
        self.log(f"Matched Unity Editor Version: {scaffold_results['editor_version']}")
        if not scaffold_results["version_exact"]:
            self.log("WARNING: the game's exact Unity version is not installed; Unity will upgrade the project on open.")
        self.log(f"Generated Packages/manifest.json and GameCore.asmdef")
        self.log(f"Generated {len(scaffold_results['stubs_created'])} placeholder SDK stub(s); compilation is not verified.")


        # 3. Create 1-Click Unity Launch Script
        target_unity_exe = None
        for ver, exe in INSTALLED_EDITORS:
            if ver == scaffold_results['editor_version'] and os.path.exists(exe):
                target_unity_exe = exe
                break
        if not target_unity_exe:
            for _, exe in INSTALLED_EDITORS:
                if os.path.exists(exe):
                    target_unity_exe = exe
                    break

        # These live beside the project, not inside it: a Unity project folder should
        # hold only Assets, Packages and ProjectSettings, or the editor imports the
        # stray files as project content.
        if target_unity_exe:
            open_bat = os.path.join(self.work_folder, "Open_In_Unity.bat")
            with open(open_bat, "w", encoding="utf-8") as f:
                f.write(f'@echo off\nstart "" "{target_unity_exe}" -projectPath "%~dp005_project"\n')
            self.log(f"Created 1-Click Unity Launcher: {open_bat}")

        # 4. If Web/Capacitor Game: Also provide instant offline runner
        if "Capacitor" in info.get("engine", "") or info.get("has_web_assets"):
            dist_dir = os.path.join(self.work_folder, "web_runner")
            os.makedirs(dist_dir, exist_ok=True)
            for root, dirs, files in os.walk(self.dir_02_assetripper):
                for f in files:
                    s = os.path.join(root, f)
                    rel = os.path.relpath(s, self.dir_02_assetripper)
                    d = os.path.join(dist_dir, rel)
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    shutil.copy2(s, d)
            web_runner = setup_playable_web_runner(self.work_folder, dist_dir, info.get("name", "WebGame"))
            self.log(f"Created Playable Web Runner: {web_runner}")

        return True

    def generate_report(self, info: dict):
        report_file = os.path.join(self.dir_reports, "RECOVERY_REPORT.md")
        results = self.results or []
        failed = [r for r in results if not r["ok"]]
        lines = []
        lines.append(f"# Recovery Report: {info.get('name')}")
        lines.append("")
        if not results:
            headline = "Not verified - no stage results were recorded."
        elif failed:
            headline = f"PARTIAL - {len(failed)} of {len(results)} stages produced nothing."
        else:
            headline = f"Complete - all {len(results)} stages produced output."
        lines.append(f"**Result: {headline}**")
        lines.append("")
        lines.append(f"- **Date**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"- **Source Package**: `{self.input_file}`")
        lines.append(f"- **Package Size**: {info.get('size_mb')} MB")
        lines.append(f"- **SHA256**: `{info.get('sha256')}`")
        lines.append(f"- **Engine**: {info.get('engine')} ({info.get('backend')})")
        if info.get("engine") == "Unity":
            lines.append(f"- **Unity Version**: {info.get('unity_version')}")
        lines.append(f"- **ABIs**: {', '.join(info.get('abis', []))}")
        lines.append("")
        lines.append("## Stage results")
        lines.append("")
        if results:
            lines.append("| Stage | Result | Output |")
            lines.append("|---|---|---|")
            for r in results:
                lines.append(f"| {r['stage']} | {'OK' if r['ok'] else 'FAILED'} | {r['detail'] or '-'} |")
        else:
            lines.append("No stages were recorded for this run.")
        lines.append("")
        if failed:
            lines.append("## What is missing")
            lines.append("")
            lines.append("These stages wrote no files, so anything downstream of them is incomplete:")
            lines.append("")
            for r in failed:
                lines.append(f"- **{r['stage']}** - {r['detail'] or 'produced nothing'}")
            lines.append("")
            lines.append("A common cause is a game built with a newer engine version than the bundled")
            lines.append("AssetRipper or Il2CppDumper supports. Check the Live Diagnostic Console and")
            lines.append("the tool logs for the exact error.")
            lines.append("")
        lines.append("## Folders")
        lines.append("")
        for folder, what in (
            (self.dir_00_original, "pristine copy of the source archive"),
            (self.dir_01_extracted, "unpacked container files"),
            (self.dir_02_assetripper, "ripped assets and materials"),
            (self.dir_03_il2cpp, "IL2CPP dump output"),
            (self.dir_03_scripts, "recovered scripts"),
            (self.dir_04_jadx, "Java/DEX decompilation"),
            (self.dir_05_project, "generated Unity project"),
        ):
            count = self.count_files(folder)
            state = f"{count} file(s)" if count else "EMPTY"
            lines.append(f"- `{os.path.basename(folder)}/` - {what}: {state}")
        with open(report_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.log(f"Recovery report generated at: {report_file}")
