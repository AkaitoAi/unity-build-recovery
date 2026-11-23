"""The agent prompt must follow the same order and names as the studio itself."""
import importlib
import os
import sys
import tempfile
import unittest

TOOL_ROOT = tempfile.mkdtemp()
os.environ["RE_TOOLS"] = TOOL_ROOT
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "studio"))
import paths  # noqa: E402
import agent_prompt  # noqa: E402

IL2CPP = {"name": "Toy", "engine": "Unity", "has_il2cpp": True, "unity_version": "2022.3.62f3", "sha256": "ab"}
MONO = {"name": "M", "engine": "Unity", "has_mono": True}
GODOT = {"name": "G", "engine": "Godot"}


def prompt(source, info):
    return agent_prompt.build_prompt(source, r"D:\Rec\Game", info)


class PromptOrder(unittest.TestCase):
    def test_il2cpp_runs_the_code_step_before_the_asset_rip_and_installs_the_dummy_assemblies(self):
        text = prompt(r"C:\g\toy.apk", IL2CPP)
        code, rip = text.index("**Code first (IL2CPP).**"), text.index("**Assets and scenes, after the code step.**")
        self.assertLess(code, rip)
        between = text[code:rip]
        self.assertIn("Managed", between)
        self.assertIn("DummyDll", between)

    def test_mono_does_not_depend_on_a_separate_decompiler(self):
        text = prompt(r"C:\g\m.apk", MONO)
        self.assertIn("ExportedProject\\Assets\\Scripts", text)
        self.assertIn("decompiler is optional", text)

    def test_godot_unwraps_containers_before_running_gdre(self):
        text = prompt(r"C:\g\game.xapk", GODOT)
        unwrap, recover = text.index("**Pick what GDRE reads.**"), text.index("**Recover.**")
        self.assertLess(unwrap, recover)
        for word in (".xapk", ".pck", "Expand-Archive"):
            self.assertIn(word, text[unwrap:recover])

    def test_a_folder_package_has_no_unpack_step_and_no_hash_step(self):
        folder = tempfile.mkdtemp()
        text = prompt(folder, MONO)
        self.assertIn("robocopy", text)
        self.assertIn("build), so it has no single hash", text)
        self.assertNotIn(" x \"" + folder, text)           # no 7z extract on a folder
        self.assertNotIn("confirm its SHA-256", text.split("2. **Copy.**")[0])


class PromptNamesMatchTheStudio(unittest.TestCase):
    def test_the_agent_writes_agent_files_and_never_studio_files(self):
        text = prompt(r"C:\g\toy.apk", IL2CPP)
        self.assertIn("AGENT_AUDIT.json", text)
        self.assertIn("Never write a file whose name starts with `STUDIO_AUDIT`", text)
        self.assertNotIn("reports\\AUDIT.json", text)

    def test_the_prompt_uses_the_studios_definition_of_a_missing_script(self):
        text = prompt(r"C:\g\toy.apk", IL2CPP)
        self.assertIn("m_Script: {fileID: 0}", text)
        self.assertIn("do not count as missing", text)

    def test_versioned_jadx_is_found_like_the_pipeline_finds_it(self):
        os.makedirs(os.path.join(TOOL_ROOT, "jadx-v1.5.6", "bin"), exist_ok=True)
        with open(os.path.join(TOOL_ROOT, "jadx-v1.5.6", "bin", "jadx.bat"), "w") as handle:
            handle.write("@echo off")
        importlib.reload(paths)
        importlib.reload(agent_prompt)
        text = agent_prompt.build_prompt(r"C:\g\m.apk", r"D:\Rec\Game", MONO)
        self.assertIn("JADX (available)", text)
        self.assertNotIn("JADX (NOT INSTALLED)", text)

    def test_every_prompt_fits_the_clipboard_limit_and_keeps_the_safety_rules(self):
        for info, source in ((IL2CPP, r"C:\g\a.apk"), (MONO, r"C:\g\b.apk"), (GODOT, r"C:\g\c.pck"), ({"engine": "Unknown"}, r"C:\g\d.apk")):
            text = prompt(source, info)
            self.assertLess(len(text), 30000)
            self.assertIn("Never run the game's executables", text)
            self.assertIn("Do not open, import or run the project", text)
            self.assertIn("Never use \"Working\"", text)


if __name__ == "__main__":
    unittest.main()
