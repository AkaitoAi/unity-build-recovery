"""The audit's honesty rules: when it must not say 'clean', and what must not count as a defect."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "studio"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402
from test_audit import make_unity_project, write, meta, GOOD  # noqa: E402


def clean_workspace(engine="Unity"):
    work = tempfile.mkdtemp()
    make_unity_project(os.path.join(work, "05_project"), fixed=True)
    write(work, ".run.json", json.dumps({"engine": engine, "sha256": "x"}))
    return work


class HonestyRules(unittest.TestCase):
    def setUp(self):
        self.work = clean_workspace()

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def level(self, target="05_project", **kwargs):
        return audit.run_audit(self.work, target, **kwargs)["summary"]["level"]

    def test_a_clean_project_is_clean(self):
        self.assertEqual(self.level(), "L1 audit clean")

    def test_time_limit_is_never_clean(self):
        report = audit.run_audit(self.work, "05_project", seconds=0)
        self.assertTrue(report["partial"])
        self.assertEqual(report["summary"]["level"], "L1 audit incomplete")
        self.assertFalse(report["summary"]["scan_complete"])

    def test_unreadable_oversize_file_is_never_clean(self):
        original = audit.MAX_CODE_BYTES
        audit.MAX_CODE_BYTES = 10
        try:
            report = audit.run_audit(self.work, "05_project")
        finally:
            audit.MAX_CODE_BYTES = original
        self.assertEqual(report["summary"]["level"], "L1 audit incomplete")
        self.assertGreater(report["summary"]["skipped"]["oversize_file"], 0)

    def test_binary_scene_is_never_clean(self):
        path = os.path.join(self.work, "05_project", "Assets", "Scenes", "Binary.unity")
        with open(path, "wb") as handle:
            handle.write(b"\x00\x01binary serialized scene")
        report = audit.run_audit(self.work, "05_project")
        self.assertEqual(report["summary"]["level"], "L1 audit incomplete")
        self.assertEqual(report["summary"]["skipped"]["binary_yaml_file"], 1)

    def test_findings_beat_incomplete(self):
        # A defect the audit does see (an empty build scene) outranks "incomplete" even when the scan
        # stopped early; the stopped scan itself is still flagged.
        open(os.path.join(self.work, "05_project", "Assets", "Scenes", "Main.unity"), "w").close()
        report = audit.run_audit(self.work, "05_project", seconds=0)
        self.assertEqual(report["summary"]["level"], "L1 audit partial")
        self.assertFalse(report["summary"]["scan_complete"])

    def test_project_with_no_scripts_has_nothing_to_audit(self):
        shutil.rmtree(os.path.join(self.work, "05_project", "Assets", "Scripts"))
        shutil.rmtree(os.path.join(self.work, "05_project", "Assets", "_Project"))
        self.assertEqual(self.level(), "L0 extracted (nothing to audit)")

    def test_unresolved_script_reference_is_a_caveat_not_clean(self):
        scene = os.path.join(self.work, "05_project", "Assets", "Scenes", "Main.unity")
        with open(scene, "a", encoding="utf-8") as handle:
            handle.write("  m_Script: {fileID: 11500000, guid: " + "7" * 32 + ", type: 3}\n")
        report = audit.run_audit(self.work, "05_project")
        self.assertEqual(report["summary"]["level"], "L1 audit clean with caveats")
        self.assertEqual(report["summary"]["total"], 0)
        self.assertEqual(report["summary"]["caveats"]["unresolved_script_refs"], 1)

    def test_a_meta_file_without_its_asset_does_not_resolve_a_guid(self):
        write(self.work, "05_project/Assets/Ghost.cs.meta", meta("f" * 32))      # the .cs itself is not there
        scene = os.path.join(self.work, "05_project", "Assets", "Scenes", "Main.unity")
        with open(scene, "a", encoding="utf-8") as handle:
            handle.write("  m_Script: {fileID: 11500000, guid: " + "f" * 32 + ", type: 3}\n")
        report = audit.run_audit(self.work, "05_project")
        self.assertEqual(report["results"]["unresolved_script_guids"]["count"], 1)

    def test_an_empty_scene_in_the_build_settings_is_a_problem(self):
        open(os.path.join(self.work, "05_project", "Assets", "Scenes", "Main.unity"), "w").close()
        report = audit.run_audit(self.work, "05_project")
        self.assertEqual(report["summary"]["findings"]["build_scene_problem"], 1)
        self.assertEqual(report["results"]["build_settings"]["empty_or_unreadable_scenes"], ["Assets/Scenes/Main.unity"])

    def test_an_ordinary_error_comment_is_not_a_decompiler_marker(self):
        write(self.work, "05_project/Assets/Scripts/Handler.cs", "public class Handler {\n // Error handling is up to the caller\n // error codes: 1, 2\n}\n")
        self.assertEqual(audit.run_audit(self.work, "05_project")["results"]["syntax"]["decompiler_error_markers"], 0)

    def test_real_decompiler_markers_are_found(self):
        write(self.work, "05_project/Assets/Scripts/A.cs", "/* Error: Unable to decompile this method */\npublic class A { }\n")
        write(self.work, "05_project/Assets/Scripts/B.cs", "// Could not decompile Foo\npublic class B { }\n")
        write(self.work, "05_project/Assets/Scripts/C.cs", "// ERROR: unsupported IL\npublic class C { }\n")
        self.assertEqual(audit.run_audit(self.work, "05_project")["results"]["syntax"]["decompiler_error_markers"], 3)

    def test_generic_types_with_different_arity_are_not_duplicates(self):
        write(self.work, "05_project/Assets/Scripts/E1.cs", "public class Event<T> { }\n")
        write(self.work, "05_project/Assets/Scripts/E2.cs", "public class Event<T1, T2> { }\n")
        write(self.work, "05_project/Assets/Scripts/E3.cs", "public class Event { }\n")
        self.assertEqual(audit.run_audit(self.work, "05_project")["results"]["duplicate_types"]["count"], 0)
        write(self.work, "05_project/Assets/Scripts/E4.cs", "public class Event<U> { }\n")
        self.assertEqual(audit.run_audit(self.work, "05_project")["results"]["duplicate_types"]["count"], 1)

    def test_interface_and_enum_methods_are_not_credited_to_the_class_before(self):
        write(self.work, "03_scripts/dump.cs",
              "// Image 0: Assembly-CSharp.dll - 0\npublic class Lonely // TypeDefIndex: 1\n{\n}\n"
              "public interface IThing // TypeDefIndex: 2\n{\n\t// RVA: 0x10 Offset: 0x10\n\tvoid Do();\n}\n"
              "public enum Kind // TypeDefIndex: 3\n{\n\t// RVA: 0x20 Offset: 0x20\n}\n"
              "public class Real // TypeDefIndex: 4\n{\n\t// RVA: -1 Offset: -1\n\tpublic abstract void A();\n\t// RVA: 0x30 Offset: 0x30\n\tpublic void B() { }\n}\n")
        gap = audit.run_audit(self.work, "05_project")["results"]["logic_gap"]
        self.assertEqual(gap["game_classes_with_methods"], 1)            # only Real; the abstract (-1) method is not counted
        self.assertEqual(gap["methods_without_bodies"], 1)

    def test_a_nested_source_type_closes_a_logic_gap(self):
        write(self.work, "03_scripts/dump.cs",
              "// Image 0: Assembly-CSharp.dll - 0\npublic class Inner // TypeDefIndex: 1\n{\n\t// RVA: 0x10 Offset: 0x10\n\tpublic void A() { }\n}\n")
        write(self.work, "05_project/Assets/Scripts/Outer.cs", "public class Outer {\n  public class Inner { }\n}\n")
        self.assertEqual(audit.run_audit(self.work, "05_project")["results"]["logic_gap"]["classes_without_source"], 0)


class FilesAreTrustedOnlyWithTheirSchema(unittest.TestCase):
    def setUp(self):
        self.work = clean_workspace()
        os.makedirs(os.path.join(self.work, "reports"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_the_agents_audit_files_are_never_read_or_overwritten(self):
        agent = os.path.join(self.work, "reports", "AGENT_AUDIT.json")
        with open(agent, "w") as handle:
            handle.write('{"agent": "numbers"}')
        audit.run_audit(self.work, "05_project")
        self.assertEqual(open(agent).read(), '{"agent": "numbers"}')
        self.assertEqual(audit.load_latest(self.work)["target"], "05_project")

    def test_a_file_without_the_schema_is_ignored(self):
        for name in ("STUDIO_AUDIT.json", "AUDIT.json"):
            with open(os.path.join(self.work, "reports", name), "w") as handle:
                handle.write('{"summary": "forged"}')
        self.assertIsNone(audit.load_latest(self.work))

    def test_a_damaged_baseline_does_not_crash_the_next_audit(self):
        audit.run_audit(self.work, "05_project")
        with open(os.path.join(self.work, "reports", "STUDIO_AUDIT_05_project.json"), "w") as handle:
            handle.write('{"anything": 1}')
        shutil.copytree(os.path.join(self.work, "05_project"), os.path.join(self.work, "06_playable"))
        report = audit.run_audit(self.work, "06_playable")
        self.assertIsNone(report["comparison"])

    def test_a_changed_project_makes_the_saved_audit_stale(self):
        audit.run_audit(self.work, "05_project")
        self.assertFalse(audit.load_latest(self.work)["stale"])
        write(self.work, "05_project/Assets/Scripts/New.cs", "public class New { }\n")
        self.assertTrue(audit.load_latest(self.work)["stale"])
        audit.run_audit(self.work, "05_project")
        self.assertFalse(audit.load_latest(self.work)["stale"])


class GodotRules(unittest.TestCase):
    def test_a_uid_main_scene_is_resolved_through_the_scene_headers(self):
        work = tempfile.mkdtemp()
        try:
            project = os.path.join(work, "05_project")
            write(project, "project.godot", 'config_version=5\n[application]\nrun/main_scene="uid://c8a1b2"\n')
            write(project, "levels/start.tscn", '[gd_scene format=3 uid="uid://c8a1b2"]\n[node name="Root" type="Node"]\n')
            write(work, ".run.json", json.dumps({"engine": "Godot"}))
            report = audit.run_audit(work, "05_project")
            self.assertTrue(report["results"]["build_settings"]["ok"])
            self.assertEqual(report["results"]["build_settings"]["main_scene_resolved"], "res://levels/start.tscn")
            write(project, "project.godot", 'config_version=5\n[application]\nrun/main_scene="uid://nothing"\n')
            self.assertFalse(audit.run_audit(work, "05_project")["results"]["build_settings"]["ok"])
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
