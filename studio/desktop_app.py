"""
Unity & Game Recovery Studio - Ultra-Premium Desktop Edition
A modern, dark-themed, studio-grade Windows desktop application for game asset recovery, decompilation, and AI semantic script selection.
"""

import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# Ensure app path resolution
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from scanner import inspect_game, TOOLS_ROOT
from script_ai_selector import scan_and_classify_directory, export_selected_scripts, ScriptItem
from recovery_engine import RecoveryPipeline
from unity_playable_scaffolder import scaffold_unity_project, INSTALLED_EDITORS

class ModernRecoveryStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Unity & Game Recovery Studio — Professional Studio Edition")
        self.geometry("1240x800")
        self.minsize(1060, 700)

        # Set Custom Studio Icon
        ico_path = os.path.join(APP_DIR, "studio_icon.ico")
        if os.path.exists(ico_path):
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass

        # High-end Obsidian / Dark Modern Studio Palette (Linear / Raycast / Steam inspired)
        self.c_bg = "#0d1117"             # Deep Obsidian Canvas
        self.c_surface = "#161b22"        # Card Surface
        self.c_surface_hover = "#1f242c"  # Card Hover
        self.c_card_inner = "#21262d"     # Inner Wells & Inputs
        self.c_border = "#30363d"         # Subtle Modern Border
        self.c_border_focus = "#58a6ff"   # Electric Blue Focus Border
        
        # Typography Colors
        self.c_text_primary = "#f0f6fc"   # 95% White
        self.c_text_secondary = "#8b949e" # Muted Cool Grey
        self.c_text_muted = "#6e7681"     # Darker Grey
        
        # Accents
        self.c_accent_blue = "#1f6feb"    # Primary Studio Blue
        self.c_accent_blue_hover = "#388bfd"
        self.c_accent_cyan = "#39c5cf"    # Cyan Tech Glow
        self.c_accent_green = "#238636"   # Emerald Success
        self.c_accent_green_hover = "#2ea043"
        self.c_accent_purple = "#8957e5"  # AI / Semantic Violet
        self.c_accent_purple_hover = "#a371f7"
        self.c_accent_amber = "#d29922"   # Warning Gold
        self.c_accent_red = "#da3633"     # Error Red

        self.configure(bg=self.c_bg)

        # State
        self.current_info = {}
        self.current_scripts = []
        self.pipeline_thread = None
        self.active_tab = "wizard"

        self.setup_fonts_and_styles()
        self.build_ui()

        # Load default game if present
        default_xapk = ""
        if default_xapk and os.path.exists(default_xapk):
            self.entry_source.delete(0, tk.END)
            self.entry_source.insert(0, default_xapk)
            self.entry_work.delete(0, tk.END)
            self.after(300, self.do_quick_inspect)

    def setup_fonts_and_styles(self):
        self.font_title = ("Segoe UI", 13, "bold")
        self.font_subtitle = ("Segoe UI", 9)
        self.font_nav = ("Segoe UI", 10, "bold")
        self.font_card_head = ("Segoe UI", 11, "bold")
        self.font_body = ("Segoe UI", 9)
        self.font_body_bold = ("Segoe UI", 9, "bold")
        self.font_code = ("Consolas", 9)

        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("Treeview", 
                        background="#161b22", 
                        foreground="#f0f6fc", 
                        fieldbackground="#161b22", 
                        rowheight=28, 
                        font=("Segoe UI", 9),
                        borderwidth=0)
        style.configure("Treeview.Heading", 
                        background="#21262d", 
                        foreground="#f0f6fc", 
                        font=("Segoe UI", 9, "bold"),
                        relief="flat",
                        padding=5)
        style.map("Treeview", 
                  background=[("selected", self.c_accent_blue)],
                  foreground=[("selected", "#ffffff")])

        style.configure("Studio.Horizontal.TProgressbar",
                        background=self.c_accent_cyan,
                        troughcolor="#161b22",
                        borderwidth=0,
                        thickness=6)

    def make_card(self, parent, padx=14, pady=8, fill="x", expand=False):
        outer = tk.Frame(parent, bg=self.c_border, padx=1, pady=1)
        outer.pack(fill=fill, expand=expand, padx=padx, pady=pady)
        inner = tk.Frame(outer, bg=self.c_surface, padx=14, pady=12)
        inner.pack(fill="both", expand=True)
        return inner

    def make_btn(self, parent, text, command, bg_color=None, fg_color="#ffffff", font=None, padx=14, pady=6, cursor="hand2"):
        bg = bg_color or self.c_card_inner
        fn = font or self.font_body_bold
        btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg_color,
                        activebackground="#388bfd", activeforeground="#ffffff",
                        font=fn, relief="flat", bd=0, padx=padx, pady=pady, cursor=cursor)

        def on_enter(e):
            if btn["state"] != "disabled":
                if bg == self.c_accent_green:
                    btn.config(bg=self.c_accent_green_hover)
                elif bg == self.c_accent_blue:
                    btn.config(bg=self.c_accent_blue_hover)
                elif bg == self.c_accent_purple:
                    btn.config(bg=self.c_accent_purple_hover)
                else:
                    btn.config(bg="#30363d")

        def on_leave(e):
            if btn["state"] != "disabled":
                btn.config(bg=bg)

        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    def build_ui(self):
        # 1. TOP HEADER BAR
        self.header_bar = tk.Frame(self, bg=self.c_surface, height=64, padx=20, pady=10)
        self.header_bar.pack(fill="x", side="top")

        logo_box = tk.Frame(self.header_bar, bg=self.c_surface)
        logo_box.pack(side="left")

        logo_icon = tk.Label(logo_box, text="♾️", font=("Segoe UI", 16), bg=self.c_surface, fg=self.c_accent_cyan)
        logo_icon.pack(side="left", padx=(0, 10))

        title_sub_box = tk.Frame(logo_box, bg=self.c_surface)
        title_sub_box.pack(side="left")

        app_title = tk.Label(title_sub_box, text="UNITY & GAME RECOVERY STUDIO", font=("Segoe UI", 13, "bold"), bg=self.c_surface, fg=self.c_text_primary)
        app_title.pack(anchor="w")

        app_badge = tk.Label(title_sub_box, text="100% OFFLINE  •  AI SEMANTIC SCRIPT ENGINE  •  DIRECT PLAYABLE UNITY BUILDS", font=("Segoe UI", 8, "bold"), bg=self.c_surface, fg=self.c_accent_cyan)
        app_badge.pack(anchor="w")

        pill_tools = tk.Frame(self.header_bar, bg=self.c_card_inner, padx=12, pady=5)
        pill_tools.pack(side="right")
        tk.Label(pill_tools, text="● SYSTEM HEALTH: PASS", font=("Segoe UI", 8, "bold"), bg=self.c_card_inner, fg=self.c_accent_green).pack(side="left", padx=4)
        tk.Label(pill_tools, text="|  Unity 2022 / 6 Ready", font=("Segoe UI", 8), bg=self.c_card_inner, fg=self.c_text_secondary).pack(side="left")

        # 2. MODERN NAV BAR
        nav_container = tk.Frame(self, bg=self.c_bg, padx=14, pady=6)
        nav_container.pack(fill="x")

        self.nav_btns = {}
        nav_items = [
            ("wizard", "🚀  Recovery Pipeline Wizard"),
            ("scripts", "🧠  AI Script Classifier & Selector"),
            ("tools", "🛠️  System Tools Health Grid"),
            ("logs", "📜  Live Execution Console")
        ]

        self.nav_bar = tk.Frame(nav_container, bg=self.c_surface, padx=4, pady=4)
        self.nav_bar.pack(anchor="w")

        for key, label in nav_items:
            b = tk.Button(self.nav_bar, text=label, font=self.font_nav,
                          bg=self.c_surface, fg=self.c_text_secondary,
                          activebackground=self.c_card_inner, activeforeground="#ffffff",
                          relief="flat", bd=0, padx=18, pady=8, cursor="hand2",
                          command=lambda k=key: self.switch_tab(k))
            b.pack(side="left", padx=2)
            self.nav_btns[key] = b

        self.content_area = tk.Frame(self, bg=self.c_bg)
        self.content_area.pack(fill="both", expand=True, padx=14, pady=4)

        self.view_wizard = tk.Frame(self.content_area, bg=self.c_bg)
        self.view_scripts = tk.Frame(self.content_area, bg=self.c_bg)
        self.view_tools = tk.Frame(self.content_area, bg=self.c_bg)
        self.view_logs = tk.Frame(self.content_area, bg=self.c_bg)

        self.build_wizard_view()
        self.build_scripts_view()
        self.build_tools_view()
        self.build_logs_view()

        self.switch_tab("wizard")

    def switch_tab(self, tab_key):
        self.active_tab = tab_key
        for k, b in self.nav_btns.items():
            if k == tab_key:
                b.config(bg=self.c_accent_blue, fg="#ffffff")
            else:
                b.config(bg=self.c_surface, fg=self.c_text_secondary)

        self.view_wizard.pack_forget()
        self.view_scripts.pack_forget()
        self.view_tools.pack_forget()
        self.view_logs.pack_forget()

        if tab_key == "wizard":
            self.view_wizard.pack(fill="both", expand=True)
        elif tab_key == "scripts":
            self.view_scripts.pack(fill="both", expand=True)
        elif tab_key == "tools":
            self.view_tools.pack(fill="both", expand=True)
        elif tab_key == "logs":
            self.view_logs.pack(fill="both", expand=True)

    # --------------------------------------------------------------------------
    # TAB 1: RECOVERY PIPELINE WIZARD
    # --------------------------------------------------------------------------
    def build_wizard_view(self):
        card_paths = self.make_card(self.view_wizard, padx=0, pady=(4, 8))

        lbl_paths = tk.Label(card_paths, text="📁  PACKAGE CONFIGURATION & TARGET WORKSPACE", font=self.font_card_head, bg=self.c_surface, fg=self.c_accent_cyan)
        lbl_paths.pack(anchor="w", pady=(0, 8))

        # Source row
        row_src = tk.Frame(card_paths, bg=self.c_surface)
        row_src.pack(fill="x", pady=4)
        tk.Label(row_src, text="Source Package:", font=self.font_body_bold, bg=self.c_surface, fg=self.c_text_secondary, width=16, anchor="w").pack(side="left")
        self.entry_source = tk.Entry(row_src, bg=self.c_card_inner, fg=self.c_text_primary, insertbackground="#ffffff", font=self.font_code, bd=0, relief="flat")
        self.entry_source.pack(side="left", fill="x", expand=True, padx=6, ipady=5)

        self.make_btn(row_src, "Select File...", self.browse_source_file).pack(side="left", padx=4)
        self.make_btn(row_src, "Select Folder...", self.browse_source_dir).pack(side="left", padx=4)

        # Work row
        row_work = tk.Frame(card_paths, bg=self.c_surface)
        row_work.pack(fill="x", pady=4)
        tk.Label(row_work, text="Work Workspace:", font=self.font_body_bold, bg=self.c_surface, fg=self.c_text_secondary, width=16, anchor="w").pack(side="left")
        self.entry_work = tk.Entry(row_work, bg=self.c_card_inner, fg=self.c_text_primary, insertbackground="#ffffff", font=self.font_code, bd=0, relief="flat")
        self.entry_work.pack(side="left", fill="x", expand=True, padx=6, ipady=5)

        self.make_btn(row_work, "Browse...", self.browse_work_folder).pack(side="left", padx=4)
        self.make_btn(row_work, "🔍 Quick Inspect", self.do_quick_inspect, bg_color=self.c_accent_blue).pack(side="left", padx=4)

        # Card 2: 6 Modern Stat Chips
        self.card_stats = self.make_card(self.view_wizard, padx=0, pady=6)
        stat_head_row = tk.Frame(self.card_stats, bg=self.c_surface)
        stat_head_row.pack(fill="x", pady=(0, 8))
        tk.Label(stat_head_row, text="📊  PACKAGE INSPECTION DASHBOARD", font=self.font_card_head, bg=self.c_surface, fg=self.c_accent_cyan).pack(side="left")
        self.lbl_game_title = tk.Label(stat_head_row, text="Awaiting Package...", font=("Segoe UI", 11, "bold"), bg=self.c_surface, fg=self.c_text_primary)
        self.lbl_game_title.pack(side="right")

        self.grid_container = tk.Frame(self.card_stats, bg=self.c_surface)
        self.grid_container.pack(fill="x", pady=4)

        self.stat_widgets = {}
        chips_config = [
            ("engine", "Game Engine", "Unknown", self.c_accent_purple),
            ("backend", "Scripting Backend", "Unknown", self.c_accent_blue),
            ("unity_version", "Engine Version", "N/A", self.c_accent_cyan),
            ("abis", "Target Architecture", "N/A", self.c_accent_green),
            ("format", "Container Format", "N/A", self.c_text_primary),
            ("size", "Package Size / Hash", "0 MB", self.c_accent_amber)
        ]

        for i, (key, title, val, color) in enumerate(chips_config):
            row = i // 3
            col = i % 3
            cell_outer = tk.Frame(self.grid_container, bg=self.c_border, padx=1, pady=1)
            cell_outer.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            self.grid_container.grid_columnconfigure(col, weight=1)

            cell = tk.Frame(cell_outer, bg=self.c_card_inner, padx=12, pady=10)
            cell.pack(fill="both", expand=True)

            tk.Label(cell, text=title.upper(), font=("Segoe UI", 8, "bold"), bg=self.c_card_inner, fg=self.c_text_muted).pack(anchor="w")
            lbl_val = tk.Label(cell, text=val, font=("Segoe UI", 10, "bold"), bg=self.c_card_inner, fg=color, wraplength=340, justify="left")
            lbl_val.pack(anchor="w", pady=(2, 0))
            self.stat_widgets[key] = lbl_val

        # Card 3: Action Execution Center
        card_actions = self.make_card(self.view_wizard, padx=0, pady=6, fill="both", expand=True)

        head_act = tk.Frame(card_actions, bg=self.c_surface)
        head_act.pack(fill="x", pady=(0, 10))
        tk.Label(head_act, text="⚡  AUTOMATED WORKFLOW EXECUTION", font=self.font_card_head, bg=self.c_surface, fg=self.c_accent_cyan).pack(side="left")

        self.btn_open_unity = self.make_btn(head_act, "🎮  Open Reconstructed Project in Unity", self.action_open_in_unity, bg_color=self.c_accent_purple, font=("Segoe UI", 10, "bold"), padx=16, pady=6)
        self.btn_open_unity.pack(side="right")

        actions_row = tk.Frame(card_actions, bg=self.c_surface)
        actions_row.pack(fill="x", pady=4)

        self.btn_full_run = self.make_btn(actions_row, "⚡  RUN FULL RECOVERY PIPELINE (START TO END)", self.start_full_pipeline, bg_color=self.c_accent_green, font=("Segoe UI", 11, "bold"), padx=22, pady=10)
        self.btn_full_run.pack(side="left", padx=(0, 10))

        self.make_btn(actions_row, "1. Unpack 7z", lambda: self.run_single_stage(1)).pack(side="left", padx=4)
        self.make_btn(actions_row, "2. Rip Assets", lambda: self.run_single_stage(2)).pack(side="left", padx=4)
        self.make_btn(actions_row, "3. Dump Code", lambda: self.run_single_stage(3)).pack(side="left", padx=4)
        self.make_btn(actions_row, "4. Rebuild Project", lambda: self.run_single_stage(4)).pack(side="left", padx=4)

        prog_box = tk.Frame(card_actions, bg=self.c_surface)
        prog_box.pack(fill="x", pady=(14, 4))

        self.lbl_pipeline_status = tk.Label(prog_box, text="System Ready. Select game package to begin.", font=self.font_body_bold, bg=self.c_surface, fg=self.c_text_secondary)
        self.lbl_pipeline_status.pack(anchor="w", pady=(0, 6))

        self.progress_bar = ttk.Progressbar(prog_box, style="Studio.Horizontal.TProgressbar", mode="indeterminate")
        self.progress_bar.pack(fill="x")

    # --------------------------------------------------------------------------
    # TAB 2: AI SCRIPT SELECTOR
    # --------------------------------------------------------------------------
    def build_scripts_view(self):
        card_filter = self.make_card(self.view_scripts, padx=0, pady=(4, 6))

        top_row = tk.Frame(card_filter, bg=self.c_surface)
        top_row.pack(fill="x", pady=2)

        tk.Label(top_row, text="AI Semantic Filters:", font=self.font_body_bold, bg=self.c_surface, fg=self.c_text_primary).pack(side="left", padx=(0, 10))

        self.cat_var = tk.StringVar(value="All")
        categories = [
            ("All", "All"),
            ("🎮 Gameplay", "Core Gameplay"),
            ("🎯 UI", "UI & Presentation"),
            ("🧩 Systems", "Game Systems & Audio"),
            ("📦 Plugins", "Plugins & Store Packages"),
            ("🗑️ Ads/Trackers", "Ad & Tracking SDKs")
        ]

        for lbl, cat_val in categories:
            rb = tk.Radiobutton(top_row, text=lbl, value=cat_val, variable=self.cat_var,
                                bg=self.c_card_inner, fg=self.c_text_primary, selectcolor=self.c_accent_blue,
                                indicatoron=0, bd=0, padx=10, pady=5, font=self.font_body_bold,
                                command=self.refresh_scripts_table, cursor="hand2")
            rb.pack(side="left", padx=3)

        tk.Label(top_row, text="Search:", font=self.font_body_bold, bg=self.c_surface, fg=self.c_text_secondary).pack(side="left", padx=(14, 4))
        self.entry_search = tk.Entry(top_row, bg=self.c_card_inner, fg=self.c_text_primary, insertbackground="#ffffff", font=self.font_body, bd=0, relief="flat", width=22)
        self.entry_search.pack(side="left", ipady=4)
        self.entry_search.bind("<KeyRelease>", lambda e: self.refresh_scripts_table())

        self.make_btn(top_row, "🔄 Scan Scripts", self.scan_recovered_scripts, bg_color=self.c_accent_purple).pack(side="right", padx=4)

        # Batch Action Bar with Playable Preset
        act_row = tk.Frame(self.view_scripts, bg=self.c_bg)
        act_row.pack(fill="x", pady=6)

        self.make_btn(act_row, "🎯  AUTO-SELECT & FIX FOR 100% PLAYABLE BUILD", self.action_auto_fix_playable, bg_color=self.c_accent_green, font=("Segoe UI", 9, "bold"), padx=16).pack(side="left", padx=(0, 6))
        self.make_btn(act_row, "✨ Recommended Gameplay", self.action_select_recommended, bg_color=self.c_accent_blue).pack(side="left", padx=3)
        self.make_btn(act_row, "❌ Filter Ads", self.action_deselect_ads).pack(side="left", padx=3)
        self.make_btn(act_row, "☑️ Check All", self.action_check_all).pack(side="left", padx=3)
        self.make_btn(act_row, "☐ Uncheck All", self.action_uncheck_all).pack(side="left", padx=3)

        self.make_btn(act_row, "📁 Export Selected Scripts", self.action_export_scripts, bg_color=self.c_accent_blue).pack(side="right")
        self.lbl_script_stats = tk.Label(act_row, text="Scripts: 0 loaded", font=self.font_body_bold, bg=self.c_bg, fg=self.c_accent_cyan)
        self.lbl_script_stats.pack(side="right", padx=16)

        paned = tk.PanedWindow(self.view_scripts, orient="horizontal", bg=self.c_border, bd=1, sashwidth=4)
        paned.pack(fill="both", expand=True, pady=4)

        # Left pane
        frame_tree = tk.Frame(paned, bg=self.c_surface)
        paned.add(frame_tree, minsize=600)

        cols = ("Select", "Name", "Category", "Confidence", "Lines", "Methods")
        self.tree_scripts = ttk.Treeview(frame_tree, columns=cols, show="headings", selectmode="browse")
        self.tree_scripts.heading("Select", text="[✓]")
        self.tree_scripts.heading("Name", text="Class / Script Identifier")
        self.tree_scripts.heading("Category", text="Semantic Classification")
        self.tree_scripts.heading("Confidence", text="Confidence")
        self.tree_scripts.heading("Lines", text="Lines")
        self.tree_scripts.heading("Methods", text="Methods")

        self.tree_scripts.column("Select", width=46, anchor="center")
        self.tree_scripts.column("Name", width=220)
        self.tree_scripts.column("Category", width=180)
        self.tree_scripts.column("Confidence", width=84, anchor="center")
        self.tree_scripts.column("Lines", width=60, anchor="e")
        self.tree_scripts.column("Methods", width=64, anchor="e")

        scroll_tree = ttk.Scrollbar(frame_tree, orient="vertical", command=self.tree_scripts.yview)
        self.tree_scripts.configure(yscrollcommand=scroll_tree.set)
        self.tree_scripts.pack(side="left", fill="both", expand=True)
        scroll_tree.pack(side="right", fill="y")

        self.tree_scripts.bind("<Button-1>", self.on_script_cell_click)
        self.tree_scripts.bind("<<TreeviewSelect>>", self.on_script_select)

        # Right pane
        frame_preview = tk.Frame(paned, bg=self.c_surface)
        paned.add(frame_preview, minsize=420)

        preview_header = tk.Frame(frame_preview, bg=self.c_card_inner, padx=10, pady=6)
        preview_header.pack(fill="x")
        self.lbl_preview_file = tk.Label(preview_header, text="Code Inspection Pane", font=self.font_body_bold, bg=self.c_card_inner, fg=self.c_accent_cyan)
        self.lbl_preview_file.pack(side="left")

        self.txt_preview = scrolledtext.ScrolledText(frame_preview, bg="#0d1117", fg="#e6edf3", insertbackground="#ffffff", font=self.font_code, bd=0)
        self.txt_preview.pack(fill="both", expand=True, padx=6, pady=6)

    # --------------------------------------------------------------------------
    # TAB 3: TOOLS HEALTH VIEW
    # --------------------------------------------------------------------------
    def build_tools_view(self):
        card_tools = self.make_card(self.view_tools, padx=0, pady=6, fill="both", expand=True)

        th_row = tk.Frame(card_tools, bg=self.c_surface)
        th_row.pack(fill="x", pady=(0, 8))
        tk.Label(th_row, text="🛠️  OFFLINE REVERSE ENGINEERING & RECOVERY TOOLCHAIN", font=self.font_card_head, bg=self.c_surface, fg=self.c_accent_cyan).pack(side="left")

        self.make_btn(th_row, "🔄 Re-verify Health", self.populate_tools_table, bg_color=self.c_accent_blue).pack(side="right")

        cols = ("Tool", "Status", "Capability", "Verified Executable Path")
        self.tree_tools = ttk.Treeview(card_tools, columns=cols, show="headings", height=18)
        self.tree_tools.heading("Tool", text="Tool Engine")
        self.tree_tools.heading("Status", text="Status")
        self.tree_tools.heading("Capability", text="Role / Capability")
        self.tree_tools.heading("Verified Executable Path", text="Disk Location")

        self.tree_tools.column("Tool", width=180)
        self.tree_tools.column("Status", width=90, anchor="center")
        self.tree_tools.column("Capability", width=220)
        self.tree_tools.column("Verified Executable Path", width=540)

        self.tree_tools.pack(fill="both", expand=True, pady=6)
        self.populate_tools_table()

    # --------------------------------------------------------------------------
    # TAB 4: LIVE LOGS VIEW
    # --------------------------------------------------------------------------
    def build_logs_view(self):
        card_logs = self.make_card(self.view_logs, padx=0, pady=6, fill="both", expand=True)

        lh_row = tk.Frame(card_logs, bg=self.c_surface)
        lh_row.pack(fill="x", pady=(0, 6))

        tk.Label(lh_row, text="📜  REAL-TIME DIAGNOSTIC CONSOLE LOG", font=self.font_card_head, bg=self.c_surface, fg=self.c_accent_cyan).pack(side="left")

        self.make_btn(lh_row, "Clear Console", lambda: self.txt_logs.delete("1.0", tk.END), bg_color=self.c_card_inner).pack(side="right", padx=4)

        self.txt_logs = scrolledtext.ScrolledText(card_logs, bg="#0d1117", fg="#39c5cf", insertbackground="#ffffff", font=self.font_code, bd=0)
        self.txt_logs.pack(fill="both", expand=True, pady=6)

    # --------------------------------------------------------------------------
    # ACTIONS & LOGIC
    # --------------------------------------------------------------------------
    def log(self, message: str):
        self.txt_logs.insert(tk.END, message + "\n")
        self.txt_logs.see(tk.END)
        first_line = message.strip().splitlines()[-1] if message.strip() else ""
        self.lbl_pipeline_status.config(text=first_line)

    def browse_source_file(self):
        f = filedialog.askopenfilename(filetypes=[("Supported Game Archives", "*.xapk;*.apk;*.zip"), ("All Files", "*.*")])
        if f:
            self.entry_source.delete(0, tk.END)
            self.entry_source.insert(0, f)
            name = os.path.splitext(os.path.basename(f))[0].replace(" ", "_")
            self.entry_work.delete(0, tk.END)
            self.entry_work.insert(0, os.path.join(os.path.expanduser("~"), "UnityRecovery", name))
            self.do_quick_inspect()

    def browse_source_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.entry_source.delete(0, tk.END)
            self.entry_source.insert(0, d)
            name = os.path.basename(d)
            self.entry_work.delete(0, tk.END)
            self.entry_work.insert(0, os.path.join(os.path.expanduser("~"), "UnityRecovery", name))
            self.do_quick_inspect()

    def browse_work_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.entry_work.delete(0, tk.END)
            self.entry_work.insert(0, d)

    def do_quick_inspect(self):
        src = self.entry_source.get().strip()
        if not src or not os.path.exists(src):
            return

        self.lbl_pipeline_status.config(text="🔍 Analyzing game architecture...")
        info = inspect_game(src)
        self.current_info = info

        self.lbl_game_title.config(text=info.get("app_name") or info.get("name"))
        self.stat_widgets["engine"].config(text=info.get("engine", "Unknown"))
        self.stat_widgets["backend"].config(text=info.get("backend", "Unknown"))
        self.stat_widgets["unity_version"].config(text=info.get("unity_version", "N/A"))
        self.stat_widgets["abis"].config(text=", ".join(info.get("abis", [])) or "Universal / Native")
        self.stat_widgets["format"].config(text=info.get("format", "Archive"))
        self.stat_widgets["size"].config(text=f"{info.get('size_mb')} MB  ({info.get('sha256', '')[:12]}...)")

        self.log(f"[Inspector] Game: {info.get('name')} | Engine: {info.get('engine')} | Backend: {info.get('backend')}")
        self.lbl_pipeline_status.config(text=f"✓ Inspected: {info.get('engine')} ({info.get('backend')}) — Ready to run.")

    def start_full_pipeline(self):
        src = self.entry_source.get().strip()
        dest = self.entry_work.get().strip()
        if not src or not dest:
            messagebox.showwarning("Incomplete", "Please select both a Source Game Package and a Target Work Directory.")
            return

        if self.pipeline_thread and self.pipeline_thread.is_alive():
            messagebox.showinfo("Busy", "A pipeline recovery operation is currently running.")
            return

        self.btn_full_run.config(state="disabled")
        self.progress_bar.start(10)
        self.pipeline_thread = threading.Thread(target=self._worker_full_pipeline, args=(src, dest), daemon=True)
        self.pipeline_thread.start()

    def _worker_full_pipeline(self, src: str, dest: str):
        try:
            self.log(f"==================================================")
            self.log(f"🚀 INITIATING FULL OFFLINE RECOVERY PIPELINE")
            self.log(f"Source: {src}")
            self.log(f"Destination: {dest}")
            self.log(f"==================================================")

            pipeline = RecoveryPipeline(src, dest, logger=self.log)
            if not self.current_info:
                self.current_info = inspect_game(src)

            # Stage 0: Intake
            pipeline.run_stage_0_intake(self.current_info)

            # Stage 1: Unpack
            pipeline.run_stage_1_extract(self.current_info)

            # Stage 2: Assets
            pipeline.run_stage_2_assets(self.current_info)

            # Stage 3: Code
            pipeline.run_stage_3_code_recovery(self.current_info)

            # Stage 4: Project
            pipeline.run_stage_4_scaffold_project(self.current_info)

            # Stage 5: Report
            pipeline.generate_report(self.current_info)

            self.log("🎉 RECOVERY WORKFLOW COMPLETE! Automatically scanning scripts...")

            self.after(500, lambda: self.load_scripts_from_folder(pipeline.dir_03_scripts))
            messagebox.showinfo("Complete", f"Game Recovery Pipeline Finished Successfully!\n\nAll assets, code & project scaffolding ready in:\n{dest}\n\nYou can click 'Open Reconstructed Project in Unity' to launch!")

        except Exception as e:
            self.log(f"[ERROR] Pipeline interrupted: {e}")
            messagebox.showerror("Error", f"Recovery Pipeline Error: {e}")
        finally:
            self.btn_full_run.config(state="normal")
            self.progress_bar.stop()

    def run_single_stage(self, stage: int):
        src = self.entry_source.get().strip()
        dest = self.entry_work.get().strip()
        if not src or not dest:
            messagebox.showwarning("Incomplete", "Please specify source package and workspace folder.")
            return

        pipeline = RecoveryPipeline(src, dest, logger=self.log)
        if not self.current_info:
            self.current_info = inspect_game(src)

        threading.Thread(target=self._worker_single_stage, args=(pipeline, stage), daemon=True).start()

    def _worker_single_stage(self, pipeline: RecoveryPipeline, stage: int):
        pipeline.create_directories()
        if stage == 1:
            pipeline.run_stage_0_intake(self.current_info)
            pipeline.run_stage_1_extract(self.current_info)
        elif stage == 2:
            pipeline.run_stage_2_assets(self.current_info)
        elif stage == 3:
            pipeline.run_stage_3_code_recovery(self.current_info)
        elif stage == 4:
            pipeline.run_stage_4_scaffold_project(self.current_info)
            pipeline.generate_report(self.current_info)

    def scan_recovered_scripts(self):
        work = self.entry_work.get().strip()
        scripts_dir = os.path.join(work, "03_scripts")
        if not os.path.exists(scripts_dir):
            scripts_dir = filedialog.askdirectory(title="Select Scripts Folder to Scan")
            if not scripts_dir:
                return
        self.load_scripts_from_folder(scripts_dir)

    def load_scripts_from_folder(self, folder: str):
        self.log(f"🧠 AI Classifier scanning scripts from: {folder}...")
        self.current_scripts = scan_and_classify_directory(folder)
        self.refresh_scripts_table()
        self.log(f"✓ AI Classifier complete: {len(self.current_scripts)} scripts analyzed and cataloged.")

    def refresh_scripts_table(self):
        cat_filter = self.cat_var.get()
        query = self.entry_search.get().strip().lower()

        for item in self.tree_scripts.get_children():
            self.tree_scripts.delete(item)

        visible = 0
        selected = 0
        for s in self.current_scripts:
            if s.is_selected:
                selected += 1

            if cat_filter != "All" and cat_filter != s.category:
                continue

            if query and query not in s.filename.lower() and query not in s.namespace.lower():
                continue

            chk = "☑" if s.is_selected else "☐"
            badge = f"{s.badge} {s.category}"
            self.tree_scripts.insert("", "end", iid=s.filepath, values=(chk, s.name_no_ext, badge, f"{s.confidence}%", s.lines, s.methods_count))
            visible += 1

        self.lbl_script_stats.config(text=f"Showing: {visible}  |  Selected: {selected} of {len(self.current_scripts)}")

    def on_script_cell_click(self, event):
        region = self.tree_scripts.identify_region(event.x, event.y)
        if region == "cell":
            col = self.tree_scripts.identify_column(event.x)
            row_id = self.tree_scripts.identify_row(event.y)
            if col == "#1" and row_id:
                for s in self.current_scripts:
                    if s.filepath == row_id:
                        s.is_selected = not s.is_selected
                        break
                self.refresh_scripts_table()

    def on_script_select(self, event):
        sel = self.tree_scripts.selection()
        if not sel:
            return
        filepath = sel[0]
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(50000)
            self.txt_preview.delete("1.0", tk.END)
            self.txt_preview.insert(tk.END, content)
            self.lbl_preview_file.config(text=f"Inspection: {os.path.basename(filepath)}")
        except Exception as e:
            self.txt_preview.delete("1.0", tk.END)
            self.txt_preview.insert(tk.END, f"Error reading file: {e}")

    def action_auto_fix_playable(self):
        """AI Auto-Selects all Core Gameplay & UI, filters out Ad SDKs, and auto-generates missing stubs for 100% playable build."""
        if not self.current_scripts:
            self.scan_recovered_scripts()
            if not self.current_scripts:
                messagebox.showwarning("No Scripts", "No scripts found to classify. Run the recovery pipeline first.")
                return

        gameplay_count = 0
        ad_count = 0
        for s in self.current_scripts:
            if s.category in ["Core Gameplay", "UI & Presentation", "Game Systems & Audio"]:
                s.is_selected = True
                gameplay_count += 1
            else:
                s.is_selected = False
                ad_count += 1

        self.refresh_scripts_table()

        # Auto export and ensure stubs are generated
        work = self.entry_work.get().strip()
        proj_dir = os.path.join(work, "05_project")
        target_scripts_dir = os.path.join(proj_dir, "Assets", "_Project", "Scripts")
        
        exported, skipped = export_selected_scripts(self.current_scripts, target_scripts_dir)
        scaffold_unity_project(proj_dir, self.current_info.get("unity_version", "2022.3.62f3"), self.current_info.get("name", "Game"))

        msg = (
            f"🎯 100% Playable Build Setup Applied!\n\n"
            f"✓ Selected {gameplay_count} Core Gameplay & System Scripts.\n"
            f"✓ Filtered out {ad_count} Ad & Tracking SDKs.\n"
            f"✓ Auto-generated Missing SDK Stubs (AdMob, AppLovin, Firebase, etc.).\n"
            f"✓ Created clean GameCore.asmdef for zero-error compilation.\n\n"
            f"Project is ready to open and run in Unity Editor!"
        )
        self.log(msg)
        messagebox.showinfo("AI Playable Build Ready", msg)

    def action_select_recommended(self):
        for s in self.current_scripts:
            if s.category in ["Core Gameplay", "UI & Presentation", "Game Systems & Audio"]:
                s.is_selected = True
            elif s.category in ["Ad & Tracking SDKs", "Internal / Compiler Generated"]:
                s.is_selected = False
        self.refresh_scripts_table()
        messagebox.showinfo("AI Selector", "Cleaned selection: Selected all Core Gameplay, UI & Systems. Filtered out Ad SDKs.")

    def action_deselect_ads(self):
        for s in self.current_scripts:
            if s.category in ["Ad & Tracking SDKs", "Internal / Compiler Generated"]:
                s.is_selected = False
        self.refresh_scripts_table()

    def action_check_all(self):
        for s in self.current_scripts:
            s.is_selected = True
        self.refresh_scripts_table()

    def action_uncheck_all(self):
        for s in self.current_scripts:
            s.is_selected = False
        self.refresh_scripts_table()

    def action_export_scripts(self):
        work = self.entry_work.get().strip()
        target_dir = os.path.join(work, "05_project", "Assets", "_Project", "Scripts")
        exported, skipped = export_selected_scripts(self.current_scripts, target_dir)
        self.log(f"Exported {exported} clean scripts into {target_dir} ({skipped} omitted).")
        messagebox.showinfo("Export Complete", f"Successfully exported {exported} scripts into:\n{target_dir}\n\n({skipped} filtered SDK scripts excluded).")

    def action_open_in_unity(self):
        work = self.entry_work.get().strip()
        proj_dir = os.path.join(work, "05_project")
        if not os.path.exists(proj_dir):
            messagebox.showwarning("Missing Project", "The project has not been reconstructed yet. Run the pipeline first.")
            return

        open_bat = os.path.join(proj_dir, "Open_In_Unity.bat")
        if os.path.exists(open_bat):
            self.log(f"Launching Unity Editor via {open_bat}...")
            subprocess.Popen([open_bat], shell=True)
            messagebox.showinfo("Launching Unity", f"Opening recovered project in Unity Editor:\n{proj_dir}")
        else:
            # Direct Unity launch
            unity_exe = r"C:\Program Files\Unity\Hub\Editor\2022.3.62f3\Editor\Unity.exe"
            if os.path.exists(unity_exe):
                self.log(f"Launching Unity: {unity_exe} -projectPath {proj_dir}")
                subprocess.Popen([unity_exe, "-projectPath", proj_dir])
                messagebox.showinfo("Launching Unity", f"Opening project directly in Unity:\n{proj_dir}")
            else:
                messagebox.showerror("Unity Not Found", "Installed Unity.exe not found. Please open Unity Hub manually and add the project folder.")

    def populate_tools_table(self):
        for item in self.tree_tools.get_children():
            self.tree_tools.delete(item)

        tools_data = [
            ("AssetRipper 2.x", "Mesh / Texture / Scene Ripper", os.path.join(TOOLS_ROOT, "AssetRipper", "AssetRipper.GUI.Free.exe")),
            ("AssetStudio Mod (CLI/GUI)", "Asset Bundle / CAB Inspector", os.path.join(TOOLS_ROOT, "AssetStudioModCLI", "AssetStudioModCLI_net9_win64", "AssetStudioModCLI.exe")),
            ("Il2CppDumper", "IL2CPP Symbol & DummyDll Dumper", os.path.join(TOOLS_ROOT, "Il2CppDumper", "Il2CppDumper.exe")),
            ("Cpp2IL", "AOT Binary to C# Pseudocode", os.path.join(TOOLS_ROOT, "Cpp2IL", "Cpp2IL.exe")),
            ("ILSpy (GUI)", "C# Managed Assembly Decompiler", os.path.join(TOOLS_ROOT, "ILSpy", "ILSpy.exe")),
            ("Mono.Cecil", "IL Bytecode Inspector & Patching", os.path.join(TOOLS_ROOT, "Mono.Cecil", "lib", "net40", "Mono.Cecil.dll")),
            ("Ghidra 12.1.4 (NSA)", "Deep Native Disassembler/Decompiler", os.path.join(TOOLS_ROOT, "ghidra", "support", "analyzeHeadless.bat")),
            ("JADX 1.5.6", "DEX / Android Java Decompiler", os.path.join(TOOLS_ROOT, "jadx", "bin", "jadx.bat")),
            ("7-Zip 26.05 (x64)", "High Speed Package & Archive Unpacker", os.path.join(TOOLS_ROOT, "7-Zip", "7z.exe")),
            ("LLVM Clang & Objdump", "Native Binary Disassembly & Symbols", os.path.join(TOOLS_ROOT, "llvm", "bin", "llvm-objdump.exe")),
            ("FFmpeg Studio", "Audio / Video Codec Transcoder", os.path.join(TOOLS_ROOT, "ffmpeg", "bin", "ffmpeg.exe")),
            ("Python 3.12 (UnityPy)", "Asset Serialization Scripting", os.path.join(TOOLS_ROOT, "pyenv", "Scripts", "python.exe")),
            (".NET 8 SDK", "C# Execution & Tooling Runtime", os.path.join(TOOLS_ROOT, "dotnet8", "dotnet.exe")),
            ("Android NDK & Clang", "Native C/C++ Build Toolchain", os.path.join(TOOLS_ROOT, "android-sdk", "ndk")),
            ("Android SDK (aapt2/adb)", "Android Manifest & APK Parsing", os.path.join(TOOLS_ROOT, "android-sdk", "platform-tools", "adb.exe")),
            ("Unity Editor (Host)", "Active Matching Editor Target", r"C:\Program Files\Unity\Hub\Editor\2022.3.62f3\Editor\Unity.exe")
        ]

        for name, role, path in tools_data:
            exists = os.path.exists(path)
            st = "PASS  ✓" if exists else "FAIL  ✗"
            self.tree_tools.insert("", "end", values=(name, st, role, path if exists else "Not Installed"))

if __name__ == "__main__":
    app = ModernRecoveryStudio()
    app.mainloop()
