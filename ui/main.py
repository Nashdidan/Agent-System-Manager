"""
Agent System — main application.
Thin App class that wires together panels, dialogs, and the PM engine.
"""

import tkinter as tk
from tkinter import messagebox
import os
import threading
import subprocess

from agent_manager import AgentRegistry, IDLE, THINKING, DEAD
from theme import (
    BG_PRIMARY, BG_SECONDARY, BG_SURFACE, BG_SASH, BG_HOVER,
    FG_PRIMARY, FG_DIM, FG_BLUE, FG_GREEN, FG_RED, FG_YELLOW,
    FONT, FONT_SM, FONT_MD, FONT_LG,
    FONT_BOLD, FONT_MD_BOLD, FONT_LG_BOLD,
    FONT_MONO, FONT_MONO_SM, FONT_MONO_MD, FONT_MONO_LG,
    FONT_MONO_BOLD, FONT_MONO_MD_BOLD,
    BTN_MUTED, BTN_SMALL, SCROLLBAR,
)
from pm_engine import (
    anthropic, PM_TOOLS, PM_MODEL, REPO_DIR, TELEGRAM_BOT_SCRIPT,
    load_projects, save_projects, load_conversation, save_conversation,
    load_env, load_pm_system_prompt, ensure_project_db, ensure_central_db,
    execute_pm_tool, trim_messages,
    get_pending_writes, resolve_write_db, get_feed_since,
    get_project_approvals, resolve_project_approval,
    inject_pending_tasks, engineer_system_prompt, cli_pm_system_prompt,
    get_unprocessed_events, mark_event_processing, write_pm_feed_direct,
)
from panels import (
    PanelManager,
    build_agents_panel, build_chat_panel, build_feed_panel, build_approvals_panel,
)
from dialogs import ReviewWindow, ProjectDialog, SettingsDialog


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Agent System")
        self.geometry("1400x820")
        self.configure(bg=BG_PRIMARY)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        ensure_central_db()
        self.conversation   = load_conversation()
        self._api_messages  = self._build_api_messages()

        self._current_write   = None
        self._last_feed_id    = None
        self._pm_buffer       = ""
        self._pm_thinking     = False
        self._active_agent_id = "PM"
        self._agent_rows: dict[str, dict] = {}
        self._bot_process: subprocess.Popen | None = None
        self._pm_mode = "api"  # "api" or "cli"

        self.registry = AgentRegistry()

        # Create a PM CLI agent (used when mode is "cli")
        from agent_manager import AgentProcess
        self._pm_cli_agent = AgentProcess(
            "PM_CLI", "PM (CLI)", REPO_DIR,
            system_prompt=cli_pm_system_prompt(),
            on_session_saved=lambda aid, sid: None,
        )
        self._pm_cli_agent.start()

        self._build_ui()

        env = load_env()
        if env.get("ANTHROPIC_API_KEY"):
            self._api_key_var.set(env["ANTHROPIC_API_KEY"])

        self._replay_chat_history()
        self._poll_pending_writes()
        self._poll_feed()
        self._poll_agent_status()
        self._poll_bot_status()
        self._sync_project_agents()
        self._poll_events()

    # ── API message building ─────────────────────────────────

    def _build_api_messages(self) -> list:
        msgs = []
        for m in load_conversation():
            if isinstance(m.get("content"), str):
                msgs.append({"role": m["role"], "content": m["content"]})
        return msgs

    def _on_close(self):
        self.registry.kill_all()
        self._pm_cli_agent.kill()
        self._stop_bot()
        self.destroy()

    # ── UI construction ──────────────────────────────────────

    def _build_ui(self):
        from theme import BG_TOOLBAR
        top_bar = tk.Frame(self, bg=BG_TOOLBAR)
        top_bar.pack(fill=tk.X)

        self._api_key_var = tk.StringVar()

        self._pm_status_label = tk.Label(top_bar, text="\u25cf idle", bg=BG_TOOLBAR,
                                          fg=FG_GREEN, font=FONT_MONO_SM)
        self._pm_status_label.pack(side=tk.LEFT, padx=(8, 6))

        self._bot_status_label = tk.Label(top_bar, text="\u25cb bot", bg=BG_TOOLBAR,
                                           fg=FG_DIM, font=FONT_MONO_SM)
        self._bot_status_label.pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(top_bar, text="\u2699", command=self._open_settings,
                  bg=BG_TOOLBAR, fg=FG_DIM, relief=tk.FLAT,
                  font=FONT_SM, cursor="hand2", padx=4
                  ).pack(side=tk.RIGHT, padx=6, pady=2)

        # PM mode toggle
        mode_frame = tk.Frame(top_bar, bg=BG_SURFACE)
        mode_frame.pack(side=tk.RIGHT, padx=4, pady=3)
        self._pm_mode_var = tk.StringVar(value="api")
        self._api_radio = tk.Radiobutton(
            mode_frame, text="api", variable=self._pm_mode_var, value="api",
            command=self._on_pm_mode_changed,
            bg=FG_BLUE, fg="#ffffff", selectcolor=FG_BLUE,
            activebackground=FG_BLUE, activeforeground="#ffffff",
            font=FONT_MONO_SM, indicatoron=False, padx=6, pady=0,
            relief=tk.FLAT, bd=0,
        )
        self._api_radio.pack(side=tk.LEFT)
        self._cli_radio = tk.Radiobutton(
            mode_frame, text="cli", variable=self._pm_mode_var, value="cli",
            command=self._on_pm_mode_changed,
            bg=BG_SURFACE, fg=FG_DIM, selectcolor=FG_GREEN,
            activebackground=FG_GREEN, activeforeground="#ffffff",
            font=FONT_MONO_SM, indicatoron=False, padx=6, pady=0,
            relief=tk.FLAT, bd=0,
        )
        self._cli_radio.pack(side=tk.LEFT)

        # Main PanedWindow
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=BG_SASH,
                               sashwidth=2, sashrelief=tk.FLAT)
        paned.pack(fill=tk.BOTH, expand=True)

        self.panel_mgr = PanelManager(self, paned)

        self.panel_mgr.add("agents", "Agents & Projects",
                           lambda p: build_agents_panel(p, self),
                           width=230, minsize=180)
        self.panel_mgr.add("chat", "Chat",
                           lambda p: build_chat_panel(p, self),
                           minsize=300, stretch="always")
        self.panel_mgr.add("feed", "Live Feed",
                           lambda p: build_feed_panel(p, self),
                           minsize=200, width=300)
        self.panel_mgr.add("approvals", "Pending File Changes",
                           lambda p: build_approvals_panel(p, self),
                           minsize=180, width=280)

    # ── PM mode toggle ───────────────────────────────────────

    def _on_pm_mode_changed(self):
        mode = self._pm_mode_var.get()
        if self._pm_thinking:
            self._pm_mode_var.set(self._pm_mode)
            messagebox.showinfo("PM is busy", "Wait for the PM to finish before switching modes.")
            return
        self._pm_mode = mode
        from theme import BG_SURFACE
        if mode == "api":
            self._api_radio.config(bg=FG_BLUE, fg="#ffffff")
            self._cli_radio.config(bg=BG_SURFACE, fg=FG_DIM)
            self._pm_status_label.config(text="\u25cf api idle", fg=FG_GREEN)
            self._pm_mode_label.config(text="api")
            self._pm_wake_btn.pack_forget()
            self._pm_kill_btn.pack_forget()
        else:
            self._api_radio.config(bg=BG_SURFACE, fg=FG_DIM)
            self._cli_radio.config(bg=FG_GREEN, fg="#ffffff")
            self._pm_status_label.config(text="\u25cf cli idle", fg=FG_GREEN)
            self._pm_mode_label.config(text="cli")
            self._pm_wake_btn.pack(side=tk.LEFT, padx=1)
            self._pm_kill_btn.pack(side=tk.LEFT, padx=1)

    def _wake_pm_cli(self):
        if self._pm_cli_agent.status == DEAD:
            self._pm_cli_agent.start()

    def _kill_pm_cli(self):
        self._pm_cli_agent.kill()
        # Recreate with fresh system prompt
        from agent_manager import AgentProcess
        self._pm_cli_agent = AgentProcess(
            "PM_CLI", "PM (CLI)", REPO_DIR,
            system_prompt=cli_pm_system_prompt(),
            on_session_saved=lambda aid, sid: None,
        )
        self._pm_cli_agent.start()

    # ── Settings / bot management ────────────────────────────

    def _open_settings(self):
        SettingsDialog(self)

    def _check_bot_conflict(self, token: str) -> bool:
        import urllib.request
        import urllib.error
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=0&limit=1"
            urllib.request.urlopen(url, timeout=5)
            return False
        except urllib.error.HTTPError as e:
            return e.code == 409
        except Exception:
            return False

    def _start_bot(self):
        if self._bot_process and self._bot_process.poll() is None:
            messagebox.showwarning("Bot already running",
                                   "The bot is already running from this UI.")
            return
        env = os.environ.copy()
        env.update(load_env())
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            messagebox.showerror("Missing token",
                                 "Set TELEGRAM_BOT_TOKEN in Settings before starting the bot.")
            return
        if self._check_bot_conflict(token):
            messagebox.showerror(
                "Bot already running",
                "Another bot instance is already running with this token.\n\n"
                "Stop it before starting a new one."
            )
            return
        self._bot_process = subprocess.Popen(
            ["python", TELEGRAM_BOT_SCRIPT],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _stop_bot(self):
        if self._bot_process and self._bot_process.poll() is None:
            self._bot_process.terminate()
        self._bot_process = None

    def _poll_bot_status(self):
        if self._bot_process is not None:
            if self._bot_process.poll() is None:
                self._bot_status_label.config(text="\u25cf Bot: running", fg=FG_GREEN)
            else:
                self._bot_process = None
                self._bot_status_label.config(text="\u25cb Bot: stopped", fg=FG_DIM)
        self.after(2000, self._poll_bot_status)

    # ── Agent controls ───────────────────────────────────────

    def _switch_agent(self, agent_id: str):
        self._active_agent_id = agent_id
        from theme import BG_SURFACE
        for aid, widgets in self._agent_rows.items():
            selected = aid == agent_id
            bg = BG_SURFACE if selected else BG_SECONDARY
            fg = FG_PRIMARY if selected else FG_DIM
            widgets["row"].config(bg=bg)
            widgets["dot"].config(bg=bg)
            widgets["label"].config(bg=bg, fg=fg)
        if agent_id == "PM":
            name = "Project Manager"
        else:
            projects = load_projects()
            p = next((p for p in projects if p["id"] == agent_id), None)
            name = p["name"] if p else agent_id
        self._chat_title.config(text=name)

    def _add_agent_row(self, agent_id: str, display_name: str):
        if agent_id in self._agent_rows:
            return
        row = tk.Frame(self.agents_frame, bg=BG_SECONDARY, cursor="hand2")
        row.pack(fill=tk.X, pady=1)
        row.bind("<Button-1>", lambda e, aid=agent_id: self._switch_agent(aid))

        dot = tk.Label(row, text="\u25cb", bg=BG_SECONDARY, fg=FG_DIM,
                       font=FONT_MONO_SM, cursor="hand2")
        dot.pack(side=tk.LEFT, padx=(6, 4))
        dot.bind("<Button-1>", lambda e, aid=agent_id: self._switch_agent(aid))

        label = tk.Label(row, text=display_name, bg=BG_SECONDARY, fg=FG_DIM,
                         font=FONT_SM, cursor="hand2")
        label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        label.bind("<Button-1>", lambda e, aid=agent_id: self._switch_agent(aid))

        btn_frame = tk.Frame(row, bg=BG_SECONDARY)
        btn_frame.pack(side=tk.RIGHT, padx=2)
        tk.Button(btn_frame, text="w",
                  command=lambda aid=agent_id: self._wake_agent(aid),
                  bg=BG_SURFACE, fg=FG_GREEN, relief=tk.FLAT,
                  font=FONT_MONO_SM, padx=2, cursor="hand2").pack(side=tk.LEFT, padx=1)
        tk.Button(btn_frame, text="k",
                  command=lambda aid=agent_id: self._kill_agent(aid),
                  bg=BG_SURFACE, fg=FG_RED, relief=tk.FLAT,
                  font=FONT_MONO_SM, padx=2, cursor="hand2").pack(side=tk.LEFT, padx=1)
        self._agent_rows[agent_id] = {"dot": dot, "label": label, "row": row}

    def _wake_agent(self, agent_id: str):
        agent = self.registry.get(agent_id)
        if agent and not agent.is_alive():
            agent.start()

    def _kill_agent(self, agent_id: str):
        agent = self.registry.get(agent_id)
        if agent:
            agent.kill()

    def _poll_agent_status(self):
        for agent_id, widgets in self._agent_rows.items():
            agent = self.registry.get(agent_id)
            if not agent:
                continue
            dot = widgets["dot"]
            if agent.status == THINKING:
                dot.config(text="\u25cc", fg=FG_YELLOW)
                widgets["label"].config(fg=FG_YELLOW)
            elif agent.status == IDLE:
                dot.config(text="\u25cf", fg=FG_GREEN)
                widgets["label"].config(fg=FG_PRIMARY)
            else:
                dot.config(text="\u25cb", fg=FG_DIM)
                widgets["label"].config(fg=FG_DIM)
        self.after(1000, self._poll_agent_status)

    def _sync_project_agents(self):
        for p in load_projects():
            agent_id = p["id"]
            db_path = p.get("db_path")
            if db_path:
                ensure_project_db(db_path)
            if agent_id not in self._agent_rows:
                self.registry.get_or_create(
                    agent_id, p["name"], p.get("path", REPO_DIR),
                    system_prompt=engineer_system_prompt(p),
                )
                self._add_agent_row(agent_id, p["name"])
                self.registry.get(agent_id).start()
        self.after(5000, self._sync_project_agents)

    # ── Project management ───────────────────────────────────

    def _refresh_project_list(self):
        self.project_list.delete(0, tk.END)
        for p in load_projects():
            self.project_list.insert(tk.END, f"[{p['type']}] {p['name']}")

    def _add_project(self):
        ProjectDialog(self, title="Add Project", on_save=self._save_new_project)

    def _save_new_project(self, data):
        projects = load_projects()
        projects.append(data)
        save_projects(projects)
        self._refresh_project_list()
        self.registry.get_or_create(
            data["id"], data["name"], data.get("path", REPO_DIR),
            system_prompt=engineer_system_prompt(data),
        )
        self._add_agent_row(data["id"], data["name"])

    def _edit_project(self):
        idx = self.project_list.curselection()
        if not idx:
            messagebox.showinfo("Select a project", "Select a project to edit.")
            return
        projects = load_projects()
        project  = projects[idx[0]]
        def on_save(data):
            projects[idx[0]] = data
            save_projects(projects)
            self._refresh_project_list()
        ProjectDialog(self, title="Edit Project", existing=project, on_save=on_save)

    def _delete_project(self):
        idx = self.project_list.curselection()
        if not idx:
            messagebox.showinfo("Select a project", "Select a project to delete.")
            return
        projects = load_projects()
        name = projects[idx[0]]["name"]
        if messagebox.askyesno("Delete", f"Delete project '{name}'?"):
            projects.pop(idx[0])
            save_projects(projects)
            self._refresh_project_list()

    # ── Chat ─────────────────────────────────────────────────

    def _active_agent_name(self) -> str:
        if self._active_agent_id == "PM":
            return "PM"
        projects = load_projects()
        p = next((p for p in projects if p["id"] == self._active_agent_id), None)
        return p["name"] if p else self._active_agent_id

    def _append_chat(self, role: str, text: str):
        self.chat_box.configure(state=tk.NORMAL)
        if role == "user":
            self.chat_box.insert(tk.END, f"\nYou: ", "user")
            self.chat_box.insert(tk.END, text + "\n")
        elif role == "pm_start":
            self.chat_box.insert(tk.END, f"\n{self._active_agent_name()}: ", "pm")
        elif role == "pm":
            self.chat_box.insert(tk.END, text, "pm")
        elif role == "tool":
            self.chat_box.insert(tk.END, text, "tool")
        elif role == "error":
            self.chat_box.insert(tk.END, f"\nError: {text}\n", "error")
        self.chat_box.configure(state=tk.DISABLED)
        self.chat_box.see(tk.END)

    def _replay_chat_history(self):
        for msg in self.conversation:
            if msg["role"] == "user" and isinstance(msg["content"], str):
                self._append_chat("user", msg["content"])
            elif msg["role"] == "assistant" and isinstance(msg["content"], str):
                self._append_chat("pm_start", "")
                self._append_chat("pm", msg["content"] + "\n")

    def _set_pm_thinking(self, thinking: bool):
        self._pm_thinking = thinking
        mode_label = "API" if self._pm_mode == "api" else "CLI"
        if thinking:
            self._pm_dot.config(text="\u25cc", fg=FG_YELLOW)
            self._pm_status_label.config(text=f"\u25cc {mode_label} thinking", fg=FG_YELLOW)
        else:
            self._pm_dot.config(text="\u25cf", fg=FG_GREEN)
            self._pm_status_label.config(text=f"\u25cf {mode_label} idle", fg=FG_GREEN)

    def _pm_cli_done(self, user_message: str):
        self._append_chat("pm", "\n")
        self._set_pm_thinking(False)

    def _pm_cli_error(self, err: str):
        self._append_chat("error", err)
        self._set_pm_thinking(False)

    def _send_message(self):
        text = self.input_var.get().strip()
        if not text:
            return

        if self._active_agent_id == "PM":
            if self._pm_thinking:
                messagebox.showinfo("PM is busy", "The PM is currently thinking. Please wait.")
                return

            if self._pm_mode == "api":
                # API mode
                if not anthropic:
                    messagebox.showerror("Missing package",
                                         "anthropic package not installed.\nRun: pip install anthropic")
                    return
                api_key = self._api_key_var.get().strip()
                if not api_key or not api_key.startswith("sk-ant-"):
                    messagebox.showerror("API Key", "No valid Anthropic API key found. Add it in \u2699 Settings.")
                    return
                self.input_var.set("")
                self._append_chat("user", text)
                self._append_chat("pm_start", "")
                self._pm_buffer = ""
                self._set_pm_thinking(True)
                threading.Thread(target=self._pm_api_loop, args=(text, api_key),
                                 daemon=True).start()
            else:
                # CLI mode — route through AgentProcess
                if self._pm_cli_agent.status == DEAD:
                    self._pm_cli_agent.start()
                self.input_var.set("")
                self._append_chat("user", text)
                self._append_chat("pm_start", "")
                self._set_pm_thinking(True)
                self._pm_cli_agent.send(
                    text,
                    on_chunk=lambda chunk: self.after(0, self._append_chat, "pm", chunk),
                    on_done=lambda: self.after(0, self._pm_cli_done, text),
                    on_error=lambda err: self.after(0, self._pm_cli_error, err),
                )
        else:
            agent = self.registry.get(self._active_agent_id)
            if not agent:
                messagebox.showerror("Agent not found", f"No agent for {self._active_agent_id}")
                return
            if agent.status == THINKING:
                messagebox.showinfo("Busy", "This engineer is already thinking. Please wait.")
                return
            if agent.status == DEAD:
                agent.start()
            self.input_var.set("")
            self._append_chat("user", text)
            self._append_chat("pm_start", "")
            full_message = inject_pending_tasks(self._active_agent_id, text)
            agent.send(
                full_message,
                on_chunk=lambda chunk: self.after(0, self._append_chat, "pm", chunk),
                on_done=lambda: self.after(0, self._append_chat, "pm", "\n"),
                on_error=lambda err: self.after(0, self._append_chat, "error", err),
            )

    def _pm_api_loop(self, user_message: str, api_key: str):
        client        = anthropic.Anthropic(api_key=api_key)
        system_prompt = load_pm_system_prompt()
        messages      = trim_messages(self._api_messages)
        messages.append({"role": "user", "content": user_message})

        try:
            while True:
                text_buf = []

                with client.messages.stream(
                    model=PM_MODEL,
                    max_tokens=8096,
                    system=system_prompt,
                    messages=messages,
                    tools=PM_TOOLS,
                ) as stream:
                    for text in stream.text_stream:
                        text_buf.append(text)
                        self.after(0, self._append_chat, "pm", text)
                    final = stream.get_final_message()

                assistant_content = []
                for block in final.content:
                    if block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type":  "tool_use",
                            "id":    block.id,
                            "name":  block.name,
                            "input": block.input,
                        })
                messages.append({"role": "assistant", "content": assistant_content})

                tool_uses = [b for b in final.content if b.type == "tool_use"]
                if not tool_uses:
                    text_content = "".join(text_buf).strip()
                    if text_content:
                        self._api_messages = messages
                        self.conversation.append({"role": "user",      "content": user_message})
                        self.conversation.append({"role": "assistant", "content": text_content})
                        save_conversation(self.conversation)
                    break

                tool_results = []
                for block in tool_uses:
                    self.after(0, self._append_chat, "tool", f"\n[tool: {block.name}]\n")
                    result = execute_pm_tool(block.name, block.input)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result,
                    })

                messages.append({"role": "user", "content": tool_results})
                self.after(0, self._append_chat, "pm_start", "")

        except Exception as e:
            self.after(0, self._append_chat, "error", str(e))
        finally:
            self._pm_buffer = ""
            self.after(0, self._set_pm_thinking, False)

    def _clear_history(self):
        if messagebox.askyesno("Clear history", "Clear PM conversation history?"):
            self.conversation  = []
            self._api_messages = []
            save_conversation(self.conversation)
            self.chat_box.configure(state=tk.NORMAL)
            self.chat_box.delete("1.0", tk.END)
            self.chat_box.configure(state=tk.DISABLED)

    # ── Live feed ────────────────────────────────────────────

    def _poll_feed(self):
        try:
            entries = get_feed_since(self._last_feed_id)
            if not self._last_feed_id and entries:
                self.feed_box.configure(state=tk.NORMAL)
                self.feed_box.delete("1.0", tk.END)
                self.feed_box.configure(state=tk.DISABLED)
            for entry in entries:
                self._append_feed(entry)
                self._last_feed_id = entry["id"]
        except Exception:
            pass
        self.after(2000, self._poll_feed)

    def _append_feed(self, entry: dict):
        self.feed_box.configure(state=tk.NORMAL)
        time_str   = entry.get("created_at", "")[:19].replace("T", " ")
        project_id = entry.get("project_id") or ""
        event_type = entry.get("event_type", "info")
        summary    = entry.get("summary", "")
        self.feed_box.insert(tk.END, f"{time_str} ", "time")
        if project_id:
            self.feed_box.insert(tk.END, f"[{project_id}] ", "project")
        self.feed_box.insert(tk.END, summary + "\n", event_type)
        self.feed_box.configure(state=tk.DISABLED)
        self.feed_box.see(tk.END)

    # ── Event watcher (replaces MCP server's watcher) ──────

    def _poll_events(self):
        """Poll all project DBs for unprocessed events — replaces server.py's _event_watcher."""
        try:
            for p in load_projects():
                db_path = p.get("db_path")
                if not db_path or not os.path.exists(db_path):
                    continue
                events = get_unprocessed_events(db_path)
                if events:
                    for e in events:
                        mark_event_processing(db_path, e["id"])
                    summary = ", ".join(f"{e['type']}: {e['content']}" for e in events)
                    write_pm_feed_direct(
                        f"[{p['name']}] {summary}",
                        project_id=p["id"],
                        event_type="info",
                    )
        except Exception:
            pass
        self.after(3000, self._poll_events)

    # ── Pending writes / approvals ───────────────────────────

    def _poll_pending_writes(self):
        try:
            writes = get_pending_writes()
            approvals = get_project_approvals()
            all_pending = writes + approvals

            new_ids = [p["id"] for p in all_pending]
            old_ids = [p["id"] for p in self._pending_items]

            if new_ids != old_ids:
                self._pending_items = all_pending
                self.approvals_list.delete(0, tk.END)
                for item in all_pending:
                    project_name = item.get("project_name", item.get("project_id", "?"))
                    desc = item.get("description", "file change")
                    file_name = os.path.basename(item.get("file_path", ""))
                    self.approvals_list.insert(tk.END, f"  [{project_name}] {desc}  ({file_name})")

            if all_pending:
                self.diff_status.config(
                    text=f"{len(all_pending)} pending change(s) \u2014 double-click to review", fg=FG_YELLOW)
            else:
                self._pending_items = []
                self.diff_status.config(text="No pending changes", fg=FG_DIM)
        except Exception:
            pass
        self.after(2000, self._poll_pending_writes)

    def _open_review_window(self, event=None):
        sel = self.approvals_list.curselection()
        if not sel or not self._pending_items:
            return
        idx = sel[0]
        if idx >= len(self._pending_items):
            return
        item = self._pending_items[idx]
        ReviewWindow(self, item, on_resolve=self._on_review_resolved)

    def _on_review_resolved(self):
        self._current_write = None

    def _approve_write(self, item: dict):
        if "project_id" in item:
            resolve_project_approval(item, approved=True)
        else:
            resolve_write_db(item["id"], approved=True)

    def _reject_write(self, item: dict):
        if "project_id" in item:
            resolve_project_approval(item, approved=False)
        else:
            resolve_write_db(item["id"], approved=False)


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
