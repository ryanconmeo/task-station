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

The colour taxonomy (dot/tag/label) lives in CATEGORIES; the full per-category
palette (bg/fg/bold/cursor/sel + 16 ANSI) lives in THEMES and is supplied by the
ACTIVE theme. tint_escape emits the active theme's palette for a category as OSC
escapes that iTerm and Terminal.app both honor. Edit CATEGORIES for your own
taxonomy (keys are just identifiers); edit/override THEMES for colour.
"""

import copy
import os
import re
import sys as _sys

# Master switch for terminal tinting. Tags/labels always render; OFF just stops
# tint_escape from being emitted (see cmd_prompt_tint / cmd_session_tint).
TINT_TERMINAL = True

# key → {emoji dot, short [TAG], human label}
#
# The dot is SLOT-CANONICAL: each colour slot OWNS an emoji. You pick the colour;
# the colour determines the icon. A category override / new category therefore
# needs only `tag` + `label` — the dot is inherited from the slot automatically
# (an explicit `dot` is still allowed for power users). See `_apply_overrides` and
# SLOT_DOTS below, and CATEGORIES.md.
#
# COLOUR is NOT baked here. The taxonomy (dot/tag/label) is theme-INDEPENDENT; the
# full per-category palette (bg/fg/bold/cursor/sel + 16 ANSI colours) lives in
# THEMES and is supplied by the ACTIVE theme (see config.active_theme,
# effective_themes, tint_escape). The category key is the join: THEMES[theme][key].
CATEGORIES = {
    "red":    {"dot": "🔴", "tag": "BUG",      "label": "bug"},
    "orange": {"dot": "🟠", "tag": "REVIEW",   "label": "code review"},
    "yellow": {"dot": "🟡", "tag": "FIX",      "label": "fixing PR review feedback"},
    "green":  {"dot": "🟢", "tag": "FEATURE",  "label": "feature work"},
    "blue":   {"dot": "🔵", "tag": "INFRA",    "label": "CI/CD, pipelines, cloud, deploy"},
    "purple": {"dot": "🟣", "tag": "RESEARCH", "label": "spikes / investigation"},
    "black":  {"dot": "⚫", "tag": "GENERAL",  "label": "general"},
    "pink":   {"dot": "🩷", "tag": "PERSONAL", "label": "personal projects"},
    "white":  {"dot": "🎨", "tag": "DESIGN",   "label": "design"},
    "silver": {"dot": "🪩", "tag": "TOOLING",  "label": "dev/AI tooling, config, env"},
    "gold":   {"dot": "📖", "tag": "DOCS",     "label": "documentation, writing"},
    "brown":  {"dot": "🟤", "tag": "DATA",     "label": "databases, schemas, ETL, migrations"},
}
DEFAULT = "black"

# --- THEMES: appearance-aware, full-palette colour sets ----------------------
# A THEME has TWO VARIANTS — `dark` and `light` — each a full per-category palette
# (bg/fg/bold/cursor/sel + 16 ANSI). The OS appearance, or a forced
# `--tint-theme dark|light`, picks which variant renders:
# THEMES[theme][variant][category_key]. One theme ships: `sands` (display "Sands"),
# with a "Dark Sands" (muted) and a "Light Sands" (vibrant) variant. So out of the
# box the terminal follows the OS — dark mode → Dark Sands, light mode → Light Sands
# — re-resolved every prompt/attach (see resolve_variant). Users can override any
# field and add brand-new named themes via config.json (deep-merged, variant-nested,
# by effective_themes); config.active_theme() picks the theme (default `sands`).
# Variant DISPLAY labels follow "{Dark|Light} {ThemeDisplay}" (see variant_label);
# the variant KEYS stay dark/light (the appearance mapping).
#
# The 16-ANSI ramps are shared within a variant, named once and referenced —
# keeping every list exactly 16 elements. effective_themes() deep-copies before
# merging, so these shared lists are never mutated by an override.
_DARK_ANSI = ["#2a2c33", "#ef7a8b", "#9bd485", "#e6c178", "#7aa6ec", "#c79bef", "#79c9d6", "#dcd2c0",
              "#5a5650", "#ff93a3", "#b2e69c", "#f4d690", "#94bcff", "#d7b5fb", "#94dde9", "#f3ece0"]  # Dark Sands
_LIGHT_ANSI = ["#000000", "#ff7a64", "#4fd24a", "#e6d24a", "#5f7fff", "#ef6cef", "#3fd0dc", "#cbcccd",
               "#818383", "#ff9078", "#62ee52", "#f0f152", "#7e8eff", "#f96cf9", "#4ff0f0", "#e9ebeb"]  # Light Sands
_LIGHT_WHITE_ANSI = ["#575167", "#c83b53", "#5a8a3c", "#b07d1a", "#2f6fd0", "#9450c8", "#1f8a99", "#575167",
                     "#6f6982", "#d8455f", "#67992f", "#c08a00", "#3a7ce0", "#a45fd8", "#2799aa", "#3d3850"]  # Light Sands, white slot

THEMES = {
    "sands": {
        "dark": {   # "Dark Sands" — dark, muted
            "red":    {"bg": "#2c1518", "fg": "#dcd2c0", "bold": "#e0c060", "cursor": "#e0c060", "sel": "#235a52", "ansi": _DARK_ANSI},
            "orange": {"bg": "#34200d", "fg": "#dcd2c0", "bold": "#f0926e", "cursor": "#f0926e", "sel": "#20545e", "ansi": _DARK_ANSI},
            "yellow": {"bg": "#26220f", "fg": "#dcd2c0", "bold": "#ffb454", "cursor": "#ffb454", "sel": "#4a3270", "ansi": _DARK_ANSI},
            "green":  {"bg": "#1c2a16", "fg": "#dcd2c0", "bold": "#b6e85a", "cursor": "#b6e85a", "sel": "#6e2a4e", "ansi": _DARK_ANSI},
            "blue":   {"bg": "#141d2e", "fg": "#d6d8c8", "bold": "#5bc8f5", "cursor": "#5bc8f5", "sel": "#7a5816", "ansi": _DARK_ANSI},
            "purple": {"bg": "#1f1730", "fg": "#dcd2c0", "bold": "#d9b0f0", "cursor": "#d9b0f0", "sel": "#2f5a2a", "ansi": _DARK_ANSI},
            "black":  {"bg": "#121214", "fg": "#e6c55e", "bold": "#79c9d6", "cursor": "#79c9d6", "sel": "#2e4a5e", "ansi": _DARK_ANSI},
            "pink":   {"bg": "#2b0f1d", "fg": "#dcd2c0", "bold": "#ff6ab0", "cursor": "#ff6ab0", "sel": "#245a3e", "ansi": _DARK_ANSI},
            "white":  {"bg": "#5e5c5c", "fg": "#f7f9fc", "bold": "#ec7bbd", "cursor": "#ec7bbd", "sel": "#6e4a62", "ansi": _DARK_ANSI},
            "silver": {"bg": "#242b3c", "fg": "#dde1e8", "bold": "#e6c27a", "cursor": "#e6c27a", "sel": "#6e5418", "ansi": _DARK_ANSI},
            "gold":   {"bg": "#2a2210", "fg": "#dcd2c0", "bold": "#ffd24a", "cursor": "#ffd24a", "sel": "#2e3a6e", "ansi": _DARK_ANSI},
            "brown":  {"bg": "#241910", "fg": "#dcd2c0", "bold": "#f08a4a", "cursor": "#f08a4a", "sel": "#2a5048", "ansi": _DARK_ANSI},
        },
        "light": {  # "Light Sands" — vibrant
            "red":    {"bg": "#80232a", "fg": "#e8dcc0", "bold": "#ffd84a", "cursor": "#ffd84a", "sel": "#235a52", "ansi": _LIGHT_ANSI},
            "orange": {"bg": "#934606", "fg": "#ecdcc0", "bold": "#ff8f6b", "cursor": "#ff8f6b", "sel": "#20545e", "ansi": _LIGHT_ANSI},
            "yellow": {"bg": "#6a5c00", "fg": "#f0e4a8", "bold": "#ff9d3a", "cursor": "#ff9d3a", "sel": "#4a3270", "ansi": _LIGHT_ANSI},
            "green":  {"bg": "#233a2b", "fg": "#e6e2b8", "bold": "#d7f528", "cursor": "#d7f528", "sel": "#6e2a4e", "ansi": _LIGHT_ANSI},
            "blue":   {"bg": "#0d1b4b", "fg": "#d8dcc0", "bold": "#5bc8f5", "cursor": "#5bc8f5", "sel": "#7a5816", "ansi": _LIGHT_ANSI},
            "purple": {"bg": "#330056", "fg": "#e0d4b0", "bold": "#e9afff", "cursor": "#e9afff", "sel": "#2f5a2a", "ansi": _LIGHT_ANSI},
            "black":  {"bg": "#000000", "fg": "#e6c55e", "bold": "#5fd0dc", "cursor": "#5fd0dc", "sel": "#2e4a5e", "ansi": _LIGHT_ANSI},
            "pink":   {"bg": "#320b1b", "fg": "#f4db9b", "bold": "#ff40ac", "cursor": "#ff40ac", "sel": "#245a3e", "ansi": _LIGHT_ANSI},
            "white":  {"bg": "#f4f4f2", "fg": "#2d3840", "bold": "#a82d6a", "cursor": "#a82d6a", "sel": "#ecc4de", "ansi": _LIGHT_WHITE_ANSI},
            "silver": {"bg": "#2a3142", "fg": "#eef1f6", "bold": "#f0c27a", "cursor": "#f0c27a", "sel": "#6e5418", "ansi": _LIGHT_ANSI},
            "gold":   {"bg": "#4e3507", "fg": "#f4e4b0", "bold": "#ffdb00", "cursor": "#ffdb00", "sel": "#2e3a6e", "ansi": _LIGHT_ANSI},
            "brown":  {"bg": "#332a23", "fg": "#f4bf7f", "bold": "#ef7300", "cursor": "#ef7300", "sel": "#2a5048", "ansi": _LIGHT_ANSI},
        },
    },
}
DEFAULT_THEME = "sands"
VARIANTS = ("dark", "light")

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
                # Slot-determines-emoji: an override needs only {tag,label}. A
                # missing `dot` is inherited from the slot's shipped default; an
                # explicit `dot` still wins. Brand-new keys with no slot fall back to
                # the GENERAL dot. (Colour is no longer here — it lives in THEMES.)
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
    r"explain|tell me|difference between|did|did you|have you|has|had|"
    r"is there|are there|was|were|didn't|weren't|haven't)\b", re.I)
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


def _all_items():
    """(key, meta) pairs for EVERY slot, in canonical order."""
    return [(c, CATEGORIES[c]) for c in CATEGORIES]


def auto_categories_on():
    """Whether assigning a task to a disabled slot auto-enables it (config flag,
    default on). When on, the categoriser is shown the FULL taxonomy so it can pick
    the most accurate slot even if that slot isn't on the board yet."""
    try:
        import config as _config
        return _config.auto_categories_enabled()
    except Exception:
        return False


def legend(items=None):
    """Compact one-line legend. Defaults to the enabled categories; pass `items`
    (e.g. _all_items()) to legend the full taxonomy instead."""
    items = _enabled_items() if items is None else items
    parts = ["%s %s" % (tag(c), m["label"]) for c, m in items]
    return "Legend: " + "  ·  ".join(parts)


def compact_legend():
    """Minimal key=dot+TAG legend for the per-prompt hook (token-lean). Lists the
    FULL taxonomy when auto_categories is on (the categoriser may pick any slot and
    a fresh pick auto-enables it); otherwise only the enabled categories."""
    items = _all_items() if auto_categories_on() else _enabled_items()
    return " ".join("%s=%s%s" % (c, m["dot"], m["tag"]) for c, m in items)


def picker_lines():
    """Guidance lines for the UserPromptSubmit hook: how to choose a colour. When
    auto_categories is on, present the FULL 12-slot taxonomy and note that assigning
    a not-yet-shown slot enables it automatically; otherwise scope to enabled."""
    if auto_categories_on():
        return ["Pick the MOST ACCURATE category COLOR from the full taxonomy "
                "(see CATEGORIES.md) — a slot not yet on the board is enabled "
                "automatically the moment you assign it:",
                "  " + legend(_all_items())]
    return ["Pick a category COLOR for the task from its context (see CATEGORIES.md):",
            "  " + legend()]


def auto_enable(color):
    """When auto_categories is ON and `color` resolves to a real category NOT in the
    enabled set, persist it onto the enabled set (so it shows on the board/legend
    thereafter) and return a one-line notice; otherwise return None.

    Display follows assignment: the categoriser may pick ANY of the 12 slots, and
    the first time a task lands on a disabled slot the board grows to include it."""
    key = resolve(color)
    if not key:
        return None
    try:
        import config as _config
        if not _config.auto_categories_enabled():
            return None
        if key in enabled_keys():
            return None
        cur = list(enabled_keys())
        cur.append(key)
        keys = [k for k in CATEGORIES if k in set(cur)]   # canonical order
        _config.set_enabled_categories(keys)
    except Exception:
        return None
    m = CATEGORIES[key]
    return "enabled new category %s [%s]" % (m["dot"], m["tag"])


# --- Active-theme palette access ---------------------------------------------

def effective_themes():
    """The active theme registry: the shipped THEMES with user overrides and
    brand-new named themes from config.json `themes` deep-merged on top, VARIANT-
    NESTED (per theme → per variant (`dark`/`light`) → per category → per field).
    Returns a DEEP COPY, so callers may mutate freely and the shipped THEMES (and
    the shared ANSI ramps) are never touched."""
    base = copy.deepcopy(THEMES)
    try:
        import config as _config
        user = _config.get("themes")
        if isinstance(user, dict):
            for tname, tvars in user.items():
                if not isinstance(tvars, dict):
                    continue
                dst_theme = base.setdefault(tname, {})
                for variant, tcats in tvars.items():
                    if variant not in VARIANTS or not isinstance(tcats, dict):
                        continue
                    dst_var = dst_theme.setdefault(variant, {})
                    for ckey, fields in tcats.items():
                        if not isinstance(fields, dict):
                            continue
                        dst_var.setdefault(ckey, {}).update(fields)
    except Exception:
        return copy.deepcopy(THEMES)
    return base


def available_themes():
    """Theme names available to select (shipped + any user-defined), the shipped
    `sands` first, then user themes alphabetically."""
    names = list(effective_themes())
    return sorted(names, key=lambda n: (0 if n == DEFAULT_THEME else 1, n))


def theme_display(theme):
    """Human display name for a theme key — the key with its first letter upper-cased
    (`sands` → 'Sands', `ocean` → 'Ocean'). Used to build variant labels."""
    return (theme[:1].upper() + theme[1:]) if theme else theme


def variant_label(theme, variant):
    """Display label for a theme's variant: '{Dark|Light} {ThemeDisplay}' — e.g.
    ('sands','dark') → 'Dark Sands', ('sands','light') → 'Light Sands', ('ocean',
    'dark') → 'Dark Ocean'. The variant KEYS stay dark/light; only labels change."""
    return "%s %s" % (variant.capitalize(), theme_display(theme))


def tint_theme_setting():
    """The configured appearance control `tint_theme` ("auto" | "dark" | "light"),
    default "auto". "auto" follows the OS appearance (see resolve_variant)."""
    try:
        import config as _config
        val = _config.get("tint_theme", "auto")
    except Exception:
        return "auto"
    return val if val in ("auto", "dark", "light") else "auto"


def resolve_variant():
    """The effective appearance VARIANT: "dark" or "light". Never raises.

    A forced "dark"/"light" setting is returned as-is. "auto" detects the OS
    appearance: on macOS, `defaults read -g AppleInterfaceStyle` prints "Dark" in
    dark mode and errors (no such key) in light mode. Any non-macOS platform or any
    failure falls back to "dark" (so the shipped theme renders Dark Sands)."""
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


def theme_palette(theme, key, variant=None):
    """The palette dict for category `key` under `theme`'s `variant` (resolved from
    the appearance setting if None) in the effective registry. A theme that doesn't
    define the variant/category falls back to the shipped `default` theme's same
    variant. None when even the fallback is absent."""
    if variant is None:
        variant = resolve_variant()
    eff = effective_themes()
    t = eff.get(theme) if isinstance(eff.get(theme), dict) else {}
    tv = t.get(variant) if isinstance(t.get(variant), dict) else {}
    pal = tv.get(key)
    if pal is None:                                  # fall back to default's variant
        base = eff.get(DEFAULT_THEME, {}).get(variant, {})
        pal = base.get(key) if isinstance(base, dict) else None
    return pal


def tint_escape(color, mode, term):
    """The terminal escape string that tints the window to the ACTIVE theme's full
    palette for category `color` in the resolved VARIANT, or '' for a no-op. Zero-
    setup: standard OSC that iTerm AND Terminal.app both honor, plus one iTerm-only
    extra for the bold color.

      bg      OSC 11   \\033]11;<hex>\\007
      fg      OSC 10   \\033]10;<hex>\\007
      cursor  OSC 12   \\033]12;<hex>\\007
      ANSI n  OSC 4    \\033]4;<n>;<hex>\\007   for n in 0..15 (when 'ansi' present)
      sel     OSC 17   \\033]17;<hex>\\007      (when 'sel' present)
      bold    iTerm    \\033]1337;SetColors=bold=<hexNoHash>\\007  (skipped on Terminal.app)

    Resolution order: active theme (config.active_theme, default `default`) → variant
    (resolve_variant: the OS appearance, or a forced --tint-theme) → that variant's
    per-category palette (falling back to the `default` theme's variant). Only
    dot/tag/label still come from CATEGORIES. A palette that defines ONLY a bg still
    emits just the bg. `term == 'none'`, an unknown color, or a category with no
    palette at all yield ''. `mode` is accepted for back-compat and ignored."""
    if term == "none":
        return ""
    key = resolve(color)
    if not key:
        return ""
    try:
        import config as _config
        theme = _config.active_theme()
    except Exception:
        theme = DEFAULT_THEME
    pal = theme_palette(theme, key)
    if not pal or not pal.get("bg"):
        return ""
    parts = ["\033]11;%s\007" % pal["bg"]]
    if pal.get("fg"):
        parts.append("\033]10;%s\007" % pal["fg"])
    if pal.get("cursor"):
        parts.append("\033]12;%s\007" % pal["cursor"])
    ansi = pal.get("ansi")
    if isinstance(ansi, (list, tuple)):
        for n, ah in enumerate(ansi):
            parts.append("\033]4;%d;%s\007" % (n, ah))
    if pal.get("sel"):
        parts.append("\033]17;%s\007" % pal["sel"])
    if term == "iterm" and pal.get("bold"):
        parts.append("\033]1337;SetColors=bold=%s\007" % pal["bold"].lstrip("#"))
    return "".join(parts)
