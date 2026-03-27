# Agent System

I built this to manage AI agents across my projects. You talk to a Project Manager, it breaks your requests into tasks, sends them to engineer agents, and every file change goes through your approval before anything touches disk.

## How it works

You open the app and chat with the PM. When you ask for something, the PM creates tasks and wakes up engineer agents that work directly on your codebase. Engineers can't write files on their own though. They propose changes, you see the diff in the approval panel, and you decide what gets written.

There's also a live feed that shows everything happening across all projects in real time, and all the panels can be resized or popped out into their own windows.

## PM modes

The PM can run in two ways. **API mode** uses the Anthropic SDK with your API key (costs credits). **CLI mode** runs through Claude Code on your Max subscription (free). You switch between them with a toggle in the top bar. Both do the same thing.

## What you need

Python 3.10+ and Claude Code CLI installed and logged in. If you only want API mode, you just need an API key and Claude Code is optional.

When you first open the app, if Claude Code isn't found it'll show a setup wizard with options to install it or continue with API mode only.

## Getting started

```
pip install -r requirements.txt
cd ui
python main.py
```

The app creates all the database files on its own. Add your projects with the + button, point to the project directory, and start chatting with the PM.

## Standalone executable

If you don't want to deal with Python:

```
cd installer
python build.py
```

This gives you `installer/dist/AgentSystem.exe`. Just needs Claude Code CLI on the target machine.

## Telegram bot

Same PM, same conversation, on your phone. Set it up from Settings in the app. You need a bot token from @BotFather and your chat ID. The bot defaults to CLI mode (free).

```
cd telegram_bot
pip install -r requirements.txt
```

## Project structure

```
agent-system/
  ui/                    the desktop app
    main.py              entry point
    panels.py            detachable panel system
    dialogs.py           review window, settings, setup wizard
    pm_engine.py         PM tools, DB helpers, prompts
    pm_cli_tools.py      CLI tool runner for PM
    agent_manager.py     engineer session management
    theme.py             colors and fonts (cross platform)
  telegram_bot/          telegram interface
  mcp_server/            legacy, no longer required
  installer/             builds the .exe
  pm_instructions.md     PM system prompt
  pm_memory.md           PM persistent memory
```

## How engineers work

Engineers are Claude Code subprocesses with restricted tools. They can read the codebase but can't edit files directly. When they want to change something, they write the proposed change to their project's database. The app picks it up, shows you the diff, and you approve or reject. The PM never touches files either, it only creates tasks and wakes engineers.

## Cross platform

Works on Windows, macOS, and Linux. Fonts and paths adjust automatically.
