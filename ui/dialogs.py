"""
Dialog / popup windows — ReviewWindow, ProjectDialog, SettingsDialog.
"""

import tkinter as tk
from tkinter import messagebox
import difflib
import os

from theme import (
    BG_PRIMARY, BG_SECONDARY, BG_SURFACE, FG_PRIMARY, FG_DIM, FG_BLUE,
    FG_GREEN, FG_RED, FG_YELLOW, FONT_MONO, FONT_MONO_SM, FONT_MONO_MD,
    FONT_MONO_LG, FONT_MONO_BOLD, FONT_MONO_MD_BOLD, FONT_MONO_LG_BOLD,
    BTN_PRIMARY, BTN_SUCCESS, BTN_DANGER, BTN_MUTED,
)
from pm_engine import load_projects, save_env, load_env


class ReviewWindow(tk.Toplevel):
    """Popup window for reviewing a pending file change approval."""

    def __init__(self, parent, approval: dict, on_resolve=None):
        super().__init__(parent)
        self.parent_app = parent
        self._approval = approval
        self._on_resolve = on_resolve
        self.title(f"Review — {approval.get('description', 'file change')}")
        self.configure(bg=BG_PRIMARY)
        self.geometry("900x600")
        self.grab_set()

        # ── Top bar: project + description ──
        top = tk.Frame(self, bg=BG_PRIMARY)
        top.pack(fill=tk.X, padx=12, pady=(10, 4))
        project_name = approval.get("project_name", approval.get("project_id", "?"))
        tk.Label(top, text=f"[{project_name}]", bg=BG_PRIMARY, fg=FG_BLUE,
                 font=FONT_MONO_LG_BOLD).pack(side=tk.LEFT)
        tk.Label(top, text=f"  {approval.get('description', '')}", bg=BG_PRIMARY, fg=FG_PRIMARY,
                 font=FONT_MONO_MD).pack(side=tk.LEFT)

        # ── Main area ──
        main = tk.Frame(self, bg=BG_PRIMARY)
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        # Right side: file list
        right = tk.Frame(main, bg=BG_SECONDARY, width=220)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        right.pack_propagate(False)
        tk.Label(right, text="Files", bg=BG_SECONDARY, fg=FG_YELLOW,
                 font=FONT_MONO_BOLD).pack(anchor=tk.W, padx=8, pady=(6, 4))
        file_path = approval.get("file_path", "")
        file_name = os.path.basename(file_path)
        self._file_label = tk.Label(right, text=f"  {file_name}", bg=BG_SURFACE, fg=FG_GREEN,
                                    font=FONT_MONO, anchor=tk.W, padx=6, pady=4)
        self._file_label.pack(fill=tk.X, padx=4, pady=2)
        tk.Label(right, text=file_path, bg=BG_SECONDARY, fg=FG_DIM,
                 font=("Consolas", 8), wraplength=200, anchor=tk.W).pack(anchor=tk.W, padx=8, pady=(2, 0))

        # Left side: diff viewer
        diff_frame = tk.Frame(main, bg=BG_SECONDARY)
        diff_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.diff_box = tk.Text(
            diff_frame, bg=BG_SECONDARY, fg=FG_PRIMARY, relief=tk.FLAT,
            font=FONT_MONO, state=tk.DISABLED, wrap=tk.NONE,
            padx=8, pady=6,
        )
        diff_sy = tk.Scrollbar(diff_frame, command=self.diff_box.yview, bg=BG_SURFACE)
        diff_sx = tk.Scrollbar(diff_frame, orient=tk.HORIZONTAL,
                               command=self.diff_box.xview, bg=BG_SURFACE)
        self.diff_box.configure(yscrollcommand=diff_sy.set, xscrollcommand=diff_sx.set)
        diff_sy.pack(side=tk.RIGHT, fill=tk.Y)
        diff_sx.pack(side=tk.BOTTOM, fill=tk.X)
        self.diff_box.pack(fill=tk.BOTH, expand=True)

        self.diff_box.tag_config("added",   foreground=FG_GREEN)
        self.diff_box.tag_config("removed", foreground=FG_RED)
        self.diff_box.tag_config("header",  foreground=FG_BLUE, font=FONT_MONO_BOLD)
        self.diff_box.tag_config("meta",    foreground=FG_YELLOW)

        self._render_diff(approval)

        # ── Bottom buttons ──
        btns = tk.Frame(self, bg=BG_PRIMARY)
        btns.pack(fill=tk.X, padx=12, pady=(4, 12))
        tk.Button(btns, text="  Approve  ", command=self._approve,
                  **BTN_SUCCESS).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btns, text="  Reject  ", command=self._reject,
                  **BTN_DANGER).pack(side=tk.LEFT)

    def _render_diff(self, approval: dict):
        self.diff_box.configure(state=tk.NORMAL)
        self.diff_box.delete("1.0", tk.END)

        file_path = approval.get("file_path", "")
        original_content = approval.get("original_content", "")
        if not original_content and file_path:
            abs_path = file_path
            if not os.path.isabs(abs_path):
                project = next((p for p in load_projects() if p["id"] == approval.get("project_id")), None)
                if project:
                    abs_path = os.path.join(project["path"], abs_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    original_content = f.read()
            except Exception:
                original_content = ""

        original = original_content.splitlines(keepends=True)
        new = (approval.get("new_content") or "").splitlines(keepends=True)

        self.diff_box.insert(tk.END, f"File: {file_path}\n", "header")
        self.diff_box.insert(tk.END, f"Change: {approval.get('description', '')}\n\n", "meta")

        for line in difflib.unified_diff(original, new, fromfile="current",
                                         tofile="proposed", lineterm=""):
            if line.startswith(("+++", "---")):
                self.diff_box.insert(tk.END, line + "\n", "header")
            elif line.startswith("@@"):
                self.diff_box.insert(tk.END, line + "\n", "meta")
            elif line.startswith("+"):
                self.diff_box.insert(tk.END, line + "\n", "added")
            elif line.startswith("-"):
                self.diff_box.insert(tk.END, line + "\n", "removed")
            else:
                self.diff_box.insert(tk.END, line + "\n")

        self.diff_box.configure(state=tk.DISABLED)

    def _approve(self):
        self.parent_app._approve_write(self._approval)
        if self._on_resolve:
            self._on_resolve()
        self.destroy()

    def _reject(self):
        self.parent_app._reject_write(self._approval)
        if self._on_resolve:
            self._on_resolve()
        self.destroy()


class ProjectDialog(tk.Toplevel):
    def __init__(self, parent, title, on_save, existing=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG_PRIMARY)
        self.resizable(False, False)
        self.grab_set()
        self._on_save = on_save

        fields = [
            ("id",        "ID (e.g. BE1)",          existing.get("id", "")        if existing else ""),
            ("name",      "Name",                    existing.get("name", "")      if existing else ""),
            ("type",      "Type (BE/FE/FULLSTACK)",  existing.get("type", "BE")    if existing else "BE"),
            ("path",      "Project path",            existing.get("path", "")      if existing else ""),
            ("claude_md", "CLAUDE.md path",          existing.get("claude_md", "") if existing else ""),
            ("db_path",   "DB path (agent.db)",      existing.get("db_path", "")   if existing else ""),
        ]

        self._vars = {}
        for key, label, default in fields:
            row = tk.Frame(self, bg=BG_PRIMARY)
            row.pack(fill=tk.X, padx=16, pady=4)
            tk.Label(row, text=label, bg=BG_PRIMARY, fg=FG_PRIMARY,
                     font=FONT_MONO, width=24, anchor=tk.W).pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            self._vars[key] = var
            tk.Entry(row, textvariable=var, bg=BG_SURFACE, fg=FG_PRIMARY,
                     insertbackground=FG_PRIMARY, relief=tk.FLAT,
                     font=FONT_MONO, width=38).pack(side=tk.LEFT, padx=4)

        btn_row = tk.Frame(self, bg=BG_PRIMARY)
        btn_row.pack(pady=12)
        tk.Button(btn_row, text="Save", command=self._save,
                  **BTN_PRIMARY).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  **BTN_MUTED).pack(side=tk.LEFT)

    def _save(self):
        data = {k: v.get().strip() for k, v in self._vars.items()}
        if not data["id"] or not data["name"] or not data["path"]:
            messagebox.showerror("Missing fields", "ID, Name and Path are required.")
            return
        self._on_save(data)
        self.destroy()


class SettingsDialog(tk.Toplevel):
    _FIELDS = [
        ("ANTHROPIC_API_KEY",  "Anthropic API Key",  True),
        ("TELEGRAM_BOT_TOKEN", "Telegram Bot Token", True),
        ("TELEGRAM_CHAT_ID",   "Telegram Chat ID",   False),
    ]

    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self.title("Settings")
        self.configure(bg=BG_PRIMARY)
        self.resizable(False, False)
        self.grab_set()

        env = load_env()

        tk.Label(self, text="API Keys", bg=BG_PRIMARY, fg=FG_BLUE,
                 font=FONT_MONO_MD_BOLD).pack(anchor=tk.W, padx=16, pady=(12, 4))

        self._vars = {}
        for key, label, masked in self._FIELDS:
            row = tk.Frame(self, bg=BG_PRIMARY)
            row.pack(fill=tk.X, padx=16, pady=3)
            tk.Label(row, text=label, bg=BG_PRIMARY, fg=FG_PRIMARY,
                     font=FONT_MONO, width=22, anchor=tk.W).pack(side=tk.LEFT)
            var = tk.StringVar(value=env.get(key, ""))
            self._vars[key] = var
            entry = tk.Entry(row, textvariable=var, show="*" if masked else "",
                             bg=BG_SURFACE, fg=FG_PRIMARY, insertbackground=FG_PRIMARY,
                             relief=tk.FLAT, font=FONT_MONO, width=40)
            entry.pack(side=tk.LEFT, padx=4)
            if masked:
                def _make_toggle(e=entry):
                    def _toggle():
                        e.config(show="" if e.cget("show") == "*" else "*")
                    return _toggle
                tk.Button(row, text="\U0001f441", command=_make_toggle(),
                          bg=BG_SURFACE, fg=FG_PRIMARY, relief=tk.FLAT,
                          font=FONT_MONO_SM, padx=4).pack(side=tk.LEFT)

        tk.Frame(self, bg=BG_SURFACE, height=1).pack(fill=tk.X, padx=16, pady=8)

        tk.Label(self, text="Telegram Bot", bg=BG_PRIMARY, fg=FG_BLUE,
                 font=FONT_MONO_MD_BOLD).pack(anchor=tk.W, padx=16, pady=(0, 6))

        bot_row = tk.Frame(self, bg=BG_PRIMARY)
        bot_row.pack(fill=tk.X, padx=16, pady=(0, 8))

        self._status_var = tk.StringVar()
        tk.Label(bot_row, textvariable=self._status_var, bg=BG_PRIMARY,
                 fg=FG_PRIMARY, font=FONT_MONO, width=20, anchor=tk.W).pack(side=tk.LEFT)

        self._start_btn = tk.Button(bot_row, text="Start Bot", command=self._start_bot,
                                     bg=FG_GREEN, fg=BG_PRIMARY, relief=tk.FLAT,
                                     font=FONT_MONO_BOLD, padx=10)
        self._start_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._stop_btn = tk.Button(bot_row, text="Stop Bot", command=self._stop_bot,
                                    bg=FG_RED, fg=BG_PRIMARY, relief=tk.FLAT,
                                    font=FONT_MONO_BOLD, padx=10)
        self._stop_btn.pack(side=tk.LEFT)

        btn_row = tk.Frame(self, bg=BG_PRIMARY)
        btn_row.pack(pady=12)
        tk.Button(btn_row, text="Save", command=self._save,
                  **BTN_PRIMARY).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  **BTN_MUTED).pack(side=tk.LEFT)

        self._refresh()

    def _is_bot_running(self) -> bool:
        return (self._app._bot_process is not None and
                self._app._bot_process.poll() is None)

    def _refresh(self):
        running = self._is_bot_running()
        self._status_var.set("\u25cf running" if running else "\u25cb stopped")
        self._start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self._stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        if self.winfo_exists():
            self.after(1000, self._refresh)

    def _start_bot(self):
        self._app._start_bot()

    def _stop_bot(self):
        self._app._stop_bot()

    def _save(self):
        data = {k: v.get().strip() for k, v in self._vars.items()}
        save_env(data)
        if data.get("ANTHROPIC_API_KEY"):
            self._app._api_key_var.set(data["ANTHROPIC_API_KEY"])
        messagebox.showinfo("Saved", "Settings saved.", parent=self)
        self.destroy()
