import os
import json
import subprocess
import threading
import time

os.environ["FASTMCP_HOST"] = "localhost"
os.environ["FASTMCP_PORT"] = "8001"

from fastmcp import FastMCP
import database
import project_database

REPO_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_PATH  = os.path.join(REPO_DIR, "projects.json")
PM_MEMORY_PATH = os.path.join(REPO_DIR, "pm_memory.md")
PM_PROMPT_PATH = os.path.join(REPO_DIR, "pm_instructions.md")

# initialize central DB on startup
database.init_db()

# initialize all project DBs on startup
def init_all_project_dbs():
    if not os.path.exists(PROJECTS_PATH):
        return
    with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
        projects = json.load(f)
    for p in projects:
        db_path = p.get("db_path")
        if db_path:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            project_database.init_project_db(db_path)

init_all_project_dbs()

mcp = FastMCP("agent-system")

# ── Task tools ────────────────────────────────────────────────

@mcp.tool()
def post_task(from_agent: str, to_agent: str, description: str) -> dict:
    """Post a task from one agent to another in the central task queue."""
    return database.post_task(from_agent, to_agent, description)

@mcp.tool()
def get_my_tasks(agent_id: str) -> list:
    """Get all pending tasks assigned to this agent."""
    return database.get_my_tasks(agent_id)

@mcp.tool()
def complete_task(task_id: str, result: str) -> dict:
    """Mark a central task as complete."""
    return database.complete_task(task_id, result)

@mcp.tool()
def get_all_status() -> list:
    """Get the status of all central tasks."""
    return database.get_all_status()

# ── Messaging tools ───────────────────────────────────────────

@mcp.tool()
def send_message(from_agent: str, to_agent: str, content: str, msg_type: str = "message") -> dict:
    """Send a message between agents."""
    return database.send_message(from_agent, to_agent, content, msg_type)

@mcp.tool()
def get_messages(agent_id: str) -> list:
    """Get and mark-as-read all unread messages for this agent."""
    return database.get_messages(agent_id)

@mcp.tool()
def reply_message(message_id: str, from_agent: str, content: str) -> dict:
    """Reply to a specific message."""
    return database.reply_message(message_id, from_agent, content)

# ── Project tools ─────────────────────────────────────────────

@mcp.tool()
def get_projects() -> list:
    """Get all registered projects from projects.json."""
    if not os.path.exists(PROJECTS_PATH):
        return []
    with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# ── Per-project DB tools ──────────────────────────────────────

def _get_db_path(project_id: str) -> str | None:
    if not os.path.exists(PROJECTS_PATH):
        return None
    with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
        projects = json.load(f)
    for p in projects:
        if p["id"] == project_id:
            return p.get("db_path")
    return None

@mcp.tool()
def write_project_event(project_id: str, event_type: str, content: str) -> dict:
    """
    Write an event to a project's local DB.
    project_id: which project this event belongs to
    event_type: insight / question / completion / bug / status
    content: the event details
    """
    db_path = _get_db_path(project_id)
    if not db_path:
        return {"error": f"Project not found: {project_id}"}
    return project_database.write_event(db_path, event_type, content)

@mcp.tool()
def get_project_events(project_id: str) -> list:
    """Get all unprocessed events from a project's DB."""
    db_path = _get_db_path(project_id)
    if not db_path:
        return []
    return project_database.get_unprocessed_events(db_path)

@mcp.tool()
def mark_project_event_done(project_id: str, event_id: str) -> dict:
    """Mark a project event as processed."""
    db_path = _get_db_path(project_id)
    if not db_path:
        return {"error": f"Project not found: {project_id}"}
    return project_database.mark_event_done(db_path, event_id)

@mcp.tool()
def create_project_task(project_id: str, from_project: str, description: str) -> dict:
    """
    Create a task in a project's local DB (assigned by PM).
    project_id: which project should do this task
    from_project: who is assigning it (e.g. 'PM' or another project id)
    description: what needs to be done
    """
    db_path = _get_db_path(project_id)
    if not db_path:
        return {"error": f"Project not found: {project_id}"}
    result = project_database.create_task(db_path, from_project, description)
    database.mirror_project_task(result["task_id"], project_id, description)
    return result

@mcp.tool()
def get_project_tasks(project_id: str) -> list:
    """Get all pending tasks from a project's local DB."""
    db_path = _get_db_path(project_id)
    if not db_path:
        return []
    return project_database.get_tasks(db_path)

@mcp.tool()
def complete_project_task(project_id: str, task_id: str, result: str) -> dict:
    """Mark a project task as complete."""
    db_path = _get_db_path(project_id)
    if not db_path:
        return {"error": f"Project not found: {project_id}"}
    outcome = project_database.complete_task(db_path, task_id, result)
    database.sync_task_status(task_id, "done", result)
    return outcome

# ── Wake project agent ───────────────────────────────────────

@mcp.tool()
def wake_project_agent(project_id: str) -> dict:
    """
    Wake up a project agent to process its pending tasks.
    Spawns a claude -p process in the project directory with its CLAUDE.md as context.
    project_id: the project to wake (e.g. 'sakraneldev')
    """
    if not os.path.exists(PROJECTS_PATH):
        return {"error": "projects.json not found"}
    with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
        projects = json.load(f)

    project = next((p for p in projects if p["id"] == project_id), None)
    if not project:
        return {"error": f"Project not found: {project_id}"}

    project_path = project.get("path", "")
    db_path      = project.get("db_path", "")

    claude_md_path = project.get("claude_md", os.path.join(project_path, "CLAUDE.md"))
    prompt = (
        f"You are a software engineer working on the project at {project_path}.\n"
        f"Use read_file('{claude_md_path}') to load your project conventions before starting.\n\n"
        f"Steps:\n"
        f"1. Call read_file('{claude_md_path}') to understand project conventions\n"
        f"2. Call get_project_tasks('{project_id}') to see your pending tasks\n"
        f"3. Implement each task using read_file / list_dir / write_file\n"
        f"4. Call complete_project_task('{project_id}', task_id, result) when each task is done\n"
        f"5. Call write_project_event('{project_id}', 'completion', summary) when all tasks are finished\n"
    )

    subprocess.Popen(
        ["claude", "-p", prompt, "--output-format", "stream-json",
         "--verbose", "--allowedTools", "mcp__agent-system__*"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=project_path,
    )
    return {"status": "woken", "project": project_id}

# ── File system tools ─────────────────────────────────────────

@mcp.tool()
def read_file(file_path: str) -> dict:
    """Read the contents of a file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"file_path": file_path, "content": content}
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}"}
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def list_dir(dir_path: str) -> dict:
    """List the contents of a directory."""
    try:
        entries = os.listdir(dir_path)
        result = []
        for entry in sorted(entries):
            full = os.path.join(dir_path, entry)
            result.append({"name": entry, "type": "dir" if os.path.isdir(full) else "file"})
        return {"dir_path": dir_path, "entries": result}
    except FileNotFoundError:
        return {"error": f"Directory not found: {dir_path}"}
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def write_file(project_id: str, file_path: str, content: str, description: str) -> dict:
    """
    Propose a file write — queues for user approval in the UI diff panel.
    Does NOT write immediately.
    """
    original = None
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                original = f.read()
        except Exception:
            pass
    return database.queue_write(project_id, file_path, content, description, original)

# ── PM feed tool ──────────────────────────────────────────────

@mcp.tool()
def write_pm_feed(summary: str, project_id: str = None, event_type: str = "info") -> dict:
    """
    Write a summary to the PM feed shown in the UI live feed panel.
    Call this whenever you process events or complete significant actions.
    summary: human-readable summary of what happened
    project_id: which project this relates to (optional)
    event_type: info / task_created / task_done / bug / question
    """
    return database.write_pm_feed(summary, project_id, event_type)

# ── PM memory tool ────────────────────────────────────────────

@mcp.tool()
def save_pm_memory(content: str) -> dict:
    """
    Save the PM's persistent memory across sessions.
    Call this at the end of every session.
    """
    path = os.path.abspath(PM_MEMORY_PATH)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"status": "saved"}

# ── Background event watcher ──────────────────────────────────

def _wake_pm(reason: str):
    """Spawn the PM via claude -p to handle a new event."""
    prompt_parts = []

    if os.path.exists(PM_MEMORY_PATH):
        with open(PM_MEMORY_PATH, "r", encoding="utf-8") as f:
            memory = f.read().strip()
        if memory:
            prompt_parts.append(f"[PM MEMORY]\n{memory}\n")

    if os.path.exists(PM_PROMPT_PATH):
        with open(PM_PROMPT_PATH, "r", encoding="utf-8") as f:
            instructions = f.read().strip()
        if instructions:
            prompt_parts.append(f"[PM INSTRUCTIONS]\n{instructions}\n")

    prompt_parts.append(f"[TRIGGER]\n{reason}")
    full_prompt = "\n\n".join(prompt_parts)

    subprocess.Popen(
        ["claude", "-p", full_prompt],
        cwd=REPO_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def _event_watcher():
    """Background thread: polls all project DBs for unprocessed events."""
    while True:
        try:
            if os.path.exists(PROJECTS_PATH):
                with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
                    projects = json.load(f)
                for p in projects:
                    db_path = p.get("db_path")
                    if not db_path or not os.path.exists(db_path):
                        continue
                    # Check for completion events
                    events = project_database.get_unprocessed_events(db_path)
                    if events:
                        for e in events:
                            project_database.mark_event_processing(db_path, e["id"])
                        summary = "\n".join(
                            f"- [{e['type']}] {e['content']}" for e in events
                        )
                        reason = (
                            f"New events detected in project '{p['name']}' ({p['id']}):\n"
                            f"{summary}\n\n"
                            f"Process these events: summarize them with write_pm_feed, "
                            f"create tasks if needed with create_project_task, "
                            f"mark them done with mark_project_event_done."
                        )
                        _wake_pm(reason)
                    # Check for pending approval requests
                    approvals = project_database.get_pending_approvals(db_path)
                    for a in approvals:
                        database.write_pm_feed(
                            f"[{p['name']}] Engineer requesting approval to write: {a['file_path']} — {a['description']}",
                            project_id=p["id"],
                            event_type="question",
                        )
        except Exception:
            pass
        time.sleep(3)

watcher_thread = threading.Thread(target=_event_watcher, daemon=True)
watcher_thread.start()

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
