 # Agent System Manager

  A personal dev tool I built to manage AI agents across multiple projects. You talk to a Project Manager that breaks
  down your requests into tasks and sends them to engineer agents working on your actual codebase.

  ## What it does

  - Chat with the PM in a desktop UI or Telegram
  - PM delegates tasks to engineer agents (runs on Claude Code CLI — uses Max subscription, no extra API cost)
  - Engineers work directly on your project files
  - Every file change goes through a diff approval screen before touching disk
  - Live feed shows what's happening across all projects

  ## You'll need

  - Python 3.13+
  - [Claude Code CLI](https://claude.ai/code) on your PATH
  - Anthropic API key

  ## Getting started

  1. `pip install fastmcp` inside `mcp_server/`
  2. Run `python mcp_server/server.py`
  3. Run `python ui/main.py`
  4. Hit Settings, drop in your API key, save
  5. Add your projects with **+ Add** and start talking to the PM

  ## Telegram (optional)

  Same conversation as the desktop UI, just on your phone. Set it up from **⚙ Settings** — you'll need a bot token from
  BotFatherand your chat ID.
