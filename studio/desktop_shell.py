"""
Aether Recon Studio - Desktop Window Shell

Runs the studio as a real desktop application instead of a browser tab:
starts the local studio server, then shows its interface in a plain window with
no address bar, no tabs and its own taskbar entry.

The window is drawn by the Edge/Chrome engine that is already part of Windows,
so nothing has to be installed. Closing the window shuts the server down again.

Launched by Run_Recovery_Studio.bat through pythonw.exe, so no console appears.
"""

import os
import glob
import socket
import subprocess
import sys
import time
import webbrowser

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 5899
URL = "http://127.0.0.1:{0}/".format(PORT)
WINDOW_SIZE = "1280,900"
CREATE_NO_WINDOW = 0x08000000

# A profile folder of our own. Without this the command is handed to an Edge window the
# user already has open: our window would not be a separate app and would not block here.
PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", APP_DIR), "AetherReconStudio", "WindowProfile"
)

# Chrome is listed first: in app mode it opens straight into the window, while Edge tends to
# show its own welcome/sign-in screen the first time a new profile folder is used.
_LOCAL = os.environ.get("LOCALAPPDATA", "")
BROWSERS = [
    os.path.join(_LOCAL, "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(_LOCAL, "Microsoft", "Edge", "Application", "msedge.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

from paths import TOOLS_ROOT


def studio_environment():
    """The tool paths that env.ps1 sets, handed to the server process.

    The launcher used to dot-source env.ps1 in a separate PowerShell process, so none of it
    ever reached the studio. Setting it here is what lets the Java-based tools (jadx, the
    AssetRipper headless script, the Android SDK) be found from inside the running studio.
    """
    env = os.environ.copy()
    android = os.path.join(TOOLS_ROOT, "android-sdk")
    env["RE_TOOLS"] = TOOLS_ROOT
    env["GHIDRA_INSTALL_DIR"] = os.path.join(TOOLS_ROOT, "ghidra")
    env["ANDROID_HOME"] = android
    env["ANDROID_SDK_ROOT"] = android
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    # Game and folder names may hold any language; the server's log must not choke on them.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    if not os.path.isdir(env.get("JAVA_HOME", "")):
        jdks = sorted(glob.glob(r"C:\Program Files\Eclipse Adoptium\jdk-*"), reverse=True)
        if jdks:
            env["JAVA_HOME"] = jdks[0]
    ndks = sorted(glob.glob(os.path.join(android, "ndk", "*")), reverse=True)
    ndk = ndks[0] if ndks else ""
    if ndk and os.path.isdir(ndk):
        env["ANDROID_NDK_HOME"] = ndk

    folders = [
        os.path.join(env.get("JAVA_HOME", ""), "bin") if env.get("JAVA_HOME") else "",
        os.path.join(TOOLS_ROOT, "7-Zip"),
        os.path.join(TOOLS_ROOT, "ffmpeg", "bin"),
        os.path.join(TOOLS_ROOT, "llvm", "bin"),
        os.path.join(TOOLS_ROOT, "jadx", "bin"),
        os.path.join(TOOLS_ROOT, "ghidra", "support"),
        os.path.join(android, "platform-tools"),
        os.path.join(android, "build-tools", "36.0.0"),
        os.path.join(android, "cmdline-tools", "latest", "bin"),
        os.path.join(TOOLS_ROOT, "pyenv", "Scripts"),
        os.path.join(TOOLS_ROOT, "Cpp2IL"),
        os.path.join(TOOLS_ROOT, "Il2CppDumper"),
    ]
    path = env.get("PATH", "")
    for folder in folders:
        if folder and os.path.isdir(folder) and folder.lower() not in path.lower():
            path = folder + os.pathsep + path
    env["PATH"] = path
    return env


def launch_url():
    """The address to open: the page plus the server's one-use launch nonce, read from this
    user's profile. Without it the server shows only a note saying to use the launcher."""
    try:
        from paths import NONCE_PATH
        with open(NONCE_PATH, "r", encoding="ascii") as handle:
            nonce = handle.read().strip()
        if nonce:
            return URL + "?n=" + nonce
    except Exception:
        pass
    return URL


def server_is_up():
    """True when something is already serving the studio on the loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def start_server():
    """Starts server.py in the background. Returns the process, or None if one was already running."""
    if server_is_up():
        return None
    python = sys.executable
    # Use the windowed interpreter so the server never flashes up a console of its own.
    windowed = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.exists(windowed):
        python = windowed
    # server.py prints as it starts. Under pythonw.exe there is no console, so sys.stdout is
    # None and the first print() would kill it. Giving it a real log file keeps it alive and
    # leaves something to read if it ever fails to come up.
    from paths import LOG_DIR
    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, "server-console.log"), "a", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        # --server-only: this launcher opens and owns the window, so the server must not
        # open a second one of its own.
        [python, os.path.join(APP_DIR, "server.py"), "--server-only"],
        cwd=APP_DIR,
        env=studio_environment(),
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    for _ in range(100):  # up to 20 seconds
        if server_is_up():
            break
        if process.poll() is not None:  # it died on startup
            break
        time.sleep(0.2)
    return process


def find_window_engine():
    for path in BROWSERS:
        if os.path.exists(path):
            return path
    return None


def show_window(engine):
    """Opens the studio in its own window and returns when the user closes it."""
    # One profile folder per engine: Chrome and Edge must never share a user-data-dir.
    profile = os.path.join(PROFILE_DIR, os.path.splitext(os.path.basename(engine))[0])
    os.makedirs(profile, exist_ok=True)
    # The presence of this file tells the browser engine the profile has been used before,
    # which stops it opening a welcome or sign-in page in front of the studio.
    sentinel = os.path.join(profile, "First Run")
    if not os.path.exists(sentinel):
        open(sentinel, "w").close()

    started = time.time()
    subprocess.call([
        engine,
        "--app=" + launch_url(),
        "--user-data-dir=" + profile,
        "--window-size=" + WINDOW_SIZE,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--no-service-autorun",
        "--disable-background-networking",
        "--disable-features=msEdgeWelcomePage,msImplicitSignin,msEdgeIdentityFeatures,"
        "FirstRunExperience,PrivacySandboxSettings4,DefaultBrowserPromptRefresh",
    ])
    return time.time() - started


def main():
    server = start_server()
    own_server = server is not None
    try:
        if not server_is_up():
            return 1
        engine = find_window_engine()
        if not engine:
            # No Chrome or Edge: fall back to whatever browser the system uses.
            webbrowser.open(launch_url())
            own_server = False  # nothing here tracks that window, so leave the server up
            return 0

        open_for = show_window(engine)
        # A studio window was already open: the browser handed our window to that running
        # instance and returned at once, so this process is not the one the user closes.
        # Leaving the server up keeps the window that is on screen working.
        if open_for < 5 and server_is_up():
            own_server = False
    finally:
        # Only stop the server if this launcher started it and owned the window.
        if own_server and server is not None and server.poll() is None:
            # taskkill /T also ends the PowerShell and AssetRipper processes the server started.
            subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                           capture_output=True, stdin=subprocess.DEVNULL)
            try:
                server.wait(timeout=5)
            except Exception:
                server.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
