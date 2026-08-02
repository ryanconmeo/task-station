"""feeds.py — the ONE owner of the task-station feed format (task #444).

A *feed* is a read-only, per-brain view-model of tasks, serialized as a `.js` sidecar.
Feeds are the seam between machines: the board renders them, and the two-machine sync
transport (J-track, not yet built) will produce and consume exactly this format. This
module is the ONLY implementation of it — writer, wire form, parser, and loader.

FEED ROOT — one place, `<data_dir>/feeds/`:

    feeds/
      self.js           this machine's tasks (written on every `/todo board`)
      self-archive.js   closed tasks beyond the newest 50 (the lazy shard)
      peers/*.js        feeds received from other people/machines (the sync transport
                        drops them here; nothing writes them yet)
      demo/*.js         persistent DEMO peer fixtures — `tools/seed_demo.py`, which is
                        how peer rendering is exercised until the transport lands

WIRE FORM. A feed file is JavaScript, NOT json, because a `file://` page can load a
local `<script src>` but CANNOT `fetch()` a local file (Safari and Chrome both block
it) — the same proven mechanism as `board.rev.js`:

    window.__TSFEED_<alias> = {…json…};
    (window.__TSFEEDS = window.__TSFEEDS || []).push(window.__TSFEED_<alias>);

`json.dumps` keeps the payload strictly DATA (never code). `parse_feed_file` reads the
assignment line back server-side, so the same bytes serve the browser and the renderer.

NO STORE WRITES, EVER. This module only READS the store (`backend.all_tasks()` + the
usage ledger). It deliberately does NOT call `ensure_seqs()` (which would persist
seqs): the handle is display-only and falls back to the uuid8 when a task has no seq.

SYNC-SAFE (the `local_only` contract). `prompts` (human prompt text) and `resume`
(machine-local paths) are MACHINE-LOCAL. They are written into the self feed, tagged
at feed level via `local_only`, and MUST be stripped by any share/sync export — route
every such path through `strip_local_only`, which also enforces per-task
`trail_visibility`.

`_pr_signal_id` IS A JOIN KEY. It is the field F6 cross-links on, and
`lib/artifacts.py:pr_signal_id` must agree with it. Changing its output silently
breaks PR auto-linking across brains — treat the format as frozen.

TOKEN MAPPING (per-task `tokens`): summed from the usage ledger via
`usage.task_usage(backend, task)`, which joins `session_usage` rows on `task_id`.
    tokens = total_in + total_out + sum(models[*].cache_read)
i.e. every billable input, output, and cache-read token attributed to the task's
sessions. `cost_usd` = the ledger's derived total; `models` = the model families
present. When the ledger is off/empty there is no span data to estimate from, so
`tokens = 0` with a `tokens_estimated: true` flag.

VOCABULARY (Interbrain federation): products task-station / brain-station / Interbrain;
the event stream is the *tasklog*; sync repo, relay, registry, boundaries, private/
public/org brain, handle (`<alias>-<n>`), memo. Self alias = `rnguyen`; demo peers
`jpark`/`kosei`; org alias `org`.
"""
import hashlib
import json
import os
import re

import decisions as _dec

# ---- identity / palette -----------------------------------------------------

SELF_ALIAS = "rnguyen"            # self = Entra local-part (org identity system)
SELF_BRAIN = "main"

# Owner fill colors — CVD-validated set (always paired with the alias label in the
# UI so color is never the sole channel). light / dark variants.
OWNER_COLORS = {
    "rnguyen": {"light": "#cf8a22", "dark": "#b97e1f"},
    "jpark":   {"light": "#4f8fe6", "dark": "#4f8fe6"},
    "kosei":   {"light": "#279a84", "dark": "#279a84"},
    "org":     {"light": "#8f7ae0", "dark": "#8f7ae0"},
}

# Category accent hex per slot — mirrors tools/render_board.py `_CAT_HIGHLIGHT`
# (replicated as DATA so the feed format stays decoupled from the board's render
# module; both are stdlib-only, no shared import).
_CAT_HEX = {
    "dark": {
        "red": "#ff5d5d", "orange": "#ff9b3d", "yellow": "#ffe14d",
        "green": "#6fe05a", "blue": "#3fa9ff", "purple": "#b072ff",
        "black": "#8b93a1", "pink": "#ff6ec7", "white": "#e6e9ef",
        "silver": "#aeb7c4", "gold": "#e0a92e", "brown": "#9a6233",
    },
    "light": {
        "red": "#d23440", "orange": "#dd7414", "yellow": "#c2a200",
        "green": "#3f9e2f", "blue": "#1f7fd6", "purple": "#8a3fd0",
        "black": "#3a3d44", "pink": "#cf3a96", "white": "#8890a0",
        "silver": "#7f8a9c", "gold": "#b8860b", "brown": "#7a4a22",
    },
}

ARCHIVE_AFTER = 50               # closed tasks beyond the newest 50 → archive shard

# Machine-local / privacy-sensitive vm fields — self feed only, and STRIPPED by any
# share/sync export (see strip_local_only). `prompts` carry human prompt text;
# `resume` carries machine-local resume paths. This is the `sync_safe` contract.
LOCAL_ONLY_FIELDS = ("prompts", "resume")
PROMPT_CAP = 40

FEED_SCHEMA = 3


# ---- feed root --------------------------------------------------------------

def feeds_dir(data_dir):
    """The ONE feed root: `<data_dir>/feeds`. Everything (self, peers, demo) lives
    under here — there is no second root."""
    return os.path.join(data_dir, "feeds")


# ---- small helpers ----------------------------------------------------------

def _dedupe(seq):
    """Order-preserving dedupe, dropping falsey values."""
    out, seen = [], set()
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _js_ident(name):
    """A safe JS identifier suffix for a feed's `window.__TSFEED_<x>` global."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "feed"))
    return s or "feed"


def _model_family(model_id):
    """Collapse a raw model id to a short family label (opus / sonnet / haiku / …)."""
    m = (model_id or "").lower()
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if fam in m:
            return fam
    # keep something legible for anything unrecognized
    return (model_id or "").split("/")[-1][:16] or "other"


def _pr_signal_id(url):
    """A stable, cross-brain-matchable signal id for a PR url:
    GitHub `.../o/r/pull/3` → `o/r#3`; ADO `.../pullrequest/12` → `ado!12`; else the
    last path segment.

    FROZEN FORMAT. This is the join key the feeds carry and F6 cross-links on;
    `lib/artifacts.py:pr_signal_id` must agree with it. Changing the output silently
    breaks PR auto-linking between brains, so extend rather than alter."""
    u = (url or "").strip()
    if not u:
        return ""
    m = re.search(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)", u, re.I)
    if m:
        return "%s/%s#%s" % (m.group(1), m.group(2), m.group(3))
    m = re.search(r"/pullrequest/(\d+)", u, re.I)
    if m:
        return "ado!%s" % m.group(1)
    return u.rstrip("/").rsplit("/", 1)[-1] or u


def _category(color):
    """`{tag, dot, hex, hex_dark}` for a task's category color key (or None)."""
    key, tag, dot = "black", "GENERAL", "⚫"
    try:
        import categories
        key = categories.normalize(color) if color else "black"
        meta = categories.hub_meta(color) if color else None
        if meta:
            tag = meta.get("tag") or tag
            dot = meta.get("dot") or dot
    except Exception:
        pass
    return {
        "key": key,   # category slot key ("green"…) → the house-style `cat-<key>` class
        "tag": tag,
        "dot": dot,
        "hex": _CAT_HEX["light"].get(key, _CAT_HEX["light"]["black"]),
        "hex_dark": _CAT_HEX["dark"].get(key, _CAT_HEX["dark"]["black"]),
    }


def _task_tokens(backend, task):
    """(tokens, estimated, cost_usd, models) — see the module docstring TOKEN MAPPING.
    Fully guarded: any ledger hiccup degrades to the estimated-zero fallback."""
    try:
        import usage
        data = usage.task_usage(backend, task)
    except Exception:
        data = None
    models = (data or {}).get("models") or {}
    if not models:
        return (0, True, 0.0, [])
    cache = sum(int(m.get("cache_read") or 0) for m in models.values())
    tokens = int(data.get("total_in") or 0) + int(data.get("total_out") or 0) + cache
    cost = float(data.get("total_cost_usd") or 0.0)
    fams = _dedupe(sorted(_model_family(m) for m in models))
    return (tokens, False, round(cost, 4), fams)


def _live_ids(ts):
    """The set of session ids with a RUNNING process right now (self feed only —
    liveness is never federated; foreign rows never show live)."""
    try:
        return set(ts._live_session_index().keys())
    except Exception:
        return set()


# ---- view-model -------------------------------------------------------------

def self_view_model(ts, backend, task, live_ids, cfg=None):
    """One REAL task → the read-only feed view-model. `relations` are UUID-NORMALIZED
    (uuid8, never seq): the stored edge already carries the target's full `id`, so
    `id[:8]` is the join key that survives cross-machine sync.

    `brain` (where the task lives) and `shares` (resolved audience list) come from
    brains.json via `cfg` (default brain "main", no shares). `shared_org` is derived
    (True iff the task is shared to `org`)."""
    tid = task.get("id") or ""
    uuid8 = tid[:8]
    seq = task.get("seq")
    handle = "%s-%s" % (SELF_ALIAS, seq if seq is not None else uuid8)
    sessions = set(task.get("sessions") or [])
    live = bool(sessions & live_ids)
    done, total = ts.step_progress(task)
    # EVERY still-current decision, pins first then the rest oldest-first — no recency
    # limit any more, so the board hides nothing the detail digest shows. Replaced
    # decisions (superseded / split / merged) are dropped entirely, and the whole thing
    # is projected to plain strings: the wire form stays a list of strings, so every
    # existing feed reader (board included) is untouched and a wrong decision never
    # leaves the box. The field KEEPS the name `decisions_tail` — it is schema 3 wire
    # contract that live fixtures and peer feeds already carry — even though there is
    # no tail left to take.
    decisions = [_dec.text(d) for _i, d in _dec.digest_order(task.get("decisions"))]
    prs = _dedupe(_pr_signal_id(p.get("url")) for p in ts.merged_prs(task))
    stories = _dedupe(s.get("id") for s in ts._story_refs(task))
    rels = []
    for e in (task.get("related") or []):
        rid = e.get("id")
        if rid:
            rels.append({"uuid8": rid[:8], "kind": e.get("kind") or "related"})
    tokens, estimated, cost_usd, models = _task_tokens(backend, task)
    cat = _category(task.get("color"))
    try:
        import brains as _brains
        brain = _brains.brain_for(cfg, tid) if cfg is not None else _brains.DEFAULT_BRAIN
        shares = _brains.shares_for(cfg, tid, cat.get("tag")) if cfg is not None else []
    except Exception:
        brain, shares = "main", []
    rich = _self_rich(ts, task, live_ids, seq)
    vm = {
        "uuid8": uuid8,
        "handle": handle,
        "title": task.get("title") or "",
        "status": ts.task_status(task),
        "live": live,
        "category": cat,
        "effort": task.get("effort") or "",
        "brain": brain,
        "shares": shares,
        # F5.4 per-node trail visibility (default private) — the sync_safe stripper reads
        # this at export to decide how much of the trail leaves the machine.
        "trail_visibility": (task.get("trail_visibility") or "private"),
        "links": list(task.get("links") or []),
        "forked_from": task.get("forked_from") or None,
        "tokens": tokens,
        "tokens_estimated": estimated,
        "cost_usd": cost_usd,
        "models": models,
        "updated_ts": task.get("updated_ts"),
        "relations": rels,
        "signals": {"prs": prs, "stories": stories},
        "digest": {
            "goal": (task.get("goal") or "").strip(),
            "state": (task.get("state") or "").strip(),
            "steps_done": done,
            "steps_total": total,
            "decisions_tail": decisions,
        },
        "participants": [SELF_ALIAS],
        "owner": SELF_ALIAS,
        "shared_org": ("org" in shares),
    }
    vm.update(rich)
    return vm


def _self_rich(ts, task, live_ids, seq):
    """The extra SELF-only view-model fields — sessions/hub cards, work-mix, history,
    glossary, prompt trail, resume/open one-liners, summary/repos/files. Peers
    deliberately lack these (their feeds never carry them). Every lookup is guarded so a
    ledger/helper hiccup degrades to an empty section, never a broken feed."""
    def g(fn, default):
        try:
            return fn()
        except Exception:
            return default
    bu = g(lambda: ts._board_usage(task, live_ids), None)
    rt = g(lambda: ts._resume_target(task, None), None)
    resume = None
    if rt:
        resume = {"command": rt.get("command"),
                  "label": "Resume (pinned)" if rt.get("pinned") else "Resume (hub)",
                  "activity": g(lambda: ts.rel_time(rt.get("ts")), ""),
                  "fresh": bool(rt.get("fresh"))}
    prompts = g(lambda: ts._board_prompts_all(task), []) or []
    if len(prompts) > PROMPT_CAP:
        prompts = prompts[-PROMPT_CAP:]
    return {
        "summary": (task.get("summary") or "").strip(),
        "repos": list(task.get("projects") or []),
        "files": [os.path.basename(p) for p in (task.get("files") or [])[-8:]],
        "history": [{"ts": e.get("ts", ""), "text": e.get("text", "")}
                    for e in (task.get("history") or [])],
        "glossary": [{"name": x.get("name", ""), "layer": x.get("layer", ""),
                      "state": x.get("state", ""), "def": x.get("def", "")}
                     for x in (task.get("glossary") or [])],
        "session_tree": g(lambda: ts._board_session_counts(task, live_ids), {}),
        "sessions": (bu.get("sessions") if bu else []) or [],
        "usage": (bu.get("usage") if bu else None),
        "work_mix": (bu.get("phases") if bu else []) or [],
        "prompts": prompts,                       # local_only
        "resume": resume,                         # local_only (machine-local path)
        "open_command": ("/todo %s" % seq) if seq is not None else None,
    }


def strip_local_only(feed):
    """Return a SYNC-SAFE copy of `feed`: machine-local fields removed AND per-node
    `trail_visibility` (F5.4) enforced. Any share/sync export path MUST route feeds
    through this so machine-local paths and human prompt text never leave the machine.

    trail_visibility per task:
      • private (default) — trails never exported: drop `prompts`, and blank the digest's
        `state` + `decisions_tail` (only identity + goal + step counts survive).
      • checkpoints       — digest checkpoints only: keep the full digest, drop `prompts`.
      • full              — include the human-prompt+final-response trail: keep `prompts`.
    `resume` (a machine-local path) is ALWAYS dropped, regardless of visibility."""
    import copy
    out = copy.deepcopy(feed)
    for t in out.get("tasks", []) or []:
        vis = (t.get("trail_visibility") or "private").lower()
        t.pop("resume", None)                 # machine-local path — never federated
        if vis != "full":
            t.pop("prompts", None)
        if vis == "private":
            d = t.get("digest")
            if isinstance(d, dict):
                d["state"] = ""
                d["decisions_tail"] = []
    out["local_only"] = []
    return out


def build_self_feed(ts, backend, cfg=None):
    """Return (recent_vms, archive_vms). `recent` = every open/active task (newest
    first) then the newest ARCHIVE_AFTER closed tasks; `archive` = older closed tasks
    (the lazy shard). `cfg` = the loaded brains.json (brain/shares resolution).
    Read-only; no store writes."""
    tasks = list(backend.all_tasks())
    live_ids = _live_ids(ts)

    def upd(t):
        return t.get("updated_ts") or 0

    openish = sorted((t for t in tasks if ts.task_status(t) != "closed"),
                     key=upd, reverse=True)
    closed = sorted((t for t in tasks if ts.task_status(t) == "closed"),
                    key=upd, reverse=True)
    recent_src = openish + closed[:ARCHIVE_AFTER]
    archive_src = closed[ARCHIVE_AFTER:]
    recent = [self_view_model(ts, backend, t, live_ids, cfg) for t in recent_src]
    archive = [self_view_model(ts, backend, t, live_ids, cfg) for t in archive_src]
    return recent, archive


# ---- serialization (the wire form) ------------------------------------------

def _feed_js(alias, feed):
    """Serialize a feed dict as the `.js` payload: assign the namespaced global and
    push it onto the shared registry. json.dumps keeps it strictly data (no code).
    This is THE wire form — `parse_feed_file` is its exact inverse."""
    ident = _js_ident(alias)
    payload = json.dumps(feed, ensure_ascii=False, sort_keys=True, default=str)
    return ("window.__TSFEED_%s = %s;\n"
            "(window.__TSFEEDS = window.__TSFEEDS || []).push(window.__TSFEED_%s);\n"
            % (ident, payload, ident))


def _atomic_write(path, text):
    tmp = path + ".tmp." + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _write_feed(fdir, filename, alias, feed):
    _atomic_write(os.path.join(fdir, filename), _feed_js(alias, feed))


def _feed_content_rev(tasks):
    """A stable content revision over a feed's tasks — so a peer mounting this feed can
    diff it (F5 subscriptions). Changes iff the rendered task data changes."""
    try:
        return hashlib.sha1(
            json.dumps(tasks or [], sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
    except Exception:
        return ""


# ---- parsing / loading (the inverse of the wire form) -----------------------

def parse_feed_file(path):
    """Parse a canonical feed `.js` file → the feed dict, or None when absent/unparseable
    (e.g. a legacy IIFE-wrapped feed, whose payload is a JS variable rather than inline
    JSON — skipped, never crashed on). Scans every line so a self feed's trailing
    registry-push line is ignored. Never raises.

    Returning None is CORRECT but it is also SILENT: a feed that fails to parse simply
    vanishes from the board. That is how the shipped demo fixtures rendered nowhere for
    five releases (#444). If you add a feed source, assert it parses — see
    `tests/test_feeds.py:ShippedFixtureTest`."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s.startswith("window.__TSFEED_") or "=" not in s or ";" not in s:
                    continue
                try:
                    obj = json.loads(s[s.index("=") + 1:s.rindex(";")])
                except Exception:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("tasks"), list):
                    return obj
    except Exception:
        pass
    return None


def peer_feed_files(data_dir):
    """Every peer/org feed file to render, in DETERMINISTIC order:
    `<data_dir>/feeds/peers/*.js` then `<data_dir>/feeds/demo/*.js`. Peers come first so
    real received feeds outrank demo fixtures. The self/archive feeds are excluded —
    those are the local brain, already rendered from the store."""
    out = []
    root = feeds_dir(data_dir)
    for sub in ("peers", "demo"):
        d = os.path.join(root, sub)
        try:
            names = sorted(n for n in os.listdir(d)
                           if n.endswith(".js") and not n.startswith("self"))
        except Exception:
            names = []
        out.extend(os.path.join(d, n) for n in names)
    return out


# ---- feed objects -----------------------------------------------------------

def _self_feed_obj(recent, has_archive):
    oc = OWNER_COLORS[SELF_ALIAS]
    return {
        "schema": FEED_SCHEMA,
        "kind": "self",
        "alias": SELF_ALIAS,
        "owner": SELF_ALIAS,
        "brain": SELF_BRAIN,
        "label": "My brain · main",
        # F5: the feed carries its own content rev so a subscriber can diff it.
        "rev": _feed_content_rev(recent),
        "editable": True,
        "color": oc["light"],
        "color_dark": oc["dark"],
        "tasks": recent,
        "memos": [],
        "has_archive": bool(has_archive),
        "archive_src": "feeds/self-archive.js" if has_archive else None,
        "local_only": list(LOCAL_ONLY_FIELDS),   # sync_safe contract (see strip_local_only)
    }


def _archive_feed_obj(archive):
    oc = OWNER_COLORS[SELF_ALIAS]
    return {
        "schema": FEED_SCHEMA,
        "kind": "archive",
        "alias": SELF_ALIAS,
        "owner": SELF_ALIAS,
        "brain": SELF_BRAIN,
        "label": "My brain · archive",
        "editable": True,
        "color": oc["light"],
        "color_dark": oc["dark"],
        "tasks": archive,
        "memos": [],
    }


def _brains_block(ts, cfg):
    """The `brains` block on the self feed — my brains + their sharing rules, each with a
    DERIVED read-time tally (F4; computed at read, NEVER stored, so it can't drift).
    Fully guarded: a brains hiccup degrades to the single default brain."""
    try:
        import brains as _brains
        bcfg = cfg if cfg is not None else _brains._default()
        blist = _brains.list_brains(bcfg)
        try:
            views = ts._brain_task_views()
            for b in blist:
                b["derived"] = _brains.derive(bcfg, b["name"], views)
        except Exception:
            pass
        return blist
    except Exception:
        return [{"name": "main", "archived": False, "shares": []}]


# ---- export -----------------------------------------------------------------

def export_self_feed(ts, data_dir, cfg=None):
    """Write this machine's feed to `<data_dir>/feeds/self.js` (plus `self-archive.js`
    when the archive shard is non-empty) and return the self feed dict.

    Called on every `/todo board` write, so the feed root is always current for
    `tools/seed_demo.py` and (later) the sync transport. Pure READ of the store — the
    caller owns `ensure_seqs()` if it wants stable `<alias>-<seq>` handles."""
    fdir = feeds_dir(data_dir)
    os.makedirs(fdir, exist_ok=True)
    backend = ts._backend()
    if cfg is None:
        try:
            import brains
            cfg = brains.load(data_dir)
        except Exception:
            cfg = None
    recent, archive = build_self_feed(ts, backend, cfg)
    self_feed = _self_feed_obj(recent, bool(archive))
    self_feed["brains"] = _brains_block(ts, cfg)
    _write_feed(fdir, "self.js", "self", self_feed)
    if archive:
        _write_feed(fdir, "self-archive.js", "self_archive", _archive_feed_obj(archive))
    return self_feed


def read_self_feed(data_dir):
    """The self feed dict from `<data_dir>/feeds/self.js`, or None. The read half of
    `export_self_feed` — used by the demo seeder to source REAL signal ids."""
    return parse_feed_file(os.path.join(feeds_dir(data_dir), "self.js"))
