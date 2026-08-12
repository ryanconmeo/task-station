"""Single JSON config store under the data dir, plus the `task-station config` board."""
import copy, json, os, re
import paths

# Default repo roots scanned for the hub repo index (`task-station repos`) when no
# workspace dirs are configured. Centralized here so both delegate's `--project`
# shorthand and the repo index share one source of truth.
DEFAULT_WORKSPACE_DIRS = ["~/Workspace", "~/Workspace-Other"]

def _path():
    return os.path.join(paths.data_dir(), "config.json")

def _load():
    try:
        with open(_path()) as f:
            return json.load(f)
    except Exception:
        return {}

def _save(d):
    data = paths.data_dir()
    os.makedirs(data, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path())

def get(key, default=None):
    return _load().get(key, default)

def set(key, value):
    d = _load(); d[key] = value; _save(d)

def unset(key):
    d = _load()
    if key in d:
        del d[key]; _save(d)

def workspace_dirs():
    raw = get("workspace_dirs")
    if raw is None:
        env = os.environ.get("TASK_STATION_WORKSPACE_DIRS", "")
        raw = [p for p in env.split(os.pathsep) if p] if env else []
    return [os.path.expanduser(p) for p in raw]

def repo_roots():
    """Roots to scan for the hub repo index. Precedence: an explicit `repo_roots`
    config list (set via `repos --set-roots` during first-run onboarding) > the
    configured workspace dirs (`--workspace-dirs` / `TASK_STATION_WORKSPACE_DIRS`) >
    DEFAULT_WORKSPACE_DIRS. Unlike delegate's `--project` resolution — which
    deliberately errors when nothing is configured — the repo index has a sensible
    default so the hub can route tasks out of the box."""
    explicit = get("repo_roots")
    if explicit:
        return [os.path.expanduser(p) for p in explicit]
    dirs = workspace_dirs()
    if not dirs:
        dirs = [os.path.expanduser(p) for p in DEFAULT_WORKSPACE_DIRS]
    return dirs

def set_repo_roots(paths_list):
    """Persist the repo-index discovery roots (stored un-expanded). Written by
    `repos --set-roots` once the user confirms the first-run onboarding proposal."""
    set("repo_roots", [p for p in paths_list if p])
    return get("repo_roots")

def repo_roots_configured():
    """True once the user has explicitly chosen discovery roots (either `repo_roots`
    or the legacy `workspace_dirs`). When False AND no manifest exists yet, `repos`
    drives one-time onboarding instead of silently scanning the defaults."""
    return bool(get("repo_roots") or get("workspace_dirs"))

def repo_enrich_enabled():
    """Global kill-switch for repo enrichment egress. Default ON — but this only
    *permits* the per-repo `enrich` manifest flag to take effect; enrichment is OFF
    for every repo by default, so a normal `repos --refresh` still sends nothing.
    `TASK_STATION_REPO_ENRICH=off` or `repo_enrich:false` hard-disables ALL egress
    regardless of per-repo flags (so does `repos --refresh --no-llm` per-call).
    Enrichment always degrades to a deterministic summary regardless."""
    if os.environ.get("TASK_STATION_REPO_ENRICH") == "off":
        return False
    return bool(get("repo_enrich", True))

def bare_commands():
    """True only if the user opted in (config flag or env). Default off."""
    if os.environ.get("TASK_STATION_BARE_CMDS") == "on":
        return True
    return bool(get("bare_commands", False))

def update_check_enabled():
    """True only if the user opted in (config flag). Default off — no network."""
    return bool(get("update_check", False))

def title_enabled():
    """True unless explicitly disabled — default ON. Mirrors TASK_STATION_TINT's
    env escape: `TASK_STATION_TITLE=off` (or `config --title off`) suppresses the
    auto terminal title."""
    if os.environ.get("TASK_STATION_TITLE") == "off":
        return False
    return bool(get("title", True))

def tint_enabled():
    """True unless explicitly disabled — default ON. The env escape
    `TASK_STATION_TINT` (on/off/1/0/true/false) WINS over config (so a one-off
    `TASK_STATION_TINT=on` re-enables a config `tint=off`, and vice-versa); else
    the persisted `tint` flag (default ON). Gates every terminal-tint emitter."""
    env = os.environ.get("TASK_STATION_TINT")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("tint", True))

def statusline_enabled():
    """False unless explicitly enabled — default OFF (opt-in; writes to the user's
    settings.json). The env escape `TASK_STATION_STATUSLINE` (on/off/1/0/true/false)
    WINS over config; else the persisted `statusline` flag (default off). Gates the
    SessionStart provider drop-in. Mirrors tint_enabled()/guaranteed_tracking_enabled()."""
    env = os.environ.get("TASK_STATION_STATUSLINE")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("statusline", False))

# Canonical cost-HUD row keys, in render order (Task under the header, then Turn/
# Session/5-hour/Week/Total). Duplicated from hud.ROW_KEYS (hud imports config, so
# config must not import hud) — keep the two in sync. The old `limits` row merged
# into `week` + `fivehour`; legacy row names are accepted as aliases below.
HUD_ROW_KEYS = ("task", "session", "fivehour", "week", "total")

# Back-compat aliases so config persisted under the old WS7 row names keeps working.
# `limits` bundled the 5-hour AND weekly limits; the weekly moved into `week`, so
# the residual standalone maps to `fivehour`.
_HUD_ROW_ALIASES = {
    "5-hour": "fivehour", "5hour": "fivehour", "5h": "fivehour",
    "five_hour": "fivehour", "five-hour": "fivehour", "fivehr": "fivehour",
    "limits": "fivehour",
    # `turn` was removed (Session carries the live cost); a persisted `turn` is dropped.
}


def hud_enabled():
    """False unless explicitly enabled — default OFF (opt-in; writes to the user's
    settings.json via the status-line host, like `--statusline`). The env escape
    `TASK_STATION_HUD` (on/off/1/0/true/false) WINS over config; else the persisted
    `hud` flag (default off). Gates the cost-HUD provider drop-in + its turn-baseline
    hook snapshots. Mirrors statusline_enabled()."""
    env = os.environ.get("TASK_STATION_HUD")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("hud", False))


def hud_rows():
    """The ordered subset of HUD_ROW_KEYS the cost HUD renders (default: all of them,
    task·turn·session·fivehour·week·total). Accepts a comma-separated string or a list;
    legacy names (`limits`, `5-hour`, …) are mapped via aliases, unknown tokens dropped,
    order de-duplicated. An empty/garbage result falls back to the full default set. The
    env override `TASK_STATION_HUD_ROWS` (comma-separated) WINS over persisted `hud_rows`."""
    raw = os.environ.get("TASK_STATION_HUD_ROWS")
    if raw is None:
        raw = get("hud_rows")
    if raw is None:
        return list(HUD_ROW_KEYS)
    return hud_rows_parse(raw)


def hud_rows_parse(raw):
    """Normalize a raw `--hud-rows` value (comma string or list) to the ordered,
    de-duplicated, validated key list — the value persisted by the setter. Falls back
    to the full default set when nothing valid is given."""
    tokens = raw if isinstance(raw, list) else str(raw or "").split(",")
    out = []
    for t in tokens:
        k = str(t).strip().lower()
        k = _HUD_ROW_ALIASES.get(k, k)
        if k in HUD_ROW_KEYS and k not in out:
            out.append(k)
    return out or list(HUD_ROW_KEYS)


def hud_eco_enabled():
    """Whether the HUD appends the eco-footprint column (`≈ <comparison>` of each
    row's output tokens) — default ON (Ryan wants the comparisons back). The env
    escape `TASK_STATION_HUD_ECO` (on/off/1/0/true/false) WINS over config; else the
    persisted `hud_eco` flag (default ON — Ryan asked for the eco strings back).
    Only meaningful with the HUD on."""
    env = os.environ.get("TASK_STATION_HUD_ECO")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("hud_eco", True))


def board_autorefresh_enabled():
    """False unless explicitly enabled — default OFF (opt-in). When ON, the visual
    board injects a meta-refresh tag so an open tab stays live, and the Stop hook
    quietly regenerates board.html (only if it already exists). The env escape
    `TASK_STATION_BOARD_AUTOREFRESH` (on/off/1/0/true/false) WINS over config; else
    the persisted `board_autorefresh` flag (default off). Mirrors statusline_enabled()."""
    env = os.environ.get("TASK_STATION_BOARD_AUTOREFRESH")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("board_autorefresh", False))

def interbrain_mode():
    """Interbrain federation mode for the board: 'on' | 'off' | 'auto' (default
    'auto'). The board resolves 'auto' → on when brains.json has >1 brain OR peer feeds
    exist, else off. Env escape TASK_STATION_INTERBRAIN wins over the persisted flag."""
    env = os.environ.get("TASK_STATION_INTERBRAIN")
    if env is not None:
        v = env.strip().lower()
        return v if v in ("on", "off", "auto") else "auto"
    v = str(get("interbrain", "auto")).strip().lower()
    return v if v in ("on", "off", "auto") else "auto"

def org_label():
    """Display label for the ORG brain (the shared/company brain) everywhere it appears —
    rail, chips, hulls legend, manager. Default 'Org brain' (e.g. 'Company Brain')."""
    return get("org_label") or "Org brain"

def done_closes_window_enabled():
    """False unless explicitly enabled — default OFF (opt-in). When ON, a no-arg
    `/done` (closing THIS session's attached task) also auto-closes the hosting
    terminal window ~1s later; when OFF the window is left open. There is NO
    reliable signal for a bash script to tell a human-typed /done from a model
    Skill-tool /done (`disable-model-invocation` does not gate the Skill tool), so
    the destructive close is opt-in rather than intent-detected. The `--task <ref>`
    path never closes a window regardless of this flag. The env escape
    `TASK_STATION_DONE_CLOSES_WINDOW` (on/off/1/0/true/false) WINS over config; else
    the persisted `done_closes_window` flag (default off). Mirrors
    board_autorefresh_enabled()."""
    env = os.environ.get("TASK_STATION_DONE_CLOSES_WINDOW")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("done_closes_window", False))

def board_browser():
    """The browser app the visual board opens in (a stored app name string, e.g.
    "Google Chrome"), or None to use the system default browser. Honoured by
    task-station._open_path via `open -a "<App>"`; the env var TASK_STATION_BROWSER
    takes precedence over this stored value."""
    return get("board_browser") or None

def board_prompts_enabled():
    """Whether the visual board shows the captured prompt trail (the Recent-prompts
    preview + the full-list <details>). Default ON — the board is local-only, so
    displaying same-machine prompt text is fine (prompt EXPORT stays opt-in
    elsewhere). The env escape `TASK_STATION_BOARD_PROMPTS` (on/off/1/0/true/false)
    WINS over config. Independent of prompt CAPTURE (`usage_prompts`) — with capture
    on but this off, prompts are stored yet kept off the board. Mirrors
    board_autorefresh_enabled()."""
    env = os.environ.get("TASK_STATION_BOARD_PROMPTS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("board_prompts", True))


def knowledge_graph_enabled():
    """False unless explicitly enabled — default OFF (opt-in; the second-brain tier).
    The master switch for the knowledge-graph features: task↔note co-citation edges in
    the board mini-graph, the board's per-task "Related knowledge" panel, and the
    `## Related` wikilink emission into the Obsidian mirror. When OFF, NONE of those
    do anything — the board renders exactly today's universal graph (task lineage +
    semantic `touches-same` edges) and the Obsidian note carries no `## Related`
    section. These features additionally require a configured consumer (an
    `obsidian_vault`); the flag alone is inert without one, so a user with no vault
    sees identical behaviour regardless of this flag. The env escape
    `TASK_STATION_KNOWLEDGE_GRAPH` (on/off/1/0/true/false) WINS over config; else the
    persisted `knowledge_graph` flag (default off). Mirrors board_autorefresh_enabled()."""
    env = os.environ.get("TASK_STATION_KNOWLEDGE_GRAPH")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("knowledge_graph", False))


def knowledge_plane_mode():
    """Knowledge-plane mode for the board's two-plane graph: 'on' | 'off' | 'auto'
    (default 'auto'). The board resolves 'auto' → on when an Obsidian vault is configured
    AND it yields at least one parseable note, else off. Env escape
    TASK_STATION_KNOWLEDGE_PLANE wins over the persisted flag.

    NOT `knowledge_graph_enabled()` above, and deliberately a separate switch. That one
    gates a WRITE path — the `## Related` wikilink section emitted into the user's real
    vault — as well as the task↔task co-citation edges, which is why it is opt-in and
    stays off by default. This one is READ-ONLY and board-only: it decides whether the
    board draws the vault as its own plane above the task plane, and it never writes a
    byte into a vault. That is what lets it default to auto — a user with no vault
    resolves auto → off and their board is unchanged. Mirrors interbrain_mode()."""
    env = os.environ.get("TASK_STATION_KNOWLEDGE_PLANE")
    if env is not None:
        v = env.strip().lower()
        return v if v in ("on", "off", "auto") else "auto"
    v = str(get("knowledge_plane", "auto")).strip().lower()
    return v if v in ("on", "off", "auto") else "auto"


def auto_categories_enabled():
    """True unless explicitly disabled — default ON. Mirrors TASK_STATION_TITLE's
    env escape: `TASK_STATION_AUTO_CATEGORIES=off` (or `config --auto-categories off`)
    freezes the enabled set — assigning a task to a disabled slot no longer
    auto-enables it (today's restrict-to-enabled behaviour)."""
    if os.environ.get("TASK_STATION_AUTO_CATEGORIES") == "off":
        return False
    return bool(get("auto_categories", True))

def guaranteed_tracking_enabled():
    """False unless explicitly enabled — default OFF. When ON, the UserPromptSubmit
    hook deterministically creates+attaches a provisional task on a fresh, unattached,
    non-skipped session instead of merely nudging. Env override
    `TASK_STATION_GUARANTEED_TRACKING` (on/off/1/0/true/false) wins over config."""
    env = os.environ.get("TASK_STATION_GUARANTEED_TRACKING")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("guaranteed_tracking", False))

def auto_checkpoint_enabled():
    """False unless explicitly enabled — default OFF (opt-in). The master switch for
    automatic checkpointing: when ON, a PostCompact hook stashes the harness's
    compaction summary into the attached task's history (zero model cost), a
    SessionStart-on-compact nudge asks the model to refresh the structured digest,
    and a staleness-gated Stop nudge keeps that digest current. When OFF, NONE of
    those behaviours do anything (exactly today's behaviour). The env escape
    `TASK_STATION_AUTO_CHECKPOINT` (on/off/1/0/true/false) WINS over config; else the
    persisted `auto_checkpoint` flag (default off). Mirrors statusline_enabled()."""
    env = os.environ.get("TASK_STATION_AUTO_CHECKPOINT")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("auto_checkpoint", False))

def checkpoint_at():
    """The ABSOLUTE proactive context-pressure threshold, in ESTIMATED tokens — the
    LEGACY / fallback trigger. Only meaningful with --auto-checkpoint ON: the Stop
    path prompts a full structured `/todo save` once the session's transcript-size
    token ESTIMATE grows past this, BEFORE the harness auto-compacts. `0` disables it.

    DEFAULT is now 0 (OFF): the percentage trigger (checkpoint_pct, measured from the
    transcript's real `usage` block) is the new default path — a far more accurate
    signal than the byte-size estimate. checkpoint_at stays for back-compat: an
    explicitly stored value (or the env var) keeps firing the old absolute trigger,
    which is the fallback whenever a real measurement isn't available. The env override
    TASK_STATION_CHECKPOINT_AT (integer, or 0/off to disable) WINS over config. Returns
    an int (0 = off)."""
    env = os.environ.get("TASK_STATION_CHECKPOINT_AT")
    raw = env if env is not None else get("checkpoint_at", 0)
    if isinstance(raw, str) and raw.strip().lower() in ("off", ""):
        return 0
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def checkpoint_pct():
    """The PROACTIVE context-pressure threshold, as a PERCENTAGE of the real context
    window — the DEFAULT trigger (default 65). Only meaningful with --auto-checkpoint
    ON: the Stop path prompts a full structured `/todo save` once the MEASURED context
    (see measure_context_tokens — read from the transcript's most-recent `usage` block)
    reaches this share of context_window(), BEFORE the harness auto-compacts. Unlike
    checkpoint_at's byte-size estimate, this is measured from actual token usage, so it
    fires at a real, window-relative point.

    Valid 1–95 (a value is clamped into that range); `0`/`off` disables the pct trigger
    (leaving checkpoint_at / the PostCompact stash intact). The env override
    TASK_STATION_CHECKPOINT_PCT (integer, or 0/off) WINS over config. Returns an int
    (0 = off)."""
    env = os.environ.get("TASK_STATION_CHECKPOINT_PCT")
    raw = env if env is not None else get("checkpoint_pct", 65)
    if isinstance(raw, str) and raw.strip().lower() in ("off", ""):
        return 0
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 65
    if n <= 0:
        return 0
    return min(95, max(1, n))


def context_window(model_id=None):
    """The size of the model's context window, in tokens — the denominator the
    checkpoint_pct trigger measures against, and the basis for the displayed %/tokens.

    Resolution order: an EXPLICIT setting always wins — the env override
    TASK_STATION_CONTEXT_WINDOW (integer) first, then the `context_window` config key.
    When NEITHER is set, the window is derived from `model_id` (the model actually in
    use) via pricing.context_window_for — so an Opus-1M session gets a 1,000,000 window
    and a Haiku/Sonnet session gets 200,000, without the user having to hand-set it. If
    no model is supplied and nothing is configured, falls back to 200000. A non-positive
    or unparseable explicit value is ignored (falls through to the model/default).
    Returns an int > 0."""
    env = os.environ.get("TASK_STATION_CONTEXT_WINDOW")
    raw = env if env is not None else get("context_window", None)
    if raw is not None:
        try:
            n = int(raw)
            if n > 0:
                return n            # explicit config/env wins
        except (TypeError, ValueError):
            pass
    if model_id:
        try:
            import pricing
            w = pricing.context_window_for(model_id)
            if w > 0:
                return w
        except Exception:
            pass
    return 200000


def checkpoint_milestone_edits():
    """The MILESTONE staleness threshold: how many meaningful events (file edits /
    status promotions — the same substantive-work signals that mark the digest stale)
    must accrue since the last digest refresh before the light Stop staleness nudge
    fires. Default 5 — so a couple of small edits no longer nudge, only a real batch of
    work does. `0`/`off` restores the pre-1.61 behaviour (nudge on ANY staleness, i.e.
    the first meaningful event). The env override TASK_STATION_CHECKPOINT_MILESTONE_EDITS
    (integer, or 0/off) WINS over config. Returns an int (0 = off / fire-on-any)."""
    env = os.environ.get("TASK_STATION_CHECKPOINT_MILESTONE_EDITS")
    raw = env if env is not None else get("checkpoint_milestone_edits", 5)
    if isinstance(raw, str) and raw.strip().lower() in ("off", ""):
        return 0
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 5
    return n if n > 0 else 0


def ultracode_hints_enabled():
    """True unless explicitly disabled — default ON. The env escape
    `TASK_STATION_ULTRACODE_HINTS` (on/off/1/0/true/false) WINS over config; else
    the persisted `ultracode_hints` flag (default on). Gates EVERY ultracode
    fan-out hint — the human advisory (detail recap + SessionStart) and the
    model-facing steering on an ultracode turn. Mirrors tint_enabled()/
    statusline_enabled()."""
    env = os.environ.get("TASK_STATION_ULTRACODE_HINTS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("ultracode_hints", True))


def notify_enabled():
    """False unless explicitly enabled — default OFF (opt-in). Gates the macOS
    banner channel for worker-lifecycle notifications (delegate run finished/failed).
    The env escape `TASK_STATION_NOTIFY` (on/off/1/0/true/false) WINS over config;
    else the persisted `notify` flag (default off). Mirrors statusline_enabled()."""
    env = os.environ.get("TASK_STATION_NOTIFY")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("notify", False))


def notify_webhook():
    """The webhook URL that worker-lifecycle events POST to, or None when unset.
    Independent of `notify` (the macOS switch) — a webhook fires whenever a URL is
    configured. The env override `TASK_STATION_NOTIFY_WEBHOOK` WINS over config; an
    empty string means off. Accepts any plain-JSON-POST receiver (Slack/Teams/ntfy)."""
    env = os.environ.get("TASK_STATION_NOTIFY_WEBHOOK")
    raw = env if env is not None else get("notify_webhook", "")
    return (raw or "").strip() or None


def delegate_bypass_permissions():
    """False unless explicitly enabled — default OFF (#463). OPT-IN only: gates
    whether a `--bg` delegated worker spawns with `--permission-mode
    bypassPermissions` (which needs a one-time `claude --dangerously-skip-permissions`
    disclaimer). By DEFAULT (this False) workers spawn with `--permission-mode
    dontAsk` instead — fail-closed: non-allowlisted tools are auto-denied (never
    hang), exactly like the old `-p` workers, with NO dangerous-skip. The env escape
    `TASK_STATION_DELEGATE_BYPASS` (on/off/1/0/true/false) WINS over config; else the
    persisted `delegate_bypass_permissions` flag (default OFF)."""
    env = os.environ.get("TASK_STATION_DELEGATE_BYPASS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("delegate_bypass_permissions", False))


def reap_workers_on_done():
    """True unless explicitly disabled — default ON. Master switch for reaping a
    task's still-live `--bg` worker sessions when the task is closed (#465): when ON,
    closing a task removes each confirmed delegate worker's session-store file and
    kills its process group so it can't linger/respawn in Agent View; when OFF the
    reap is a complete NO-OP (workers are left running). The reap is airtight (only a
    registry-registered, role==worker, task-station-named, non-busy, non-current
    session is ever touched), but this knob disables it entirely for users who prefer
    to stop workers by hand. The env escape `TASK_STATION_REAP_WORKERS_ON_DONE`
    (on/off/1/0/true/false) WINS over config; else the persisted flag (default on).
    Mirrors tint_enabled()."""
    env = os.environ.get("TASK_STATION_REAP_WORKERS_ON_DONE")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("reap_workers_on_done", True))


def usage_tracking_enabled():
    """Master switch for the WS1 usage ledger — True unless explicitly disabled
    (default ON; same-machine data). When OFF the scanner/flush and the derived-$
    stats-line segment are inert. The env escape `TASK_STATION_USAGE_TRACKING`
    (on/off/1/0/true/false) WINS over config. Mirrors tint_enabled()."""
    env = os.environ.get("TASK_STATION_USAGE_TRACKING")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("usage_tracking", True))

def usage_prompts_enabled():
    """Whether prompt TEXT is captured into the ledger's `prompts` table (default
    ON — same-machine data). Independent of prompt EXPORT, which stays opt-in
    elsewhere. The env escape `TASK_STATION_USAGE_PROMPTS` WINS over config. Only
    meaningful while usage tracking is on."""
    env = os.environ.get("TASK_STATION_USAGE_PROMPTS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("usage_prompts", True))

def usage_billing_mode():
    """How the derived $ is framed: `api` (default — metered, our figure IS the
    marginal bill) or `subscription` (flat-rate seat — the figure is the
    API-equivalent value, not billed per token until overage). The env override
    `TASK_STATION_USAGE_BILLING_MODE` WINS over config; an unknown value falls back
    to `api`."""
    env = os.environ.get("TASK_STATION_USAGE_BILLING_MODE")
    raw = env if env is not None else get("usage_billing_mode", "api")
    val = (raw or "api").strip().lower()
    return val if val in ("api", "subscription") else "api"

def recap_enabled():
    """Whether the weekly private usage recap auto-generates (default OFF — opt-in;
    the manual `task-station recap` command works regardless). When ON, the Stop hook
    generates the previous complete week's recap at most once per week (throttled by a
    stamp file; fail-open, zero tokens unless a curator is configured). The env escape
    `TASK_STATION_RECAP` (on/off/1/0/true/false) WINS over config. Mirrors
    board_autorefresh_enabled()."""
    env = os.environ.get("TASK_STATION_RECAP")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("recap", False))

def recap_curator_cmd():
    """The optional recap 'curator' command (default None — OFF). When set, the recap
    pipes its PRIVACY-SAFE aggregate stats (JSON — counts/ratios/titles, never prompt
    text) to this command's stdin and reads up to 3 tailored tips from its stdout.
    Off by default so a recap costs zero tokens. The env escape
    `TASK_STATION_RECAP_CURATOR_CMD` WINS over config; an empty value means OFF."""
    env = os.environ.get("TASK_STATION_RECAP_CURATOR_CMD")
    raw = env if env is not None else get("recap_curator_cmd")
    raw = (raw or "").strip()
    return raw or None

# Known GUI editors with a file-opening URL scheme, in probe priority order (used
# only to AUTO-DETECT a default when the user hasn't set one). `file` is the neutral
# fallback: a plain file:// link the browser/OS handles when no editor is detected.
_EDITOR_APP_SCHEMES = [
    ("Cursor", "cursor"),
    ("Zed", "zed"),
    ("Visual Studio Code", "vscode"),
    ("Visual Studio Code - Insiders", "vscode-insiders"),
    ("Sublime Text", "subl"),
    ("PyCharm", "pycharm"),
    ("IntelliJ IDEA", "idea"),
]
# Substring → scheme for a $VISUAL/$EDITOR hint (only GUI editors that HAVE a scheme;
# terminal editors like vim/nano/emacs have none, so they fall through to the probe).
_EDITOR_ENV_HINTS = [
    ("code-insiders", "vscode-insiders"), ("cursor", "cursor"), ("zed", "zed"),
    ("subl", "subl"), ("pycharm", "pycharm"), ("idea", "idea"), ("code", "vscode"),
]


def detect_editor_scheme():
    """Best-effort guess at the user's default GUI editor's file-opening URL scheme,
    with NO hardcoded editor assumption. Order: an explicit $VISUAL/$EDITOR GUI-editor
    hint, then the first known editor .app found in /Applications (or ~/Applications),
    else `file` (a plain file:// link the browser/OS handles). macOS has no url scheme
    that opens an arbitrary file in its per-type default app from a static page, so this
    targets the code editor that file links most often want. Cheap (a few isdir calls);
    always overridable via config / the env var."""
    for envvar in ("VISUAL", "EDITOR"):
        val = (os.environ.get(envvar) or "").lower()
        if not val:
            continue
        for needle, scheme in _EDITOR_ENV_HINTS:
            if needle in val:
                return scheme
    appdirs = ["/Applications", os.path.expanduser("~/Applications")]
    for name, scheme in _EDITOR_APP_SCHEMES:
        for d in appdirs:
            if os.path.isdir(os.path.join(d, name + ".app")):
                return scheme
    return "file"


def editor_scheme():
    """The URI scheme the visual board uses to make file paths clickable — e.g.
    `cursor`/`vscode`/`zed` renders `<scheme>://file/<abspath>`, and the neutral
    `file` renders a plain `file://<abspath>`. A copy-path button is always the
    fallback. Resolution: the env var `TASK_STATION_EDITOR_SCHEME` wins, then the
    `editor_scheme` config key; when NEITHER is set the scheme is AUTO-DETECTED from
    the user's editor (detect_editor_scheme) rather than assuming vscode."""
    env = os.environ.get("TASK_STATION_EDITOR_SCHEME")
    if env is not None and env.strip():
        return env.strip()
    raw = get("editor_scheme")
    if raw and str(raw).strip():
        return str(raw).strip()
    return detect_editor_scheme()

def obsidian_vault():
    """The absolute path to the Obsidian vault tasks export into, or "" when OFF.
    Stored un-expanded (so `~` survives a home move); returned expanded. Empty /
    unset ⇒ export disabled — this is the single opt-in switch for the whole
    Obsidian integration."""
    raw = get("obsidian_vault") or ""
    return os.path.expanduser(raw) if raw else ""

def owner():
    """The owner HANDLE for shared-vault scoping, or "" when unset (single-owner —
    BYTE-IDENTICAL to today). When set, exported/vault notes nest under
    <target>/<owner>/, the note frontmatter gains `owner: <handle>`, daily-note lines
    carry the handle, and the stream manifest + event actor carry it — so several
    people can point at ONE shared vault without colliding. Stored verbatim; the env
    escape `TASK_STATION_OWNER` WINS over config. Whitespace is trimmed."""
    env = os.environ.get("TASK_STATION_OWNER")
    raw = env if env is not None else get("owner", "")
    return (raw or "").strip()

def obsidian_daily_note_enabled():
    """True only if the user opted in — default off. When on, closing a task and a
    full `/todo save` checkpoint append a one-line entry to the vault's daily note.
    Inert unless a vault is configured."""
    return bool(get("obsidian_daily_note", False))

def obsidian_daily_heading():
    """The heading the daily-note entries are appended under (created if absent).
    Default "## Claude sessions"."""
    return get("obsidian_daily_heading") or "## Claude sessions"

def obsidian_prompts_enabled():
    """Whether the Obsidian note's `## Prompts` trail (the full timestamped prompt
    history) is written into the exported vault note — default OFF (opt-in). A vault
    may sync to third-party services, so prompt EXPORT stays opt-in even though
    prompt CAPTURE (usage_prompts) is on by default. Independent of usage tracking;
    inert unless a vault is configured. The env escape `TASK_STATION_OBSIDIAN_PROMPTS`
    (on/off/1/0/true/false) WINS over config. Mirrors obsidian_daily_note_enabled()."""
    env = os.environ.get("TASK_STATION_OBSIDIAN_PROMPTS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("obsidian_prompts", False))


def obsidian_category_hubs_enabled():
    """Whether the mirror/export emits per-note category links + maintains the
    `categories/` hub pages — default ON (the mirror itself is already opt-in via a
    vault/export dir, so once you're exporting, category clustering is on unless you
    turn it off). OFF drops the `[[categories/<slug>]]` link from notes and prunes the
    hub pages on the next sync. The env escape `TASK_STATION_OBSIDIAN_CATEGORY_HUBS`
    (on/off/1/0/true/false) WINS over config."""
    env = os.environ.get("TASK_STATION_OBSIDIAN_CATEGORY_HUBS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    v = get("obsidian_category_hubs")
    return True if v is None else bool(v)


def obsidian_subgroups_enabled():
    """Whether emergent sub-groups within a category are emitted — default ON. NESTED
    inside category hubs: this only takes effect when obsidian_category_hubs is also on
    (a within-category sub-hub is meaningless without the category hub). ON emits nested
    `categories/<cat-slug>/<token>.md` sub-hub pages for distinctive recurring tokens and
    points member notes at their most-specific sub-hub; OFF prunes the sub-hubs and
    reverts members to the plain category link on the next sync. The env escape
    `TASK_STATION_OBSIDIAN_SUBGROUPS` (on/off/1/0/true/false) WINS over config."""
    env = os.environ.get("TASK_STATION_OBSIDIAN_SUBGROUPS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    v = get("obsidian_subgroups")
    return True if v is None else bool(v)


def obsidian_story_groups_enabled():
    """Whether the mirror/export emits per-note story links + maintains the ORTHOGONAL
    `stories/` hub pages — default ON. NESTED inside category hubs (like sub-groups):
    it only takes effect when obsidian_category_hubs is also on. A story hub clusters
    the tasks that share a story id (from the structured `stories` field, referenced by
    >= 1 tasks), CROSS-category by nature. ON emits `stories/<id>.md` pages + a
    `[[stories/<id>]]` link in each member note; OFF prunes the hubs and drops the link
    on the next sync. The env escape `TASK_STATION_OBSIDIAN_STORY_GROUPS`
    (on/off/1/0/true/false) WINS over config."""
    env = os.environ.get("TASK_STATION_OBSIDIAN_STORY_GROUPS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    v = get("obsidian_story_groups")
    return True if v is None else bool(v)


def stream_enabled():
    """Whether the Tasktrail JSONL event ledger records mutations — default ON.
    INTERNAL + strictly LOCAL: every write lands under <data_dir>/stream/ (zero
    egress), so it is on by default (the durability fix for the 100-event in-blob
    cap and the future published contract). Turning it OFF makes emit() a no-op —
    nothing is written. The env escape `TASK_STATION_STREAM` (on/off/1/0/true/false)
    WINS over config; else the persisted `stream` flag (default on)."""
    env = os.environ.get("TASK_STATION_STREAM")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("stream", True))


def stream_dir():
    """External tee target for the stream — default OFF (None). This is the ONLY
    stream write OUTSIDE data_dir, so it is opt-in: when set, emit() tees each event
    line (byte-identical) and the manifest into <stream_dir>/. The env escape
    `TASK_STATION_STREAM_DIR` WINS over config; an empty value means OFF. Returns an
    expanded absolute path or None."""
    env = os.environ.get("TASK_STATION_STREAM_DIR")
    raw = env if env is not None else get("stream_dir")
    if not raw:
        return None
    return os.path.expanduser(raw)


def artifacts_root():
    """Root dir for rendered artifacts (the /brief HTML one-pagers). DERIVES from
    the data_dir seam by default — <data_dir>/artifacts — so it tracks a relocated
    task-station home (TASK_STATION_HOME / CLAUDE_CONFIG_DIR) and NEVER hardcodes a
    ~/ path. Precedence: env `TASK_STATION_ARTIFACTS_ROOT` WINS, else the persisted
    `artifacts_root` config key, else the derived default. Returns an expanded
    absolute path. Artifacts are build outputs, not documents — losing one is a
    non-event (`/brief` rebuilds it), so this lives under the mutable data home."""
    env = os.environ.get("TASK_STATION_ARTIFACTS_ROOT")
    raw = env if env is not None else get("artifacts_root")
    if raw:
        return os.path.expanduser(raw)
    return os.path.join(paths.data_dir(), "artifacts")


# -- the checker's thresholds (lib/checker.py) ----------------------------------
#
# Three numbers, all POSITIVE-ONLY. A zero or negative override is not honoured, it is
# REFUSED back to the default: zero report-days would put every DONE condition on every
# active task into the drift nag the moment it was set, and a zero claim timeout would
# fail every claim before its command produced a byte. A tunable whose extreme value
# breaks the feature is a footgun, and the cheap fix is to not accept the value.

def _positive_number(env_name, key, default):
    """A positive number from the env escape, else config, else `default`. Anything
    unparseable or <= 0 falls back — the same fail-open contract every checker entry
    point keeps."""
    raw = os.environ.get(env_name)
    if raw is None or not str(raw).strip():
        raw = get(key)
    if raw is None:
        return default
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return int(n) if n.is_integer() else n


def checker_report_days():
    """Days a countable DONE condition may sit with no completed step before the goal-
    drift check reports it. Default 3; `TASK_STATION_CHECKER_REPORT_DAYS` overrides."""
    return _positive_number("TASK_STATION_CHECKER_REPORT_DAYS", "checker_report_days", 3)


def checker_escalate_days():
    """Days after which a drifting condition escalates to stronger wording (and the nag
    re-arms once, because the tier is part of its fingerprint). Default 7 — from the plan
    that specified this check; `TASK_STATION_CHECKER_ESCALATE_DAYS` overrides."""
    return _positive_number("TASK_STATION_CHECKER_ESCALATE_DAYS",
                            "checker_escalate_days", 7)


def checker_claim_timeout():
    """Seconds one claim command may run during `claims verify`. Default 600 — generous,
    because a claim legitimately runs a test suite; `TASK_STATION_CHECKER_CLAIM_TIMEOUT`
    overrides. Never reached at session start: claims are never run there."""
    return _positive_number("TASK_STATION_CHECKER_CLAIM_TIMEOUT",
                            "checker_claim_timeout", 600)


# -- heal's one tunable (lib/heal.py) -------------------------------------------

def heal_goal_review_due():
    """Decisions that may land after a task's GOAL LINE was last written or re-read before
    that alone makes a heal due. Default 25; `TASK_STATION_HEAL_GOAL_REVIEW_DUE` overrides.

    POSITIVE-ONLY, the same contract as the three above and for a sharper reason: a zero
    would make every task carrying a goal permanently due, which is the always-on alarm the
    heal stamp was added to kill. `heal --goal-reviewed` records a re-read and resets the
    count without requiring the goal to be rewritten."""
    return _positive_number("TASK_STATION_HEAL_GOAL_REVIEW_DUE",
                            "heal_goal_review_due", 25)


def enabled_categories():
    """The configured active-category key list, or None when unconfigured
    (categories.enabled_keys() then defaults to CORE — the lean default)."""
    raw = get("enabled_categories")
    return raw if isinstance(raw, list) else None

def set_enabled_categories(keys):
    set("enabled_categories", list(keys))

def category_pack():
    """The active category-pack NAME (the discipline taxonomy in force). Delegates to
    categories.active_pack(), which validates the stored `category_pack` key against the
    shipped + org packs and falls back to 'dev' (the default pack — byte-comparable to
    pre-pack behaviour). Falls back to the raw value / 'dev' if categories is absent."""
    cats = _categories_module()
    if cats is not None:
        try:
            return cats.active_pack()
        except Exception:
            pass
    raw = get("category_pack")
    return raw if isinstance(raw, str) and raw else "dev"

def _categories_module():
    try:
        import categories as _c
        return _c
    except Exception:
        return None

def tint_theme():
    """The appearance control `tint_theme` ("auto" | "dark" | "light"), default
    "auto". "auto" follows the OS appearance; "dark"/"light" force the variant. This
    picks which VARIANT of the active theme renders (dark → Dark Sands, light → Light
    Sands for the shipped `sands` theme)."""
    val = get("tint_theme", "auto")
    return val if val in ("auto", "dark", "light") else "auto"

def active_theme():
    """The active theme NAME: config `theme`, validated against the available themes
    (shipped `sands` + any user themes), falling back to 'sands' for an absent/unknown
    value. The active theme supplies every category's full palette in two variants —
    the appearance (tint_theme) picks which renders. See
    categories.effective_themes / resolve_variant / tint_escape."""
    cats = _categories_module()
    default = getattr(cats, "DEFAULT_THEME", "sands") if cats else "sands"
    name = get("theme", default)
    if cats is None:
        return default
    try:
        avail = cats.available_themes()
    except Exception:
        return default
    return name if name in avail else default

def resolved_variant():
    """The variant ('dark'/'light') that will actually render, given tint_theme +
    the OS appearance. Thin wrapper over categories.resolve_variant for the board."""
    cats = _categories_module()
    if cats is None:
        return "dark"
    try:
        return cats.resolve_variant()
    except Exception:
        return "dark"

def _variant_label(variant, theme=None):
    """'{Dark|Light} {ThemeDisplay}' for the active theme's variant, via
    categories.variant_label (falls back to a bare capitalised variant)."""
    cats = _categories_module()
    theme = theme or active_theme()
    if cats is not None and hasattr(cats, "variant_label"):
        try:
            return cats.variant_label(theme, variant)
        except Exception:
            pass
    return variant.capitalize()

def _enabled_summary():
    """`3/12 (CORE)`-style summary of the active category set, or `N/12 (custom)`
    once the user has configured it. The factory default is carried in the board
    description parens now, so this never embeds the word "default"."""
    cats = _categories_module()
    if cats is None:
        return "n/a"
    enabled = cats.enabled_keys()
    total = len(cats.all_keys())
    name = "CORE" if enabled_categories() is None else "custom"
    return "%d/%d (%s)" % (len(enabled), total, name)

def _desktop_bridge_summary():
    """`installed` / `off` for the no-arg config board (lazy import — setup imports
    config, so keep this out of module scope)."""
    try:
        import setup
        installed, _ = setup.desktop_bridge_status()
        return "installed" if installed else "off"
    except Exception:
        return "off"

def _statusline_summary():
    """`installed (host)` / `provider-only` / `off` for the no-arg config board
    (lazy import — setup imports config, so keep this out of module scope)."""
    try:
        import setup
        return setup.statusline_status()
    except Exception:
        return "off"

def _hud_summary():
    """`installed (host)` / `provider-only` / `off` for the cost-HUD provider (lazy
    import — setup imports config)."""
    try:
        import setup
        return setup.hud_status()
    except Exception:
        return "off"


def _obsidian_sandbox_summary():
    """`on` / `off` — whether the configured vault is in the sandbox write-allowlist
    (lazy import, like the others)."""
    try:
        import setup
        return "on" if setup.sandbox_allowwrite_status() else "off"
    except Exception:
        return "off"

def board_rows():
    """The (flag, value, options-or-None, description, extra_lines-or-None, set_with-or-
    None) 6-tuples behind the config board — the SINGLE source the terminal
    `render_board()` formats AND the visual HTML board's config help panel reads, so
    the two never drift. Pure data: every value comes from this module's getters; no
    terminal width / formatting applied here. options=None marks a value-only row (the
    paths, --board-browser) that carries no OPTIONS cell; the VALUE column always shows
    the CURRENT value, the factory default lives only in the description parens.
      • extra_lines (5th element): a list[str] of extra explanation lines for the
        expansion (each rendered on its OWN line), or None.
      • set_with (6th element): the FULL "Set with" command string, or None to DERIVE
        a generic one. Both renderers put the "Set with:" line FIRST in every row's
        expansion."""
    import setup
    cats = get("categories"); n_cat = len(cats) if isinstance(cats, dict) else 0
    # Category-pack facts for the --category-pack row + the pack-aware CORE label.
    catsmod = _categories_module()
    pack_name = category_pack()
    if catsmod is not None:
        try:
            pack_opts = " · ".join(catsmod.available_packs())
        except Exception:
            pack_opts = "dev"
        pack_val = "%s (%s)" % (pack_name, catsmod.pack_display(pack_name))
        try:
            core_label = " · ".join(catsmod.CATEGORIES[k]["tag"]
                                    for k in catsmod.CORE if k in catsmod.CATEGORIES)
        except Exception:
            core_label = "BUG · FEATURE · GENERAL"
    else:
        pack_opts, pack_val, core_label = "dev", pack_name, "BUG · FEATURE · GENERAL"
    has_policy = ("policy" in setup._manifest())
    statusline_raw = _statusline_summary()
    statusline_val = "off" if statusline_raw == "off" else "on"
    bridge_installed = _desktop_bridge_summary() == "installed"
    bridge_val = "on" if bridge_installed else "off"
    if n_cat == 0:
        overrides_val = "none"
    elif n_cat == 1:
        overrides_val = "1 override"
    else:
        overrides_val = "%d overrides" % n_cat
    browser_val = board_browser() or "default"
    return [
        ("--category-pack", pack_val, pack_opts,
         "Which category PACK (discipline taxonomy) is active — each retargets the same colour slots for a discipline; ⚫ GENERAL is always present; per-slot overrides still win (default: dev)",
         ["List packs: /task-station:config --category-pack list.",
          "Packs: dev (software), finance, hr, exec, general (lean tri-slot) — plus any org packs.",
          "Org packs merge from category_packs.json in the data dir (see CATEGORIES.md)."],
         "/task-station:config --category-pack <name>"),
        ("--categories", _enabled_summary(), None,
         "Which category tags are available on the board. Starts at CORE (%s) and adds a category automatically the first time a task uses it (default: CORE)" % core_label,
         ["Turn a category on/off with the command above (toggle).",
          "Rename a category's [TAG]/label by editing categories.json (edit)."],
         "/task-station:config --enable <key>  ·  --disable <key>"),
        ("--auto-categories", "on" if auto_categories_enabled() else "off", "on · off",
         "Auto-enable a category the first time a task is assigned to it (default: on)", None, None),
        ("--category-overrides", overrides_val, None,
         "Your custom category tags/labels, saved in categories.json (default: none)",
         ["Structure — one entry per colour slot you customize:",
          '{ "green": { "tag": "PROJECT", "label": "project work" } }',
          "Only tag + label are needed; the emoji/colour come from the slot."],
         "/task-station:config (edit categories.json)"),
        ("--bare-cmds", "on" if bare_commands() else "off", "on · off",
         "Install bare /todo, /done, /pin, /unpin, /repos, /save, /history, /prompts, /glossary, /brief aliases; otherwise use the /task-station:<name> forms of the same commands (default: off)", None, None),
        ("--update-check", "on" if update_check_enabled() else "off", "on · off",
         "Notifies you when a new version is available (checks once per day) (default: off)", None, None),
        ("--board-autorefresh", "on" if board_autorefresh_enabled() else "off", "on · off",
         "Enables auto-refresh of the /todo board (default: off)", None, None),
        ("--interbrain", interbrain_mode(), "on · off · auto",
         "Interbrain federation: peer/org feeds render as read-only foreign rows + graph nodes in the board (owner chip, lock, memo-only). auto = on when >1 brain or peer feeds exist, else a single-brain board (default: auto)", None, None),
        ("--org-label", org_label(), None,
         'Display label for the org brain everywhere it appears (default: "Org brain"; e.g. "Company Brain")', None, None),
        ("--knowledge-plane", knowledge_plane_mode(), "on · off · auto",
         "Knowledge plane: your vault's notes render as a second plane above the task plane in the board graph, panned between rather than zoomed. auto = on when a vault is configured and holds at least one note, else off (default: auto)",
         ["READ-ONLY and board-only — nothing is ever written into the vault.",
          "Separate from --knowledge-graph, which gates the vault WRITE path.",
          "With no vault the plane is absent and the board is unchanged."],
         None),
        ("--done-closes-window", "on" if done_closes_window_enabled() else "off", "on · off",
         "Auto-close the terminal window ~1s after a no-arg /done closes this session's task (default: off — window stays open)",
         ["Opt-in because there is no reliable way to tell a human-typed /done from",
          "a model Skill-tool /done, so the destructive close is off by default.",
          "The /done <task#> path never closes a window regardless of this flag."],
         None),
        ("--board-browser", browser_val, None,
         "Which browser opens the board (default: your system default)",
         ['Opens board.html in this browser (macOS: open -a "<App>").',
          "Unset = your system default browser.",
          'Examples: "Google Chrome", "Firefox", "Safari", "Arc".',
          "Clear with: /task-station:config --board-browser (no value)."],
         '/task-station:config --board-browser "<App>"'),
        ("--theme", active_theme(), "sands",
         "The colour theme for the board and terminal tint (default: sands)", None,
         "/task-station:config --theme <name>"),
        ("--tint-theme", tint_theme(), "auto · dark · light",
         "Which variant renders — auto follows your system's light/dark setting (default: auto)", None, None),
        ("--tint", "on" if tint_enabled() else "off", "on · off",
         "Tint your terminal to the active task's category colour (default: on)", None, None),
        ("--title", "on" if title_enabled() else "off", "on · off",
         "Set the terminal title to the attached task — '#<seq>: <title>' (default: on)", None, None),
        ("--guaranteed-tracking", "on" if guaranteed_tracking_enabled() else "off", "on · off",
         "Auto-create and attach a task on every new session (cleaned up if left untouched) (default: off)", None, None),
        ("--auto-checkpoint", "on" if auto_checkpoint_enabled() else "off", "on · off",
         "Automatically checkpoint the attached task at compaction + nudge to keep its digest fresh (default: off)",
         ["Compaction-safe, and cheap — no full save every turn:",
          "on — a PostCompact hook stashes the harness's compaction summary into the task's history (free, no model tokens); a post-compaction nudge + a staleness nudge ask the model to refresh the structured digest",
          "off — none of the above fire (today's behaviour)"], None),
        ("--checkpoint-pct", str(checkpoint_pct()) if checkpoint_pct() else "off", "<1-95>",
         "with --auto-checkpoint on: prompt a full /todo save once the MEASURED context (from the transcript's usage block) reaches this %% of --context-window, before auto-compaction (default 65; 0/off disables)",
         ["/todo save IS the proactive structured checkpoint — a task-shaped compaction",
          "you resume from (fresh session + /todo <n>), better than the generic auto-summary.",
          "This trigger is measured from real token usage (not a byte estimate), so it fires",
          "at a true window-relative point; 0/off disables it (the PostCompact stash still runs)."],
         "/task-station:config --checkpoint-pct <1-95> | off"),
        ("--context-window", str(context_window()), "<tokens>",
         "The model's context-window size, the denominator --checkpoint-pct measures against (default 200000; raise it for a larger window, e.g. 1000000)", None,
         "/task-station:config --context-window <tokens>"),
        ("--checkpoint-at", str(checkpoint_at()) if checkpoint_at() else "off", "<tokens>",
         "with --auto-checkpoint on: LEGACY/fallback proactive trigger — prompt a full /todo save when the transcript-size token ESTIMATE grows past this, before auto-compaction (default off; use --checkpoint-pct instead; 0 = off)",
         ["A back-compat fallback for when a real usage measurement isn't available — the",
          "estimate is a rough transcript-size heuristic. Prefer --checkpoint-pct (measured).",
          "An explicitly stored value keeps firing the old absolute trigger; 0/off disables",
          "it (the PostCompact stash fallback still runs)."],
         "/task-station:config --checkpoint-at <tokens> | off"),
        ("--checkpoint-milestone-edits", str(checkpoint_milestone_edits()) if checkpoint_milestone_edits() else "off", "<count>",
         "with --auto-checkpoint on: fire the light staleness nudge only after this many meaningful events (file edits / status promotions) since the last digest refresh (default 5; 0/off = nudge on any staleness)", None,
         "/task-station:config --checkpoint-milestone-edits <count> | off"),
        ("--ultracode-hints", "on" if ultracode_hints_enabled() else "off", "on · off",
         "Suggest multi-agent breadth on big research/review/data tasks (default: on)", None, None),
        ("--notify", "on" if notify_enabled() else "off", "on · off",
         "macOS banner when a delegated worker run finishes or fails (default: off)", None, None),
        ("--delegate-bypass-permissions", "on" if delegate_bypass_permissions() else "off", "on · off",
         "OPT-IN: spawn worktree --bg workers with bypassPermissions instead of the default "
         "dontAsk (default: off). Default OFF = dontAsk: fail-closed, non-allowlisted tools "
         "auto-denied, never hangs, no dangerous-skip — like the old -p workers",
         ["on — needs a ONE-TIME `claude --dangerously-skip-permissions` acceptance per machine; "
          "lets workers run ANY tool unattended (worktree-only).",
          "off (default) — dontAsk + --allowedTools edit toolset; anything else fails closed."],
         None),
        ("--reap-workers-on-done", "on" if reap_workers_on_done() else "off", "on · off",
         "Stop this task's live --bg workers when it closes so they don't linger/respawn "
         "in Agent View (default: on)",
         ["on — closing a task removes each confirmed delegate worker's session-store file",
          "  and kills its process group. Airtight: only a registry-registered, role==worker,",
          "  task-station-named, non-busy, non-current session is ever touched — a real",
          "  working/hub session is NEVER reaped.",
          "off — the reap is a complete no-op; stop workers by hand (Agent View / kill)."],
         None),
        ("--notify-webhook", notify_webhook() or "unset", None,
         "POST worker finished/failed events to this URL (Slack/Teams/ntfy) (default: unset)",
         ["JSON body: {event, task_seq, repo, label, worktree, cost_usd, ts}.",
          "event is worker_finished | worker_failed.",
          "For ntfy.sh point it at your topic URL, e.g. https://ntfy.sh/<topic>.",
          "Clear with: /task-station:config --notify-webhook (no value)."],
         '/task-station:config --notify-webhook "<url>"'),
        ("--strict-delegation", "on" if has_policy else "off", "on · off",
         "Write the delegation-rules block into your CLAUDE.md (reversible) (default: off)", None, None),
        ("--desktop-bridge", bridge_val, "on · off",
         "Let Claude Desktop read your tasks through a local MCP bridge (default: off)",
         ["States:",
          "on — MCP bridge wired into Claude Desktop",
          "off — not wired",
          "Current: %s" % bridge_val],
         "/task-station:config --desktop-bridge on | off"),
        ("--statusline", statusline_val, "on · off",
         "Show task-station in your status bar (default: off)",
         ["States:",
          "on — task-station owns the status bar",
          "provider-only — provides a segment; another tool owns the bar",
          "off — not installed",
          "Current: %s" % statusline_raw],
         "/task-station:config --statusline on | off"),
        ("--hud", "on" if hud_enabled() else "off", "on · off",
         "Show the cost HUD (model badge header + task/session/5-hour/week/total $ rows) in your status bar (default: off)",
         ["Installs the status-bar host (if nothing else owns it) + a cost-HUD segment provider.",
          "The HUD owns the whole bar: its header carries the model badge + your task inline (the",
          "separate task segment is suppressed while it's on, restored when off).",
          "Every figure is priced by the shared usage ledger + rate table — one scanner, no drift.",
          "Toggle which rows appear with --hud-rows; the eco column with --hud-eco.",
          "Current: %s" % _hud_summary()],
         "/task-station:config --hud on | off"),
        ("--hud-rows", ",".join(hud_rows()), None,
         "Which cost-HUD rows show, in order (default: task,session,fivehour,week,total)",
         ["Comma-separated subset of: task · session · fivehour · week · total.",
          "session/fivehour read the status-line stdin; task/week/total read the usage ledger.",
          "Old names still work: `limits` → fivehour, `5-hour` → fivehour; `turn` is dropped."],
         "/task-station:config --hud-rows task,session,week,total"),
        ("--hud-eco", "on" if hud_eco_enabled() else "off", "on · off",
         "Append an eco-footprint column (≈ driving / coffee / …) to the HUD (default: on)", None, None),
        ("--obsidian-vault", obsidian_vault() or "off", None,
         "Export tasks into this Obsidian vault (one-way; opt-in). Files live under <vault>/task-station/ (default: off)",
         ["Set an absolute path to turn export on; pass no value to turn it off.",
          'Example: /task-station:config --obsidian-vault "~/Documents/Obsidian Vault".',
          "Notes are written on create/update/done/save; run `task-station obsidian --sync-all` to backfill."],
         '/task-station:config --obsidian-vault "<path>"  ·  (no value = off)'),
        ("--obsidian-daily-note", "on" if obsidian_daily_note_enabled() else "off", "on · off",
         "Append a line to the vault's daily note when a task closes or a /todo save checkpoints (default: off)", None, None),
        ("--obsidian-daily-heading", obsidian_daily_heading(), None,
         'Heading the daily-note entries go under, created if absent (default: "## Claude sessions")', None,
         '/task-station:config --obsidian-daily-heading "<heading>"'),
        ("--obsidian-prompts", "on" if obsidian_prompts_enabled() else "off", "on · off",
         "Write the full prompt trail (## Prompts) into exported vault notes (default: off)",
         ["Opt-in — a synced vault may reach third-party services, so prompt EXPORT stays off",
          "even though prompt capture (--usage-prompts) is on by default.",
          "The `task-station export` command gates the same trail behind `--include prompts`."],
         None),
        ("--obsidian-category-hubs", "on" if obsidian_category_hubs_enabled() else "off", "on · off",
         "Cluster the export/vault graph by category: a [[categories/<slug>]] link in each note + a hub page per category (default: on)",
         ["On by default — the mirror is already opt-in (a vault/export dir), so once you",
          "export, category hubs cluster your graph. Off drops the per-note category link",
          "and prunes the categories/ hub pages on the next sync/export.",
          "Hubs live under <target>/task-station/categories/ (vault) or <dir>/categories/."],
         "/task-station:config --obsidian-category-hubs on | off"),
        ("--obsidian-subgroups", "on" if obsidian_subgroups_enabled() else "off", "on · off",
         "Emergent sub-groups within a category: distinctive recurring title tokens auto-cluster into nested sub-hub pages (default: on)",
         ["Nested inside category hubs — only takes effect when --obsidian-category-hubs is on.",
          "On clusters e.g. many 'hammerspoon …' PERSONAL tasks under a hammerspoon sub-hub",
          "at categories/<cat-slug>/<token>.md; member notes link the sub-hub instead of the",
          "bare category. Off prunes the sub-hubs and reverts members to the category link.",
          "Deterministic + local — no LLM. TASK_STATION_OBSIDIAN_SUBGROUPS overrides."],
         "/task-station:config --obsidian-subgroups on | off"),
        ("--obsidian-story-groups", "on" if obsidian_story_groups_enabled() else "off", "on · off",
         "Story hubs: an orthogonal, cross-category [[stories/<id>]] link + a hub page per story referenced by >= 1 task (default: on)",
         ["Nested inside category hubs — only takes effect when --obsidian-category-hubs is on.",
          "Groups tasks by their STRUCTURED story field (a work-item id, never title tokens):",
          "any story cited by a task gets a stories/<id>.md hub listing its members",
          "(cross-category) with the ADO link when known; each member note gains a",
          "[[stories/<id>|Story <id>]] link IN ADDITION to its category link. Off prunes the",
          "hubs and drops the link. TASK_STATION_OBSIDIAN_STORY_GROUPS overrides."],
         "/task-station:config --obsidian-story-groups on | off"),
        ("--knowledge-graph", "on" if knowledge_graph_enabled() else "off", "on · off",
         "Second-brain tier: knowledge edges + ## Related wikilinks (default: off)",
         ["Adds task<->note co-citation edges to the board mini-graph, a per-task",
          "'Related knowledge' panel, and ## Related wikilink emission into the vault.",
          "Inert without an --obsidian-vault (the flag alone changes nothing).",
          "After enabling, run `task-station obsidian --sync-all` to backfill ## Related."],
         "/task-station:config --knowledge-graph on | off"),
        ("--owner", owner() or "unset", None,
         "Owner handle for a SHARED vault — notes nest under <target>/<owner>/ and carry the handle (default: unset = single-owner)",
         ["Set a handle so several people can export into ONE vault without colliding:",
          "notes + sidecar index move under the owner subfolder, frontmatter/manifest/",
          "daily-note lines carry the handle. Unset = today's flat, single-owner layout.",
          "After setting, run `task-station obsidian --sync-all` to relocate existing notes.",
          "Clear with: /task-station:config --owner (no value)."],
         '/task-station:config --owner "<handle>"  ·  (no value = unset)'),
        ("--obsidian-sandbox", _obsidian_sandbox_summary(), "on · off",
         "Instant inline exports when the vault is in a protected folder (~/Documents, iCloud) (default: off)",
         ["Adds the vault to sandbox.filesystem.allowWrite in your settings.json so a",
          "SANDBOXED in-session export can write it directly. Without it exports still",
          "sync — the unsandboxed Stop/SessionStart hooks auto-flush — just not instantly.",
          "Never forces sandbox on; reverse with --obsidian-sandbox off.",
          "Only meaningful with a vault set (--obsidian-vault)."],
         "/task-station:config --obsidian-sandbox on | off"),
        ("--usage-tracking", "on" if usage_tracking_enabled() else "off", "on · off",
         "Track per-task model usage + derived $ from your local Claude Code transcripts (default: on)",
         ["Reads only your own local session files (~/.claude/projects); nothing leaves the machine.",
          "off — the usage ledger, its stats-line %/$ segment, and the hook flush go inert."], None),
        ("--usage-prompts", "on" if usage_prompts_enabled() else "off", "on · off",
         "Capture prompt text into the ledger so tasks show what was asked (default: on)",
         ["Same-machine only — stored in tasks.db, not exported (export stays opt-in).",
          "off — usage tokens/$ still track, but no prompt rows are stored."], None),
        ("--board-prompts", "on" if board_prompts_enabled() else "off", "on · off",
         "Show the captured prompt trail on the visual board (default: on)",
         ["Board is local-only, so same-machine prompt text is fine (export stays opt-in).",
          "off — the Recent-prompts preview + the full-list <details> are omitted.",
          "Independent of --usage-prompts (capture); this only gates board DISPLAY."], None),
        ("--editor-scheme", editor_scheme(), None,
         "URI scheme the board uses to open file paths (default: auto-detected from your editor)",
         ["Board file links become <scheme>://file/<abspath> (or a plain file:// link for `file`);",
          "a copy-path button is always the fallback. Examples: cursor, vscode, zed, subl, file.",
          "Unset = auto-detect ($VISUAL/$EDITOR hint, then installed editor apps, else file).",
          "Restore auto-detect with: /task-station:config --editor-scheme (no value)."],
         "/task-station:config --editor-scheme <scheme>"),
        ("--usage-billing-mode", usage_billing_mode(), "api · subscription",
         "How the derived $ is framed (default: api)",
         ["api — metered: the derived $ IS your marginal bill (API/Console/Enterprise usage).",
          "subscription — flat-rate seat: the $ is the API-equivalent value of the work",
          "  (not billed per token; overage past your limit bills at these rates)."],
         "/task-station:config --usage-billing-mode api | subscription"),
        ("--recap", "on" if recap_enabled() else "off", "on · off",
         "Auto-generate the private weekly usage recap (default: off)",
         ["A strictly LOCAL HTML digest of your week under <data_dir>/recaps/ — never synced.",
          "When on, the Stop hook writes last week's recap once per week (fail-open, 0 tokens).",
          "The `task-station recap` command works regardless of this toggle."],
         "task-station config --recap on | off"),
        ("--recap-curator-cmd", recap_curator_cmd() or "off", None,
         "Optional command that turns recap aggregate stats into 3 tailored tips (default: off)",
         ["Receives PRIVACY-SAFE aggregates on stdin (counts/ratios/titles — NEVER prompt text);",
          "must print a JSON list of {observation, suggestion, command}. No value clears it."],
         "task-station config --recap-curator-cmd '<cmd>'"),
        ("--workspace-dirs", ":".join(get("workspace_dirs") or []) or "unset", None,
         "Repo root folders so 'delegate --project <name>' can find your repos (default: unset)", None, None),
        ("--artifacts-root", artifacts_root(), None,
         "Root dir for rendered /brief artifacts (default: derived from data-dir)",
         ["TASK_STATION_ARTIFACTS_ROOT env wins; --artifacts-root with no value clears the override."],
         "task-station config --artifacts-root <path>"),
        ("--data-dir", paths.data_dir(), None,
         "Where your tasks and config are stored — read-only; set with $TASK_STATION_HOME", None, None),
        ("--reset", "—", "(action)",
         "reset ALL settings above to factory defaults — asks to confirm (default: —)", None,
         "task-station config --reset confirm"),
    ]

def render_board():
    """The unified, width-aware `task-station config` board (no-arg view).

    A two-line legend heads the board: the MIDDLE column is the current value/STATE
    (sometimes a reported status that is NOT one of the choices — e.g. --statusline
    "provider-only" against the on·off choices) and the OPTIONS column is what you
    pass to set the flag.

    Every setting renders as a two-line STANZA: an aligned
    `<flag>  <current value>  <options>` line, then an indented description that
    ends with the factory default in parens — `(default: X)`. A blank line
    separates every stanza. The flag / value / options columns are sized to their
    widest cell per render (the path-valued rows are excluded from the value width
    so a long path never inflates the grid); on a narrow terminal the description
    wraps with a hanging indent under itself, never under a column. The former
    separate `status`, `--workspace-dirs`, and `--data-dir` blocks are folded into
    this single list — one board, nothing duplicated, no `* = default` markers."""
    import textwrap
    import term
    width = term.width()
    indent = "  "
    gutter = "  "
    desc_indent = "      "   # 6 cols — the description hangs under the flag, not a column

    rows = board_rows()
    w_flag = max(len(r[0]) for r in rows)
    w_val = max(len(r[1]) for r in rows if r[2] is not None)
    wrap_w = max(24, width - len(desc_indent))

    lines = []
    # --- top header: store path (+ set/reset hint); store breaks to its own line
    #     rather than overflow the terminal width.
    store_line = "task-station config        store: %s" % _path()
    if len(store_line) <= width:
        lines.append(store_line)
    else:
        lines.append("task-station config")
        lines.append(indent + "store: %s" % _path())
    # two legend lines: the first names the columns (so a current value/STATE that is
    # NOT one of the choices — e.g. --statusline "provider-only" vs on·off — reads as
    # reported state, not a settable input); the second is the set/reset hint.
    lines.append("columns:  <flag>   ·   current value/state   ·   choices = what you pass")
    lines.append("set a flag: task-station config --<flag> <value>     ·     reset a flag: --<flag> default")

    for r in rows:
        flag, value, options, desc = r[0], r[1], r[2], r[3]
        extra_lines = r[4] if len(r) > 4 else None
        set_with = r[5] if len(r) > 5 else None
        lines.append("")
        if options is None:
            lines.append(indent + flag.ljust(w_flag) + gutter + value)
        else:
            lines.append(indent + flag.ljust(w_flag) + gutter
                         + value.ljust(w_val) + gutter + options)
        # 1. "Set with:" — ALWAYS first; derived when set_with is None.
        cmd = set_with or _derive_set_with(flag, options)
        for seg in (textwrap.wrap("Set with: " + cmd, wrap_w) or [""]):
            lines.append(desc_indent + seg)
        # 2. the description with any trailing "(default: X)" stripped.
        m = re.search(r"\(default:\s*(.*?)\)\s*$", desc or "")
        desc_body = re.sub(r"\s*\(default:.*\)\s*$", "", desc or "")
        for seg in (textwrap.wrap(desc_body, wrap_w) or [""]):
            lines.append(desc_indent + seg)
        # 3. an explicit "Default: X." line when one was parsed from the description.
        if m:
            lines.append(desc_indent + "Default: %s." % m.group(1))
        # 4. each extra_lines entry on its OWN line (wrapped under the description).
        if extra_lines:
            for el in extra_lines:
                for seg in (textwrap.wrap(el, wrap_w) or [""]):
                    lines.append(desc_indent + seg)

    return "\n".join(lines)


def _derive_set_with(flag, options):
    """The generic "Set with" command for a row whose set_with is None: the choice
    tokens joined by " | " when options is non-None, else a "<value>" placeholder. Kept
    identical to render_board.py's HTML derivation so the two boards never drift."""
    if options is not None:
        toks = [t.strip() for t in str(options).split("·")]
        return "/task-station:config %s <%s>" % (flag, " | ".join(toks))
    return "/task-station:config %s <value>" % flag

def _categories_status(cats):
    enabled = cats.enabled_keys()
    lines = ["Enabled categories (%d/%d):" % (len(enabled), len(cats.all_keys()))]
    for k in enabled:
        m = cats.CATEGORIES[k]
        perm = "   (permanent)" if k == cats.PERMANENT else ""
        lines.append("  %-7s %s %-11s %s%s" % (k, m["dot"], "[%s]" % m["tag"], m["label"], perm))
    disabled = [k for k in cats.all_keys() if k not in enabled]
    if disabled:
        lines.append("  off: " + ", ".join(disabled))
    lines.append("")
    core_label = " · ".join(cats.CATEGORIES[k]["tag"]
                            for k in cats.CORE if k in cats.CATEGORIES) or "GENERAL"
    lines.append("The board starts lean at CORE (%s) and grows on its own:" % core_label)
    lines.append("assigning a task to a new category auto-enables that slot. Freeze the set")
    lines.append("with: config --auto-categories off  (currently %s)."
                 % ("on" if auto_categories_enabled() else "off"))
    lines.append("")
    lines.append("Toggle individual slots: config --enable <key> · config --disable <key>")
    lines.append("(⚫ GENERAL is permanent — always on, cannot be disabled.)")
    return "\n".join(lines)


def cmd_categories(arg):
    """Handle `config --categories [...]`:
      (no arg)  → show the enabled set + how to toggle slots
      edit      → print the config.json path (legacy behaviour)
    """
    if arg == ["edit"]:
        print(_path()); return
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    if arg:
        print("usage: config --categories [edit]"); return
    print(_categories_status(cats))


def _list_packs():
    """List the available category packs (shipped + org-supplied), marking the active
    one. Each row: name, display label, slot count, one-line description."""
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    active = category_pack()
    shipped = getattr(cats, "PACKS", {}) or {}
    try:
        eff = cats.effective_packs()
    except Exception:
        eff = dict(shipped)
    lines = ["Category packs (* = active):"]
    for name in cats.available_packs():
        mark = "*" if name == active else " "
        kind = "shipped" if name in shipped else "org"
        entry = eff.get(name, {}) if isinstance(eff, dict) else {}
        n = len(entry.get("slots", {})) if isinstance(entry, dict) else 0
        label = cats.pack_display(name)
        desc = cats.pack_description(name)
        lines.append("  %s %-9s %-14s %d slots (%s)%s"
                     % (mark, name, "(%s)" % label, n, kind,
                        "   " + desc if desc else ""))
    lines.append("")
    lines.append("Select a pack:  config --category-pack <name>")
    lines.append("Each pack retargets the SAME colour slots for a discipline; ⚫ GENERAL is")
    lines.append("always present. Per-slot overrides in config.json still win over the pack,")
    lines.append("and org packs merge from category_packs.json in the data dir (see CATEGORIES.md).")
    print("\n".join(lines))


def cmd_category_pack(arg):
    """Handle `config --category-pack [...]`:
      (no arg) / list   → list packs, mark active
      <name>            → select that pack as the active taxonomy
    """
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    if not arg or arg == ["list"]:
        return _list_packs()
    if len(arg) != 1:
        print("usage: config --category-pack [<name> | list]"); return
    name = arg[0]
    avail = cats.available_packs()
    if name not in avail:
        print("Unknown pack '%s'. Available: %s" % (name, ", ".join(avail))); return
    set("category_pack", name)
    print("category_pack = %s   (%s)" % (name, cats.pack_display(name)))


def toggle_category(color, on):
    """Enable/disable a single slot. Refuses to disable ⚫ GENERAL (permanent).
    Materializes the current effective set first, so toggling from the
    unconfigured (full) default behaves intuitively."""
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    key = cats.resolve(color)
    if key is None:
        print("Unknown category '%s'. Use a key, emoji, or [TAG]." % color); return
    m = cats.CATEGORIES[key]
    if not on and key == cats.PERMANENT:
        print("Refusing to disable %s [%s] — GENERAL is permanent." % (m["dot"], m["tag"])); return
    cur = list(cats.enabled_keys())
    if on and key not in cur:
        cur.append(key)
    elif not on:
        cur = [k for k in cur if k != key]
    if cats.PERMANENT not in cur:
        cur.append(cats.PERMANENT)
    keys = [k for k in cats.all_keys() if k in cur]
    set_enabled_categories(keys)
    print("%s %s [%s] — enabled set now: %s"
          % ("enabled" if on else "disabled", m["dot"], m["tag"], " ".join(keys)))


# --- themes -------------------------------------------------------------------
# A THEME is a named full-palette set (per category: bg/fg/bold/cursor/sel + 16
# ANSI). `--theme` is verb-first: the first token is a VERB if in THEME_VERBS,
# else a theme NAME to select. RESERVED names can never be saved.
THEME_VERBS = {"save", "edit", "preview", "list"}
RESERVED_THEME_NAMES = {"save", "edit", "preview", "list", "show", "default"}
_THEME_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _list_themes():
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    active = active_theme()
    shipped = getattr(cats, "THEMES", {}) or {}   # NB: module-level set() shadows builtin
    variant = resolved_variant()
    lines = ["Themes (* = active):"]
    for name in cats.available_themes():
        mark = "*" if name == active else " "
        kind = "shipped" if name in shipped else "user"
        variants = " · ".join(_variant_label(v, name) for v in getattr(cats, "VARIANTS", ("dark", "light")))
        lines.append("  %s %-12s (%s)   %s" % (mark, name, kind, variants))
    lines.append("")
    lines.append("Appearance: --tint-theme %s → %s" % (tint_theme(), _variant_label(variant)))
    lines.append("")
    lines.append("Select:  config --theme <name>")
    lines.append("Appearance:  config --tint-theme auto|dark|light")
    lines.append("Save current palette as a theme:  config --theme save <name>")
    lines.append("Edit user themes (config.json):   config --theme edit")
    lines.append("Render a preview gallery:         config --theme preview")
    print("\n".join(lines))


def _theme_save(name):
    """Snapshot BOTH variants (dark + light) of the active theme's currently-resolved
    palette into config.json themes[<name>] — a fully self-contained copy, independent
    of the current appearance. Each variant captures every category (resolving the
    active theme over the shipped fallback). Refuses reserved names and names not
    matching ^[a-z0-9][a-z0-9_-]*$."""
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    if name in RESERVED_THEME_NAMES:
        print("Refusing to save theme '%s' — reserved name (one of: %s)."
              % (name, ", ".join(sorted(RESERVED_THEME_NAMES)))); return
    if not _THEME_NAME_RE.match(name):
        print("Refusing to save theme '%s' — invalid name. Use a lowercase letter or "
              "digit, then any of [a-z0-9_-] (e.g. 'my-theme')." % name); return
    active = active_theme()
    entry = {}
    for variant in getattr(cats, "VARIANTS", ("dark", "light")):
        pals = {}
        for key in cats.CATEGORIES:
            try:
                p = cats.theme_palette(active, key, variant)
            except Exception:
                p = None
            if isinstance(p, dict) and p:
                pals[key] = copy.deepcopy(p)
        if pals:
            entry[variant] = pals
    if not entry:
        print("No active theme palette to snapshot (active = '%s')." % active); return
    d = _load()
    themes = d.get("themes")
    if not isinstance(themes, dict):
        themes = {}
    themes[name] = entry
    d["themes"] = themes
    _save(d)
    labels = " + ".join(_variant_label(v, active) for v in entry)
    counts = ", ".join("%s: %d cats" % (v, len(entry[v])) for v in entry)
    print("saved theme '%s' — snapshot of '%s' (both variants: %s; %s) → %s"
          % (name, active, labels, counts, _path()))


def _theme_preview():
    """Render the gallery for effective_themes() to <data_dir>/themes-preview.html."""
    import sys as _sys
    out = os.path.join(paths.data_dir(), "themes-preview.html")
    try:
        # realpath derefs the ~/.claude/task-station-engine symlink → real lib/
        # so tools/ resolves when run via the stable engine path (not just in-repo).
        here = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        tools = os.path.join(here, "tools")
        if tools not in _sys.path:
            _sys.path.insert(0, tools)
        import render_palettes
        html = render_palettes.render_html()
        os.makedirs(paths.data_dir(), exist_ok=True)
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, out)
        print(out)
    except Exception as e:
        print("preview failed: %s" % e)


def cmd_theme(arg):
    """Handle `config --theme [...]` (verb-first grammar):
      (no arg) / list   → list shipped + user themes, mark active
      <name>            → select that theme as active
      save <name>       → snapshot the effective active palette into config themes[<name>]
      edit              → print the config.json path (edit user themes there)
      preview           → render the gallery for effective_themes() to a HTML file
    """
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    if not arg:
        return _list_themes()
    verb, rest = arg[0], arg[1:]
    if verb in THEME_VERBS:
        if verb == "list":
            return _list_themes()
        if verb == "edit":
            print(_path()); return
        if verb == "preview":
            return _theme_preview()
        if verb == "save":
            if len(rest) != 1:
                print("usage: config --theme save <name>"); return
            return _theme_save(rest[0])
    # not a verb → a theme NAME to select
    if len(arg) != 1:
        print("usage: config --theme [<name> | save <name> | edit | preview | list]"); return
    name = arg[0]
    avail = cats.available_themes()
    if name not in avail:
        print("Unknown theme '%s'. Available: %s" % (name, ", ".join(avail))); return
    set("theme", name)
    print("theme = %s" % active_theme())


# --- factory reset -----------------------------------------------------------
# The config.json keys the board manages. `--reset confirm` pops exactly these
# (so get()'s defaults take over). NOT touched: tasks.db (a separate file) and
# externally-installed integrations that live OUTSIDE config.json — the bare
# /todo,/done command files, the Claude Desktop bridge entry, and the CLAUDE.md
# delegation block. Those are reported with their off-commands, never silently
# removed, so the user removes them deliberately.
RESET_KEYS = [
    "enabled_categories", "auto_categories", "categories", "category_pack",
    "bare_commands", "update_check", "board_autorefresh", "done_closes_window",
    "board_browser", "board_prompts",
    "theme", "tint_theme",
    "tint", "title", "guaranteed_tracking", "auto_checkpoint", "checkpoint_at",
    "checkpoint_pct", "context_window", "checkpoint_milestone_edits",
    "statusline", "hud", "hud_rows", "hud_eco", "ultracode_hints",
    "notify", "notify_webhook", "delegate_bypass_permissions",
    "reap_workers_on_done",
    "obsidian_vault", "obsidian_daily_note", "obsidian_daily_heading",
    "obsidian_prompts", "obsidian_category_hubs", "obsidian_subgroups",
    "obsidian_story_groups",
    "knowledge_graph", "knowledge_plane", "owner",
    "usage_tracking", "usage_prompts", "usage_billing_mode",
    "recap", "recap_curator_cmd",
    "editor_scheme",
    "workspace_dirs",
    "artifacts_root",
    "interbrain", "org_label",
    # The checker's three thresholds. They have no board row (they are power-user
    # tunables, edited in config.json or via the env), but a reset that left them behind
    # would keep a hand-tuned drift window in force on a station the user just reset —
    # so they are popped like everything else the config file holds.
    "checker_report_days", "checker_escalate_days", "checker_claim_timeout",
    # heal's goal-review threshold, popped for the same reason: a hand-tuned window left
    # behind by a reset would keep making heals due on a station the user just cleared.
    "heal_goal_review_due",
]


def reset_settings():
    """Pop every board-managed key from config.json, returning the count cleared.
    Other config (user themes, repo-index roots) and tasks.db are left intact."""
    d = _load()
    cleared = [k for k in RESET_KEYS if k in d]
    for k in cleared:
        del d[k]
    _save(d)
    return len(cleared)


def _commands_dir():
    """Where the SessionStart hook writes bare /todo,/done aliases (honours
    CLAUDE_CONFIG_DIR like the hook does)."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    base = os.path.expanduser(cfg) if cfg else os.path.expanduser("~/.claude")
    return os.path.join(base, "commands")


def bare_commands_installed():
    """True if any task-station-managed bare command file (/todo, /done, /repos,
    /pin, /save, /history) is present on disk. These are written by the hook
    OUTSIDE config.json, so a settings reset reports rather than deletes them."""
    cdir = _commands_dir()
    for name in ("todo", "done", "repos", "pin", "unpin", "save", "history", "prompts", "glossary", "brief"):
        try:
            with open(os.path.join(cdir, "%s.md" % name)) as f:
                if "task-station-managed" in f.readline():
                    return True
        except Exception:
            continue
    return False


def cmd_reset(token):
    """`config --reset` factory reset. Bare (`token == "ask"`, or anything other
    than the confirm token) prints what it WILL do plus the confirm command and
    resets NOTHING. `--reset confirm` wipes the board-managed settings back to
    defaults, then reports which externally-installed integrations survive (with
    their off-commands). tasks.db is never touched — your tasks survive."""
    if token != "confirm":
        print("task-station config --reset resets ALL settings on the board above to")
        print("factory defaults (categories, theme, tint, title, workspace-dirs, …).")
        print("Your tasks are NOT affected — tasks.db is left untouched.")
        print("")
        print("To proceed, re-run:  task-station config --reset confirm")
        return
    n = reset_settings()
    print("Reset %d setting%s to defaults." % (n, "" if n == 1 else "s"))
    # Integrations that live OUTSIDE config.json can't (and shouldn't) be removed
    # by a settings reset — report what survives so the user removes it deliberately.
    import setup
    leftovers = []
    if bare_commands_installed():
        leftovers.append(("bare /todo, /done, /pin, /unpin, /repos, /save, /history, /prompts, /glossary, /brief command files", "--bare-cmds off"))
    installed, _ = setup.desktop_bridge_status()
    if installed:
        leftovers.append(("Claude Desktop MCP bridge entry", "--desktop-bridge off"))
    if setup.statusline_status() != "off":
        leftovers.append(("status-bar host/provider in settings.json + statusline.d/",
                          "--statusline off"))
    if setup.hud_status() != "off":
        leftovers.append(("cost-HUD host/provider in settings.json + statusline.d/",
                          "--hud off"))
    if "policy" in setup._manifest():
        leftovers.append(("delegation-rules block in CLAUDE.md", "--strict-delegation off"))
    if setup.sandbox_allowwrite_status():
        leftovers.append(("Obsidian vault entry in sandbox.filesystem.allowWrite (settings.json)",
                          "--obsidian-sandbox off"))
    if leftovers:
        print("")
        print("Still installed outside config.json (remove deliberately):")
        for what, how in leftovers:
            print("  %s — task-station config %s" % (what, how))


def cmd_config(a):
    if getattr(a, "artifacts_root", None) is not None:
        v = (a.artifacts_root or "").strip()
        if v:
            set("artifacts_root", v)
        else:
            unset("artifacts_root")   # no value → clear the override, back to derived default
        print("artifacts_root = %s" % artifacts_root()); return
    if getattr(a, "artifacts_root_get", False):
        print(artifacts_root()); return
    if getattr(a, "workspace_dirs_get", False):
        print(":".join(get("workspace_dirs") or "")); return
    if a.workspace_dirs is not None:
        set("workspace_dirs", [p for p in a.workspace_dirs.split(os.pathsep) if p])
        print("workspace_dirs = %s" % ":".join(get("workspace_dirs"))); return
    if getattr(a, "bare_cmds", None) is not None:
        set("bare_commands", a.bare_cmds == "on")
        print("bare_commands = %s" % ("on" if get("bare_commands") else "off")); return
    if getattr(a, "bare_cmds_get", False):
        print("on" if bare_commands() else "off"); return
    if getattr(a, "update_check", None) is not None:
        set("update_check", a.update_check == "on")
        print("update_check = %s" % ("on" if get("update_check") else "off")); return
    if getattr(a, "update_check_get", False):
        print("on" if update_check_enabled() else "off"); return
    if getattr(a, "stream", None) is not None:
        set("stream", a.stream == "on")
        print("stream = %s" % ("on" if get("stream") else "off")); return
    if getattr(a, "stream_get", False):
        print("on" if stream_enabled() else "off"); return
    if getattr(a, "stream_dir", None) is not None:
        set("stream_dir", a.stream_dir or None)   # empty string clears it
        sd = stream_dir()
        print("stream_dir = %s" % (sd if sd else "off")); return
    if getattr(a, "stream_dir_get", False):
        print(stream_dir() or "off"); return
    if getattr(a, "board_autorefresh", None) is not None:
        set("board_autorefresh", a.board_autorefresh == "on")
        print("board_autorefresh = %s" % ("on" if get("board_autorefresh") else "off")); return
    if getattr(a, "board_autorefresh_get", False):
        print("on" if board_autorefresh_enabled() else "off"); return
    if getattr(a, "done_closes_window", None) is not None:
        set("done_closes_window", a.done_closes_window == "on")
        print("done_closes_window = %s" % ("on" if get("done_closes_window") else "off")); return
    if getattr(a, "done_closes_window_get", False):
        print("on" if done_closes_window_enabled() else "off"); return
    if getattr(a, "board_browser", None) is not None:
        set("board_browser", a.board_browser or None)   # empty string clears it
        bb = board_browser()
        print("board_browser = %s" % (bb if bb else "(system default)")); return
    if getattr(a, "board_browser_get", False):
        print(board_browser() or ""); return
    if getattr(a, "interbrain", None) is not None:
        set("interbrain", a.interbrain)
        print("interbrain = %s" % interbrain_mode()); return
    if getattr(a, "interbrain_get", False):
        print(interbrain_mode()); return
    if getattr(a, "knowledge_plane", None) is not None:
        set("knowledge_plane", a.knowledge_plane)
        print("knowledge_plane = %s" % knowledge_plane_mode()); return
    if getattr(a, "knowledge_plane_get", False):
        print(knowledge_plane_mode()); return
    if getattr(a, "org_label", None) is not None:
        set("org_label", a.org_label or None)     # empty clears → default "Org brain"
        print("org_label = %s" % org_label()); return
    if getattr(a, "org_label_get", False):
        print(org_label()); return
    # RETIRED (#444): the preview engine is gone, so there is nothing to select.
    # Answer the flag with one line and exit 0 — never write it, never fail.
    if (getattr(a, "board_engine", None) is not None
            or getattr(a, "board_engine_get", False)):
        print("--board-engine was retired: there is one board now (`/todo board`).")
        return
    if getattr(a, "theme", None) is not None:
        return cmd_theme(a.theme)
    if getattr(a, "tint_theme", None) is not None:
        set("tint_theme", a.tint_theme)
        print("tint_theme = %s   (variant: %s)" % (tint_theme(), resolved_variant())); return
    if getattr(a, "tint_theme_get", False):
        print(tint_theme()); return
    if getattr(a, "tint", None) is not None:
        set("tint", a.tint == "on")
        print("tint = %s" % ("on" if get("tint") else "off")); return
    if getattr(a, "tint_get", False):
        print("on" if tint_enabled() else "off"); return
    if getattr(a, "reset", None) is not None:
        return cmd_reset(a.reset)
    if getattr(a, "title", None) is not None:
        set("title", a.title == "on")
        print("title = %s" % ("on" if get("title") else "off")); return
    if getattr(a, "title_get", False):
        print("on" if title_enabled() else "off"); return
    if getattr(a, "auto_categories", None) is not None:
        set("auto_categories", a.auto_categories == "on")
        print("auto_categories = %s" % ("on" if get("auto_categories") else "off")); return
    if getattr(a, "auto_categories_get", False):
        print("on" if auto_categories_enabled() else "off"); return
    if getattr(a, "guaranteed_tracking", None) is not None:
        set("guaranteed_tracking", a.guaranteed_tracking == "on")
        print("guaranteed_tracking = %s" % ("on" if get("guaranteed_tracking") else "off")); return
    if getattr(a, "guaranteed_tracking_get", False):
        print("on" if guaranteed_tracking_enabled() else "off"); return
    if getattr(a, "auto_checkpoint", None) is not None:
        set("auto_checkpoint", a.auto_checkpoint == "on")
        print("auto_checkpoint = %s" % ("on" if get("auto_checkpoint") else "off")); return
    if getattr(a, "auto_checkpoint_get", False):
        print("on" if auto_checkpoint_enabled() else "off"); return
    if getattr(a, "checkpoint_at", None) is not None:
        v = str(a.checkpoint_at).strip().lower()
        if v in ("off", "0"):
            set("checkpoint_at", 0)
        else:
            try:
                set("checkpoint_at", max(0, int(v)))
            except ValueError:
                print("checkpoint_at: expected an integer token count or 'off'"); return
        n = checkpoint_at()
        print("checkpoint_at = %s" % ("off" if n == 0 else n)); return
    if getattr(a, "checkpoint_at_get", False):
        n = checkpoint_at()
        print("off" if n == 0 else str(n)); return
    if getattr(a, "checkpoint_pct", None) is not None:
        v = str(a.checkpoint_pct).strip().lower()
        if v in ("off", "0"):
            set("checkpoint_pct", 0)
        else:
            try:
                set("checkpoint_pct", min(95, max(1, int(v))))
            except ValueError:
                print("checkpoint_pct: expected a percent 1-95 or 'off'"); return
        n = checkpoint_pct()
        print("checkpoint_pct = %s" % ("off" if n == 0 else n)); return
    if getattr(a, "checkpoint_pct_get", False):
        n = checkpoint_pct()
        print("off" if n == 0 else str(n)); return
    if getattr(a, "context_window", None) is not None:
        try:
            set("context_window", max(1, int(str(a.context_window).strip())))
        except ValueError:
            print("context_window: expected a positive token count"); return
        print("context_window = %s" % context_window()); return
    if getattr(a, "context_window_get", False):
        print(str(context_window())); return
    if getattr(a, "checkpoint_milestone_edits", None) is not None:
        v = str(a.checkpoint_milestone_edits).strip().lower()
        if v in ("off", "0"):
            set("checkpoint_milestone_edits", 0)
        else:
            try:
                set("checkpoint_milestone_edits", max(0, int(v)))
            except ValueError:
                print("checkpoint_milestone_edits: expected an event count or 'off'"); return
        n = checkpoint_milestone_edits()
        print("checkpoint_milestone_edits = %s" % ("off" if n == 0 else n)); return
    if getattr(a, "checkpoint_milestone_edits_get", False):
        n = checkpoint_milestone_edits()
        print("off" if n == 0 else str(n)); return
    if getattr(a, "ultracode_hints", None) is not None:
        set("ultracode_hints", a.ultracode_hints == "on")
        print("ultracode_hints = %s" % ("on" if get("ultracode_hints") else "off")); return
    if getattr(a, "ultracode_hints_get", False):
        print("on" if ultracode_hints_enabled() else "off"); return
    if getattr(a, "notify", None) is not None:
        set("notify", a.notify == "on")
        print("notify = %s" % ("on" if get("notify") else "off")); return
    if getattr(a, "notify_get", False):
        print("on" if notify_enabled() else "off"); return
    if getattr(a, "delegate_bypass_permissions", None) is not None:
        set("delegate_bypass_permissions", a.delegate_bypass_permissions == "on")
        print("delegate_bypass_permissions = %s"
              % ("on" if get("delegate_bypass_permissions") else "off")); return
    if getattr(a, "delegate_bypass_permissions_get", False):
        print("on" if delegate_bypass_permissions() else "off"); return
    if getattr(a, "reap_workers_on_done", None) is not None:
        set("reap_workers_on_done", a.reap_workers_on_done == "on")
        print("reap_workers_on_done = %s"
              % ("on" if get("reap_workers_on_done") else "off")); return
    if getattr(a, "reap_workers_on_done_get", False):
        print("on" if reap_workers_on_done() else "off"); return
    if getattr(a, "notify_webhook", None) is not None:
        set("notify_webhook", a.notify_webhook or "")   # empty string clears it
        wh = notify_webhook()
        print("notify_webhook = %s" % (wh if wh else "(unset)")); return
    if getattr(a, "notify_webhook_get", False):
        print(notify_webhook() or ""); return
    if getattr(a, "obsidian_vault", None) is not None:
        set("obsidian_vault", a.obsidian_vault or None)   # empty string clears it (off)
        v = obsidian_vault()
        print("obsidian_vault = %s" % (v if v else "(off)"))
        # Non-fatal heads-up: a vault under a macOS-protected root (Documents, iCloud,
        # …) can't be written from a SANDBOXED in-session export (os.replace → EPERM).
        # The path is still accepted — and it Just Works: failed in-session exports are
        # tracked (obsidian_dirty) and auto-flushed by the UNSANDBOXED Stop/SessionStart
        # hooks (Fix B). For INSTANT inline exports, opt into the sandbox allowlist.
        if v:
            try:
                import obsidian_sync
                if obsidian_sync.is_protected_vault_path(v):
                    print("  ⚠ This vault is under a macOS-protected folder. In-session "
                          "(sandboxed) exports there may be denied — but they still sync: "
                          "the unsandboxed Stop/SessionStart hooks auto-flush pending tasks.")
                    print("    For INSTANT inline exports, run: task-station config "
                          "--obsidian-sandbox on  (adds the vault to sandbox.filesystem.allowWrite).")
            except Exception:
                pass
        return
    if getattr(a, "obsidian_vault_get", False):
        print(obsidian_vault()); return
    if getattr(a, "obsidian_sandbox", None) is not None:
        import setup
        raw = get("obsidian_vault")   # verbatim (keeps ~/) — the allowlist honours ~/
        if a.obsidian_sandbox == "on":
            if not raw:
                print("Set a vault first: /task-station:config --obsidian-vault \"<path>\".")
            else:
                print(setup.install_sandbox_allowwrite(raw))
        else:
            print(setup.remove_sandbox_allowwrite())
        return
    if getattr(a, "obsidian_sandbox_get", False):
        import setup
        print("on" if setup.sandbox_allowwrite_status() else "off"); return
    if getattr(a, "obsidian_daily_note", None) is not None:
        set("obsidian_daily_note", a.obsidian_daily_note == "on")
        print("obsidian_daily_note = %s" % ("on" if get("obsidian_daily_note") else "off")); return
    if getattr(a, "obsidian_daily_note_get", False):
        print("on" if obsidian_daily_note_enabled() else "off"); return
    if getattr(a, "obsidian_daily_heading", None) is not None:
        set("obsidian_daily_heading", a.obsidian_daily_heading or None)   # empty restores default
        print("obsidian_daily_heading = %s" % obsidian_daily_heading()); return
    if getattr(a, "obsidian_daily_heading_get", False):
        print(obsidian_daily_heading()); return
    if getattr(a, "obsidian_prompts", None) is not None:
        set("obsidian_prompts", a.obsidian_prompts == "on")
        print("obsidian_prompts = %s" % ("on" if get("obsidian_prompts") else "off")); return
    if getattr(a, "obsidian_prompts_get", False):
        print("on" if obsidian_prompts_enabled() else "off"); return
    if getattr(a, "obsidian_category_hubs", None) is not None:
        set("obsidian_category_hubs", a.obsidian_category_hubs == "on")
        print("obsidian_category_hubs = %s"
              % ("on" if obsidian_category_hubs_enabled() else "off")); return
    if getattr(a, "obsidian_category_hubs_get", False):
        print("on" if obsidian_category_hubs_enabled() else "off"); return
    if getattr(a, "obsidian_subgroups", None) is not None:
        set("obsidian_subgroups", a.obsidian_subgroups == "on")
        print("obsidian_subgroups = %s"
              % ("on" if obsidian_subgroups_enabled() else "off")); return
    if getattr(a, "obsidian_subgroups_get", False):
        print("on" if obsidian_subgroups_enabled() else "off"); return
    if getattr(a, "obsidian_story_groups", None) is not None:
        set("obsidian_story_groups", a.obsidian_story_groups == "on")
        print("obsidian_story_groups = %s"
              % ("on" if obsidian_story_groups_enabled() else "off")); return
    if getattr(a, "obsidian_story_groups_get", False):
        print("on" if obsidian_story_groups_enabled() else "off"); return
    if getattr(a, "knowledge_graph", None) is not None:
        set("knowledge_graph", a.knowledge_graph == "on")
        print("knowledge_graph = %s" % ("on" if get("knowledge_graph") else "off")); return
    if getattr(a, "knowledge_graph_get", False):
        print("on" if knowledge_graph_enabled() else "off"); return
    if getattr(a, "owner", None) is not None:
        set("owner", a.owner or None)   # empty string clears it (single-owner)
        o = owner()
        print("owner = %s" % (o if o else "(unset)")); return
    if getattr(a, "owner_get", False):
        print(owner() or ""); return
    if getattr(a, "usage_tracking", None) is not None:
        set("usage_tracking", a.usage_tracking == "on")
        print("usage_tracking = %s" % ("on" if get("usage_tracking") else "off")); return
    if getattr(a, "usage_tracking_get", False):
        print("on" if usage_tracking_enabled() else "off"); return
    if getattr(a, "usage_prompts", None) is not None:
        set("usage_prompts", a.usage_prompts == "on")
        print("usage_prompts = %s" % ("on" if get("usage_prompts") else "off")); return
    if getattr(a, "usage_prompts_get", False):
        print("on" if usage_prompts_enabled() else "off"); return
    if getattr(a, "board_prompts", None) is not None:
        set("board_prompts", a.board_prompts == "on")
        print("board_prompts = %s" % ("on" if get("board_prompts") else "off")); return
    if getattr(a, "board_prompts_get", False):
        print("on" if board_prompts_enabled() else "off"); return
    if getattr(a, "usage_billing_mode", None) is not None:
        set("usage_billing_mode", a.usage_billing_mode)
        print("usage_billing_mode = %s" % usage_billing_mode()); return
    if getattr(a, "usage_billing_mode_get", False):
        print(usage_billing_mode()); return
    if getattr(a, "recap", None) is not None:
        set("recap", a.recap == "on")
        print("recap = %s" % ("on" if get("recap") else "off")); return
    if getattr(a, "recap_get", False):
        print("on" if recap_enabled() else "off"); return
    if getattr(a, "recap_curator_cmd", None) is not None:
        val = (a.recap_curator_cmd or "").strip()
        if val:
            set("recap_curator_cmd", val)
        else:
            unset("recap_curator_cmd")          # no value → clear it (curator OFF)
        print("recap_curator_cmd = %s" % (recap_curator_cmd() or "off")); return
    if getattr(a, "recap_curator_cmd_get", False):
        print(recap_curator_cmd() or "off"); return
    if getattr(a, "editor_scheme", None) is not None:
        val = (a.editor_scheme or "").strip()
        if val:
            set("editor_scheme", val)
        else:
            unset("editor_scheme")           # no value → restore auto-detect
        print("editor_scheme = %s" % editor_scheme()); return
    if getattr(a, "editor_scheme_get", False):
        print(editor_scheme()); return
    if getattr(a, "category_pack", None) is not None:
        return cmd_category_pack(a.category_pack)
    if getattr(a, "category_pack_get", False):
        print(category_pack()); return
    if getattr(a, "categories", None) is not None:
        return cmd_categories(a.categories);
    if getattr(a, "enable", None) is not None:
        return toggle_category(a.enable, True)
    if getattr(a, "disable", None) is not None:
        return toggle_category(a.disable, False)
    import setup
    if getattr(a, "strict_delegation", None) is not None:
        print(setup.set_policy(a.strict_delegation == "on")); return
    if getattr(a, "desktop_bridge", None) is not None:
        print(setup.install_desktop_bridge() if a.desktop_bridge == "on"
              else setup.remove_desktop_bridge()); return
    if getattr(a, "statusline_get", False):
        print("on" if statusline_enabled() else "off"); return
    if getattr(a, "statusline", None) is not None:
        on = a.statusline == "on"
        set("statusline", on)
        print(setup.install_statusline() if on else setup.remove_statusline()); return
    if getattr(a, "hud_get", False):
        print("on" if hud_enabled() else "off"); return
    if getattr(a, "hud", None) is not None:
        on = a.hud == "on"
        set("hud", on)
        print(setup.install_hud() if on else setup.remove_hud()); return
    if getattr(a, "hud_rows", None) is not None:
        set("hud_rows", ",".join(hud_rows_parse(a.hud_rows)))
        print("hud_rows = %s" % ",".join(hud_rows())); return
    if getattr(a, "hud_rows_get", False):
        print(",".join(hud_rows())); return
    if getattr(a, "hud_eco", None) is not None:
        set("hud_eco", a.hud_eco == "on")
        print("hud_eco = %s" % ("on" if get("hud_eco") else "off")); return
    if getattr(a, "hud_eco_get", False):
        print("on" if hud_eco_enabled() else "off"); return
    # No flags: the single unified settings + status board. The status facts are
    # folded into render_board() now, so we no longer print setup.status() here
    # (setup.status() is unchanged and still used by the install flow).
    print(render_board())
