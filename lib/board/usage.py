# usage.py
"""Transcript scanner + usage-ledger upsert + per-task aggregation (WS1).

Reads only the user's OWN local Claude Code session transcripts
(~/.claude/projects/<enc-cwd>/<sid>.jsonl and their <sid>/subagents/agent-*.jsonl)
and rolls per-model token counts + a DERIVED $ (there is no per-message costUSD in
a transcript — see lib/pricing.py) into the `session_usage` / `prompts` tables the
store owns. Everything here is defensive: a malformed line is skipped, a scan error
degrades to "stale numbers", and nothing raised out of here may break a hook.

Attribution is by transcript, one `session_usage` row per session id. A session
shared by several tasks is attributed as a whole to the task whose activity spans
contain its latest activity (falling back to the most-recently-updated task) — a
v1 approximation of true per-message attribution, surfaced in the derived note.

`PROJECTS_ROOT` / `WORKERS_REGISTRY` are module globals so the engine can point
them at its own resolved paths (and tests at a temp dir) before calling in.
"""
import json
import math
import os
import re
import sys
from datetime import datetime

import config
import phases
import pricing

# Resolved by the caller (task-station.py sets these to its own frozen paths;
# tests repoint them at a temp projects dir / workers.json).
PROJECTS_ROOT = os.path.join(
    os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "projects")
WORKERS_REGISTRY = None   # abs path to workers.json; None → no worker sessions

# The per-model roll-up keys stored in the models/sidechain JSON blobs (the shape
# WS3/WS4 read). `cost_usd` is null for an unknown (unpriced) model.
_MODEL_KEYS = ("in", "out", "cache_read", "cache_w5m", "cache_w1h", "web",
               "cost_usd", "msgs")

# Window lengths (seconds) for the ledger's anchor-aligned bucket populations that
# feed the HUD's μ/μ+σ window coloring. Kept here (not imported from hud) so usage
# never imports hud — the HUD imports usage, not the reverse. Mirror hud's
# _FIVE_HOUR_SECS / _WEEK_SECS.
FIVE_HOUR_SECS = 18000
WEEK_SECS = 604800


def _iso_to_ts(s):
    """Epoch seconds for a transcript ISO-8601 timestamp, or None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- path helpers ---

def find_session_path(sid):
    """Locate `<sid>.jsonl` across every project bucket (a session's bucket is its
    LAUNCH cwd, which for a delegated worker differs from the hub's). None if absent."""
    if not sid:
        return None
    try:
        buckets = os.listdir(PROJECTS_ROOT)
    except OSError:
        return None
    for b in buckets:
        p = os.path.join(PROJECTS_ROOT, b, sid + ".jsonl")
        if os.path.exists(p):
            return p
    return None


def _subagent_files(session_path, sid):
    """The `<sid>/subagents/agent-*.jsonl` files beside a session transcript."""
    subdir = os.path.join(os.path.dirname(session_path), sid, "subagents")
    try:
        names = os.listdir(subdir)
    except OSError:
        return []
    return [os.path.join(subdir, n) for n in sorted(names)
            if n.startswith("agent-") and n.endswith(".jsonl")]


def _load_registry():
    if not WORKERS_REGISTRY:
        return {}
    try:
        with open(WORKERS_REGISTRY) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------- model roll-up ------
#
# Working buckets carry an extra `unpriced` flag so a partial sum + an unknown
# model round-trip through the JSON blobs (where cost_usd is stored as null).

def _new_model():
    return {"in": 0, "out": 0, "cache_read": 0, "cache_w5m": 0, "cache_w1h": 0,
            "web": 0, "cost_usd": 0.0, "msgs": 0, "unpriced": False}


def _add_bucket(dst, stored):
    """Merge a STORED model bucket (as read back from the JSON blob) into a working
    accumulator `dst`. A stored cost_usd of null marks the model unpriced."""
    for model, d in (stored or {}).items():
        w = dst.setdefault(model, _new_model())
        for k in ("in", "out", "cache_read", "cache_w5m", "cache_w1h", "web", "msgs"):
            w[k] += d.get(k) or 0
        c = d.get("cost_usd", 0.0)
        if c is None:
            w["unpriced"] = True
        else:
            w["cost_usd"] += c or 0.0


def _accumulate(dst, model, usage, cost):
    """Fold one assistant message's usage (+ its derived cost) into `dst[model]`."""
    w = dst.setdefault(model or "?", _new_model())
    w["in"] += usage.get("input_tokens") or 0
    w["out"] += usage.get("output_tokens") or 0
    w["cache_read"] += usage.get("cache_read_input_tokens") or 0
    w1h, w5m = pricing._cache_write_split(usage)
    w["cache_w1h"] += w1h
    w["cache_w5m"] += w5m
    w["web"] += (usage.get("server_tool_use") or {}).get("web_search_requests") or 0
    w["msgs"] += 1
    if cost is None:
        w["unpriced"] = True
    else:
        w["cost_usd"] += cost


def _dump_bucket(working):
    """Working accumulator → the stored JSON shape (cost_usd null when unpriced)."""
    out = {}
    for model, w in working.items():
        d = {k: (w.get(k) or 0) for k in _MODEL_KEYS if k != "cost_usd"}
        d["cost_usd"] = None if w.get("unpriced") else round(w.get("cost_usd") or 0.0, 6)
        out[model] = d
    return out


# --------------------------------------------------------- phase roll-up ------
#
# The WS3 work-phase split. One working bucket per phase carries output tokens +
# derived cost + message count (mirroring the model bucket's `unpriced` flag). The
# stored blob adds a `__v` version stamp so a classifier change triggers a rescan.

def _new_phase():
    return {"out": 0, "cost_usd": 0.0, "msgs": 0, "unpriced": False, "names": {}}


def _add_phase_bucket(dst, stored):
    """Merge a STORED phase blob into a working accumulator, skipping the `__v`
    stamp (and any non-phase key). A stored cost_usd of null marks it unpriced. The
    `other` bucket's `names` drill-down counter (tool/command → count) is merged too."""
    for name, d in (stored or {}).items():
        if name not in phases.PHASES:
            continue
        w = dst.setdefault(name, _new_phase())
        w["out"] += d.get("out") or 0
        w["msgs"] += d.get("msgs") or 0
        c = d.get("cost_usd", 0.0)
        if c is None:
            w["unpriced"] = True
        else:
            w["cost_usd"] += c or 0.0
        for sig, n in (d.get("names") or {}).items():
            w["names"][sig] = w["names"].get(sig, 0) + (n or 0)


def _accumulate_phase(dst, phase, out_tokens, cost, name=None):
    """Fold one classified assistant message's output tokens + derived cost into
    `dst[phase]`. When `name` is given (the phase is 'other' and a tool/command signal
    is known), tally it under that bucket's `names` counter for the board drill-down."""
    w = dst.setdefault(phase, _new_phase())
    w["out"] += out_tokens or 0
    w["msgs"] += 1
    if cost is None:
        w["unpriced"] = True
    else:
        w["cost_usd"] += cost
    if name:
        w["names"][name] = w["names"].get(name, 0) + 1


def _dump_phases(working):
    """Working phase accumulator → the stored JSON shape (cost_usd null when
    unpriced), stamped with the current PHASES_VERSION under `__v`. The `other` bucket
    carries a `names` map (tool/command → count) when contributors were captured, for
    the board's 'what fell into other' drill-down."""
    out = {"__v": phases.PHASES_VERSION}
    for name, w in working.items():
        blob = {"out": w.get("out") or 0, "msgs": w.get("msgs") or 0,
                "cost_usd": None if w.get("unpriced")
                else round(w.get("cost_usd") or 0.0, 6)}
        if w.get("names"):
            blob["names"] = dict(w["names"])
        out[name] = blob
    return out


def _phases_stale(row):
    """True when a stored row's phase blob was written by a different PHASES_VERSION
    (or predates WS3 phases) — a signal to fully rescan so the split recomputes."""
    return (row.get("phases") or {}).get("__v") != phases.PHASES_VERSION


# ----------------------------------------------------------- line handling ----

def _classify_user_line(obj):
    """(kind, text) for a user transcript line worth storing as a prompt, or None
    to skip it. Skips sidechain lines and tool_result arrays; slash commands →
    'command', compaction summaries → 'compact', everything else → 'prompt'."""
    if obj.get("isSidechain"):
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, list):
        # A tool_result arrives as a user line — not a prompt.
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                return None
        text = " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text").strip()
    elif isinstance(content, str):
        text = content.strip()
    else:
        return None
    if not text:
        return None
    if obj.get("isCompactSummary"):
        return ("compact", text)
    m = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if m:
        name = m.group(1).strip()
        am = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        args = am.group(1).strip() if am else ""
        return ("command", (name + (" " + args if args else "")).strip())
    return ("prompt", text)


def _scan_lines(chunk, dst, prompts, capture_prompts, ts_acc, phase_acc):
    """Process the complete-line prefix of a byte `chunk` (a parent transcript
    slice). Accumulates assistant usage into `dst`, the per-message work-phase split
    into `phase_acc`, collects prompt tuples, and tracks first/last ts in the
    two-element list `ts_acc`. Returns (bytes_consumed, entrypoint, cwd) —
    bytes_consumed stops at the last '\\n' so a truncated trailing line is left for
    the next flush."""
    parts = chunk.split(b"\n")
    trailing = parts[-1]                 # partial line (or b"" when chunk ends in \n)
    consumed = len(chunk) - len(trailing)
    entrypoint = cwd = None
    for raw in parts[:-1]:
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("entrypoint"):
            entrypoint = obj.get("entrypoint")
        if obj.get("cwd"):
            cwd = obj.get("cwd")
        line_ts = _iso_to_ts(obj.get("timestamp"))
        typ = obj.get("type")
        if typ == "assistant":
            if obj.get("isSidechain"):
                continue                 # counted from the subagent files instead
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage") or {}
            model = msg.get("model")
            cost = pricing.message_cost(model, usage, line_ts)
            _accumulate(dst, model, usage, cost)
            phase, signal = phases.classify_message_named(obj)
            _accumulate_phase(phase_acc, phase,
                              usage.get("output_tokens") or 0, cost, signal)
            _track_ts(ts_acc, line_ts)
        elif typ == "user":
            kc = _classify_user_line(obj)
            if kc and capture_prompts:
                prompts.append((obj.get("uuid"), line_ts, kc[0], kc[1]))
            if kc:
                _track_ts(ts_acc, line_ts)
    return consumed, entrypoint, cwd


def _track_ts(ts_acc, ts):
    if ts is None:
        return
    if ts_acc[0] is None or ts < ts_acc[0]:
        ts_acc[0] = ts
    if ts_acc[1] is None or ts > ts_acc[1]:
        ts_acc[1] = ts


def _scan_subagents(session_path, sid):
    """Full (re)scan of the subagent transcripts into a fresh sidechain bucket.
    Subagent files are small + finite, so they're recomputed each pass rather than
    byte-offset tracked (the single scanned_size bookmark is the parent's)."""
    dst = {}
    for fp in _subagent_files(session_path, sid):
        try:
            with open(fp, "rb") as f:
                data = f.read()
        except OSError:
            continue
        for raw in data.split(b"\n"):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage") or {}
            model = msg.get("model")
            _accumulate(dst, model, usage,
                        pricing.message_cost(model, usage, _iso_to_ts(obj.get("timestamp"))))
    return dst


# ------------------------------------------------------------- attribution ----

def attribute_message(ts, tasks):
    """The v1 shared-session rule: the first candidate task whose activity spans
    contain `ts`, else the most-recently-updated candidate. None for no candidates."""
    if not tasks:
        return None
    if ts is not None:
        for t in tasks:
            for span in t.get("spans") or []:
                try:
                    if float(span[0]) <= ts <= float(span[1]):
                        return t
                except (TypeError, ValueError, IndexError):
                    continue
    return max(tasks, key=lambda t: t.get("updated_ts") or 0)


def _session_candidates(store, session_id, task_id):
    """The tasks that reference `session_id` (plus the requesting task as a fallback)
    — the candidate pool both the session-owner and per-prompt attribution draw from."""
    try:
        candidates = [t for t in store.all_tasks()
                      if session_id in (t.get("sessions") or [])]
    except Exception:
        candidates = []
    if not candidates and task_id:
        try:
            t = store.load_task(task_id)
        except Exception:
            t = None
        if t:
            candidates = [t]
    return candidates


def _owner_id(store, session_id, task_id, ts, candidates=None):
    """Deterministic owning-task id for a session: attribute_message over the tasks
    that reference the session (plus the requesting task), falling back to task_id."""
    if candidates is None:
        candidates = _session_candidates(store, session_id, task_id)
    owner = attribute_message(ts, candidates)
    return owner["id"] if owner else task_id


def _prompt_owner_id(ts, candidates, fallback):
    """PER-PROMPT task attribution: the task whose activity span contains the prompt's
    ts wins; a prompt outside every span keeps the session-owner `fallback`. A shared
    session (a hub that attaches to several tasks over its life) used to file its WHOLE
    trail under the single session owner — dropping those prompts from every other
    task's history; span-matching each prompt individually fixes that."""
    if ts is None or len(candidates) < 2:
        return fallback
    for t in candidates:
        for span in t.get("spans") or []:
            try:
                if float(span[0]) <= ts <= float(span[1]):
                    return t["id"]
            except (TypeError, ValueError, IndexError):
                continue
    return fallback


def _role_from_entrypoint(entrypoint):
    if entrypoint == "sdk-cli":
        return "worker"
    if entrypoint == "cli":
        return "hub"
    return "unknown"


# --------------------------------------------------------------- scan / API ---

def scan_session(store, session_id, task_id, role=None, label=None):
    """Incrementally scan one session transcript (+ its subagent files) and upsert
    its `session_usage` row and `prompts`. Returns the (stored-shape) session row.

    Incremental: skips when size+mtime are unchanged; reads from the stored byte
    offset otherwise; a shrunk file (rotation) triggers a full rescan; a truncated
    trailing line is left for the next flush. The parent's per-model roll-up is
    ACCUMULATED across scans; the subagent (sidechain) bucket is recomputed fresh."""
    row = store.get_session_usage(session_id) or {}
    path = find_session_path(session_id) or row.get("path")
    if not path or not os.path.exists(path):
        return row or _stub_row(session_id, task_id, role, label, path)
    try:
        st = os.stat(path)
    except OSError:
        return row or _stub_row(session_id, task_id, role, label, path)
    size, mtime = st.st_size, st.st_mtime

    if row and size == int(row.get("scanned_size") or 0) \
            and mtime == float(row.get("scanned_mtime") or 0) \
            and not _phases_stale(row):
        return row                       # unchanged + phases current → cheap no-op

    # A shrunk file (rotation), a first scan, OR a classifier-version bump forces a
    # full rescan — the phase split can only be recomputed from the whole transcript.
    full = size < int(row.get("scanned_size") or 0) or not row or _phases_stale(row)
    offset = 0 if full else int(row.get("scanned_size") or 0)
    parent = {}
    parent_phases = {}
    if not full:
        _add_bucket(parent, row.get("models"))
        _add_phase_bucket(parent_phases, row.get("phases"))
    ts_acc = [None, None] if full else [row.get("first_ts"), row.get("last_ts")]
    entrypoint = None if full else row.get("entrypoint")
    cwd = None if full else row.get("cwd")

    prompts = []
    capture_prompts = config.usage_prompts_enabled()
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return row or _stub_row(session_id, task_id, role, label, path)
    consumed, ep, cw = _scan_lines(chunk, parent, prompts, capture_prompts,
                                   ts_acc, parent_phases)
    if ep:
        entrypoint = ep
    if cw:
        cwd = cw
    new_size = offset + consumed

    sidechain = _scan_subagents(path, session_id)

    candidates = _session_candidates(store, session_id, task_id)
    owner_id = _owner_id(store, session_id, task_id, ts_acc[1] or ts_acc[0],
                         candidates=candidates)
    resolved_role = role or _role_from_entrypoint(entrypoint)
    new_row = {
        "session_id": session_id, "task_id": owner_id, "path": path, "cwd": cwd,
        "entrypoint": entrypoint, "role": resolved_role,
        "label": label if label is not None else row.get("label"),
        "scanned_size": new_size, "scanned_mtime": mtime,
        "first_ts": ts_acc[0], "last_ts": ts_acc[1],
        "models": _dump_bucket(parent), "sidechain": _dump_bucket(sidechain),
        "phases": _dump_phases(parent_phases),     # WS3 per-message work-phase split
    }
    store.upsert_session_usage(new_row)
    for uuid_, ts, kind, text in prompts:
        store.upsert_prompt({"uuid": uuid_, "session_id": session_id,
                             "task_id": _prompt_owner_id(ts, candidates, owner_id),
                             "ts": ts, "kind": kind, "text": text})
    return new_row


def _stub_row(session_id, task_id, role, label, path):
    return {"session_id": session_id, "task_id": task_id, "path": path, "cwd": None,
            "entrypoint": None, "role": role or "unknown", "label": label,
            "scanned_size": 0, "scanned_mtime": 0, "first_ts": None, "last_ts": None,
            "models": {}, "sidechain": {}, "phases": {}}


def _worker_sessions(task):
    """(session_id, label) for every delegated worker run recorded for this task in
    workers.json (keys `<seq>:<project>[:<label>]`)."""
    seq = task.get("seq")
    if seq is None:
        return []
    base = "%s:" % seq
    out = []
    for k, e in _load_registry().items():
        if k.startswith(base) and isinstance(e, dict) and e.get("session_id"):
            out.append((e["session_id"], e.get("label") or e.get("project")))
    return out


def refresh_task(store, task):
    """(Re)scan every session attached to `task` — its hub sessions AND every worker
    session recorded in workers.json. Best-effort per session; a scan error degrades
    to stale numbers and never propagates (the hook path must not crash)."""
    if not config.usage_tracking_enabled():
        return
    seen = set()
    for sid in (task.get("sessions") or []):
        if not sid or sid in seen or sid.startswith("__"):
            continue
        seen.add(sid)
        try:
            scan_session(store, sid, task["id"])
        except Exception as e:
            sys.stderr.write("usage: scan of %s failed: %s\n" % (sid[:8], e))
    for sid, label in _worker_sessions(task):
        if not sid or sid in seen:
            continue
        seen.add(sid)
        try:
            scan_session(store, sid, task["id"], role="worker", label=label)
        except Exception as e:
            sys.stderr.write("usage: scan of worker %s failed: %s\n" % (sid[:8], e))


# ------------------------------------------------------- all-session scan -----
#
# The Week/Total ledger rows must reflect EVERY session, not only those scanned for
# an attached task — else they understate. scan_all walks every parent transcript
# under PROJECTS_ROOT and upserts it (byte-offset incremental, same as scan_session),
# attributing it to its owning task when one references it and NULL otherwise.

def scan_all(store):
    """Incrementally scan EVERY session transcript under PROJECTS_ROOT into the
    ledger. A session referenced by a task keeps that attribution (via scan_session's
    owner rule); one that belongs to no task lands with task_id NULL. Subagent
    transcripts (nested `<sid>/subagents/`) are skipped — they're counted through
    their parent's scan. Best-effort per session; a scan error degrades to stale
    numbers. Returns the count of transcripts visited; a no-op (0) when tracking off.

    Run AFTER refresh_task in the flush path: an unchanged file short-circuits
    scan_session before re-attribution, so whoever fully scans a shared/worker
    session first fixes its task_id — refresh_task must win that race."""
    if not config.usage_tracking_enabled():
        return 0
    seen = set()
    n = 0
    try:
        buckets = os.listdir(PROJECTS_ROOT)
    except OSError:
        return 0
    for b in buckets:
        bucket = os.path.join(PROJECTS_ROOT, b)
        try:
            names = os.listdir(bucket)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".jsonl"):
                continue                          # subagent dirs aren't .jsonl files
            sid = name[:-6]                       # strip ".jsonl"
            if not sid or sid in seen:
                continue
            seen.add(sid)
            try:
                scan_session(store, sid, None)
                n += 1
            except Exception as e:
                sys.stderr.write("usage scan-all: %s failed: %s\n" % (sid[:8], e))
    return n


# ----------------------------------------------------- costbar one-time import ---
#
# History whose transcripts are gone (rotated/deleted) can't be scanned. costbar
# kept a rollup cache — {sid: {cost, output_tok, turns, date}} — that we import ONCE
# so Total/Week reflect the true lifetime. Imported rows carry cost + out tokens only
# (no model split), source='costbar-import', task_id NULL, and are inserted ONLY for
# sids not already in the ledger AND with no transcript on disk (so a live session
# always wins). Idempotent: a re-run finds every sid already in the ledger.

def _costbar_cache_path():
    cfg = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"))
    return os.path.join(cfg, "cache", "session_totals.json")


def _parse_costbar_date(s):
    """Epoch seconds for costbar's cache `date` (ISO or Y-M-D-ish), or None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(s), fmt).timestamp()
        except ValueError:
            continue
    return None


def import_costbar(store, path=None):
    """One-time import of costbar's session_totals.json rollup cache. Returns a
    summary dict {imported, skipped_in_ledger, skipped_has_transcript, path[, error]}.
    Defensive: a missing/garbage cache returns an error field, never raises."""
    path = path or _costbar_cache_path()
    result = {"imported": 0, "skipped_in_ledger": 0,
              "skipped_has_transcript": 0, "path": path}
    try:
        with open(path) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        result["error"] = "no readable costbar cache at %s" % path
        return result
    if not isinstance(cache, dict):
        result["error"] = "unexpected costbar cache shape at %s" % path
        return result
    for sid, rec in cache.items():
        if not sid or not isinstance(rec, dict):
            continue
        if store.get_session_usage(sid):
            result["skipped_in_ledger"] += 1
            continue
        if find_session_path(sid):
            result["skipped_has_transcript"] += 1     # live transcript → scan wins
            continue
        try:
            cost = float(rec.get("cost") or rec.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        try:
            out = int(rec.get("output_tok") or rec.get("out") or 0)
        except (TypeError, ValueError):
            out = 0
        try:
            turns = int(rec.get("turns") or 0)
        except (TypeError, ValueError):
            turns = 0
        ts = _parse_costbar_date(rec.get("date"))
        # A single synthetic bucket carrying cost + out only — no model split. It
        # rolls into ledger_totals via _session_cost/_session_out like any model.
        models = {"costbar-import": {
            "in": 0, "out": out, "cache_read": 0, "cache_w5m": 0, "cache_w1h": 0,
            "web": 0, "msgs": turns, "cost_usd": round(cost, 6)}}
        store.upsert_session_usage({
            "session_id": sid, "task_id": None, "path": None, "cwd": None,
            "entrypoint": None, "role": "costbar-import", "label": None,
            "scanned_size": 0, "scanned_mtime": 0, "first_ts": ts, "last_ts": ts,
            "models": models, "sidechain": {}, "phases": {},
            "source": "costbar-import"})
        result["imported"] += 1
    return result


# ------------------------------------------------------------- aggregation ----

def _session_summary(r):
    """A compact per-session dict for the CLI table: short sid, role/label, merged
    (parent + subagent) token totals, and derived $ (None when any model unpriced)."""
    w = {}
    _add_bucket(w, r.get("models"))
    _add_bucket(w, r.get("sidechain"))
    unpriced = any(m["unpriced"] for m in w.values())
    return {
        "session_id": r.get("session_id"),
        "sid": (r.get("session_id") or "")[:8],
        "role": r.get("role") or "unknown",
        "label": r.get("label"),
        "in": sum(m["in"] for m in w.values()),
        "out": sum(m["out"] for m in w.values()),
        "cache_read": sum(m["cache_read"] for m in w.values()),
        "cost_usd": None if unpriced else round(sum(m["cost_usd"] for m in w.values()), 6),
        "models": {m: pricing.model_family(m) for m in w},
        "first_ts": r.get("first_ts"), "last_ts": r.get("last_ts"),
    }


def _derived_note(any_unpriced, unknown_models):
    """The 'how these numbers are derived' footnote — the same text the board's
    derivation panel shows. Adapts to the billing-mode framing."""
    parts = [
        "Derived from your local transcript token counts × published $/MTok rates "
        "(verified 2026-07-04); the cache-write TTL split is estimated, so the "
        "figure can differ from the reported $ by ~1%.",
    ]
    if config.usage_billing_mode() == "subscription":
        parts.append("Shown as API-equivalent value — a flat-rate seat is not billed "
                     "per token; overage past your limit bills at these rates.")
    else:
        parts.append("In API/metered billing this derived $ is the marginal bill.")
    parts.append("A session shared by multiple tasks is attributed as a whole to the "
                 "task whose spans contain its latest activity (v1 approximation).")
    parts.append("Work phases are heuristic (tool-mix per message + attribution "
                 "skill, cost-weighted like the model mix).")
    if any_unpriced:
        parts.append("Unknown model(s) — %s — are shown as unpriced (—) and excluded "
                     "from the $ total; their share falls back to output-token share."
                     % ", ".join(sorted(unknown_models)))
    return " ".join(parts)


def task_usage(store, task):
    """Aggregate the usage ledger for one task: per-model token/$ roll-up (with a %
    share), the per-session breakdown, totals, and the derivation note. `pct` is a
    model's share of derived cost, falling back to its share of output tokens when
    any model is unpriced. Reads the persisted ledger only — cheap, no transcript IO."""
    rows = store.session_usage_for_task(task["id"])
    agg = {}
    phase_agg = {}
    sessions = []
    for r in rows:
        _add_bucket(agg, r.get("models"))
        _add_bucket(agg, r.get("sidechain"))
        _add_phase_bucket(phase_agg, r.get("phases"))
        sessions.append(_session_summary(r))

    any_unpriced = any(w["unpriced"] for w in agg.values())
    unknown_models = {m for m, w in agg.items() if w["unpriced"]}
    total_in = sum(w["in"] for w in agg.values())
    total_out = sum(w["out"] for w in agg.values())
    total_cost = round(sum(w["cost_usd"] for w in agg.values() if not w["unpriced"]), 6)

    models = {}
    for m, w in agg.items():
        d = {k: w[k] for k in ("in", "out", "cache_read", "cache_w5m", "cache_w1h",
                               "web", "msgs")}
        d["cost_usd"] = None if w["unpriced"] else round(w["cost_usd"], 6)
        models[m] = d
    for m, d in models.items():
        if not any_unpriced and total_cost > 0:
            d["pct"] = (d["cost_usd"] or 0.0) / total_cost
        else:
            d["pct"] = (d["out"] / total_out) if total_out else 0.0

    # Work-phase split — mirrors the model-mix weighting so the two panels compare:
    # pct is a phase's share of derived cost, falling back to its share of output
    # tokens when any phase carries an unpriced (unknown-model) message.
    phases_any_unpriced = any(w["unpriced"] for w in phase_agg.values())
    total_phase_out = sum(w["out"] for w in phase_agg.values())
    total_phase_cost = round(
        sum(w["cost_usd"] for w in phase_agg.values() if not w["unpriced"]), 6)
    phases_out = {}
    for name, w in phase_agg.items():
        d = {"out": w["out"], "msgs": w["msgs"],
             "cost_usd": None if w["unpriced"] else round(w["cost_usd"], 6)}
        if w.get("names"):
            d["names"] = dict(w["names"])
        if not phases_any_unpriced and total_phase_cost > 0:
            d["pct"] = (d["cost_usd"] or 0.0) / total_phase_cost
        else:
            d["pct"] = (d["out"] / total_phase_out) if total_phase_out else 0.0
        phases_out[name] = d

    cost = task.get("cost") or {}
    try:
        reported = float(cost.get("total_usd") or 0.0)
    except (TypeError, ValueError):
        reported = 0.0
    # Crashed/timed-out spend, tracked separately from the real-work reported total
    # (RESOLVED #4): neither skipped nor folded into `reported_cost_usd`.
    try:
        wasted = float(cost.get("wasted_usd") or 0.0)
    except (TypeError, ValueError):
        wasted = 0.0

    return {
        "models": models,
        "phases": phases_out,
        "sessions": sessions,
        "total_cost_usd": total_cost,
        "total_in": total_in,
        "total_out": total_out,
        "reported_cost_usd": reported,
        "wasted_cost_usd": wasted,
        "any_unpriced": any_unpriced,
        "derived_note": _derived_note(any_unpriced, unknown_models),
    }


def _session_cost(r):
    """Merged (parent + subagent) derived $ for ONE ledger row, or None when any of
    its models are unpriced (unknown) — mirrors _session_summary's cost rule."""
    w = {}
    _add_bucket(w, r.get("models"))
    _add_bucket(w, r.get("sidechain"))
    if any(m["unpriced"] for m in w.values()):
        return None
    return round(sum(m["cost_usd"] for m in w.values()), 6)


def _session_out(r):
    """Merged (parent + subagent) output-token total for ONE ledger row."""
    w = {}
    _add_bucket(w, r.get("models"))
    _add_bucket(w, r.get("sidechain"))
    return sum(m["out"] for m in w.values())


def ledger_totals(store, week_start_ts=None, now_ts=None, five_hour_start_ts=None):
    """Whole-ledger roll-up for the cost HUD's 5-hour / Week / Total rows: grand-total
    derived $ + output tokens across EVERY session, the same restricted to the current
    week (a session's latest activity within [week_start_ts, now_ts]) and to the current
    5-hour window ([five_hour_start_ts, now_ts]), the earliest activity ts, and the
    per-session derived-$ list that feeds μ/μ+σ threshold coloring. Reads the persisted
    `session_usage` ledger only — no transcript IO; priced entirely by lib/pricing.py
    (one rate table, no second scanner). A session with an unknown (unpriced) model
    contributes its tokens but not its $, and flips `any_unpriced`. The window figures
    are coarse — a session's whole cost counts if its LAST activity lands in the window,
    matching the Week row's existing approximation. Degrades to all-zero on no ledger.

    Also returns `five_hour_bucket_costs` / `week_bucket_costs`: the per-CLOSED-window
    summed derived-$ totals (one entry per historical 5-hour / weekly window that has
    fully elapsed), which feed the HUD's μ/μ+σ window coloring for the 5-hour and Week
    rows. Each priced session's whole cost lands in the bucket of its last-activity
    timestamp; the current in-progress window (bucket 0) and any future bucket are
    EXCLUDED so only complete windows shape the distribution. Approximations: (a) past
    5-hour boundaries are projected as exact 5h multiples of the CURRENT anchor —
    Claude's real 5h window is rolling so historical boundaries are approximate, which
    is acceptable for a color heuristic (weekly boundaries are stable); (b) a session
    straddling a boundary is counted wholly in its last-activity bucket."""
    try:
        rows = store.all_session_usage() if store else []
    except Exception:
        rows = []
    grand_cost = 0.0
    grand_out = 0
    week_cost = 0.0
    week_out = 0
    five_hour_cost = 0.0
    five_hour_out = 0
    first_ts = None
    session_costs = []
    # Anchor-aligned closed-window buckets keyed by integer bucket index (negative =
    # a historical, fully-elapsed window; 0 = the current in-progress window). Only
    # negative buckets feed μ/σ coloring — bucket 0 is partial and excluded.
    five_hour_bins = {}
    week_bins = {}
    any_unpriced = False
    for r in rows:
        cost = _session_cost(r)
        out = _session_out(r)
        grand_out += out
        if cost is None:
            any_unpriced = True
        else:
            grand_cost += cost
            session_costs.append(cost)
        fts = r.get("first_ts")
        if fts is not None:
            first_ts = fts if first_ts is None else min(first_ts, fts)
        act = r.get("last_ts")
        if act is None:
            act = r.get("first_ts")
        in_now = act is not None and (now_ts is None or act <= now_ts)
        if week_start_ts is not None and in_now and act >= week_start_ts:
            week_out += out
            if cost is not None:
                week_cost += cost
        if five_hour_start_ts is not None and in_now and act >= five_hour_start_ts:
            five_hour_out += out
            if cost is not None:
                five_hour_cost += cost
        # Historical closed-window populations for μ/σ coloring: attribute each priced
        # session's whole cost to the bucket of its last activity, keeping only fully
        # elapsed (index < 0) buckets — bucket 0 is the current partial window.
        if cost is not None and act is not None:
            if five_hour_start_ts is not None:
                b = math.floor((act - five_hour_start_ts) / FIVE_HOUR_SECS)
                if b < 0:
                    five_hour_bins[b] = five_hour_bins.get(b, 0.0) + cost
            if week_start_ts is not None:
                b = math.floor((act - week_start_ts) / WEEK_SECS)
                if b < 0:
                    week_bins[b] = week_bins.get(b, 0.0) + cost
    return {
        "grand_cost": round(grand_cost, 6),
        "grand_out": grand_out,
        "week_cost": round(week_cost, 6),
        "week_out": week_out,
        "five_hour_cost": round(five_hour_cost, 6),
        "five_hour_out": five_hour_out,
        "first_ts": first_ts,
        "session_costs": session_costs,
        "five_hour_bucket_costs": list(five_hour_bins.values()),
        "week_bucket_costs": list(week_bins.values()),
        "any_unpriced": any_unpriced,
    }


def task_prompts(store, task, include_compact=False):
    """The WS6 tasks-by-prompt view: the chronological, session-attributed trail of
    the exact user prompts (and slash commands) that drove one task — oldest first.

    Each row is {uuid, ts, session_id, sid, role, label, kind, text}. `role`/`label`
    are joined from the `session_usage` ledger (hub vs each delegated worker); a
    session with no ledger row degrades to role 'unknown'. `compact` (compaction-
    summary) rows are omitted unless `include_compact`; empty-text rows are always
    dropped. Returns [] when usage tracking is off. Reads the persisted `prompts` +
    `session_usage` tables only — cheap, no transcript IO."""
    if not config.usage_tracking_enabled():
        return []
    roles = {}
    for r in store.session_usage_for_task(task["id"]):
        sid = r.get("session_id")
        if sid:
            roles[sid] = (r.get("role") or "unknown", r.get("label"))
    out = []
    for r in store.prompts_for_task(task["id"]):
        kind = r.get("kind") or "prompt"
        if kind == "compact" and not include_compact:
            continue
        text = (r.get("text") or "").strip()
        if not text:
            continue
        # Harness-injected user-role lines (local-command echoes, background-task
        # notifications, hook reminders) are not things a human typed — keep them
        # out of the trail unless the caller asked for everything.
        if not include_compact and text.startswith(
                ("<local-command-", "<task-notification>", "<system-reminder>")):
            continue
        sid = r.get("session_id") or ""
        role, label = roles.get(sid, ("unknown", None))
        out.append({
            "uuid": r.get("uuid"),
            "ts": r.get("ts"),
            "session_id": sid,
            "sid": sid[:8],
            "role": role,
            "label": label,
            "kind": kind,
            "text": text,
        })
    # prompts_for_task already ORDER BY ts, but re-sort defensively (NULL ts last)
    # so the trail is deterministic even if a row lacks a timestamp.
    out.sort(key=lambda p: (p["ts"] is None, p["ts"] or 0))
    return out
