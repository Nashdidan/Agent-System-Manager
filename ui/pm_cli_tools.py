"""
CLI tool runner for PM in CLI mode.
This script is the PM's ONLY interface to execute operations.
It deliberately does NOT support file editing — only PM operations.

Usage: python pm_cli_tools.py <command> [args as JSON]
"""

import sys
import os
import json
import sqlite3
import uuid
import subprocess
from datetime import datetime

# Setup paths
UI_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(UI_DIR)
PROJECTS_PATH = os.path.join(REPO_DIR, "projects.json")
DB_PATH = os.path.join(REPO_DIR, "mcp_server", "agent_system.db")
PM_MEMORY_PATH = os.path.join(REPO_DIR, "pm_memory.md")


def _load_projects():
    if not os.path.exists(PROJECTS_PATH):
        return []
    with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_project(project_id):
    return next((p for p in _load_projects() if p["id"] == project_id), None)


def get_projects():
    projects = _load_projects()
    for p in projects:
        print(f"  {p['id']}: {p['name']} ({p['type']}) — {p['path']}")
    if not projects:
        print("No projects registered.")


def create_task(project_id, description):
    project = _get_project(project_id)
    if not project:
        print(f"ERROR: Project '{project_id}' not found")
        return
    db_path = project.get("db_path")
    if not db_path:
        print(f"ERROR: No db_path for project '{project_id}'")
        return
    task_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
                 (task_id, "PM", description, "pending", None, now, now))
    conn.commit()
    conn.close()
    # Mirror to central DB
    conn2 = sqlite3.connect(DB_PATH)
    conn2.execute("INSERT OR IGNORE INTO tasks VALUES (?,?,?,?,?,?,?,?)",
                  (task_id, "PM", project_id, description, "pending", None, now, now))
    conn2.commit()
    conn2.close()
    print(f"Task {task_id} created for {project['name']}")


def get_tasks(project_id):
    project = _get_project(project_id)
    if not project:
        print(f"ERROR: Project '{project_id}' not found")
        return
    db_path = project.get("db_path")
    if not db_path or not os.path.exists(db_path):
        print("No tasks found.")
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall()
    conn.close()
    if not rows:
        print("No tasks found.")
        return
    for r in rows:
        print(f"  [{r['id']}] {r['description']} — status: {r['status']}")


def complete_task(project_id, task_id, result):
    project = _get_project(project_id)
    if not project:
        print(f"ERROR: Project '{project_id}' not found")
        return
    conn = sqlite3.connect(project["db_path"])
    conn.execute("UPDATE tasks SET status='done', result=?, updated_at=? WHERE id=?",
                 (result, datetime.now().isoformat(), task_id))
    conn.commit()
    conn.close()
    print(f"Task {task_id} marked done")


def wake_engineer(project_id):
    project = _get_project(project_id)
    if not project:
        print(f"ERROR: Project '{project_id}' not found")
        return
    # Import engineer system prompt
    from pm_engine import engineer_system_prompt
    sys_prompt = engineer_system_prompt(project)
    prompt = "You have been woken up by the PM to process pending tasks. Check your task queue and process each task."
    proc = subprocess.Popen(
        ["claude", "-p", prompt, "--output-format", "stream-json",
         "--verbose", "--dangerously-skip-permissions",
         "--allowedTools", "Read,Glob,Grep,Bash",
         "--system-prompt", sys_prompt],
        cwd=project["path"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Woke engineer for {project['name']} (PID {proc.pid})")


def ask_engineer(project_id, message):
    project = _get_project(project_id)
    if not project:
        print(f"ERROR: Project '{project_id}' not found")
        return
    from pm_engine import engineer_system_prompt
    sys_prompt = engineer_system_prompt(project)
    try:
        result = subprocess.run(
            ["claude", "-p", message, "--dangerously-skip-permissions",
             "--allowedTools", "Read,Glob,Grep,Bash",
             "--system-prompt", sys_prompt],
            capture_output=True, text=True, encoding="utf-8",
            cwd=project["path"], timeout=120,
        )
        print(result.stdout.strip() or result.stderr.strip() or "(no response)")
    except subprocess.TimeoutExpired:
        print("ERROR: Engineer did not respond within 120 seconds")


def write_feed(summary, project_id=None, event_type="info"):
    conn = sqlite3.connect(DB_PATH)
    feed_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn.execute("INSERT INTO pm_feed VALUES (?,?,?,?,?)",
                 (feed_id, project_id, event_type, summary, now))
    conn.commit()
    conn.close()
    print(f"Feed updated: {summary}")


def save_memory(content):
    with open(PM_MEMORY_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("Memory saved")


def cleanup_tasks(project_id):
    project = _get_project(project_id)
    if not project:
        print(f"ERROR: Project '{project_id}' not found")
        return
    conn = sqlite3.connect(project["db_path"])
    deleted = conn.execute("DELETE FROM tasks WHERE status='done'").rowcount
    conn.commit()
    conn.close()
    print(f"Deleted {deleted} completed tasks")


def get_all_status():
    if not os.path.exists(DB_PATH):
        print("No central DB found.")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    conn.close()
    if not rows:
        print("No tasks in central DB.")
        return
    for r in rows:
        print(f"  [{r['id']}] {r['to_agent']}: {r['description']} — {r['status']}")


COMMANDS = {
    "get_projects": lambda args: get_projects(),
    "create_task": lambda args: create_task(args["project_id"], args["description"]),
    "get_tasks": lambda args: get_tasks(args["project_id"]),
    "complete_task": lambda args: complete_task(args["project_id"], args["task_id"], args["result"]),
    "wake_engineer": lambda args: wake_engineer(args["project_id"]),
    "ask_engineer": lambda args: ask_engineer(args["project_id"], args["message"]),
    "write_feed": lambda args: write_feed(args["summary"], args.get("project_id"), args.get("event_type", "info")),
    "save_memory": lambda args: save_memory(args["content"]),
    "cleanup_tasks": lambda args: cleanup_tasks(args["project_id"]),
    "get_all_status": lambda args: get_all_status(),
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Available commands:", ", ".join(COMMANDS.keys()))
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print("Available:", ", ".join(COMMANDS.keys()))
        sys.exit(1)
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    COMMANDS[cmd](args)
