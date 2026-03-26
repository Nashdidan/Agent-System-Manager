import tkinter as tk
from tkinter import messagebox
import json
import os
import sqlite3
import difflib
import threading
import uuid
import subprocess
from datetime import datetime

try:
    import anthropic
except ImportError:
    anthropic = None

from agent_manager import AgentRegistry, IDLE, THINKING, DEAD

# ── Paths ─────────────────────────────────────────────────────

UI_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(UI_DIR)

PROJECTS_PATH        = os.path.join(REPO_DIR, "projects.json")
CONVERSATION_PATH    = os.path.join(UI_DIR, "pm_conversation.json")
DB_PATH              = os.path.join(REPO_DIR, "mcp_server", "agent_system.db")
PM_MEMORY_PATH       = os.path.join(REPO_DIR, "pm_memory.md")
PM_INSTRUCTIONS_PATH = os.path.join(REPO_DIR, "pm_instructions.md")
TELEGRAM_ENV_PATH    = os.path.join(REPO_DIR, "telegram_bot", ".env")
TELEGRAM_BOT_SCRIPT  = os.path.join(REPO_DIR, "telegram_bot", "bot.py")

PM_MODEL = "claude-sonnet-4-6"

# ── PM Tools (Anthropic tool schemas) ─────────────────────────

PM_TOOLS = [
    {
        "name": "get_projects",
        "description": "Get all registered projects.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_all_status",
        "description": "Get the status of all central tasks.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "create_project_task",
        "description": "Create a task in a project's local DB. The engineer picks it up when woken.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id":   {"type": "string", "description": "Which project does this task"},
                "from_project": {"type": "string", "description": "Who assigns it — use 'PM'"},
                "description":  {"type": "string", "description": "Detailed description of what to implement"},
            },
            "required": ["project_id", "from_project", "description"],
        },
    },
    {
        "name": "get_project_tasks",
        "description": "Get all pending tasks for a project.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "complete_project_task",
        "description": "Mark a project task as complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "task_id":    {"type": "string"},
                "result":     {"type": "string"},
            },
            "required": ["project_id", "task_id", "result"],
        },
    },
    {
        "name": "wake_project_agent",
        "description": "Wake up a project's engineer to process pending tasks. Always call after create_project_task.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "write_pm_feed",
        "description": "Write a summary to the live feed in the UI. Call after any significant action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary":    {"type": "string"},
                "project_id": {"type": "string"},
                "event_type": {
                    "type": "string",
                    "enum": ["info", "task_created", "task_done", "bug", "question"],
                },
            },
            "required": ["summary"],
        },
    },
    {
        "name": "save_pm_memory",
        "description": "Save persistent notes for your next session. Call at the end of each session.",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the contents of a directory.",
        "input_schema": {
            "type": "object",
            "properties": {"dir_path": {"type": "string"}},
            "required": ["dir_path"],
        },
    },
    {
        "name": "ask_project_agent",
        "description": (
            "Send a question or instruction directly to a project's engineer and get their response "
            "back immediately. Use this for back-and-forth conversation, code review requests, "
            "quick questions about the codebase, or any task where you need an answer now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Which project's engineer to ask"},
                "message":    {"type": "string", "description": "Your question or instruction"},
            },
            "required": ["project_id", "message"],
        },
    },
    {
        "name": "cleanup_project_tasks",
        "description": "Delete all completed (done) tasks from a project's DB. Only call after the user explicitly confirms.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
]

# ── PM Tool execution ─────────────────────────────────────────

def _load_projects() -> list:
    if not os.path.exists(PROJECTS_PATH):
        return []
    with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _get_project_db_path(project_id: str):
    project = next((p for p in _load_projects() if p["id"] == project_id), None)
    return project.get("db_path") if project else None

def _ensure_project_db(db_path: str):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           TEXT PRIMARY KEY,
            from_project TEXT NOT NULL,
            description  TEXT NOT NULL,
            status       TEXT DEFAULT 'pending',
            result       TEXT,
            created_at   TEXT,
            updated_at   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL,
            content    TEXT NOT NULL,
            status     TEXT DEFAULT 'unprocessed',
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approvals (
            id           TEXT PRIMARY KEY,
            file_path    TEXT NOT NULL,
            new_content  TEXT NOT NULL,
            description  TEXT,
            status       TEXT DEFAULT 'pending',
            created_at   TEXT,
            updated_at   TEXT
        )
    """)
    conn.commit()
    conn.close()

def execute_pm_tool(name: str, tool_input: dict) -> str:
    try:
        if name == "get_projects":
            return json.dumps(_load_projects())

        elif name == "get_all_status":
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
            conn.close()
            return json.dumps([dict(r) for r in rows])

        elif name == "create_project_task":
            project_id   = tool_input["project_id"]
            from_project = tool_input["from_project"]
            description  = tool_input["description"]
            db_path = _get_project_db_path(project_id)
            if not db_path:
                return json.dumps({"error": f"Project not found: {project_id}"})
            _ensure_project_db(db_path)
            task_id = str(uuid.uuid4())[:8]
            now = datetime.now().isoformat()
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
                (task_id, from_project, description, "pending", None, now, now),
            )
            conn.commit()
            conn.close()
            # Mirror into central DB so PM has full view
            conn2 = sqlite3.connect(DB_PATH)
            conn2.execute(
                "INSERT OR IGNORE INTO tasks VALUES (?,?,?,?,?,?,?,?)",
                (task_id, "PM", project_id, description, "pending", None, now, now),
            )
            conn2.commit()
            conn2.close()
            return json.dumps({"task_id": task_id, "status": "created"})

        elif name == "get_project_tasks":
            project_id = tool_input["project_id"]
            db_path = _get_project_db_path(project_id)
            if not db_path or not os.path.exists(db_path):
                return json.dumps([])
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at ASC"
            ).fetchall()
            conn.close()
            return json.dumps([dict(r) for r in rows])

        elif name == "complete_project_task":
            project_id = tool_input["project_id"]
            task_id    = tool_input["task_id"]
            result     = tool_input["result"]
            db_path = _get_project_db_path(project_id)
            if not db_path:
                return json.dumps({"error": f"Project not found: {project_id}"})
            now = datetime.now().isoformat()
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE tasks SET status='done', result=?, updated_at=? WHERE id=?",
                (result, now, task_id),
            )
            conn.commit()
            conn.close()
            return json.dumps({"task_id": task_id, "status": "done"})

        elif name == "wake_project_agent":
            project_id = tool_input["project_id"]
            projects   = _load_projects()
            project    = next((p for p in projects if p["id"] == project_id), None)
            if not project:
                return json.dumps({"error": f"Project not found: {project_id}"})
            project_path   = project.get("path", "")
            db_path        = project.get("db_path", "")
            claude_md_path = project.get("claude_md", os.path.join(project_path, "CLAUDE.md"))
            sys_prompt = self._engineer_system_prompt(project) if hasattr(self, '_engineer_system_prompt') else ""
            prompt = (
                f"You have been woken up by the PM to process pending tasks.\n"
                f"Read {claude_md_path} for project conventions, then check your task queue.\n\n"
                f"To read your pending tasks, run:\n"
                f"```bash\n"
                f'python -c "import sqlite3; conn = sqlite3.connect(r\'{db_path}\'); rows = conn.execute(\'SELECT id, description FROM tasks WHERE status=\\\"pending\\\"\').fetchall(); [print(f\\\"Task {{r[0]}}: {{r[1]}}\\\") for r in rows]; conn.close()"\n'
                f"```\n\n"
                f"Process each task. Remember: you CANNOT write files directly — use the approval system described in your system prompt.\n"
            )
            subprocess.Popen(
                ["claude", "-p", prompt, "--output-format", "stream-json",
                 "--verbose", "--dangerously-skip-permissions",
                 "--allowedTools", "Read,Glob,Grep,Bash",
                 "--system-prompt", sys_prompt],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=project_path,
            )
            return json.dumps({"status": "woken", "project": project_id})

        elif name == "write_pm_feed":
            summary    = tool_input["summary"]
            project_id = tool_input.get("project_id")
            event_type = tool_input.get("event_type", "info")
            feed_id    = str(uuid.uuid4())[:8]
            now        = datetime.now().isoformat()
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO pm_feed VALUES (?,?,?,?,?)",
                (feed_id, project_id, event_type, summary, now),
            )
            conn.commit()
            conn.close()
            return json.dumps({"feed_id": feed_id, "status": "written"})

        elif name == "save_pm_memory":
            with open(PM_MEMORY_PATH, "w", encoding="utf-8") as f:
                f.write(tool_input["content"])
            return json.dumps({"status": "saved"})

        elif name == "read_file":
            file_path = tool_input["file_path"]
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.dumps({"file_path": file_path, "content": f.read()})
            except FileNotFoundError:
                return json.dumps({"error": f"File not found: {file_path}"})

        elif name == "list_dir":
            dir_path = tool_input["dir_path"]
            try:
                entries = sorted(os.listdir(dir_path))
                result  = [
                    {"name": e, "type": "dir" if os.path.isdir(os.path.join(dir_path, e)) else "file"}
                    for e in entries
                ]
                return json.dumps({"dir_path": dir_path, "entries": result})
            except FileNotFoundError:
                return json.dumps({"error": f"Directory not found: {dir_path}"})

        elif name == "ask_project_agent":
            project_id = tool_input["project_id"]
            message    = tool_input["message"]
            projects   = _load_projects()
            project    = next((p for p in projects if p["id"] == project_id), None)
            if not project:
                return json.dumps({"error": f"Project not found: {project_id}"})
            project_path   = project.get("path", "")
            claude_md_path = project.get("claude_md", os.path.join(project_path, "CLAUDE.md"))
            sys_prompt = self._engineer_system_prompt(project) if hasattr(self, '_engineer_system_prompt') else ""
            prompt = (
                f"The Project Manager is asking you:\n\n{message}\n\n"
                f"Respond clearly and concisely. Use Read, Glob, Grep as needed to answer accurately.\n"
                f"If the PM asks you to change files, use the approval system described in your system prompt."
            )
            try:
                result = subprocess.run(
                    ["claude", "-p", prompt, "--dangerously-skip-permissions",
                     "--allowedTools", "Read,Glob,Grep,Bash",
                     "--system-prompt", sys_prompt],
                    capture_output=True, text=True, encoding="utf-8",
                    cwd=project_path, timeout=120,
                )
                response = result.stdout.strip() or result.stderr.strip() or "(no response)"
                return json.dumps({"engineer_response": response})
            except subprocess.TimeoutExpired:
                return json.dumps({"error": "Engineer did not respond within 120 seconds"})

        elif name == "cleanup_project_tasks":
            project_id = tool_input["project_id"]
            db_path = _get_project_db_path(project_id)
            if not db_path or not os.path.exists(db_path):
                return json.dumps({"error": f"No DB found for project: {project_id}"})
            conn = sqlite3.connect(db_path)
            result = conn.execute("DELETE FROM tasks WHERE status='done'")
            deleted = result.rowcount
            conn.commit()
            conn.close()
            return json.dumps({"deleted": deleted, "project_id": project_id})

        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as e:
        return json.dumps({"error": str(e)})

# ── Persistence helpers ───────────────────────────────────────

def load_projects():
    return _load_projects()

def save_projects(projects):
    with open(PROJECTS_PATH, "w", encoding="utf-8") as f:
        json.dump(projects, f, indent=2)

def load_conversation():
    if not os.path.exists(CONVERSATION_PATH):
        return []
    with open(CONVERSATION_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_conversation(messages):
    with open(CONVERSATION_PATH, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2)

def load_env() -> dict:
    result = {}
    if not os.path.exists(TELEGRAM_ENV_PATH):
        return result
    with open(TELEGRAM_ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    return result

def save_env(data: dict):
    os.makedirs(os.path.dirname(TELEGRAM_ENV_PATH), exist_ok=True)
    with open(TELEGRAM_ENV_PATH, "w", encoding="utf-8") as f:
        for k, v in data.items():
            f.write(f"{k}={v}\n")

# ── SQLite helpers ────────────────────────────────────────────

def get_pending_writes():
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM pending_writes WHERE status='pending' ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def resolve_write_db(write_id: str, approved: bool):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE pending_writes SET status=? WHERE id=?",
                 ("approved" if approved else "rejected", write_id))
    conn.commit()
    conn.close()
    if approved:
        conn2 = sqlite3.connect(DB_PATH)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT * FROM pending_writes WHERE id=?", (write_id,)).fetchone()
        conn2.close()
        if row:
            os.makedirs(os.path.dirname(row["file_path"]), exist_ok=True)
            with open(row["file_path"], "w", encoding="utf-8") as f:
                f.write(row["new_content"])

def get_feed_since(since_id: str = None):
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if since_id:
        rows = conn.execute(
            "SELECT * FROM pm_feed WHERE rowid > "
            "(SELECT rowid FROM pm_feed WHERE id=?) ORDER BY created_at ASC",
            (since_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pm_feed ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)] if not since_id else [dict(r) for r in rows]

# ── Main UI ───────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Agent System")
        self.geometry("1400x820")
        self.configure(bg="#1e1e2e")
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.conversation   = load_conversation()
        self._api_messages  = self._build_api_messages()

        self._current_write   = None
        self._last_feed_id    = None
        self._pm_buffer       = ""
        self._pm_thinking     = False
        self._active_agent_id = "PM"
        self._agent_rows: dict[str, dict] = {}
        self._bot_process: subprocess.Popen | None = None

        self.registry = AgentRegistry()

        self._build_ui()

        # Pre-fill API key from .env if present
        env = load_env()
        if env.get("ANTHROPIC_API_KEY"):
            self._api_key_var.set(env["ANTHROPIC_API_KEY"])

        self._replay_chat_history()
        self._poll_pending_writes()
        self._poll_feed()
        self._poll_agent_status()
        self._poll_bot_status()
        self._sync_project_agents()

    def _build_api_messages(self) -> list:
        msgs = []
        for m in load_conversation():
            if isinstance(m.get("content"), str):
                msgs.append({"role": m["role"], "content": m["content"]})
        return msgs

    def _load_pm_system_prompt(self) -> str:
        parts = []
        if os.path.exists(PM_INSTRUCTIONS_PATH):
            with open(PM_INSTRUCTIONS_PATH, "r", encoding="utf-8") as f:
                parts.append(f.read().strip())
        if os.path.exists(PM_MEMORY_PATH):
            memory = open(PM_MEMORY_PATH, "r", encoding="utf-8").read().strip()
            if memory:
                parts.append(f"## Your memory from previous sessions\n\n{memory}")
        return "\n\n---\n\n".join(parts) if parts else ""

    def _on_close(self):
        self.registry.kill_all()
        self._stop_bot()
        self.destroy()

    # ── Settings / bot management ─────────────────────────────

    def _open_settings(self):
        SettingsDialog(self)

    def _check_bot_conflict(self, token: str) -> bool:
        """Returns True if another bot instance is already polling Telegram."""
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
                self._bot_status_label.config(text="● Bot: running", fg="#a6e3a1")
            else:
                self._bot_process = None
                self._bot_status_label.config(text="○ Bot: stopped", fg="#6c7086")
        self.after(2000, self._poll_bot_status)

    # ── UI construction ───────────────────────────────────────

    def _build_ui(self):
        top_bar = tk.Frame(self, bg="#181825", pady=6)
        top_bar.pack(fill=tk.X, padx=8, pady=(8, 0))
        self._api_key_var = tk.StringVar()
        self._pm_status_label = tk.Label(top_bar, text="● idle", bg="#181825",
                                          fg="#a6e3a1", font=("Consolas", 10))
        self._pm_status_label.pack(side=tk.LEFT, padx=16)

        self._bot_status_label = tk.Label(top_bar, text="○ Bot: stopped", bg="#181825",
                                           fg="#6c7086", font=("Consolas", 10))
        self._bot_status_label.pack(side=tk.LEFT, padx=8)

        tk.Button(top_bar, text="⚙ Settings", command=self._open_settings,
                  bg="#45475a", fg="#cdd6f4", relief=tk.FLAT,
                  font=("Consolas", 10), padx=8).pack(side=tk.RIGHT, padx=8)

        main = tk.Frame(self, bg="#1e1e2e")
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        main.columnconfigure(1, weight=2)
        main.columnconfigure(2, weight=2)
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg="#181825", width=230)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.grid_propagate(False)
        self._build_left_panel(left)

        mid = tk.Frame(main, bg="#1e1e2e")
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        self._build_chat_panel(mid)

        right = tk.Frame(main, bg="#1e1e2e")
        right.grid(row=0, column=2, sticky="nsew")
        self._build_right_panel(right)

    def _build_left_panel(self, parent):
        tk.Label(parent, text="Agents", bg="#181825", fg="#89b4fa",
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 4))

        self.agents_frame = tk.Frame(parent, bg="#181825")
        self.agents_frame.pack(fill=tk.X, padx=6)

        pm_row = tk.Frame(self.agents_frame, bg="#2a2a3e", cursor="hand2")
        pm_row.pack(fill=tk.X, pady=2)
        pm_row.bind("<Button-1>", lambda e: self._switch_agent("PM"))
        self._pm_dot = tk.Label(pm_row, text="●", bg="#2a2a3e", fg="#a6e3a1",
                                font=("Consolas", 12), cursor="hand2")
        self._pm_dot.pack(side=tk.LEFT, padx=(0, 4))
        self._pm_dot.bind("<Button-1>", lambda e: self._switch_agent("PM"))
        pm_label = tk.Label(pm_row, text="Project Manager", bg="#2a2a3e", fg="#cdd6f4",
                 font=("Consolas", 10), cursor="hand2")
        pm_label.pack(side=tk.LEFT)
        pm_label.bind("<Button-1>", lambda e: self._switch_agent("PM"))
        tk.Label(pm_row, text="[API]", bg="#2a2a3e", fg="#6c7086",
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=4)
        self._agent_rows["PM"] = {"dot": self._pm_dot, "label": pm_label, "row": pm_row}

        tk.Frame(parent, bg="#313244", height=1).pack(fill=tk.X, padx=6, pady=8)

        tk.Label(parent, text="Projects", bg="#181825", fg="#89b4fa",
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(0, 4))

        list_frame = tk.Frame(parent, bg="#181825")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=6)

        sb = tk.Scrollbar(list_frame, bg="#313244")
        self.project_list = tk.Listbox(
            list_frame, yscrollcommand=sb.set,
            bg="#313244", fg="#cdd6f4", selectbackground="#89b4fa",
            selectforeground="#1e1e2e", relief=tk.FLAT,
            font=("Consolas", 10), activestyle="none", bd=0,
        )
        sb.config(command=self.project_list.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.project_list.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(parent, bg="#181825")
        btn_frame.pack(fill=tk.X, padx=6, pady=6)
        for text, cmd in [("+ Add", self._add_project),
                          ("Edit",  self._edit_project),
                          ("Delete", self._delete_project)]:
            tk.Button(btn_frame, text=text, command=cmd,
                      bg="#45475a", fg="#cdd6f4", relief=tk.FLAT,
                      font=("Consolas", 9), padx=4).pack(side=tk.LEFT, padx=2)

        self._refresh_project_list()

    def _add_agent_row(self, agent_id: str, display_name: str):
        if agent_id in self._agent_rows:
            return
        row = tk.Frame(self.agents_frame, bg="#181825", cursor="hand2")
        row.pack(fill=tk.X, pady=2)
        row.bind("<Button-1>", lambda e, aid=agent_id: self._switch_agent(aid))

        dot = tk.Label(row, text="○", bg="#181825", fg="#6c7086",
                       font=("Consolas", 12), cursor="hand2")
        dot.pack(side=tk.LEFT, padx=(0, 4))
        dot.bind("<Button-1>", lambda e, aid=agent_id: self._switch_agent(aid))

        label = tk.Label(row, text=display_name, bg="#181825", fg="#cdd6f4",
                         font=("Consolas", 10), cursor="hand2")
        label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        label.bind("<Button-1>", lambda e, aid=agent_id: self._switch_agent(aid))

        btn_frame = tk.Frame(row, bg="#181825")
        btn_frame.pack(side=tk.RIGHT)
        tk.Button(btn_frame, text="Wake",
                  command=lambda aid=agent_id: self._wake_agent(aid),
                  bg="#313244", fg="#a6e3a1", relief=tk.FLAT,
                  font=("Consolas", 9), padx=4).pack(side=tk.LEFT, padx=1)
        tk.Button(btn_frame, text="Kill",
                  command=lambda aid=agent_id: self._kill_agent(aid),
                  bg="#313244", fg="#f38ba8", relief=tk.FLAT,
                  font=("Consolas", 9), padx=4).pack(side=tk.LEFT, padx=1)
        self._agent_rows[agent_id] = {"dot": dot, "label": label, "row": row}

    def _build_chat_panel(self, parent):
        self._chat_title = tk.Label(parent, text="Chat — Project Manager", bg="#1e1e2e",
                                    fg="#89b4fa", font=("Consolas", 11, "bold"))
        self._chat_title.pack(anchor=tk.W, pady=(0, 4))

        chat_frame = tk.Frame(parent, bg="#1e1e2e")
        chat_frame.pack(fill=tk.BOTH, expand=True)

        self.chat_box = tk.Text(
            chat_frame, bg="#1e1e2e", fg="#cdd6f4", relief=tk.FLAT,
            font=("Consolas", 11), state=tk.DISABLED, wrap=tk.WORD,
            padx=10, pady=10,
        )
        chat_scroll = tk.Scrollbar(chat_frame, command=self.chat_box.yview, bg="#313244")
        self.chat_box.configure(yscrollcommand=chat_scroll.set)
        chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_box.pack(fill=tk.BOTH, expand=True)

        self.chat_box.tag_config("user",  foreground="#89b4fa", font=("Consolas", 11, "bold"))
        self.chat_box.tag_config("pm",    foreground="#a6e3a1", font=("Consolas", 11))
        self.chat_box.tag_config("tool",  foreground="#f9e2af", font=("Consolas", 10, "italic"))
        self.chat_box.tag_config("error", foreground="#f38ba8", font=("Consolas", 11, "bold"))

        input_frame = tk.Frame(parent, bg="#181825", pady=6)
        input_frame.pack(fill=tk.X)
        self.input_var = tk.StringVar()
        entry = tk.Entry(input_frame, textvariable=self.input_var,
                         bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
                         relief=tk.FLAT, font=("Consolas", 12))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 6), ipady=6)
        entry.bind("<Return>", lambda e: self._send_message())
        tk.Button(input_frame, text="Send", command=self._send_message,
                  bg="#89b4fa", fg="#1e1e2e", relief=tk.FLAT,
                  font=("Consolas", 11, "bold"), padx=14).pack(side=tk.RIGHT, padx=(0, 10))
        tk.Button(input_frame, text="Clear", command=self._clear_history,
                  bg="#45475a", fg="#cdd6f4", relief=tk.FLAT,
                  font=("Consolas", 10), padx=8).pack(side=tk.RIGHT, padx=(0, 4))

    def _build_right_panel(self, parent):
        tk.Label(parent, text="Live Feed", bg="#1e1e2e", fg="#f9e2af",
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, pady=(0, 4))

        feed_frame = tk.Frame(parent, bg="#1e1e2e")
        feed_frame.pack(fill=tk.BOTH, expand=True)

        self.feed_box = tk.Text(
            feed_frame, bg="#181825", fg="#cdd6f4", relief=tk.FLAT,
            font=("Consolas", 10), state=tk.DISABLED, wrap=tk.WORD,
            padx=8, pady=6,
        )
        feed_scroll = tk.Scrollbar(feed_frame, command=self.feed_box.yview, bg="#313244")
        self.feed_box.configure(yscrollcommand=feed_scroll.set)
        feed_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.feed_box.pack(fill=tk.BOTH, expand=True)

        self.feed_box.tag_config("time",         foreground="#6c7086", font=("Consolas", 9))
        self.feed_box.tag_config("project",      foreground="#89b4fa", font=("Consolas", 10, "bold"))
        self.feed_box.tag_config("task_created", foreground="#a6e3a1")
        self.feed_box.tag_config("task_done",    foreground="#a6e3a1", font=("Consolas", 10, "bold"))
        self.feed_box.tag_config("bug",          foreground="#f38ba8")
        self.feed_box.tag_config("question",     foreground="#f9e2af")
        self.feed_box.tag_config("info",         foreground="#cdd6f4")

        tk.Label(parent, text="Pending File Changes", bg="#1e1e2e", fg="#f9e2af",
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, pady=(8, 4))

        diff_outer = tk.Frame(parent, bg="#1e1e2e", height=220)
        diff_outer.pack(fill=tk.X)
        diff_outer.pack_propagate(False)

        self.diff_box = tk.Text(
            diff_outer, bg="#181825", fg="#cdd6f4", relief=tk.FLAT,
            font=("Consolas", 10), state=tk.DISABLED, wrap=tk.NONE,
            padx=8, pady=6,
        )
        diff_sy = tk.Scrollbar(diff_outer, command=self.diff_box.yview, bg="#313244")
        diff_sx = tk.Scrollbar(diff_outer, orient=tk.HORIZONTAL,
                               command=self.diff_box.xview, bg="#313244")
        self.diff_box.configure(yscrollcommand=diff_sy.set, xscrollcommand=diff_sx.set)
        diff_sy.pack(side=tk.RIGHT, fill=tk.Y)
        diff_sx.pack(side=tk.BOTTOM, fill=tk.X)
        self.diff_box.pack(fill=tk.BOTH, expand=True)

        self.diff_box.tag_config("added",   foreground="#a6e3a1")
        self.diff_box.tag_config("removed", foreground="#f38ba8")
        self.diff_box.tag_config("header",  foreground="#89b4fa", font=("Consolas", 10, "bold"))
        self.diff_box.tag_config("meta",    foreground="#f9e2af")

        diff_btns = tk.Frame(parent, bg="#1e1e2e")
        diff_btns.pack(fill=tk.X, pady=(4, 0))
        tk.Button(diff_btns, text="Approve", command=self._approve_write,
                  bg="#a6e3a1", fg="#1e1e2e", relief=tk.FLAT,
                  font=("Consolas", 11, "bold"), padx=14).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(diff_btns, text="Reject", command=self._reject_write,
                  bg="#f38ba8", fg="#1e1e2e", relief=tk.FLAT,
                  font=("Consolas", 11, "bold"), padx=14).pack(side=tk.LEFT)
        self.diff_status = tk.Label(diff_btns, text="No pending changes",
                                    bg="#1e1e2e", fg="#6c7086", font=("Consolas", 10))
        self.diff_status.pack(side=tk.LEFT, padx=12)

    # ── Agent controls ────────────────────────────────────────

    def _switch_agent(self, agent_id: str):
        self._active_agent_id = agent_id
        # Update highlight
        for aid, widgets in self._agent_rows.items():
            bg = "#2a2a3e" if aid == agent_id else "#181825"
            widgets["row"].config(bg=bg)
            widgets["dot"].config(bg=bg)
            widgets["label"].config(bg=bg)
        # Update chat title
        if agent_id == "PM":
            name = "Project Manager"
        else:
            projects = load_projects()
            p = next((p for p in projects if p["id"] == agent_id), None)
            name = p["name"] if p else agent_id
        self._chat_title.config(text=f"Chat — {name}")

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
                dot.config(text="◌", fg="#f9e2af")
                widgets["label"].config(fg="#f9e2af")
            elif agent.status == IDLE:
                dot.config(text="●", fg="#a6e3a1")
                widgets["label"].config(fg="#cdd6f4")
            else:
                dot.config(text="○", fg="#6c7086")
                widgets["label"].config(fg="#6c7086")
        self.after(1000, self._poll_agent_status)

    def _engineer_system_prompt(self, project: dict) -> str:
        base = ""
        claude_md = project.get("claude_md", "")
        if claude_md and os.path.exists(claude_md):
            with open(claude_md, "r", encoding="utf-8") as f:
                base = f.read().strip() + "\n\n"
        project_id = project.get("id", "unknown")
        project_path = project.get("path", "")
        db_path = project.get("db_path", "")
        rules = (
            f"## Your identity\n"
            f"You are an engineer for project '{project_id}' at {project_path}.\n\n"
            f"## Your tools\n"
            f"Use only native tools: Read, Glob, Grep, Bash.\n"
            f"You have NO access to any MCP server — do not attempt to call MCP tools.\n"
            f"You do NOT have direct write permissions — all file writes must go through the approval system below.\n\n"
            f"## Task queue\n"
            f"The PM will inject your pending tasks directly into the conversation.\n"
            f"Each task will have an ID — implement it using your tools.\n\n"
            f"## How to write files (approval required)\n"
            f"You CANNOT write files directly. You MUST run the Bash commands below — do not describe them, do not skip them, actually execute them.\n"
            f"IMPORTANT: Do NOT ask the user for confirmation before submitting. Just submit the approval request immediately.\n"
            f"The user will approve or reject through the UI — never ask 'should I go ahead?' or 'want me to make this edit?'.\n\n"
            f"1. EXECUTE this Bash command to submit the request (replace placeholders):\n"
            f"```bash\n"
            f'python -c "\nimport sqlite3, uuid, datetime\n'
            f'conn = sqlite3.connect(r\'{db_path}\')\n'
            f'aid = str(uuid.uuid4())[:8]\n'
            f'now = datetime.datetime.now().isoformat()\n'
            f'conn.execute(\'INSERT INTO approvals VALUES (?,?,?,?,?,?,?)\', [aid, \'<file_path>\', \'<new_content>\', \'<description>\', \'pending\', now, now])\n'
            f'conn.commit()\n'
            f'print(aid)\n'
            f'conn.close()\n'
            f'"\n'
            f"```\n\n"
            f"2. Poll until approved or rejected:\n"
            f"```bash\n"
            f'python -c "\nimport sqlite3, time\n'
            f'conn = sqlite3.connect(r\'{db_path}\')\n'
            f'while True:\n'
            f'    row = conn.execute(\'SELECT status FROM approvals WHERE id=?\', [\'<approval_id>\']).fetchone()\n'
            f'    if row and row[0] != \'pending\': print(row[0]); break\n'
            f'    time.sleep(2)\n'
            f'conn.close()\n'
            f'"\n'
            f"```\n\n"
            f"3. If approved, the UI writes the file. If rejected, adjust and resubmit.\n\n"
            f"## Marking tasks done\n"
            f"When all file changes are approved and the task is complete:\n"
            f"```bash\n"
            f'python -c "import sqlite3, datetime; conn = sqlite3.connect(r\'{db_path}\'); conn.execute(\'UPDATE tasks SET status=\\\"done\\\", result=?, updated_at=? WHERE id=?\', [\'done - waiting for PM approval: <summary>\', datetime.datetime.now().isoformat(), \'<task_id>\']); conn.commit(); conn.close()"\n'
            f"```\n"
        )
        return base + rules

    def _sync_project_agents(self):
        for p in load_projects():
            agent_id = p["id"]
            db_path = p.get("db_path")
            if db_path:
                _ensure_project_db(db_path)
            if agent_id not in self._agent_rows:
                self.registry.get_or_create(
                    agent_id, p["name"], p.get("path", REPO_DIR),
                    system_prompt=self._engineer_system_prompt(p),
                )
                self._add_agent_row(agent_id, p["name"])
                self.registry.get(agent_id).start()
        self.after(5000, self._sync_project_agents)

    # ── Project management ────────────────────────────────────

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
            system_prompt=self._engineer_system_prompt(data),
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

    # ── Chat ──────────────────────────────────────────────────

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
        if thinking:
            self._pm_dot.config(text="◌", fg="#f9e2af")
            self._pm_status_label.config(text="◌ thinking", fg="#f9e2af")
        else:
            self._pm_dot.config(text="●", fg="#a6e3a1")
            self._pm_status_label.config(text="● idle", fg="#a6e3a1")

    def _send_message(self):
        text = self.input_var.get().strip()
        if not text:
            return

        if self._active_agent_id == "PM":
            if not anthropic:
                messagebox.showerror("Missing package",
                                     "anthropic package not installed.\nRun: pip install anthropic")
                return
            if self._pm_thinking:
                messagebox.showinfo("PM is busy", "The PM is currently thinking. Please wait.")
                return
            api_key = self._api_key_var.get().strip()
            if not api_key or not api_key.startswith("sk-ant-"):
                messagebox.showerror("API Key", "No valid Anthropic API key found. Add it in ⚙ Settings.")
                return
            self.input_var.set("")
            self._append_chat("user", text)
            self._append_chat("pm_start", "")
            self._pm_buffer = ""
            self._set_pm_thinking(True)
            threading.Thread(target=self._pm_api_loop, args=(text, api_key),
                             daemon=True).start()
        else:
            # Direct chat with a project engineer
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
            # Inject pending tasks from DB directly into message
            full_message = self._inject_pending_tasks(self._active_agent_id, text)
            agent.send(
                full_message,
                on_chunk=lambda chunk: self.after(0, self._append_chat, "pm", chunk),
                on_done=lambda: self.after(0, self._append_chat, "pm", "\n"),
                on_error=lambda err: self.after(0, self._append_chat, "error", err),
            )

    def _inject_pending_tasks(self, project_id: str, user_message: str) -> str:
        """Prepend pending tasks from the DB into the engineer's message."""
        try:
            db_path = _get_project_db_path(project_id)
            if not db_path or not os.path.exists(db_path):
                return user_message
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at ASC"
            ).fetchall()
            conn.close()
            if not rows:
                return user_message
            lines = [f"[PENDING TASKS FROM PM — process these if relevant:]"]
            for r in rows:
                lines.append(f"  - [{r['id']}] {r['description']}")
            lines.append("")
            return "\n".join(lines) + user_message
        except Exception:
            return user_message

    def _trim_messages(self, msgs: list, max_count: int = 20) -> list:
        """Trim to max_count, but never start with an orphaned tool_result block."""
        trimmed = msgs[-max_count:]
        while trimmed:
            content = trimmed[0].get("content", "")
            if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                trimmed = trimmed[1:]
            else:
                break
        return trimmed

    def _pm_api_loop(self, user_message: str, api_key: str):
        """PM agentic loop — Anthropic API with inline tool execution."""
        client        = anthropic.Anthropic(api_key=api_key)
        system_prompt = self._load_pm_system_prompt()
        messages      = self._trim_messages(self._api_messages)
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

                # Build assistant message preserving tool_use blocks
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
                    # Done — save to conversation
                    text_content = "".join(text_buf).strip()
                    if text_content:
                        self._api_messages = messages
                        self.conversation.append({"role": "user",      "content": user_message})
                        self.conversation.append({"role": "assistant", "content": text_content})
                        save_conversation(self.conversation)
                    break

                # Execute tools, show in chat
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

    # ── Live feed ─────────────────────────────────────────────

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

    # ── Diff / pending writes ─────────────────────────────────

    def _get_project_approvals(self) -> list:
        """Collect pending approval requests from all project agent.db files."""
        results = []
        for p in load_projects():
            db_path = p.get("db_path")
            if not db_path or not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM approvals WHERE status='pending' ORDER BY created_at ASC"
                ).fetchall()
                conn.close()
                results.extend([{**dict(r), "project_id": p["id"], "project_name": p["name"]} for r in rows])
            except Exception:
                pass
        return results

    def _resolve_project_approval(self, approval: dict, approved: bool):
        db_path = _get_project_db_path(approval["project_id"])
        if not db_path:
            return
        conn = sqlite3.connect(db_path)
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE approvals SET status=?, updated_at=? WHERE id=?",
            ("approved" if approved else "rejected", now, approval["id"])
        )
        conn.commit()
        conn.close()
        if approved:
            file_path = approval["file_path"]
            # If path is relative, resolve against the project's directory
            if not os.path.isabs(file_path):
                project = next((p for p in load_projects() if p["id"] == approval["project_id"]), None)
                if project:
                    file_path = os.path.join(project["path"], file_path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(approval["new_content"])

    def _poll_pending_writes(self):
        try:
            # Check MCP pending_writes first, then project approvals
            writes = get_pending_writes()
            approvals = self._get_project_approvals()
            all_pending = writes + approvals

            if all_pending:
                first = all_pending[0]
                item_id = first["id"]
                if self._current_write is None or self._current_write["id"] != item_id:
                    self._current_write = first
                    self._show_diff(first)
                total = len(all_pending)
                self.diff_status.config(text=f"{total} pending change(s)", fg="#f9e2af")
            else:
                if self._current_write is not None:
                    self._current_write = None
                    self._clear_diff()
                self.diff_status.config(text="No pending changes", fg="#6c7086")
        except Exception:
            pass
        self.after(2000, self._poll_pending_writes)

    def _show_diff(self, write: dict):
        self.diff_box.configure(state=tk.NORMAL)
        self.diff_box.delete("1.0", tk.END)
        original = (write.get("original_content") or "").splitlines(keepends=True)
        new      = (write.get("new_content") or "").splitlines(keepends=True)
        self.diff_box.insert(tk.END, f"File: {write.get('file_path','')}\n", "header")
        self.diff_box.insert(tk.END, f"Change: {write.get('description','')}\n\n", "meta")
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

    def _clear_diff(self):
        self.diff_box.configure(state=tk.NORMAL)
        self.diff_box.delete("1.0", tk.END)
        self.diff_box.configure(state=tk.DISABLED)

    def _approve_write(self):
        if not self._current_write:
            return
        if "project_id" in self._current_write:
            self._resolve_project_approval(self._current_write, approved=True)
        else:
            resolve_write_db(self._current_write["id"], approved=True)
        self._current_write = None

    def _reject_write(self):
        if not self._current_write:
            return
        if "project_id" in self._current_write:
            self._resolve_project_approval(self._current_write, approved=False)
        else:
            resolve_write_db(self._current_write["id"], approved=False)
        self._current_write = None


# ── Project dialog ────────────────────────────────────────────

class ProjectDialog(tk.Toplevel):
    def __init__(self, parent, title, on_save, existing=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg="#1e1e2e")
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
            row = tk.Frame(self, bg="#1e1e2e")
            row.pack(fill=tk.X, padx=16, pady=4)
            tk.Label(row, text=label, bg="#1e1e2e", fg="#cdd6f4",
                     font=("Consolas", 10), width=24, anchor=tk.W).pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            self._vars[key] = var
            tk.Entry(row, textvariable=var, bg="#313244", fg="#cdd6f4",
                     insertbackground="#cdd6f4", relief=tk.FLAT,
                     font=("Consolas", 10), width=38).pack(side=tk.LEFT, padx=4)

        btn_row = tk.Frame(self, bg="#1e1e2e")
        btn_row.pack(pady=12)
        tk.Button(btn_row, text="Save", command=self._save,
                  bg="#89b4fa", fg="#1e1e2e", relief=tk.FLAT,
                  font=("Consolas", 11, "bold"), padx=14).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg="#45475a", fg="#cdd6f4", relief=tk.FLAT,
                  font=("Consolas", 10), padx=10).pack(side=tk.LEFT)

    def _save(self):
        data = {k: v.get().strip() for k, v in self._vars.items()}
        if not data["id"] or not data["name"] or not data["path"]:
            messagebox.showerror("Missing fields", "ID, Name and Path are required.")
            return
        self._on_save(data)
        self.destroy()


# ── Settings dialog ───────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    _FIELDS = [
        ("ANTHROPIC_API_KEY",  "Anthropic API Key",  True),
        ("TELEGRAM_BOT_TOKEN", "Telegram Bot Token", True),
        ("TELEGRAM_CHAT_ID",   "Telegram Chat ID",   False),
    ]

    def __init__(self, app: "App"):
        super().__init__(app)
        self._app = app
        self.title("Settings")
        self.configure(bg="#1e1e2e")
        self.resizable(False, False)
        self.grab_set()

        env = load_env()

        tk.Label(self, text="API Keys", bg="#1e1e2e", fg="#89b4fa",
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, padx=16, pady=(12, 4))

        self._vars = {}
        for key, label, masked in self._FIELDS:
            row = tk.Frame(self, bg="#1e1e2e")
            row.pack(fill=tk.X, padx=16, pady=3)
            tk.Label(row, text=label, bg="#1e1e2e", fg="#cdd6f4",
                     font=("Consolas", 10), width=22, anchor=tk.W).pack(side=tk.LEFT)
            var = tk.StringVar(value=env.get(key, ""))
            self._vars[key] = var
            entry = tk.Entry(row, textvariable=var, show="*" if masked else "",
                             bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
                             relief=tk.FLAT, font=("Consolas", 10), width=40)
            entry.pack(side=tk.LEFT, padx=4)
            if masked:
                def _make_toggle(e=entry):
                    def _toggle():
                        e.config(show="" if e.cget("show") == "*" else "*")
                    return _toggle
                tk.Button(row, text="👁", command=_make_toggle(),
                          bg="#313244", fg="#cdd6f4", relief=tk.FLAT,
                          font=("Consolas", 9), padx=4).pack(side=tk.LEFT)

        tk.Frame(self, bg="#313244", height=1).pack(fill=tk.X, padx=16, pady=8)

        tk.Label(self, text="Telegram Bot", bg="#1e1e2e", fg="#89b4fa",
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, padx=16, pady=(0, 6))

        bot_row = tk.Frame(self, bg="#1e1e2e")
        bot_row.pack(fill=tk.X, padx=16, pady=(0, 8))

        self._status_var = tk.StringVar()
        tk.Label(bot_row, textvariable=self._status_var, bg="#1e1e2e",
                 fg="#cdd6f4", font=("Consolas", 10), width=20, anchor=tk.W).pack(side=tk.LEFT)

        self._start_btn = tk.Button(bot_row, text="Start Bot", command=self._start_bot,
                                     bg="#a6e3a1", fg="#1e1e2e", relief=tk.FLAT,
                                     font=("Consolas", 10, "bold"), padx=10)
        self._start_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._stop_btn = tk.Button(bot_row, text="Stop Bot", command=self._stop_bot,
                                    bg="#f38ba8", fg="#1e1e2e", relief=tk.FLAT,
                                    font=("Consolas", 10, "bold"), padx=10)
        self._stop_btn.pack(side=tk.LEFT)

        btn_row = tk.Frame(self, bg="#1e1e2e")
        btn_row.pack(pady=12)
        tk.Button(btn_row, text="Save", command=self._save,
                  bg="#89b4fa", fg="#1e1e2e", relief=tk.FLAT,
                  font=("Consolas", 11, "bold"), padx=14).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg="#45475a", fg="#cdd6f4", relief=tk.FLAT,
                  font=("Consolas", 10), padx=10).pack(side=tk.LEFT)

        self._refresh()

    def _is_bot_running(self) -> bool:
        return (self._app._bot_process is not None and
                self._app._bot_process.poll() is None)

    def _refresh(self):
        running = self._is_bot_running()
        self._status_var.set("● running" if running else "○ stopped")
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


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
