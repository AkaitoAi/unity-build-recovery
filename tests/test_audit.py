"""Checks the studio audit against projects with known defects. Run: python -m unittest discover tests"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "studio"))
import audit  # noqa: E402

GOOD, MAT, BAD_SCRIPT, MISSING_ASSET = ("a" * 32, "b" * 32, "c" * 32, "d" * 32)


def write(root, relative, text):
    path = os.path.join(root, relative.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def meta(guid):
    return f"fileFormatVersion: 2\nguid: {guid}\n"


def make_unity_project(root, fixed=False):
    scene = "%YAML 1.1\n--- !u!114 &1\nMonoBehaviour:\n"
    scene += f"  m_Script: {{fileID: 11500000, guid: {GOOD}, type: 3}}\n"
    if not fixed:
        scene += "  m_Script: {fileID: 0}\n  m_Script: {fileID: 0}\n"
        scene += f"  m_Script: {{fileID: 11500000, guid: {BAD_SCRIPT}, type: 3}}\n"
        scene += f"  tex: {{fileID: 2800000, guid: {MISSING_ASSET}, type: 3}}\n"
    scene += f"  mat: {{fileID: 2100000, guid: {MAT}, type: 2}}\n"
    scene += "  builtin: {fileID: 10303, guid: 0000000000000000f000000000000000, type: 0}\n"
    write(root, "Assets/Scenes/Main.unity", scene)
    write(root, "Assets/Scenes/Main.unity.meta", meta("e" * 32))
    write(root, "Assets/Prefabs/P.prefab", "%YAML 1.1\nMonoBehaviour:\n" + ("" if fixed else "  m_Script: {fileID: 0}\n"))
    write(root, "Assets/Materials/Mat.mat.meta", meta(MAT))
    write(root, "Assets/Materials/Mat.mat", "%YAML 1.1\nMaterial:\n")
    write(root, "Assets/Scripts/Player.cs.meta", meta(GOOD))
    write(root, "Assets/Scripts/Player.cs",
          "using TMPro;\nusing Firebase;\nnamespace Game {\n  public class Player {\n    string s = \"{\";\n"
          "    // a comment with a brace {\n    class Util { }\n  }\n}\n")
    write(root, "Assets/Scripts/Dup1.cs", "namespace Game {\n public class Util { }\n}\n")
    if not fixed:
        write(root, "Assets/Scripts/Dup2.cs", "namespace Game\n{\n public class Util { }\n}\n")
        write(root, "Assets/Scripts/Broken.cs", "public class B {\n void F() {\n}\n")
        write(root, "Assets/Scripts/Marked.cs", "/* Error: decompilation failed */\npublic class M { }\n")
    else:
        write(root, "Assets/Scripts/Broken.cs", "public class B {\n void F() {\n}\n}\n")
    write(root, "Assets/_Project/Scripts/Stubs/FirebaseStub.cs", "namespace Firebase { public class FirebaseApp { } }\n")
    write(root, "Assets/Scripts/Partial1.cs", "public partial class Big { }\n")
    write(root, "Assets/Scripts/Partial2.cs", "public partial class Big { }\n")
    write(root, "Assets/Scripts/TwoSpaces.cs", "namespace A { public class X { } }\nnamespace B { public class X { } }\n")
    scenes = ("EditorBuildSettings:\n  m_Scenes:\n  - enabled: 1\n    path: Assets/Scenes/Main.unity\n    guid: x\n")
    if not fixed:
        scenes += "  - enabled: 1\n    path: Assets/Scenes/Gone.unity\n    guid: y\n"
    write(root, "ProjectSettings/EditorBuildSettings.asset", scenes)
    deps = {"com.unity.ugui": "1.0.0"}
    if fixed:
        deps["com.unity.textmeshpro"] = "3.0.6"
    write(root, "Packages/manifest.json", json.dumps({"dependencies": deps}))


class UnityAuditTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        make_unity_project(os.path.join(self.work, "05_project"))
        write(self.work, "03_scripts/dump.cs",
              "// Image 0: Assembly-CSharp.dll - 0\n// Image 1: UnityEngine.CoreModule.dll - 100\n"
              "public class Player : MonoBehaviour // TypeDefIndex: 5\n{\n\t// Methods\n\t// RVA: 0x1 Offset: 0x1\n\tpublic void A() { }\n"
              "\t// RVA: 0x2 Offset: 0x2\n\tpublic void B() { }\n\t// RVA: 0x3 Offset: 0x3\n\tpublic void C() { }\n}\n"
              "public class Enemy : MonoBehaviour // TypeDefIndex: 6\n{\n\t// RVA: 0x4 Offset: 0x4\n\tpublic void A() { }\n"
              "\t// RVA: 0x5 Offset: 0x5\n\tpublic void B() { }\n\t// RVA: 0x6 Offset: 0x6\n\tpublic void C() { }\n"
              "\t// RVA: 0x7 Offset: 0x7\n\tpublic void D() { }\n}\n"
              "public class Big // TypeDefIndex: 7\n{\n\t// RVA: 0x8 Offset: 0x8\n\tpublic void A() { }\n}\n"
              "public class Vec // TypeDefIndex: 150\n{\n\t// RVA: 0x9 Offset: 0x9\n\tpublic void A() { }\n}\n")
        write(self.work, ".run.json", json.dumps({"engine": "Unity", "sha256": "x"}))

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_known_defects_are_counted_exactly(self):
        report = audit.run_audit(self.work, "05_project")
        r = report["results"]
        self.assertEqual(r["missing_scripts"]["count"], 3)
        self.assertEqual(r["missing_scripts"]["files_affected"], 2)
        self.assertEqual(r["unresolved_script_guids"]["count"], 1)
        self.assertEqual(r["unresolved_asset_guids"]["count"], 1)          # the built-in guid is ignored
        self.assertEqual(r["duplicate_types"]["count"], 1)                 # Game.Util; partial, nested, other-namespace are not
        self.assertEqual(r["syntax"]["unbalanced_braces"], 1)              # braces in strings and comments do not count
        self.assertEqual(r["syntax"]["decompiler_error_markers"], 1)
        self.assertFalse(r["build_settings"]["ok"])
        self.assertEqual(len(r["build_settings"]["missing_scene_files"]), 1)
        self.assertEqual([p["package"] for p in r["packages"]["needed_but_missing"]], ["com.unity.textmeshpro"])
        self.assertEqual([s["sdk"] for s in r["online_sdks"]["used"]], ["Firebase"])
        self.assertEqual(r["online_sdks"]["used"][0]["files"], 1)          # the studio's own stub is not counted
        self.assertEqual(r["online_sdks"]["studio_stubs"], ["FirebaseStub.cs"])
        gap = r["logic_gap"]
        self.assertEqual((gap["game_classes_with_methods"], gap["classes_without_source"], gap["methods_without_bodies"]), (3, 1, 4))
        self.assertEqual(report["summary"]["total"], 8)
        self.assertEqual(report["summary"]["level"], "L1 audit partial")

    def test_files_are_written_and_before_after_compares(self):
        audit.run_audit(self.work, "05_project")
        fixed = os.path.join(self.work, "06_playable")
        make_unity_project(fixed, fixed=True)
        report = audit.run_audit(self.work, "06_playable")
        self.assertEqual(report["summary"]["total"], 0)
        self.assertEqual(report["summary"]["level"], "L1 audit clean")
        comparison = report["comparison"]
        self.assertEqual((comparison["before_total"], comparison["after_total"]), (8, 0))
        for name in ("AUDIT.json", "AUDIT.md", "AUDIT_05_project.json", "AUDIT_06_playable.json"):
            self.assertTrue(os.path.exists(os.path.join(self.work, "reports", name)), name)
        self.assertEqual(audit.load_latest(self.work)["target"], "06_playable")

    def test_missing_project_is_reported_not_crashed(self):
        report = audit.run_audit(self.work, "06_playable")
        self.assertEqual(report["summary"]["level"], "not audited")

    def test_audit_never_modifies_the_project(self):
        def snapshot():
            return sorted((p, os.path.getmtime(p)) for p in audit._walk_files(os.path.join(self.work, "05_project")))
        before = snapshot()
        audit.run_audit(self.work, "05_project")
        self.assertEqual(before, snapshot())


class GodotAuditTests(unittest.TestCase):
    def test_godot_findings(self):
        work = tempfile.mkdtemp()
        try:
            project = os.path.join(work, "05_project")
            write(project, "project.godot", 'config_version=5\n[application]\nrun/main_scene="res://main.tscn"\n'
                  '[autoload]\nGlobals="*res://globals.gd"\nGone="*res://gone.gd"\n')
            write(project, "main.tscn", '[ext_resource path="res://globals.gd" id=1]\n[ext_resource path="res://missing.png" id=2]\n')
            write(project, "globals.gd", "extends Node\n")
            write(project, "old.gdc", "x")
            write(project, ".autoconverted/main.gdc", "x")   # a decompiled copy GDRE keeps: not a defect
            write(project, "main.gd", "extends Node\n")
            write(project, "gdre_export.log", "INFO fine\nWARNING: odd\nERROR: bad\n")
            write(work, ".run.json", json.dumps({"engine": "Godot"}))
            report = audit.run_audit(work, "05_project")
            r = report["results"]
            self.assertTrue(r["build_settings"]["ok"])
            self.assertEqual(r["missing_resources"]["count"], 2)       # missing.png and gone.gd (named in project.godot)
            self.assertEqual(r["autoloads"]["missing"], ["res://gone.gd"])
            self.assertEqual(r["syntax"]["undecompiled_gdc"], 1)
            self.assertEqual((r["recovery_log"]["errors"], r["recovery_log"]["warnings"]), (1, 1))
            self.assertEqual(report["summary"]["level"], "L1 audit partial")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
