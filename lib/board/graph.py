"""Board graph seam: WS4 usage view-model, canonical relations, WS-D semantic edges, F1 foreign rendering, and the board view-model builders."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
from board.memos import *
from board.sessions import *
from board.render import *
import os
import re

import decisions as _dec
import knowledge as _knowledge
import paths
import steps as _steps

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_family_mix", "_merge_row_models", "_phase_weight", "_phase_segments",
    "_usage_rate_rows", "_board_usage",
    "_board_prompt_rows", "_board_prompt_view", "_board_prompts",
    "_board_prompts_all",
    "_cost_band_thresholds", "_session_usage_summary",
    "_aggregate_usage_summary", "_board_hub_cards",
    "related_edges", "_rel_kind_rank", "canonical_relations",
    "_task_signals", "semantic_edges", "_task_cited_notes",
    "build_board_graph", "parse_pr_number", "shared_signal_groups",
    "build_render_graph",
    "_worker_name", "_session_block_lines", "_related_line",
    "_session_resume_command", "_hub_sessions",
    "_board_session_counts", "_board_related", "_board_view_model",
    "_interbrain_on", "_knowledge_plane_on", "board_notes",
    "_foreign_view_model", "foreign_view_models",
]


# --------------------------------------------------- WS4 board usage view-model ---
#
# The board's per-task usage panels (model-mix bar, per-session table, work-mix phase
# bar, derivation footnote, prompts preview) read a flattened view-model built here so
# tools/render_board.py stays a pure renderer. Everything is defensive: usage tracking
# off, an empty ledger, or ANY error degrades the block to absent — a board must render.

def _family_mix(bucket_by_model):
    """[{'family','pct','unpriced'}] merged by model family from a
    {model_id: {'out', 'cost_usd'(None=unpriced), ...}} dict, sorted by share desc.
    Share is by derived-cost when every model is priced, else by output-token count
    (so an unknown-model task still shows a proportional bar)."""
    import pricing
    fams = {}
    total_cost = 0.0
    total_out = 0
    any_unpriced = False
    for mid, d in (bucket_by_model or {}).items():
        fam = pricing.model_family(mid)
        f = fams.setdefault(fam, {"family": fam, "cost": 0.0, "out": 0, "unpriced": False})
        c = d.get("cost_usd")
        if c is None:
            f["unpriced"] = True
            any_unpriced = True
        else:
            f["cost"] += c or 0.0
            total_cost += c or 0.0
        o = d.get("out") or 0
        f["out"] += o
        total_out += o
    segs = []
    for f in fams.values():
        if not any_unpriced and total_cost > 0:
            pct = f["cost"] / total_cost
        elif total_out:
            pct = f["out"] / total_out
        else:
            pct = 0.0
        segs.append({"family": f["family"], "pct": pct, "unpriced": f["unpriced"]})
    segs.sort(key=lambda s: (-s["pct"], s["family"]))
    return segs


def _merge_row_models(row):
    """A session_usage row's parent `models` + subagent `sidechain` blobs merged into
    one {model_id: {'in','out','cache_read','cost_usd'(None when any part unpriced)}}
    dict — the input to _family_mix for that session's own model mini-mix."""
    agg = {}
    for blob in (row.get("models"), row.get("sidechain")):
        for mid, d in (blob or {}).items():
            w = agg.setdefault(mid, {"in": 0, "out": 0, "cache_read": 0,
                                     "cost_usd": 0.0, "_unpriced": False})
            w["in"] += d.get("in") or 0
            w["out"] += d.get("out") or 0
            w["cache_read"] += d.get("cache_read") or 0
            c = d.get("cost_usd")
            if c is None:
                w["_unpriced"] = True
            else:
                w["cost_usd"] += c or 0.0
    for w in agg.values():
        if w.pop("_unpriced"):
            w["cost_usd"] = None
    return agg


def _phase_weight(v):
    """A single phase blob value → a non-negative numeric weight for the work-mix bar.
    WS3 owns the exact shape (built in parallel); this reads a bare number or the first
    numeric among a handful of likely keys, so the bar lights up whatever WS3 stores."""
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return max(0.0, float(v))
    if isinstance(v, dict):
        for k in ("cost_usd", "weight", "out", "tokens", "msgs", "seconds", "pct"):
            x = v.get(k)
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                return max(0.0, float(x))
    return 0.0


def _phase_segments(rows):
    """WS3 phase blobs merged across the task's session rows → [{'label','pct'}] sorted
    by share desc. Empty (block omitted) when WS3 hasn't populated any phases yet.

    Only real phase names (`phases.PHASES`) are counted: the stored blob carries a
    `__v` version stamp (and could carry other non-phase keys), which must never leak
    out as a `__v` bar segment — same whitelist usage.py applies when aggregating."""
    import phases
    totals = {}
    names = {}                          # "other" drill-down: tool/command → count
    for r in rows:
        for label, v in (r.get("phases") or {}).items():
            if label not in phases.PHASES:
                continue
            totals[label] = totals.get(label, 0.0) + _phase_weight(v)
            if label == "other" and isinstance(v, dict):
                for sig, n in (v.get("names") or {}).items():
                    names[sig] = names.get(sig, 0) + (n or 0)
    grand = sum(totals.values())
    if grand <= 0:
        return []
    segs = [{"label": k, "pct": v / grand} for k, v in totals.items() if v > 0]
    segs.sort(key=lambda s: (-s["pct"], s["label"]))
    # attach the top 'other' contributors (desc by count) for the board drill-down.
    if names:
        top = sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        for s in segs:
            if s["label"] == "other":
                s["names"] = [{"name": n, "count": c} for n, c in top]
    return segs


def _usage_rate_rows(models):
    """The published $/MTok rate rows ACTUALLY used by this task — one per distinct
    priced family present (unknown/unpriced models carry no sheet → skipped), for the
    board's derivation table. Keyed off the real model ids so the family/version and
    date-dependent Sonnet pricing resolve exactly as the cost derivation did."""
    import pricing
    seen = set()
    out = []
    for mid in sorted(models):
        rate = pricing.rates_for(mid)
        if rate is None:
            continue
        fam = pricing.model_family(mid)
        if fam in seen:
            continue
        seen.add(fam)
        out.append({"family": fam, "model": mid, "in": rate["in"], "out": rate["out"],
                    "w5m": rate["w5m"], "w1h": rate["w1h"], "read": rate["read"]})
    return out


def _board_usage(task, live_sids=None):
    """Assemble the board usage view-model for one task: the task-level model mix, the
    per-session breakdown (per-session mini-mix, tokens, $ derived + $ reported from the
    delegate runs, live flag when WS5 supplies live_sids), token/$ totals with the
    reported cross-check, the phase (work-mix) segments, the rate rows used, and the
    derivation note. Returns None (block omitted) when usage tracking is off, the ledger
    is empty, or the store/data-shaping below hits a hiccup — never breaks a render. The
    two gate checks live OUTSIDE the try (WS7 debug lesson: a bare `except Exception`
    around the whole function once swallowed a real bug right alongside the expected
    off/empty cases, costing real debug time)."""
    import config as _cfg
    if not _cfg.usage_tracking_enabled():
        return None
    try:
        store = _backend()
        data = _usage_engine().task_usage(store, task)
    except Exception:
        return None
    models = data.get("models") or {}
    if not models:
        return None
    try:
        live = set(live_sids or [])
        rows = {r.get("session_id"): r for r in store.session_usage_for_task(task["id"])}
        # $ reported per session, summed from the delegate per-run records (WS2).
        reported_by_sid = {}
        for run in (task.get("runs") or []):
            sid = run.get("session_id")
            c = run.get("cost_usd")
            if sid and isinstance(c, (int, float)) and not isinstance(c, bool):
                reported_by_sid[sid] = reported_by_sid.get(sid, 0.0) + float(c)
        sessions = []
        for s in data.get("sessions") or []:
            sid_full = s.get("session_id")
            sessions.append({
                "sid": s.get("sid"),
                "role": s.get("role") or "unknown",
                "label": s.get("label"),
                "live": sid_full in live,
                "in": s.get("in") or 0,
                "out": s.get("out") or 0,
                "cache_read": s.get("cache_read") or 0,
                "cost_usd": s.get("cost_usd"),
                "reported": reported_by_sid.get(sid_full),
                "mix": _family_mix(_merge_row_models(rows.get(sid_full) or {})),
            })
        usage_vm = {
            "mix": _family_mix(models),
            "total_in": data.get("total_in", 0),
            "total_out": data.get("total_out", 0),
            "total_cost_usd": data.get("total_cost_usd", 0.0),
            "reported_cost_usd": data.get("reported_cost_usd", 0.0),
            "any_unpriced": bool(data.get("any_unpriced")),
            "derived_note": data.get("derived_note", ""),
            "rates": _usage_rate_rows(models),
        }
        return {"usage": usage_vm, "sessions": sessions,
                "phases": _phase_segments(list(rows.values()))}
    except Exception:
        return None


def _board_prompt_rows(task):
    """The task's enriched, chronological prompt trail (session-attributed) for the
    board's panels, or [] when usage tracking OR the `board_prompts` display gate is
    off (or nothing captured). Reuses usage.task_prompts so the board, the CLI, and
    the MCP tool share ONE attribution join. Display is local-only (the board never
    leaves the machine); prompt EXPORT stays opt-in elsewhere. Defensive — a store
    hiccup degrades to [] rather than breaking the board render."""
    try:
        import config as _cfg
        if not _cfg.board_prompts_enabled():
            return []
        return _usage_engine().task_prompts(_backend(), task)
    except Exception:
        return []


def _board_prompt_view(r):
    """One prompt row shaped for the board view-model (ts/kind/text + session
    attribution for the full-list panel)."""
    return {"ts": r.get("ts"), "kind": r.get("kind") or "prompt",
            "text": r.get("text") or "", "role": r.get("role"),
            "label": r.get("label"), "sid": r.get("sid")}


def _board_prompts(task, limit=5):
    """The last `limit` captured prompts (most-recent last) for the board's collapsed
    Recent-prompts preview. [] when the ledger / board_prompts gate is off."""
    return [_board_prompt_view(r) for r in _board_prompt_rows(task)[-limit:]]


def _board_prompts_all(task):
    """The FULL captured prompt trail (oldest first) for the board's expandable
    'All prompts' <details>. [] when the ledger / board_prompts gate is off."""
    return [_board_prompt_view(r) for r in _board_prompt_rows(task)]


def _cost_band_thresholds(costs):
    """(lo, hi) = (μ, μ+σ) of the priced per-session derived $ once ≥3 priced sessions
    exist, else the fixed (0.01, 0.05) fallback — mirrors hud.py `_session_thresholds`
    so the board colours cost figures with the SAME stdev bands as the HUD costbar."""
    import math
    priced = [float(c) for c in costs
              if isinstance(c, (int, float)) and not isinstance(c, bool)]
    if len(priced) >= 3:
        n = len(priced)
        mean = sum(priced) / n
        var = sum((x - mean) ** 2 for x in priced) / n
        sd = math.sqrt(var) if var > 0 else 0.0
        return [round(mean, 6), round(mean + sd, 6)]
    return [0.01, 0.05]


def _session_usage_summary(row):
    """A per-session usage summary from ONE ledger row: merged model mix, token totals,
    derived $ (None when any model unpriced), msg count, and the per-session work-mix.
    `{}`-safe: an absent row degrades to a zeroed summary."""
    mm = _merge_row_models(row) if row else {}
    costs = [d.get("cost_usd") for d in mm.values()]
    any_unpriced = any(c is None for c in costs)
    return {
        "in": sum(d.get("in") or 0 for d in mm.values()),
        "out": sum(d.get("out") or 0 for d in mm.values()),
        "cache_read": sum(d.get("cache_read") or 0 for d in mm.values()),
        "cost_usd": None if any_unpriced else round(sum(c or 0.0 for c in costs), 6),
        "any_unpriced": any_unpriced,
        "msgs": sum(d.get("msgs") or 0 for d in mm.values()),
        "mix": _family_mix(mm),
        "phases": _phase_segments([row]) if row else [],
    }


def _aggregate_usage_summary(rows):
    """Merge several ledger rows into ONE usage summary (the hub-plus-its-workers
    aggregate): merged model mix, summed tokens, derived $ (None when ANY part is
    unpriced), and the merged work-mix across all rows."""
    merged = {}
    for row in rows:
        for mid, d in (_merge_row_models(row) or {}).items():
            w = merged.setdefault(mid, {"in": 0, "out": 0, "cache_read": 0,
                                        "cost_usd": 0.0, "_unpriced": False, "msgs": 0})
            w["in"] += d.get("in") or 0
            w["out"] += d.get("out") or 0
            w["cache_read"] += d.get("cache_read") or 0
            w["msgs"] += d.get("msgs") or 0
            c = d.get("cost_usd")
            if c is None:
                w["_unpriced"] = True
            else:
                w["cost_usd"] += c or 0.0
    for w in merged.values():
        if w.pop("_unpriced"):
            w["cost_usd"] = None
    costs = [d.get("cost_usd") for d in merged.values()]
    any_unpriced = any(c is None for c in costs)
    return {
        "in": sum(d.get("in") or 0 for d in merged.values()),
        "out": sum(d.get("out") or 0 for d in merged.values()),
        "cache_read": sum(d.get("cache_read") or 0 for d in merged.values()),
        "cost_usd": None if any_unpriced else round(sum(c or 0.0 for c in costs), 6),
        "any_unpriced": any_unpriced,
        "mix": _family_mix(merged),
        "phases": _phase_segments(rows),
    }


def _board_hub_cards(task, live_sids=None):
    """The per-hub view-model for the board's restructured expanded panel (board
    B10–B14): one card PER HUB session, each baking in its own prompts, its cost +
    work-mix, and its nested worker sessions (with per-worker cost + work-mix + prompts
    folded in). The `main` hub (the `/todo <n>` resume target) and the pinned hub are
    flagged so the renderer can float + highlight them.

    Returns `{"hubs": [...], "cost_thresholds": [lo, hi]}`. `[]`/fallback thresholds
    when the ledger is off/empty. Any ledger session not attributed to a known hub or
    worker is gathered into a trailing 'unattributed' pseudo-hub so nothing is lost.
    Fully defensive — every failure degrades to an empty list, never a broken board."""
    empty = {"hubs": [], "cost_thresholds": [0.01, 0.05]}
    try:
        import config as _cfg
        usage_on = _cfg.usage_tracking_enabled()
    except Exception:
        usage_on = False
    live = set(live_sids or [])
    by_sid = {}
    prompts_by_sid = {}
    reported_by_sid = {}
    all_session_costs = []
    try:
        for run in (task.get("runs") or []):
            sid = run.get("session_id")
            c = run.get("cost_usd")
            if sid and isinstance(c, (int, float)) and not isinstance(c, bool):
                reported_by_sid[sid] = reported_by_sid.get(sid, 0.0) + float(c)
        if usage_on:
            store = _backend()
            for r in store.session_usage_for_task(task["id"]):
                sid = r.get("session_id")
                if sid:
                    by_sid[sid] = r
            # The board prompt trail shows ONLY human-typed prompts, EACH followed by
            # Claude's last-bullet reply — the SAME machinery the markdown/CLI views use
            # (_human_prompts_with_replies: filters to human + one transcript read per
            # session for the reply). No cap → the full human trail flows into the cards.
            for p in _human_prompts_with_replies(_usage_engine().task_prompts(store, task)):
                sid = p.get("session_id")
                if not sid:
                    continue
                prompts_by_sid.setdefault(sid, []).append({
                    "ts": p.get("ts"), "kind": p.get("kind") or "prompt",
                    "text": p.get("text") or "", "role": p.get("role"),
                    "label": p.get("label"), "sid": p.get("sid"),
                    "human": True, "reply": p.get("reply") or "",
                })
    except Exception:
        return empty
    # per-session derived $ for the stdev cost bands (priced sessions only).
    for r in by_sid.values():
        s = _session_usage_summary(r)
        if s["cost_usd"] is not None:
            all_session_costs.append(s["cost_usd"])
    thresholds = _cost_band_thresholds(all_session_costs)

    try:
        tree = session_tree(task)
    except Exception:
        tree = {"hubs": [], "orphan_workers": []}
    meta = task.get("session_meta") or {}
    covered = set()

    def _worker_card(wk):
        sid = wk.get("sid") or ""
        covered.add(sid)
        summ = _session_usage_summary(by_sid.get(sid))
        d, model = wk.get("dir"), wk.get("model")
        resume = bg_aware_resume(sid, d) if (d and sid) else (
            ("cd %s && claude" % d) if d else None)
        prompts = list(prompts_by_sid.get(sid, []))
        # 3-tier liveness: running (a real live process, pid in live_sids), resumable
        # (a resume target exists but nothing is running), else linked.
        state = "running" if sid in live else ("resumable" if resume else "linked")
        return {
            "sid8": sid[:8], "session_id": sid, "label": wk.get("label") or "worker",
            "model": model, "live": sid in live, "state": state,
            "age": rel_time(wk.get("ts")),
            "resume_command": resume, "prompts": prompts,
            "prompt_count": len(prompts), "reported": reported_by_sid.get(sid, 0.0),
            **summ,
        }

    def _hub_card(sid, pinned, main, live_flag, msgs):
        covered.add(sid)
        m = meta.get(sid) or {}
        own = _session_usage_summary(by_sid.get(sid))
        wk_dicts = []
        # workers nested under this hub in the session tree (by spawner).
        for h in tree.get("hubs", []):
            if h.get("sid") == sid:
                wk_dicts = h.get("workers") or []
                break
        workers = [_worker_card(w) for w in wk_dicts]
        agg = _aggregate_usage_summary(
            [by_sid[s] for s in ([sid] + [w.get("sid") for w in wk_dicts])
             if s in by_sid])
        own_prompts = list(prompts_by_sid.get(sid, []))
        first_human = next((p["text"] for p in own_prompts if p["human"]), None)
        oneliner = (first_human or "attached · %s" % _tildify(m.get("cwd")))[:100]
        # hub prompts = its OWN prompts + all its workers' prompts, chronological.
        all_prompts = own_prompts + [p for w in workers for p in w["prompts"]]
        all_prompts.sort(key=lambda p: (p.get("ts") is None, p.get("ts") or 0))
        reported = reported_by_sid.get(sid, 0.0) + sum(w["reported"] for w in workers)
        # 3-tier liveness: running (a real live process — pid in live_sids), resumable
        # (has a transcript on disk, live_flag; resume works but nothing is running), else
        # linked (recorded, no live transcript). `live` stays as the strict RUNNING bit
        # so any legacy reader means "actually running", not "survived a crash".
        state = "running" if sid in live else ("resumable" if live_flag else "linked")
        return {
            "sid8": sid[:8], "session_id": sid, "pinned": pinned, "main": main,
            "live": sid in live, "state": state, "role": "hub", "msgs": msgs,
            "ordinal": m.get("ordinal"),
            "age": rel_time(m.get("ts")), "oneliner": oneliner,
            "resume_command": _session_resume_command(sid, m),
            "own": own, "workers": workers,
            "agg": agg, "reported": round(reported, 6),
            "prompts": all_prompts, "prompt_count": len(all_prompts),
        }

    cards = []
    for h in tree.get("hubs", []):
        cards.append(_hub_card(h.get("sid") or "", bool(h.get("pinned")),
                               bool(h.get("main")), bool(h.get("live")),
                               h.get("msgs") or 0))
    # main hub floats first, then pinned, then the rest (newest-first order preserved).
    cards.sort(key=lambda c: (0 if c["main"] else 1, 0 if c["pinned"] else 1))

    # Anything in the ledger not attributed to a hub or its workers → a trailing
    # 'unattributed' pseudo-hub, so no cost/prompt is silently dropped.
    orphans = [w for w in (tree.get("orphan_workers") or [])]
    leftover_sids = [s for s in by_sid if s not in covered
                     and s not in {w.get("sid") for w in orphans}]
    if orphans or leftover_sids:
        workers = [_worker_card(w) for w in orphans]
        leftover_rows = [by_sid[s] for s in leftover_sids]
        agg = _aggregate_usage_summary(
            leftover_rows + [by_sid[w["session_id"]] for w in workers
                             if w["session_id"] in by_sid])
        prompts = [p for w in workers for p in w["prompts"]]
        for s in leftover_sids:
            prompts += prompts_by_sid.get(s, [])
        prompts.sort(key=lambda p: (p.get("ts") is None, p.get("ts") or 0))
        if agg["mix"] or workers or prompts:
            cards.append({
                "sid8": "", "session_id": "", "pinned": False, "main": False,
                "live": False, "role": "unattributed", "msgs": 0, "age": "",
                "oneliner": "sessions with no recorded hub",
                "resume_command": None,
                "own": _aggregate_usage_summary(leftover_rows),
                "workers": workers, "agg": agg, "reported": 0.0,
                "prompts": prompts, "prompt_count": len(prompts),
            })
    return {"hubs": cards, "cost_thresholds": thresholds}


# ---- WS3: session tree + task relations (detail + board consume these) -------

def related_edges(task, tasks=None, semantic=False):
    """Task-to-task relation edges for one task, both directions.

    Returns `{"out": [edge…], "in": [edge…]}`. `out` = this task's own `related`
    list (spawned-from / related edges it stored); `in` = reverse edges DERIVED by
    scanning other tasks' `related` lists for entries pointing at `task["id"]`. Each
    edge is annotated with the OTHER task's current `status` (None when that task is
    gone). Pass `tasks` (a task-blob list) to reuse a single load — the board builds
    it once so the scan stays O(N); detail leaves it None and scans `all_tasks()`.

    `semantic=True` adds a third key, `"semantic"` (the derived `touches-same` edges
    from `semantic_edges()`); default False keeps the return shape byte-identical to
    every existing caller (detail render / tests are unaffected)."""
    scan = tasks if tasks is not None else all_tasks()
    by_id = {t.get("id"): t for t in scan}
    tid = task.get("id")
    out = []
    for e in task.get("related") or []:
        other = by_id.get(e.get("id"))
        out.append({"seq": e.get("seq"), "id": e.get("id"), "kind": e.get("kind"),
                    "ts": e.get("ts"), "status": task_status(other) if other else None})
    inn = []
    for o in scan:
        if o.get("id") == tid:
            continue
        for e in o.get("related") or []:
            if e.get("id") == tid:
                inn.append({"seq": o.get("seq"), "id": o.get("id"),
                            "kind": e.get("kind"), "status": task_status(o)})
    result = {"out": out, "in": inn}
    if semantic:
        result["semantic"] = semantic_edges(task, scan)
    return result


# --- canonical relation resolution -------------------------------------------
#
# A task↔task relationship is ONE thing, but the store can record it up to three
# times: this task's `related` list holds it, the OTHER task's list holds the mirror
# (a reciprocal pair), and — because `append_related` dedups on id+KIND — the same
# side may hold it twice under two different kinds. Every consumer that walked the
# out edges and then the in edges therefore printed the same counterpart two or
# three times. `canonical_relations` is the ONE resolver they all route through.
#
# The dedup key is THE OTHER TASK'S id alone, never (id, kind): keying on the kind
# is exactly what leaves the mixed-label duplicate standing, since the two records
# differ precisely in their kind.

def _rel_kind_rank(kind):
    """Precedence rank for a relation kind — lowest wins. An unknown/future kind
    sorts last but never raises, so a store written by a newer version degrades to
    "shown, ranked last" instead of breaking a render."""
    return _REL_KIND_RANK.get(kind, _REL_KIND_RANK_UNKNOWN)


def canonical_relations(task, tasks=None, rev=None, edges=None):
    """ONE relationship per other task, deduped on the OTHER TASK's id.

    Returns a list of `{"id", "seq", "kind", "dir", "status"}` where `dir` is
    "out" (this task stored the edge) or "in" (derived from the other side). The
    shared resolver behind the `Related:` detail line, the board card's relation
    row and the graph's lineage precedence, so all three agree about what a pair IS.

    The rules, in force order:

      * dedup key = the other task's `id`; an entry carrying no id falls back to a
        `("seq", n)` key so nothing is ever silently dropped;
      * lowest `_REL_KIND_RANK` wins the label — a pair recorded as BOTH
        `spawned-from` and `related` (legal: `append_related` dedups on id+kind)
        resolves to `spawned-from`, once;
      * on an equal kind, `dir="out"` beats `dir="in"` — the side that asserted it
        wins;
      * a self-edge is dropped, matching `_add_undirected`;
      * order is kind rank, then the other task's seq — two runs over one store
        produce identical output.

    Where the raw edges come from, in precedence order: an already-built `edges`
    (a `related_edges`-shaped `{"out": […], "in": […]}`); else `rev`, this task's
    reverse-edge list out of the board's O(1) rev_map, with the out side read
    straight off `task["related"]`; else a `related_edges(task, tasks)` scan. Pure
    apart from that scan."""
    if edges is None:
        if rev is None:
            edges = related_edges(task, tasks)
        else:
            edges = {"out": [{"seq": r.get("seq"), "id": r.get("id"),
                              "kind": r.get("kind")}
                             for r in (task.get("related") or [])],
                     "in": list(rev)}

    def _pick(e):
        """Which recorded edge REPRESENTS the pair: kind first, then out beats in."""
        return (_rel_kind_rank(e.get("kind")), 0 if e.get("dir") == "out" else 1)

    def _order(e):
        """Display order: kind rank, then the counterpart's seq (seq-less last),
        then its id — a total order, so the output never wobbles between runs."""
        seq = e.get("seq")
        return (_rel_kind_rank(e.get("kind")),
                seq if seq is not None else (1 << 30),
                str(e.get("id") or ""))

    tid, tseq = task.get("id"), task.get("seq")
    best = {}
    for direction in ("out", "in"):
        for e in (edges.get(direction) or []):
            oid, oseq = e.get("id"), e.get("seq")
            if oid:
                if oid == tid:
                    continue                    # self-edge
            elif oseq is not None and oseq == tseq:
                continue                        # id-less entry pointing at itself
            key = oid if oid else ("seq", oseq)
            cand = {"id": oid, "seq": oseq, "kind": e.get("kind"),
                    "dir": direction, "status": e.get("status")}
            prev = best.get(key)
            if prev is None or _pick(cand) < _pick(prev):
                best[key] = cand
    return sorted(best.values(), key=_order)


# --- WS-D: universal semantic edges + (gated) knowledge co-citation ----------
#
# A UNIVERSAL improvement to the task graph — derived, never stored, and computed
# entirely from signals task-station already records (PRs, stories, files, repos).
# Nothing here reads a vault or needs any config: a public user with no second brain
# gets the richer graph and unchanged everything else. The co-citation layer
# (task↔note) is the ONLY vault-aware part and is gated off by default (see
# build_board_graph's `knowledge` arg + config.knowledge_graph_enabled()).

def _task_signals(task):
    """The dedup set of `(kind, value)` signals used to derive `touches-same` edges:
    PR urls, story urls, recently-edited file paths, and repo/project names. Pure —
    reads only fields the store already carries; empty set on a task with none."""
    sig = set()
    for p in merged_prs(task):
        u = (p.get("url") or "").strip()
        if u:
            sig.add(("pr", u))
    for s in merged_stories(task):
        u = (s.get("url") or "").strip()
        if u:
            sig.add(("story", u))
    for f in (task.get("files") or []):
        f = (f or "").strip()
        if f:
            sig.add(("file", f))
    for r in (task.get("projects") or []):
        r = (r or "").strip()
        if r:
            sig.add(("repo", r))
    return sig


def semantic_edges(task, tasks=None):
    """Derived `touches-same` edges for one task: every OTHER task that shares a PR,
    story, file, or repo/project with it. Undirected (a symmetric relation) so each
    counterpart appears once. Each edge carries `via` (the sorted shared signal
    kinds), `weight` (sum of `_SEMANTIC_WEIGHTS` over the shared signals), and the
    other task's `status`. Sorted strongest-first. Pure derivation — nothing is
    stored on the task; a task with no signals yields []. O(N × signals)."""
    scan = tasks if tasks is not None else all_tasks()
    mine = _task_signals(task)
    if not mine:
        return []
    tid = task.get("id")
    edges = []
    for o in scan:
        if o.get("id") == tid:
            continue
        shared = mine & _task_signals(o)
        if not shared:
            continue
        via = sorted({k for k, _ in shared})
        weight = sum(_SEMANTIC_WEIGHTS.get(k, 1) for k, _ in shared)
        edges.append({"seq": o.get("seq"), "id": o.get("id"),
                      "kind": "touches-same", "via": via, "weight": weight,
                      "status": task_status(o)})
    edges.sort(key=lambda e: (-e["weight"], e.get("seq") if e.get("seq") is not None else 1 << 30))
    return edges


def _task_cited_notes(task):
    """The set of vault note slugs a task cites via `[[wikilink]]` in its human text
    (title / goal / state / summary / decisions / history). The co-citation signal
    for the second-brain-gated knowledge graph — two tasks citing the same note are
    working the same knowledge. Empty set on a task with no wikilinks (the common
    case), so this never fabricates edges.

    `knowledge.task_note_links` is the single implementation, shared with the two-plane
    view's cross-plane `cites` edge. It also excludes a `[[task:502]]` target, which is a
    reference to a TASK rather than to a note — so such a mention can no longer enter
    this signal as a note that does not exist. The measured corpus has none of them, so
    that exclusion changes no edge that exists today."""
    return _knowledge.task_note_links(task)


def build_board_graph(tasks, knowledge=False):
    """The relation graph the board mini-graph renders. Nodes = tasks; edges are:

      * lineage — the stored `related` edges (`spawned-from` directed child→parent,
        `related` undirected); UNIVERSAL.
      * `related-knowledge` — task↔task co-citation (both cite the same `[[note]]`),
        weighted by shared-note count; SECOND-BRAIN-GATED, only when `knowledge=True`.

    Derived `touches-same` (shared PR/story/file/repo) edges are NOT emitted — see the
    note at the loop below. `semantic_edges` itself is untouched and still serves the
    export and the signal hubs.

    Only nodes that touch ≥1 edge are returned, so a bare / relation-free store yields
    `{"nodes": [], "edges": []}` and the renderer omits the panel entirely (a public
    user with no relations sees exactly today's board). Lineage collapses to ONE edge
    per unordered pair, labelled by `_REL_KIND_RANK` — the same precedence
    `canonical_relations` applies to the text and board surfaces — so a pair recorded
    under two kinds draws once. The derived tiers stay deduped by unordered seq-pair
    + kind, strongest weight winning. Pure and O(N + edges) — no I/O, no config read
    (the caller resolves `knowledge`)."""
    nodes_by_seq = {}
    for t in tasks:
        seq = t.get("seq")
        if seq is None:
            continue
        cur = task_status(t)
        glyph = STATUS_GLYPH_CLOSED if cur == STATUS_CLOSED else STATUS_GLYPH.get(cur, "○")
        nodes_by_seq[seq] = {"seq": seq, "id": t.get("id"),
                             "title": t.get("title", ""), "color": t.get("color"),
                             "status": cur, "glyph": glyph}
    by_id_seq = {t.get("id"): t.get("seq") for t in tasks}

    edges = []
    lineage = {}                   # frozenset({a,b}) → the ONE canonical lineage edge
    undirected = {}                # frozenset({a,b}) + kind → strongest edge dict

    def _add_undirected(a, b, kind, weight, via):
        if a is None or b is None or a == b:
            return
        key = (frozenset((a, b)), kind)
        prev = undirected.get(key)
        if prev is None or weight > prev["weight"]:
            undirected[key] = {"a": a, "b": b, "kind": kind, "dir": "none",
                               "weight": weight, "via": via}

    # Lineage edges from every task's stored `related` list — ONE per unordered pair.
    # A pair can be recorded up to three times (each side stores the other, and
    # `append_related` permits a second KIND for the same target), so it is collapsed
    # here on `_REL_KIND_RANK`. The winning kind decides the arrow: `spawned-from`
    # stays directed child→parent, anything else stays undirected.
    for t in tasks:
        a = t.get("seq")
        if a is None:
            continue
        for e in (t.get("related") or []):
            # Resolve the counterpart from its `id` — the only machine-portable
            # handle a stored entry carries. A `seq` is local to one store, so a
            # stored one is trusted ONLY for a legacy entry that has no id at all.
            # Preparation, not repair: today every stored entry carries both and
            # they agree, so this reorder changes nothing about the current graph.
            eid = e.get("id")
            b = by_id_seq.get(eid) if eid else e.get("seq")
            if b is None or b == a or b not in nodes_by_seq:
                continue
            kind = e.get("kind") or "related"
            key = frozenset((a, b))
            prev = lineage.get(key)
            if prev is not None:
                prev_rank, rank = _rel_kind_rank(prev["kind"]), _rel_kind_rank(kind)
                # Equal rank keeps the lexicographically smaller (a, b) so the
                # surviving orientation does not depend on the input's task order.
                if prev_rank < rank or (prev_rank == rank
                                        and (prev["a"], prev["b"]) <= (a, b)):
                    continue
            lineage[key] = {"a": a, "b": b, "kind": kind,
                            "dir": "a->b" if kind == "spawned-from" else "none",
                            "weight": 2, "via": ["lineage"]}
    edges.extend(lineage.values())

    # NO semantic `touches-same` edges. Two tasks touching one file is not a
    # relationship worth drawing, and the kind was 97% of every edge in the graph —
    # it buried the lineage it sat next to. Shared PRs / repos / stories are NOT lost:
    # they still reach the graph as signal HUBS with spokes (`build_render_graph`),
    # which say the same thing once per signal instead of once per pair. Only a
    # file-only share now draws nothing at all.
    #
    # `semantic_edges` / `_task_signals` / `_SEMANTIC_WEIGHTS` deliberately stay —
    # the vault/markdown export (`_related_pairs`) still emits its `touches same`
    # pairs, and the signal hubs still read the same signals. This is the GRAPH
    # dropping a consumer, not the signal being deleted.

    # Co-citation `related-knowledge` edges (undirected) — SECOND-BRAIN-GATED.
    if knowledge:
        cited = {}
        for t in tasks:
            seq = t.get("seq")
            if seq is None:
                continue
            notes = _task_cited_notes(t)
            if notes:
                cited[seq] = notes
        seqs = sorted(cited)
        for i in range(len(seqs)):
            for j in range(i + 1, len(seqs)):
                shared = cited[seqs[i]] & cited[seqs[j]]
                if shared:
                    _add_undirected(seqs[i], seqs[j], "related-knowledge",
                                    len(shared), sorted(shared))

    edges.extend(undirected.values())

    # Keep only nodes that participate in ≥1 edge.
    touched = set()
    for e in edges:
        touched.add(e["a"])
        touched.add(e["b"])
    nodes = [nodes_by_seq[s] for s in sorted(touched) if s in nodes_by_seq]
    # Deterministic edge order (stable render + stable tests).
    edges.sort(key=lambda e: (e["a"], e["b"], e["kind"]))
    return {"nodes": nodes, "edges": edges}


def parse_pr_number(url):
    """The PR number for a "PR <n>" label, parsed from an ADO or GitHub PR url:
    GitHub `.../pull/<n>`, ADO `.../pullrequest/<n>`, else any trailing path number.
    Returns the number as a STRING, or the trimmed url when unparseable. Pure — no I/O."""
    u = (url or "").strip()
    if not u:
        return u
    m = re.search(r"/(?:pull|pullrequest|pull-request|pullrequests)/(\d+)", u, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)/*$", u.rstrip("/"))
    if m:
        return m.group(1)
    return u


def shared_signal_groups(tasks):
    """Group tasks by shared pr/story/repo signal VALUE (via `_task_signals`) for the
    render graph's signal hubs. Returns `(groups, labels, singletons)`:

      * groups     — {(kind, value): [seqs]} for kind in ("pr","story","repo") shared by
                     >= 2 distinct member tasks (the hub threshold); seqs sorted.
      * labels     — {(kind, value): human label} — "PR <n>" / "repo <project>" /
                     "story <id>".
      * singletons — {seq: [labels]} for pr/story/repo signals owned by exactly ONE task
                     (no hub; surfaced later in step 3).

    Stories are keyed by their `story_ref` id (NOT the raw url) so a hub value matches the
    emitted `stories/<id>` page and a bare-id + full-url reference to the same work item
    collapse together. `file` signals are intentionally excluded — a file-only share forms
    no hub, and since the graph stopped emitting `touches-same` it now draws nothing at
    all (deliberate: "no need to link tasks on shared files"). Pure; deterministic output.
    Distinct from `_compute_story_group_ids` (different threshold + key space)."""
    try:
        import obsidian_sync
    except Exception:
        obsidian_sync = None
    members = {}        # (kind, value) -> set(seq)
    labels = {}         # (kind, value) -> human label
    for t in tasks:
        seq = t.get("seq")
        if seq is None:
            continue
        for kind, value in _task_signals(t):
            if kind == "pr":
                key = ("pr", value)
                labels[key] = "PR %s" % parse_pr_number(value)
            elif kind == "repo":
                key = ("repo", value)
                labels[key] = "repo %s" % value
            elif kind == "story":
                sid = None
                if obsidian_sync is not None:
                    try:
                        sid, _ = obsidian_sync.story_ref({"url": value})
                    except Exception:
                        sid = None
                if not sid:
                    continue
                key = ("story", sid)
                labels[key] = "story %s" % sid
            else:
                continue                       # file — no hub
            members.setdefault(key, set()).add(seq)
    groups, singletons = {}, {}
    for key, seqs in members.items():
        if len(seqs) >= 2:
            groups[key] = sorted(seqs)
        else:
            (only_seq,) = tuple(seqs)
            singletons.setdefault(only_seq, []).append(labels[key])
    for s in singletons:
        singletons[s] = sorted(singletons[s])
    return groups, labels, singletons


def build_render_graph(tasks, knowledge=False, notes=None):
    """Render-layer augmentation of `build_board_graph`: adds category + signal HUB nodes
    and re-keys every node to a typed STRING id for the board mini-graph. Pure; does NOT
    mutate `build_board_graph`'s output or the export path (both contract-pinned).

    Node ids are `"t:<seq>"` (task), `"cat:<key>"` (category hub), `"sig:<kind>:<value>"`
    (signal hub), or `"n:<slug>"` (a vault NOTE — the knowledge plane, see below). Edges
    keep `kind,dir,weight,via` and re-map `a`/`b` to those string ids.

      * Category hub — one per category with >= 2 tasks present among the base graph's task
        nodes; each member task gets a `membership` spoke. The KEY only is carried (the
        renderer resolves the hex via `_highlight_fb`).
      * Signal hub — one per shared pr/story/repo group (>= 2 members in the base graph),
        each member task getting a kind-tagged spoke. This is now the ONLY way a shared
        PR / repo / story reaches the graph, since the base stopped emitting the direct
        `touches-same` edge that used to sit alongside it.
      * lineage + `related-knowledge` edges are re-mapped direct + untouched.
      * Note node — one per note in `notes`, the KNOWLEDGE PLANE (see below).

    A task is DRAWN when it carries a base edge (lineage / co-citation), belongs to a
    >=2-member signal group, or sits on a cross-plane edge — the second clause is what
    keeps the hub tier alive now that the base no longer emits a `touches-same` edge for
    every shared signal (see the note on `present` below), and the third is what stops a
    citation pointing at a task the graph never drew.

    THE KNOWLEDGE PLANE (`notes`, from `board_notes` / `knowledge.vault_notes`). Passing
    a corpus adds a SECOND plane to this one graph: a node per note, `links-to` edges
    between notes, and the three cross-plane kinds (`cites`, `distilled-from`,
    `references`) — and nothing else crosses the gap. Every node then carries `plane`
    (`task` or `knowledge`); with no notes NO node carries it, so the default graph is
    byte-identical to the one this function returned before the plane existed — the same
    parity rule the Interbrain owner/brain stamping follows, for the same reason.

    EVERY note is drawn, edge or no edge. The corpus is global and rendered whole, so an
    orphan note is a real node that the layout seats out at the rim; that is a fact about
    the vault, not an absence to hide.

    Every node carries `deg` (incident-edge count). Nodes are sorted by (type-rank,
    seq/key/value/slug) and edges by (kind, a, b) — type-homogeneous keys only, never
    mixing int/str. Returns `{nodes, edges, singletons}` where `singletons` =
    `{seq: [labels]}` for pr/story/repo signals with exactly one task (renderer reads
    only nodes/edges). A relation-free / solo board with no notes (no base edges AND no
    shared signal AND no corpus) returns the base unchanged so the panel stays absent."""
    base = build_board_graph(tasks, knowledge)
    tasks_by_seq = {t.get("seq"): t for t in tasks if t.get("seq") is not None}
    notes = list(notes or [])

    # WHICH TASKS THE GRAPH DRAWS. Until the base stopped emitting `touches-same`,
    # every task sharing a PR/repo/story with another was ALREADY a base node — that
    # edge put it there — so "base nodes" and "base nodes + signal-group members" were
    # the same set and this distinction never showed. They are not the same set now:
    # sourcing `present` from base nodes alone would delete every signal HUB along with
    # the direct edge, since a hub is only built from tasks already present. Unioning
    # the >=2-member signal groups back in keeps the hub tier exactly as it renders
    # today, so the ONLY thing the `touches-same` removal actually drops is a
    # file-only share — which is precisely the ruling ("no need to link tasks on
    # shared files"); a file signal forms no hub, so it now draws nothing at all.
    groups, labels, singletons = shared_signal_groups(tasks)
    grouped = set()
    for _gseqs in groups.values():
        if len(_gseqs) >= 2:
            grouped.update(_gseqs)
    # The cross-plane edges are resolved HERE because `present` needs them: a task whose
    # only relation is a citation of a note has no base edge and no signal group, so
    # without this clause the citation would point at a task node that was never built.
    cross = _knowledge.cross_plane_edges(tasks, notes) if notes else []
    linked = {e["seq"] for e in cross if e.get("seq") in tasks_by_seq}
    if not base["edges"] and not grouped and not notes:
        return base                                 # relation-free / solo → no panel

    base_seqs = {n["seq"] for n in base["nodes"]}
    present = base_seqs | grouped | linked          # drawn task seqs (int)

    def tid(seq):
        return "t:%d" % seq

    def nid(slug):
        return "n:%s" % slug

    # ---- task nodes (fresh dicts — never mutate base) --------------------------
    nodes = []
    for n in base["nodes"]:
        nodes.append({"id": tid(n["seq"]), "type": "task", "seq": n["seq"],
                      "title": n.get("title", ""), "color": n.get("color"),
                      "status": n.get("status"), "glyph": n.get("glyph")})
    # A task that reaches the graph ONLY through a signal hub has no base node, so its
    # node dict is built here — same fields, same glyph rule as build_board_graph.
    for seq in sorted(present - base_seqs):
        t = tasks_by_seq.get(seq)
        if t is None:
            continue
        cur = task_status(t)
        nodes.append({"id": tid(seq), "type": "task", "seq": seq,
                      "title": t.get("title", ""), "color": t.get("color"),
                      "status": cur,
                      "glyph": (STATUS_GLYPH_CLOSED if cur == STATUS_CLOSED
                                else STATUS_GLYPH.get(cur, "○"))})

    edges = []

    # ---- category hubs: one per category with >= 1 present task ----------------
    # (Threshold is >=1 so every category present in the edge graph gets a hub; the
    # relation-free/solo board is still gated out by the early return above. The hub's
    # `key` is the NORMALISED category, while a task node carries its stored `color`
    # raw — the board's filter keys on the latter and looks hubs up by the former, so
    # the two must stay the same string. They do: every write path normalises `color`.)
    try:
        import categories
    except Exception:
        categories = None
    if categories is not None:
        cat_members = {}                              # key -> [seqs present]
        for seq in present:
            t = tasks_by_seq.get(seq)
            if t is None:
                continue
            try:
                ckey = categories.normalize(t.get("color") or "")
            except Exception:
                continue
            cat_members.setdefault(ckey, []).append(seq)
        for ckey in sorted(cat_members):
            seqs = sorted(cat_members[ckey])
            if len(seqs) < 1:
                continue
            try:
                meta = categories.hub_meta(ckey)
                label = "%s [%s] %s" % (meta["dot"], meta["tag"], meta["label"])
            except Exception:
                label = ckey
            nodes.append({"id": "cat:%s" % ckey, "type": "hub", "key": ckey,
                          "label": label, "status": "open"})
            for seq in seqs:
                edges.append({"a": tid(seq), "b": "cat:%s" % ckey,
                              "kind": "membership", "dir": "none", "weight": 1,
                              "via": []})

    # ---- signal hubs: shared pr/story/repo groups (>= 2 present members) -------
    # `groups`/`labels`/`singletons` were resolved above, where `present` needed them.
    sig_by_seq = {}                                   # seq -> set(signal hub id)
    for key in sorted(groups):
        kind, value = key
        seqs = [s for s in groups[key] if s in present]
        if len(seqs) < 2:
            continue
        sid = "sig:%s:%s" % (kind, value)
        nodes.append({"id": sid, "type": "signal", "kind": kind,
                      "label": labels[key]})
        w = _SEMANTIC_WEIGHTS.get(kind, 1)
        for seq in seqs:
            edges.append({"a": tid(seq), "b": sid, "kind": kind, "dir": "none",
                          "weight": w, "via": [labels[key]]})
            sig_by_seq.setdefault(seq, set()).add(sid)

    # ---- re-map base edges -----------------------------------------------------
    # The `touches-same` branch is now UNREACHABLE from build_board_graph, which no
    # longer emits that kind. Kept as a guard so a hand-built or future base graph
    # carrying one still can't draw an edge that duplicates a signal hub; delete it
    # (and `sig_by_seq`) if the kind is ever removed from the codebase entirely.
    for e in base["edges"]:
        if e["kind"] == "touches-same":
            shared_hub = sig_by_seq.get(e["a"], set()) & sig_by_seq.get(e["b"], set())
            if shared_hub:
                continue                              # redundant with a signal hub
        edges.append({"a": tid(e["a"]), "b": tid(e["b"]), "kind": e["kind"],
                      "dir": e.get("dir", "none"), "weight": e.get("weight", 1),
                      "via": list(e.get("via") or [])})

    # ---- the KNOWLEDGE PLANE: one node per note, then its own edges -------------
    # A note node carries the frontmatter under `kind` (its `type`) and `area`, because
    # `type` on a node already means the structural class (task / hub / signal / note)
    # and one word cannot mean both. `area` is left exactly as the vault wrote it —
    # empty when the note declares none — and the LAYOUT is what maps that to the
    # `unfiled` sector, since which sector a note sits in is a placement decision.
    note_slugs = set()
    for n in notes:
        slug = n.get("slug")
        if not slug or slug in note_slugs:
            continue
        note_slugs.add(slug)
        nodes.append({"id": nid(slug), "type": "note", "slug": slug,
                      "title": n.get("title") or slug,
                      "description": n.get("description", ""),
                      "kind": n.get("type", ""), "area": n.get("area", ""),
                      "path": n.get("path", "")})
    for e in _knowledge.note_edges(notes):
        edges.append({"a": nid(e["a"]), "b": nid(e["b"]), "kind": e["kind"],
                      "dir": e.get("dir", "none"), "weight": e.get("weight", 1),
                      "via": []})
    # THE GAP. Exactly three kinds cross it, and the direction asymmetry is deliberate:
    # `cites` is a task pointing UP at what it read, `distilled-from` a note pointing
    # DOWN at what produced it. They are not inverses, so both are drawn when both hold.
    # An endpoint that does not exist was already dropped by `cross_plane_edges`; the
    # `present`/`note_slugs` checks here are the second half of the same rule, for a
    # task the drawn set still excludes.
    for e in cross:
        seq, slug = e.get("seq"), e.get("slug")
        if seq not in present or slug not in note_slugs:
            continue
        if e["dir"] == "task->note":
            a, b = tid(seq), nid(slug)
        else:
            a, b = nid(slug), tid(seq)
        edges.append({"a": a, "b": b, "kind": e["kind"], "dir": "a->b",
                      "weight": 1, "via": []})

    # ---- node degree -----------------------------------------------------------
    deg = {}
    for e in edges:
        deg[e["a"]] = deg.get(e["a"], 0) + 1
        deg[e["b"]] = deg.get(e["b"], 0) + 1
    for n in nodes:
        n["deg"] = deg.get(n["id"], 0)

    # WHICH PLANE EACH NODE IS ON — stamped only when a corpus was passed, so a board
    # with no notes emits the identical node dicts it emitted before this plane existed.
    # A category or signal hub is task-plane furniture, so it takes `task` too.
    if notes:
        for n in nodes:
            n["plane"] = "knowledge" if n.get("type") == "note" else "task"

    # ---- deterministic order (type-homogeneous sort keys only) -----------------
    def _node_key(n):
        t = n.get("type")
        if t == "task":
            return (0, n.get("seq"))
        if t == "hub":
            return (1, n.get("key") or "")
        if t == "note":
            return (3, n.get("slug") or "")
        return (2, n.get("kind") or "", n.get("id") or "")
    nodes.sort(key=_node_key)
    edges.sort(key=lambda e: (e["kind"], e["a"], e["b"]))
    return {"nodes": nodes, "edges": edges, "singletons": singletons}


def _worker_name(w):
    """`project` or `project:label` for a worker row in the Sessions tree."""
    proj = w.get("project") or "?"
    lbl = w.get("label")
    return "%s:%s" % (proj, lbl) if lbl else proj


def _session_block_lines(task):
    """The terminal-detail `Sessions:` block (hubs with workers nested under their
    spawning hub), using the canonical session-state vocabulary: ● running (a Claude
    process is alive now) / ◐ resumable (saved transcript, nothing running) / ○ gone
    (no transcript found). Returns [] when the task has no recorded sessions — so a
    bare task stays exactly as it renders today."""
    if not task.get("session_meta"):
        return []
    tree = session_tree(task)
    hubs = tree.get("hubs") or []
    orphans = tree.get("orphan_workers") or []
    if not hubs and not orphans:
        return []
    running_sids = set(_live_session_index())
    HUB_CAP, WK_CAP = 6, 4
    out = ["Sessions:"]

    def _wk_line(w, indent):
        return "%s↳ %s  worker (%s) — %s · %s" % (
            indent, (w.get("sid") or "????????")[:8], _worker_name(w),
            w.get("model") or "?", rel_time(w.get("ts")))

    for h in hubs[:HUB_CAP]:
        tags = ["hub", "main" if h.get("main") else "side-quest"]
        if h.get("pinned"):
            tags.append("pinned")
        if h.get("sid") in running_sids:
            glyph, word = "●", "running"
        elif h.get("live"):
            glyph, word = "◐", "resumable"
        else:
            glyph, word = "○", "gone"
        ordn = h.get("ordinal")
        onum = ("%s-%s " % (task.get("seq"), ordn)
                if ordn is not None and task.get("seq") is not None else "")
        out.append("  %s %s%s  %s — %s · %d msgs · %s · %s" % (
            glyph, onum, (h.get("sid") or "????????")[:8],
            " · ".join(tags), word,
            h.get("msgs") or 0, rel_time(h.get("ts")), _tildify(h.get("cwd"))))
        wks = h.get("workers") or []
        for w in wks[:WK_CAP]:
            out.append(_wk_line(w, "      "))
        if len(wks) > WK_CAP:
            out.append("      … +%d more worker(s)" % (len(wks) - WK_CAP))
    if len(hubs) > HUB_CAP:
        out.append("  … +%d more hub(s)" % (len(hubs) - HUB_CAP))
    for w in orphans[:WK_CAP]:
        out.append("  ↳ (unlinked) %s  worker (%s) — %s · %s" % (
            (w.get("sid") or "????????")[:8], _worker_name(w),
            w.get("model") or "?", rel_time(w.get("ts"))))
    if len(orphans) > WK_CAP:
        out.append("  … +%d more worker(s)" % (len(orphans) - WK_CAP))
    return out


def _related_line(task, edges=None):
    """The `Related:` artifacts line, or None when the task has no relation edges
    (either direction). ONE entry per counterpart — a reciprocal pair is a single
    relationship, so it prints once, resolved through `canonical_relations`.

    Each kind renders as its stored word or its derived inverse (`_REL_LINE_WORDS`):
    `depends on #N` / `blocks #N` · `parent #P` / `children #Q` · `duplicates #N` ·
    `replaces #N` / `replaced by #N` · `absorbed-by #N` / `absorbed #N` ·
    `from #N (spawned-from)` / `spawned #N`, with anything else — including `related`
    and any future kind — reading `related #N`. A closed edge target gets a trailing
    ` ✕`.

    CONSECUTIVE entries sharing a label are GROUPED under it — `children #Q, #R` — so a
    parent with eight children reads the label once rather than eight times.
    `canonical_relations` sorts by kind, so same-kind entries are already adjacent. The
    closed mark is per TARGET, so it rides with its own `#N` and grouping never loses it;
    a run of one renders through the format strings above, unchanged."""
    runs = []
    for r in canonical_relations(task, edges=edges):
        mark = " ✕" if r.get("status") == STATUS_CLOSED else ""
        out = r.get("dir") == "out"
        idx = 0 if out else 1
        label = _REL_LINE_LABELS.get(r.get("kind"), _REL_LINE_LABELS_DEFAULT)[idx]
        fmt = _REL_LINE_WORDS.get(r.get("kind"), _REL_LINE_DEFAULT)[idx]
        if runs and runs[-1][0] == label:
            runs[-1][1].append("#%s%s" % (r.get("seq"), mark))
        else:
            runs.append([label, ["#%s%s" % (r.get("seq"), mark)], (fmt % r.get("seq")) + mark])
    parts = [run[2] if len(run[1]) == 1 else "%s %s" % (run[0], ", ".join(run[1]))
             for run in runs]
    return ("Related: " + " · ".join(parts)) if parts else None


def _session_resume_command(sid, meta_entry):
    """The `cd <cwd> && claude …` resume one-liner for ONE recorded session id (used
    by the per-hub-session board rows), or None when no cwd is knowable. Mirrors
    `_resume_target`'s self-correcting-cwd logic for a single sid: a live transcript
    (≥1 user msg) → `--resume <sid>` with the transcript's own launch cwd; a preborn
    unborn session → `--session-id <sid>`; else a plain fresh `claude` in the recorded
    cwd. Pure display — mints nothing."""
    meta_entry = meta_entry or {}
    path = g("_find_session_path")(sid)
    if path and g("_session_msgcount")(path) >= 1:
        cwd = _session_cwd(path) or meta_entry.get("cwd")
        if cwd:
            return bg_aware_resume(sid, cwd)
    cwd = meta_entry.get("cwd")
    if not cwd:
        return None
    if meta_entry.get("preborn"):
        return "cd %s && claude --session-id %s" % (cwd, sid)
    return "cd %s && claude" % cwd


def _hub_sessions(task, live_sids=None):
    """Per-hub-session one-liners for the board's merged Sessions section (WS7 → WS6).

    One dict per `session_meta` hub, pinned-first then newest-first:
      {sid8, session_id, oneliner, pinned, live, role, msgs, age, cost_usd,
       out_tokens, resume_command}
    `oneliner` is that session's first captured user PROMPT (truncated to 80 chars;
    command/compaction rows skipped) else a plain attach note; `cost_usd`/`out_tokens`
    come from the usage ledger row for the sid (`cost_usd` None when it used an unknown
    model). Reuses WS3's `session_tree` hub set when that sibling is merged, else derives
    hubs inline from `session_meta`. [] when the task has no `session_meta` — so a bare
    task adds nothing. Defensive: any ledger hiccup degrades to no cost/one-liner, never
    breaks the board."""
    meta = task.get("session_meta") or {}
    if not meta:
        return []
    live = set(live_sids or [])
    pinned_sid = task.get("pinned_session")
    ledger = {}
    first_prompt = {}
    try:
        import config as _cfg
        if _cfg.usage_tracking_enabled():
            store = _backend()
            for r in store.session_usage_for_task(task["id"]):
                sid = r.get("session_id")
                if sid:
                    ledger[sid] = r
            for p in _usage_engine().task_prompts(store, task):
                sid = p.get("session_id")
                if sid and p.get("kind") == "prompt" and sid not in first_prompt:
                    first_prompt[sid] = (p.get("text") or "").strip()
    except Exception:
        pass
    hubs = [(sid, (m or {})) for sid, m in meta.items() if (m or {}).get("role") == "hub"]
    # pinned first, then newest recorded meta ts first.
    hubs.sort(key=lambda kv: (kv[0] == pinned_sid, kv[1].get("ts") or 0), reverse=True)
    out = []
    for sid, m in hubs:
        path = g("_find_session_path")(sid)
        msgs = g("_session_msgcount")(path) if path else 0
        one = first_prompt.get(sid) or ("attached · %s" % _tildify(m.get("cwd")))
        row = ledger.get(sid)
        cost_usd, out_tok = None, 0
        if row is not None:
            mm = _merge_row_models(row)
            out_tok = sum(d.get("out") or 0 for d in mm.values())
            costs = [d.get("cost_usd") for d in mm.values()]
            cost_usd = None if any(c is None for c in costs) else round(sum(costs), 6)
        out.append({
            "sid8": sid[:8],
            "session_id": sid,
            "oneliner": one[:80],
            "pinned": sid == pinned_sid,
            "live": sid in live,
            "role": "hub",
            "ordinal": m.get("ordinal"),
            "msgs": msgs,
            "age": rel_time(m.get("ts")),
            "cost_usd": cost_usd,
            "out_tokens": out_tok,
            "resume_command": _session_resume_command(sid, m),
        })
    return out


def _board_session_counts(task, live_sids=None):
    """WS4: compact session-tree counts for the board card —
    `{"hubs", "workers", "running", "resumable", "live_hubs"}` using the canonical
    session-state vocabulary: `running` = hubs with a real live process (pid in
    `live_sids`); `resumable` = hubs with a findable transcript but nothing running.
    `live_hubs` is kept as the legacy transcript-findable count for older renderers.
    Prefers WS3's richer `session_tree(task)` when that sibling helper is merged;
    otherwise derives minimally from the task's own `session_meta` hubs + its
    recorded delegate workers so the board degrades gracefully when WS3 is absent.
    Never raises; a bare task (no sessions, no workers) yields all-zero counts (the
    renderer omits the row)."""
    running_sids = set(live_sids or [])
    st = globals().get("session_tree")
    if callable(st):
        try:
            tree = st(task) or {}
            hubs = tree.get("hubs") or []
            workers = sum(len(h.get("workers") or []) for h in hubs)
            workers += len(tree.get("orphan_workers") or [])
            live = sum(1 for h in hubs if h.get("live"))
            running = sum(1 for h in hubs if h.get("sid") in running_sids)
            resumable = sum(1 for h in hubs
                            if h.get("live") and h.get("sid") not in running_sids)
            return {"hubs": len(hubs), "workers": workers, "live_hubs": live,
                    "running": running, "resumable": resumable}
        except Exception:
            pass
    meta = task.get("session_meta") or {}
    hub_sids = [sid for sid, m in meta.items()
                if isinstance(m, dict) and (m.get("role") or "hub") == "hub"]
    live = 0
    resumable = 0
    for sid in hub_sids:
        try:
            p = g("_find_session_path")(sid)
            if p and g("_session_msgcount")(p) >= 1:
                live += 1
                if sid not in running_sids:
                    resumable += 1
        except Exception:
            pass
    running = sum(1 for sid in hub_sids if sid in running_sids)
    # A real delegate worker carries a "session" key (None when a worktree exists
    # but no session ran yet); the placeholder "no worker recorded yet" entry does
    # not, so `"session" in w` counts only genuinely-registered workers.
    workers = sum(1 for w in worker_targets(task) if "session" in w)
    return {"hubs": len(hub_sids), "workers": workers, "live_hubs": live,
            "running": running, "resumable": resumable}


def _board_related(task, tasks=None, rev_map=None):
    """WS4: relation edges for the board card —
        {"from": [{"seq","kind","id"}…],           # edges stored ON this task
         "in":   [{"seq","kind","status","id"}…]}  # edges pointing AT this task

    ONE entry per counterpart across BOTH lists: this is a second, independent
    derivation of the same data the detail line shows, so it routes through
    `canonical_relations` too — a reciprocal pair lands in whichever list wins the
    precedence rather than appearing in both. The `id` is emitted on both sides so
    a consumer can dedup on the task rather than on a machine-local seq; every
    pre-existing key is retained (purely additive).

    `rev_map` (target task id → list of incoming edge dicts) is built ONCE by
    write_board so each card's reverse edges are an O(1) lookup and the board stays
    O(N) — never an N² per-task scan. When `rev_map` is absent (detail path / tests)
    the reverse edges are derived by scanning `tasks` (or `all_tasks()`). Empty lists
    when the task has no relations, so the renderer omits the row."""
    tid = task.get("id")
    if rev_map is not None:
        rev = list(rev_map.get(tid) or [])
    else:
        rev = []
        try:
            scan = tasks if tasks is not None else all_tasks()
        except Exception:
            scan = []
        for other in scan:
            if not isinstance(other, dict) or other.get("id") == tid:
                continue
            for r in (other.get("related") or []):
                if r.get("id") == tid:
                    rev.append({"seq": other.get("seq"), "id": other.get("id"),
                                "kind": r.get("kind"), "status": task_status(other)})
    # rev is always a list (never None), so the resolver reuses the O(1) reverse
    # index / the scan just done and never re-scans the store itself.
    rels = canonical_relations(task, rev=rev)
    out = [{"seq": r["seq"], "kind": r["kind"], "id": r["id"]}
           for r in rels if r["dir"] == "out"]
    inn = [{"seq": r["seq"], "kind": r["kind"], "status": r["status"], "id": r["id"]}
           for r in rels if r["dir"] == "in"]
    return {"from": out, "in": inn}


def _board_view_model(task, live_sids=None, tasks=None, rev_map=None, knowledge=False,
                      live_seqs=None):
    """Flatten a task into the plain dict the HTML board renders — every field the
    card shows, including the derived briefing + the resume one-liner. Decouples
    rendering (tools/render_board.py, stdlib + categories only) from the store.

    `live_sids` (a set of running session ids, supplied by WS5 when available) marks
    per-session live dots; absent it defaults to no live sessions (graceful degrade).

    `knowledge` (SECOND-BRAIN-GATED, default off) adds the `"knowledge"` key — the
    task's cited `[[notes]]` — for the board's per-card "Related knowledge" panel.
    Left None when off, so the renderer omits the panel and a public user sees no
    change."""
    cur = task_status(task)
    glyph = STATUS_GLYPH_CLOSED if cur == STATUS_CLOSED else STATUS_GLYPH.get(cur, "○")
    # The BOARD's 4-state display status folds the live-session signal into the stored
    # status (the old separate live column is gone). closed → closed; a task with a
    # running session → `live` (green, regardless of new/in-progress); an in-progress
    # task with NO running session → `paused` (yellow); an untouched task → `new`.
    # The green state is called `live` so "active" only ever means the stored lifecycle.
    # This is a board-only display overlay — the STORED status (open/active/closed) is
    # unchanged, so the terminal/markdown views and every stored-status contract are
    # untouched.
    _seq0 = task.get("seq")
    _is_live = bool(live_seqs) and _seq0 is not None and _seq0 in live_seqs
    if cur == STATUS_CLOSED:
        status_disp = "closed"
    elif _is_live:
        status_disp = "live"
    elif cur == STATUS_ACTIVE:
        status_disp = "paused"
    else:
        status_disp = "new"
    # 3-tuple (base, dir, abspath) — WS6 links the basename via the editor scheme and
    # copies the abspath; a legacy 2-tuple consumer still unpacks (base, dir, *_).
    files = [(os.path.basename(p), os.path.dirname(p) or ".", p)
             for p in (task.get("files") or [])[-8:]]
    color = task.get("color")
    eff = task.get("effort") or ""
    # The hub/pinned resume one-liner + its last-activity ts, from the SAME
    # computation the terminal task-detail uses (resume_command wraps _resume_target).
    rt = _resume_target(task, None)
    resume_main = None
    if rt:
        resume_main = {
            "command": rt["command"],
            "activity": rel_time(rt["ts"]),
            "pinned": rt["pinned"],
            "fresh": rt["fresh"],
            "label": "Resume (pinned)" if rt["pinned"] else "Resume (hub)",
        }
    seq = task.get("seq")
    # The simple OPEN command — attaches/opens the task in the CURRENT session (the
    # recap), distinct from the resume one-liner that jumps back into the original
    # working session. Only emitted when the task has a seq to address it by.
    open_command = ("/todo %s" % seq) if seq is not None else None
    # WS4 usage view-model (model mix / per-session / phases / prompts) — None/[] when
    # the ledger is off or empty, so the renderer omits the panels.
    bu = _board_usage(task, live_sids)
    hub_cards = _board_hub_cards(task, live_sids)
    # WS7 cost cell / display — never `$n/a`: derived when priced, else the reported $,
    # else a `≥` floor, else `$0.00`. Reuses the ledger already fetched by _board_usage;
    # when there's no ledger, falls back to the delegate-reported task cost.
    if bu and bu.get("usage"):
        stats_cost = _stats_cost(bu["usage"])
    else:
        rep = 0.0
        try:
            rep = float((task.get("cost") or {}).get("total_usd") or 0.0)
        except (TypeError, ValueError):
            rep = 0.0
        stats_cost = _stats_cost({"total_cost_usd": 0.0, "reported_cost_usd": rep,
                                  "any_unpriced": False})
    done, total = step_progress(task)
    steps_cell = ("%d/%d" % (done, total)) if total else None
    try:
        import config as _cfg
        editor_scheme = _cfg.editor_scheme()
    except Exception:
        editor_scheme = "vscode"
    return {
        "seq": seq,
        "title": task.get("title", ""),
        "full_title": task.get("title", ""),       # never truncated in the expanded detail
        "open_command": open_command,              # `/todo <seq>` — open in this session
        "summary": (task.get("summary") or "").strip(),
        "status": cur,
        "status_label": cur,                      # the WORD: open / active / closed
        # BOARD 4-state display status (new / paused / live / closed) — folds the
        # live-session signal into the stored status (see above). Drives the status pill,
        # the row breathing, the category counts, and the status filter — all from ONE
        # value so they can never disagree. `live` is the raw running-session boolean.
        "status_display": status_disp,
        "live": _is_live,
        "glyph": glyph,
        "color": color,
        "tag": cat_tag(color),
        "label": (cats.label(color) if (cats and color) else ""),
        # whether the user has overridden this category's tag/label (board marks it)
        "overridden": bool(cats and color and hasattr(cats, "overridden_keys")
                           and cats.normalize(color) in cats.overridden_keys()),
        "effort": eff,
        "effort_label": EFFORT_WORD.get(eff, ""),
        "effort_gauge": EFFORT_GAUGE.get(eff, EFFORT_GAUGE_EMPTY),
        "activity": rel_time(task.get("updated_ts")),
        "stats": task_stats_line(task),            # compact time/cost line ('' when none)
        "goal": (task.get("goal") or "").strip(),
        "state": (task.get("state") or "").strip(),
        # ACTIVE steps only (a superseded one is off the checklist); the shape is
        # unchanged, so a task with nothing superseded renders exactly as before.
        "steps": [{"text": _steps.text(s), "done": _steps.is_done(s)}
                  for _i, s in _steps.live(task.get("steps"))],
        "progress": list(step_progress(task)),     # [done, total] — active steps only
        "decisions": _dec.live_texts(task.get("decisions")),
        # The append-only HISTORY trail (`update --log` → `history`, entries {ts,text}).
        # Off the normal resume path in the terminal; the board renders it collapsed
        # (secondary to the current snapshot), mirroring `/todo <n> history`.
        "history": [{"ts": e.get("ts", ""), "text": e.get("text", "")}
                    for e in (task.get("history") or [])],
        "repos": list(task.get("projects") or []),
        "prs": merged_prs(task),
        "stories": merged_stories(task),
        "story_refs": _story_refs(task),   # WS13: {id,url} per story, for the board STORY column
        # F5 correspondence: peer pairs (links) + fork provenance. Empty/None on a task
        # with no correspondence, so the renderer emits nothing (parity preserved).
        "links": list(task.get("links") or []),
        "forked_from": task.get("forked_from") or None,
        "files": files,
        "pinned": bool(task.get("pinned_session")),
        "resume": rt["command"] if rt else None,   # back-compat (plain command string)
        "resume_main": resume_main,                # hub/pinned: command + activity + label
        "workers": worker_targets(task),           # de-emphasised worker subsection
        # WS4 usage panels — model mix + totals + derivation, the per-session breakdown,
        # the WS3 work-mix phases, and the prompts preview. `usage` is None / the lists
        # empty when the ledger is off/empty so the renderer omits the blocks.
        "usage": bu["usage"] if bu else None,
        "sessions": bu["sessions"] if bu else [],
        "phases": bu["phases"] if bu else [],
        "prompts_preview": _board_prompts(task),
        "prompts_full": _board_prompts_all(task),   # WS6 full trail (session-attributed)
        # WS7 → WS6 contract keys. `stats_cost` never says n/a; `steps_cell`/`cost_cell`
        # are the promoted grid columns; `hub_sessions` are the per-hub one-liners for
        # the merged Sessions section; `editor_scheme` drives the clickable file links.
        "stats_cost": stats_cost,
        "steps_cell": steps_cell,
        "cost_cell": stats_cost["text"],
        "hub_sessions": _hub_sessions(task, live_sids),
        # board B10–B14: the per-hub cards (each baking in its own prompts, cost +
        # work-mix, and nested worker sessions) + the task-wide stdev cost bands.
        "hubs": hub_cards["hubs"],
        "cost_thresholds": hub_cards["cost_thresholds"],
        "editor_scheme": editor_scheme,
        # WS4: compact session-tree counts + relation edges. Empty (all-zero counts /
        # empty edge lists) on a bare task, so the renderer omits both rows. `session_tree`
        # (NOT `sessions`, which is TAKEN by the per-session usage rows above) holds the
        # hub/worker/live counts; `related` holds outgoing + derived incoming edges.
        "session_tree": _board_session_counts(task, live_sids),
        "related": _board_related(task, tasks=tasks, rev_map=rev_map),
        # WS-D second-brain-gated: the cited [[notes]] for the "Related knowledge"
        # panel. None (not []) when the knowledge tier is off, so the renderer's
        # `if t.get("knowledge")` omits the panel and bare tasks are unchanged.
        "knowledge": (sorted(_task_cited_notes(task)) or None) if knowledge else None,
    }


# ---- F1: server-side foreign (peer/org) rendering --------------------------------------
#
# When Interbrain is ON and peer feed files exist, foreign tasks are parsed from the
# CANONICAL feed serialization (`window.__TSFEED_<alias> = <json>;` — what `feeds._feed_js`
# writes and the two-machine sync will produce) and mapped into the board view-model so
# they render through the SAME _row/section builders as local tasks — read-only (owner chip
# + 🔒 + memo-only, no sessions/prompts/resume). Foreign tasks NEVER touch the store.
#
# `lib/feeds.py` owns the format: parsing (`feeds.parse_feed_file`) and the peers-then-demo
# load order (`feeds.peer_feed_files`) live there, so there is exactly ONE implementation.
# (The demo fixtures under fixtures/demo-feeds/ are canonical too, as of #444 — they used
# to be client-side IIFEs, which this path silently skipped. Any legacy non-canonical file
# is still skipped rather than fatal.)

def _interbrain_on(data_dir):
    """Resolve config.interbrain_mode() for the board: on/off explicit; `auto` → on when
    brains.json has >1 brain OR any peer feed file exists. Off (and any error) ⇒ False, so
    the DEFAULT board is byte-parity with the pre-federation render."""
    try:
        import config as _cfg
        mode = _cfg.interbrain_mode()
    except Exception:
        return False
    if mode == "on":
        return True
    if mode == "off":
        return False
    n_brains = 1
    try:
        import brains as _brains
        n_brains = len(_brains.load(data_dir).get("brains", {})) or 1
    except Exception:
        n_brains = 1
    try:
        import feeds as _feeds
        return n_brains > 1 or bool(_feeds.peer_feed_files(data_dir))
    except Exception:
        return n_brains > 1


def _knowledge_plane_on(vault=None):
    """Resolve config.knowledge_plane_mode() for the board's two-plane graph: on/off
    explicit; `auto` → on when a vault is configured AND it yields at least one parseable
    note. Off (and any error) ⇒ False.

    WHY AUTO IS SAFE AS A DEFAULT: a user with no vault resolves auto → off, and their
    board is byte-identical to today's — there is no plane, no note node, no cross-plane
    edge. That is the same parity law `_interbrain_on` obeys above, and it is the reason
    this gate can default to `auto` rather than off. The gate is read-only: nothing on
    this path writes into a vault (that is `config.knowledge_graph_enabled()`, a
    different switch entirely).

    An explicit `vault` argument wins over config, the way `obsidian_sync.resolve_vault`
    lets a caller pass one directly."""
    try:
        import config as _cfg
        mode = _cfg.knowledge_plane_mode()
    except Exception:
        return False
    if mode == "on":
        return True
    if mode == "off":
        return False
    try:
        v = vault or _cfg.obsidian_vault()
        return bool(v) and bool(_knowledge.vault_notes(v))
    except Exception:
        return False


def board_notes(vault=None):
    """The knowledge plane's corpus for the board — [] unless the plane resolves ON.

    ONE GLOBAL CORPUS of the whole vault, on every render: not a per-task tree and not a
    filtered slice, because the knowledge layer is a plane in its own right rather than a
    derived view of the task layer. Guarded end-to-end — an unreadable or absent vault
    yields [], never an exception, so a vault on an unmounted drive degrades to today's
    single-plane board. Mirrors `foreign_view_models`: the gate is checked HERE, so the
    caller can hand the result straight to `build_render_graph`."""
    try:
        if not _knowledge_plane_on(vault):
            return []
        import config as _cfg
        return _knowledge.vault_notes(vault or _cfg.obsidian_vault())
    except Exception:
        return []


def _foreign_view_model(feed, ftask):
    """Map ONE foreign feed task → the board view-model dict, flagged `foreign=True` and
    read-only. Owner/handle/brain/colour come from the feed; the category/effort/status
    fields reuse the LOCAL task vocabulary so the shared _row builder renders foreign and
    local rows identically. Pure; never touches the store."""
    owner = ftask.get("owner") or feed.get("owner") or feed.get("alias") or "peer"
    uuid8 = (ftask.get("uuid8") or "")[:8]
    handle = ftask.get("handle") or ("%s-%s" % (owner, uuid8 or "?"))
    # foreign tasks carry their feed's brain field if present, else the alias itself.
    brain = ftask.get("brain") or feed.get("alias") or owner
    owner_color = feed.get("color") or _OWNER_FALLBACK_COLOR
    owner_color_dark = feed.get("color_dark") or owner_color
    # canonical feeds carry category as {key,tag,dot,hex,hex_dark}; tolerate a bare key.
    cat = ftask.get("category")
    if isinstance(cat, dict):
        color = cat.get("key") or ""
        tag = cat.get("tag") or ""
    else:
        color = cat or ""
        tag = cat_tag(color) if color else ""
    st = (ftask.get("status") or "open").lower()
    if st == "closed":
        status, sdisp = "closed", "closed"
    elif st == "active":
        status, sdisp = "active", "paused"   # foreign never shows `live` (no local session)
    else:
        status, sdisp = "open", "new"
    eff = ftask.get("effort") or ""
    effU = eff.upper()
    digest = ftask.get("digest") or {}
    signals = ftask.get("signals") or {}
    # raw normalized signal ids (feed form) — for cross-brain graph edge matching.
    sig_prs = [p for p in (signals.get("prs") or []) if p]
    sig_stories = [s for s in (signals.get("stories") or []) if s]
    return {
        "foreign": True,
        "editable": False,
        "owner": owner,
        "owner_color": owner_color,
        "owner_color_dark": owner_color_dark,
        "handle": handle,
        "brain": brain,
        "seq": None,
        "id": "foreign:%s:%s" % (owner, uuid8 or handle),
        "uuid8": uuid8,
        "title": ftask.get("title") or "",
        "full_title": ftask.get("title") or "",
        "status": status,
        "status_label": status,
        "status_display": sdisp,
        "live": False,
        "color": color,
        "tag": tag,
        "label": "",
        "overridden": False,
        "effort": eff,
        "effort_label": EFFORT_WORD.get(effU, ""),
        "effort_gauge": EFFORT_GAUGE.get(effU, EFFORT_GAUGE_EMPTY),
        "activity": rel_time(ftask.get("updated_ts")),
        "summary": "",
        "goal": (digest.get("goal") or "").strip(),
        "state": (digest.get("state") or "").strip(),
        "steps": [],
        "progress": [int(digest.get("steps_done") or 0),
                     int(digest.get("steps_total") or 0)],
        "decisions": [_dec.text(d) for d in (digest.get("decisions_tail") or [])],
        "history": [],
        "prs": [{"label": "PR %s" % parse_pr_number(p), "num": parse_pr_number(p),
                 "url": None} for p in sig_prs],
        "stories": [{"id": s} for s in sig_stories],
        "story_refs": [{"id": s, "url": None} for s in sig_stories],
        "repos": [],
        "files": [],
        # A peer that exports with trail_visibility=full carries `prompts` in its feed
        # entry (its own sync_safe stripper let them through). Pass them to the detail view
        # so "Peer detail renders whatever visibility grants" (F5.4); empty otherwise.
        "prompts_preview": list((ftask.get("prompts") or []))[:5],
        "prompts_full": list(ftask.get("prompts") or []),
        "usage": None,
        "sessions": [],
        "phases": [],
        "hubs": [],
        "session_tree": {},
        "related": {},
        "knowledge": None,
        "resume": None,
        "resume_main": None,
        "open_command": None,
        "shared_org": bool(ftask.get("shared_org")),
        "shares": list(ftask.get("shares") or []),
        # raw signal ids kept for the cross-brain graph edges (not rendered in the row).
        "signal_prs": sig_prs,
        "signal_stories": sig_stories,
    }


def foreign_view_models(data_dir=None):
    """All foreign (peer/org) view-models for the board — [] unless Interbrain
    resolves ON. Reads every canonical peer feed under feeds/{peers,demo}/, maps each
    task via _foreign_view_model, and returns them in feed-file order. Guarded end-to-end:
    a bad feed is skipped, never fatal. `data_dir` defaults to paths.data_dir()."""
    dd = data_dir or paths.data_dir()
    if not _interbrain_on(dd):
        return []
    import feeds as _feeds
    out = []
    for path in _feeds.peer_feed_files(dd):
        feed = _feeds.parse_feed_file(path)
        if not feed:
            continue
        # never treat the local brain's own feed as foreign.
        if (feed.get("kind") or "") in ("self", "archive"):
            continue
        for ftask in feed.get("tasks") or []:
            try:
                out.append(_foreign_view_model(feed, ftask))
            except Exception:
                continue
    return out
