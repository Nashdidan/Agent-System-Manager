"""
PM engine — tool definitions, tool execution, API loop helpers,
database helpers, and persistence functions.
"""

import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime

try:
    import anthropic
except ImportError:
    anthropic = None

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

# ── Project / persistence helpers ─────────────────────────────

def load_projects() -> list:
    if not os.path.exists(PROJECTS_PATH):
        return []
    with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_projects(projects: list):
    with open(PROJECTS_PATH, "w", encoding="utf-8") as f:
        json.dump(projects, f, indent=2)

def _get_project_db_path(project_id: str):
    project = next((p for p in load_projects() if p["id"] == project_id), None)
    return project.get("db_path") if project else None

def ensure_project_db(db_path: str):
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

def load_conversation() -> list:
    if not os.path.exists(CONVERSATION_PATH):
        return []
    with open(CONVERSATION_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_conversation(messages: list):
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

def load_pm_system_prompt() -> str:
    parts = []
    if os.path.exists(PM_INSTRUCTIONS_PATH):
        with open(PM_INSTRUCTIONS_PATH, "r", encoding="utf-8") as f:
            parts.append(f.read().strip())
    if os.path.exists(PM_MEMORY_PATH):
        memory = open(PM_MEMORY_PATH, "r", encoding="utf-8").read().strip()
        if memory:
            parts.append(f"## Your memory from previous sessions\n\n{memory}")
    return "\n\n---\n\n".join(parts) if parts else ""

# ── SQLite helpers ────────────────────────────────────────────

def get_pending_writes() -> list:
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

def get_feed_since(since_id: str = None) -> list:
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

def get_project_approvals() -> list:
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

def resolve_project_approval(approval: dict, approved: bool):
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
        if not os.path.isabs(file_path):
            project = next((p for p in load_projects() if p["id"] == approval["project_id"]), None)
            if project:
                file_path = os.path.join(project["path"], file_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(approval["new_content"])

def inject_pending_tasks(project_id: str, user_message: str) -> str:
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
        lines = ["[PENDING TASKS FROM PM — process these if relevant:]"]
        for r in rows:
            lines.append(f"  - [{r['id']}] {r['description']}")
        lines.append("")
        return "\n".join(lines) + user_message
    except Exception:
        return user_message

# ── Engineer system prompt ────────────────────────────────────

def engineer_system_prompt(project: dict) -> str:
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

# ── PM Tool execution ─────────────────────────────────────────

def execute_pm_tool(name: str, tool_input: dict) -> str:
    try:
        if name == "get_projects":
            return json.dumps(load_projects())

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
            ensure_project_db(db_path)
            task_id = str(uuid.uuid4())[:8]
            now = datetime.now().isoformat()
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
                (task_id, from_project, description, "pending", None, now, now),
            )
            conn.commit()
            conn.close()
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
            projects   = load_projects()
            project    = next((p for p in projects if p["id"] == project_id), None)
            if not project:
                return json.dumps({"error": f"Project not found: {project_id}"})
            project_path   = project.get("path", "")
            db_path        = project.get("db_path", "")
            claude_md_path = project.get("claude_md", os.path.join(project_path, "CLAUDE.md"))
            sys_prompt = engineer_system_prompt(project)
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
            projects   = load_projects()
            project    = next((p for p in projects if p["id"] == project_id), None)
            if not project:
                return json.dumps({"error": f"Project not found: {project_id}"})
            project_path   = project.get("path", "")
            sys_prompt = engineer_system_prompt(project)
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

# ── API loop helper ───────────────────────────────────────────

def trim_messages(msgs: list, max_count: int = 20) -> list:
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
