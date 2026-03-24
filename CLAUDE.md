# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A multi-agent software development system where an AI Project Manager (PM) coordinates engineer agents across multiple projects. The user interacts through a Tkinter UI — chatting with the PM, approving tasks, and reviewing file changes before they're written to disk.

## Architecture

```
PM (Anthropic API, claude-sonnet-4-6)
  └── uses tools executed locally by the UI (no HTTP to MCP server needed)
       ├── create_project_task  → writes to project's agent.db
       ├── wake_project_agent   → spawns claude -p subprocess in project dir
       ├── write_pm_feed        → writes to agent_system.db pm_feed table
       └── read_file / list_dir → direct file system access

Engineer agents (claude -p subprocess, Max subscription, no API cost)
  └── uses MCP tools via ~/.claude/settings.json → localhost:8000/mcp
       ├── get_project_tasks    → reads from project's agent.db
       ├── read_file / list_dir / write_file → file system via MCP server
       ├── complete_project_task → marks task done in agent.db
       └── write_project_event  → triggers background PM wakeup
```

## Project Structure

```
agent-system\
├── mcp_server\
│   ├── server.py          ← FastMCP server (port 8000) — used by engineer agents
│   ├── database.py        ← SQLite: tasks, messages, pending_writes, pm_feed
│   ├── project_database.py← Per-project SQLite: tasks + events tables
│   ├── agent_system.db    ← runtime database (created when server.py runs)
│   └── .venv\
├── ui\
│   ├── main.py            ← Tkinter UI + PM API loop + tool execution
│   ├── agent_manager.py   ← AgentProcess/AgentRegistry for project agent display
│   └── pm_conversation.json ← PM chat history (persisted across sessions)
├── projects.json          ← registry of all projects (source of truth)
├── pm_instructions.md     ← PM system prompt
└── pm_memory.md           ← PM persistent memory (written by save_pm_memory tool)
```

## How to Run

```powershell
# Terminal 1 — MCP server (required for engineer agents)
cd C:\Idan\Projects\agent-system\mcp_server
.\.venv\Scripts\Activate.ps1
python server.py

# Terminal 2 — UI
cd C:\Idan\Projects\agent-system\ui
python main.py
```

No ngrok needed. The MCP server is registered in `~/.claude/settings.json` at `http://localhost:8000/mcp` for engineer subprocesses.

## How It Works

1. User enters Anthropic API key in the top bar (not saved to disk)
2. User types a feature request in the PM chat panel
3. UI calls Anthropic API with `PM_TOOLS` defined inline; streams response to chat
4. PM calls tools (e.g. `create_project_task` + `wake_project_agent`) — UI executes them locally
5. `wake_project_agent` spawns `claude -p` in the project directory with `--allowedTools mcp__agent-system__*`
6. Engineer reads tasks from its DB via MCP, implements them, calls `write_file` → queued in `pending_writes`
7. UI polls `pending_writes` every 2s → diff appears → user Approve/Reject → file written
8. Engineer calls `write_project_event` → background watcher in server.py wakes PM → PM writes to `pm_feed`
9. Live feed panel shows PM feed entries in real time

## PM Tools (defined in ui/main.py as PM_TOOLS)

| Tool | Executed by UI as |
|---|---|
| `get_projects` | reads `projects.json` |
| `get_all_status` | sqlite query on `agent_system.db` tasks |
| `create_project_task` | sqlite insert into project's `agent.db` |
| `get_project_tasks` | sqlite query on project's `agent.db` |
| `complete_project_task` | sqlite update on project's `agent.db` |
| `wake_project_agent` | spawns `claude -p` subprocess |
| `write_pm_feed` | sqlite insert into `agent_system.db` pm_feed |
| `save_pm_memory` | writes `pm_memory.md` |
| `read_file` / `list_dir` | direct filesystem access |

## Database Schema

**agent_system.db** (central):
```sql
tasks(id, from_agent, to_agent, description, status, result, created_at, updated_at)
messages(id, from_agent, to_agent, content, type, status, reply_to, created_at)
pending_writes(id, project_id, file_path, original_content, new_content, description, status, created_at)
pm_feed(id, project_id, event_type, summary, created_at)
```

**agent.db** (per-project):
```sql
tasks(id, from_project, description, status, result, created_at, updated_at)
events(id, type, content, status, created_at)
```

## projects.json Schema

```json
[
  {
    "id": "sakraneldev",
    "name": "Sakranel",
    "type": "FULLSTACK",
    "path": "C:\\Idan\\Projects\\Sakranel-Dev",
    "claude_md": "C:\\Idan\\Projects\\Sakranel-Dev\\CLAUDE.md",
    "db_path": "C:\\Idan\\Projects\\Sakranel-Dev\\agent.db"
  }
]
```

## Key Notes

- `agent_system.db` is created in whichever directory `server.py` is run from — always run from `mcp_server\`
- `pm_conversation.json` stores text-only conversation; delete it to reset PM memory
- `_api_messages` (in-memory) keeps full Anthropic format with tool_use/tool_result blocks for accurate context
- `write_file` (MCP tool used by engineers) never writes immediately — always goes through diff approval UI
- The background event watcher in `server.py` polls all project DBs every 3s for `write_project_event` calls
