"""Optional category / colour plugin for claude-todo.

If this module is importable, `todo.py` tags each task with a category — an
emoji dot + `[TAG]` after the title, a legend under the list — and, when
TINT_TERMINAL is on, suggests a zsh alias that tints the terminal to match.
It also maps skill (slash-command) invocations to a category so the
UserPromptSubmit hook can tint the terminal the instant a skill runs (see
SKILL_COLORS / color_for_prompt and on_user_prompt.sh).

This file is the ONLY place the colour taxonomy and the terminal-tinting live.
`todo.py` imports it defensively, so the tracker degrades gracefully:

  • Delete / rename this file        → a plain, colourless tracker.
  • Keep it, set TINT_TERMINAL=False → tags + labels, but no tint suggestions
                                        (for anyone who lacks the zsh aliases).

The colour KEYS double as zsh alias names: `zsh -ic '<key>'` is expected to
switch the Terminal.app profile (the author's aliases map e.g. `green` →
`Green Sands`). Edit CATEGORIES to your own taxonomy; the keys only need to be
valid alias names if you leave TINT_TERMINAL on.
"""

import os
import re
import json as _json
import sys as _sys
import paths as _paths

# Turn OFF if you don't have <Color>-named Terminal profiles + matching zsh
# aliases. Tags/labels still render; only the `zsh -ic '<color>'` hints vanish.
TINT_TERMINAL = True

# key (== zsh alias name) → {emoji dot, short [TAG], human label}
CATEGORIES = {
    "red":    {"dot": "🔴", "tag": "BUG",       "label": "bug"},
    "orange": {"dot": "🟠", "tag": "REVIEW",    "label": "code review"},
    "yellow": {"dot": "🟡", "tag": "YELLOW",    "label": "reserved"},
    "green":  {"dot": "🟢", "tag": "VOLT",      "label": "coding for Volt"},
    "blue":   {"dot": "🔵", "tag": "DEVOPS",    "label": "devops"},
    "purple": {"dot": "🟣", "tag": "SPECIAL",   "label": "special"},
    "black":  {"dot": "⚫", "tag": "GENERAL",   "label": "general"},
    "pink":   {"dot": "🩷", "tag": "DESIGN",    "label": "design"},
    "white":  {"dot": "⚪", "tag": "SKILLS",    "label": "skills and memories"},
    "silver": {"dot": "🩶", "tag": "PERSONAL",  "label": "personal projects"},
    "gold":   {"dot": "🟨", "tag": "FIX PR",    "label": "fixing PR review feedback"},
    "brown":  {"dot": "🟤", "tag": "MIGRATION", "label": "ConnX & legacy migration for Volt"},
}
DEFAULT = "black"
_TAG_WIDTH = max(len(m["tag"]) for m in CATEGORIES.values()) + 2  # +2 for "[]"

# Skill (slash-command) → category, applied IMMEDIATELY on prompt-submit so the
# terminal tints the moment a skill runs — no waiting for Claude to decide.
# Each entry is (regex, colour); the regex is `search`ed (case-insensitive)
# against the invoked command name WITH any "plugin:" prefix kept, e.g.
# "volt:review-pr-auto" or "connxlandingzone:review-branch-lite". First match
# wins; an unmatched skill (or a plain typed prompt) tints nothing. Edit freely.
SKILL_COLORS = [
    (r"fix-pr",                                                    "gold"),    # fixing PR review feedback
    (r"review|security-review",                                    "orange"),  # PR / code review (incl. connx reviews)
    (r"^connxlandingzone:",                                        "brown"),   # ConnX building (non-review; reviews matched above stay orange)
    (r"story-runner",                                              "green"),   # Volt feature work
    (r"update-config|keybindings|permission|schedule|statusline|"
     r"\binit\b|claude-api|\bloop\b|deep-research|simplify|verify", "white"),   # Claude tooling
]

def _apply_overrides():
    """Merge user overrides from <data_dir>/categories.json over the shipped defaults,
    so customizations survive `/plugin update`. Any absent/invalid/malformed config
    leaves the shipped defaults entirely unchanged (never crashes module import)."""
    global TINT_TERMINAL, SKILL_COLORS, _TAG_WIDTH
    cfg = os.path.join(_paths.data_dir(), "categories.json")
    if not os.path.isfile(cfg):
        return
    # snapshot so a malformed override can't corrupt the shipped defaults
    cat_snapshot = dict(CATEGORIES)
    tint_snapshot, skill_snapshot, width_snapshot = TINT_TERMINAL, list(SKILL_COLORS), _TAG_WIDTH
    try:
        with open(cfg) as f:
            data = _json.load(f)
        if isinstance(data.get("categories"), dict):
            for key, meta in data["categories"].items():
                # only accept well-formed entries; silently skip the rest
                if isinstance(meta, dict) and {"dot", "tag", "label"} <= set(meta):
                    CATEGORIES[key] = meta
        if "tint_terminal" in data:
            TINT_TERMINAL = bool(data["tint_terminal"])
        if isinstance(data.get("skill_colors"), list):
            SKILL_COLORS = [tuple(x) for x in data["skill_colors"]] + SKILL_COLORS
        _TAG_WIDTH = max(len(m["tag"]) for m in CATEGORIES.values()) + 2
    except Exception:
        # any unexpected malformation → restore shipped defaults entirely
        CATEGORIES.clear(); CATEGORIES.update(cat_snapshot)
        TINT_TERMINAL, SKILL_COLORS, _TAG_WIDTH = tint_snapshot, skill_snapshot, width_snapshot


_apply_overrides()

_CMD_RE = re.compile(r"<command-name>\s*/?\s*([^<\s]+)", re.I)


def command_name(prompt):
    """The invoked slash-command / skill name (sans leading slash), or None.

    Slash commands reach the UserPromptSubmit hook wrapped as
    `<command-name>/volt:review-pr-auto</command-name>`; a hand-typed prompt
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


def _build_aliases():
    """Reverse lookup so a category can be named by key, emoji dot, [TAG]/TAG,
    or human label — whatever the caller copied out of the legend/picker.

    Exact keys are registered first so they can never be shadowed; everything
    else uses setdefault so the first (primary) category wins a shared token
    (e.g. the 🟡 dot and the "reserved" label, which two entries each carry)."""
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


def tint_command(color):
    """The shell command that tints the terminal to `color`, or None when
    terminal tinting is disabled."""
    return "zsh -ic '%s'" % normalize(color) if TINT_TERMINAL else None


def legend():
    """Compact one-line legend of the assigned (non-reserved) categories."""
    parts = ["%s %s" % (tag(c), m["label"])
             for c, m in CATEGORIES.items() if m["label"] != "reserved"]
    return "Legend: " + "  ·  ".join(parts)


def compact_legend():
    """Minimal key=dot+TAG legend for the per-prompt hook (token-lean)."""
    return " ".join("%s=%s%s" % (c, m["dot"], m["tag"])
                    for c, m in CATEGORIES.items() if m["label"] != "reserved")


def picker_lines():
    """Guidance lines for the UserPromptSubmit hook: how to choose a colour."""
    lines = ["Pick a category COLOR for the task from its context (see CATEGORIES.md):",
             "  " + legend()]
    reserved = [c for c, m in CATEGORIES.items() if m["label"] == "reserved"]
    if reserved:
        lines.append("  (reserved / unassigned: " + ", ".join(reserved) + ")")
    return lines
