"""brain-station — query engine + orchestration CLI (the private brain).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4c) from the brain source tree's
``scripts/brain.py`` @ 0.14.0 (module rename ``brain`` -> ``brain.search``, per
the plan's port map). Body behaviour is unchanged; the ``sys.path`` self-bootstrap
is gone (it is a package module now) and every sibling import is relative.

Vault schema/rules: the vault's CLAUDE.md. Paths resolve at runtime via
:mod:`brain.config` (env -> the brain config file -> defaults) — nothing is baked
in at install time.

THE ENTRY POINT IS ``-m``: ``python3 -m brain.search <cmd>`` with ``lib/`` on
``PYTHONPATH``. A package module with relative imports cannot be run by path, so
the ``__main__`` guard below is LIVE only through ``-m`` (chunks 3/4a/4b marked
theirs inert for exactly that reason; this one is exercised by
``tests/brain/test_write_guards.py`` and the CLI cases in
``tests/brain/test_naming_write_path.py``). Phase 5 owns whatever wrapper ships.

Layer rule: brain may import core and its own siblings, never board. Stdlib + the
sibling modules only — the ONE sanctioned board edge lives in :mod:`subscribe`,
and this module reaches it through that sibling, never directly.
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

from . import config
from . import notes
from . import naming
from . import references
from . import subscribe
from . import errorlog
from . import episodic as _episodic  # aliased: 'episodic' is a param name in default_roots/cmd_search
from . import peers
from . import publish

_CFG = config.load()
VAULT = _CFG["vault"]
MEMORY = _CFG["memory"]
CONTENT_DIRS = ("notes", "projects", "reports", "plans", "raw")


# Tier priority for cross-tier dedup: lower wins.
# notes(vault) > memory > org_brain > peers > task-station.
TIER_NOTES, TIER_MEMORY, TIER_ORG_BRAIN, TIER_PEERS, TIER_TASKS = 0, 1, 2, 3, 4


def _rg(rg_args):
    """Run ripgrep, returning stdout. rg exit 0/1 are normal (match / no match);
    exit >=2 is a real error — surfaced to stderr AND the error log, never swallowed."""
    try:
        r = subprocess.run(["rg", *rg_args], capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("brain: rg (ripgrep) not found — install it (macOS: brew install ripgrep)")
    if r.returncode >= 2:
        msg = f"rg error (exit {r.returncode}) for args {rg_args[:4]}…: {r.stderr.strip()}"
        sys.stderr.write("brain: " + msg + "\n")
        errorlog.record("brain:rg", msg)
    return r.stdout


def _rg_files(term, path_strs):
    """Files (one per line) matching ``term`` as a FIXED STRING (``-F``): a term
    like ``(paren`` or ``a.b`` is searched literally, never as a regex that could
    error out and silently return zero hits. A multi-word term is one phrase."""
    return _rg(["-liF", "--glob", "*.md", "-e", term, *path_strs]).splitlines()


def _fm(path):
    """Parse a note's frontmatter ONCE (quote-aware, via notes). Cached by the
    caller so each hit is read a single time."""
    try:
        fm, _ = notes.parse_note(Path(path).read_text(errors="ignore"))
        return fm
    except OSError:
        return {}


def _norm_title(s):
    return re.sub(r"[\s_-]+", "-", (s or "").strip().lower())


def default_roots(episodic=False, include_peers=False, cfg=None):
    """The standard tiered search roots (ordered, tagged with tier priority).

    ``include_peers`` adds a peers tier (each cloned teammate's ``notes/``) BELOW
    org_brain and ABOVE task-station — explicit opt-in only (CLI ``--peers`` / MCP
    ``peers``); context injection never passes it. ``cfg`` defaults to the
    module config; a caller may pass an explicit one (tests)."""
    cfg = cfg or _CFG
    vault = cfg["vault"]
    memory = cfg.get("memory")
    roots = [(TIER_NOTES, vault / d) for d in CONTENT_DIRS]
    if memory:
        roots.append((TIER_MEMORY, memory))
    roots.append((TIER_ORG_BRAIN, vault / "org_brain"))
    if include_peers:
        for root in peers.peer_roots(cfg):
            roots.append((TIER_PEERS, root))
    if episodic:
        # The episodic adapter owns where the exported markdown mirror lives —
        # no hardcoded task-station/ path here (one-way consumer, glue lives there).
        for root in _episodic.episodic_roots():
            roots.append((TIER_TASKS, root))
    return [(t, p) for t, p in roots if p.exists()]


def search_hits(terms, roots, limit=12):
    """Ranked, cross-tier-deduped hits over ``roots`` (an ordered list of
    ``(tier, path)``; lower tier wins on dupes).

    Scoring per term: title match 3 > description match 2 > body match 1.
    Tiebreak: newest ``verified:`` date, then path. Terms are fixed-string
    matched (see :func:`_rg_files`). Duplicate (slug, normalized-title) hits
    across tiers collapse to the highest tier and do NOT consume the hit budget
    — dedup happens before the ``limit`` cut. Returns ``[(path, score, description)]``.
    Frontmatter is parsed exactly once per hit."""
    roots = [(int(t), Path(p)) for t, p in roots if Path(p).exists()]
    if not roots or not terms:
        return []
    path_strs = [str(p) for _, p in roots]
    fmcache = {}

    def fm_of(f):
        if f not in fmcache:
            fmcache[f] = _fm(f)
        return fmcache[f]

    scores = {}
    for term in terms:
        tl = term.lower()
        for f in _rg_files(term, path_strs):
            fm = fm_of(f)
            if tl in Path(f).stem.lower():
                w = 3
            elif tl in fm.get("description", "").lower():
                w = 2
            else:
                w = 1
            scores[f] = scores.get(f, 0) + w

    def tier_of(f):
        fp = Path(f)
        best = 99
        for t, root in roots:
            if root == fp.parent or root in fp.parents:
                best = min(best, t)
        return best

    def key_of(f):
        fm = fm_of(f)
        stem = Path(f).stem
        title = fm.get("name") or fm.get("title") or stem
        return (stem.lower(), _norm_title(title))

    # dedup: per (slug, title) keep the best tier, then highest score
    best = {}
    for f, sc in scores.items():
        k, tr = key_of(f), tier_of(f)
        cur = best.get(k)
        if cur is None or (tr, -sc) < (cur[1], -cur[2]):
            best[k] = (f, tr, sc)

    def sort_key(item):
        f, _tr, sc = item
        v = fm_of(f).get("verified", "")
        dated, ordinal = 1, 0  # undated (or unparseable) sorts after dated within a score band
        if re.match(r"\d{4}-\d{2}-\d{2}", v or ""):
            try:
                dated, ordinal = 0, -datetime.date.fromisoformat(v[:10]).toordinal()
            except ValueError:
                dated, ordinal = 1, 0
        return (-sc, dated, ordinal, f)

    ranked = sorted(best.values(), key=sort_key)
    return [(f, sc, fm_of(f).get("description", "")) for f, _tr, sc in ranked[:limit]]


def current_task_handle(cfg=None):
    """The task handle for a task-attached session (config/env ``task``), or None.
    When set, ``brain search`` nudges the caller to record a reference for any
    org (org_brain-clone) hit it surfaces — the explicit, auditable half of the
    task↔knowledge wire (automatic hook-path capture is a later iteration)."""
    cfg = cfg or _CFG
    return cfg.get("task") or None


def _is_org_brain_hit(path, cfg):
    """True iff ``path`` lives under the org_brain (org clone) tier root."""
    org_brain = cfg["vault"] / "org_brain"
    p = Path(path)
    return org_brain == p.parent or org_brain in p.parents


def reference_hints(hits, cfg=None):
    """One hint line per org (org_brain-clone) hit — but only when the session is
    task-attached. ``hits`` is the ``search_hits`` result. Returns ``[]`` when
    there is no current task or no org hit. Mirrored by MCP ``brain_search``."""
    cfg = cfg or _CFG
    handle = current_task_handle(cfg)
    if not handle:
        return []
    label = cfg.get("org_label") or "org"
    lines = []
    for f, _s, _d in hits:
        if _is_org_brain_hit(f, cfg):
            slug = Path(f).stem
            lines.append(f"hint: `brain ref add {slug} --task {handle}` "
                         f"— record this {label} reference")
    return lines


def cmd_search(a):
    hits = search_hits(a.terms, default_roots(a.episodic, a.peers), a.limit)
    if not hits:
        print("no hits")
        return
    for f, s, desc in hits:
        # a hit under the peers tree displays as peer:<alias>/<slug>, not a raw path
        print(f"{s}  {peers.peer_label(f, _CFG) or f}")
        if desc:
            print(f"      {desc}")
    for hint in reference_hints(hits):
        print(hint)
    if a.snippets:
        # snippet grep is also fixed-string per term (one -e each), so metachars are literal
        args = ["-inF", "-C1"]
        for t in a.terms:
            args += ["-e", t]
        print(_rg([*args, *[f for f, _, _ in hits[:5]]]))


def recent_tasks(days=14):
    """Recent task-station activity via the episodic adapter (the ONLY episodic
    access point). Returns a list of folded task records (dicts), or ``None``
    when the episodic layer is unavailable."""
    return _episodic.recent_tasks(days)


def cmd_recent_tasks(a):
    rows = recent_tasks(a.days)
    if rows is None:
        print("brain: episodic layer unavailable — no Tasktrail stream or exported "
              "mirror found (this is fine without task-station)")
        return
    if not rows:
        print("no recent tasks")
        return
    for r in rows:
        print(f"#{r.get('seq')} [{r.get('status')}] {r.get('title')}")
        if r.get("summary"):
            print(f"    {str(r['summary'])[:300]}")


def naming_contract(cfg=None):
    """The merged naming contract: shipped generic half + this install's org
    registry (read from the org-brain clone; absent ⇒ generic only)."""
    cfg = cfg or _CFG
    clone = cfg.get("org_brain_clone")
    return naming.load_contract(str(clone) if clone else None)


def new_note(slug, description, type_="reference", scope="personal", source="manual",
             folder="notes", tags=None, contract=None, area=None, plane=None,
             extra=None):
    config.require_valid()  # never write to a silently-defaulted vault
    # The org schema requires area: + plane: on every knowledge node. area is
    # DERIVED from the slug's domain, and an unregistered domain raises here
    # (NoteIOError) rather than producing an unstamped node.
    stamp = notes.knowledge_stamp(
        slug, folder, contract=contract if contract is not None else naming_contract(),
        area=area, plane=plane)
    # Single write path (brain/notes.py): slug + traversal validation,
    # YAML-safe frontmatter, git-commit-at-write. mode='create' fails if it exists.
    return notes.write_note(
        VAULT, slug, mode="create", description=description,
        type=type_, scope=scope, source=source, folder=folder, actor="agent",
        tags=tags, area=stamp.get("area"), plane=stamp.get("plane"), extra=extra,
    )


def _parse_tags(raw):
    """``--tags a,b, c`` -> ``["a", "b", "c"]`` (comma-split, trimmed, deduped-empty)."""
    if not raw:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


def merge_target(slug, description, dirs, contract=None):
    """The mandatory pre-create lookup, run TWO ways for two different reasons.

    Identity first: an exact or name-normalized hit on the SLUG is the node, so
    ``find_target`` grades it ``update`` outright. Otherwise the description is
    what gets graded — the contract's ``scoreOn``. Slug text can still SUGGEST a
    candidate (``choose``), which is the grey band working as designed; what it
    may never do is drive an update, because two real nodes scored 0.595 on slug
    text and were entirely different facts.

    Returns ``find_target``'s dict, or ``None`` when nothing is close enough.
    """
    by_slug = naming.find_target(slug, dirs, contract)
    if by_slug and by_slug["action"] == "update":
        return by_slug
    by_desc = naming.find_target(description, dirs, contract)
    if by_desc and by_desc["action"] == "update":
        return by_desc
    candidates = [c for c in (by_desc, by_slug) if c]
    return max(candidates, key=lambda c: c["score"]) if candidates else None


def _print_target(hit, label):
    print(f"{label}: {hit['slug']}  ({hit['reason']}, score {hit['score']}, "
          f"description {hit['descScore']})")
    print(f"      {hit['path']}")


def converge(target_slug, proposed_slug, text, source="manual"):
    """Record a ``choose`` decision as CONVERGENCE: the fact lands on the existing
    node (a dated bullet under ``## Updates``) and the node is stamped
    ``converged-with:`` so the abandoned name is on the record and the next
    session does not re-litigate the same pair."""
    for folder in ("notes", "projects"):
        if (VAULT / folder / f"{target_slug}.md").exists():
            return notes.write_note(
                VAULT, target_slug, mode="append", body=text, folder=folder,
                source=source, actor="agent",
                extra={"converged-with": proposed_slug})
    sys.exit(f"brain: --update target {target_slug!r} not found under notes/ or projects/")


def cmd_new(a):
    config.require_valid()  # a write path — refuse a silently-defaulted vault first
    contract = naming_contract()

    findings = naming.slug_findings(a.slug, a.folder, contract)
    for f in findings:
        sys.stderr.write(f"brain: naming {f['severity']} — {f['check']}: {f['detail']}\n")
        if f.get("fix"):
            sys.stderr.write(f"          fix: {f['fix']}\n")
    if naming.has_error(findings):
        sys.exit(f"brain: refusing to create {a.slug!r} — compose the slug as "
                 f"<domain>[-<subdomain>]-<subject> from a registered domain "
                 f"(see the fix above)")

    # The merge-target lookup is MANDATORY — no node is created without a result
    # in hand — but the ACTION is graded, and only the caller resolves the middle.
    hit = merge_target(a.slug, a.description, find_target_dirs(), contract)
    extra = None
    if hit and hit["action"] == "update":
        _print_target(hit, "SAME FACT")
        sys.exit(f"brain: not creating {a.slug!r} — that node already states this "
                 f"fact; update it in place (bump verified:, add a dated correction "
                 f"if the claim changed)")
    if hit and hit["action"] == "choose":
        _print_target(hit, "CANDIDATE")
        if a.update:
            path = converge(a.update, a.slug, a.description, a.source)
            print(path)
            print(f"converged into {a.update} (recorded as converged-with: {a.slug})")
            return
        if not a.new:
            sys.exit("brain: a candidate exists but the call needs judgement — "
                     "re-run with --new (a distinct fact; recorded as distinct-from:) "
                     f"or --update {hit['slug']} (the same fact)")
        extra = {"distinct-from": hit["slug"]}

    try:
        path = new_note(a.slug, a.description, a.type, a.scope, a.source, a.folder,
                        tags=_parse_tags(a.tags), contract=contract,
                        area=a.area, plane=a.plane, extra=extra)
    except notes.NoteIOError as e:
        sys.exit(f"brain: {e}")
    print(path)
    print("REMINDER: fill body, add to INDEX.md, link from a hub, then `brain log note <slug>`")


def find_target_dirs(cfg=None):
    """Default node dirs for ``find-target``: the vault's own knowledge nodes
    (notes/ + projects/). The promote pipeline passes the org-brain clone instead."""
    cfg = cfg or _CFG
    return [cfg["vault"] / "notes", cfg["vault"] / "projects"]


def cmd_find_target(a):
    text = " ".join(a.text)
    hit = naming.find_target(text, find_target_dirs(), naming_contract(),
                             threshold=a.threshold)
    if not hit:
        print("none")
        return
    print(f"{hit['slug']}  ({hit['reason']}, score {hit['score']})")
    print(f"      {hit['path']}")


def cmd_ref(a):
    if a.action == "add":
        config.require_valid()  # a write — refuse a silently-defaulted vault
        if not a.task:
            sys.exit("brain ref add: --task <handle> is required")
        try:
            path = references.ref_add(_CFG, a.slug, a.task)
        except notes.NoteIOError as e:
            sys.exit(f"brain: {e}")
        print(path)
        return
    if a.action == "refresh":
        config.require_valid()  # a write — refuse a silently-defaulted vault
        if not a.slug:
            sys.exit("brain ref refresh: <slug> is required")
        try:
            path = references.ref_refresh(_CFG, a.slug)
        except notes.NoteIOError as e:
            sys.exit(f"brain: {e}")
        print(path)
        return
    # list (both directions of the wire)
    rows = references.ref_list(_CFG, task=a.task, node=a.node, dirty=a.dirty)
    if not rows:
        print("no references")
        return
    for r in rows:
        flag = "  DIRTY" if r["dirty"] else ""
        print(f"{r['org_node']}  (rev {r['org_rev'][:8] or '—'}, fetched {r['fetched']}){flag}")
        if r["tasks"]:
            print(f"      tasks: {', '.join(r['tasks'])}")


def cmd_subscriptions(a):
    # Manual trigger for the memo check (same engine the org-brain-pull hook and
    # /brain-heal use). Reads the org clone; the only thing written outside the
    # brain plane is a board memo, through subscribe's sanctioned bridge.
    rep = subscribe.check(_CFG, deliver=not a.no_deliver)
    if not rep["enabled"] and not a.no_deliver:
        print("  (memos disabled — set knowledge_memos, or task-station not detected)")
    for line in subscribe.report_lines(rep):
        print(line)


def append_log(op, message):
    config.require_valid()  # never write to a silently-defaulted vault
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(VAULT / "LOG.md", "a") as fh:
        fh.write(f"- {stamp} · {op} · {message}\n")


def cmd_log(a):
    append_log(a.op, " ".join(a.message))
    print("logged")


def status_text():
    lines = []
    lines.append(f"vault          {VAULT}{'' if VAULT.exists() else '  (MISSING — run /brain-init)'}")
    for d in (*CONTENT_DIRS, "memory", "task-station"):
        p = VAULT / d
        if p.exists():
            lines.append(f"{d:14} {len(list(p.rglob('*.md')))} notes")
    linked = "linked" if (VAULT / "org_brain").exists() else "not yet linked (queue: notes/_org_brain-queue.md)"
    lines.append(f"{'org_brain':14} {linked}")
    log = VAULT / "LOG.md"
    if log.exists():
        lines.append("\nrecent LOG:")
        lines.extend(log.read_text().splitlines()[-5:])
    return "\n".join(lines)


def cmd_status(a):
    print(status_text())


def cmd_publish(a):
    config.require_valid()  # reads the vault; refuse a silently-defaulted one
    mirror = a.mirror or _CFG["publish_mirror"]
    if not VAULT.exists():
        sys.exit(f"brain: vault missing at {VAULT} — run /brain-init")
    res = publish.run(_CFG, mirror=mirror, owner=a.owner)
    publish._print_summary(res, mirror)


def cmd_peers(a):
    if a.action == "list":
        rows = peers.list_peers(_CFG)
        if not rows:
            print("no peers (registry empty / org_brain not linked; add via registry.json "
                  "or the peers_extra config key)")
            return
        for r in rows:
            mark = "✓" if r["cloned"] else "·"
            print(f"{mark} {r['alias']:16} {r['name']}  {r['shared']}")
        return
    if a.action == "sync":
        res = peers.sync(_CFG, a.alias)
        if not res:
            print("no peer clones to sync")
            return
        for r in res:
            print(f"peers sync {r['alias']}: {r['status']}")
        return
    if not a.alias:
        sys.exit(f"brain peers {a.action}: an alias is required")
    if a.action == "add":
        res = peers.add(_CFG, a.alias)
    else:  # remove
        res = peers.remove(_CFG, a.alias)
    tail = f" — {res['message']}" if res.get("message") else ""
    tail += f" -> {res['path']}" if res.get("path") else ""
    print(f"peers {a.action} {a.alias}: {res['status']}{tail}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="brain", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="ranked search across the brain (+memory, +org_brain)")
    s.add_argument("terms", nargs="+")
    s.add_argument("--episodic", action="store_true")
    s.add_argument("--peers", action="store_true", help="also search cloned teammates' shared brains (read-only)")
    s.add_argument("--limit", type=int, default=12)
    s.add_argument("--snippets", action="store_true")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("recent-tasks", help="recent task-station activity (read-only; graceful without it)")
    s.add_argument("--days", type=int, default=14)
    s.set_defaults(fn=cmd_recent_tasks)
    s = sub.add_parser("new", help="create a note skeleton")
    s.add_argument("slug")
    s.add_argument("--description", required=True)
    s.add_argument("--type", default="reference")
    s.add_argument("--scope", default="personal")
    s.add_argument("--source", default="manual")
    s.add_argument("--folder", default="notes")
    s.add_argument("--tags", help="comma-separated flat tags (departments AND teams)")
    s.add_argument("--area", help="override the area derived from the slug's domain")
    s.add_argument("--plane", help="override the plane (default: knowledge for notes/ and projects/)")
    s.add_argument("--new", action="store_true",
                   help="graded lookup returned a candidate: this is a DISTINCT fact "
                        "(recorded as distinct-from:)")
    s.add_argument("--update", metavar="SLUG",
                   help="graded lookup returned a candidate: this is the SAME fact — "
                        "append it to SLUG (recorded as converged-with:)")
    s.set_defaults(fn=cmd_new)
    s = sub.add_parser("find-target", help="best existing merge-target node for proposed content (or 'none')")
    s.add_argument("text", nargs="+", help="proposed title/description")
    s.add_argument("--threshold", type=float, default=0.6)
    s.set_defaults(fn=cmd_find_target)
    s = sub.add_parser("ref", help="reference records — the task↔knowledge wire (add / list / refresh)")
    s.add_argument("action", choices=["add", "list", "refresh"])
    s.add_argument("slug", nargs="?", help="org node slug (required for add / refresh)")
    s.add_argument("--task", help="task handle (required for add; a filter for list)")
    s.add_argument("--node", help="list only: filter to one org node slug")
    s.add_argument("--dirty", action="store_true", help="list only: refs whose org node moved on since fetch")
    s.set_defaults(fn=cmd_ref)
    s = sub.add_parser("subscriptions", help="memo tasks whose org references went dirty (reads the clone; the only outside write is a board memo)")
    s.add_argument("action", choices=["check"])
    s.add_argument("--no-deliver", action="store_true", help="report dirty references only; do not send memos")
    s.set_defaults(fn=cmd_subscriptions)
    s = sub.add_parser("log", help="append to LOG.md")
    s.add_argument("op")
    s.add_argument("message", nargs="+")
    s.set_defaults(fn=cmd_log)
    s = sub.add_parser("status", help="brain health at a glance (also prints the configured vault path)")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("publish", help="mirror eligible notes to your shared brain (opt-out; scope: private excluded)")
    s.add_argument("--mirror", help="override the publish mirror dir (default: config publish_mirror)")
    s.add_argument("--owner", help="owner name/UPN for the mirror README header")
    s.set_defaults(fn=cmd_publish)
    s = sub.add_parser("peers", help="teammates' shared brains — lazy clone + sync + search (never auto-pulled)")
    s.add_argument("action", choices=["list", "add", "sync", "remove"])
    s.add_argument("alias", nargs="?", help="peer alias (required for add/remove; optional for sync)")
    s.set_defaults(fn=cmd_peers)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except config.ConfigError as e:
        sys.exit(f"brain: {e}")


if __name__ == "__main__":
    main()
