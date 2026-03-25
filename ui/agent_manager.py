import subprocess
import threading
import json
import os

IDLE     = "idle"
THINKING = "thinking"
DEAD     = "dead"

SESSIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_sessions.json")

def _load_sessions() -> dict:
    if os.path.exists(SESSIONS_PATH):
        try:
            with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_sessions(sessions: dict):
    with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2)


class AgentProcess:
    """
    Wraps a claude -p --continue session.
    Each message spawns a new process but reuses the last session via --continue,
    so there's no cold-start context loss between messages.
    """

    def __init__(self, agent_id: str, display_name: str, cwd: str, system_prompt: str = None,
                 allowed_tools: str = None, session_id: str = None,
                 on_session_saved=None):
        self.agent_id         = agent_id
        self.display_name     = display_name
        self.cwd              = cwd
        self.system_prompt    = system_prompt
        self.allowed_tools    = allowed_tools
        self.session_id       = session_id        # persisted across restarts
        self._on_session_saved = on_session_saved # callback(agent_id, session_id)
        self.status           = DEAD
        self._proc            = None
        self._has_session     = session_id is not None
        self._lock            = threading.Lock()

    # ── Public API ────────────────────────────────────────────

    def start(self):
        """Mark as ready (no persistent process needed)."""
        with self._lock:
            self.status = IDLE

    def kill(self):
        """Terminate any in-flight process and reset session."""
        with self._lock:
            if self._proc:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc        = None
            self._has_session = False
            self.status       = DEAD

    def is_alive(self) -> bool:
        """True if ready or currently thinking (has a session or is running)."""
        return self.status in (IDLE, THINKING)

    def send(self, message: str, on_chunk, on_done, on_error):
        """
        Send a message to this agent.
        Uses --continue to resume the existing session if one exists,
        or starts fresh on first call.
        System prompt is passed via --system-prompt on first call only.
        """
        with self._lock:
            if self.status == THINKING:
                on_error(f"{self.display_name} is already thinking.")
                return
            self.status = THINKING

        threading.Thread(
            target=self._run, args=(message, on_chunk, on_done, on_error),
            daemon=True
        ).start()

    # ── Internal ──────────────────────────────────────────────

    def _run(self, message: str, on_chunk, on_done, on_error):
        try:
            cmd = ["claude", "-p", message, "--output-format", "stream-json", "--verbose",
                   "--dangerously-skip-permissions"]

            if self.session_id:
                cmd += ["--resume", self.session_id]
            elif self._has_session:
                cmd += ["--continue"]
            elif self.system_prompt:
                cmd += ["--system-prompt", self.system_prompt]

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=self.cwd,
            )
            with self._lock:
                self._proc = proc

            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    on_chunk(raw_line)
                    continue

                etype = event.get("type", "")

                if etype == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text" and block.get("text"):
                            on_chunk(block["text"])

                elif etype == "text":
                    if event.get("text"):
                        on_chunk(event["text"])

                elif etype == "result":
                    new_session_id = event.get("session_id")
                    if new_session_id and new_session_id != self.session_id:
                        self.session_id = new_session_id
                        if self._on_session_saved:
                            self._on_session_saved(self.agent_id, new_session_id)
                    self._has_session = True
                    with self._lock:
                        self._proc  = None
                        self.status = IDLE
                    on_done()
                    return

                elif etype == "error":
                    with self._lock:
                        self._proc  = None
                        self.status = IDLE
                    on_error(event.get("error", {}).get("message", "Unknown error"))
                    return

            proc.wait()
            stderr_out = proc.stderr.read()
            with self._lock:
                self._proc  = None
                self.status = IDLE

            if proc.returncode != 0 and stderr_out:
                on_error(stderr_out.strip())
            else:
                self._has_session = True
                on_done()

        except FileNotFoundError:
            with self._lock:
                self.status = DEAD
            on_error("'claude' command not found. Make sure Claude Code is installed and on PATH.")
        except Exception as e:
            with self._lock:
                self._proc  = None
                self.status = IDLE
            on_error(str(e))

    # ── Status display ────────────────────────────────────────

    @property
    def status_dot(self) -> str:
        return {"idle": "●", "thinking": "◌", "dead": "○"}.get(self.status, "○")

    @property
    def status_color(self) -> str:
        return {"idle": "#a6e3a1", "thinking": "#f9e2af", "dead": "#6c7086"}.get(self.status, "#6c7086")


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, AgentProcess] = {}
        self._sessions = _load_sessions()
        self._lock = threading.Lock()

    def _on_session_saved(self, agent_id: str, session_id: str):
        self._sessions[agent_id] = session_id
        _save_sessions(self._sessions)

    def get_or_create(self, agent_id: str, display_name: str, cwd: str,
                      system_prompt: str = None, allowed_tools: str = None) -> AgentProcess:
        with self._lock:
            if agent_id not in self._agents:
                session_id = self._sessions.get(agent_id)
                self._agents[agent_id] = AgentProcess(
                    agent_id, display_name, cwd, system_prompt, allowed_tools,
                    session_id=session_id, on_session_saved=self._on_session_saved,
                )
            return self._agents[agent_id]

    def get(self, agent_id: str) -> AgentProcess | None:
        return self._agents.get(agent_id)

    def all_agents(self) -> list[AgentProcess]:
        return list(self._agents.values())

    def kill_all(self):
        for agent in self._agents.values():
            agent.kill()
