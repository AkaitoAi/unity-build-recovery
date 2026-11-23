"""Tests for scanner detection, download extraction safety, cancel, version and the HTTP guards."""
import hashlib
import http.client
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "studio"))
os.environ.setdefault("STUDIO_NONCE_FILE", os.path.join(tempfile.gettempdir(), "studio-test-nonce2.txt"))
os.environ.setdefault("STUDIO_LOG_DIR", os.path.join(tempfile.gettempdir(), "studio-test-logs"))
import paths  # noqa: E402
import recovery_engine  # noqa: E402
import scanner  # noqa: E402
import server  # noqa: E402
import tool_updates  # noqa: E402


def make_zip(path, names):
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"x")


class ScannerDetection(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="studio-test-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_an_il2cpp_apk_is_unity_il2cpp(self):
        apk = os.path.join(self.root, "game.apk")
        make_zip(apk, ["lib/arm64-v8a/libil2cpp.so", "lib/arm64-v8a/libunity.so",
                       "assets/bin/Data/Managed/Metadata/global-metadata.dat", "classes.dex"])
        info = scanner.inspect_game(apk)
        self.assertEqual(info["engine"], "Unity")
        self.assertTrue(info["has_il2cpp"])
        self.assertEqual(len(info["sha256"]), 64)

    def test_a_plain_zip_is_not_called_unity(self):
        pack = os.path.join(self.root, "notes.zip")
        make_zip(pack, ["readme.txt", "photo.png"])
        self.assertNotEqual(scanner.inspect_game(pack)["engine"], "Unity")

    def test_a_missing_path_reports_an_error(self):
        self.assertIn("error", scanner.inspect_game(os.path.join(self.root, "nope.apk")))


class DownloadExtraction(unittest.TestCase):
    """A hostile archive from a release must not write outside its folder."""

    def test_a_zip_slip_member_is_refused(self):
        root = tempfile.mkdtemp(prefix="studio-test-")
        try:
            blob = io.BytesIO()
            with zipfile.ZipFile(blob, "w") as archive:
                archive.writestr("../evil.txt", b"x")
                archive.writestr("jadx/bin/jadx.bat", b"x")
            data = blob.getvalue()

            class Response:
                headers = {"Content-Length": str(len(data))}

                def __init__(self):
                    self.buffer = io.BytesIO(data)

                def read(self, n):
                    return self.buffer.read(n)

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            class Opener:
                def open(self, request, timeout=0):
                    return Response()

            saved = (tool_updates.TOOLS_ROOT, tool_updates._newest_release, tool_updates._github_opener)
            tool_updates.TOOLS_ROOT = root
            tool_updates._newest_release = lambda *a, **k: {
                "tag": "v9", "download": "https://github.com/skylot/jadx/releases/download/v9/jadx-9.zip",
                "download_name": "jadx-9.zip", "size_mb": 0, "sha256": hashlib.sha256(data).hexdigest(), "url": "u"}
            tool_updates._github_opener = lambda: Opener()
            try:
                with self.assertRaises(RuntimeError) as caught:
                    tool_updates.install("JADX", None, {"version": "v9", "sha256": hashlib.sha256(data).hexdigest()})
                self.assertIn("unsafe path", str(caught.exception))
            finally:
                tool_updates.TOOLS_ROOT, tool_updates._newest_release, tool_updates._github_opener = saved
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(root), "evil.txt")))
            self.assertFalse(os.path.exists(os.path.join(root, "evil.txt")))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_wrong_checksum_installs_nothing(self):
        root = tempfile.mkdtemp(prefix="studio-test-")
        try:
            saved = (tool_updates.TOOLS_ROOT, tool_updates._newest_release)
            tool_updates.TOOLS_ROOT = root
            tool_updates._newest_release = lambda *a, **k: {
                "tag": "v9", "download": "https://github.com/x/y/a.zip", "download_name": "a.zip",
                "size_mb": 0, "sha256": "ab" * 32, "url": "u"}
            try:
                with self.assertRaises(RuntimeError):
                    tool_updates.install("JADX", None, {"version": "v9", "sha256": "cd" * 32})   # not the approved digest
            finally:
                tool_updates.TOOLS_ROOT, tool_updates._newest_release = saved
            self.assertEqual(os.listdir(root), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class Cancel(unittest.TestCase):
    def tearDown(self):
        recovery_engine.CANCEL.clear()

    def test_a_cancelled_run_starts_no_tool(self):
        recovery_engine.request_cancel()
        with self.assertRaises(recovery_engine.RecoveryCancelled):
            recovery_engine._run([sys.executable, "-c", "print(1)"])

    def test_cancel_ends_a_running_tool(self):
        result = {}

        def work():
            try:
                recovery_engine._run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=120)
                result["done"] = True
            except recovery_engine.RecoveryCancelled:
                result["cancelled"] = True

        thread = threading.Thread(target=work)
        thread.start()
        for _ in range(50):
            with recovery_engine._LIVE_LOCK:
                if recovery_engine._LIVE:
                    break
            threading.Event().wait(0.1)
        recovery_engine.request_cancel()
        thread.join(15)
        self.assertTrue(result.get("cancelled"))

    def test_a_normal_run_returns_its_output(self):
        done = recovery_engine._run([sys.executable, "-c", "print('hi')"], text=True)
        self.assertEqual(done.stdout.strip(), "hi")


class VersionAndFiles(unittest.TestCase):
    def test_the_version_file_is_read(self):
        self.assertRegex(paths.studio_version(), r"^\d+\.\d+\.\d+$")

    def test_the_log_folder_is_outside_the_source_tree(self):
        self.assertFalse(paths.LOG_DIR.startswith(paths.APP_DIR))


class HttpGuards(unittest.TestCase):
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

    def request(self, method, path, headers=None, body=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.putrequest(method, path, skip_host=True)
        connection.putheader("Host", host or f"127.0.0.1:{self.port}")
        for key, value in (headers or {}).items():
            connection.putheader(key, value)
        connection.endheaders(body)
        response = connection.getresponse()
        data = response.read().decode("utf-8", "replace")
        connection.close()
        return response.status, data

    def api_headers(self):
        return {"X-Studio-Token": server.SESSION_TOKEN, "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Type": "application/json"}

    def test_a_foreign_host_is_refused(self):
        self.assertEqual(self.request("GET", "/api/version", self.api_headers(), host="evil.example")[0], 403)

    def test_a_foreign_origin_is_refused(self):
        headers = self.api_headers()
        headers["Origin"] = "http://evil.example"
        self.assertEqual(self.request("POST", "/api/inspect", headers, b"{}")[0], 403)

    def test_an_oversized_body_is_refused(self):
        headers = self.api_headers()
        headers["Content-Length"] = str(server.MAX_BODY_BYTES + 1)
        self.assertEqual(self.request("POST", "/api/inspect", headers)[0], 413)

    def test_version_needs_the_token_and_reports_the_version(self):
        self.assertEqual(self.request("GET", "/api/version")[0], 403)
        status, body = self.request("GET", "/api/version", self.api_headers())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], paths.studio_version())

    def test_cancel_when_nothing_runs_is_harmless(self):
        status, body = self.request("POST", "/api/pipeline/cancel", self.api_headers(), b"{}")
        self.assertEqual((status, json.loads(body)["cancelled"]), (200, False))

    def test_a_server_error_is_logged_with_its_traceback(self):
        server.setup_logging()
        status, _body = self.request("POST", "/api/open-folder", self.api_headers(),
                                     json.dumps({"work_folder": os.path.join(tempfile.gettempdir(), "no-such-ws-xyz"),
                                                 "sub": ""}).encode())
        self.assertEqual(status, 400)                        # a refused request, not a crash


if __name__ == "__main__":
    unittest.main()
