# ── Theme — macOS Terminal style, cross-platform ─────────────
import sys
import platform

# ── Platform-aware font selection ─────────────────────────────
_os = platform.system()
if _os == "Darwin":       # macOS
    _MONO = "Menlo"
elif _os == "Windows":
    _MONO = "Cascadia Mono"
else:                     # Linux
    _MONO = "DejaVu Sans Mono"

# ── Platform-aware python command ─────────────────────────────
PYTHON_CMD = sys.executable  # always correct, even in venvs

# Colors (Terminal.app dark profile)
BG_PRIMARY   = "#1e1e1e"
BG_SECONDARY = "#252526"
BG_SURFACE   = "#2d2d2d"
BG_HIGHLIGHT = "#37373d"
BG_SASH      = "#3e3e42"
BG_TOOLBAR   = "#252526"
BG_CARD      = "#2d2d2d"
BG_HOVER     = "#333333"

FG_PRIMARY   = "#cccccc"
FG_SECONDARY = "#b0b0b0"
FG_DIM       = "#6a6a6a"
FG_BLUE      = "#5a9bcf"
FG_GREEN     = "#6ab04c"
FG_RED       = "#e06c75"
FG_YELLOW    = "#d19a66"
FG_ORANGE    = "#d19a66"
FG_INDIGO    = "#7c7cba"

# Fonts — all monospace, compact
FONT_SM        = (_MONO, 9)
FONT           = (_MONO, 9)
FONT_MD        = (_MONO, 10)
FONT_LG        = (_MONO, 11)
FONT_XL        = (_MONO, 12)
FONT_BOLD      = (_MONO, 9, "bold")
FONT_MD_BOLD   = (_MONO, 10, "bold")
FONT_LG_BOLD   = (_MONO, 11, "bold")
FONT_XL_BOLD   = (_MONO, 12, "bold")
FONT_ITALIC    = (_MONO, 9, "italic")
FONT_MONO      = (_MONO, 9)
FONT_MONO_SM   = (_MONO, 8)
FONT_MONO_BOLD = (_MONO, 9, "bold")

# Backward compat aliases
FONT_MONO_MD       = (_MONO, 10)
FONT_MONO_LG       = (_MONO, 11)
FONT_MONO_MD_BOLD  = (_MONO, 10, "bold")
FONT_MONO_LG_BOLD  = (_MONO, 11, "bold")
FONT_MONO_ITALIC   = (_MONO, 9, "italic")

# Buttons
BTN_PRIMARY = dict(bg=FG_BLUE, fg="#ffffff", relief="flat", font=FONT_BOLD,
                   padx=8, pady=1, cursor="hand2")
BTN_SUCCESS = dict(bg=FG_GREEN, fg="#ffffff", relief="flat", font=FONT_BOLD,
                   padx=8, pady=1, cursor="hand2")
BTN_DANGER  = dict(bg=FG_RED, fg="#ffffff", relief="flat", font=FONT_BOLD,
                   padx=8, pady=1, cursor="hand2")
BTN_MUTED   = dict(bg=BG_SURFACE, fg=FG_DIM, relief="flat", font=FONT_SM,
                   padx=6, pady=1, cursor="hand2")
BTN_SMALL   = dict(bg=BG_SURFACE, fg=FG_DIM, relief="flat", font=FONT_SM,
                   padx=4, pady=0, cursor="hand2")

ENTRY_STYLE = dict(bg=BG_SURFACE, fg=FG_PRIMARY, insertbackground=FG_BLUE,
                   relief="flat", font=FONT_MD, highlightthickness=0)

SCROLLBAR = dict(bg=BG_SURFACE, troughcolor=BG_PRIMARY, highlightthickness=0, bd=0)

TEXT_READONLY = dict(bg=BG_PRIMARY, fg=FG_PRIMARY, relief="flat", font=FONT,
                     state="disabled", wrap="word", padx=6, pady=4,
                     highlightthickness=0, borderwidth=0)
