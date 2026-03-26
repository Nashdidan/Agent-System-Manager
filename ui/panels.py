"""
Detachable panel system + individual panel builders.
Each panel can be resized via PanedWindow sashes or popped out
into its own floating window and docked back.
"""

import tkinter as tk
import os

from theme import (
    BG_PRIMARY, BG_SECONDARY, BG_SURFACE, BG_HIGHLIGHT, BG_SASH,
    FG_PRIMARY, FG_DIM, FG_BLUE, FG_GREEN, FG_RED, FG_YELLOW,
    FONT_MONO, FONT_MONO_SM, FONT_MONO_MD, FONT_MONO_LG,
    FONT_MONO_BOLD, FONT_MONO_MD_BOLD, FONT_MONO_LG_BOLD, FONT_MONO_ITALIC,
    BTN_PRIMARY, BTN_MUTED, BTN_SMALL, SCROLLBAR,
)


class PanelManager:
    """
    Manages detachable/resizable panels inside a PanedWindow.
    Attach to an App instance — it stores panels, handles pop-out / dock-back.
    """

    def __init__(self, parent: tk.Tk, paned: tk.PanedWindow):
        self._parent = parent
        self._paned = paned
        self._panels: dict[str, dict] = {}
        self._detached: dict[str, tk.Toplevel] = {}

    def add(self, panel_id: str, title: str, build_fn, *, minsize=180, width=None, stretch="never"):
        """Create a panel, build its content, and add to the PanedWindow."""
        outer = tk.Frame(self._paned, bg=BG_PRIMARY)
        if width:
            outer.configure(width=width)

        # Title bar
        title_bar = tk.Frame(outer, bg=BG_SECONDARY)
        title_bar.pack(fill=tk.X)
        tk.Label(title_bar, text=title, bg=BG_SECONDARY, fg=FG_BLUE,
                 font=FONT_MONO_BOLD).pack(side=tk.LEFT, padx=8, pady=4)
        pop_btn = tk.Button(title_bar, text="\u2197",
                            command=lambda: self.pop_out(panel_id),
                            bg=BG_SECONDARY, fg=FG_DIM, relief=tk.FLAT,
                            font=FONT_MONO, cursor="hand2", padx=4)
        pop_btn.pack(side=tk.RIGHT, padx=4, pady=2)

        # Content
        content = tk.Frame(outer, bg=BG_PRIMARY)
        content.pack(fill=tk.BOTH, expand=True)
        build_fn(content)

        self._panels[panel_id] = {
            "outer": outer, "content": content, "title_bar": title_bar,
            "title": title, "build_fn": build_fn, "pop_btn": pop_btn,
        }

        kw = {"minsize": minsize}
        if width:
            kw["width"] = width
        if stretch == "always":
            kw["stretch"] = "always"
        self._paned.add(outer, **kw)
        return content

    def pop_out(self, panel_id: str):
        """Detach a panel into its own floating window."""
        if panel_id in self._detached:
            self._detached[panel_id].lift()
            return
        panel = self._panels[panel_id]
        outer = panel["outer"]

        # Remember position
        panes = list(self._paned.panes())
        panel["pane_index"] = panes.index(str(outer)) if str(outer) in panes else 0

        self._paned.forget(outer)

        win = tk.Toplevel(self._parent)
        win.title(panel["title"])
        win.configure(bg=BG_PRIMARY)
        win.geometry("500x600")
        win.protocol("WM_DELETE_WINDOW", lambda: self.dock_back(panel_id))

        outer.pack_forget()
        outer.master = win
        outer.pack(in_=win, fill=tk.BOTH, expand=True)

        panel["pop_btn"].configure(text="\u2199", command=lambda: self.dock_back(panel_id))
        self._detached[panel_id] = win

    def dock_back(self, panel_id: str):
        """Bring a floating panel back into the main PanedWindow."""
        if panel_id not in self._detached:
            return
        panel = self._panels[panel_id]
        win = self._detached.pop(panel_id)
        outer = panel["outer"]

        outer.pack_forget()
        outer.master = self._paned

        idx = panel.get("pane_index", 0)
        current_panes = list(self._paned.panes())
        if idx >= len(current_panes):
            self._paned.add(outer, minsize=180)
        else:
            self._paned.add(outer, before=current_panes[idx], minsize=180)

        panel["pop_btn"].configure(text="\u2197", command=lambda: self.pop_out(panel_id))
        win.destroy()


# ── Panel builders ────────────────────────────────────────────
# Each builder receives a `content` frame and the `app` instance.

def build_agents_panel(parent: tk.Frame, app):
    """Left panel: agents list + project list."""
    app.agents_frame = tk.Frame(parent, bg=BG_SECONDARY)
    app.agents_frame.pack(fill=tk.X, padx=6, pady=(6, 0))

    # PM row
    pm_row = tk.Frame(app.agents_frame, bg=BG_HIGHLIGHT, cursor="hand2")
    pm_row.pack(fill=tk.X, pady=2)
    pm_row.bind("<Button-1>", lambda e: app._switch_agent("PM"))
    app._pm_dot = tk.Label(pm_row, text="\u25cf", bg=BG_HIGHLIGHT, fg=FG_GREEN,
                           font=FONT_MONO_LG, cursor="hand2")
    app._pm_dot.pack(side=tk.LEFT, padx=(0, 4))
    app._pm_dot.bind("<Button-1>", lambda e: app._switch_agent("PM"))
    pm_label = tk.Label(pm_row, text="Project Manager", bg=BG_HIGHLIGHT, fg=FG_PRIMARY,
                        font=FONT_MONO, cursor="hand2")
    pm_label.pack(side=tk.LEFT)
    pm_label.bind("<Button-1>", lambda e: app._switch_agent("PM"))
    app._pm_mode_label = tk.Label(pm_row, text="[API]", bg=BG_HIGHLIGHT, fg=FG_DIM,
                                   font=FONT_MONO_SM)
    app._pm_mode_label.pack(side=tk.LEFT, padx=4)

    # Wake/Kill buttons for CLI mode (hidden by default)
    pm_btn_frame = tk.Frame(pm_row, bg=BG_HIGHLIGHT)
    pm_btn_frame.pack(side=tk.RIGHT)
    app._pm_wake_btn = tk.Button(pm_btn_frame, text="Wake",
                                  command=app._wake_pm_cli,
                                  bg=BG_SURFACE, fg=FG_GREEN, relief=tk.FLAT,
                                  font=FONT_MONO_SM, padx=4)
    app._pm_kill_btn = tk.Button(pm_btn_frame, text="Kill",
                                  command=app._kill_pm_cli,
                                  bg=BG_SURFACE, fg=FG_RED, relief=tk.FLAT,
                                  font=FONT_MONO_SM, padx=4)
    # Hidden initially (API mode)
    app._pm_wake_btn.pack_forget()
    app._pm_kill_btn.pack_forget()

    app._agent_rows["PM"] = {"dot": app._pm_dot, "label": pm_label, "row": pm_row}

    tk.Frame(parent, bg=BG_SURFACE, height=1).pack(fill=tk.X, padx=6, pady=8)

    tk.Label(parent, text="Projects", bg=BG_PRIMARY, fg=FG_BLUE,
             font=FONT_MONO_MD_BOLD).pack(anchor=tk.W, padx=10, pady=(0, 4))

    list_frame = tk.Frame(parent, bg=BG_SECONDARY)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=6)

    sb = tk.Scrollbar(list_frame, **SCROLLBAR)
    app.project_list = tk.Listbox(
        list_frame, yscrollcommand=sb.set,
        bg=BG_SURFACE, fg=FG_PRIMARY, selectbackground=FG_BLUE,
        selectforeground=BG_PRIMARY, relief=tk.FLAT,
        font=FONT_MONO, activestyle="none", bd=0,
    )
    sb.config(command=app.project_list.yview)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    app.project_list.pack(fill=tk.BOTH, expand=True)

    btn_frame = tk.Frame(parent, bg=BG_PRIMARY)
    btn_frame.pack(fill=tk.X, padx=6, pady=6)
    for text, cmd in [("+ Add", app._add_project),
                      ("Edit",  app._edit_project),
                      ("Delete", app._delete_project)]:
        tk.Button(btn_frame, text=text, command=cmd, **BTN_SMALL).pack(side=tk.LEFT, padx=2)

    app._refresh_project_list()


def build_chat_panel(parent: tk.Frame, app):
    """Middle panel: chat + input."""
    app._chat_title = tk.Label(parent, text="Chat \u2014 Project Manager", bg=BG_PRIMARY,
                               fg=FG_BLUE, font=FONT_MONO_MD_BOLD)
    app._chat_title.pack(anchor=tk.W, pady=(0, 4))

    chat_frame = tk.Frame(parent, bg=BG_PRIMARY)
    chat_frame.pack(fill=tk.BOTH, expand=True)

    app.chat_box = tk.Text(
        chat_frame, bg=BG_PRIMARY, fg=FG_PRIMARY, relief=tk.FLAT,
        font=FONT_MONO_MD, state=tk.DISABLED, wrap=tk.WORD,
        padx=10, pady=10,
    )
    chat_scroll = tk.Scrollbar(chat_frame, command=app.chat_box.yview, **SCROLLBAR)
    app.chat_box.configure(yscrollcommand=chat_scroll.set)
    chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    app.chat_box.pack(fill=tk.BOTH, expand=True)

    app.chat_box.tag_config("user",  foreground=FG_BLUE, font=FONT_MONO_MD_BOLD)
    app.chat_box.tag_config("pm",    foreground=FG_GREEN, font=FONT_MONO_MD)
    app.chat_box.tag_config("tool",  foreground=FG_YELLOW, font=FONT_MONO_ITALIC)
    app.chat_box.tag_config("error", foreground=FG_RED, font=FONT_MONO_MD_BOLD)

    input_frame = tk.Frame(parent, bg=BG_SECONDARY, pady=6)
    input_frame.pack(fill=tk.X)
    app.input_var = tk.StringVar()
    entry = tk.Entry(input_frame, textvariable=app.input_var,
                     bg=BG_SURFACE, fg=FG_PRIMARY, insertbackground=FG_PRIMARY,
                     relief=tk.FLAT, font=FONT_MONO_LG)
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 6), ipady=6)
    entry.bind("<Return>", lambda e: app._send_message())
    tk.Button(input_frame, text="Send", command=app._send_message,
              **BTN_PRIMARY).pack(side=tk.RIGHT, padx=(0, 10))
    tk.Button(input_frame, text="Clear", command=app._clear_history,
              **BTN_MUTED).pack(side=tk.RIGHT, padx=(0, 4))


def build_feed_panel(parent: tk.Frame, app):
    """Feed panel: live PM feed entries."""
    feed_frame = tk.Frame(parent, bg=BG_PRIMARY)
    feed_frame.pack(fill=tk.BOTH, expand=True)

    app.feed_box = tk.Text(
        feed_frame, bg=BG_SECONDARY, fg=FG_PRIMARY, relief=tk.FLAT,
        font=FONT_MONO, state=tk.DISABLED, wrap=tk.WORD,
        padx=8, pady=6,
    )
    feed_scroll = tk.Scrollbar(feed_frame, command=app.feed_box.yview, **SCROLLBAR)
    app.feed_box.configure(yscrollcommand=feed_scroll.set)
    feed_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    app.feed_box.pack(fill=tk.BOTH, expand=True)

    app.feed_box.tag_config("time",         foreground=FG_DIM, font=FONT_MONO_SM)
    app.feed_box.tag_config("project",      foreground=FG_BLUE, font=FONT_MONO_BOLD)
    app.feed_box.tag_config("task_created", foreground=FG_GREEN)
    app.feed_box.tag_config("task_done",    foreground=FG_GREEN, font=FONT_MONO_BOLD)
    app.feed_box.tag_config("bug",          foreground=FG_RED)
    app.feed_box.tag_config("question",     foreground=FG_YELLOW)
    app.feed_box.tag_config("info",         foreground=FG_PRIMARY)


def build_approvals_panel(parent: tk.Frame, app):
    """Approvals panel: compact list of pending file changes, double-click to review."""
    approvals_outer = tk.Frame(parent, bg=BG_PRIMARY)
    approvals_outer.pack(fill=tk.BOTH, expand=True)

    app.approvals_list = tk.Listbox(
        approvals_outer, bg=BG_SECONDARY, fg=FG_PRIMARY, relief=tk.FLAT,
        font=FONT_MONO, selectbackground=BG_SURFACE,
        selectforeground=FG_YELLOW, activestyle="none",
        borderwidth=0, highlightthickness=0,
    )
    approvals_sy = tk.Scrollbar(approvals_outer, command=app.approvals_list.yview, **SCROLLBAR)
    app.approvals_list.configure(yscrollcommand=approvals_sy.set)
    approvals_sy.pack(side=tk.RIGHT, fill=tk.Y)
    app.approvals_list.pack(fill=tk.BOTH, expand=True)
    app.approvals_list.bind("<Double-1>", app._open_review_window)
    app._pending_items = []

    app.diff_status = tk.Label(parent, text="No pending changes \u2014 double-click to review",
                               bg=BG_PRIMARY, fg=FG_DIM, font=FONT_MONO)
    app.diff_status.pack(anchor=tk.W, pady=(4, 0))
