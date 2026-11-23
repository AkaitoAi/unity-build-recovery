"""Tests for the second round of review fixes: launch nonce, run lock, agent tracking, workspace rules,
tool approval, Craft CLI trust and download redirects. Run: python -m unittest discover tests"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "studio"))
os.environ.setdefault("STUDIO_NONCE_FILE", os.path.join(tempfile.gettempdir(), "studio-test-nonce.txt"))
import agents  # noqa: E402
import craft  # noqa: E402
import scanner  # noqa: E402
import server  # noqa: E402
import tool_needs  # noqa: E402
import tool_updates  # noqa: E402


def make_dir():
    return tempfile.mkdtemp(prefix="studio-test-")


class Cpp2ILPresence(unittest.TestCase):
    def test_an_empty_folder_is_not_installed(self):
        root = make_dir()
        try:
            os.makedirs(os.path.join(root, "Cpp2IL"))
            old = tool_needs.TOOLS_ROOT
            tool_needs.TOOLS_ROOT = root
            try:
                self.assertFalse(tool_needs._present("Cpp2IL"))
                open(os.path.join(root, "Cpp2IL", "notes.txt"), "w").close()
                self.assertFalse(tool_needs._present("Cpp2IL"))
                open(os.path.join(root, "Cpp2IL", "Cpp2IL-2022.1.exe"), "w").close()
                self.assertTrue(tool_needs._present("Cpp2IL"))
            finally:
                tool_needs.TOOLS_ROOT = old
        finally:
            shutil.rmtree(root, ignore_errors=True)


class WorkspaceRules(unittest.TestCase):
    def setUp(self):
        self.root = make_dir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def marker(self, folder, data):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, ".run.json"), "w") as handle:
            handle.write(data if isinstance(data, str) else json.dumps(data))

    def test_an_empty_or_new_folder_is_accepted(self):
        self.assertTrue(server.resolve_work(os.path.join(self.root, "new")))
        os.makedirs(os.path.join(self.root, "empty"))
        self.assertTrue(server.resolve_work(os.path.join(self.root, "empty")))

    def test_a_real_marker_is_accepted_and_a_stray_one_is_not(self):
        good = os.path.join(self.root, "good")
        self.marker(good, {"sha256": "ab" * 32, "engine": "Unity"})
        open(os.path.join(good, "extra.txt"), "w").close()
        self.assertTrue(server.resolve_work(good))
        for name, data in (("empty-sha", {"sha256": "", "engine": "Unity"}), ("garbage", "not json"), ("list", "[1]")):
            folder = os.path.join(self.root, name)
            self.marker(folder, data)
            with self.assertRaises(ValueError, msg=name):
                server.resolve_work(folder)

    def test_a_folder_with_other_files_is_refused(self):
        folder = os.path.join(self.root, "docs")
        os.makedirs(folder)
        open(os.path.join(folder, "thesis.docx"), "w").close()
        with self.assertRaises(ValueError):
            server.resolve_work(folder)

    def test_the_older_layout_without_a_marker_is_accepted(self):
        folder = os.path.join(self.root, "old")
        os.makedirs(os.path.join(folder, "00_original"))
        os.makedirs(os.path.join(folder, "reports"))
        self.assertTrue(server.resolve_work(folder))

    def test_a_folder_inside_a_workspace_is_refused(self):
        outer = os.path.join(self.root, "outer")
        self.marker(outer, {"sha256": "ab" * 32})
        with self.assertRaises(ValueError):
            server.resolve_work(os.path.join(outer, "inner"))


class FolderHash(unittest.TestCase):
    def test_a_build_folder_gets_a_hash_that_follows_its_contents(self):
        root = make_dir()
        try:
            with open(os.path.join(root, "a.dll"), "wb") as handle:
                handle.write(b"12345")
            first = scanner.inspect_game(root)["sha256"]
            self.assertEqual(len(first), 64)
            self.assertEqual(first, scanner.inspect_game(root)["sha256"])
            with open(os.path.join(root, "b.dat"), "wb") as handle:
                handle.write(b"x")
            self.assertNotEqual(first, scanner.inspect_game(root)["sha256"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class RunLock(unittest.TestCase):
    def tearDown(self):
        with server.PIPELINE_LOCK:
            server.PIPELINE_STATE["running"] = False
            server.PIPELINE_STATE["owner"] = ""

    def test_only_the_owner_releases(self):
        token = server.acquire_run()
        self.assertTrue(token)
        self.assertIsNone(server.acquire_run())
        server.release_run("someone-else")
        self.assertIsNone(server.acquire_run())          # still held
        server.release_run(token)
        self.assertTrue(server.acquire_run())

    def test_an_agent_in_the_workspace_blocks_mutators_there_only(self):
        work, other = make_dir(), make_dir()
        try:
            self.assertTrue(agents.reserve(work, "Test agent"))
            self.assertIsNone(server.acquire_run(work))
            token = server.acquire_run(other)
            self.assertTrue(token)
            server.release_run(token)
            self.assertTrue(server.busy_reason(any_agent=True))      # installs wait for every agent
            self.assertTrue(agents.release(work))
            self.assertFalse(server.busy_reason(work))
        finally:
            agents.release(work)
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(other, ignore_errors=True)


class AgentTracking(unittest.TestCase):
    def test_a_second_reservation_fails_until_released(self):
        work = make_dir()
        try:
            self.assertTrue(agents.reserve(work, "A"))
            self.assertFalse(agents.reserve(work, "B"))
            self.assertEqual(agents.active(work)[0]["agent"], "A")
            self.assertTrue(agents.release(work))
            self.assertEqual(agents.active(work), [])
        finally:
            agents.release(work)
            shutil.rmtree(work, ignore_errors=True)

    def test_a_finished_process_releases_the_workspace_and_its_mcp_file(self):
        work = make_dir()
        try:
            config = os.path.join(work, ".rea-mcp.json")
            open(config, "w").close()
            proc = subprocess.Popen([sys.executable, "-c", "pass"])
            agents._register(work, "Quick", proc, config)
            proc.wait()
            self.assertEqual(agents.active(work), [])
            self.assertFalse(os.path.exists(config))
        finally:
            agents.release(work)
            shutil.rmtree(work, ignore_errors=True)

    def test_a_cmd_shim_refuses_a_workspace_path_cmd_would_misread(self):
        work = make_dir()
        shim_dir = make_dir()
        try:
            shim = os.path.join(shim_dir, "claude.cmd")
            with open(shim, "w") as handle:
                handle.write("@echo off\r\n")
            bad = os.path.join(work, "a&b")
            old = agents._which
            agents._which = lambda name: shim
            try:
                agents.reserve(bad, "x")
                with self.assertRaises(ValueError):
                    agents.launch("claude", "src", bad, {}, copy_prompt=False)
                self.assertEqual(agents.active(bad), [])      # the failed launch freed the reservation
            finally:
                agents._which = old
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(shim_dir, ignore_errors=True)


class CraftCliTrust(unittest.TestCase):
    def test_a_program_inside_a_workspace_is_not_trusted(self):
        folder = make_dir()
        try:
            exe = os.path.join(folder, "craft-anything-cli.exe")
            open(exe, "w").close()
            self.assertIsNone(craft.find_cli(exe))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_a_program_in_the_tool_root_is_trusted(self):
        root = make_dir()
        old = craft.TOOLS_ROOT
        try:
            exe = os.path.join(root, "craft-anything-cli.exe")
            open(exe, "w").close()
            craft.TOOLS_ROOT = root
            self.assertEqual(craft.find_cli(exe), exe)
        finally:
            craft.TOOLS_ROOT = old
            shutil.rmtree(root, ignore_errors=True)

    def test_a_relative_path_and_other_names_are_refused(self):
        self.assertIsNone(craft.find_cli("sub\\craft-anything-cli.exe"))
        self.assertIsNone(craft.find_cli("notepad.exe"))

    def test_the_source_must_be_a_known_folder(self):
        with self.assertRaises(ValueError):
            craft.start("x", "task", "", "", 1, source="../elsewhere")

    def test_claim_is_single(self):
        self.assertTrue(craft.claim())
        try:
            self.assertFalse(craft.claim())
        finally:
            craft.unclaim()


class DownloadRules(unittest.TestCase):
    def redirect(self, url):
        handler = tool_updates._GithubOnlyRedirects()
        request = urllib.request.Request("https://github.com/x/y/releases/download/v1/a.zip")
        return handler.redirect_request(request, None, 302, "Found", {}, url)

    def test_github_hosts_are_followed(self):
        self.assertIsNotNone(self.redirect("https://objects.githubusercontent.com/abc"))
        self.assertIsNotNone(self.redirect("https://release-assets.githubusercontent.com/abc"))
        self.assertIsNotNone(self.redirect("https://github.com/x/y/z"))

    def test_other_hosts_and_plain_http_are_refused(self):
        for url in ("https://evil.example/a.zip", "http://objects.githubusercontent.com/a",
                    "https://githubusercontent.com.evil.example/a", "https://notgithub.com/a"):
            with self.assertRaises(RuntimeError, msg=url):
                self.redirect(url)

    def test_an_npm_install_is_refused_for_a_version_that_was_not_the_newest(self):
        original = tool_updates._newest_npm
        tool_updates._newest_npm = lambda package: {"npm_version": "2.0.0"}
        try:
            with self.assertRaises(RuntimeError) as caught:
                tool_updates.install("REA", None, {"version": "v1.0.0"})
            self.assertIn("2.0.0", str(caught.exception))
        finally:
            tool_updates._newest_npm = original


class PageNonce(unittest.TestCase):
    """The page is served only to a browser opened with the launcher's nonce."""

    @classmethod
    def setUpClass(cls):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.old_port = server.PORT
        server.PORT = cls.port
        server.rotate_nonce()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.StudioRequestHandler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.PORT = cls.old_port

    def get(self, path, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode("utf-8"), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8"), error.headers

    def test_no_nonce_no_page_no_token(self):
        status, body, _headers = self.get("/")
        self.assertEqual(status, 403)
        self.assertNotIn(server.SESSION_TOKEN, body)

    def test_the_nonce_works_once_and_sets_a_cookie_for_reloads(self):
        nonce = open(server.NONCE_PATH, encoding="ascii").read().strip()
        status, body, headers = self.get("/?n=" + nonce)
        self.assertEqual(status, 200)
        self.assertIn(server.SESSION_TOKEN, body)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertEqual(self.get("/?n=" + nonce)[0], 403)                  # used up
        self.assertEqual(self.get("/", {"Cookie": cookie.split(";")[0]})[0], 200)   # a reload keeps working
        self.assertNotEqual(open(server.NONCE_PATH, encoding="ascii").read().strip(), nonce)

    def test_a_wrong_cookie_is_refused(self):
        self.assertEqual(self.get("/", {"Cookie": "studio_session=guess"})[0], 403)

    def test_post_needs_an_origin_and_the_token(self):
        def post(headers):
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/inspect", data=b"{}", headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        token = {"X-Studio-Token": server.SESSION_TOKEN, "Content-Type": "application/json"}
        self.assertEqual(post(token), 403)                                         # no Origin
        origin = {"Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(post({**origin, "Content-Type": "application/json"}), 403)  # no token
        self.assertEqual(post({**token, **origin}), 400)                           # allowed, then "choose a package"

    def test_open_unity_needs_the_confirmation(self):
        class Fake:
            pass
        with self.assertRaises(ValueError):
            server.StudioRequestHandler.handle_open_unity(Fake(), {"work_folder": make_dir()})


if __name__ == "__main__":
    unittest.main()
