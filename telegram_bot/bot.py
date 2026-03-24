#!/usr/bin/env python3
"""
Telegram bot interface for the Agent System PM.
Shares pm_conversation.json and agent_system.db with the Tkinter UI.

Setup:
  cd telegram_bot
  pip install -r requirements.txt
  set TELEGRAM_BOT_TOKEN=<token from @BotFather>
  set ANTHROPIC_API_KEY=<your key>
  set TELEGRAM_CHAT_ID=<your numeric chat ID>
  python bot.py
"""

import asyncio
import difflib
import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))payoneer
except ImportError:
    pass  # dotenv is optional; fall back to real environment variables

try:
    import anthropic
except ImportError:
    raise SystemExit("Run: pip install anthropic")

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters
except ImportError:
    raise SystemExit("Run: pip install python-telegram-bot")

# ── Config ────────────────────────────────────────────────────

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ALLOWED_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")  # leave empty to allow all (not recommended)

# ── Paths (mirrors ui/main.py) ────────────────────────────────

BOT_DIR  = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BOT_DIR)
UI_DIR   = os.path.join(REPO_DIR, "ui")

PROJECTS_PATH        = os.path.join(REPO_DIR, "projects.json")
CONVERSATION_PATH    = os.path.join(UI_DIR, "pm_conversation.json")
DB_PATH              = os.path.join(REPO_DIR, "mcp_server", "agent_system.db")
PM_MEMORY_PATH       = os.path.join(REPO_DIR, "pm_memory.md")
PM_INSTRUCTIONS_PATH = os.path.join(REPO_DIR, "pm_instructions.md")

PM_MODEL = "claude-sonnet-4-6"

# ── PM Tools ──────────────────────────────────────────────────

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
        "description": "Write a summary to the live feed. Call after any significant action.",
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
        "description": "Save persistent notes for your next session.",
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
            "back immediately. Use for code review, questions, or any task needing an answer now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "message":    {"type": "string"},
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

# ── Tool execution ────────────────────────────────────────────

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
            id TEXT PRIMARY KEY, from_project TEXT NOT NULL,
            description TEXT NOT NULL, status TEXT DEFAULT 'pending',
            result TEXT, created_at TEXT, updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, type TEXT NOT NULL,
            content TEXT NOT NULL, status TEXT DEFAULT 'unprocessed', created_at TEXT
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
            conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
                         (task_id, from_project, description, "pending", None, now, now))
            conn.commit()
            conn.close()
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
            conn.execute("UPDATE tasks SET status='done', result=?, updated_at=? WHERE id=?",
                         (result, now, task_id))
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
            claude_md_path = project.get("claude_md", os.path.join(project_path, "CLAUDE.md"))
            prompt = (
                f"You are a software engineer working on the project at {project_path}.\n"
                f"Use read_file('{claude_md_path}') to load project conventions before starting.\n\n"
                f"Steps:\n"
                f"1. Call read_file('{claude_md_path}') to understand conventions\n"
                f"2. Call get_project_tasks('{project_id}') to see pending tasks\n"
                f"3. Implement each task using read_file / list_dir / write_file\n"
                f"4. When a task is done, call complete_project_task('{project_id}', task_id, result) with a short summary of what was done\n"
                f"5. If you cannot complete a task (missing info, unclear requirements, technical blocker), still call complete_project_task but prefix the result with 'BLOCKED: ' followed by a clear explanation of exactly what is missing or wrong — be specific so the PM can relay it to the user\n"
                f"6. If a task requires clarification before you can start, prefix with 'NEEDS_INFO: ' followed by your exact question\n"
                f"7. Call write_project_event('{project_id}', 'completion', summary) when all tasks have been processed (done or blocked)\n"
            )
            subprocess.Popen(
                ["claude", "-p", prompt, "--output-format", "stream-json",
                 "--verbose", "--allowedTools", "mcp__agent-system__*"],
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
            conn.execute("INSERT INTO pm_feed VALUES (?,?,?,?,?)",
                         (feed_id, project_id, event_type, summary, now))
            conn.commit()
            conn.close()
            return json.dumps({"feed_id": feed_id, "status": "written"})

        elif name == "save_pm_memory":
            with open(PM_MEMORY_PATH, "w", encoding="utf-8") as f:
                f.write(tool_input["content"])
            return json.dumps({"status": "saved"})

        elif name == "read_file":
            try:
                with open(tool_input["file_path"], "r", encoding="utf-8") as f:
                    return json.dumps({"content": f.read()})
            except FileNotFoundError:
                return json.dumps({"error": f"File not found: {tool_input['file_path']}"})

        elif name == "list_dir":
            try:
                entries = sorted(os.listdir(tool_input["dir_path"]))
                result  = [
                    {"name": e, "type": "dir" if os.path.isdir(os.path.join(tool_input["dir_path"], e)) else "file"}
                    for e in entries
                ]
                return json.dumps({"entries": result})
            except FileNotFoundError:
                return json.dumps({"error": f"Directory not found: {tool_input['dir_path']}"})

        elif name == "ask_project_agent":
            project_id = tool_input["project_id"]
            message    = tool_input["message"]
            projects   = _load_projects()
            project    = next((p for p in projects if p["id"] == project_id), None)
            if not project:
                return json.dumps({"error": f"Project not found: {project_id}"})
            project_path   = project.get("path", "")
            claude_md_path = project.get("claude_md", os.path.join(project_path, "CLAUDE.md"))
            prompt = (
                f"You are a software engineer working on the project at {project_path}.\n"
                f"Read {claude_md_path} for project conventions.\n\n"
                f"The Project Manager is asking:\n\n{message}\n\n"
                f"Respond clearly and concisely. Use read_file / list_dir as needed to answer accurately."
            )
            try:
                result = subprocess.run(
                    ["claude", "-p", prompt],
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

# ── Conversation helpers ───────────────────────────────────────

def load_conversation() -> list:
    if not os.path.exists(CONVERSATION_PATH):
        return []
    with open(CONVERSATION_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_conversation(messages: list):
    with open(CONVERSATION_PATH, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2)

def _build_api_messages(conversation: list) -> list:
    msgs = []
    for m in conversation:
        if isinstance(m.get("content"), str):
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs

def _trim_messages(msgs: list) -> list:
    """Strip leading orphaned tool_result blocks that would cause a 400."""
    while msgs and msgs[0].get("role") == "user":
        first_content = msgs[0].get("content", [])
        if isinstance(first_content, list) and any(b.get("type") == "tool_result" for b in first_content):
            msgs = msgs[1:]
        else:
            break
    return msgs

def _load_pm_system_prompt() -> str:
    parts = []
    if os.path.exists(PM_INSTRUCTIONS_PATH):
        with open(PM_INSTRUCTIONS_PATH, "r", encoding="utf-8") as f:
            parts.append(f.read().strip())
    if os.path.exists(PM_MEMORY_PATH):
        memory = open(PM_MEMORY_PATH, "r", encoding="utf-8").read().strip()
        if memory:
            parts.append(f"## Your memory from previous sessions\n\n{memory}")
    return "\n\n---\n\n".join(parts) if parts else ""

# ── PM agentic loop ───────────────────────────────────────────

def _run_pm_loop_sync(user_message: str) -> str:
    """
    Run the full PM agentic loop (tool calls included) synchronously.
    Shares pm_conversation.json with the Tkinter UI.
    Returns the accumulated text response.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    conversation = load_conversation()
    api_messages = _build_api_messages(conversation)

    conversation.append({"role": "user", "content": user_message})
    api_messages.append({"role": "user", "content": user_message})
    save_conversation(conversation)

    system_prompt = _load_pm_system_prompt()
    full_text = ""

    while True:
        trimmed = _trim_messages(list(api_messages))
        if len(trimmed) > 40:
            trimmed = _trim_messages(trimmed[-40:])

        response = client.messages.create(
            model=PM_MODEL,
            max_tokens=8096,
            system=system_prompt,
            tools=PM_TOOLS,
            messages=trimmed,
        )

        text_chunks = []
        tool_uses   = []

        for block in response.content:
            if block.type == "text":
                text_chunks.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        text = "".join(text_chunks)
        if text:
            full_text += text

        api_messages.append({"role": "assistant", "content": response.content})
        if text:
            conversation.append({"role": "assistant", "content": text})
            save_conversation(conversation)

        if response.stop_reason == "end_turn" or not tool_uses:
            break

        tool_results = []
        for tu in tool_uses:
            result = execute_pm_tool(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })

        api_messages.append({"role": "user", "content": tool_results})

    return full_text or "(done)"

# ── Pending writes ────────────────────────────────────────────

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

def resolve_write(write_id: str, approved: bool):
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
            "SELECT * FROM pm_feed ORDER BY created_at DESC LIMIT 1"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Bot state ─────────────────────────────────────────────────

_sent_write_ids: set = set()
_last_feed_id: str   = None

# ── Helpers ───────────────────────────────────────────────────

def _is_authorized(update: Update) -> bool:
    if not ALLOWED_CHAT_ID:
        return True
    return str(update.effective_chat.id) == ALLOWED_CHAT_ID

def _split_message(text: str, limit: int = 4000) -> list:
    """Split long text into chunks within Telegram's limit."""
    return [text[i:i+limit] for i in range(0, len(text), limit)]

# ── Handlers ──────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    user_text    = update.message.text
    thinking_msg = await update.message.reply_text("Thinking...")

    try:
        response_text = await asyncio.to_thread(_run_pm_loop_sync, user_text)
    except Exception as e:
        await thinking_msg.edit_text(f"Error: {e}")
        return

    chunks = _split_message(response_text)
    await thinking_msg.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await update.message.reply_text(chunk)

    await _check_pending_writes(update.effective_chat.id, context.bot)


async def _check_pending_writes(chat_id: int, bot):
    global _sent_write_ids
    for w in get_pending_writes():
        if w["id"] in _sent_write_ids:
            continue
        _sent_write_ids.add(w["id"])

        old_lines = (w.get("original_content") or "").splitlines(keepends=True)
        new_lines = (w.get("new_content") or "").splitlines(keepends=True)
        diff_str  = "".join(list(difflib.unified_diff(old_lines, new_lines, lineterm=""))[:80])

        path = w.get("file_path", "")
        desc = w.get("description", "")
        text = f"Pending file change\n{path}\n{desc}\n\n{diff_str}"

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve", callback_data=f"approve:{w['id']}"),
            InlineKeyboardButton("Reject",  callback_data=f"reject:{w['id']}"),
        ]])

        for chunk in _split_message(text):
            msg = await bot.send_message(chat_id=chat_id, text=chunk, reply_markup=keyboard)
            # Only attach buttons to last chunk
            if chunk != _split_message(text)[-1]:
                await bot.edit_message_reply_markup(chat_id=chat_id,
                                                    message_id=msg.message_id,
                                                    reply_markup=None)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, write_id = query.data.split(":", 1)
    approved = action == "approve"

    try:
        await asyncio.to_thread(resolve_write, write_id, approved)
        status = "Approved — written to disk" if approved else "Rejected"
    except Exception as e:
        status = f"Error: {e}"

    await query.edit_message_reply_markup(None)
    await query.message.reply_text(status)
    _sent_write_ids.discard(write_id)


async def poll_feed(context: ContextTypes.DEFAULT_TYPE):
    """Push new PM feed entries to the owner's chat."""
    global _last_feed_id
    if not ALLOWED_CHAT_ID:
        return

    entries = await asyncio.to_thread(get_feed_since, _last_feed_id)
    for entry in entries:
        _last_feed_id = entry["id"]
        icons = {"task_created": "[task]", "task_done": "[done]", "bug": "[bug]", "question": "[?]"}
        icon  = icons.get(entry.get("event_type", ""), "[info]")
        proj  = entry.get("project_id") or "PM"
        text  = f"{icon} [{proj}] {entry['summary']}"
        try:
            await context.bot.send_message(chat_id=int(ALLOWED_CHAT_ID), text=text)
        except Exception:
            pass

# ── Main ─────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable")
    if not ANTHROPIC_API_KEY:
        raise SystemExit("Set ANTHROPIC_API_KEY environment variable")

    print("Starting Telegram bot...")
    if ALLOWED_CHAT_ID:
        print(f"Restricted to chat ID: {ALLOWED_CHAT_ID}")
    else:
        print("WARNING: TELEGRAM_CHAT_ID not set — bot will respond to anyone")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(poll_feed, interval=15, first=10)

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
