"""brain-station — subscription memos: when an org knowledge node changes, every
referencing task hears about it.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4c) from the brain source tree's
``scripts/subscriptions.py`` @ 0.14.0 (module rename ``subscriptions`` ->
``subscribe``, per the plan's port map). One half of the port is a REWRITE: the
delivery bridge, below.

``check`` walks the reference stubs (:mod:`references`), finds the DIRTY ones
(their ``org_rev`` is behind the org-brain clone for that node file), resolves the
``tasks[]`` handles each stub recorded, and — when memos are enabled and the board
plane is reachable — delivers a memo to each task.

THE ONE SANCTIONED brain->board IMPORT (the two-plane bridge)
-------------------------------------------------------------
The standalone source delivered memos by SHELLING OUT to task-station's published
CLI (``<cli> memo send --task <ref> --text <body>``) — the only bridge available
when the two planes lived in two repos. In the monorepo they are one tree, so this
module imports ``board.memos`` and calls :func:`board.memos.memo_send` DIRECTLY.

This is the ONLY module in the brain plane allowed to import ``board.*``, ever,
and ``board.memos`` is the only board module it may name. Everything else about
the layer rule stands: brain may import core and its own siblings, never board.
``tests/brain/test_subscribe.py::FederationLayeringTest`` encodes the exception
precisely — every other 4c module is held to core+stdlib+siblings, and this one
is held to core+stdlib+siblings+``board.memos``.

The import is function-local, for two reasons worth keeping: (a) the whole module
stays importable — and every non-delivery path (detection, the change summary, the
report) stays usable — on a tree where the board plane is not on ``sys.path``;
(b) a fresh copy of the engine facade REGENERATES the ``board.*`` seam modules
(``lib/task-station.py``'s purge list), so a module reference cached at import
time can be a stale generation. Resolving it per delivery always gets the live one.

Guards baked in everywhere, because this fires from a detached hook:
  * config-gated (``knowledge_memos``; auto-on only when the board is detected)
  * graceful-absent — no board store ⇒ silent skip, one breadcrumb to the error log
  * idempotent — a memo fires ONCE per (node, node-rev, task); the delivered
    tuples are stamped in the state dir, so a re-check with nothing newly changed
    delivers nothing. A fresh node revision is a new node-rev ⇒ it re-fires; a
    ``brain ref refresh`` bumps the stub past the change ⇒ no longer dirty.

Stdlib only; the org-brain clone is read locally (no network); vault is untouched.
"""
import argparse
import json
import subprocess
from pathlib import Path

from . import config
from . import errorlog
from . import notes
from . import references

STATE_NAME = "subscriptions-delivered.json"


# --------------------------------------------------------------------------- #
# board-plane resolution (the ONLY sanctioned bridge — board.memos, never the DB)
# --------------------------------------------------------------------------- #
def board_store(cfg):
    """The board's store DIRECTORY (the parent of its sqlite file), or ``None``.

    ``brain.config`` already resolves ``tasks_db`` through ``core.paths.data_dir``
    — the same data home the board writes — so this derivation follows a relocated
    store for free."""
    db = cfg.get("tasks_db")
    return Path(db).parent if db else None


def board_present(cfg):
    """True iff the board's store is on disk.

    Replaces the source's ``cli_present`` (the published CLI file) AND its
    ``_task_station_detected`` (CLI-on-disk OR db-on-disk): with the bridge
    direct, the store IS the presence signal, and it is the source's own second
    limb. Also the guard that keeps this module from CREATING an empty store —
    ``board.store.get_backend`` would happily mint one, and the brain must never
    conjure a board out of nothing."""
    db = cfg.get("tasks_db")
    try:
        return bool(db and Path(db).exists())
    except OSError:
        return False


def _board_memos(cfg):
    """The live ``board.memos`` module, ready to read/write the board's store —
    or ``None`` when the board plane is unreachable (logged, never raised).

    The binding step is the one piece of board machinery this has to know about:
    every split board module reads the engine facade's live globals through
    ``_shared.g("NAME")``, and in a brain-only process (the detached org-pull
    check, ``brain subscriptions check``) NOTHING has loaded that facade. The
    delivery path reads exactly one of those globals — ``STORE`` — so when no
    facade has bound one, bind that single name from the config we already
    resolved. A facade already in the process wins: it is checked first and never
    overwritten."""
    if not board_present(cfg):
        return None
    try:
        import board.memos as memos   # THE sanctioned brain->board edge (see module docstring)
    except Exception as e:            # ImportError, or anything a board module raises on import
        errorlog.record("subscribe:board", e)
        return None
    try:
        memos.g("STORE")              # an engine facade already bound its globals
    except Exception:
        try:
            memos.bind({"STORE": str(board_store(cfg))})
        except Exception as e:
            errorlog.record("subscribe:board", e)
            return None
    return memos


def memos_enabled(cfg):
    """Whether subscription memos should be delivered. Explicit ``knowledge_memos``
    (True/False) wins; when unset (None) it auto-enables iff the board is detected
    — so a machine with no board store never tries, and one with it gets memos for
    free."""
    raw = cfg.get("knowledge_memos")
    if raw is not None:
        return bool(raw)
    return board_present(cfg)


# --------------------------------------------------------------------------- #
# delivered-state (idempotence stamps)
# --------------------------------------------------------------------------- #
def _state_file(cfg):
    d = cfg.get("state_dir")
    d = Path(d) if d else config.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / STATE_NAME


def _key(node, node_rev, task):
    return f"{node}\t{node_rev}\t{task}"


def _load_delivered(cfg):
    try:
        data = json.loads(_state_file(cfg).read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    items = data.get("delivered") if isinstance(data, dict) else None
    return set(items) if isinstance(items, list) else set()


def _save_delivered(cfg, keys):
    try:
        _state_file(cfg).write_text(json.dumps({"delivered": sorted(keys)}))
    except OSError as e:
        errorlog.record("subscribe:state", e)


# --------------------------------------------------------------------------- #
# node revision + change summary (read the local clone only)
# --------------------------------------------------------------------------- #
def _clone(cfg):
    d = cfg.get("org_brain_clone")
    if d and (Path(d) / ".git").exists():
        return Path(d)
    return None


def _node_rel(node):
    return f"notes/{node}.md"


def current_node_rev(cfg, node):
    """The clone commit sha that LAST touched this node file — the node's current
    revision. Idempotence keys on this (not the stub's stale org_rev) so each new
    node revision re-fires exactly one memo. ``None`` when unresolvable."""
    clone = _clone(cfg)
    if not clone:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(clone), "log", "-1", "--format=%H", "--", _node_rel(node)],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def _first_changed_heading(cfg, node, org_rev):
    """The first markdown heading ADDED to the node file since ``org_rev`` (scans
    the added side of the diff), or ``None``."""
    clone = _clone(cfg)
    if not clone or not org_rev:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(clone), "diff", f"{org_rev}..HEAD", "--", _node_rel(node)],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added = line[1:].strip()
            if added.startswith("#"):
                return added
    return None


def _current_verified(cfg, node):
    """The ``verified:`` date on the node's CURRENT content in the clone, or None."""
    clone = _clone(cfg)
    if not clone:
        return None
    f = clone / _node_rel(node)
    try:
        fm, _ = notes.parse_note(f.read_text(errors="ignore"))
    except OSError:
        return None
    return fm.get("verified") or None


def change_summary(cfg, node, org_rev):
    """A short human summary of what moved: new verified date + first changed
    heading. Degrades to ``content updated`` when the clone can't be diffed."""
    parts = []
    verified = _current_verified(cfg, node)
    if verified:
        parts.append(f"verified {verified}")
    heading = _first_changed_heading(cfg, node, org_rev)
    if heading:
        parts.append(f"changed: {heading}")
    return "; ".join(parts) if parts else "content updated"


# --------------------------------------------------------------------------- #
# check — the entry point
# --------------------------------------------------------------------------- #
def memo_text(node, summary):
    """The memo body. Names the exact re-fetch command so the task owner can act."""
    return (f"org knowledge updated: {node} ({summary}) — your reference is behind; "
            f"review + re-fetch with `brain ref refresh {node}`")


def _send_memo(memos, task, text):
    """Deliver ONE memo through ``board.memos`` — the direct in-process
    replacement for the source's CLI subprocess. Returns True on a clean send;
    any failure is a silent False with a breadcrumb, because a detached hook must
    never surface a board hiccup.

    Mirrors the CLI's ``memo send`` path exactly (``board.cmds.surface.cmd_memo``):
    resolve the ref (seq / id / id-prefix, then a bare id), post the memo, stamp
    ``updated_ts``, save. ``from_sid=None`` because the sender is not a session —
    the source passed no ``--session`` either, so the memo reads as "from desktop"
    on both sides of the port. No ``corrects`` target: a subscription memo says a
    reference is BEHIND; it replaces nothing in the durable stores."""
    try:
        t = memos.resolve_ref(task) or memos.load_task(task)
        if not t:
            errorlog.record("subscribe:memo", f"no task matching {task!r}")
            return False
        memos.memo_send(t, text, from_sid=None)
        t["updated_ts"] = memos._now()
        memos.save_task(t)
    except Exception as e:
        errorlog.record("subscribe:memo", f"memo send failed for {task}: {e}")
        return False
    return True


def check(cfg, *, deliver=True, today=None):
    """Walk reference stubs, surface DIRTY ones, and (when enabled + reachable)
    deliver one idempotent memo per (dirty node, task). Returns a report dict::

        {"dirty": [{node, org_rev, node_rev, tasks, summary, delivered:[task...]}],
         "delivered": <int total memos sent this run>,
         "enabled": <bool>, "board_absent": <bool>}

    Idempotence keys on (node, node_rev, task): a re-check with nothing newly
    changed delivers nothing; a fresh node revision re-fires exactly one memo per
    task; a ``ref refresh`` clears the stub's dirtiness so no memo fires at all.
    ``deliver=False`` computes the report without touching the board."""
    report = {"dirty": [], "delivered": 0,
              "enabled": memos_enabled(cfg), "board_absent": False}
    dirty_refs = references.ref_list(cfg, dirty=True)

    active = deliver and report["enabled"] and bool(dirty_refs)
    memos = _board_memos(cfg) if active else None
    if active and memos is None:
        report["board_absent"] = True
        errorlog.record("subscribe",
                        f"task-station board unreachable — skipped subscription memos "
                        f"for {len(dirty_refs)} dirty reference(s)")

    delivered = _load_delivered(cfg)
    changed = False
    for r in dirty_refs:
        node, org_rev = r["org_node"], r["org_rev"]
        entry = {
            "node": node,
            "org_rev": org_rev,
            "node_rev": current_node_rev(cfg, node) or org_rev,
            "tasks": list(r["tasks"]),
            "summary": change_summary(cfg, node, org_rev),
            "delivered": [],
        }
        report["dirty"].append(entry)
        if memos is None:
            continue
        text = memo_text(node, entry["summary"])
        for task in entry["tasks"]:
            key = _key(node, entry["node_rev"], task)
            if key in delivered:
                continue
            if _send_memo(memos, task, text):
                delivered.add(key)
                changed = True
                report["delivered"] += 1
                entry["delivered"].append(task)

    if changed:
        _save_delivered(cfg, delivered)
    return report


def report_lines(rep):
    """Human-readable surfacing of a :func:`check` report — the lines /brain-heal
    (and the manual CLI) print: the dirty-reference + delivered-memo tallies, then
    one line per dirty node (with its change summary and any memo recipients)."""
    lines = [f"dirty references: {len(rep['dirty'])}  memos delivered: {rep['delivered']}"]
    if rep.get("board_absent"):
        lines.append("  (task-station board absent — memos skipped)")
    for d in rep["dirty"]:
        got = f" -> memo: {', '.join(d['delivered'])}" if d.get("delivered") else ""
        lines.append(f"  {d['node']} ({d['summary']}){got}")
        if d.get("tasks"):
            lines.append(f"      tasks: {', '.join(d['tasks'])}")
    return lines


def main(argv=None):
    p = argparse.ArgumentParser(prog="brain-subscribe", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="deliver memos to tasks whose org references went dirty")
    c.add_argument("--no-deliver", action="store_true",
                   help="report dirty references only; do not send memos")
    a = p.parse_args(argv)
    try:
        cfg = config.load()
        rep = check(cfg, deliver=not a.no_deliver)
    except Exception as e:  # a hook-triggered path must never crash a session
        errorlog.record("subscribe:main", e)
        return
    for line in report_lines(rep):
        print(line)


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the entry point
    main()
