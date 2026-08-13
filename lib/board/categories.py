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
import json
import os
import re
import sys as _sys
import term as _term

# Master switch for terminal tinting. Tags/labels always render; OFF just stops
# tint_escape from being emitted (see cmd_prompt_tint / cmd_session_tint).
TINT_TERMINAL = True

# --- Category PACKS: swappable discipline taxonomies (task #444) --------------
# A PACK is a named set of up to 12 category slots that REUSE the same colour /
# palette machinery: every slot keys off one of the 12 canonical colour slots (so
# THEMES supplies its palette and the dot is slot-canonical), and the pack only
# renames the dot/tag/label/guide per slot. The DEV pack below is the original
# software-engineering taxonomy — the DEFAULT, so an unconfigured install is
# byte-for-byte identical to before packs existed. Other shipped packs (finance,
# hr, exec, general) retarget the same slots for a different discipline. Select the
# active pack with `config --category-pack <name>`; org-supplied packs merge in from
# a `category_packs.json` in the data dir (see effective_packs / CATEGORIES.md).
#
# COLOUR is NOT baked into a slot. The taxonomy (dot/tag/label) is theme-INDEPENDENT;
# the full per-category palette (bg/fg/bold/cursor/sel + 16 ANSI colours) lives in
# THEMES and is supplied by the ACTIVE theme (see config.active_theme,
# effective_themes, tint_escape). The colour key is the join: THEMES[theme][key].
#
# The dot is SLOT-CANONICAL: each colour slot OWNS an emoji (see _CANONICAL_DOTS).
# A pack slot / override therefore needs only `tag` + `label` — the dot is inherited
# from the slot automatically (an explicit `dot` is still allowed). See
# `_apply_overrides`, SLOT_DOTS, and CATEGORIES.md.
#
# ⚫ GENERAL (the `black` slot) is PERMANENT — every pack MUST define it, and it is
# always the DEFAULT / catch-all slot.

# The canonical per-slot emoji dot — the source of truth a pack slot (or an override)
# inherits when it omits `dot`. Tied to the colour slot and shared across packs, so
# the dot always matches the slot's palette colour.
_CANONICAL_DOTS = {
    "red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢",
    "blue": "🔵", "purple": "🟣", "black": "⚫", "pink": "🩷",
    "white": "🎨", "silver": "🪩", "gold": "📖", "brown": "🟤",
}

# Each pack: display `label`, one-line `description`, the lean `core` seed (the
# enabled set for a fresh board — GENERAL is always forced in), and `slots` mapping
# a canonical colour key → {tag, label, guide} (dot inherited from _CANONICAL_DOTS).
# Slot ORDER within a pack is the board / legend order.
PACKS = {
    "dev": {
        "label": "Developer",
        "description": "Software-engineering taxonomy (the default).",
        "core": ("red", "green", "black"),
        "slots": {
            "red":    {"tag": "BUG",      "label": "bug",
                       "guide": "Fixing a defect / broken behaviour."},
            "orange": {"tag": "REVIEW",   "label": "code review",
                       "guide": "Reviewing someone else's code or a PR."},
            "yellow": {"tag": "FIX",      "label": "fixing PR review feedback",
                       "guide": "Addressing PR review feedback on your own PR."},
            "green":  {"tag": "FEATURE",  "label": "feature work",
                       "guide": "Feature / product coding."},
            "blue":   {"tag": "INFRA",    "label": "CI/CD, pipelines, cloud, deploy",
                       "guide": "CI/CD, pipelines, cloud, deploys, DNS, environment setup."},
            "purple": {"tag": "RESEARCH", "label": "spikes / investigation",
                       "guide": "Spikes / investigation: research, prototypes, one-off exploration."},
            "black":  {"tag": "GENERAL",  "label": "general",
                       "guide": "General / catch-all when nothing else fits (the permanent default)."},
            "pink":   {"tag": "PERSONAL", "label": "personal projects",
                       "guide": "Personal projects / side work."},
            "white":  {"tag": "DESIGN",   "label": "design",
                       "guide": "UI/UX, theming, layout, visual design."},
            "silver": {"tag": "TOOLING",  "label": "dev/AI tooling, config, env",
                       "guide": "Dev/AI tooling, config & env: skills, slash commands, hooks, memory, this task-station."},
            "gold":   {"tag": "DOCS",     "label": "documentation, writing",
                       "guide": "Documentation & writing: READMEs, guides, changelogs."},
            "brown":  {"tag": "DATA",     "label": "databases, schemas, ETL, migrations",
                       "guide": "Data work: databases, schemas, queries, SQL, ETL, and data migrations."},
        },
    },
    "finance": {
        "label": "Finance",
        "description": "Accounting & finance-ops taxonomy.",
        "core": ("red", "green", "black"),
        "slots": {
            "red":    {"tag": "CLOSE",     "label": "period-end close",
                       "guide": "Period-end close: closing the books, journal entries, accruals."},
            "orange": {"tag": "AUDIT",     "label": "audit & controls",
                       "guide": "Audit prep, internal controls, SOX, reviewer requests."},
            "green":  {"tag": "REPORTING", "label": "financial reporting",
                       "guide": "Financial statements & reporting: P&L, balance sheet, management reports."},
            "gold":   {"tag": "BUDGET",    "label": "budgeting & forecasting",
                       "guide": "Budgets, forecasts, variance analysis, planning."},
            "purple": {"tag": "TAX",       "label": "tax",
                       "guide": "Tax filings, provisions, compliance, planning."},
            "brown":  {"tag": "VENDOR",    "label": "AP / vendor",
                       "guide": "Accounts payable, vendor invoices, payments, reconciliations."},
            "black":  {"tag": "GENERAL",   "label": "general",
                       "guide": "General / catch-all when nothing else fits (the permanent default)."},
        },
    },
    "hr": {
        "label": "HR / People",
        "description": "People-operations taxonomy.",
        "core": ("green", "blue", "black"),
        "slots": {
            "green":  {"tag": "RECRUITING", "label": "recruiting",
                       "guide": "Sourcing, interviews, offers, the hiring pipeline."},
            "blue":   {"tag": "ONBOARDING", "label": "onboarding",
                       "guide": "New-hire onboarding, provisioning, orientation."},
            "orange": {"tag": "REVIEWS",    "label": "performance reviews",
                       "guide": "Performance reviews, calibration, feedback cycles."},
            "gold":   {"tag": "POLICY",     "label": "policy",
                       "guide": "HR policies, the handbook, compliance, documentation."},
            "pink":   {"tag": "BENEFITS",   "label": "benefits & comp",
                       "guide": "Benefits, compensation, payroll, leave."},
            "black":  {"tag": "GENERAL",    "label": "general",
                       "guide": "General / catch-all when nothing else fits (the permanent default)."},
        },
    },
    "exec": {
        "label": "Executive",
        "description": "Leadership / executive taxonomy.",
        "core": ("red", "green", "black"),
        "slots": {
            "red":    {"tag": "DECISIONS",   "label": "decisions",
                       "guide": "Decisions to make or record: approvals, sign-offs, calls."},
            "blue":   {"tag": "MEETINGS",    "label": "meetings",
                       "guide": "Meetings, 1:1s, prep, agendas, follow-ups."},
            "green":  {"tag": "INITIATIVES", "label": "initiatives",
                       "guide": "Strategic initiatives, OKRs, cross-functional programs."},
            "gold":   {"tag": "COMMS",       "label": "communications",
                       "guide": "Communications: announcements, memos, stakeholder updates."},
            "black":  {"tag": "GENERAL",     "label": "general",
                       "guide": "General / catch-all when nothing else fits (the permanent default)."},
        },
    },
    "general": {
        "label": "General",
        "description": "Lean, discipline-neutral tri-slot taxonomy.",
        "core": ("green", "pink", "black"),
        "slots": {
            "green":  {"tag": "WORK",     "label": "work",
                       "guide": "Work tasks: the default for anything job-related."},
            "pink":   {"tag": "PERSONAL", "label": "personal",
                       "guide": "Personal projects / side work / life admin."},
            "black":  {"tag": "GENERAL",  "label": "general",
                       "guide": "General / catch-all when nothing else fits (the permanent default)."},
        },
    },
}
DEFAULT_PACK = "dev"
PERMANENT = "black"   # ⚫ GENERAL — every pack must define it; always the default slot
DEFAULT = "black"


def _org_packs_path():
    """The org-supplied pack file: `category_packs.json` in the data dir, or None if
    the data dir can't be resolved. Packs there add/override shipped packs."""
    try:
        import paths
        return os.path.join(paths.data_dir(), "category_packs.json")
    except Exception:
        return None


def _valid_pack(entry):
    """True when `entry` is a well-formed pack: a dict with a `slots` dict whose every
    value carries at least {tag, label}, and which defines the PERMANENT (black /
    GENERAL) slot. Malformed org/user packs are ignored wholesale (never crash)."""
    if not isinstance(entry, dict):
        return False
    slots = entry.get("slots")
    if not isinstance(slots, dict) or PERMANENT not in slots:
        return False
    for meta in slots.values():
        if not (isinstance(meta, dict) and {"tag", "label"} <= set(meta)):
            return False
    return True


def effective_packs():
    """The pack registry: shipped PACKS with any org-supplied packs from
    `category_packs.json` (data dir) merged on top. Returns a DEEP COPY, so callers
    may mutate freely and the shipped PACKS are never touched. An org file may ADD a
    new pack or OVERRIDE slots/metadata of a shipped pack (same merge discipline as
    the `categories` override map — malformed entries are skipped, never crash). The
    file is either a bare {name: pack} map or nests the map under a "packs" key. A
    pack that fails validation after merge is reverted / dropped."""
    base = copy.deepcopy(PACKS)
    path = _org_packs_path()
    if not path:
        return base
    try:
        with open(path) as f:
            org = json.load(f)
    except Exception:
        return base
    if not isinstance(org, dict):
        return base
    org_packs = org.get("packs") if isinstance(org.get("packs"), dict) else org
    if not isinstance(org_packs, dict):
        return base
    for name, entry in org_packs.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        if name in base:
            dst = base[name]
            for k, v in entry.items():
                if k == "slots" and isinstance(v, dict):
                    dslots = dst.setdefault("slots", {})
                    for sk, sv in v.items():
                        if isinstance(sv, dict):
                            dslots.setdefault(sk, {}).update(sv)
                else:
                    dst[k] = v
            if not _valid_pack(dst):
                base[name] = copy.deepcopy(PACKS[name])   # revert a broken merge
        elif _valid_pack(entry):
            base[name] = copy.deepcopy(entry)
    return base


def available_packs():
    """Pack names available to select (shipped + org), the DEFAULT pack first, then
    the rest alphabetically."""
    names = list(effective_packs())
    return sorted(names, key=lambda n: (0 if n == DEFAULT_PACK else 1, n))


def active_pack():
    """The active pack NAME: config `category_pack`, validated against the available
    packs (shipped + org), falling back to DEFAULT_PACK for an absent/unknown value."""
    try:
        import config as _config
        name = _config.get("category_pack")
    except Exception:
        name = None
    return name if isinstance(name, str) and name in effective_packs() else DEFAULT_PACK


def pack_display(name):
    """Human display label for a pack (its `label`, else the name itself)."""
    entry = effective_packs().get(name)
    if isinstance(entry, dict) and entry.get("label"):
        return entry["label"]
    return name


def pack_description(name):
    """One-line description for a pack (its `description`, else "")."""
    entry = effective_packs().get(name)
    if isinstance(entry, dict) and entry.get("description"):
        return entry["description"]
    return ""


def _build_pack(name):
    """Materialise pack `name` from the effective registry into the module's active
    taxonomy triple: (CATEGORIES {key:{dot,tag,label}}, CATEGORY_GUIDE {key:guide},
    CORE tuple). Dots fill from an explicit slot `dot`, else _CANONICAL_DOTS, else the
    GENERAL dot. Slot order is preserved. Falls back to the DEV pack if the named pack
    is missing/invalid. PERMANENT is always forced into CORE."""
    packs = effective_packs()
    entry = packs.get(name)
    if not _valid_pack(entry):
        entry = packs.get(DEFAULT_PACK) or PACKS[DEFAULT_PACK]
    cats, guide = {}, {}
    fallback_dot = _CANONICAL_DOTS.get(PERMANENT, "⚫")
    for key, meta in entry["slots"].items():
        cats[key] = {"dot": meta.get("dot") or _CANONICAL_DOTS.get(key, fallback_dot),
                     "tag": meta["tag"], "label": meta["label"]}
        if meta.get("guide"):
            guide[key] = meta["guide"]
    core = tuple(k for k in entry.get("core", ()) if k in cats)
    if PERMANENT not in core:
        core = core + (PERMANENT,)
    return cats, guide, core


# The ACTIVE taxonomy, materialised from the active pack. `_apply_overrides()` then
# merges the config.json `categories` per-slot overrides on top (unchanged machinery),
# so an override still wins over its pack slot.
CATEGORIES, CATEGORY_GUIDE, CORE = _build_pack(active_pack())

# A pristine snapshot of the ACTIVE-PACK defaults, captured BEFORE _apply_overrides()
# can mutate CATEGORIES. `default_tag_label` reads this so the board can show what a
# slot looked like before a user override (the "Default: …" line on overridden rows).
_SHIPPED = {k: dict(v) for k, v in CATEGORIES.items()}


def category_guide(color):
    """The "when to use" sentence for category `color` (resolved via the same key /
    emoji / [TAG] / label aliases as resolve()), or "" for an unknown key."""
    key = resolve(color)
    return CATEGORY_GUIDE.get(key, "") if key else ""


_HUB_SLUG_RE = re.compile(r"[^a-z0-9]+")


def hub_slug(color):
    """The category-hub SLUG for `color` (any key / emoji / [TAG] / label alias): its
    [TAG] lowercased and reduced to a filesystem/wikilink-safe token (black→'general',
    red→'bug', green→'feature'). Unknown/empty ⇒ the DEFAULT category's slug, so a hub
    slug is always well-formed. This is the join between a task note's
    `[[categories/<slug>]]` link and its hub page filename."""
    key = normalize(color)
    s = _HUB_SLUG_RE.sub("-", CATEGORIES[key]["tag"].strip().lower()).strip("-")
    return s or key


# --- Emergent sub-groups within a category (WS11) ----------------------------
# A refinement of category hubs: within ONE category's task set, tokens that recur
# across several of its tasks (and are DISTINCTIVE to it) auto-cluster into a
# sub-group — e.g. many "hammerspoon …" personal tasks form a `hammerspoon` sub-hub
# under the PERSONAL category. Pure, deterministic, stdlib-only; NO LLM. The emission
# (sub-hub pages + most-specific per-note link) lives in export.py / task-station.py;
# this module owns only the detection rule.

# Tokens that must never seed a sub-group: common task verbs / status words (given
# by the WS11 design) plus obvious English stopwords >= 4 chars (the < 4 filter
# already drops the/and/for/…). A distinctive noun like "hammerspoon" survives; a
# generic "update"/"review"/"which" does not.
_SUBGROUP_STOPWORDS = frozenset({
    # task / status words
    "fix", "add", "update", "make", "setup", "task", "issue", "review", "check",
    "remove", "create", "support", "error", "test", "tests",
    # obvious English stopwords (>= 4 chars)
    "this", "that", "these", "those", "with", "from", "into", "your", "yours",
    "their", "them", "they", "then", "than", "when", "what", "which", "whom",
    "whose", "where", "will", "would", "could", "should", "have", "has", "had",
    "been", "were", "was", "are", "and", "the", "for", "but", "not", "you",
    "our", "out", "over", "some", "more", "most", "only", "just", "like", "also",
    "such", "very", "here", "there", "about", "after", "before", "again", "once",
    "each", "every", "both", "into", "onto", "upon", "does", "done", "using",
    "used", "make", "made", "need", "needs", "want", "wants", "work", "works",
})

_SUBGROUP_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SUBGROUP_MIN_LEN = 4          # a token must be at least this long to cluster
_SUBGROUP_MIN_TASKS = 3        # a token must appear in >= this many category tasks
_SUBGROUP_OUTSIDE_FRAC = 0.10  # DISTINCTIVE: < this fraction of OUTSIDE tasks


def _subgroup_tokens(item):
    """The distinctive-candidate token SET for one task-like item — lowercase
    alnum tokens (length >= 4) drawn from its `title` + `slug`, minus stopwords.
    A set (per-task de-duped) so a token repeated in one task counts once toward
    its "appears in N tasks" frequency."""
    text = "%s %s" % (item.get("title") or "", item.get("slug") or "")
    toks = set(_SUBGROUP_TOKEN_RE.findall(text.lower()))
    return {t for t in toks
            if len(t) >= _SUBGROUP_MIN_LEN and t not in _SUBGROUP_STOPWORDS}


def _category_self_tokens(key):
    """The tokens a category NAMES ITSELF by — its key plus the lowercase alnum tokens
    of its [TAG] and label (e.g. green → {'green', 'feature', 'work'}). A token in this
    set must never seed a sub-group WITHIN that category: a `feature/feature` or
    `migration/migration` sub-hub is redundant with the category hub itself. Reads the
    live (override-merged) CATEGORIES, so a user's renamed tag/label is honoured."""
    meta = CATEGORIES.get(key, {})
    text = "%s %s %s" % (key, meta.get("tag", ""), meta.get("label", ""))
    return set(_SUBGROUP_TOKEN_RE.findall(text.lower()))


def detect_subgroups(tasks_by_category):
    """Detect emergent sub-groups within each category. Pure + deterministic.

    `tasks_by_category` maps a category key -> a list of task-like dicts, each
    carrying at least a `title` (and optionally a `slug`); the SAME dict objects
    are returned in the member lists, so a caller keys membership off its own
    identity. All categories are passed together because distinctiveness is judged
    against tasks OUTSIDE the category.

    A token seeds a sub-group within a category when it (a) appears in >=
    `_SUBGROUP_MIN_TASKS` of that category's tasks, (b) is DISTINCTIVE — present in
    < `_SUBGROUP_OUTSIDE_FRAC` of all tasks outside the category (so generic
    words like 'update'/'release' never cluster), (c) is not a stopword, (d) is not one
    of the category's OWN name tokens (its key/tag/label — a `feature/feature` sub-hub
    is redundant), and (e) satisfies the PLURALITY rule: a token may seed a sub-group
    only in the ONE category where it occurs most frequently; on a tie for that maximum
    it seeds one nowhere (so a shared 'story' clusters only where it dominates). Each
    task joins at most ONE sub-group per category: the qualifying token with the highest
    in-category frequency (ties broken alphabetically). After that single assignment,
    only groups still holding >= `_SUBGROUP_MIN_TASKS` members survive.

    Returns `{category_key: {token: [item, ...]}}` — only categories with >= 1
    surviving group, only surviving groups, members in input order. Tokens within a
    category are emitted in alphabetical order so the mapping is byte-stable."""
    # Pre-tokenise every item once; build the global per-token document frequency
    # (across ALL categories) so distinctiveness is a cheap subtraction later.
    all_items = [(ck, it) for ck, items in tasks_by_category.items() for it in items]
    total_all = len(all_items)
    tokens_of = {}
    global_df = {}
    for _ck, it in all_items:
        ts = _subgroup_tokens(it)
        tokens_of[id(it)] = ts
        for t in ts:
            global_df[t] = global_df.get(t, 0) + 1

    # Per-category in-category document frequency (tasks in the category holding a token).
    in_df_by_cat = {}
    for ck, items in tasks_by_category.items():
        d = {}
        for it in items:
            for t in tokens_of[id(it)]:
                d[t] = d.get(t, 0) + 1
        in_df_by_cat[ck] = d

    # PLURALITY pre-pass: the category that OWNS each token is the one where it occurs
    # most frequently; a tie for that maximum (two categories share the top count) means
    # no category owns it, so it seeds a sub-group NOWHERE.
    token_owner = {}
    all_tokens = set()
    for d in in_df_by_cat.values():
        all_tokens.update(d)
    for t in all_tokens:
        best_cat, best_n, tied = None, 0, False
        for ck, d in in_df_by_cat.items():
            n = d.get(t, 0)
            if n > best_n:
                best_cat, best_n, tied = ck, n, False
            elif n == best_n and n > 0:
                tied = True
        token_owner[t] = None if tied else best_cat

    result = {}
    for ck, items in tasks_by_category.items():
        n_cat = len(items)
        outside_total = total_all - n_cat
        in_df = in_df_by_cat[ck]
        self_tokens = _category_self_tokens(ck)
        # Candidate tokens: frequent enough, distinctive, non-stopword (stopwords are
        # already gone from the token sets), NOT a category-self token, and owned by
        # THIS category under the plurality rule.
        candidates = {}
        for t, c in in_df.items():
            if c < _SUBGROUP_MIN_TASKS:
                continue
            if t in self_tokens:
                continue
            if token_owner.get(t) != ck:
                continue
            outside_count = global_df.get(t, 0) - c
            if outside_total > 0 and outside_count >= _SUBGROUP_OUTSIDE_FRAC * outside_total:
                continue
            candidates[t] = c
        if not candidates:
            continue
        # Single-assignment: each task -> its highest-frequency candidate token
        # (ties alphabetical). Then keep only groups still holding >= the threshold.
        groups = {}
        for it in items:
            cand = tokens_of[id(it)] & set(candidates)
            if not cand:
                continue
            best = min(cand, key=lambda t: (-candidates[t], t))
            groups.setdefault(best, []).append(it)
        kept = {t: groups[t] for t in sorted(groups) if len(groups[t]) >= _SUBGROUP_MIN_TASKS}
        if kept:
            result[ck] = kept
    return result


def hub_meta(color):
    """The category-hub metadata for `color` as a dict — `key` (canonical category
    key), `slug`, `dot` (emoji), `tag` ([TAG] text), `label` (human label), and
    `description` (the "when to use" guide sentence). Unknown/empty color resolves to
    the DEFAULT category. Consumed by the export/mirror to build a hub page's
    frontmatter + heading and the per-note category link."""
    key = normalize(color)
    m = CATEGORIES[key]
    return {"key": key, "slug": hub_slug(key), "dot": m["dot"], "tag": m["tag"],
            "label": m["label"], "description": CATEGORY_GUIDE.get(key, "")}


def default_tag_label(color):
    """The SHIPPED default {dot, tag, label} for the slot `color` resolves to (via the
    same key / emoji / [TAG] / label aliases as resolve()), captured BEFORE any user
    override mutated CATEGORIES. The board renders this as the "Default: …" line on an
    overridden category row. None for an unknown colour."""
    key = resolve(color)
    if not key or key not in _SHIPPED:
        return None
    m = _SHIPPED[key]
    return {"dot": m.get("dot"), "tag": m.get("tag"), "label": m.get("label")}

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
            "orange": {"bg": "#3a1b08", "fg": "#dcd2c0", "bold": "#f0926e", "cursor": "#f0926e", "sel": "#20545e", "ansi": _DARK_ANSI},
            "yellow": {"bg": "#26220f", "fg": "#dcd2c0", "bold": "#ffb454", "cursor": "#ffb454", "sel": "#4a3270", "ansi": _DARK_ANSI},
            "green":  {"bg": "#1c2a16", "fg": "#dcd2c0", "bold": "#b6e85a", "cursor": "#b6e85a", "sel": "#6e2a4e", "ansi": _DARK_ANSI},
            "blue":   {"bg": "#141d2e", "fg": "#d6d8c8", "bold": "#5bc8f5", "cursor": "#5bc8f5", "sel": "#7a5816", "ansi": _DARK_ANSI},
            "purple": {"bg": "#1f1730", "fg": "#dcd2c0", "bold": "#d9b0f0", "cursor": "#d9b0f0", "sel": "#2f5a2a", "ansi": _DARK_ANSI},
            "black":  {"bg": "#121214", "fg": "#e6c55e", "bold": "#79c9d6", "cursor": "#79c9d6", "sel": "#2e4a5e", "ansi": _DARK_ANSI},
            "pink":   {"bg": "#2b0f1d", "fg": "#dcd2c0", "bold": "#ff6ab0", "cursor": "#ff6ab0", "sel": "#245a3e", "ansi": _DARK_ANSI},
            "white":  {"bg": "#5e5c5c", "fg": "#f7f9fc", "bold": "#ec7bbd", "cursor": "#ec7bbd", "sel": "#6e4a62", "ansi": _DARK_ANSI},
            "silver": {"bg": "#242b3c", "fg": "#dde1e8", "bold": "#e6c27a", "cursor": "#e6c27a", "sel": "#6e5418", "ansi": _DARK_ANSI},
            "gold":   {"bg": "#2e2a0c", "fg": "#dcd2c0", "bold": "#ffd24a", "cursor": "#ffd24a", "sel": "#2e3a6e", "ansi": _DARK_ANSI},
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

# The canonical per-slot emoji — the source of truth an override inherits from when
# it omits `dot` (see _apply_overrides). Slot-canonical and pack-independent, so an
# override for a slot not in the active pack still gets the right dot.
SLOT_DOTS = dict(_CANONICAL_DOTS)

# --- Active (enabled) categories ---------------------------------------------
# A lean, growable set of "on" slots, persisted in config.json as
# `enabled_categories`. Unconfigured ⇒ CORE only (the board starts small and grows:
# auto_categories auto-enables a slot the first time a task is assigned to it — see
# auto_enable). ⚫ GENERAL is PERMANENT: always enabled, never disable-able. CORE
# (the active pack's lean seed, removable except GENERAL) and PERMANENT are defined
# with the pack registry above and follow the active pack.

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
# must hard-steer to task-station (the cross-session board + session-resume binding),
# not the native per-session task tools. These run once per prompt, like _CMD_RE.

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


def overridden_keys():
    """The category keys the user has CUSTOMIZED via config.json `categories`
    (a {tag,label} override merged over the shipped default). Used to mark which
    categories are user-overridden vs. defaults on the board. Empty set when none /
    config unreadable. Mirrors the same {tag,label} acceptance as _apply_overrides."""
    try:
        import config as _config
        cats = _config.get("categories")
    except Exception:
        return set()
    if not isinstance(cats, dict):
        return set()
    return {k for k, v in cats.items()
            if isinstance(v, dict) and {"tag", "label"} <= set(v)}


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
    auto_categories is on, present the ACTIVE PACK's full taxonomy and note that
    assigning a not-yet-shown slot enables it automatically; otherwise scope to
    enabled. The taxonomy shown is the active pack's, so self-categorisation works for
    any discipline (a finance/hr/exec pack teaches its own slots, not dev's)."""
    pack = active_pack()
    tail = "" if pack == DEFAULT_PACK else " (pack: %s)" % pack
    if auto_categories_on():
        return ["Pick the MOST ACCURATE category COLOR from the active pack's "
                "%d-slot taxonomy%s (see CATEGORIES.md) — a slot not yet on the board "
                "is enabled automatically the moment you assign it:" % (len(CATEGORIES), tail),
                "  " + legend(_all_items())]
    return ["Pick a category COLOR for the task from its context%s (see CATEGORIES.md):" % tail,
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


def tint_escape(color, term):
    """The terminal escape string that tints the window to the ACTIVE theme's full
    palette for category `color` in the resolved VARIANT, or '' for a no-op. Zero-
    setup: standard OSC that iTerm AND Terminal.app both honor, plus one iTerm-only
    extra for the bold color.

      bg      OSC 11   \\033]11;<hex>\\007
      fg      OSC 10   \\033]10;<hex>\\007
      cursor  OSC 12   \\033]12;<hex>\\007
      ANSI n  OSC 4    \\033]4;<n>;<hex>\\007   for n in 0..15 (when 'ansi' present)
      sel     OSC 17   \\033]17;<hex>\\007      (when 'sel' present)
      bold    iTerm    \\033]1337;SetColors=bold=<hexNoHash>\\007  (iTerm only)

    The standard OSC (11/10/12/4/17) is emitted for ANY tinting terminal — iTerm2,
    Apple Terminal, and any other xterm-compatible terminal term.detect() classes as
    "osc" (WezTerm, VS Code, Ghostty, Windows Terminal, kitty, Alacritty, …). The
    SetColors=bold extra is iTerm-only. When running inside tmux ($TMUX set) the whole
    escape is wrapped in tmux's DCS passthrough (term.tmux_wrap) so it reaches the
    real terminal instead of being swallowed by the pane.

    Resolution order: active theme (config.active_theme, default `default`) → variant
    (resolve_variant: the OS appearance, or a forced --tint-theme) → that variant's
    per-category palette (falling back to the `default` theme's variant). Only
    dot/tag/label still come from CATEGORIES. A palette that defines ONLY a bg still
    emits just the bg. `term == 'none'`, an unknown color, or a category with no
    palette at all yield ''."""
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
    # Under tmux the escape must be DCS-wrapped or the pane swallows it; a no-op
    # otherwise (term.tmux_wrap gates on $TMUX). Wrap the whole sequence at once.
    return _term.tmux_wrap("".join(parts))
