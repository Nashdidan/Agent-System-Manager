import sqlite3
import uuid
from datetime import datetime

DB_PATH = "agent_system.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()

    # -- Tasks table --
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id          TEXT PRIMARY KEY,
            from_agent  TEXT NOT NULL,
            to_agent    TEXT NOT NULL,
            description TEXT NOT NULL,
            status      TEXT DEFAULT 'pending',
            result      TEXT,
            created_at  TEXT,
            updated_at  TEXT
        )
    """)

    # -- Messages table --
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          TEXT PRIMARY KEY,
            from_agent  TEXT NOT NULL,
            to_agent    TEXT NOT NULL,
            content     TEXT NOT NULL,
            type        TEXT DEFAULT 'message',
            status      TEXT DEFAULT 'unread',
            reply_to    TEXT,
            created_at  TEXT
        )
    """)

    # -- PM feed table --
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pm_feed (
            id         TEXT PRIMARY KEY,
            project_id TEXT,
            event_type TEXT,
            summary    TEXT NOT NULL,
            created_at TEXT
        )
    """)

    # -- Pending writes table --
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_writes (
            id               TEXT PRIMARY KEY,
            project_id       TEXT NOT NULL,
            file_path        TEXT NOT NULL,
            original_content TEXT,
            new_content      TEXT NOT NULL,
            description      TEXT,
            status           TEXT DEFAULT 'pending',
            created_at       TEXT
        )
    """)

    conn.commit()
    conn.close()

# ── Task functions ────────────────────────────────────────────

def post_task(from_agent: str, to_agent: str, description: str) -> dict:
    conn = get_connection()
    task_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
        (task_id, from_agent, to_agent, description, "pending", None, now, now)
    )
    conn.commit()
    conn.close()
    return {"task_id": task_id, "status": "posted"}

def get_my_tasks(agent_id: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE to_agent=? AND status='pending'",
        (agent_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def complete_task(task_id: str, result: str) -> dict:
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE tasks SET status='done', result=?, updated_at=? WHERE id=?",
        (result, now, task_id)
    )
    conn.commit()
    conn.close()
    return {"task_id": task_id, "status": "done"}

def get_all_status() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tasks ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ── Message functions ─────────────────────────────────────────

def send_message(from_agent: str, to_agent: str, content: str, msg_type: str = "message", reply_to: str = None) -> dict:
    conn = get_connection()
    msg_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
        (msg_id, from_agent, to_agent, content, msg_type, "unread", reply_to, now)
    )
    conn.commit()
    conn.close()
    return {"message_id": msg_id, "status": "sent"}

def get_messages(agent_id: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM messages WHERE to_agent=? AND status='unread' ORDER BY created_at ASC",
        (agent_id,)
    ).fetchall()
    # Mark as read
    conn.execute(
        "UPDATE messages SET status='read' WHERE to_agent=? AND status='unread'",
        (agent_id,)
    )
    conn.commit()
    conn.close()
    return [dict(row) for row in rows]

# ── PM feed functions ─────────────────────────────────────────

def write_pm_feed(summary: str, project_id: str = None, event_type: str = "info") -> dict:
    conn = get_connection()
    feed_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO pm_feed VALUES (?,?,?,?,?)",
        (feed_id, project_id, event_type, summary, now)
    )
    conn.commit()
    conn.close()
    return {"feed_id": feed_id, "status": "written"}

def get_pm_feed(since: str = None) -> list:
    conn = get_connection()
    if since:
        rows = conn.execute(
            "SELECT * FROM pm_feed WHERE created_at > ? ORDER BY created_at ASC",
            (since,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pm_feed ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Pending write functions ───────────────────────────────────

def queue_write(project_id: str, file_path: str, new_content: str, description: str, original_content: str = None) -> dict:
    conn = get_connection()
    write_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO pending_writes VALUES (?,?,?,?,?,?,?,?)",
        (write_id, project_id, file_path, original_content, new_content, description, "pending", now)
    )
    conn.commit()
    conn.close()
    return {"write_id": write_id, "status": "pending_approval"}

def get_pending_writes() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM pending_writes WHERE status='pending' ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def mirror_project_task(task_id: str, project_id: str, description: str) -> dict:
    """Mirror a project task into the central DB so the PM has a full view."""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO tasks VALUES (?,?,?,?,?,?,?,?)",
        (task_id, "PM", project_id, description, "pending", None, now, now)
    )
    conn.commit()
    conn.close()
    return {"task_id": task_id, "status": "mirrored"}

def sync_task_status(task_id: str, status: str, result: str) -> dict:
    """Sync a project task completion back to the central DB."""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
        (status, result, now, task_id)
    )
    conn.commit()
    conn.close()
    return {"task_id": task_id, "status": status}

def resolve_write(write_id: str, approved: bool) -> dict:
    conn = get_connection()
    status = "approved" if approved else "rejected"
    conn.execute(
        "UPDATE pending_writes SET status=? WHERE id=?",
        (status, write_id)
    )
    conn.commit()
    conn.close()
    return {"write_id": write_id, "status": status}

# ── Message functions ─────────────────────────────────────────

def reply_message(message_id: str, from_agent: str, content: str) -> dict:
    conn = get_connection()
    # Get original message to know who to reply to
    original = conn.execute(
        "SELECT * FROM messages WHERE id=?",
        (message_id,)
    ).fetchone()
    conn.close()

    if not original:
        return {"status": "error", "message": "Original message not found"}

    # Send reply back to original sender
    return send_message(
        from_agent=from_agent,
        to_agent=original["from_agent"],
        content=content,
        msg_type="answer",
        reply_to=message_id
    )