"""Optional category / colour plugin for Task Station.

If this module is importable, `task-station.py` tags each task with a category — an
emoji dot + `[TAG]` after the title, a legend under the list — and, when
TINT_TERMINAL is on, tints the terminal to the category's full Sands palette via
standard terminal escapes (zero-setup; no profiles or shell aliases needed). It
also maps skill (slash-command) invocations to a category so the UserPromptSubmit
hook can tint the terminal the instant a skill runs (see SKILL_COLORS /
color_for_prompt, tint_escape, and on_user_prompt.sh).

This file is the ONLY place the colour taxonomy and the terminal-tinting live.
`task-station.py` imports it defensively, so the tracker degrades gracefully:

  • Delete / rename this file        → a plain, colourless tracker.
  • Keep it, set TINT_TERMINAL=False → tags + labels, but no terminal tinting.

Each slot ships a full baked palette (see CATEGORIES); tint_escape emits it as
OSC escapes that iTerm and Terminal.app both honor. Edit CATEGORIES to your own
taxonomy — keys are just identifiers now (no alias/profile coupling).
"""

import os
import re
import sys as _sys

# Master switch for terminal tinting. Tags/labels always render; OFF just stops
# tint_escape from being emitted (see cmd_prompt_tint / cmd_session_tint).
TINT_TERMINAL = True

# key → {emoji dot, short [TAG], human label, + baked palette}
#
# The dot is SLOT-CANONICAL: each colour slot OWNS an emoji. You pick the colour;
# the colour determines the icon. A category override / new category therefore
# needs only `tag` + `label` — the dot (and the baked palette) are inherited from
# the slot automatically (an explicit `dot` is still allowed for power users). See
# `_apply_overrides` and SLOT_DOTS below, and CATEGORIES.md.
#
# Palette by slot: the per-slot palette is kept BY SLOT through the white↔silver
# category swap — the `white` slot keeps White Sands (now home to 🎨 DESIGN) and
# `silver` keeps Silver Sands (now home to 🪩 TOOLING). Only the dot/tag/label
# move between the two slots; each category thus lands on the intended palette.
# Each slot bakes in a full "Sands" palette (background/foreground/bold/cursor +
# 16 ANSI colors + selection), emitted as terminal escapes by tint_escape. The
# look is theme-INDEPENDENT: `hex` and `hex_light` are the same value, so the
# Sands background applies in both OS appearances. A user override that sets only
# {tag,label} still inherits the full palette from its slot (see _apply_overrides).
CATEGORIES = {
    "red": {
        "dot": "🔴", "tag": "BUG", "label": "bug",
        "hex": "#7a251e", "hex_light": "#7a251e",
        "fg": "#d7c9a7", "bold": "#dfbd22", "cursor": "#ffffff", "selbg": "#5ac39d",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "orange": {
        "dot": "🟠", "tag": "REVIEW", "label": "code review",
        "hex": "#753300", "hex_light": "#753300",
        "fg": "#ffaf1f", "bold": "#ffd63a", "cursor": "#ffffff", "selbg": "#5ac396",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "yellow": {
        "dot": "🟡", "tag": "FIX", "label": "fixing PR review feedback",
        "hex": "#564e00", "hex_light": "#564e00",
        "fg": "#fff127", "bold": "#ffff4e", "cursor": "#ffffff", "selbg": "#5ac3b5",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "green": {
        "dot": "🟢", "tag": "FEATURE", "label": "feature work",
        "hex": "#2e381a", "hex_light": "#2e381a",
        "fg": "#f3e2b2", "bold": "#d7f528", "cursor": "#ffffff", "selbg": "#5ac3a0",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "blue": {
        "dot": "🔵", "tag": "INFRA", "label": "CI/CD, pipelines, cloud, deploy",
        "hex": "#0d1b4b", "hex_light": "#0d1b4b",
        "fg": "#c0d8f0", "bold": "#5bc8f5", "cursor": "#ffffff", "selbg": "#c35a96",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "purple": {
        "dot": "🟣", "tag": "RESEARCH", "label": "spikes / investigation",
        "hex": "#330056", "hex_light": "#330056",
        "fg": "#ca75ff", "bold": "#e9afff", "cursor": "#ffffff", "selbg": "#c3945a",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "black": {
        "dot": "⚫", "tag": "GENERAL", "label": "general",
        "hex": "#000000", "hex_light": "#000000",
        "fg": "#e6c55e", "bold": "#00d6e2", "cursor": "#ffffff", "selbg": "#5ac3a3",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "pink": {
        "dot": "🩷", "tag": "PERSONAL", "label": "personal projects",
        "hex": "#320b1b", "hex_light": "#320b1b",
        "fg": "#f4db9b", "bold": "#ff40ac", "cursor": "#ffffff", "selbg": "#5ac39f",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "white": {
        "dot": "🎨", "tag": "DESIGN", "label": "design",
        "hex": "#ffffff", "hex_light": "#ffffff",
        "fg": "#2d3840", "bold": "#a82d6a", "cursor": "#b3377b", "selbg": "#c35a9f",
        "ansi": ["#2d3840", "#b45648", "#6caa71", "#c4ac62", "#5685a8", "#ad64be", "#69c6c9", "#c1c8cc", "#506573", "#df6c5a", "#79be7e", "#e5c872", "#49a2e1", "#d389e5", "#77e1e5", "#d8e1e7"],
    },
    "silver": {
        "dot": "🪩", "tag": "TOOLING", "label": "dev/AI tooling, config, env",
        "hex": "#191d27", "hex_light": "#191d27",
        "fg": "#e0e0e0", "bold": "#ea7ba5", "cursor": "#e6709e", "selbg": "#c35a7f",
        "ansi": ["#35424c", "#b45648", "#6caa71", "#c4ac62", "#6d96b4", "#bd7bcd", "#7ccbcd", "#dee5eb", "#465c6d", "#df6c5a", "#79be7e", "#e5c872", "#67b5ed", "#d389e5", "#84dde0", "#e5eff5"],
    },
    "gold": {
        "dot": "📖", "tag": "DOCS", "label": "documentation, writing",
        "hex": "#4e3507", "hex_light": "#4e3507",
        "fg": "#f8eaa5", "bold": "#ffdb00", "cursor": "#ffffff", "selbg": "#5ac3aa",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
    "brown": {
        "dot": "🟤", "tag": "DATA", "label": "databases, schemas, ETL, migrations",
        "hex": "#22140c", "hex_light": "#22140c",
        "fg": "#f4bf7f", "bold": "#ef7300", "cursor": "#ffffff", "selbg": "#5ac38d",
        "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"],
    },
}
DEFAULT = "black"

# The canonical per-slot emoji, captured from the shipped defaults BEFORE any user
# override mutates CATEGORIES — this is the source of truth an override inherits
# from when it omits `dot` (see _apply_overrides).
SLOT_DOTS = {key: meta["dot"] for key, meta in CATEGORIES.items()}

# --- Active (enabled) categories ---------------------------------------------
# A lean, growable set of "on" slots, persisted in config.json as
# `enabled_categories`. Unconfigured ⇒ CORE only (the board starts small and
# grows: auto_categories auto-enables a slot the first time a task is assigned to
# it — see auto_enable). ⚫ GENERAL is PERMANENT: always enabled, never
# disable-able. CORE (the seeded lean default, removable except GENERAL):
# BUG · FEATURE · GENERAL.
PERMANENT = "black"
CORE = ("red", "green", "black")  # BUG · FEATURE · GENERAL

_TAG_WIDTH = max(len(m["tag"]) for m in CATEGORIES.values()) + 2  # +2 for "[]"

# Skill (slash-command) → category, applied IMMEDIATELY on prompt-submit so the
# terminal tints the moment a skill runs — no waiting for Claude to decide.
# Each entry is (regex, colour); the regex is `search`ed (case-insensitive)
# against the invoked command name WITH any "plugin:" prefix kept, e.g.
# "myplugin:review-pr" or "myplugin:build-feature". First match
# wins; an unmatched skill (or a plain typed prompt) tints nothing. Edit freely.
SKILL_COLORS = [
    (r"fix-pr",                                                    "yellow"),  # fixing PR review feedback
    (r"review|security-review",                                    "orange"),  # PR / code review
    (r"update-config|keybindings|permission|schedule|statusline|"
     r"\binit\b|claude-api|\bloop\b|deep-research|simplify|verify", "silver"),  # Claude tooling skills → TOOLING (silver slot)
]

def _apply_overrides():
    """Merge user overrides from config.json over the shipped defaults,
    so customizations survive `/plugin update`. Any absent/invalid/malformed config
    leaves the shipped defaults entirely unchanged (never crashes module import)."""
    global TINT_TERMINAL, SKILL_COLORS, _TAG_WIDTH
    import config as _config
    cat_snapshot = dict(CATEGORIES)
    tint_snapshot, skill_snapshot, width_snapshot = TINT_TERMINAL, list(SKILL_COLORS), _TAG_WIDTH
    try:
        cats = _config.get("categories")
        if isinstance(cats, dict):
            for key, meta in cats.items():
                # Slot-determines-emoji: an override needs only {tag,label}. Missing
                # fields (dot, hex, hex_light) are inherited from the slot's shipped
                # default; an explicit `dot` still wins. Brand-new keys with no slot
                # fall back to the GENERAL dot.
                if isinstance(meta, dict) and {"tag", "label"} <= set(meta):
                    merged = dict(CATEGORIES.get(key, {}))
                    merged.update(meta)
                    merged.setdefault("dot", SLOT_DOTS.get(key, CATEGORIES[DEFAULT]["dot"]))
                    CATEGORIES[key] = merged
        if _config.get("tint_terminal") is not None:
            TINT_TERMINAL = bool(_config.get("tint_terminal"))
        sc = _config.get("skill_colors")
        if isinstance(sc, list):
            # Accept ONLY well-formed 2-element [pattern, color] string pairs;
            # silently skip anything malformed. A bad entry must never reach
            # color_for_prompt's `for pat, color in SKILL_COLORS` unpack (that
            # would raise ValueError outside this import-time guard) — nor should
            # one bad entry discard the good ones.
            valid = [(x[0], x[1]) for x in sc
                     if isinstance(x, (list, tuple)) and len(x) == 2
                     and isinstance(x[0], str) and isinstance(x[1], str)]
            SKILL_COLORS = valid + SKILL_COLORS
        _TAG_WIDTH = max(len(m["tag"]) for m in CATEGORIES.values()) + 2
    except Exception:
        CATEGORIES.clear(); CATEGORIES.update(cat_snapshot)
        TINT_TERMINAL, SKILL_COLORS, _TAG_WIDTH = tint_snapshot, skill_snapshot, width_snapshot


_apply_overrides()

_CMD_RE = re.compile(r"<command-name>\s*/?\s*([^<\s]+)", re.I)


def command_name(prompt):
    """The invoked slash-command / skill name (sans leading slash), or None.

    Slash commands reach the UserPromptSubmit hook wrapped as
    `<command-name>/myplugin:review-pr</command-name>`; a hand-typed prompt
    that simply starts with `/foo` is also recognised. Anything else → None."""
    if not prompt:
        return None
    m = _CMD_RE.search(prompt)
    if m:
        return m.group(1).strip().lstrip("/") or None
    s = prompt.strip()
    if s.startswith("/") and len(s) > 1:
        return s[1:].split()[0]
    return None


def color_for_prompt(prompt):
    """Category colour for a skill-invocation prompt, or None when the prompt
    invokes no skill / no SKILL_COLORS pattern matches. Used by the hook to tint
    the terminal the instant a skill runs."""
    name = command_name(prompt)
    if not name:
        return None
    for pat, color in SKILL_COLORS:
        if re.search(pat, name, re.I):
            return normalize(color)
    return None


# --- Explicit "create/attach a task" intent in a free-typed prompt -----------
# When the user literally asks to make/attach a task, the prompt-context hook
# must hard-steer to task-station and away from the built-in/native TaskCreate
# (ephemeral session-todo) tool. These run once per prompt, like _CMD_RE.

# Same-clause guards: a question ABOUT the concept, or a negation, before the
# matched verb means it is NOT an imperative → no intent.
_INTENT_QUESTION_RE = re.compile(
    r"\b(what's|whats|what|how|why|does|do you|can you|should|could|when|"
    r"explain|tell me|difference between)\b", re.I)
_INTENT_NEGATION_RE = re.compile(
    r"\b(don't|dont|do not|no need to|without|never|stop|instead of)\b", re.I)

# attach — more specific ("to … task" shape); checked BEFORE create.
ATTACH_INTENT_RES = [
    re.compile(r"\battach\b(\s+(this|it|me|us))?\s+to\s+(a|the|task)\b", re.I),
    re.compile(r"\battach\s+to\s+(a\s+|the\s+)?task\b", re.I),
    re.compile(r"\badd\s+(this|it|that)\s+to\s+(the\s+)?(existing\s+)?task\b", re.I),
    re.compile(r"\blink\s+(this\s+|it\s+|that\s+)?to\s+(a\s+|the\s+)?(existing\s+)?task\b", re.I),
    re.compile(r"\bassociate\s+(this\s+|it\s+|that\s+)?with\s+(the\s+)?task\b", re.I),
]

# create — imperative to make a NEW task.
CREATE_INTENT_RES = [
    re.compile(r"\bmake\s+(this|it|that|them|a)?\s*((in)?to\s+)?(a\s+)?(new\s+)?task\b", re.I),
    re.compile(r"\bcreate\s+(a\s+|this\s+|it\s+|that\s+)?(as\s+)?(a\s+)?(new\s+)?task\b", re.I),
    re.compile(r"\badd\s+a\s+(new\s+)?task\b", re.I),
    re.compile(r"\bstart\s+a\s+(new\s+)?task\b", re.I),
    re.compile(r"\bopen\s+a\s+(new\s+)?task\b", re.I),
    re.compile(r"\bnew\s+task\b", re.I),
    re.compile(r"\btrack\s+(this|it|that)(\s+as\s+a\s+task)?\b", re.I),
    re.compile(r"\bsave\s+(this|it|that)\s+as\s+a\s+task\b", re.I),
    re.compile(r"\blog\s+(this|it|that)\s+as\s+a\s+task\b", re.I),
]


def task_intent(prompt):
    """Return 'create' | 'attach' | None for prompts that EXPLICITLY ask to
    create or attach a task. Conservative: only fires on clear imperative
    phrasing, not on questions about the concept or negations."""
    if not prompt:
        return None
    for intent, regexes in (("attach", ATTACH_INTENT_RES), ("create", CREATE_INTENT_RES)):
        for rx in regexes:
            m = rx.search(prompt)
            if not m:
                continue
            # Same-clause guard: only the text since the last clause boundary
            # (so "what does create a task do?" / "don't make a task" → None).
            clause = re.split(r"[.;\n?!]", prompt[:m.start()])[-1]
            if _INTENT_QUESTION_RE.search(clause) or _INTENT_NEGATION_RE.search(clause):
                return None
            return intent
    return None


def _build_aliases():
    """Reverse lookup so a category can be named by key, emoji dot, [TAG]/TAG,
    or human label — whatever the caller copied out of the legend/picker.

    Exact keys are registered first so they can never be shadowed; everything
    else uses setdefault so the first (primary) category wins any token shared
    between two slots."""
    m = {}
    for key in CATEGORIES:
        m[key] = key
    for key, meta in CATEGORIES.items():
        m.setdefault(meta["dot"], key)
        m.setdefault(meta["tag"].lower(), key)
        m.setdefault("[%s]" % meta["tag"].lower(), key)
        m.setdefault(meta["label"].lower(), key)
    return m


_ALIASES = _build_aliases()


def resolve(color):
    """Resolve a key / emoji dot / [TAG] / label to a known category key, or
    None if the input matches no category. Case-insensitive."""
    c = (color or "").strip().lower()
    return _ALIASES.get(c) if c else None


def is_known(color):
    """True when `color` names a real category (vs. the DEFAULT fallback).

    Lets callers tell "the user explicitly chose general" from "the user typed
    something we didn't understand" — the latter must not silently become black."""
    return resolve(color) is not None


def all_keys():
    """Every defined slot key, in canonical order (enabled or not)."""
    return list(CATEGORIES)


def enabled_keys():
    """The active category keys, in canonical CATEGORIES order.

    Reads `enabled_categories` from config live (no module reload needed). An
    absent/empty/invalid value ⇒ CORE only (the lean default — the board starts
    small and grows via auto_enable). ⚫ GENERAL (black) is PERMANENT — always
    present even if config omits it."""
    raw = None
    try:
        import config as _config
        raw = _config.get("enabled_categories")
    except Exception:
        raw = None
    if isinstance(raw, list) and raw:
        sel = {k for k in raw if k in CATEGORIES}
    else:
        sel = set(CORE)
    sel.add(PERMANENT)
    return [k for k in CATEGORIES if k in sel]


def is_enabled(color):
    """True when `color` resolves to a currently-enabled category."""
    key = resolve(color)
    return bool(key) and key in enabled_keys()


def normalize(color):
    """Map a category key, emoji dot, [TAG], or label to a known category key;
    fall back to DEFAULT for anything unrecognized."""
    return resolve(color) or DEFAULT


def label(color):
    return CATEGORIES[normalize(color)]["label"]


def tag(color, pad=False):
    """`<emoji> [TAG]` for a category. The emoji conveys the colour; the
    bracketed tag names it. When `pad`, the bracketed tag is right-padded to a
    fixed width so columns after it line up despite the tag's varying length."""
    meta = CATEGORIES[normalize(color)]
    t = "[%s]" % meta["tag"]
    if pad:
        t = t.ljust(_TAG_WIDTH)
    return "%s %s" % (meta["dot"], t)


def summary(color):
    """One-line description: `Category: 🔴 [BUG] bug (red)`."""
    c = normalize(color)
    return "Category: %s %s (%s)" % (tag(c), label(c), c)


def _enabled_items():
    """(key, meta) pairs for the enabled slots, in canonical order."""
    ek = set(enabled_keys())
    return [(c, CATEGORIES[c]) for c in CATEGORIES if c in ek]


def legend():
    """Compact one-line legend of the enabled categories."""
    parts = ["%s %s" % (tag(c), m["label"]) for c, m in _enabled_items()]
    return "Legend: " + "  ·  ".join(parts)


def compact_legend():
    """Minimal key=dot+TAG legend for the per-prompt hook (token-lean).
    Only enabled categories appear."""
    return " ".join("%s=%s%s" % (c, m["dot"], m["tag"])
                    for c, m in _enabled_items())


def picker_lines():
    """Guidance lines for the UserPromptSubmit hook: how to choose a colour.
    Scoped to the enabled categories only."""
    return ["Pick a category COLOR for the task from its context (see CATEGORIES.md):",
            "  " + legend()]


def _hex_of(color):
    key = resolve(color)  # None if unrecognized (no DEFAULT fallback)
    m = CATEGORIES.get(key) if key else None
    return m.get("hex") if m else None


def tint_theme_setting():
    """The configured `tint_theme` ("auto" | "dark" | "light"), default "auto".
    "auto" means "follow the OS appearance" (see resolve_theme)."""
    try:
        import config as _config
        val = _config.get("tint_theme", "auto")
    except Exception:
        return "auto"
    return val if val in ("auto", "dark", "light") else "auto"


def resolve_theme():
    """Resolve the effective palette: "dark" or "light". Never raises.

    A manual "dark"/"light" setting is returned as-is (no detection). "auto"
    detects the OS appearance: on macOS, `defaults read -g AppleInterfaceStyle`
    prints "Dark" in dark mode and errors (no such key) in light mode. Any
    non-macOS platform or any failure falls back to "dark" — today's behaviour."""
    setting = tint_theme_setting()
    if setting in ("dark", "light"):
        return setting
    if _sys.platform != "darwin":
        return "dark"
    try:
        import subprocess
        out = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True, timeout=2,
        )
        return "dark" if out.stdout.strip() == "Dark" else "light"
    except Exception:
        return "dark"


def hex_for(color, theme=None):
    """The tint hex for `color` under the given theme (resolved if None).
    Returns the light palette when theme is "light" and the slot defines
    `hex_light`, else the dark `hex` — so user overrides that only set `hex`
    still work. None when the colour is unrecognized."""
    key = resolve(color)  # None if unrecognized (no DEFAULT fallback)
    m = CATEGORIES.get(key) if key else None
    if not m:
        return None
    if theme is None:
        theme = resolve_theme()
    if theme == "light" and m.get("hex_light"):
        return m["hex_light"]
    return m.get("hex")


def tint_escape(color, mode, term):
    """The terminal escape string that tints the window to `color`'s full Sands
    palette, or '' for a no-op. Zero-setup: standard OSC so iTerm AND Terminal.app
    both honor most of it, plus one iTerm-only extra for the bold color.

      bg      OSC 11   \\033]11;<hex>\\007
      fg      OSC 10   \\033]10;<hex>\\007
      cursor  OSC 12   \\033]12;<hex>\\007
      ANSI n  OSC 4    \\033]4;<n>;<hex>\\007   for n in 0..15 (when 'ansi' present)
      selbg   OSC 17   \\033]17;<hex>\\007      (when 'selbg' present)
      bold    iTerm    \\033]1337;SetColors=bold=<hexNoHash>\\007  (skipped on Terminal.app)

    A slot that defines ONLY a bg (no fg/bold/ansi) still emits just the bg, so a
    minimal public taxonomy keeps working. `term == 'none'` or an unknown color
    yields ''. `mode` is accepted for back-compat (profile mode was removed) and
    ignored — tinting is always the direct escape now."""
    if term == "none":
        return ""
    key = resolve(color)
    m = CATEGORIES.get(key) if key else None
    if not m:
        return ""
    bg = hex_for(color)  # theme-aware, but Sands is theme-independent (hex==hex_light)
    if not bg:
        return ""
    parts = ["\033]11;%s\007" % bg]
    if m.get("fg"):
        parts.append("\033]10;%s\007" % m["fg"])
    if m.get("cursor"):
        parts.append("\033]12;%s\007" % m["cursor"])
    ansi = m.get("ansi")
    if isinstance(ansi, (list, tuple)):
        for n, ah in enumerate(ansi):
            parts.append("\033]4;%d;%s\007" % (n, ah))
    if m.get("selbg"):
        parts.append("\033]17;%s\007" % m["selbg"])
    if term == "iterm" and m.get("bold"):
        parts.append("\033]1337;SetColors=bold=%s\007" % m["bold"].lstrip("#"))
    return "".join(parts)
