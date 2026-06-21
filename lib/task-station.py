#!/usr/bin/env python3
"""Task Station — persistent, cross-session task tracking for Claude Code.

Engine for the /todo and /done commands and the SessionStart / UserPromptSubmit
hooks. Tasks live as one JSON file per task under store/tasks/. A session is
"attached" to at most one task via a link file under store/links/<session_id>.

Subcommands:
  create  --session ID --title T --summary S   create a task, attach the session
  attach  --session ID --task REF              attach session to an existing task
  bump    --session ID                          touch the attached task's activity
  skip    --session ID                          mark session intentionally untracked (silences nudge)
  done    --session ID                          close the attached task
  render  --session ID --arg STR                /todo entrypoint (list | detail+attach)
  prompt-context --session ID                   UserPromptSubmit hook context
  session-start  --session ID --source SRC      SessionStart hook context
  guidance                                      full attach/create how-to (on demand)

REF is a 1-based index from the most recent `render` listing, or a task id /
id-prefix. All writes are atomic (temp file + os.replace).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import paths
import store

BASE = os.path.dirname(os.path.abspath(__file__))  # code location only (self-invocation)
DATA = paths.data_dir()                             # mutable state — survives /plugin update
STORE = os.path.join(DATA, "store")
TASKS_DIR = os.path.join(STORE, "tasks")
LINKS_DIR = os.path.join(STORE, "links")
PENDING_BRIEFS = os.path.join(DATA, "pending-briefs")
DELEGATE_REGISTRY = os.path.join(DATA, "workers.json")
PROJECTS_ROOT = os.path.join(
    os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "projects")

LOG_KEEP = 25          # max activity-log entries kept per task
NUDGE_PROMPT_MAX = 120  # chars of the prompt stored in the activity log
NUDGE_ESCALATE_AFTER = 4   # unattached prompts before the nudge escalates
SKIP_SENTINEL = "__skip__"  # link value marking a session intentionally untracked
MAX_CLOSED_IN_LIST = 5  # closed tasks shown in the /todo list (most recent first)
SUBSTANCE_FLOOR = 3     # min user messages for a session to count as "real working" work

# Task lifecycle is ONE field — `status` — with three values:
#   open (○)  →  active (●)  →  closed
# A topic merely *raised* starts `open` and shows on the board immediately; it
# graduates to `active` when work actually starts (delegate --worktree, a file
# edit in an attached session, the manual `status` command, or `create --active`);
# /done closes it. "On the board / not done" means status in {open, active};
# "is closed" stays status == "closed". A missing/unknown status reads as open
# (back-compat — pre-existing tasks were open/closed only).
STATUS_OPEN = "open"
STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"
STATUS_DEFAULT = STATUS_OPEN
STATUS_BOARD = (STATUS_OPEN, STATUS_ACTIVE)   # "not closed" — on the board
STATUS_SETTABLE = (STATUS_OPEN, STATUS_ACTIVE)  # the manual `status` command's range
STATUS_GLYPH = {STATUS_OPEN: "○", STATUS_ACTIVE: "●"}

# Categories / colours are an OPTIONAL plugin: all of that logic lives in
# categories.py. If it's absent (or fails to import), `cats` is None and the
# tracker runs plain and colourless — no tags, no --color, no tint hints. See
# categories.py and CATEGORIES.md.
try:
    import categories as cats
except Exception:
    cats = None


def cat_color(color):
    """Normalised colour to store on a task, or None when categories are off."""
    return cats.normalize(color) if cats else None


def cat_tag(color, pad=False):
    """`<emoji> [TAG]` for the list, or "" when categories are off."""
    return cats.tag(color, pad=pad) if cats else ""


def task_status(task):
    """A task's lifecycle status, defaulting a missing/unknown value to open —
    so tasks written before this field existed read as open (back-compat)."""
    s = (task or {}).get("status")
    return s if s in (STATUS_OPEN, STATUS_ACTIVE, STATUS_CLOSED) else STATUS_DEFAULT


def is_closed(task):
    """True iff the task is done (status == closed)."""
    return task_status(task) == STATUS_CLOSED


def is_on_board(task):
    """True iff the task is still on the board (not closed — open or active)."""
    return not is_closed(task)


def status_glyph(task, muted_closed=True):
    """Leading lifecycle glyph for a row: `○` open / `●` active. Single-width,
    ASCII-safe. Closed tasks mute to a blank placeholder (single space) so the
    column still aligns — closed tasks live in their own section."""
    if muted_closed and is_closed(task):
        return " "
    s = task_status(task)
    return STATUS_GLYPH.get(s, STATUS_GLYPH[STATUS_OPEN])


def status_legend():
    """One-line legend explaining the leading glyphs (closed shown separately)."""
    return "Status:  %s open · %s active  (closed shown separately)" % (
        STATUS_GLYPH[STATUS_OPEN], STATUS_GLYPH[STATUS_ACTIVE])


def statusline_segment(task, width=0):
    """A ready-to-display, ANSI-colored one-line segment for a status bar:
    '#<seq>  <dot> [TAG]  <title>'. Self-contained — it carries its own colors
    and knows nothing about the bar that renders it. When width > 0 the title is
    truncated (with an ellipsis) so the whole visible segment fits that many
    columns; width 0 means no limit."""
    RESET = "\033[0m"
    C_SEQ   = "\033[38;2;235;215;120m"   # task number
    C_TAG   = "\033[38;2;150;150;160m"   # [CATEGORY] tag text
    C_TITLE = "\033[38;2;215;215;220m"   # title
    seq = str(task.get("seq", "") or "")
    title = task.get("title", "") or ""
    tag = cat_tag(task.get("color"))     # '<emoji> [TAG]' — emoji is self-colored
    # Color only the bracketed tag text, leaving the emoji dot untouched.
    if tag and "[" in tag:
        dot, _, rest = tag.partition("[")
        tag_disp = "%s%s[%s%s" % (dot, C_TAG, rest, RESET)
    else:
        tag_disp = tag
    prefix_plain = "#%s  %s%s" % (seq, (tag + "  ") if tag else "", "")
    if width and width > 0:
        avail = width - len(prefix_plain)
        if avail < 1:
            avail = 1
        if len(title) > avail:
            title = title[: max(1, avail - 1)] + "…"
    parts = ["%s#%s%s" % (C_SEQ, seq, RESET)]
    if tag:
        parts.append(tag_disp)
    parts.append("%s%s%s" % (C_TITLE, title, RESET))
    return "  ".join(parts)


def task_oneline(task):
    """One-line task summary matching the /todo list row's content: number,
    title, category tag, effort gauge. Used by the -s jump confirmation so it
    reads the same as the list. No fixed-width padding (it stands alone, not in
    a column) and no activity timestamp."""
    parts = ["%s  %s" % (task.get("seq", task["id"][:8]), task["title"])]
    tag = cat_tag(task.get("color"))
    if tag:
        parts.append(tag)
    parts.append(effort_cell(task.get("effort")))
    return "  ".join(parts)


def cat_lines(color):
    """Category summary line(s), or [] when categories are off. The terminal is
    tinted automatically by the hooks (tint_escape) — nothing to run by hand."""
    if not cats:
        return []
    return [cats.summary(color)]


# ----------------------------------------------------------------- effort ----
# Optional per-task effort estimate (complexity / scope), shown as a column in
# the /todo list. Canonical t-shirt sizes; a 5-segment filled bar makes the
# column scannable at a glance — count of filled segments (not bar height) is
# the size cue, which reads instantly even on a single row. Stored on the task
# as one of EFFORT_ORDER, or absent.
EFFORT_ORDER = ["XS", "S", "M", "L", "XL"]
_EFFORT_SLOTS = len(EFFORT_ORDER)
# filled ▰ to (index+1), empty ▱ for the rest → ▰▱▱▱▱ (XS) … ▰▰▰▰▰ (XL)
EFFORT_GAUGE = {
    s: "▰" * (i + 1) + "▱" * (_EFFORT_SLOTS - i - 1)
    for i, s in enumerate(EFFORT_ORDER)
}
EFFORT_GAUGE_EMPTY = "▱" * _EFFORT_SLOTS  # placeholder when effort is unset
EFFORT_WORD = {"XS": "trivial", "S": "small", "M": "medium", "L": "large", "XL": "huge"}
_EFFORT_ALIASES = {
    "xs": "XS", "tiny": "XS", "trivial": "XS", "1": "XS",
    "s": "S", "small": "S", "2": "S",
    "m": "M", "med": "M", "medium": "M", "3": "M",
    "l": "L", "large": "L", "big": "L", "4": "L",
    "xl": "XL", "huge": "XL", "epic": "XL", "5": "XL", "xxl": "XL",
}


def normalize_effort(val):
    """Map an agent/user-supplied effort token to a canonical size, or None.

    Accepts the sizes themselves (xs/s/m/l/xl), words (small/large/…) and the
    numeric 1–5 scale. Unknown input returns None so a typo never mislabels a
    task — the caller leaves the field unset rather than guessing."""
    if not val:
        return None
    return _EFFORT_ALIASES.get(str(val).strip().lower())


def effort_cell(effort):
    """Fixed-width `<gauge> <size>` cell for the list, or a neutral placeholder.

    The gauge is a fixed 5-segment bar; the size label is padded to 2 so XS/XL
    line up with S/M/L. Unknown effort renders an all-empty bar + `--` so the
    column stays aligned."""
    if effort in EFFORT_GAUGE:
        return "%s %-2s" % (EFFORT_GAUGE[effort], effort)
    return "%s --" % EFFORT_GAUGE_EMPTY


def effort_legend():
    return "Effort:  " + "  ".join("%s %s" % (EFFORT_GAUGE[s], s) for s in EFFORT_ORDER)


def commands_footer():
    """The authoritative one-line `/todo` command list — the single source of
    truth that commands/todo.md relays. Dense `·`-separated style matching the
    Effort:/Legend: lines; lists every command with a short label."""
    return ("Commands:  /todo <n> (open & resume)  ·  /todo <n[,n…]> -s (jump to pinned "
            "session, new window — comma list jumps several)  ·  /todo closed [N] · /todo all "
            "(more closed)  ·  /done (close current)  ·  /done <n[,n…]> (close by number; "
            "comma list closes several)  ·  /task-station:config (settings)")


def commands_footer_md():
    """The command list as a self-contained Markdown mini-table, emitted verbatim
    under the `/todo` board. Deliberately DECOUPLED from the ASCII
    `commands_footer()` one-liner (no longer derived by splitting it) so the
    Markdown surface can have its own two-column `Command | Action` shape."""
    return (
        "**Commands**\n"
        "\n"
        "| Command | Action |\n"
        "|---|---|\n"
        "| `/todo [<n>]` | list board / open & resume a task |\n"
        "| `/todo <n> -s` | jump into the task's session (new window) |\n"
        "| `/todo closed [N]` · `all` | list closed tasks |\n"
        "| `/done [<n,…>]` | close current / by number |\n"
        "| `/task-station:config` | settings |"
    )


# ---------------------------------------------------------------- storage ----
#
# The read/write layer lives in store.py — a SQLite backend (`<store>/tasks.db`)
# when sqlite3 is available, the original file-per-task JSON store as a fallback.
# The functions below keep their historical names/signatures so call sites (and
# the tests) don't change; each just delegates to the active backend. STORE is a
# module global the tests repoint, so resolve the backend per call against it.


def _backend():
    return store.get_backend(STORE)


def _ensure_dirs():
    _backend().ensure()


def _now():
    return time.time()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _iso_to_ts(s):
    """Epoch seconds for an ISO string written by _iso, or None if unparseable."""
    try:
        return datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def load_task(task_id):
    return _backend().load_task(task_id)


def save_task(task):
    task["updated_at"] = _iso(task["updated_ts"])
    _backend().save_task(task)


def all_tasks():
    return _backend().all_tasks()


def sorted_tasks():
    """Not-closed (open + active) before closed; within each, most recent
    activity first."""
    return sorted(
        all_tasks(),
        key=lambda t: (1 if is_closed(t) else 0, -t.get("updated_ts", 0)),
    )


def _max_seq(tasks=None):
    tasks = tasks if tasks is not None else all_tasks()
    return max((t.get("seq") or 0 for t in tasks), default=0)


def ensure_seqs():
    """Backfill stable per-task sequence numbers, assigned in creation order.

    Every task gets a permanent `seq` the first time it's seen — the number a
    user sees in `/todo` and types as `/todo <n>`. Unlike the old render-time
    index, a task keeps its number even as others are added, closed, or reorder
    by recent activity. Idempotent: tasks that already have a seq keep it.
    """
    tasks = all_tasks()
    missing = [t for t in tasks if not t.get("seq")]
    if not missing:
        return
    n = _max_seq(tasks)
    for t in sorted(missing, key=lambda t: t.get("created_ts", 0)):
        n += 1
        t["seq"] = n
        save_task(t)


# ------------------------------------------------------------------ links ----

def get_link(session):
    return _backend().get_link(session)


def set_link(session, task_id):
    _backend().set_link(session, task_id)


def clear_link(session):
    _backend().clear_link(session)


def live_session_count(task):
    """How many of this task's recorded sessions are STILL attached to it.

    `task["sessions"]` is append-only — it keeps every session that ever touched
    the task, even ones that later attached elsewhere, closed, or were skipped —
    so a raw `len()` over-reports. The live count is the sessions whose link
    currently resolves back to this task; that's the real concurrent-session
    signal /todo surfaces."""
    return _backend().live_session_count(task)


def get_count(session):
    """How many prompts this session has gone without attaching to a task."""
    return _backend().get_count(session)


def bump_count(session):
    return _backend().bump_count(session)


def clear_count(session):
    _backend().clear_count(session)


# -- edited / blocked markers: the "real work happened" enforcement signal -----
# A session that has EDITED a file but has no attached task is doing untracked
# work. `.edited` records that an edit happened; `.blocked` counts how many times
# the Stop gate has refused to let the turn end, so a non-complying loop can't
# wedge the session (we give up after STOP_GATE_MAX_BLOCKS).
STOP_GATE_MAX_BLOCKS = 2


def mark_edited(session):
    """Record that this session edited a file. Returns True only on the FIRST
    call (so the PostToolUse reminder is one-shot, not per-edit)."""
    return _backend().mark_edited(session)


def has_edited(session):
    return _backend().has_edited(session)


def get_blocked(session):
    return _backend().get_blocked(session)


def bump_blocked(session):
    return _backend().bump_blocked(session)


def clear_edit_markers(session):
    _backend().clear_edit_markers(session)


# -------------------------------------------------------------- utilities ----

def rel_time(ts):
    if not ts:
        return "—"
    d = max(0, int(_now() - ts))
    if d < 60:
        return "just now"
    if d < 3600:
        return "%dm ago" % (d // 60)
    if d < 86400:
        return "%dh ago" % (d // 3600)
    if d < 7 * 86400:
        return "%dd ago" % (d // 86400)
    return datetime.fromtimestamp(ts).strftime("%b %-d")


def resolve_ref(ref):
    """Resolve a /todo argument to a task dict.

    An all-digit ref is matched against tasks' stable `seq` numbers (the numbers
    shown in the listing). Anything else — or a digit string matching no seq —
    is matched against task ids by exact match or prefix, so a longer all-digit
    id prefix that happens to contain no hex letters (e.g. "03471986") still
    resolves correctly.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    ensure_seqs()
    listing = sorted_tasks()
    if ref.isdigit():
        i = int(ref)
        for t in listing:
            if t.get("seq") == i:
                return t
        # No task with that number: fall through and treat as an id prefix.
    for t in listing:
        if t["id"] == ref or t["id"].startswith(ref):
            return t
    return None


_DEDUP_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "all", "new", "add", "fix",
    "update", "make", "use", "via", "per", "out", "off", "this", "that",
}


def _norm_tokens(s):
    toks = re.findall(r"[a-z0-9]+", (s or "").lower())
    return {t for t in toks if len(t) > 2 and t not in _DEDUP_STOPWORDS}


def _norm_nums(s):
    """Numeric identifiers in a title (PR/bug/story numbers, phase numbers)."""
    return set(re.findall(r"\d+", s or ""))


def similar_open_task(title):
    """Return the most similar OPEN task if its title strongly overlaps `title`.

    Scores the larger of Jaccard overlap and containment (share of the new
    title's tokens already covered by an existing one). Containment only counts
    when at least two tokens are shared, so a one-word title can't match
    everything. Containment handles noise like a trailing "(PROJ-1234)" tag and
    singular/plural drift that would otherwise sink a pure-Jaccard score.

    Numeric IDs are treated as identity: if the new title carries number(s)
    (a PR/bug/story #) and a candidate shares none of them, they are different
    work items and the candidate is skipped — this stops short, generic titles
    ("Auto-review PR 697") from colliding on process words ("auto", "review")
    alone with an unrelated open task.
    """
    want = _norm_tokens(title)
    if not want:
        return None
    want_nums = _norm_nums(title)
    best, best_score = None, 0.0
    for t in sorted_tasks():
        if is_closed(t):
            continue
        cand_title = t.get("title", "")
        # A numbered new title only matches a candidate sharing one of its numbers.
        if want_nums and not (want_nums & _norm_nums(cand_title)):
            continue
        have = _norm_tokens(cand_title)
        if not have:
            continue
        inter = len(want & have)
        jaccard = inter / len(want | have)
        containment = inter / len(want) if inter >= 2 else 0.0
        score = max(jaccard, containment)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 0.6 else None


def add_log(task, note):
    note = (note or "").strip()
    if not note:
        return
    task.setdefault("log", []).append({"ts": _iso(_now()), "note": note[:NUDGE_PROMPT_MAX]})
    task["log"] = task["log"][-LOG_KEEP:]


def touch(task, session=None, note=None, reopen=False):
    task["updated_ts"] = _now()
    if reopen and is_closed(task):
        task["status"] = STATUS_OPEN          # reopening a closed task → open
    if session:
        if session not in task.get("sessions", []):
            task.setdefault("sessions", []).append(session)
        # Record where this session is running so /todo can later hand back a
        # `cd … && claude --resume …` one-liner that reopens it in the right dir.
        task.setdefault("session_meta", {})[session] = {"cwd": os.getcwd(), "ts": _now(), "role": "hub"}
    add_log(task, note)


def set_status(task, status, note=None):
    """Move a task between the settable board states (open ⇄ active). Idempotent —
    returns True only if it changed, logging the transition so the activity trail
    shows when work began. Refuses anything outside open/active (returns False) —
    closing goes through /done, not here — so a typo never mislabels a task."""
    if status not in STATUS_SETTABLE:
        return False
    if task_status(task) == status:
        return False
    task["status"] = status
    add_log(task, note or ("status → %s" % status))
    return True


def promote_active(task, note=None):
    """Promote an OPEN task to active because work has started. Idempotent — a
    no-op (returns False) when the task is already active or closed (an edit never
    resurrects a closed task)."""
    if task_status(task) != STATUS_OPEN:
        return False
    return set_status(task, STATUS_ACTIVE,
                      note=note or "auto-promoted to active (work started)")


def _project_dir_for(cwd):
    """The session-transcript bucket Claude Code uses for a given launch cwd."""
    return os.path.join(PROJECTS_ROOT, cwd.replace("/", "-"))


def _session_msgcount(path):
    """Count of non-empty, non-system user messages in a transcript (0 if unreadable).

    Used to tell a real working session from an empty/stray one — size alone lies
    (a freshly-spawned empty session can still be several KB of system init)."""
    n = 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                c = msg.get("content")
                if isinstance(c, str):
                    t = c
                elif isinstance(c, list):
                    t = " ".join(b.get("text", "") for b in c
                                 if isinstance(b, dict) and b.get("type") == "text")
                else:
                    t = ""
                t = t.strip()
                if t and not t.startswith("<"):
                    n += 1
    except OSError:
        return 0
    return n


def _find_session_path(sid):
    """Locate a session's transcript across ALL project buckets.

    A session's bucket is its LAUNCH cwd, which can differ from whatever cwd /todo
    happened to record (e.g. you launched from ~ but cd'd into a worktree before the
    task was touched). So we search every bucket by session id rather than trusting
    the recorded cwd. Returns the `.jsonl` path, or None."""
    try:
        buckets = os.listdir(PROJECTS_ROOT)
    except OSError:
        return None
    for b in buckets:
        p = os.path.join(PROJECTS_ROOT, b, sid + ".jsonl")
        if os.path.exists(p):
            return p
    return None


def _session_cwd(path):
    """The cwd a session was launched in, read from the transcript itself —
    authoritative (Claude Code records it on every entry), and decode-free (we never
    have to reverse the lossy bucket-name encoding). None if unreadable/absent."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


def _load_delegate_registry():
    try:
        with open(DELEGATE_REGISTRY) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def worker_lines(task):
    """Resume lines for the in-project workers this task has delegated into.

    Worker session-ids are read LIVE from the delegate registry (keyed
    <seq>:<project>[:<label>]) so they reflect delegate's own self-healing rather
    than a stale snapshot. Lists the default worker per repo plus any labelled
    concurrent ones. Returns [] when the task has no recorded projects."""
    projects = task.get("projects") or []
    if not projects:
        return []
    reg = _load_delegate_registry()
    seq = task.get("seq")
    out = []
    for p in projects:
        base = "%s:%s" % (seq, p)
        keys = sorted(k for k in reg if k == base or k.startswith(base + ":"))
        if not keys:
            out.append("    %-22s (no worker recorded yet)" % p)
            continue
        for k in keys:
            e = reg.get(k, {})
            d, sid = e.get("dir"), e.get("session_id")
            disp = p if k == base else "%s [%s]" % (p, e.get("label") or k.split(":", 2)[-1])
            if d and sid:
                out.append("    %-22s cd %s && claude --resume %s" % (disp, d, sid))
            elif d:
                out.append("    %-22s cd %s && claude   (no active worker yet)" % (disp, d))
            else:
                out.append("    %-22s (no worker recorded yet)" % disp)
    return out


def _is_resumable(cmd):
    """True when `cmd` already targets a CONCRETE session — a live `--resume` or a
    pre-bound `--session-id`. The jump path uses this to decide whether to use the
    command as-is or mint a fresh session; a descriptive "start fresh" line (bare
    `claude`) or None is NOT resumable."""
    return bool(cmd) and ("--resume " in cmd or "--session-id " in cmd)


def _fresh_session_cwd(meta):
    """Best cwd for a freshly minted session: the most recently recorded session's
    cwd (so the new window opens where the work lives), else the process cwd."""
    for m in sorted((meta or {}).values(), key=lambda m: m.get("ts", 0), reverse=True):
        if m.get("cwd"):
            return m["cwd"]
    return os.getcwd()


def fresh_resume_command(task, preborn=False):
    """Mint a brand-new session id, pre-bind it to `task`, and return
    `(sid, "cd <cwd> && claude --session-id <sid>")`.

    Pre-binding = a hub `session_meta` entry + a session→task link, so when the
    emitted command launches the window, SessionStart sees the link and
    auto-attaches it. This MINTS a uuid, so it is called ONLY from paths that
    actually open a window (the `-s` jump, `pin --new`) — never from the pure
    display path (`resume_command`), which must not mint on every render.

    `preborn=True` marks the meta entry so `resume_command` will emit
    `--session-id <sid>` for it (used by `pin --new`) until its transcript exists."""
    new_sid = str(uuid.uuid4())
    meta = task.setdefault("session_meta", {})
    cwd = _fresh_session_cwd(meta)
    entry = {"cwd": cwd, "ts": _now(), "role": "hub"}
    if preborn:
        entry["preborn"] = True
    meta[new_sid] = entry
    if new_sid not in task.setdefault("sessions", []):
        task["sessions"].append(new_sid)
    save_task(task)
    set_link(new_sid, task["id"])
    return new_sid, "cd %s && claude --session-id %s" % (cwd, new_sid)


def resume_command(task, current_session=None):
    """`cd <dir> && claude …` for the HUB session that holds this task's context.

    GUARANTEE: only ever resumes one of THIS task's own recorded sessions — never
    another task's. (Critical: every hub shares the home bucket, so a whole-bucket
    fallback or `claude --continue` could resume an unrelated task. We never do that.)

    SELF-CORRECTING cwd: the resume directory comes from the *transcript itself* (its
    recorded launch cwd), located by searching every bucket for the session id — NOT
    from whatever cwd /todo happened to capture. So a session is still found and
    resumed correctly even if it was recorded against the wrong directory (e.g. you
    launched from ~ but cd'd into a worktree before the task was touched). Prefers the
    most recent SUBSTANTIVE session (so merely opening `/todo <n>` to look — a 1-2
    message session — never displaces the real working session); if none of the
    task's sessions have a findable live transcript, starts fresh. Returns None only
    when there are no recorded sessions."""
    meta = task.get("session_meta") or {}
    if not meta:
        return None
    # The resumed window tints itself on attach via the SessionStart hook
    # (cmd_session_tint → tint_escape), so the resume command stays a clean
    # `cd … && claude …` with no tint prefix.
    # An explicit pin wins (PK-style): always resume that exact session, with the cwd
    # self-corrected from its transcript. Falls through to the heuristic only if the
    # pinned session has no findable live transcript (so a stale pin can't strand you).
    pin = task.get("pinned_session")
    if pin:
        path = _find_session_path(pin)
        if path and _session_msgcount(path) >= 1:
            cwd = _session_cwd(path) or (meta.get(pin) or {}).get("cwd")
            if cwd:
                return "cd %s && claude --resume %s" % (cwd, pin)
        # A pin deliberately pre-bound to an UNBORN session (`pin --new`) has no
        # transcript yet. Honour it anyway by emitting `--session-id <pin>` so the
        # window that opens BECOMES that session — stays PURE (the uuid already
        # exists; nothing is minted here). Once it's born, the branch above wins.
        pm = meta.get(pin) or {}
        if pm.get("preborn"):
            cwd = pm.get("cwd") or os.getcwd()
            return "cd %s && claude --session-id %s" % (cwd, pin)
    hubs = [(sid, m) for sid, m in meta.items() if m.get("role") == "hub"]
    pool = hubs or list(meta.items())
    # For each of THIS task's sessions, find its transcript ANYWHERE and read the
    # cwd from the transcript — independent of the (possibly wrong) recorded cwd.
    # SKIPPED sessions (link == SKIP_SENTINEL) are deliberately untracked and must
    # NEVER be a resume target, even with a live transcript.
    live = []
    for sid, m in pool:
        if get_link(sid) == SKIP_SENTINEL:
            continue
        path = _find_session_path(sid)
        if not path:
            continue
        msgs = _session_msgcount(path)
        if msgs >= 1:
            cwd = _session_cwd(path) or m.get("cwd")
            if cwd:
                live.append((sid, cwd, os.path.getmtime(path), msgs))
    # The current session is NEVER a valid `-s` target: resuming the very
    # conversation you jumped from is the tainting bug. Exclude it HARD (no
    # fallback to it) — if nothing else remains, fall through to fresh-start.
    live = [x for x in live if x[0] != current_session]
    if live:
        # Prefer SUBSTANTIVE sessions: a session that merely ran `/todo <n>` to look
        # has 1-2 messages and must not displace the real working session. Among
        # sessions past a small substance floor, take the most recent; only if none
        # clear the floor do we fall back to the most recent of any.
        cands = [x for x in live if x[3] >= SUBSTANCE_FLOOR] or live
        cands.sort(key=lambda x: x[2], reverse=True)   # newest transcript first
        sid, cwd, _, _ = cands[0]
        return "cd %s && claude --resume %s" % (cwd, sid)
    # No findable live transcript for any recorded session → fresh start
    # (NEVER --continue, which in the shared home bucket could resume a different task).
    pool.sort(key=lambda kv: kv[1].get("ts", 0), reverse=True)
    for sid, m in pool:
        if m.get("cwd"):
            return ("cd %s && claude   # no live session found — starting fresh; "
                    "re-attach with /todo %s" % (m["cwd"], task.get("seq", "")))
    return None


def new_task(title, summary, color=None, effort=None, status=STATUS_DEFAULT):
    ts = _now()
    t = {
        "id": str(uuid.uuid4()),
        "title": title.strip() or "Untitled task",
        "summary": summary.strip(),
        "status": status if status in STATUS_BOARD else STATUS_DEFAULT,
        "created_ts": ts,
        "created_at": _iso(ts),
        "updated_ts": ts,
        "updated_at": _iso(ts),
        "sessions": [],
        "log": [],
    }
    c = cat_color(color)
    if c is not None:
        t["color"] = c
    e = normalize_effort(effort)
    if e is not None:
        t["effort"] = e
    return t


# ------------------------------------------------------------- subcommands ----

def _is_substantive_tracked(session):
    """True when `session` is itself a real, tracked working conversation — linked
    to a live task (not unlinked, not skipped) AND past the substance floor. Used
    by `create` to avoid binding a busy parent conversation as a NEW task's resume
    target (the spun-off-task tainting bug)."""
    if not session:
        return False
    link = get_link(session)
    if not link or link == SKIP_SENTINEL:
        return False
    path = _find_session_path(session)
    return bool(path) and _session_msgcount(path) >= SUBSTANCE_FLOOR


def cmd_create(a):
    if not getattr(a, "force", False):
        dup = similar_open_task(a.title)
        if dup:
            attach_hint = ("attach --session %s --task %s" % (a.session, dup["id"][:8])
                           if getattr(a, "session", None)
                           else "attach --session <session-id> --task %s" % dup["id"][:8])
            print("Not created — likely a duplicate of open task [%s] %s.\n"
                  "Attach instead:  python3 %s/task-station.py %s\n"
                  "Or re-run create with --force to make a separate task."
                  % (dup["id"][:8], dup["title"], BASE, attach_hint))
            return
    requested = getattr(a, "color", None)
    if cats and requested and not cats.is_known(requested):
        print("⚠ --color '%s' is not a known category; defaulting to %s. "
              "Recategorize later with: attach --color <key|emoji|[TAG]>."
              % (requested, cats.DEFAULT))
    if getattr(a, "effort", None) and not normalize_effort(a.effort):
        print("⚠ --effort '%s' is not a known size; leaving it unset. "
              "Use xs/s/m/l/xl (or 1–5)." % a.effort)
    status = STATUS_ACTIVE if getattr(a, "active", False) else STATUS_DEFAULT
    task = new_task(a.title, a.summary, requested, getattr(a, "effort", None), status=status)
    ensure_seqs()                      # number any pre-seq tasks before we pick ours
    task["seq"] = _max_seq() + 1       # stable number, never reused even after /done

    session = getattr(a, "session", None)
    no_attach = getattr(a, "no_attach", False)
    # #6: creating from a SUBSTANTIVE tracked conversation defaults to no-attach so
    # the busy parent session isn't silently made the new task's resume target.
    # `--attach` forces the old bind-this-session behaviour; `--no-attach` is explicit.
    substantive = (not no_attach and not getattr(a, "attach", False)
                   and _is_substantive_tracked(session))
    if substantive:
        no_attach = True

    if no_attach or not session:
        # Unattached create: empty sessions[]/session_meta, no session→task link.
        # `/todo <n> -s` then has no recorded session and fresh-starts a clean one.
        touch(task, note="created (no-attach)")
        save_task(task)
        if substantive:
            print("⚠ Created from a substantive tracked session — NOT binding this "
                  "conversation as the new task's resume target (use --attach to "
                  "override). /todo %s -s starts a fresh session." % task["seq"])
        else:
            print("Created task [%s] %s (unattached). /todo %s -s starts a fresh "
                  "session." % (task["id"][:8], task["title"], task["seq"]))
        for line in cat_lines(task.get("color")):
            print(line)
        return

    touch(task, session=session, note="created")
    save_task(task)
    set_link(session, task["id"])
    clear_count(session)
    print("Created and attached to task [%s] %s" % (task["id"][:8], task["title"]))
    for line in cat_lines(task.get("color")):
        print(line)


def cmd_attach(a):
    task = resolve_ref(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    reopened = task.get("status") == "closed"
    # When categories are on: a recognized --color (re)categorizes the task —
    # this is how a task auto-tracked as the default 'general' gets corrected to
    # its real topic later. An unrecognized --color is REFUSED, not silently
    # mapped to the default, so a typo / stray emoji can't quietly mislabel the
    # task. With no --color we only backfill the default on a task that has none.
    if cats:
        requested = getattr(a, "color", None)
        if requested and cats.is_known(requested):
            task["color"] = cats.normalize(requested)
        elif requested:
            print("⚠ Ignoring --color '%s': not a known category. Use a key, "
                  "emoji, or [TAG] — e.g. brown, 🟤, or DATABASE. (Keeping %s.)"
                  % (requested, task.get("color") or cats.DEFAULT))
            if not task.get("color"):
                task["color"] = cats.DEFAULT
        elif not task.get("color"):
            task["color"] = cats.DEFAULT
    touch(task, session=a.session, note="attached", reopen=True)
    # --note folds a cross-session prompt into this task's activity log instead of
    # spawning a sibling task ("fold don't fork" — see commands/todo.md §grouping).
    note = getattr(a, "note", None)
    if note and note.strip():
        add_log(task, note.strip())
    save_task(task)
    set_link(a.session, task["id"])
    clear_count(a.session)
    print("Attached to task [%s] %s%s%s"
          % (task["id"][:8], task["title"], " (reopened)" if reopened else "",
             " (note appended)" if note and note.strip() else ""))
    for line in cat_lines(task.get("color")):
        print(line)


def cmd_bump(a):
    task_id = get_link(a.session)
    if not task_id:
        return
    task = load_task(task_id)
    if not task:
        return
    touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
    save_task(task)


def cmd_skip(a):
    set_link(a.session, SKIP_SENTINEL)
    clear_count(a.session)
    clear_edit_markers(a.session)   # skip is a deliberate opt-out — stop the gate nagging
    print("This session is marked untracked — the [task-station] nudge will stay silent. "
          "Attaching to or creating a task later resumes tracking.")


def cmd_detach(a):
    """Remove a session from a task's resume candidates.

    Drops `<session>` from the task's `sessions[]` and `session_meta`, clears
    `pinned_session` if it pointed at this session, and clears the session→task
    link if it still points here. `--task` selects the task; without it, the
    session's currently-linked task is used. Idempotent — a missing reference just
    reports "nothing to detach"."""
    session = a.session
    task = resolve_ref(a.task) or load_task(a.task) if getattr(a, "task", None) else None
    if not task:
        link = get_link(session)
        if link and link != SKIP_SENTINEL:
            task = load_task(link)
    if not task:
        print("detach: no task for session %s — pass --task <id-or-number>." % session[:8])
        return
    label = task.get("seq", task["id"][:8])
    cleared = []
    if session in task.get("sessions", []):
        task["sessions"].remove(session)
        cleared.append("sessions[]")
    meta = task.get("session_meta") or {}
    if session in meta:
        del meta[session]
        cleared.append("session_meta")
    if task.get("pinned_session") == session:
        task.pop("pinned_session", None)
        cleared.append("pin")
    if not cleared:
        print("Session %s was not attached to task %s — nothing to detach."
              % (session[:8], label))
        return
    touch(task, note="detached session %s" % session[:8])
    save_task(task)
    if get_link(session) == task["id"]:
        clear_link(session)
        clear_count(session)
        cleared.append("link")
    print("Detached session %s from task %s (cleared: %s)."
          % (session[:8], label, ", ".join(cleared)))


def _open_tasks_brief(limit=8):
    """A compact 'tasks on the board you might attach to' list for hook reasons."""
    rows = [t for t in sorted_tasks() if is_on_board(t)][:limit]
    return "\n".join("  - [%s] %s" % (t["id"][:8], t["title"]) for t in rows)


def cmd_mark_edited(a):
    """PostToolUse(Write|Edit|NotebookEdit): if this session edited a file but is
    NOT tracking a task, emit a one-shot reminder. Silent when already tracked,
    skipped, or already reminded — so it costs ~one injection per session, max."""
    if os.environ.get("TASK_STATION_GATE") == "off":
        return
    link = get_link(a.session)
    if link == SKIP_SENTINEL:      # session deliberately untracked — stay silent
        return
    if link:                       # attached to a real task — editing means work
        # has started, so promote an open task to active (idempotent), then
        # we're done (tracked sessions get no nudge).
        task = load_task(link)
        if task and promote_active(task):
            save_task(task)
        return
    if not mark_edited(a.session):  # one-shot: the reminder already fired
        return
    msg = (
        "[task-station] You just edited a file and this session is NOT tracking a task. "
        "This is exactly the work that should be tracked. Attach to an existing "
        "task or create one NOW (or `skip` if this is genuinely throwaway) — the "
        "Stop gate will otherwise refuse to end the turn until you do.\n"
        "Create:  python3 %s/task-station.py create --session %s --color <color> "
        "--effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'\n"
        "Attach:  python3 %s/task-station.py attach --session %s --task <id-or-number>\n"
        "Open tasks:\n%s"
        % (BASE, a.session, BASE, a.session, _open_tasks_brief() or "  (none)")
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": msg}}))


def cmd_stop_gate(a):
    """Stop hook: refuse to end the turn if this session edited files but never
    tracked a task. Self-healing — clears its markers the moment a task is
    attached or the session is skipped — and capped at STOP_GATE_MAX_BLOCKS so a
    non-complying loop can't wedge the session."""
    if os.environ.get("TASK_STATION_GATE") == "off":
        return
    if not has_edited(a.session):
        return                              # no untracked edits → nothing to enforce
    link = get_link(a.session)
    if link:                                # real task attached, or skipped
        clear_edit_markers(a.session)
        return
    if get_blocked(a.session) >= STOP_GATE_MAX_BLOCKS:
        clear_edit_markers(a.session)       # gave it two tries — don't wedge the session
        return
    bump_blocked(a.session)
    reason = (
        "This session edited files but is not tracking a /todo task. Before you "
        "finish, attach to an existing task or create one — or mark the session "
        "skipped if this edit is genuinely throwaway. Pick exactly one:\n"
        "  Create:  python3 %s/task-station.py create --session %s --color <color> "
        "--effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'\n"
        "  Attach:  python3 %s/task-station.py attach --session %s --task <id-or-number>\n"
        "  Skip:    python3 %s/task-station.py skip --session %s\n"
        "Open tasks:\n%s"
        % (BASE, a.session, BASE, a.session, BASE, a.session,
           _open_tasks_brief() or "  (none)")
    )
    print(json.dumps({"decision": "block", "reason": reason}))


def _split_refs(ref):
    """Split a `--task` value into individual refs: comma-separated, each
    whitespace-trimmed, empties dropped. A single ref is just a list of one.

    Shared by every batchable mutating subcommand (done / update / pin / unpin /
    add-project) so they all honor the same contract: one result line per ref, a
    bad ref reported but never aborting the rest."""
    return [r.strip() for r in (ref or "").split(",") if r.strip()]


def _close_one(ref, session):
    """Close a single task by seq/id ref and return one human result line.

    Detaches every session linked to the task so none can silently reopen it.
    Returns a no-match / already-closed / closed line — never raises — so a
    caller closing a comma list can keep going past a bad ref."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "No task matching '%s'." % ref
    if is_closed(task):
        return "Task [%s] %s is already closed." % (task["id"][:8], task["title"])
    task["status"] = STATUS_CLOSED              # close from open OR active
    touch(task, session=session, note="closed (by id)")
    save_task(task)
    # Detach EVERY session linked to this task so none can silently reopen it.
    for sess in list(task.get("sessions", [])):
        if get_link(sess) == task["id"]:
            clear_link(sess)
            clear_count(sess)
            clear_edit_markers(sess)   # closing is a deliberate wrap-up — don't let the gate block
    return "Closed task [%s] %s. Reopen later with /todo." % (task["id"][:8], task["title"])


def cmd_done(a):
    # Two modes:
    #   --task REF  → close any task by seq/id from anywhere (no session needed).
    #   --session   → close the task attached to this session (the /done path).
    ref = getattr(a, "task", None)
    if ref:
        # --task accepts a comma-separated list (e.g. "1,2,5"): close each ref,
        # print one result line per task, and tolerate a mix of valid/invalid —
        # a bad ref is reported but doesn't abort the rest. A single number is
        # just a list of one.
        refs = _split_refs(ref)
        if not refs:
            print("No task matching '%s'.\n\n%s" % (ref, _format_list()))
            return
        for r in refs:
            print(_close_one(r, a.session or None))
        return

    if not a.session:
        print("Pass --task <id-or-number> to close a specific task, "
              "or --session <id> to close the session's attached task.")
        return
    task_id = get_link(a.session)
    task = load_task(task_id) if task_id else None
    if not task:
        print("No task is attached to this session. Nothing to close.")
        return
    task["status"] = STATUS_CLOSED          # close from open OR active
    touch(task, session=a.session, note="closed")
    save_task(task)
    clear_link(a.session)   # detach so a later message can't silently reopen it
    clear_count(a.session)
    clear_edit_markers(a.session)   # deliberate wrap-up — don't let the Stop gate block
    print("Closed task [%s] %s and detached this session. Reopen later with /todo."
          % (task["id"][:8], task["title"]))


def _live_marker(task):
    """` ⧉N` when more than one session is concurrently attached to this task,
    else "". Every open task trivially has ≥1 live session, so the marker only
    appears on the interesting case (N > 1) and never clutters the common row."""
    n = live_session_count(task)
    return " ⧉%d" % n if n > 1 else ""


def _format_list(closed_limit=MAX_CLOSED_IN_LIST):
    # closed_limit caps how many closed tasks are shown (most recent first).
    # None means "show every closed task" (`/todo all`); an int shows that many
    # (`/todo closed` / `/todo closed N`). The default keeps the bare `/todo`
    # list short.
    ensure_seqs()                      # guarantee every task has its stable number
    listing = sorted_tasks()
    if not listing:
        return ("No tasks yet. One will be tracked automatically once the work "
                "in a session becomes clear, or say so explicitly.")
    lines = []
    closed_total = sum(1 for t in listing if is_closed(t))
    capped = closed_limit is not None and closed_total > closed_limit
    if capped:
        shown = 0
        trimmed = []
        for t in listing:
            if is_closed(t):
                shown += 1
                if shown > closed_limit:
                    continue
            trimmed.append(t)
        listing = trimmed
    # Two sections: the board (open + active, glyph-distinguished) then closed.
    last_section = None
    for t in listing:
        section = "CLOSED" if is_closed(t) else "OPEN"
        if section != last_section:
            lines.append("")
            lines.append(section)
            last_section = section
        tag = cat_tag(t.get("color"), pad=True)
        eff = effort_cell(t.get("effort"))
        marker = _live_marker(t)
        g = status_glyph(t)            # leading lifecycle glyph, before the number
        if tag:
            lines.append("%s %3d  %-40.40s  %s  %s  %s%s"
                         % (g, t["seq"], t["title"], tag, eff, rel_time(t.get("updated_ts")), marker))
        else:
            lines.append("%s %3d  %-40.40s  %s  %s%s"
                         % (g, t["seq"], t["title"], eff, rel_time(t.get("updated_ts")), marker))
    if capped:
        lines.append("     … %d older closed task(s) hidden  ·  show more with /todo closed N "
                     "or /todo all  ·  reachable by number: /todo <n> or /done <n>"
                     % (closed_total - closed_limit))
    lines.append("")
    lines.append(status_legend())
    lines.append(effort_legend())
    if cats:
        lines.append(cats.legend())
    lines.append(commands_footer())
    return ("Tasks (not-closed first, then by recent activity):\n"
            + "\n".join(lines))


def _md_escape(text):
    """Escape the characters that would break a GitHub table cell."""
    return (text or "").replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _md_effort(effort):
    """`▰▱` gauge + size for a Markdown cell — same content as the ASCII column
    (reuses effort_cell) but without its fixed-width padding."""
    return effort_cell(effort).rstrip()


def _md_task_row(task):
    """One GitHub-table row: `|  | # | Task | Category | Effort | Activity |`.
    The leading STATUS column holds the lifecycle glyph (`●` active / `○` open),
    EMPTY for closed tasks; the `#` cell holds the bare seq number only. The Task
    cell carries the ` ⧉N` live-session marker (when >1), mirroring the ASCII
    list; the Category cell keeps the `<emoji> [TAG]` intact."""
    status_cell = STATUS_GLYPH.get(task_status(task), "")   # "" for closed
    return "| %s | %s | %s | %s | %s | %s |" % (
        status_cell,
        task.get("seq", ""),
        _md_escape(task["title"]) + _live_marker(task),
        cat_tag(task.get("color")),
        _md_effort(task.get("effort")),
        rel_time(task.get("updated_ts")),
    )


_MD_HEADER = ("|  | # | Task | Category | Effort | Activity |\n"
              "|:-:|--:|------|----------|--------|----------|")


def _format_list_md(closed_limit=MAX_CLOSED_IN_LIST):
    """Markdown form of the /todo list — what the skill now prints VERBATIM (no
    hand-transcription). Two GitHub tables, Open first then Closed, preserving the
    tracker's ordering; columns are a centered STATUS glyph · # (stable seq,
    right-aligned) · Task · Category (`<emoji> [TAG]`) · Effort (`▰▱` bar + size)
    · Activity (relative time). Honors the same closed-limit logic as the ASCII
    list (default MAX_CLOSED_IN_LIST, `all`, or N) and repeats the hidden-older
    note after the Closed table, then the Commands footer mini-table."""
    ensure_seqs()
    listing = sorted_tasks()
    if not listing:
        return ("No tasks yet. One will be tracked automatically once the work "
                "in a session becomes clear, or say so explicitly.")
    board_tasks = [t for t in listing if is_on_board(t)]   # open + active
    closed_tasks = [t for t in listing if is_closed(t)]
    closed_total = len(closed_tasks)
    capped = closed_limit is not None and closed_total > closed_limit
    shown_closed = closed_tasks[:closed_limit] if capped else closed_tasks

    out = []
    if board_tasks:
        out.append("### Open")
        out.append(_MD_HEADER)
        out.extend(_md_task_row(t) for t in board_tasks)
    if shown_closed:
        if out:
            out.append("")
        out.append("### Closed")
        out.append(_MD_HEADER)
        out.extend(_md_task_row(t) for t in shown_closed)
    if capped:
        out.append("")
        out.append("… %d older closed task(s) hidden — show more with `/todo closed N` "
                   "or `/todo all`." % (closed_total - closed_limit))
    out.append("")
    out.append("_%s active · %s open · (closed below)_" % (STATUS_GLYPH[STATUS_ACTIVE], STATUS_GLYPH[STATUS_OPEN]))
    out.append(commands_footer_md())
    return "\n".join(out)


def _format_detail(task, session):
    out = []
    cur = task_status(task)
    # Header carries the glyph for board tasks (○ open / ● active); closed has none.
    glyph = (STATUS_GLYPH[cur] + " ") if cur in STATUS_GLYPH else ""
    out.append("Task [%s]  —  %s%s" % (task["id"][:8], glyph, cur.upper()))
    out.append("Title:   %s" % task["title"])
    if cats:
        out.append(cats.summary(task.get("color")))
    eff = task.get("effort")
    if eff in EFFORT_GAUGE:
        out.append("Effort:  %s %s (%s)" % (EFFORT_GAUGE[eff], eff, EFFORT_WORD[eff]))
    out.append("Created: %s (%s)" % (rel_time(task.get("created_ts")), task.get("created_at", "")))
    out.append("Updated: %s" % rel_time(task.get("updated_ts")))
    # Live = sessions still attached right now (link resolves back to this task);
    # total = every session that ever touched it (append-only, never pruned).
    out.append("Live sessions: %d  (of %d ever attached)"
               % (live_session_count(task), len(task.get("sessions", []))))
    out.append("")
    out.append("Summary:")
    out.append(task.get("summary") or "  (no summary recorded)")
    log = task.get("log", [])
    if log:
        out.append("")
        out.append("Recent activity (most recent last):")
        for e in log[-12:]:
            when = rel_time(_iso_to_ts(e.get("ts", "")))
            out.append("  • [%s] %s" % (when, e.get("note", "")))
    out.append("")
    out.append("This session is now ATTACHED to this task (id %s). Continue the work "
               "described above; the user's next message resumes it. To close it, use /done."
               % task["id"])
    resume = resume_command(task, session)
    workers = worker_lines(task)
    if resume or workers:
        out.append("")
        out.append("Resume the working session that holds this task's context "
                   "(cd + resume, one command):")
        if resume:
            out.append("    Hub%s:  %s" % (" (pinned)" if task.get("pinned_session") else "", resume))
        if workers:
            out.append("  In-project workers this task has delegated into "
                       "(drop into one directly to debug a repo):")
            out.extend(workers)
    return "\n".join(out)


def _open_jump_window(cmd):
    """Open a NEW Terminal.app window running `cmd` (the resume one-liner) and
    bring it to the front, via open-session-window.sh. The current window — the
    one /todo was typed in — is left untouched.

    Best-effort and macOS/Terminal.app-only: any failure (not darwin, osascript
    missing, AppleScript error, script absent) returns False so the caller falls
    back to just printing the command for the user to run by hand. Never raises."""
    if sys.platform != "darwin":
        return False
    script = os.path.join(BASE, "open-session-window.sh")
    if not os.path.exists(script):
        return False
    try:
        r = subprocess.run(["bash", script, cmd],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _format_detail_session(task, session, resume=None, opened=False):
    """Compact `/todo <n> -s` view: skip the recap and jump straight into the
    task's main connected working session.

    When `opened` is True we've ALREADY launched a fresh Terminal window running
    `resume` (the current window is left as-is), so we just confirm it. When it's
    False — no recorded session yet, or the auto-open failed — we print the
    one-liner for the user to run by hand. `resume` is the precomputed resume
    command (recomputed here if not supplied)."""
    out = []
    out.append("[SESSION-JUMP] Task [%s] — %s — %s"
               % (task["id"][:8], task["status"].upper(), task["title"]))
    out.append("")
    if resume is None:
        resume = resume_command(task, session)
    fresh = bool(resume) and "--session-id " in resume
    verb = "starting a fresh session for" if fresh else "resuming"
    if resume and opened:
        out.append("Opened a NEW Terminal window %s this task's working session "
                   "(this window is left as-is). Command now running there:" % verb)
        out.append("    %s" % resume)
        out.append("")
        out.append("[JUMP-WINDOW-OPENED] The jump window is already running the "
                   "command. Reply with EXACTLY this one line and nothing else (no "
                   "preamble, recap, or extra words); do not run the command yourself:")
        out.append("    ↪ " + task_oneline(task))
    elif resume:
        label = ("Start a fresh session for this task (cd + new session, one command):"
                 if fresh else "Resume the main connected session (cd + resume, one command):")
        out.append(label)
        out.append("    %s" % resume)
    else:
        out.append("No recorded working session to resume yet — start one in the "
                   "task's directory, or run `/todo %s` for the full detail."
                   % task.get("seq", task["id"][:8]))
    return "\n".join(out)


def _jump_one(ref, session):
    """Attach `session` to the task named by `ref`, open a fresh jump window for
    it, and return its `[SESSION-JUMP]` block. Used per-ref so `/todo <n,n…> -s`
    can jump into several tasks at once (one window + one block per task).

    Returns a no-match line (never raises) so a bad ref in a comma list is
    reported without aborting the others."""
    task = resolve_ref(ref)
    if not task:
        return "No task matching '%s'." % ref
    touch(task, session=session, note="resumed", reopen=True)
    save_task(task)
    set_link(session, task["id"])
    clear_count(session)
    resume = resume_command(task, session)
    # No concrete session to resume (no recorded one, or the only candidate was
    # THIS session) → mint + pre-bind a fresh one so the jump window auto-attaches
    # to a clean session instead of tainting into the current conversation.
    if not _is_resumable(resume):
        _sid, resume = fresh_resume_command(task)
    opened = _open_jump_window(resume) if resume else False
    return _format_detail_session(task, session, resume=resume, opened=opened)


def _parse_session_flag(arg):
    """Pull a `-s` / `--session` token out of a /todo arg (e.g. `1 -s` or `-s 1`).

    `-s` means "jump straight into the task's connected working session" — emit
    the resume one-liner and skip the recap. Returns (clean_arg, session) where
    clean_arg has the flag removed so it still resolves to the task number/id.
    The flag may sit on either side of the number; only a bare `-s`/`--session`
    token counts, never a substring of an id."""
    toks = (arg or "").split()
    session = False
    kept = []
    for t in toks:
        if t in ("-s", "--session"):
            session = True
        else:
            kept.append(t)
    return " ".join(kept), session


DEFAULT_CLOSED_LIST = 20  # how many closed tasks `/todo closed` (no count) shows


def _parse_list_arg(arg):
    """Recognize the listing keywords `closed [N]` and `all`.

    Returns the closed-task limit to pass to _format_list (None = show every
    closed task) when `arg` is a listing request, or False when it isn't (so
    the caller falls through to treating `arg` as a task ref). `closed` with no
    count uses DEFAULT_CLOSED_LIST; `closed N` uses N; `all` shows everything.
    """
    toks = arg.lower().split()
    if not toks:
        return False
    if toks[0] == "all":
        return None
    if toks[0] in ("closed", "recent"):
        if len(toks) > 1 and toks[1].isdigit():
            return max(1, int(toks[1]))
        return DEFAULT_CLOSED_LIST
    return False


def _print_list_footer():
    """Opt-in (default off) update nudge, list view only. Silent when off/up-to-date."""
    import update_check
    line = update_check.nudge_line()
    if line:
        print(line)


def cmd_render(a):
    # --format md makes the LIST branches emit GitHub-flavored Markdown tables the
    # skill prints verbatim (no hand-transcription). Detail and session-jump
    # branches are unaffected — they stay ASCII for this PR.
    md = getattr(a, "format", None) == "md"
    _fmt_list = _format_list_md if md else _format_list
    arg, jump = _parse_session_flag((a.arg or "").strip())
    if not arg:
        print(_fmt_list())
        _print_list_footer()
        return
    closed_limit = _parse_list_arg(arg)
    if closed_limit is not False:
        print(_fmt_list(closed_limit=closed_limit))
        _print_list_footer()
        return
    if jump:
        # -s: jump straight into the task's working session in a FRESH window
        # (leaving this one untouched). The ref before -s may be a comma list
        # (`/todo 1,2,5 -s`): attach + open one window and emit one
        # [SESSION-JUMP] block PER task. A single number is just a list of one.
        # Opening happens here so it's immediate and deterministic; each block
        # falls back to printing its one-liner if its window can't open.
        refs = [r.strip() for r in arg.split(",") if r.strip()]
        if not refs:
            print("No task matching '%s'.\n\n%s" % (arg, _fmt_list()))
            return
        print("\n\n".join(_jump_one(r, a.session) for r in refs))
        return
    task = resolve_ref(arg)
    if not task:
        print("No task matching '%s'.\n\n%s" % (arg, _fmt_list()))
        return
    touch(task, session=a.session, note="resumed", reopen=True)
    save_task(task)
    set_link(a.session, task["id"])
    clear_count(a.session)
    print(_format_detail(task, a.session))


def _add_project_one(ref, project):
    """Record `project` on the task named by `ref` (idempotent). Returns an error
    line for a bad ref, or None on success (success stays silent — this is
    machine-called by delegate)."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "add-project: no task matching %r" % ref
    projs = task.setdefault("projects", [])
    if project not in projs:
        projs.append(project)
        task["updated_ts"] = _now()
        save_task(task)
    return None


def cmd_add_project(a):
    """Record that a task has delegated work into a repo (project). Idempotent.

    Called by delegate.py when a worker is spawned with --seq, so /todo can
    list the task's in-project workers in its detail view. No session attach, no
    activity-log entry — keeps the link bookkeeping quiet. `--task` accepts a
    comma-separated list (record the project on several tasks at once); a bad ref
    is reported on stderr without aborting the rest."""
    refs = _split_refs(a.task)
    if not refs:
        sys.stderr.write("add-project: no task given\n")
        return
    for r in refs:
        err = _add_project_one(r, a.project)
        if err:
            sys.stderr.write(err + "\n")


def cmd_status(a):
    """Show or set a task's lifecycle status between the board states (○ open /
    ● active). `status --task <ref>` with no value reports the current status;
    `status --task <ref> open|active` sets it (idempotent). Closing goes through
    /done, not here — a closed task is reported but not settable from here."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    value = getattr(a, "value", None)
    cur = task_status(task)
    if not value:
        glyph = STATUS_GLYPH.get(cur, "")
        print("Task [%s] %s — status: %s %s"
              % (task["id"][:8], task["title"], glyph, cur))
        return
    value = value.strip().lower()
    if value not in STATUS_SETTABLE:
        if value == STATUS_CLOSED:
            print("status: close a task with /done (or `done --task %s`), not `status`."
                  % task.get("seq", task["id"][:8]))
        else:
            print("status: unknown status '%s' — use 'open' or 'active'." % value)
        return
    if is_closed(task):
        print("Task [%s] %s is closed — reopen it via /todo %s first."
              % (task["id"][:8], task["title"], task.get("seq", task["id"][:8])))
        return
    if set_status(task, value, note="status set to %s (manual)" % value):
        save_task(task)
        print("Task [%s] %s → %s %s"
              % (task["id"][:8], task["title"], STATUS_GLYPH[value], value))
    else:
        print("Task [%s] %s already %s %s."
              % (task["id"][:8], task["title"], STATUS_GLYPH[value], value))


def cmd_session_title(a):
    """Print the window/title-bar label for an attached session (or nothing).

    The SessionStart hook puts this in hookSpecificOutput.sessionTitle so the
    terminal reads `#<seq>: <title>` — the closest we get to auto-labelling
    the hub (the resume-NAME can't be set programmatically on a running session)."""
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task:
        return
    ensure_seqs()
    print("#%s: %s" % (task.get("seq", "?"), task["title"]))


def cmd_whoami(a):
    """Map any session id → its task. The backstop that identifies a session
    regardless of whether it was ever named."""
    task_id = get_link(a.session)
    porcelain = getattr(a, "porcelain", False)
    if task_id == SKIP_SENTINEL:
        if not porcelain:
            print("session %s: intentionally untracked (skipped)" % a.session[:8])
        return
    statusline = getattr(a, "statusline", False)
    task = load_task(task_id) if task_id else None
    if not task:
        if not porcelain and not statusline:
            print("session %s: not attached to any task" % a.session[:8])
        return
    ensure_seqs()
    if porcelain:
        # Machine-readable: just the seq, for scripts (e.g. delegate auto-inherit).
        print(task.get("seq", ""))
        return
    if statusline:
        # A ready-to-display, ANSI-colored segment for a status bar —
        # '#<seq>  <dot> [TAG]  <title>'. Self-contained: knows nothing about who
        # renders it. Honors --width (>0) by truncating the title so the whole
        # segment fits that many columns; --width 0 means no limit.
        print(statusline_segment(task, getattr(a, "width", 0)))
        return
    print("session %s → task-station %s · %s (%s)"
          % (a.session[:8], task.get("seq", "?"), task["title"], task["status"]))


def _update_one(ref, a):
    """Apply the update flags to the single task named by `ref` and return its
    result line(s). Never raises — a bad ref returns a no-match line — so a caller
    updating a comma list keeps going past it. The SAME flags are applied to every
    task in a batch (e.g. set several tasks' colour at once)."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "update: no task matching %r" % ref
    msgs = []
    changed = []
    if a.title is not None:
        task["title"] = a.title.strip(); changed.append("title")
    if a.summary is not None:
        task["summary"] = a.summary.strip(); changed.append("summary")
    if a.append_summary:
        base = (task.get("summary") or "").rstrip()
        add = a.append_summary.strip()
        task["summary"] = (base + "\n" + add) if base else add
        changed.append("summary+")
    if a.color is not None and cats:
        task["color"] = cat_color(a.color); changed.append("color")
    if a.effort is not None:
        e = normalize_effort(a.effort)
        if e is None:
            msgs.append("update: ignoring --effort %r — use xs/s/m/l/xl (or 1–5)." % a.effort)
        else:
            task["effort"] = e; changed.append("effort")
    label = task.get("seq", task["id"][:8])
    if not changed:
        msgs.append("update %s: nothing to change (pass --title/--summary/--append-summary/--color/--effort)" % label)
        return "\n".join(msgs)
    touch(task, note="scope updated: " + ", ".join(changed))
    save_task(task)
    msgs.append("updated task %s: %s" % (label, ", ".join(changed)))
    # A scope change is the moment effort might have grown or shrunk — prompt a
    # re-rate so the column tracks reality, but only when this update touched
    # scope WITHOUT already re-rating (so re-setting effort itself stays quiet).
    if {"title", "summary", "summary+"} & set(changed) and "effort" not in changed:
        cur = task.get("effort")
        shown = ("currently %s %s" % (EFFORT_GAUGE[cur], cur)) if cur in EFFORT_GAUGE else "currently unset"
        msgs.append("  ↳ scope changed (%s). If the work now looks bigger or smaller, re-rate:\n"
                    "      python3 %s/task-station.py update --task %s --effort <xs|s|m|l|xl>"
                    % (shown, BASE, label))
    return "\n".join(msgs)


def cmd_update(a):
    """Amend a task's title / summary / scope / colour after creation.

    Fills the gap that `summary` was otherwise frozen at create — keeps the task
    description current as scope drifts. `--task` accepts a comma-separated list:
    the same flags are applied to each task, one result line per ref, a bad ref
    reported but not aborting the rest."""
    refs = _split_refs(a.task)
    if not refs:
        sys.stderr.write("update: no task given\n")
        return
    for r in refs:
        print(_update_one(r, a))


def _pin_one(ref, a):
    """Pin session `a.session` as the resume target for the task named by `ref`
    and return its result line. A bad ref returns a no-match line (never raises)
    so a comma list keeps going past it."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "pin: no task matching %r" % ref
    # `pin --new`: pre-bind an UNBORN session as the pin. Mints a uuid, records it
    # (preborn) + links it, and emits `claude --session-id <uuid>` so opening it
    # BECOMES the task's session — bypassing the stale-pin "no transcript" guard
    # for this intentional case.
    if getattr(a, "new", False):
        sid, cmd = fresh_resume_command(task, preborn=True)
        task["pinned_session"] = sid
        touch(task, note="pinned a fresh (unborn) session %s" % sid[:8])
        save_task(task)
        label = task.get("seq", task["id"][:8])
        return ("Pinned task %s → fresh session %s (unborn — opens on first launch)\n"
                "  resume: %s" % (label, sid[:8], cmd))
    if not a.session:
        return "pin: task %s needs --session <id> or --new" % task.get("seq", task["id"][:8])
    task["pinned_session"] = a.session
    meta = task.setdefault("session_meta", {})
    if a.session not in meta:
        path = _find_session_path(a.session)
        meta[a.session] = {"cwd": (_session_cwd(path) if path else None) or os.getcwd(),
                           "ts": _now(), "role": "hub"}
    touch(task, note="pinned resume session %s" % a.session[:8])
    save_task(task)
    label = task.get("seq", task["id"][:8])
    if _find_session_path(a.session):
        return ("Pinned task %s → session %s\n  resume: %s"
                % (label, a.session[:8], resume_command(task)))
    return ("Pinned task %s → session %s — note: no transcript found for that id yet; "
            "/todo falls back to the heuristic until it appears." % (label, a.session[:8]))


def cmd_pin(a):
    """Pin a specific session as the task's canonical resume target (PK-style).

    `/todo` then always resumes THIS session, overriding the most-recent-substantive
    heuristic — the cwd is still read live from the transcript, so the pin survives
    directory changes. A pin with no findable live transcript is ignored (falls back
    to the heuristic) so it can't strand you. `--task` accepts a comma-separated
    list (pin the session across several tasks), one result line per ref."""
    refs = _split_refs(a.task)
    if not refs:
        sys.stderr.write("pin: no task given\n")
        return
    for r in refs:
        print(_pin_one(r, a))


def _unpin_one(ref):
    """Drop the pinned resume session on the task named by `ref` and return its
    result line. A bad ref returns a no-match line (never raises)."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "unpin: no task matching %r" % ref
    if task.pop("pinned_session", None):
        touch(task, note="unpinned resume session")
        save_task(task)
        return ("Unpinned task %s — resume reverts to most-recent-substantive."
                % task.get("seq", task["id"][:8]))
    return "Task %s was not pinned." % task.get("seq", task["id"][:8])


def cmd_unpin(a):
    """Drop a task's pinned resume session — revert to most-recent-substantive.

    `--task` accepts a comma-separated list (unpin several at once), one result
    line per ref, a bad ref reported but not aborting the rest."""
    refs = _split_refs(a.task)
    if not refs:
        sys.stderr.write("unpin: no task given\n")
        return
    for r in refs:
        print(_unpin_one(r))


def cmd_prompt_color(a):
    """Print the category colour a skill-invocation prompt maps to (or nothing).

    The UserPromptSubmit hook calls this FIRST and, if it prints a colour, tints
    the terminal straight away — so a skill like /review tints the terminal the
    instant it's run, before Claude responds. Silent (prints nothing) when
    categories are off, the prompt isn't a skill, or the skill has no mapping."""
    if not cats or not hasattr(cats, "color_for_prompt"):
        return
    prompt = a.prompt if getattr(a, "prompt", None) is not None else os.environ.get("TASK_STATION_PROMPT", "")
    color = cats.color_for_prompt(prompt)
    if color:
        print(color)


def cmd_prompt_tint(a):
    """Like prompt-color, but emit the actual full-palette tint escape for the
    detected terminal (zero-setup OSC; see categories.tint_escape). The
    UserPromptSubmit hook writes whatever this prints to the originating TTY."""
    if os.environ.get("TASK_STATION_TINT") == "off":
        return
    if not cats or not hasattr(cats, "color_for_prompt") or not cats.TINT_TERMINAL:
        return
    prompt = a.prompt if getattr(a, "prompt", None) is not None else os.environ.get("TASK_STATION_PROMPT", "")
    color = cats.color_for_prompt(prompt)
    if not color:
        return
    import config, term
    esc = cats.tint_escape(color, config.tint_mode(), term.detect())
    if esc:
        sys.stdout.write(esc)


def cmd_session_tint(a):
    """Emit the full-palette tint escape for the ATTACHED task's category, so the
    terminal tints on attach/resume (not only on the first prompt). Mirrors
    prompt-tint but resolves the colour from the session's task instead of the
    prompt. Silent when tinting is off, the session is unattached/skipped, or the
    task carries no colour; the SessionStart hook writes the bytes to the TTY."""
    if os.environ.get("TASK_STATION_TINT") == "off":
        return
    if not cats or not getattr(cats, "TINT_TERMINAL", False):
        return
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task or not task.get("color"):
        return
    import config, term
    esc = cats.tint_escape(task.get("color"), config.tint_mode(), term.detect())
    if esc:
        sys.stdout.write(esc)


def cmd_prompt_title(a):
    """Emit an OSC title escape that labels the terminal tab/window `#<seq>: <title>`
    for an attached session — the on-attach surface, run by UserPromptSubmit every
    prompt. Pure stdout (like prompt-tint); the hook delivers the bytes to the real
    terminal. Emits NOTHING when the title feature is off (config / `TASK_STATION_TITLE=off`)
    or the session is unattached/skipped, so the user's own title is left untouched."""
    import config
    if not config.title_enabled():
        return
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task:
        return
    ensure_seqs()
    # OSC 0 sets both tab and window title (Terminal.app + iTerm2); \033]0; … \007.
    sys.stdout.write("\033]0;#%s: %s\007" % (task.get("seq", "?"), task["title"]))


def cmd_prompt_context(a):
    """UserPromptSubmit: bump if attached; otherwise nudge Claude to attach/create.

    An EXPLICIT 'create/attach a task' request in the prompt always prints a hard
    directive FIRST — even in a skipped or already-attached session — steering to
    task-station and away from the built-in/native TaskCreate session-todo tool."""
    intent = (cats.task_intent(os.environ.get("TASK_STATION_PROMPT", ""))
              if (cats and hasattr(cats, "task_intent")) else None)
    task_id = get_link(a.session)
    skipped = task_id == SKIP_SENTINEL
    task = load_task(task_id) if (task_id and not skipped) else None

    if intent:
        verb = "attach" if intent == "attach" else "create"
        dlines = [
            "⚡ EXPLICIT TASK INTENT — the user's message explicitly asks to %s a task." % verb,
            "Use task-station NOW. Do NOT use the built-in/native TaskCreate (session todo) tool — that",
            "list is ephemeral (this-conversation-only) and is NOT what the user wants.",
        ]
        if intent == "create":
            dlines.append("  create: python3 %s/task-station.py create --session %s --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                          % (BASE, a.session))
            if task:
                dlines.append("You are already attached to [%s] %s; if the user wants a SEPARATE task, "
                              "create with --force; if they mean this same task, you are already tracking "
                              "it — just say so." % (task["id"][:8], task["title"]))
        else:  # attach
            dlines.append("  attach: python3 %s/task-station.py attach --session %s --task <task-id> [--color <color>]"
                          % (BASE, a.session))
            opens = [t for t in sorted_tasks() if is_on_board(t)]
            if opens:
                dlines.append("Open tasks you can attach to:")
                for t in opens[:8]:
                    dlines.append("  - [%s] %s (%s)" % (t["id"][:8], t["title"], rel_time(t.get("updated_ts"))))
        print("\n".join(dlines))
        # The directive IS the message. Keep an attached task's activity fresh as
        # usual, but don't also dump the standard nudge after a hard directive.
        if task:
            touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
            save_task(task)
        return

    if skipped:
        return  # session intentionally untracked: stay silent

    if task:
        was_closed = task.get("status") == "closed"
        touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
        save_task(task)
        if was_closed:
            print("[task-station] Reopened task [%s] %s — this session is working on it again."
                  % (task["id"][:8], task["title"]))
        return  # attached & open: stay silent to avoid clutter

    # Not attached: count the miss, surface open tasks, and nudge Claude.
    n = bump_count(a.session)

    # Intermediate misses (1 < n < NUDGE_ESCALATE_AFTER): a SINGLE compact line.
    # The full block — open-task list, attach/create syntax, colour legend, tint,
    # guidance pointer — was already shown at n == 1, so reprinting it every
    # message just burns tokens. The first miss and the escalation still get the
    # full block (below).
    if 1 < n < NUDGE_ESCALATE_AFTER:
        line = ("[task-station] Still untracked (msg %d). Track the topic as an OPEN task "
                "(○) — or fold it into a task above with `attach --note` — else skip." % n)
        # Category auto-detection is a compiled-regex + dict lookup — effectively
        # free — so it keeps running on EVERY prompt, even the collapsed nudge. If
        # this prompt maps to a category, carry just that one hint (no legend) so a
        # later attach can still auto-categorize.
        if cats and hasattr(cats, "color_for_prompt"):
            skill_color = cats.color_for_prompt(os.environ.get("TASK_STATION_PROMPT", ""))
            if skill_color:
                line += (" This prompt maps to category '%s' (%s) — use --color %s on attach."
                         % (skill_color, cats.label(skill_color), skill_color))
        print(line)
        return

    opens = [t for t in sorted_tasks() if is_on_board(t)]
    lines = ["[task-station] This session is not attached to a tracked task yet."]
    if opens:
        lines.append("Open tasks that may match what the user wants:")
        for t in opens[:8]:
            lines.append("  - [%s] %s (%s)" % (t["id"][:8], t["title"], rel_time(t.get("updated_ts"))))
    lines.append("")

    if n >= NUDGE_ESCALATE_AFTER:
        lines.append("⚠ %d messages in and still untracked. If this session is doing real "
                     "work, attach or create a task NOW. If it is just Q&A, silence this with:" % n)
        lines.append("      python3 %s/task-station.py skip --session %s" % (BASE, a.session))
        lines.append("")

    # Compact form: full rules/examples live in `task-station.py guidance` (and the
    # SessionStart injection points there) — keep the per-prompt cost minimal.
    lines.append("Track this topic NOW as an OPEN task (○) — even a question counts; it "
                 "shows on the board immediately and AUTO-PROMOTES to active (●) when you act "
                 "on it (edit a file, delegate, multi-step). FIRST scan the tasks above: if "
                 "this prompt continues one of them, FOLD INTO IT — `attach --session %s --task "
                 "<id> --note '<this prompt>'` — don't create a sibling. Only a genuinely new "
                 "topic creates a task. (Skip only if it's truly throwaway/meta.)" % a.session)
    if cats:
        skill_color = (cats.color_for_prompt(os.environ.get("TASK_STATION_PROMPT", ""))
                       if hasattr(cats, "color_for_prompt") else None)
        if skill_color:
            lines.append("This prompt's skill maps to category '%s' (%s); terminal already tinted — "
                         "use --color %s."
                         % (skill_color, cats.label(skill_color), skill_color))
        lines.append("  attach: python3 %s/task-station.py attach --session %s --task <task-id> [--color <color>]" % (BASE, a.session))
        lines.append("  create: python3 %s/task-station.py create --session %s --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                     % (BASE, a.session))
        legend = cats.compact_legend() if hasattr(cats, "compact_legend") else ""
        if legend:
            lines.append("Colors: " + legend)
        lines.append("Tell the user in one short line (\"📋 Tracking: <title>\"). "
                     "The terminal tints to the category automatically. "
                     "Full rules: python3 %s/task-station.py guidance" % BASE)
    else:
        lines.append("  attach: python3 %s/task-station.py attach --session %s --task <task-id>" % (BASE, a.session))
        lines.append("  create: python3 %s/task-station.py create --session %s --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                     % (BASE, a.session))
        lines.append("Tell the user in one short line (\"📋 Tracking: <title>\"). "
                     "Full rules: python3 %s/task-station.py guidance" % BASE)
    print("\n".join(lines))


def cmd_guidance(a):
    """Full attach/create how-to, fetched on demand (kept out of the per-prompt
    injection for token economy — `prompt-context` points here)."""
    lines = ["[task-station] Every topic gets tracked from the first prompt — TRACK, don't stay silent:",
             "  - STATUS: a topic you merely raise starts OPEN (○) — track it now, even a plain question.",
             "    It shows on the board immediately and AUTO-PROMOTES to ACTIVE (●) when work starts",
             "    (you edit a file in this session, delegate --worktree, or run a multi-step process).",
             "    /done then closes it. Status is one field: open (○) → active (●) → closed.",
             "  - FOLD, DON'T FORK: before creating, scan the board (open + active). If this prompt",
             "    continues an existing task, ATTACH to it and append the prompt as a note — no sibling.",
             "  - write a one-line title good enough to recognise the topic later.",
             'TRACK examples:  "how does X work?" (open), "add dark mode", "fix the auth bug"',
             "FOLD example:    a follow-up question about a task on the board → attach --note, not a new task",
             "SKIP only genuinely throwaway/meta chatter: python3 %s/task-station.py skip --session <session-id>" % BASE]
    if cats:
        lines.extend(cats.picker_lines())
        lines.append("  • Matches a task on the board → attach (FOLD IN; --note appends this prompt to its log; "
                     "--color sets/recategorizes — a key, emoji, or [TAG]):")
        lines.append("      python3 %s/task-station.py attach --session <session-id> --task <task-id> [--note '<prompt>'] [--color <color>]" % BASE)
        lines.append("  • Otherwise → create with its colour and an effort estimate "
                     "(xs/s/m/l/xl — your read of the task's complexity & scope). New tasks "
                     "start open (○); add --active to start active (●) when work has already begun:")
        lines.append("      python3 %s/task-station.py create --session <session-id> --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentence summary>' [--active]"
                     % BASE)
        if cats.TINT_TERMINAL:
            lines.append("The terminal is tinted to the task's category automatically "
                         "(full palette via terminal escapes) — nothing to run by hand.")
    else:
        lines.append("  • attach: python3 %s/task-station.py attach --session <session-id> --task <task-id>" % BASE)
        lines.append("  • create: python3 %s/task-station.py create --session <session-id> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentence summary>'" % BASE)
    lines.append("Always track via task-station (attach/create above) — NEVER the built-in/native "
                 "TaskCreate session-todo tool; that list is ephemeral and invisible later.")
    lines.append("Do this as a side action, but DO tell the user in one short line when you "
                 "create or attach a task — e.g. \"📋 Tracking this as a new task: <title>\" or "
                 "\"📋 Attached to existing task: <title>\".")
    print("\n".join(lines))


def _repos_load(repo_index, roots, data_dir):
    """Return the structured index, reading repos.json if present, else building
    it from a fresh scan (so term/--json queries work before a first --refresh)."""
    p = os.path.join(data_dir, "repos.json")
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        # Auto-build on a read path: stay deterministic (no model calls); explicit
        # `repos --refresh` is the only place enrichment runs.
        return repo_index.build_index(roots, data_dir=data_dir, use_llm=False)


def _repos_render_manifest(repo_index, data_dir):
    """Print the full include/exclude manifest — every discovered repo + flags."""
    manifest = repo_index.load_manifest(data_dir)
    print("repo manifest  %s" % os.path.join(data_dir, "repos.config.json"))
    print("  toggle: repos include/exclude <name>  ·  repos enrich <name> [on|off]")
    print("")
    if not manifest:
        print("  (empty — run `repos --refresh` to discover repos)")
        return
    print("  index  enrich  repo")
    for name in sorted(manifest):
        e = repo_index.entry_for(manifest, name)
        print("  %-6s %-7s %s" % (
            "[x]" if e["index"] else "[ ]",
            "[x]" if e["enrich"] else "[ ]",
            name + ("" if e["index"] else "   (excluded)")))


def _repos_set_flag(repo_index, data_dir, name, key, value):
    """Set a manifest flag for `name` (or its basename if a path was given).
    Returns the resolved repo name, or None if it isn't in the manifest."""
    manifest = repo_index.load_manifest(data_dir)
    candidate = name if name in manifest else os.path.basename(os.path.normpath(name))
    if candidate not in manifest:
        return None
    entry = repo_index.entry_for(manifest, candidate)
    entry[key] = value
    manifest[candidate] = entry
    repo_index.save_manifest(data_dir, manifest)
    return candidate


def _repos_manifest_action(repo_index, data_dir, action, terms):
    """Handle the no-JSON-editing toggle subcommands. Returns True if it consumed
    the invocation."""
    if action == "config":
        _repos_render_manifest(repo_index, data_dir)
        return True
    if len(terms) < 2:
        print("usage: repos %s <name>%s" % (
            action, " [on|off]" if action == "enrich" else ""))
        return True
    name = terms[1]
    if action == "include":
        key, value, label = "index", True, "included (index:true)"
    elif action == "exclude":
        key, value, label = "index", False, "excluded (index:false)"
    else:  # enrich
        on = (terms[2].lower() != "off") if len(terms) > 2 else True
        key, value, label = "enrich", on, "enrich:%s" % ("on" if on else "off")
    resolved = _repos_set_flag(repo_index, data_dir, name, key, value)
    if resolved is None:
        print("repos: no repo named %r in the manifest. Run `repos --refresh` to "
              "discover repos, then `repos config` to list them." % name)
    else:
        print("repos: %s → %s" % (resolved, label))
        if action == "enrich" and value:
            print("       (its README + tree NAMES will be sent to the model on the "
                  "next `repos --refresh`)")
    return True


def cmd_repos(a):
    """Hub repo index: `repos [show]` prints repos.md (building it if missing),
    `repos --refresh [--force] [--quiet] [--dry-run] [--re-summarize]` rescans +
    rewrites the index, `repos <term...>` ranks matches, `--json` emits the
    structured list. Include/exclude surface: `repos config` lists the manifest;
    `repos include/exclude <name>` and `repos enrich <name> [on|off]` flip flags.
    First-run onboarding via `--detect-roots` + `--set-roots`. Not stored in
    tasks.db; lives at <data_dir>/repos.{md,json} + repos.config.json."""
    import config
    import repo_index
    data_dir = paths.data_dir()
    md_path = os.path.join(data_dir, "repos.md")
    terms = [t for t in (a.terms or []) if t != "show"]

    # --- Onboarding helpers (no scan) ---
    if getattr(a, "detect_roots", False):
        found = repo_index.detect_roots()
        if found:
            print("repos: detected candidate roots:")
            for p in found:
                print("  %s" % p)
            print("")
            print("Enrichment is OFF by default — listing a repo sends nothing to "
                  "Claude unless you turn it on per-repo with `repos enrich <name>`.")
            print("Confirm/adjust, then: repos --set-roots %s" % ",".join(found))
        else:
            print("repos: no candidate roots detected under your home directory.")
            print("Set them explicitly: repos --set-roots <p1,p2,...>")
        return
    if getattr(a, "set_roots", None) is not None:
        chosen = [p.strip() for p in a.set_roots.split(",") if p.strip()]
        config.set_repo_roots(chosen)
        print("repos: roots set → %s" % ", ".join(chosen))
        print("       Enrichment stays OFF until you opt in per repo "
              "(`repos enrich <name>`). Run `repos --refresh` to build the index.")
        return

    # --- Manifest toggle subcommands (no JSON editing) ---
    action = terms[0] if terms else None
    if action in ("include", "exclude", "enrich", "config"):
        _repos_manifest_action(repo_index, data_dir, action, terms)
        return

    roots = config.repo_roots()

    # --- First-run onboarding: no roots configured and no manifest yet ---
    if (not config.repo_roots_configured()
            and not os.path.exists(os.path.join(data_dir, "repos.config.json"))
            and not (a.refresh or a.force) and not terms and not a.json):
        found = repo_index.detect_roots()
        print("repos: first-run setup — no workspace roots configured yet.")
        print("")
        if found:
            print("Detected candidate roots:")
            for p in found:
                print("  %s" % p)
        else:
            print("No candidate roots auto-detected; you can name your own.")
        print("")
        print("Enrichment is OFF by default — listing a repo sends NOTHING to Claude "
              "unless you turn it on per-repo with `repos enrich <name>`.")
        print("")
        print("To proceed: confirm the roots above, then run "
              "`repos --set-roots <p1,p2,...>` followed by `repos --refresh`.")
        return

    repos = None
    if a.refresh or a.force:
        # Rescan + rewrite. Enrichment is OPT-IN: a model call fires ONLY for
        # `enrich:true` repos (and only when new/changed). A normal refresh sends
        # NOTHING off-machine. --no-llm (or the repo_enrich config gate) forces the
        # deterministic path; --dry-run reports what WOULD be sent without sending;
        # --re-summarize regenerates summaries even if cached.
        use_llm = config.repo_enrich_enabled() and not getattr(a, "no_llm", False)
        dry_run = getattr(a, "dry_run", False)
        egress = []
        repos = repo_index.build_index(
            roots, data_dir=data_dir, use_llm=use_llm, dry_run=dry_run,
            re_summarize=getattr(a, "re_summarize", False), egress=egress)
        if not a.quiet and not a.json:
            if dry_run:
                if egress:
                    print("repos: --dry-run — WOULD send README+tree for: %s "
                          "(nothing sent)" % ", ".join(sorted(egress)))
                else:
                    print("repos: --dry-run — no repos are enrich:true; nothing "
                          "would be sent.")
            elif egress:
                print("repos: enriching (sending README+tree NAMES): %s"
                      % ", ".join(sorted(egress)))
        if a.quiet and not terms and not a.json:
            sent = (" · sent: %s" % ", ".join(sorted(egress))) if egress else " · sent: nothing"
            print("repos: indexed %d repo(s) → %s%s" % (len(repos), md_path, sent))
            return

    if terms:
        if repos is None:
            repos = _repos_load(repo_index, roots, data_dir)
        q = " ".join(terms)
        hits = [r for r in repo_index.match(q, repos) if repo_index.score(q, r) > 0]
        if a.json:
            print(json.dumps(hits, indent=2, ensure_ascii=False))
        elif hits:
            print(repo_index.render_md(hits, query=q))
        else:
            print("No repos match %r." % q)
        return

    if a.json:
        if repos is None:
            repos = _repos_load(repo_index, roots, data_dir)
        print(json.dumps(repos, indent=2, ensure_ascii=False))
        return

    if repos is not None:
        # Just refreshed (non-quiet) → print what we wrote.
        print(repo_index.render_md(repos))
        return
    if not os.path.exists(md_path):
        repo_index.build_index(roots, data_dir=data_dir, use_llm=False)
    with open(md_path) as f:
        print(f.read())


def cmd_session_start(a):
    task_id = get_link(a.session)
    if task_id == SKIP_SENTINEL:
        return  # session intentionally untracked: stay silent
    task = load_task(task_id) if task_id else None
    if task:
        msg = ["[task-station] This session is attached to task [%s] %s (%s). Continue it; /done to close."
               % (task["id"][:8], task["title"], task["status"])]
        msg.extend(cat_lines(task.get("color")))
        print("\n".join(msg))
        return
    opens = [t for t in sorted_tasks() if is_on_board(t)]
    if not opens:
        return
    lines = ["[task-station] You have %d open task(s). If the user's request matches one, attach to it "
             "(full how-to: python3 %s/task-station.py guidance); otherwise a new task will be tracked "
             "once the work is clear:" % (len(opens), BASE)]
    for t in opens[:8]:
        lines.append("  - [%s] %s (%s)" % (t["id"][:8], t["title"], rel_time(t.get("updated_ts"))))
    print("\n".join(lines))


# ------------------------------------------------------------------- main ----

def main():
    p = argparse.ArgumentParser(prog="task-station")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("create"); sp.add_argument("--session", default=None)
    sp.add_argument("--title", required=True); sp.add_argument("--summary", default="")
    sp.add_argument("--color", default=None); sp.add_argument("--effort", default=None)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-attach", dest="no_attach", action="store_true",
                    help="create unattached (empty sessions) — /todo <n> -s fresh-starts")
    sp.add_argument("--attach", action="store_true",
                    help="force-bind --session even if it's a substantive tracked session")
    sp.add_argument("--active", action="store_true",
                    help="start the task active (●) instead of the default open (○)")
    sp.set_defaults(fn=cmd_create)

    sp = sub.add_parser("attach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", required=True); sp.add_argument("--color", default=None)
    sp.add_argument("--note", default=None,
                    help="append this text to the task's activity log (fold a prompt in)")
    sp.set_defaults(fn=cmd_attach)

    sp = sub.add_parser("detach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", default=None,
                    help="task to detach from (default: the session's linked task)")
    sp.set_defaults(fn=cmd_detach)

    sp = sub.add_parser("bump"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_bump)

    sp = sub.add_parser("skip"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_skip)

    sp = sub.add_parser("done"); sp.add_argument("--session", default=None)
    sp.add_argument("--task", default=None)   # close any task by seq/id from anywhere
    sp.set_defaults(fn=cmd_done)

    sp = sub.add_parser("mark-edited"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_mark_edited)   # PostToolUse(Write|Edit|NotebookEdit) one-shot reminder

    sp = sub.add_parser("stop-gate"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_stop_gate)     # Stop hook: block ending an untracked edit session

    sp = sub.add_parser("render"); sp.add_argument("--session", required=True)
    sp.add_argument("--arg", default="")
    sp.add_argument("--format", choices=["ascii", "md"], default="ascii",
                    help="list output format: ascii (default) or md (GitHub tables, printed verbatim)")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("add-project"); sp.add_argument("--task", required=True)
    sp.add_argument("--project", required=True); sp.set_defaults(fn=cmd_add_project)

    sp = sub.add_parser("status"); sp.add_argument("--task", required=True)
    sp.add_argument("value", nargs="?", default=None,
                    help="open|active to set; omit to report the current status (close via /done)")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("session-title"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_title)

    sp = sub.add_parser("whoami"); sp.add_argument("--session", required=True)
    sp.add_argument("--porcelain", action="store_true",
                    help="print only the attached task's seq (empty if none) for scripts")
    sp.add_argument("--statusline", action="store_true",
                    help="print a colored '#seq <dot> [TAG] title' status-bar segment (empty if no task)")
    sp.add_argument("--width", type=int, default=0,
                    help="with --statusline, truncate the title so the segment fits N columns (0 = no limit)")
    sp.set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("update"); sp.add_argument("--task", required=True)
    sp.add_argument("--title", default=None); sp.add_argument("--summary", default=None)
    sp.add_argument("--append-summary", dest="append_summary", default=None)
    sp.add_argument("--color", default=None); sp.add_argument("--effort", default=None)
    sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("pin"); sp.add_argument("--task", required=True)
    sp.add_argument("--session", default=None)
    sp.add_argument("--new", action="store_true",
                    help="pin a freshly-minted unborn session (claude --session-id <uuid>)")
    sp.set_defaults(fn=cmd_pin)

    sp = sub.add_parser("unpin"); sp.add_argument("--task", required=True)
    sp.set_defaults(fn=cmd_unpin)

    sp = sub.add_parser("prompt-color"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_color)

    sp = sub.add_parser("prompt-tint"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_tint)

    sp = sub.add_parser("session-tint"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_tint)

    sp = sub.add_parser("prompt-title"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_title)

    sp = sub.add_parser("prompt-context"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_prompt_context)

    sp = sub.add_parser("guidance")
    sp.set_defaults(fn=cmd_guidance)

    sp = sub.add_parser("session-start"); sp.add_argument("--session", required=True)
    sp.add_argument("--source", default=""); sp.set_defaults(fn=cmd_session_start)

    sp = sub.add_parser("repos")
    sp.add_argument("terms", nargs="*",
                    help="terms to rank repos by; omit (or 'show') to print the index. "
                         "Also: include/exclude/enrich <name>, config")
    sp.add_argument("--refresh", action="store_true", help="rescan roots + rewrite the index")
    sp.add_argument("--force", action="store_true",
                    help="reserved: bypass the future refresh debounce (today == --refresh)")
    sp.add_argument("--json", action="store_true", help="emit the structured list for the skill")
    sp.add_argument("--quiet", action="store_true", help="with --refresh, print only a one-line summary")
    sp.add_argument("--no-llm", dest="no_llm", action="store_true",
                    help="with --refresh, skip model enrichment — deterministic summary/keywords only")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="with --refresh, report which enrich:true repos WOULD be sent — send nothing")
    sp.add_argument("--re-summarize", dest="re_summarize", action="store_true",
                    help="with --refresh, regenerate summaries even when one already exists")
    sp.add_argument("--detect-roots", dest="detect_roots", action="store_true",
                    help="propose candidate discovery roots for first-run setup")
    sp.add_argument("--set-roots", dest="set_roots", default=None,
                    help="persist a comma-separated list of discovery roots")
    sp.set_defaults(fn=cmd_repos)

    sp = sub.add_parser("config")
    sp.add_argument("--workspace-dirs", dest="workspace_dirs", default=None)
    sp.add_argument("--workspace-dirs-get", dest="workspace_dirs_get", action="store_true")
    sp.add_argument("--categories", dest="categories", nargs="*", default=None,
                    help="(no arg) show enabled set + presets · 'edit' print config path · 'preset <name>' apply a preset")
    sp.add_argument("--enable", dest="enable", default=None,
                    help="enable a category slot (key, emoji, or [TAG])")
    sp.add_argument("--disable", dest="disable", default=None,
                    help="disable a category slot (refuses ⚫ GENERAL — permanent)")
    sp.add_argument("--bare-cmds", dest="bare_cmds", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--bare-cmds-get", dest="bare_cmds_get", action="store_true")
    sp.add_argument("--update-check", dest="update_check", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--update-check-get", dest="update_check_get", action="store_true")
    sp.add_argument("--tint-theme", dest="tint_theme", nargs="?", choices=["auto","dark","light"], const="auto", default=None)
    sp.add_argument("--tint-theme-get", dest="tint_theme_get", action="store_true")
    sp.add_argument("--title", dest="title", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--title-get", dest="title_get", action="store_true")
    sp.add_argument("--policy", nargs="?", choices=["on", "off"], const="on", default=None)
    sp.add_argument("--desktop-bridge", dest="desktop_bridge", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="wire the dependency-free MCP server into Claude Desktop (on) / remove it (off)")
    sp.set_defaults(fn=lambda a: __import__("config").cmd_config(a))

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
