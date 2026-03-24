import sqlite3
import uuid
from datetime import datetime


def get_connection(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_project_db(db_path: str):
    """Initialize a project's local database with events and tasks tables."""
    conn = get_connection(db_path)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL,
            content    TEXT NOT NULL,
            status     TEXT DEFAULT 'unprocessed',
            created_at TEXT
        )
    """)
    # type: insight / question / completion / bug / status
    # status: unprocessed / processing / done

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
    # Tasks assigned TO this project by the PM

    conn.commit()
    conn.close()


# ── Event functions ───────────────────────────────────────────

def write_event(db_path: str, event_type: str, content: str) -> dict:
    conn = get_connection(db_path)
    event_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO events VALUES (?,?,?,?,?)",
        (event_id, event_type, content, "unprocessed", now)
    )
    conn.commit()
    conn.close()
    return {"event_id": event_id, "status": "written"}


def get_unprocessed_events(db_path: str) -> list:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM events WHERE status='unprocessed' ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_event_done(db_path: str, event_id: str) -> dict:
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE events SET status='done' WHERE id=?",
        (event_id,)
    )
    conn.commit()
    conn.close()
    return {"event_id": event_id, "status": "done"}


def mark_event_processing(db_path: str, event_id: str) -> dict:
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE events SET status='processing' WHERE id=?",
        (event_id,)
    )
    conn.commit()
    conn.close()
    return {"event_id": event_id, "status": "processing"}


# ── Task functions ────────────────────────────────────────────

def create_task(db_path: str, from_project: str, description: str) -> dict:
    conn = get_connection(db_path)
    task_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
        (task_id, from_project, description, "pending", None, now, now)
    )
    conn.commit()
    conn.close()
    return {"task_id": task_id, "status": "created"}


def get_tasks(db_path: str, status: str = "pending") -> list:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status=? ORDER BY created_at ASC",
        (status,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_task(db_path: str, task_id: str, result: str) -> dict:
    conn = get_connection(db_path)
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE tasks SET status='done', result=?, updated_at=? WHERE id=?",
        (result, now, task_id)
    )
    conn.commit()
    conn.close()
    return {"task_id": task_id, "status": "done"}
