"""
Native file / folder picker for Aether Recon Studio.

Run as a process of its own by server.py. A Tk dialog has to own the thread it runs on, and
the studio answers requests on worker threads, so opening one inline either fails outright or
leaves the dialog unresponsive. Running it here keeps the studio itself answering while the
picker is on screen.

Usage:  python pick_path.py [file|folder]
Prints the chosen path on stdout, or nothing at all if the user cancels.
"""

import sys
import tkinter as tk
from tkinter import filedialog


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "file"

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "folder":
            path = filedialog.askdirectory(parent=root, title="Select Workspace Folder")
        else:
            path = filedialog.askopenfilename(
                parent=root,
                title="Select Game Package",
                filetypes=[
                    ("Game Packages", "*.xapk *.apk *.apks *.zip *.pck *.exe"),
                    ("All Files", "*.*"),
                ],
            )
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    if path:
        sys.stdout.write(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
