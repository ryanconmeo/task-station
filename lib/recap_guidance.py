"""Versioned guidance data for the weekly recap (task 444, run 4).

PURE DATA + tiny pure helpers, NO IO, NO task-station imports beyond stdlib. This is
the ONE place to update as models/practices evolve — the recap renders these tables
against the user's observed week, and the curator (if configured) can cite them.

Four tables:
  * STRATEGY_PRACTICES — universal LLM best practice, natural language, applicable in
    ANY chat. Tooling is cited only where it genuinely serves a practice, and NEVER a
    model-invoked feature (see INVOKED_BY / the review's directive #2).
  * INVOKED_BY — the feature-invocation registry. Every task-station feature the
    guidance may reference records who invokes it: human | both | model | human-audit.
    A `model`-only feature is NEVER put in front of a human as a "run this" action.
  * MODEL_ROLE_MATRIX — per work TYPE, the recommended capability class + effort, with
    a version + generation note so future model generations update ONE table.
  * ECO_ASSUMPTIONS — DIRECTIONAL energy/CO2/water factors (ranges, cited inline) for
    the cost-equivalence section. Deliberately order-of-magnitude, never precise.
"""

# ============================================================ capability classes ==
#
# Tiers name CAPABILITY CLASSES, not fixed model ids. As new generations ship, remap
# the current cheapest-capable / mid / strongest families here — ONE place.

MATRIX_VERSION = "2026.1"

MODEL_GENERATION_NOTE = (
    "Tiers name capability CLASSES, not fixed model ids. As new generations ship, map "
    "your current cheapest-capable / mid / strongest models onto these classes and "
    "update this one table (lib/recap_guidance.py) — the guidance follows automatically."
)

# Current representative families per class (lowercased pricing.model_family output).
CLASS_FAMILIES = {
    "cheap": ("haiku",),
    "mid": ("sonnet",),
    "strong": ("opus", "fable"),
}

CLASS_LABEL = {"cheap": "cheapest capable", "mid": "mid", "strong": "strongest",
               "unknown": "unknown"}

# A representative current model id per class — used only to make a `/model` hint
# concrete. Update alongside CLASS_FAMILIES when a generation ships.
CLASS_EXAMPLE_MODEL = {"cheap": "claude-haiku-4-5", "mid": "claude-sonnet-5",
                       "strong": "claude-opus-4-8"}


def example_model_for_class(cls):
    return CLASS_EXAMPLE_MODEL.get(cls, "claude-sonnet-5")

_CLASS_RANK = {"cheap": 0, "mid": 1, "strong": 2}


def class_of_family(family):
    """The capability class for a model family, or 'unknown' when unrecognised."""
    fam = (family or "").lower()
    for cls, fams in CLASS_FAMILIES.items():
        if fam in fams:
            return cls
    return "unknown"


def class_rank(cls):
    """Orderable rank (cheap<mid<strong); 'unknown' sorts as mid so it never trips an
    over/under-powered delta on its own."""
    return _CLASS_RANK.get(cls, 1)


# =============================================================== feature registry ==
#
# invoked_by: who actually triggers the feature.
#   human       — a person types it (a slash command / CLI they run themselves)
#   both        — either a person OR the assistant may invoke it
#   model       — the ASSISTANT invokes it (e.g. via MCP); NEVER tell a human to "run" it
#   human-audit — the assistant proposes, a human reviews/approves
# `human_action` is the exact thing to put in a human recommendation — present ONLY for
# features a human may run (human / both / human-audit); None for model-only features.

INVOKED_BY = {
    "save": {"invoked_by": "human", "what": "checkpoint the current context into the task digest",
             "human_action": "/todo save"},
    "memo": {"invoked_by": "both", "what": "leave a durable, ack-able note between sessions/machines",
             "human_action": "task-station memo --task <n> \"<note>\""},
    "brief": {"invoked_by": "both", "what": "render a shareable house-style one-pager for a task",
              "human_action": "/brief"},
    "delegate": {"invoked_by": "human", "what": "hand repo work to a worker in its own worktree",
                 "human_action": "task-station delegate --project <name> \"<task>\""},
    "auto_checkpoint": {"invoked_by": "human",
                        "what": "let the Stop hook nudge a save before auto-compaction",
                        "human_action": "task-station config --auto-checkpoint on"},
    "recap": {"invoked_by": "human", "what": "this weekly recap",
              "human_action": "task-station recap"},
    # Model-invoked — a human is NEVER told to run these; they show up only as
    # "your assistant can do this — just ask".
    "search": {"invoked_by": "model", "what": "full-text search across your task history",
               "human_action": None},
    "brains_suggest": {"invoked_by": "human-audit",
                       "what": "the assistant proposes related brain notes; you approve",
                       "human_action": "review the Related-knowledge panel on the board"},
}


def is_human_recommendable(feature):
    """True unless the feature is MODEL-invoked. A model-only feature must never be
    recommended to a human as a 'run this' action (the W29 bug class)."""
    entry = INVOKED_BY.get(feature)
    if not entry:
        return True                      # unknown key → not a registry feature; caller owns it
    return entry.get("invoked_by") != "model"


def human_action(feature):
    """The exact command/action to show a human for a feature, or None when the
    feature is model-invoked (so callers omit any 'run this')."""
    entry = INVOKED_BY.get(feature) or {}
    if entry.get("invoked_by") == "model":
        return None
    return entry.get("human_action")


def feature_invoker(feature):
    return (INVOKED_BY.get(feature) or {}).get("invoked_by")


# ============================================================ strategy practices ==
#
# Universal, model-agnostic best practice a non-expert can apply in ANY LLM chat.
# `feature` (optional) cites a task-station tool that SERVES the practice; the recap
# renders a human command for human/both features and an "ask your assistant" line for
# a model feature — never a "run this" for a model-invoked tool.

STRATEGY_PRACTICES = [
    {"key": "specific",
     "title": "Say the goal, the constraints, and what “done” looks like",
     "body": "Open with the outcome you want, the hard constraints, and how you'll "
             "judge success. Paste the real code, error, or data — not a paraphrase. "
             "A precise first message beats three rounds of clarifying questions."},
    {"key": "reuse-context",
     "title": "Build context once, then reuse it",
     "body": "Keep a short, living summary of the decisions and state that matter so "
             "you — or a fresh chat — never re-explain from scratch. Checkpoint it "
             "before you switch machines or start a new thread.",
     "feature": "save"},
    {"key": "dont-rederive",
     "title": "Retrieve before you re-derive",
     "body": "Before asking the same question again, pull up the earlier answer. Your "
             "assistant can search your own history and past decisions for you — ask "
             "for it rather than reconstructing the thread from memory.",
     "feature": "search"},
    {"key": "match-model",
     "title": "Match the model to the job",
     "body": "Hard reasoning, architecture, and planning deserve the strongest model at "
             "high effort; rote edits and formatting run just as well on the cheapest "
             "capable one for a fraction of the cost. The matrix below is the cheat sheet."},
    {"key": "small-steps",
     "title": "Work in small, verifiable steps",
     "body": "Ask for a plan, approve it, then execute a step at a time and check each "
             "result. Staged work is cheaper and more reliable than one giant prompt you "
             "have to unwind when it drifts."},
    {"key": "token-economy",
     "title": "Mind the context window — long threads re-send everything",
     "body": "Every turn re-processes the whole conversation, so a ballooning thread "
             "quietly multiplies cost and dilutes focus. When a chat gets long, capture a "
             "summary and start fresh instead of dragging the history along.",
     "feature": "save"},
    {"key": "repeatable",
     "title": "Make the good prompts repeatable",
     "body": "Save the prompts and patterns that worked so you can rerun them, and hand "
             "off with a written brief rather than a verbal recap — repeatable beats "
             "re-remembered.",
     "feature": "brief"},
]


# ============================================================== model-role matrix ==
#
# Per work TYPE: recommended capability class(es) + effort. `classes` is the allowed
# set (cheap|mid|strong); a range like ["mid","strong"] means "scale to difficulty".

MODEL_ROLE_MATRIX = [
    {"work_type": "mechanical", "title": "Mechanical / bulk edits",
     "examples": "renames, boilerplate, formatting, test scaffolding",
     "classes": ["cheap"], "effort": "low",
     "why": "rote transforms don't need a frontier model — the cheapest capable tier "
            "produces the same edit for a fraction of the cost."},
    {"work_type": "delegated_impl", "title": "Delegated implementation",
     "examples": "a scoped feature or fix handed to a worker",
     "classes": ["mid", "strong"], "effort": "medium",
     "why": "scale the tier to difficulty — mid for well-specified work, strong only "
            "when the logic is genuinely hard."},
    {"work_type": "hard_logic", "title": "Hard logic / architecture / planning",
     "examples": "design, tricky algorithms, cross-cutting refactors, brainstorming",
     "classes": ["strong"], "effort": "high",
     "why": "reasoning-dense work is exactly where the strongest model at high effort "
            "earns its cost."},
    {"work_type": "research", "title": "Wide research",
     "examples": "surveying many files or sources",
     "classes": ["mid", "strong"], "effort": "medium",
     "why": "fan breadth out on a mid model, then synthesize the findings with a strong "
            "one — don't pay frontier rates for the wide scan."},
    {"work_type": "review", "title": "Review",
     "examples": "code review, verifying a diff",
     "classes": ["strong"], "effort": "high",
     "why": "a strong model with an adversarial “try to break this” framing catches "
            "what a quick pass misses."},
    {"work_type": "conversation", "title": "Conversation / triage",
     "examples": "quick questions, status, routing",
     "classes": ["mid"], "effort": "low",
     "why": "keep the loop cheap and fast; escalate only when the question turns hard."},
]

_MATRIX_BY_TYPE = {r["work_type"]: r for r in MODEL_ROLE_MATRIX}


def matrix_row(work_type):
    return _MATRIX_BY_TYPE.get(work_type)


def recommended_classes(row):
    return list(row.get("classes") or [])


def tier_label(row):
    """Human label for a row's recommended class(es), e.g. 'cheapest capable' or
    'mid → strongest (by difficulty)'."""
    classes = recommended_classes(row)
    labels = [CLASS_LABEL.get(c, c) for c in classes]
    if len(labels) <= 1:
        return labels[0] if labels else "—"
    return "%s → %s (by difficulty)" % (labels[0], labels[-1])


def fit_delta(observed_class, row):
    """Compare an OBSERVED capability class to a matrix row's recommendation.
    Returns ('over'|'under'|'on'|'unknown', human_phrase)."""
    if not observed_class or observed_class == "unknown":
        return "unknown", "no clear dominant model"
    rec = recommended_classes(row)
    if not rec:
        return "unknown", ""
    ranks = [class_rank(c) for c in rec]
    obs = class_rank(observed_class)
    if obs > max(ranks):
        return "over", "you ran a stronger tier than needed — costs more for the same result"
    if obs < min(ranks):
        return "under", "you ran a lighter tier than recommended — quality may suffer"
    return "on", "on target"


# work-type inference for the OBSERVED week. Maps a session's dominant work phase (+
# whether it ran in a delegated worker) onto a matrix work_type.
_PHASE_WORK_TYPE = {
    "implementation": "mechanical",     # overridden to delegated_impl for worker sessions
    "planning": "hard_logic",
    "research": "research",
    "verification": "review",
    "delivery": "mechanical",
    "other": "conversation",
}


def work_type_for(phase, role=None):
    """The matrix work_type for an observed session's dominant phase + role."""
    if role == "worker" and phase == "implementation":
        return "delegated_impl"
    return _PHASE_WORK_TYPE.get(phase or "other", "conversation")


# ================================================================ eco assumptions ==
#
# DIRECTIONAL only. No vendor publishes per-token energy; real figures swing with
# hardware, batching, context length, and cooling. Everything below is an order-of-
# magnitude community/agency estimate expressed as a RANGE and must be labelled so.

ECO_VERSION = "2026.1"

# Energy per 1M tokens PROCESSED (input + output + cache reads), by capability class.
# (low, high) kWh. Larger models cost more energy per token.
ECO_ENERGY_KWH_PER_MTOK = {
    "cheap": (0.02, 0.15),
    "mid": (0.05, 0.40),
    "strong": (0.20, 1.20),
    "unknown": (0.05, 0.40),
}

# Grid carbon intensity (kg CO2e per kWh): global-average electricity mix. Hyperscaler
# regions are often lower; a coal-heavy grid higher.
ECO_GRID_KG_PER_KWH = (0.35, 0.45)

# Data-center water intensity (litres per kWh): on-site cooling + off-site (the water
# footprint of the electricity itself). Published WUE figures vary widely.
ECO_WATER_L_PER_KWH = (0.20, 3.90)

ECO_CITATIONS = [
    {"factor": "energy / token",
     "value": "0.02–1.2 kWh per 1M tokens (by model class)",
     "note": "order-of-magnitude — no vendor publishes per-token energy; scales with "
             "model size, context length, batching and hardware."},
    {"factor": "grid intensity",
     "value": "0.35–0.45 kg CO₂e / kWh",
     "note": "global-average electricity mix (~0.4); a clean regional grid is lower."},
    {"factor": "water",
     "value": "0.2–3.9 L / kWh",
     "note": "data-center cooling + the water footprint of the electricity; reported "
             "water-use effectiveness ranges widely by site and season."},
]


def eco_estimate(token_by_class):
    """DIRECTIONAL energy/CO2/water RANGES for a {capability_class: processed_tokens}
    map. Returns {kwh, co2_kg, water_l} each a (low, high) tuple. Pure arithmetic over
    the assumption ranges above; never a point estimate."""
    e_lo = e_hi = 0.0
    for cls, tok in (token_by_class or {}).items():
        lo, hi = ECO_ENERGY_KWH_PER_MTOK.get(cls, ECO_ENERGY_KWH_PER_MTOK["unknown"])
        mtok = (tok or 0) / 1_000_000.0
        e_lo += mtok * lo
        e_hi += mtok * hi
    return {
        "kwh": (e_lo, e_hi),
        "co2_kg": (e_lo * ECO_GRID_KG_PER_KWH[0], e_hi * ECO_GRID_KG_PER_KWH[1]),
        "water_l": (e_lo * ECO_WATER_L_PER_KWH[0], e_hi * ECO_WATER_L_PER_KWH[1]),
    }
