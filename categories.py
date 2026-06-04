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

import re

# Turn OFF if you don't have <Color>-named Terminal profiles + matching zsh
# aliases. Tags/labels still render; only the `zsh -ic '<color>'` hints vanish.
TINT_TERMINAL = True

# key (== zsh alias name) → {emoji dot, short [TAG], human label}
CATEGORIES = {
    "red":    {"dot": "🔴", "tag": "BUG",       "label": "bug"},
    "orange": {"dot": "🟠", "tag": "REVIEW",    "label": "code review"},
    "yellow": {"dot": "🟡", "tag": "PERSONAL",  "label": "personal projects"},
    "green":  {"dot": "🟢", "tag": "VOLT",      "label": "coding for Volt"},
    "blue":   {"dot": "🔵", "tag": "DEVOPS",    "label": "devops"},
    "purple": {"dot": "🟣", "tag": "SPECIAL",   "label": "special"},
    "black":  {"dot": "⚫", "tag": "GENERAL",   "label": "general"},
    "pink":   {"dot": "🩷", "tag": "DESIGN",    "label": "design"},
    "white":  {"dot": "⚪", "tag": "SKILLS",    "label": "skills and memories"},
    "silver": {"dot": "🩶", "tag": "SILVER",    "label": "reserved"},
    "gold":   {"dot": "🟡", "tag": "GOLD",      "label": "reserved"},
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
    (r"fix-pr",                                                    "red"),     # fixing PR feedback / defects
    (r"review|security-review",                                    "orange"),  # PR / code review (incl. connx reviews)
    (r"^connxlandingzone:",                                        "brown"),   # ConnX building (non-review; reviews matched above stay orange)
    (r"story-runner",                                              "green"),   # Volt feature work
    (r"update-config|keybindings|permission|schedule|statusline|"
     r"\binit\b|claude-api|\bloop\b|deep-research|simplify|verify", "white"),   # Claude tooling
]

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


def normalize(color):
    """Map an arbitrary string to a known category key; fall back to DEFAULT."""
    c = (color or "").strip().lower()
    return c if c in CATEGORIES else DEFAULT


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


def picker_lines():
    """Guidance lines for the UserPromptSubmit hook: how to choose a colour."""
    lines = ["Pick a category COLOR for the task from its context (see CATEGORIES.md):",
             "  " + legend()]
    reserved = [c for c, m in CATEGORIES.items() if m["label"] == "reserved"]
    if reserved:
        lines.append("  (reserved / unassigned: " + ", ".join(reserved) + ")")
    return lines
