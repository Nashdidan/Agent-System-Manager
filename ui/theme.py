# ── Theme constants for the Agent System UI ──────────────────

# Base colors (Catppuccin Mocha)
BG_PRIMARY   = "#1e1e2e"
BG_SECONDARY = "#181825"
BG_SURFACE   = "#313244"
BG_HIGHLIGHT = "#2a2a3e"
BG_SASH      = "#313244"

FG_PRIMARY   = "#cdd6f4"
FG_DIM       = "#6c7086"
FG_BLUE      = "#89b4fa"
FG_GREEN     = "#a6e3a1"
FG_RED       = "#f38ba8"
FG_YELLOW    = "#f9e2af"

# Fonts
FONT_MONO_SM   = ("Consolas", 9)
FONT_MONO      = ("Consolas", 10)
FONT_MONO_MD   = ("Consolas", 11)
FONT_MONO_LG   = ("Consolas", 12)
FONT_MONO_BOLD = ("Consolas", 10, "bold")
FONT_MONO_MD_BOLD = ("Consolas", 11, "bold")
FONT_MONO_LG_BOLD = ("Consolas", 12, "bold")
FONT_MONO_ITALIC   = ("Consolas", 10, "italic")

# Common widget style kwargs
BTN_PRIMARY = dict(bg=FG_BLUE, fg=BG_PRIMARY, relief="flat", font=FONT_MONO_MD_BOLD, padx=14)
BTN_SUCCESS = dict(bg=FG_GREEN, fg=BG_PRIMARY, relief="flat", font=FONT_MONO_MD_BOLD, padx=14)
BTN_DANGER  = dict(bg=FG_RED, fg=BG_PRIMARY, relief="flat", font=FONT_MONO_MD_BOLD, padx=14)
BTN_MUTED   = dict(bg="#45475a", fg=FG_PRIMARY, relief="flat", font=FONT_MONO, padx=8)
BTN_SMALL   = dict(bg=BG_SURFACE, fg=FG_PRIMARY, relief="flat", font=FONT_MONO_SM, padx=4)

ENTRY_STYLE = dict(bg=BG_SURFACE, fg=FG_PRIMARY, insertbackground=FG_PRIMARY, relief="flat", font=FONT_MONO_LG)

SCROLLBAR = dict(bg=BG_SURFACE)

TEXT_READONLY = dict(bg=BG_SECONDARY, fg=FG_PRIMARY, relief="flat", font=FONT_MONO,
                     state="disabled", wrap="word", padx=8, pady=6)
