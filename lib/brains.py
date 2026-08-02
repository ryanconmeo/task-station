"""brains.py — the Interbrain "brains & sharing" config (task #444).

Answers two questions for every task: WHERE it lives (its brain) and WHO can see it
(the sharing rules). `lib/feeds.py` resolves both onto each task's feed view-model, so
this file is what makes a feed say "this is my `work` brain, shared with jpark + org".

Persisted to `<data_dir>/brains.json` — a NEW, additive config file. It NEVER touches
`tasks.db` / the store: the task→brain assignment is a uuid→brain map kept HERE, so the
store schema stays untouched (the same discipline as the sync design's machine-local
fields).

Model (the UI vocabulary):
  • BRAIN  — where a task lives. One owner, one brain per task; default "main".
  • SHARING RULE — an audience grant on a brain (who can see its slice), optionally
    scoped to a category tag. Sharing is NEVER a place/rail section — it's visibility.

Schema v2 (brains.json) — brains are DEFINABLE structures:
  {"version": 2,
   "brains": {"<name>": {"archived": bool,
                         "shares": [{"with": "<alias|org>", "tag": "<TAG>|null"}],
                         "name": "<name>",           # mirrors the key (self-describing)
                         "description": "<one line>",
                         "purpose": "<what it's for>",
                         "keywords": ["..."],        # auto-attach signal
                         "repos": ["..."],           # auto-attach signal (repo/cwd)
                         "category_affinity": ["TAG", ...],
                         "created_ts": <epoch>}},
   "assign": {"<task-uuid>": "<brain-name>"},        # non-main assignments only
   "pinned": {"<task-uuid>": true}}                  # manual overrides the scorer respects

v1 → v2 migrates IN PLACE on load (new fields default empty); the core structure is valid
even when every field is empty, so a single-brain 'main'-only user sees no behavior change.

AUTO-ATTACH: the user NEVER names a brain. On task create (and the attach-with-edit /
promote-to-active moment) `score_brains` scores every brain from the task's signals
(repo/cwd · keyword · category_affinity · session skill); the best score ≥ threshold wins,
else 'main'. `auto_assign` writes the winner ONLY when the task is still on 'main' and not
pinned (never yanks a task out of a scored brain silently). `brains assign` is the manual
override — it sets `pinned`, which the scorer respects forever.

DERIVED block: `derive` computes `{open_count, active_count, recent_focus,
dominant_categories, top_signals, last_activity_ts}` at READ time from the store + the
assignment map. NEVER stored — always recomputed, so it can't drift.

CLI: `task-station brains <list|add|edit|rename|archive|share|unshare|assign|suggest|show>`
mutates this file (a real write — permitted; "no store writes" is about tasks.db). This CLI
is the ONLY write path: the board is a static `file://` page and CANNOT write, so any UI
over this config can only emit the exact `task-station brains …` commands to run.
"""
import json
import os

DEFAULT_BRAIN = "main"
VERSION = 2

# Auto-attach weights (F4). repo/cwd dominates; keyword accrues (capped); category and
# the session skill hint are tie-breakers. Best score ≥ THRESHOLD wins, else 'main'.
W_REPO = 4
W_KEYWORD = 2
W_KEYWORD_CAP = 6
W_CATEGORY = 2
W_SKILL = 1
SCORE_THRESHOLD = 3

# The definable per-brain fields (beyond archived/shares). Kept in one place so add/edit,
# migration, and list_brains all agree on the shape.
FIELD_KEYS = ("description", "purpose", "keywords", "repos", "category_affinity")
_LIST_FIELDS = ("keywords", "repos", "category_affinity")


def path(data_dir=None):
    import paths
    return os.path.join(data_dir or paths.data_dir(), "brains.json")


def _blank_brain(name=""):
    return {"archived": False, "shares": [], "name": name, "description": "",
            "purpose": "", "keywords": [], "repos": [], "category_affinity": [],
            "created_ts": 0}


def _default():
    return {"version": VERSION,
            "brains": {DEFAULT_BRAIN: _blank_brain(DEFAULT_BRAIN)},
            "assign": {},
            "pinned": {}}


def _migrate_brain(name, meta):
    """Bring ONE brain dict up to the v2 shape in place (v1 entries lack the definable
    fields). Idempotent — a v2 brain is returned unchanged."""
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("archived", False)
    meta.setdefault("shares", [])
    meta["name"] = meta.get("name") or name
    meta.setdefault("description", "")
    meta.setdefault("purpose", "")
    for k in _LIST_FIELDS:
        v = meta.get(k)
        meta[k] = list(v) if isinstance(v, list) else []
    meta.setdefault("created_ts", 0)
    return meta


def load(data_dir=None):
    """Load brains.json (defaults + `main` always present, v1→v2 migrated in memory).
    Never raises — a missing or corrupt file yields the default config."""
    p = path(data_dir)
    cfg = _default()
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            if isinstance(raw.get("brains"), dict):
                cfg["brains"] = raw["brains"]
            if isinstance(raw.get("assign"), dict):
                cfg["assign"] = raw["assign"]
            if isinstance(raw.get("pinned"), dict):
                cfg["pinned"] = raw["pinned"]
    except Exception:
        pass
    cfg["brains"].setdefault(DEFAULT_BRAIN, _blank_brain(DEFAULT_BRAIN))
    for n, b in list(cfg["brains"].items()):
        cfg["brains"][n] = _migrate_brain(n, b)
    cfg.setdefault("pinned", {})
    cfg["version"] = VERSION          # in-memory bump; persisted on the next save
    return cfg


def save(cfg, data_dir=None):
    """Atomically write brains.json. Only ever writes this one file."""
    p = path(data_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp." + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, indent=2, sort_keys=True))
    os.replace(tmp, p)


# ---- pure operations on a loaded cfg (return True when they changed it) ------

def _split_list(val):
    """Normalize a --keywords/--repos/--category-affinity value to a clean list. Accepts a
    list already, or a comma/space-separated string. Blanks dropped, deduped, order kept."""
    if val is None:
        return None
    items = val if isinstance(val, list) else str(val).replace(",", " ").split()
    out, seen = [], set()
    for x in items:
        x = str(x).strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def add(cfg, name, now=0, **fields):
    name = (name or "").strip()
    if not name or name in cfg["brains"]:
        return False
    meta = _blank_brain(name)
    meta["created_ts"] = now or 0
    cfg["brains"][name] = meta
    _apply_fields(meta, fields)
    return True


def edit(cfg, name, **fields):
    """Set definable fields on an EXISTING brain. Returns True when anything changed. Only
    keys present in `fields` (and non-None) are touched — list fields REPLACE (not append),
    so an empty string clears them."""
    name = (name or "").strip()
    if name not in cfg["brains"]:
        return False
    return _apply_fields(cfg["brains"][name], fields)


def _apply_fields(meta, fields):
    changed = False
    for k in ("description", "purpose"):
        v = fields.get(k)
        if v is not None and meta.get(k, "") != v.strip():
            meta[k] = v.strip()
            changed = True
    for k in _LIST_FIELDS:
        v = _split_list(fields.get(k))
        if v is not None and meta.get(k, []) != v:
            meta[k] = v
            changed = True
    return changed


def rename(cfg, old, new):
    old, new = (old or "").strip(), (new or "").strip()
    if old not in cfg["brains"] or not new or new in cfg["brains"]:
        return False
    cfg["brains"][new] = cfg["brains"].pop(old)
    cfg["brains"][new]["name"] = new
    for uuid, br in list(cfg["assign"].items()):
        if br == old:
            cfg["assign"][uuid] = new
    return True


def archive(cfg, name, archived=True):
    name = (name or "").strip()
    if name not in cfg["brains"]:
        return False
    cfg["brains"][name]["archived"] = bool(archived)
    return True


def share(cfg, brain, with_, tag=None):
    brain, with_ = (brain or "").strip(), (with_ or "").strip()
    tag = (tag or None)
    if brain not in cfg["brains"] or not with_:
        return False
    rules = cfg["brains"][brain]["shares"]
    for r in rules:
        if r.get("with") == with_ and (r.get("tag") or None) == tag:
            return False
    rules.append({"with": with_, "tag": tag})
    return True


def unshare(cfg, brain, with_, tag=None):
    brain, with_ = (brain or "").strip(), (with_ or "").strip()
    tag = (tag or None)
    if brain not in cfg["brains"]:
        return False
    rules = cfg["brains"][brain]["shares"]
    kept = [r for r in rules
            if not (r.get("with") == with_ and (r.get("tag") or None) == tag)]
    if len(kept) == len(rules):
        return False
    cfg["brains"][brain]["shares"] = kept
    return True


def assign(cfg, uuid, brain, pinned=False):
    """Manual assignment (`brains assign`). Sets the uuid→brain map (main is implicit —
    kept out of the map to stay lean) and, when `pinned`, records a permanent override the
    scorer respects forever (never re-scored, never yanked)."""
    uuid, brain = (uuid or "").strip(), (brain or "").strip()
    if not uuid or brain not in cfg["brains"]:
        return False
    if brain == DEFAULT_BRAIN:
        cfg["assign"].pop(uuid, None)   # default is implicit — keep the map lean
    else:
        cfg["assign"][uuid] = brain
    if pinned:
        cfg.setdefault("pinned", {})[uuid] = True
    return True


def is_pinned(cfg, uuid):
    return bool((cfg.get("pinned") or {}).get((uuid or "").strip()))


def auto_assign(cfg, uuid, brain):
    """Scorer-driven assignment. Respects pins and never yanks a task out of a non-main
    brain: writes ONLY when the task is currently on 'main' AND not pinned. Returns True
    iff it changed the assignment. Assigning to 'main' is a no-op (main is the default)."""
    uuid, brain = (uuid or "").strip(), (brain or "").strip()
    if not uuid or brain == DEFAULT_BRAIN or brain not in cfg["brains"]:
        return False
    if is_pinned(cfg, uuid):
        return False
    if brain_for(cfg, uuid) != DEFAULT_BRAIN:
        return False
    cfg["assign"][uuid] = brain
    return True


# ---- auto-attach scoring (F4) -----------------------------------------------

def _norm_repo(r):
    """A repo signal reduced to a comparable token: basename, lowercased, worktree suffix
    trimmed (so `Projectname-2704-x` and a `projectname` brain repo match on the leading
    stem too)."""
    r = os.path.basename(str(r or "").rstrip("/")).strip().lower()
    return r


def score_brains(cfg, signals, threshold=None):
    """Score every (non-archived) brain against a task's `signals` and pick a winner.

    signals = {"repos": [paths/names], "cwd": "<path>", "text": "title + summary",
               "category": "<TAG or key>", "skill": "<session skill hint>"}

    Weights: repo/cwd match W_REPO(4) · keyword hit W_KEYWORD(2) per hit capped
    W_KEYWORD_CAP(6) · category_affinity W_CATEGORY(2) · skill hint W_SKILL(1). Best total
    ≥ threshold wins; else DEFAULT_BRAIN. Ties break by brain list order (main first).

    Returns {"threshold": n, "winner": name, "scores": [{"name","total","breakdown":
    {repo,keyword,category,skill}, "archived": bool} …]} in list_brains order — the audit
    table `brains suggest` prints. Pure; never touches the store."""
    if threshold is None:
        threshold = SCORE_THRESHOLD
    signals = signals or {}
    repo_tokens = set()
    for r in (signals.get("repos") or []):
        t = _norm_repo(r)
        if t:
            repo_tokens.add(t)
    if signals.get("cwd"):
        t = _norm_repo(signals["cwd"])
        if t:
            repo_tokens.add(t)
    text = (signals.get("text") or "").lower()
    category = (signals.get("category") or "").strip().lower()
    skill = (signals.get("skill") or "").strip().lower()

    rows = []
    best_name, best_total = DEFAULT_BRAIN, -1
    for b in list_brains(cfg):
        name = b["name"]
        bd = {"repo": 0, "keyword": 0, "category": 0, "skill": 0}
        if not b.get("archived"):
            brepos = {_norm_repo(x) for x in (b.get("repos") or []) if x}
            brepos.discard("")
            # Exact token match, or worktree-stem match: a cwd basename like
            # `projectname-2704-x` matches a brain repo `projectname` (`<repo>-<slug>`
            # worktree naming). Prefix requires the "-" boundary so a short brain name
            # never swallows a longer unrelated repo that merely starts with the same
            # letters — `projectname` must not match `projectnamespace`; a more specific
            # exact token (e.g. a brain listing `projectname-infra`) still matches
            # exactly on its own.
            if repo_tokens & brepos or any(
                    t.startswith(br + "-") for t in repo_tokens for br in brepos):
                bd["repo"] = W_REPO
            kws = [k for k in (b.get("keywords") or []) if k]
            if text:
                hits = sum(1 for k in kws if k.lower() in text)
                bd["keyword"] = min(hits * W_KEYWORD, W_KEYWORD_CAP)
            aff = [c.strip().lower() for c in (b.get("category_affinity") or []) if c]
            if category and category in aff:
                bd["category"] = W_CATEGORY
            if skill and (skill == name.lower()
                          or skill in [k.lower() for k in kws]):
                bd["skill"] = W_SKILL
        total = sum(bd.values())
        rows.append({"name": name, "total": total, "breakdown": bd,
                     "archived": bool(b.get("archived"))})
        # main is only ever the fallback winner — a scored brain must beat it outright.
        if name != DEFAULT_BRAIN and not b.get("archived") and total > best_total:
            best_total, best_name = total, name
    winner = best_name if best_total >= threshold else DEFAULT_BRAIN
    return {"threshold": threshold, "winner": winner, "scores": rows}


# ---- derived block (computed at read, NEVER stored) -------------------------

def derive(cfg, name, tasks):
    """Compute the read-time derived block for brain `name` from `tasks` — a list of light
    dicts `{uuid, status, title, category, signals:{prs,stories}, updated_ts}`. Returns
    `{open_count, active_count, recent_focus, dominant_categories, top_signals,
    last_activity_ts}`. Pure; the caller supplies task views from the store."""
    mine = [t for t in (tasks or []) if brain_for(cfg, t.get("uuid") or "") == name]
    open_count = active_count = 0
    last_ts = 0
    cat_freq, sig_freq = {}, {}
    actives = []
    for t in mine:
        st = (t.get("status") or "").lower()
        if st != "closed":
            open_count += 1
        if st == "active":
            active_count += 1
            actives.append(t)
        ts = t.get("updated_ts") or 0
        if ts and ts > last_ts:
            last_ts = ts
        cat = (t.get("category") or "").strip()
        if cat:
            cat_freq[cat] = cat_freq.get(cat, 0) + 1
        sig = t.get("signals") or {}
        for s in (list(sig.get("prs") or []) + list(sig.get("stories") or [])):
            if s:
                sig_freq[s] = sig_freq.get(s, 0) + 1
    actives.sort(key=lambda t: t.get("updated_ts") or 0, reverse=True)
    recent_focus = [(t.get("title") or "").strip() for t in actives[:3]]
    dominant = sorted(cat_freq, key=lambda c: (-cat_freq[c], c))[:3]
    top_signals = sorted(sig_freq, key=lambda s: (-sig_freq[s], s))[:3]
    return {"open_count": open_count, "active_count": active_count,
            "recent_focus": recent_focus, "dominant_categories": dominant,
            "top_signals": top_signals, "last_activity_ts": last_ts or None}


# ---- resolution (read side, used by the exporter) ---------------------------

def brain_for(cfg, uuid):
    """The brain a task lives in (its full uuid → brain, default `main`)."""
    return (cfg or {}).get("assign", {}).get(uuid, DEFAULT_BRAIN)


def shares_for(cfg, uuid, tag=None):
    """The resolved audience list for a task: its brain's sharing rules that apply —
    a rule with no tag applies to every task in the brain; a tag-scoped rule applies
    only when it matches the task's category tag. Deduped, order-preserving."""
    brain = brain_for(cfg, uuid)
    meta = (cfg or {}).get("brains", {}).get(brain) or {}
    out, seen = [], set()
    for r in meta.get("shares", []):
        rt = r.get("tag") or None
        if rt is not None and tag is not None and rt != tag:
            continue
        if rt is not None and tag is None:
            continue
        w = r.get("with")
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def list_brains(cfg):
    """[{name, archived, shares, description, purpose, keywords, repos,
    category_affinity, created_ts}] in insertion order, `main` first."""
    names = list((cfg or {}).get("brains", {}).keys())
    names.sort(key=lambda n: (n != DEFAULT_BRAIN, n))
    out = []
    for n in names:
        m = _migrate_brain(n, dict(cfg["brains"][n]))
        out.append({"name": n, "archived": bool(m.get("archived")),
                    "shares": list(m.get("shares", [])),
                    "description": m.get("description", ""),
                    "purpose": m.get("purpose", ""),
                    "keywords": list(m.get("keywords", [])),
                    "repos": list(m.get("repos", [])),
                    "category_affinity": list(m.get("category_affinity", [])),
                    "created_ts": m.get("created_ts", 0)})
    return out
