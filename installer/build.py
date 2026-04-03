"""
Build script for AgentSystem executable.
Run from the installer/ directory:
    python build.py

Output: installer/dist/AgentSystem.exe (Windows) or AgentSystem (macOS/Linux)
"""

import subprocess
import sys
import os

INSTALLER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(INSTALLER_DIR)
UI_DIR = os.path.join(REPO_DIR, "ui")
MAIN_SCRIPT = os.path.join(UI_DIR, "main.py")

# Files to bundle alongside the exe
DATA_FILES = [
    (os.path.join(UI_DIR, "theme.py"), "ui"),
    (os.path.join(UI_DIR, "panels.py"), "ui"),
    (os.path.join(UI_DIR, "dialogs.py"), "ui"),
    (os.path.join(UI_DIR, "pm_engine.py"), "ui"),
    (os.path.join(UI_DIR, "pm_cli_tools.py"), "ui"),
    (os.path.join(UI_DIR, "agent_manager.py"), "ui"),
    (os.path.join(REPO_DIR, "pm_instructions.md"), "."),
]

def main():
    # Ensure pyinstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Build the --add-data arguments
    sep = ";" if sys.platform == "win32" else ":"
    add_data_args = []
    for src, dest in DATA_FILES:
        if os.path.exists(src):
            add_data_args += ["--add-data", f"{src}{sep}{dest}"]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "AgentSystem",
        "--distpath", os.path.join(INSTALLER_DIR, "dist"),
        "--workpath", os.path.join(INSTALLER_DIR, "build"),
        "--specpath", INSTALLER_DIR,
        *add_data_args,
        "--paths", UI_DIR,
        MAIN_SCRIPT,
    ]

    print(f"Building from: {MAIN_SCRIPT}")
    print(f"Output: {os.path.join(INSTALLER_DIR, 'dist')}")
    print()
    subprocess.check_call(cmd)
    print("\nDone! Executable is in installer/dist/")


if __name__ == "__main__":
    main()
