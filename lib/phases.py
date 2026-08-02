# phases.py
"""Deterministic work-phase classification for the WS3 usage panel.

`classify_message(line)` inspects ONE parsed assistant transcript line — its
`message.content` tool_use block names plus the envelope `attributionSkill` — and
returns one of planning|research|implementation|verification|delivery|other. No
model calls; the whole thing is a tool-name + regex lookup, so a session's phase
mix is cheap to (re)derive during the usage scan.

Rules (per the WS3 brief):
1. `attributionSkill` maps FIRST (a skill that drove the turn wins over its tools):
   brainstorming / writing-plans / plan → planning; code-review / verify / review →
   verification; deep-research → research. An unknown skill falls through to tools.
2. Tool names, by precedence: Edit|Write|NotebookEdit → implementation;
   Bash matching the test/build regex → verification; Bash matching the ship regex
   → delivery; Enter|ExitPlanMode → planning; Read|Grep|Glob|WebSearch|WebFetch|
   Agent|Explore → research. A bash command matching neither regex gives no signal.
3. A mixed-tool message takes the HIGHEST-precedence hit
   (implementation > verification > delivery > planning > research). No signal → other.

`PHASES_VERSION` is stamped into each stored `session_usage.phases` blob; bumping it
tells lib/usage.py to fully rescan a session so the phase split recomputes under the
new logic (see usage._phases_stale)."""
import re

# The six buckets, plus the catch-all. Also the whitelist usage.py uses to skip the
# `__v` version stamp (and any future non-phase key) when aggregating a stored blob.
PHASES = ("planning", "research", "implementation", "verification", "delivery", "other")

# Bump when the classification logic changes — a changed stamp forces a rescan so
# already-scanned sessions re-derive their phase mix instead of keeping a stale one.
# v2 (WS7): MCP tools, Task/SendMessage, TodoWrite/TaskCreate, and read-only Bash get
# real buckets instead of falling into "other" — so the work-mix reflects the actual
# work and the "other" slice shrinks. The bump forces stale rollups to rescan.
# v3 (board B5): file-mutation / redirection / run-script Bash + more MCP verbs get real
# buckets (shrinking "other" further), AND classification now names the tool/command that
# lands in "other" so the board can drill down into the top contributors.
# v4: classification unchanged — bumped to force the one-time full rescan that
# re-files each stored prompt under its span-matched task (usage._prompt_owner_id;
# shared sessions used to dump their whole trail on one owner task).
PHASES_VERSION = 4

# attributionSkill substring → phase, first match wins. Skill ids carry plugin
# prefixes/suffixes (e.g. "superpowers:writing-plans", "requesting-code-review"),
# so we match by substring on the lowercased id.
_SKILL_RULES = (
    ("brainstorming", "planning"),
    ("writing-plans", "planning"),
    ("plan", "planning"),
    ("code-review", "verification"),
    ("verify", "verification"),
    ("review", "verification"),
    ("deep-research", "research"),
)

# Bash command → phase. First the test/build family (verification), then the ship
# family (delivery), then a read-only inspection family (research); a command matching
# none yields no phase signal.
_VERIFY_CMD = re.compile(r"(unittest|pytest|npm (test|run build)|tsc|go test|cargo (test|build)|make\b)")
_DELIVERY_CMD = re.compile(r"(git (push|commit|merge)|gh pr|az repos|release)")
# Read-only shell (listing, reading, searching, git inspection) is research work —
# anchored at the command start so a write pipeline isn't miscounted (v2).
_READONLY_CMD = re.compile(
    r"^\s*(ls|cat|rg|grep|fd|find|head|tail|bat|wc|tree|git\s+(status|diff|log|show|branch))\b")
# File-mutation shell (v3): commands that create/move/delete/permission files are
# implementation work. Anchored at the command start.
_IMPL_CMD = re.compile(
    r"^\s*(mkdir|mv|cp|rm|rmdir|touch|chmod|chown|ln|tee|patch|dd|truncate|sed\s+-i)\b")
# Running code / scripts / package managers (v3): treated as verification (you run it
# to see it work). Anchored at the command start.
_RUN_CMD = re.compile(
    r"^\s*(python3?|node|deno|bun|ruby|go run|cargo run|npm run|npm start|npm ci|"
    r"npm install|pip install|yarn|pnpm|\./|sh\s|bash\s|zsh\s)\b")
# A shell redirect that WRITES a file (`> f`, `>> f`, `tee f`) — implementation. The
# negative lookahead skips fd-dup forms like `2>&1` / `1>&2` that redirect a stream,
# not a file (v3).
_REDIRECT_WRITE = re.compile(r"(?<!\d)>>?(?!\s*&?\s*\d)\s*\S")

_IMPL_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})
# planning: plan-mode + the task/worktree bookkeeping tools (v2).
_PLAN_TOOLS = frozenset({"EnterPlanMode", "ExitPlanMode",
                         "TodoWrite", "TaskCreate", "TaskUpdate", "EnterWorktree"})
# research: read/search tools + subagent dispatch/messaging (v2 adds Task/SendMessage).
_RESEARCH_TOOLS = frozenset({"Read", "Grep", "Glob", "WebSearch", "WebFetch",
                             "Agent", "Explore", "Task", "SendMessage"})

# MCP tool suffix heuristics (v2): an `mcp__<server>__<verb_noun>` name maps by the
# verb it carries. Read-ish verbs are research; write-ish verbs are implementation.
# ADO PR/work-item mutations (create/update/vote/link) are DELIVERY and win first.
_ADO_DELIVERY = ("create_pull_request", "update_pull_request", "vote",
                 "wit_create", "wit_update", "link")
_MCP_RESEARCH = ("find", "read", "search", "list", "get", "overview", "discover", "query",
                 "show", "profile", "analyze", "status", "test", "inspect", "fetch")
_MCP_IMPL = ("replace", "insert", "edit", "rename", "write", "update", "delete", "execute",
             "create", "add", "remove", "run", "set", "assign", "upsert", "build")

# Precedence rank for the mixed-tool tie-break (lower = higher precedence).
_RANK = {"implementation": 0, "verification": 1, "delivery": 2,
         "planning": 3, "research": 4}


def _phase_for_skill(skill):
    """The phase an `attributionSkill` maps to, or None when it matches no rule."""
    if not skill:
        return None
    s = skill.lower()
    for needle, phase in _SKILL_RULES:
        if needle in s:
            return phase
    return None


def _phase_for_mcp(name):
    """The phase an `mcp__…` tool name maps to by its verb heuristic, or None when
    nothing matches (genuinely unknown MCP signal → falls through to `other`)."""
    low = name.lower()
    if "__ado__" in low and any(k in low for k in _ADO_DELIVERY):
        return "delivery"
    if any(k in low for k in _MCP_RESEARCH):
        return "research"
    if any(k in low for k in _MCP_IMPL):
        return "implementation"
    return None


def _bash_signal_name(cmd):
    """A compact identifier for a Bash command that carried no phase signal — its
    first bare token (the program name), for the 'other' drill-down. '' when empty."""
    for tok in (cmd or "").split():
        if tok in ("sudo", "env", "time", "nohup") or "=" in tok:
            continue                    # skip wrappers + VAR=val prefixes
        return "$ " + tok
    return ""


def _phase_for_tool(block):
    """The phase one tool_use block signals, or None when it carries no signal
    (an unrecognised tool, or a bash command matching no regex)."""
    name = block.get("name") or ""
    if name.startswith("mcp__"):
        return _phase_for_mcp(name)
    if name in _IMPL_TOOLS:
        return "implementation"
    if name == "Bash":
        cmd = (block.get("input") or {}).get("command") or ""
        if _VERIFY_CMD.search(cmd):
            return "verification"
        if _DELIVERY_CMD.search(cmd):
            return "delivery"
        if _READONLY_CMD.search(cmd):
            return "research"
        if _IMPL_CMD.search(cmd) or _REDIRECT_WRITE.search(cmd):
            return "implementation"
        if _RUN_CMD.search(cmd):
            return "verification"
        return None
    if name in _PLAN_TOOLS:
        return "planning"
    if name in _RESEARCH_TOOLS:
        return "research"
    return None


def _tool_name_for_drill(block):
    """The identifier a tool_use block contributes to the 'other' drill-down when it
    carries NO phase signal — the tool name, or `$ <prog>` for a bare Bash command."""
    name = block.get("name") or "?"
    if name == "Bash":
        return _bash_signal_name((block.get("input") or {}).get("command") or "") or "$ ?"
    return name


def _tool_uses(message):
    """The tool_use blocks in an assistant message's content array (empty otherwise)."""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def classify_message_named(line):
    """Like `classify_message` but returns `(phase, name)`: when the phase is 'other',
    `name` is the single tool/command signal responsible (the last unrecognised tool
    block, or None when there were no tool blocks at all) so the board can accumulate a
    drill-down of the top 'other' contributors. `name` is None for any real phase."""
    line = line or {}
    skill_phase = _phase_for_skill(line.get("attributionSkill"))
    if skill_phase:
        return skill_phase, None
    message = line.get("message")
    if not isinstance(message, dict):
        return "other", None
    best = None
    other_name = None
    for block in _tool_uses(message):
        phase = _phase_for_tool(block)
        if phase is None:
            other_name = _tool_name_for_drill(block)
            continue
        if best is None or _RANK[phase] < _RANK[best]:
            best = phase
    if best:
        return best, None
    return "other", other_name


def classify_message(line):
    """Classify one parsed assistant transcript line into a work phase. See module
    docstring for the rules. Always returns a member of `PHASES` (never raises)."""
    return classify_message_named(line)[0]
