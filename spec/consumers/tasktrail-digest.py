#!/usr/bin/env python3
"""Tasktrail reference consumer #2 — a plain, non-Obsidian digest compiler.

Reads ANY `tasktrail/` bundle (see docs/spec/TASKTRAIL.md, spec_version 1.0) and
compiles a plain `digest.md`: open tasks, recently-closed tasks, and a per-task
one-liner — with ZERO wikilink / frontmatter assumptions in its OUTPUT. Stdlib only.

Contract behaviours it demonstrates:
  * bootstrap from notes/ (full state) then tail events/ from a saved cursor;
  * honour `task.deleted` tombstones (drop the task);
  * detect a manifest `generation` bump and FULL re-sync (redaction) — discard
    derived state and re-bootstrap so forgotten content is absent;
  * idempotent re-runs: the same input produces byte-identical `digest.md`;
  * never writes outside its target `--out` directory (only `digest.md` + a
    `.tasktrail-digest-state.json` cursor/state file land there).

Usage:
    tasktrail-digest.py --stream <tasktrail-dir> --out <target-dir>
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

STATE_NAME = ".tasktrail-digest-state.json"
DIGEST_NAME = "digest.md"
_ONELINER_MAX = 120


# ----------------------------------------------------------------- reading ----

def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_manifest(stream_dir):
    with open(os.path.join(stream_dir, "tasktrail.json"), encoding="utf-8") as f:
        return json.load(f)


def _iter_events(stream_dir):
    """Yield (global_index, envelope) across shards in spec read order: shard name
    ascending, then append (line) order within a shard."""
    ev_dir = os.path.join(stream_dir, "events")
    if not os.path.isdir(ev_dir):
        return
    idx = 0
    for name in sorted(n for n in os.listdir(ev_dir) if n.endswith(".jsonl")):
        shard = name
        line_no = 0
        with open(os.path.join(ev_dir, name), encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                line_no += 1
                yield (idx, shard, line_no, json.loads(s))
                idx += 1


# ------------------------------------------------------------- frontmatter ----

def _frontmatter(text):
    """Minimal reader for the flat-YAML frontmatter subset Tasktrail notes use:
    `key: scalar`, `key: []`, and `key:` + `  - item` block lists. Returns a dict
    of the keys this consumer needs (values are strings; quotes stripped)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    fm, cur = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("  - ") and cur is not None:
            fm.setdefault(cur, [])
            if isinstance(fm[cur], list):
                fm[cur].append(_unquote(line[4:]))
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):(.*)$", line)
        if not m:
            continue
        key, rest = m.group(1), m.group(2).strip()
        if rest == "":
            cur = key
            fm[key] = []
        elif rest == "[]":
            fm[key] = []
            cur = None
        else:
            fm[key] = _unquote(rest)
            cur = None
    return fm


def _unquote(s):
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


def _section(text, heading):
    """The body text under a `## <heading>` note section, collapsed to one line;
    the `_(none)_` placeholder reads as empty. Body-only, best-effort."""
    m = re.search(r"^##\s+%s\s*$" % re.escape(heading), text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s+", rest, re.MULTILINE)
    chunk = rest[:nxt.start()] if nxt else rest
    chunk = chunk.strip()
    if chunk == "_(none)_":
        return ""
    return re.sub(r"\s+", " ", chunk).strip()


# ---------------------------------------------------------------- state --------

def _blank_task(uuid):
    return {"uuid": uuid, "seq": None, "title": "", "status": "open",
            "goal": "", "state": "", "summary": "", "closed_date": "", "n": 0}


def _bootstrap(stream_dir):
    """Seed task state from notes/ (full current state). Returns {uuid: task}."""
    tasks = {}
    notes_dir = os.path.join(stream_dir, "notes")
    if not os.path.isdir(notes_dir):
        return tasks
    for name in sorted(n for n in os.listdir(notes_dir) if n.endswith(".md")):
        text = _read(os.path.join(notes_dir, name))
        fm = _frontmatter(text)
        uuid = fm.get("uuid")
        if not uuid:
            continue
        t = _blank_task(uuid)
        seq = fm.get("seq")
        t["seq"] = int(seq) if isinstance(seq, str) and seq.isdigit() else None
        t["title"] = fm.get("title") or ""
        t["status"] = fm.get("status") or "open"
        t["closed_date"] = fm.get("closed") or ""
        t["goal"] = _section(text, "Goal")
        t["state"] = _section(text, "State")
        t["summary"] = _section(text, "Summary")
        tasks[uuid] = t
    return tasks


def _apply(tasks, env):
    """Fold one event into `tasks` (a {uuid: task} map). Skips already-applied
    events (by per-task n) and redaction-stub payloads."""
    u = (env.get("task") or {}).get("uuid")
    if not u:
        return
    n = env.get("n")
    ev = env.get("event")
    data = env.get("data") or {}

    if ev == "task.deleted" or ev == "task.redacted":
        tasks.pop(u, None)                    # tombstone / forgotten → drop
        return

    t = tasks.get(u)
    if t is None:
        t = _blank_task(u)
        tasks[u] = t
    if isinstance(n, int):
        if n <= t["n"]:
            return                            # idempotent: already applied
        t["n"] = n
    seq = (env.get("task") or {}).get("seq")
    if isinstance(seq, int):
        t["seq"] = seq
    if data.get("redacted") is True:
        return                                # stubbed payload: nothing to fold

    if ev == "task.created":
        if data.get("title"):
            t["title"] = data["title"]
        if data.get("goal"):
            t["goal"] = data["goal"]
        if data.get("status"):
            t["status"] = data["status"]
    elif ev == "task.updated":
        f = data.get("fields") or {}
        for k in ("title", "goal", "state", "summary"):
            if k in f:
                t[k] = f[k]
    elif ev == "task.status":
        t["status"] = data.get("status") or t["status"]
        cts = data.get("closed_ts")
        if cts:
            t["closed_date"] = datetime.fromtimestamp(cts, timezone.utc).strftime("%Y-%m-%d")
        elif t["status"] != "closed":
            t["closed_date"] = ""             # reopened
    elif ev in ("task.checkpoint", "task.snapshot"):
        for k in ("goal", "state", "summary"):
            if data.get(k):
                t[k] = data[k]


# --------------------------------------------------------------- rendering ----

def _one_liner(t):
    for k in ("summary", "state", "goal"):
        v = (t.get(k) or "").strip()
        if v:
            v = re.sub(r"\s+", " ", v)
            return v[:_ONELINER_MAX]
    return ""


def _label(t):
    return ("#%s · %s" % (t["seq"], t["title"])) if t["seq"] is not None else (t["title"] or t["uuid"])


def _render(tasks, manifest):
    """The plain digest.md — deterministic, no timestamps, no wikilinks."""
    vals = list(tasks.values())
    is_closed = lambda t: (t.get("status") == "closed")
    openish = [t for t in vals if not is_closed(t)]
    closed = [t for t in vals if is_closed(t)]
    openish.sort(key=lambda t: (t["seq"] is None, t["seq"] if t["seq"] is not None else 0, t["uuid"]))
    closed.sort(key=lambda t: (t["closed_date"], t["seq"] if t["seq"] is not None else 0, t["uuid"]),
                reverse=True)

    out = ["# Task digest", ""]
    out.append("Tasktrail spec_version %s · generation %s"
               % (manifest.get("spec_version", "?"), manifest.get("generation", "?")))
    out.append("")
    out.append("## Open (%d)" % len(openish))
    out.append("")
    if openish:
        for t in openish:
            ol = _one_liner(t)
            out.append("- %s — %s" % (_label(t), ol) if ol else "- %s" % _label(t))
    else:
        out.append("_(none)_")
    out.append("")
    out.append("## Recently closed (%d)" % len(closed))
    out.append("")
    if closed:
        for t in closed:
            when = t["closed_date"] or "?"
            out.append("- %s — closed %s" % (_label(t), when))
    else:
        out.append("_(none)_")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ driver -----

def _load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def sync(stream_dir, out_dir):
    """Bootstrap-or-resume, tail new events, and (re)write digest.md. Returns the
    path to the written digest.md."""
    stream_dir = os.path.abspath(stream_dir)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    state_path = os.path.join(out_dir, STATE_NAME)
    digest_path = os.path.join(out_dir, DIGEST_NAME)
    # target confinement: everything we write is under out_dir by construction.
    for p in (state_path, digest_path):
        if os.path.commonpath([out_dir, os.path.abspath(p)]) != out_dir:
            raise ValueError("refusing to write outside target dir: %s" % p)

    manifest = _load_manifest(stream_dir)
    gen = manifest.get("generation")
    prior = _load_state(state_path)

    if prior and prior.get("generation") == gen and "tasks" in prior:
        tasks = {t["uuid"]: t for t in prior["tasks"]}     # resume
        consumed = int(prior.get("events_consumed", 0))
    else:
        tasks = _bootstrap(stream_dir)                      # fresh / generation bump → full re-sync
        consumed = 0

    last_shard, last_offset, seen = "", 0, 0
    for idx, shard, line_no, env in _iter_events(stream_dir):
        seen = idx + 1
        last_shard, last_offset = shard, line_no
        if idx < consumed:
            continue
        _apply(tasks, env)

    digest = _render(tasks, manifest)
    with open(digest_path, "w", encoding="utf-8") as f:
        f.write(digest)
    _save_state(state_path, {
        "spec_version": manifest.get("spec_version"),
        "generation": gen,
        "cursor": {"shard": last_shard, "offset": last_offset},
        "events_consumed": seen,
        "tasks": sorted(tasks.values(), key=lambda t: t["uuid"]),
    })
    return digest_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tasktrail reference consumer: plain digest.md")
    ap.add_argument("--stream", required=True, help="path to a tasktrail/ bundle")
    ap.add_argument("--out", required=True, help="target dir for digest.md + state file")
    args = ap.parse_args(argv)
    path = sync(args.stream, args.out)
    sys.stdout.write("wrote %s\n" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
