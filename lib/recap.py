"""Weekly recap — a strictly private, local, self-contained HTML digest of the
user's week of LLM-assisted work (task 444).

PURE STDLIB. Reads only the persisted local ledger + task store (never touches a
transcript): the `session_usage` / `prompts` tables (lib/usage.py owns the write
path), the task store, the Tasktrail event stream, and — best-effort — the HUD
snapshot dir. Produces a Spotify-Wrapped-register one-pager: what you did, what it
cost, and CONCRETE guidance to use LLMs more effectively.

Privacy is the load-bearing invariant (see the module's privacy note below and the
`assert`ions in tests/test_recap.py):
  * NEVER emits raw prompt text, task summaries (only titles + #seq handles), or any
    path outside the data dir.
  * The aggregate stats dict handed to an optional curator carries counts / ratios /
    titles ONLY — the same privacy floor as the rendered HTML.
  * Output lands under <data_dir>/recaps/<YYYY-Www>.html and is machine-local: it is
    added to NO sync boundary, NO export, NO manifest.

The module is store-injected (every function that reads takes a `store`) so it is
unit-testable against a fixture backend, mirroring lib/usage.py.
"""
import html
import json
import os
import subprocess
from datetime import datetime, timedelta

import config
import paths
import pricing
import recap_guidance as guidance

# The owner-amber accent (task-station's owner hue), light / dark variants. Bars use
# this single hue for magnitude; text is NEVER drawn in it (contrast rule).
_ACCENT_LIGHT = "#cf8a22"
_ACCENT_DARK = "#b97e1f"

# Session-length floor (assistant messages) above which a save-less session is
# flagged as a context-loss risk. Deterministic heuristic knob.
_LONG_SESSION_MSGS = 40

# How many top tasks / categories the "where it went" section lists.
_TOP_TASKS = 8

# The slash features whose absence over the week seeds an "unused feature" tip.
# Detected from captured command prompts (kind='command'); text is inspected
# in-process for the feature token ONLY and never emitted.
_FEATURE_TOKENS = {
    "search": ("search",),
    "memo": ("memo",),
    "brief": ("brief",),
    "save": ("save",),          # `/todo save` — the checkpoint
}

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ------------------------------------------------------------- week bounds -----

def resolve_week(week=None, now_ts=None):
    """The [start, end) epoch bounds + canonical `YYYY-Www` label for an ISO week.

    `week` is an explicit `YYYY-Www` string (else the ISO week containing `now_ts`,
    else the current wall-clock week). Bounds are LOCAL midnight Monday → next Monday
    so weekday/hour framing matches the user's lived week. Returns
    {label, start_ts, end_ts}. Raises ValueError on an unparseable `week`."""
    if week:
        label = _normalize_week_label(week)
        year, wk = _parse_week_label(label)
        monday = datetime.fromisocalendar(year, wk, 1)
    else:
        base = datetime.fromtimestamp(now_ts) if now_ts is not None else datetime.now()
        iso = base.isocalendar()
        monday = datetime.fromisocalendar(iso[0], iso[1], 1)
        label = "%04d-W%02d" % (iso[0], iso[1])
    start = monday.timestamp()
    end = (monday + timedelta(days=7)).timestamp()
    return {"label": label, "start_ts": start, "end_ts": end}


def previous_complete_week_label(now_ts=None):
    """The `YYYY-Www` label of the last FULLY-elapsed ISO week (the natural target for
    an auto-generated 'your week in review')."""
    cur = resolve_week(now_ts=now_ts)
    prior = datetime.fromtimestamp(cur["start_ts"]) - timedelta(days=1)
    iso = prior.isocalendar()
    return "%04d-W%02d" % (iso[0], iso[1])


def _normalize_week_label(week):
    w = str(week or "").strip().upper().replace(" ", "")
    _parse_week_label(w)     # validate
    return w


def _parse_week_label(label):
    """(year, week_number) for a `YYYY-Www` label. Raises ValueError otherwise."""
    s = str(label or "").strip().upper()
    if "-W" not in s:
        raise ValueError("week must look like 2026-W29, got %r" % label)
    y, w = s.split("-W", 1)
    year, wk = int(y), int(w)
    if not (1 <= wk <= 53):
        raise ValueError("ISO week out of range: %r" % label)
    return year, wk


# ---------------------------------------------------------------- helpers ------

def recaps_dir():
    """<data_dir>/recaps — the machine-local home for rendered recaps. Never synced."""
    return os.path.join(paths.data_dir(), "recaps")


def recap_path(label):
    return os.path.join(recaps_dir(), "%s.html" % label)


def _esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def _merge_models(dst, blob):
    """Fold a stored model/sidechain blob into a working {family: {...}} accumulator,
    keyed by the SHORT family (opus/fable/sonnet/haiku/…) the recap reports."""
    for model, d in (blob or {}).items():
        fam = pricing.model_family(model)
        w = dst.setdefault(fam, {"out": 0, "in": 0, "cache_read": 0, "msgs": 0,
                                 "cost_usd": 0.0, "unpriced": False})
        w["out"] += d.get("out") or 0
        w["in"] += d.get("in") or 0
        w["cache_read"] += d.get("cache_read") or 0
        w["msgs"] += d.get("msgs") or 0
        c = d.get("cost_usd", 0.0)
        if c is None:
            w["unpriced"] = True
        else:
            w["cost_usd"] += c or 0.0


def _session_activity_ts(r):
    """The activity timestamp a session is bucketed by (last activity, else first)."""
    return r.get("last_ts") if r.get("last_ts") is not None else r.get("first_ts")


def _row_totals(r):
    """(out, cost_or_None, msgs) merged across a row's parent + subagent buckets."""
    w = {}
    _merge_models(w, r.get("models"))
    _merge_models(w, r.get("sidechain"))
    out = sum(m["out"] for m in w.values())
    msgs = sum(m["msgs"] for m in w.values())
    unpriced = any(m["unpriced"] for m in w.values())
    cost = None if unpriced else round(sum(m["cost_usd"] for m in w.values()), 6)
    return out, cost, msgs


def _merge_intervals(intervals):
    """Merge overlapping (start, end) intervals; returns a sorted disjoint list."""
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


# ---------------------------------------------------------------- collect ------

def collect(store, start_ts, end_ts, label, now_ts=None):
    """Roll the persisted ledger + task store into the privacy-safe aggregate dict
    that BOTH the renderer and the optional curator consume. Reads only stored tables
    (no transcript IO). Every value is a count / ratio / total / title — never prompt
    text, never a summary, never an out-of-data-dir path."""
    rows = [r for r in _safe(store.all_session_usage)
            if _in_week(_session_activity_ts(r), start_ts, end_ts)]
    tasks_by_id = {t.get("id"): t for t in _safe(store.all_tasks)}

    models = {}
    task_tokens = {}          # task_id -> [out, cost_or_None]
    intervals = []
    hour_weight = {}          # local hour -> cost (fallback out)
    day_weight = {}           # weekday idx -> {"cost":,"out":}
    hub_cost = worker_cost = 0.0
    session_lens = []
    total_in = total_out = total_cache = total_msgs = 0
    total_cost = 0.0
    any_unpriced = False
    sessions_meta = []

    for r in rows:
        _merge_models(models, r.get("models"))
        _merge_models(models, r.get("sidechain"))
        out, cost, msgs = _row_totals(r)
        total_out += out
        total_msgs += msgs
        # in / cache totals from the merged per-family view for the headline.
        fam_view = {}
        _merge_models(fam_view, r.get("models"))
        _merge_models(fam_view, r.get("sidechain"))
        total_in += sum(m["in"] for m in fam_view.values())
        total_cache += sum(m["cache_read"] for m in fam_view.values())
        if cost is None:
            any_unpriced = True
        else:
            total_cost += cost

        tid = r.get("task_id")
        if tid:
            agg = task_tokens.setdefault(tid, [0, 0.0, False])
            agg[0] += out
            if cost is None:
                agg[2] = True
            else:
                agg[1] += cost

        fts, lts = r.get("first_ts"), r.get("last_ts")
        if fts is not None and lts is not None and lts >= fts:
            s = max(fts, start_ts)
            e = min(lts, end_ts)
            if e > s:
                intervals.append((s, e))
                session_lens.append((e - s) / 60.0)
            mid = fts + (lts - fts) / 2.0
            hr = datetime.fromtimestamp(mid).hour
            hour_weight[hr] = hour_weight.get(hr, 0.0) + (cost if cost else out / 1e6)
        act = _session_activity_ts(r)
        if act is not None:
            wd = datetime.fromtimestamp(act).weekday()
            dw = day_weight.setdefault(wd, {"cost": 0.0, "out": 0})
            dw["cost"] += cost or 0.0
            dw["out"] += out

        role = r.get("role") or "unknown"
        if role == "worker":
            worker_cost += cost or 0.0
        elif role == "hub":
            hub_cost += cost or 0.0

        # Per-session heuristic material (NO ids, NO text): dominant family + phase,
        # the implementation ('mechanical') phase share, msg count, out + cost.
        mech_pct = _mechanical_pct(r.get("phases"))
        fam_top = _dominant_family(r.get("models"), r.get("sidechain"))
        phase_top = _dominant_phase(r.get("phases"))
        sessions_meta.append({"msgs": msgs, "family_top": fam_top,
                              "mechanical_pct": mech_pct, "cost": cost, "out": out,
                              "phase_top": phase_top, "role": role,
                              "session_id": r.get("session_id")})

    # --- prompts-derived features + save detection (counts / booleans only) ---
    prompts = _prompts_in_week(store, start_ts, end_ts)
    features = {k: False for k in _FEATURE_TOKENS}
    compact_events = 0
    save_sessions = set()
    reexplain = 0
    by_session_prompts = {}
    for p in prompts:
        kind = p.get("kind")
        sid = p.get("session_id")
        by_session_prompts.setdefault(sid, []).append(p)
        if kind == "compact":
            compact_events += 1
            continue
        low = (p.get("text") or "").lower()
        if kind == "command":
            for feat, toks in _FEATURE_TOKENS.items():
                if any(tok in low for tok in toks):
                    features[feat] = True
                    if feat == "save" and sid:
                        save_sessions.add(sid)
    # Re-explained context after compaction: a long human prompt within 10 min of a
    # compaction summary (length only — text is never inspected beyond len()).
    for sid, ps in by_session_prompts.items():
        ps_sorted = sorted(ps, key=lambda x: (x.get("ts") is None, x.get("ts") or 0))
        last_compact = None
        for p in ps_sorted:
            ts = p.get("ts")
            if p.get("kind") == "compact":
                last_compact = ts
            elif (p.get("kind") == "prompt" and last_compact is not None
                  and ts is not None and 0 <= ts - last_compact <= 600
                  and len(p.get("text") or "") >= 600):
                reexplain += 1
                last_compact = None

    for m in sessions_meta:
        m["has_save"] = m.get("session_id") in save_sessions
        m.pop("session_id", None)      # drop the id before the dict leaves this fn

    # --- tasks: touched / closed + top-by-tokens + category breakdown ---
    tasks_touched = len([tid for tid in task_tokens if tid in tasks_by_id])
    tasks_closed = 0
    for t in tasks_by_id.values():
        cts = t.get("closed_ts")
        if cts is not None and _in_week(cts, start_ts, end_ts):
            tasks_closed += 1

    top_tasks = []
    for tid, (out, cost, unp) in task_tokens.items():
        t = tasks_by_id.get(tid)
        if not t:
            continue
        top_tasks.append({
            "handle": "#%s" % (t.get("seq") if t.get("seq") is not None else tid[:6]),
            "title": t.get("title") or "Untitled task",
            "out": out,
            "cost": None if unp else round(cost, 6),
            "category": _category_label(t.get("color")),
        })
    top_tasks.sort(key=lambda d: d["out"], reverse=True)
    top_tasks = top_tasks[:_TOP_TASKS]

    cat_agg = {}
    for tid, (out, cost, unp) in task_tokens.items():
        t = tasks_by_id.get(tid)
        if not t:
            continue
        lbl = _category_label(t.get("color"))
        c = cat_agg.setdefault(lbl, {"out": 0, "cost": 0.0})
        c["out"] += out
        c["cost"] += 0.0 if unp else cost
    categories = sorted(
        ({"label": k, "out": v["out"], "cost": round(v["cost"], 6)}
         for k, v in cat_agg.items()),
        key=lambda d: d["out"], reverse=True)

    # --- model mix with fit note ---
    total_model_cost = sum(w["cost_usd"] for w in models.values() if not w["unpriced"])
    total_model_out = sum(w["out"] for w in models.values()) or 1
    model_list = []
    for fam, w in models.items():
        priced = not w["unpriced"]
        pct = ((w["cost_usd"] / total_model_cost) if (priced and total_model_cost > 0)
               else w["out"] / total_model_out)
        model_list.append({
            "family": fam, "out": w["out"], "msgs": w["msgs"],
            "cost": round(w["cost_usd"], 6) if priced else None,
            "pct": pct,
        })
    model_list.sort(key=lambda d: (d["cost"] or 0, d["out"]), reverse=True)

    # --- work patterns ---
    merged = _merge_intervals(intervals)
    active_hours = round(sum(e - s for s, e in merged) / 3600.0, 1)
    longest_focus_min = round(max((e - s for s, e in merged), default=0) / 60.0, 1)
    busiest = _busiest_day(day_weight)
    peak_hour = max(hour_weight, key=hour_weight.get) if hour_weight else None
    deleg_total = hub_cost + worker_cost
    delegation_pct = (worker_cost / deleg_total) if deleg_total > 0 else 0.0
    memo_count, memo_ack_median = _memo_turnaround(tasks_by_id.values(), start_ts, end_ts)

    # --- observed work-type mix (for the model-role matrix delta) ---
    # Bucket each session by its dominant phase (+ worker role) → matrix work_type,
    # accumulating output tokens and a token-weighted dominant capability class.
    wt_acc = {}
    for m in sessions_meta:
        wtype = guidance.work_type_for(m.get("phase_top"), m.get("role"))
        cls = guidance.class_of_family(m.get("family_top"))
        b = wt_acc.setdefault(wtype, {"out": 0, "sessions": 0, "class_out": {}})
        b["out"] += m.get("out") or 0
        b["sessions"] += 1
        b["class_out"][cls] = b["class_out"].get(cls, 0) + (m.get("out") or 0)
    work_types = {}
    for wtype, b in wt_acc.items():
        observed_class = (max(b["class_out"], key=b["class_out"].get)
                          if b["class_out"] else "unknown")
        work_types[wtype] = {"out": b["out"], "sessions": b["sessions"],
                             "observed_class": observed_class}

    # --- eco: processed tokens (in+out+cache) bucketed by capability class ---
    token_by_class = {}
    for fam, w in models.items():
        cls = guidance.class_of_family(fam)
        token_by_class[cls] = (token_by_class.get(cls, 0)
                               + w["in"] + w["out"] + w["cache_read"])
    eco = guidance.eco_estimate(token_by_class)

    return {
        "week": label,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "generated_ts": now_ts,
        "totals": {
            "cost_usd": None if any_unpriced and total_cost == 0 else round(total_cost, 2),
            "any_unpriced": any_unpriced,
            "tokens_in": total_in,
            "tokens_out": total_out,
            "tokens_cache_read": total_cache,
            "msgs": total_msgs,
            "sessions": len(rows),
            "active_hours": active_hours,
            "longest_focus_min": longest_focus_min,
            "tasks_touched": tasks_touched,
            "tasks_closed": tasks_closed,
            "busiest_day": busiest,
        },
        # Cost is EQUIVALENCE, not spend: the derived $ is what this work would cost at
        # API list prices — a flat-rate seat does not pay per token.
        "cost_equivalence": {
            "api_list_usd": None if any_unpriced and total_cost == 0 else round(total_cost, 2),
            "any_unpriced": any_unpriced,
        },
        "eco": {
            "version": guidance.ECO_VERSION,
            "processed_tokens": sum(token_by_class.values()),
            "token_by_class": token_by_class,
            "kwh": list(eco["kwh"]),
            "co2_kg": list(eco["co2_kg"]),
            "water_l": list(eco["water_l"]),
        },
        "matrix_version": guidance.MATRIX_VERSION,
        "work_types": work_types,
        "models": model_list,
        "tasks": top_tasks,
        "categories": categories,
        "patterns": {
            "hub_cost": round(hub_cost, 2),
            "worker_cost": round(worker_cost, 2),
            "delegation_pct": delegation_pct,
            "session_len_median_min": round(_median(session_lens), 1) if session_lens else 0.0,
            "session_len_mean_min": round(sum(session_lens) / len(session_lens), 1) if session_lens else 0.0,
            "peak_hour": peak_hour,
            "memo_count": memo_count,
            "memo_ack_median_min": memo_ack_median,
        },
        "features": {**features, "compact_events": compact_events, "reexplain": reexplain},
        "sessions_meta": sessions_meta,
        "rate_limit_touches": _rate_limit_touches(start_ts, end_ts),
    }


def has_data(agg):
    """True when the week has any activity worth a full recap (else the empty-week
    page is rendered)."""
    t = agg.get("totals") or {}
    return bool(t.get("sessions") or t.get("tokens_out") or t.get("tasks_touched"))


# --------------------------------------------------------- collect helpers -----

def _safe(fn):
    try:
        return fn() or []
    except Exception:
        return []


def _in_week(ts, start_ts, end_ts):
    return ts is not None and start_ts <= ts < end_ts


def _prompts_in_week(store, start_ts, end_ts):
    """Prompts whose ts lands in the week, via the store's window read when present,
    else degrade to [] (feature/heuristic detection simply goes quiet)."""
    getter = getattr(store, "prompts_in_window", None)
    if not callable(getter):
        return []
    try:
        return getter(start_ts, end_ts) or []
    except Exception:
        return []


def _mechanical_pct(phases):
    """The 'implementation' (mechanical edit) phase's share of a session's output
    tokens — the signal a top-tier model may be doing rote work. 0.0 when unknown."""
    if not isinstance(phases, dict):
        return 0.0
    total = 0
    impl = 0
    for name, d in phases.items():
        if not isinstance(d, dict):
            continue
        o = d.get("out") or 0
        total += o
        if name == "implementation":
            impl += o
    return (impl / total) if total else 0.0


def _dominant_phase(phases):
    """The work phase carrying the most output tokens in a session, or None. Skips the
    `__v` version stamp and any non-phase key."""
    if not isinstance(phases, dict):
        return None
    best = None
    best_out = -1
    for name, d in phases.items():
        if name == "__v" or not isinstance(d, dict):
            continue
        o = d.get("out") or 0
        if o > best_out:
            best, best_out = name, o
    return best


def _dominant_family(models, sidechain):
    w = {}
    _merge_models(w, models)
    _merge_models(w, sidechain)
    if not w:
        return None
    return max(w, key=lambda f: w[f]["out"])


def _category_label(color):
    """The human category label for a task color key, degrading to the raw key /
    'uncategorized' — never raises (categories is import-heavy, so guard it)."""
    if not color:
        return "uncategorized"
    try:
        import categories
        return categories.label(color) or color
    except Exception:
        return color


def _busiest_day(day_weight):
    if not day_weight:
        return None
    wd = max(day_weight, key=lambda d: (day_weight[d]["cost"], day_weight[d]["out"]))
    return {"day": _WEEKDAYS[wd], "cost": round(day_weight[wd]["cost"], 2),
            "out": day_weight[wd]["out"]}


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _memo_turnaround(tasks, start_ts, end_ts):
    """(count, median-ack-minutes|None) over memos SENT in the week that got an ack —
    a proxy for two-machine correspondence responsiveness."""
    turns = []
    count = 0
    for t in tasks:
        for m in (t.get("memos") or []):
            mts = m.get("ts")
            if not _in_week(mts, start_ts, end_ts):
                continue
            count += 1
            acks = [a.get("ts") for a in (m.get("acks") or []) if a.get("ts") is not None]
            if acks:
                turns.append((min(acks) - mts) / 60.0)
    median = round(_median(turns), 1) if turns else None
    return count, median


def _rate_limit_touches(start_ts, end_ts):
    """Best-effort count of rate-limit touches from HUD snapshots in <data_dir>/hud/.
    Returns None (not 0) when the HUD dir is absent/unparseable so the recap can tell
    'no touches' from 'no data'. Never raises."""
    d = os.path.join(paths.data_dir(), "hud")
    if not os.path.isdir(d):
        return None
    touches = 0
    seen_any = False
    try:
        names = os.listdir(d)
    except OSError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, ValueError):
            continue
        seen_any = True
        ts = snap.get("ts") or snap.get("updated_ts")
        if ts is not None and not _in_week(ts, start_ts, end_ts):
            continue
        # A touch is any recorded limit/throttle flag the HUD may have stored; we
        # accept a few shapes and skip gracefully if none are present.
        if snap.get("rate_limited") or snap.get("throttled") or snap.get("limit_touch"):
            touches += 1
    return touches if seen_any else None


# -------------------------------------------------------- deterministic tips ---

def deterministic_tips(agg):
    """This week's flags — observation → suggestion → exact next action. Each is a
    {observation, suggestion, command, feature?} dict. Fires only on real signal (a
    clean week yields few or zero). Every tip that cites a task-station feature is
    filtered so a MODEL-invoked feature is NEVER put in front of a human (directive #2):
    the tip carries a `feature` key and a final guard drops any that slips through."""
    tips = []
    totals = agg.get("totals") or {}
    patterns = agg.get("patterns") or {}
    features = agg.get("features") or {}
    metas = agg.get("sessions_meta") or []
    work_types = agg.get("work_types") or {}

    # 1. Model-fit, driven by the matrix — the biggest OVER-powered work type.
    over = []
    for wtype, obs in work_types.items():
        row = guidance.matrix_row(wtype)
        if not row or (obs.get("out") or 0) <= 0:
            continue
        verdict, phrase = guidance.fit_delta(obs.get("observed_class"), row)
        if verdict == "over":
            over.append((obs["out"], wtype, obs, row, phrase))
    if over:
        over.sort(reverse=True)
        _, wtype, obs, row, phrase = over[0]
        target = guidance.recommended_classes(row)[0]
        tips.append({
            "observation": "You ran the %s tier on %s (%s of output) — %s."
                           % (guidance.CLASS_LABEL.get(obs["observed_class"], "?"),
                              row["title"].lower(), _fmt_tokens(obs["out"]), phrase),
            "suggestion": row["why"] + " Recommended: %s, %s effort."
                          % (guidance.tier_label(row), row["effort"]),
            "command": "/model %s" % guidance.example_model_for_class(target),
        })

    # 2. Long sessions with no checkpoint → context-loss risk.
    long_nosave = [m for m in metas
                   if (m.get("msgs") or 0) >= _LONG_SESSION_MSGS and not m.get("has_save")]
    if long_nosave:
        tips.append({
            "observation": "%d long session(s) (≥%d messages) ended without a "
                           "checkpoint." % (len(long_nosave), _LONG_SESSION_MSGS),
            "suggestion": "Without a checkpoint, auto-compaction can drop context you "
                          "then re-explain. Save before you get deep, or let the Stop "
                          "hook nudge you automatically.",
            "feature": "save",
        })

    # 3. Re-explaining context right after a compaction.
    if (features.get("reexplain") or 0) >= 1:
        tips.append({
            "observation": "%d time(s) you sent a long prompt within 10 min of a "
                           "compaction — likely re-explaining lost context."
                           % features["reexplain"],
            "suggestion": "A pre-compaction checkpoint preserves the digest so the "
                          "next session resumes without a re-brief.",
            "feature": "save",
        })

    # 4. Cost outlier — one session's API-list value dwarfs the rest.
    costs = sorted((m["cost"] for m in metas if m.get("cost")), reverse=True)
    if len(costs) >= 3 and costs[0] >= 1.0:
        med = _median(costs)
        if med > 0 and costs[0] >= 3 * med:
            tips.append({
                "observation": "One session was worth ~$%.2f at API list prices — "
                               "%.1f× your median session (~$%.2f)."
                               % (costs[0], costs[0] / med, med),
                "suggestion": "Long single sessions re-send the whole thread every turn. "
                              "Split large tasks and checkpoint so a fresh session "
                              "starts lean.",
                "feature": "save",
            })

    # 5. Unused high-leverage features — HUMAN-recommendable ones only (a model-
    #    invoked feature like search is deliberately excluded here).
    if (totals.get("tasks_touched") or 0) >= 3:
        blurbs = {
            "memo": "Memos leave durable, ack-able notes between sessions/machines so "
                    "context survives a handoff.",
            "brief": "A brief renders a shareable house-style one-pager for a task in "
                     "seconds.",
        }
        for f in ("memo", "brief"):
            if not features.get(f) and guidance.is_human_recommendable(f):
                tips.append({
                    "observation": "You didn't use `%s` this week." % f,
                    "suggestion": blurbs[f],
                    "feature": f,
                })
                break

    # 6. No delegation despite a busy week.
    if (patterns.get("delegation_pct") or 0) == 0 and (totals.get("sessions") or 0) >= 8:
        tips.append({
            "observation": "Every session ran in the hub — no work was delegated.",
            "suggestion": "Repo-specific edits/builds/tests run cleaner in a delegated "
                          "worker (its own worktree + project machinery), freeing the "
                          "hub for coordination.",
            "feature": "delegate",
        })

    return [t for t in tips if _resolve_tip_action(t)]


def _resolve_tip_action(tip):
    """Resolve a tip's `command` from its `feature` via the registry when not set
    explicitly, and GUARD directive #2: a tip citing a MODEL-invoked feature is
    dropped (returns False) so it never reaches a human. A tip with no feature keeps
    its literal command. Returns True to keep the tip."""
    feat = tip.get("feature")
    if not feat:
        return bool(tip.get("command") or tip.get("suggestion"))
    if not guidance.is_human_recommendable(feat):
        return False                      # model-invoked → never recommend to a human
    if not tip.get("command"):
        tip["command"] = guidance.human_action(feat) or ""
    return True


def build_strategy():
    """The universal LLM strategy practices, resolved for a HUMAN reader. A practice
    citing a human/both feature shows the exact command; one citing a MODEL feature
    shows NO command (via_assistant=True → 'ask your assistant') so a model-invoked
    tool is never presented as a 'run this'."""
    out = []
    for p in guidance.STRATEGY_PRACTICES:
        feat = p.get("feature")
        entry = {"key": p["key"], "title": p["title"], "body": p["body"],
                 "feature": feat, "command": None, "via_assistant": False}
        if feat:
            if guidance.is_human_recommendable(feat):
                entry["command"] = guidance.human_action(feat)
            else:
                entry["via_assistant"] = True
        out.append(entry)
    return out


def build_matrix_rows(agg):
    """The model-role matrix rows, each annotated with the observed-vs-recommended
    delta when the user did that KIND of work this week. `observed` is None for a row
    with no matching work this week (rendered as plain reference)."""
    work_types = agg.get("work_types") or {}
    rows = []
    for row in guidance.MODEL_ROLE_MATRIX:
        obs = work_types.get(row["work_type"])
        observed = None
        if obs and (obs.get("out") or 0) > 0:
            verdict, phrase = guidance.fit_delta(obs.get("observed_class"), row)
            observed = {
                "class": obs.get("observed_class"),
                "class_label": guidance.CLASS_LABEL.get(obs.get("observed_class"), "unknown"),
                "out": obs["out"], "sessions": obs["sessions"],
                "verdict": verdict, "phrase": phrase,
            }
        rows.append({"row": row, "tier_label": guidance.tier_label(row),
                     "observed": observed})
    return rows


# --------------------------------------------------------------- curator -------

def curator_tips(cmd, agg, timeout=20):
    """Pipe the PRIVACY-SAFE aggregate dict (JSON, no prompt text) through a
    user-configured curator command's stdin and parse up to 3 tailored tips from its
    stdout. A tip is {observation, suggestion, command}. Fully defensive: any
    failure, timeout, or malformed output yields [] (the recap still renders with the
    deterministic tips). Only ever called when `cmd` is configured (default OFF)."""
    if not cmd:
        return []
    payload = json.dumps(_curator_payload(agg), ensure_ascii=False)
    try:
        proc = subprocess.run(cmd, shell=True, input=payload, capture_output=True,
                              text=True, timeout=timeout)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return _parse_curator_out(proc.stdout)


def _curator_payload(agg):
    """The exact aggregate subset handed to the curator — no session_meta ids, no
    text, no paths. A deliberate allowlist so a curator NEVER sees anything private."""
    return {
        "week": agg.get("week"),
        "totals": agg.get("totals"),
        "cost_equivalence": agg.get("cost_equivalence"),
        "eco": agg.get("eco"),
        "matrix_version": agg.get("matrix_version"),
        "work_types": agg.get("work_types"),
        "models": agg.get("models"),
        "tasks": agg.get("tasks"),
        "categories": agg.get("categories"),
        "patterns": agg.get("patterns"),
        "features": agg.get("features"),
    }


def _parse_curator_out(text):
    try:
        data = json.loads(text or "")
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("tips") or data.get("guidance") or []
    if not isinstance(data, list):
        return []
    out = []
    for item in data[:3]:
        if not isinstance(item, dict):
            continue
        obs = str(item.get("observation") or "").strip()
        sug = str(item.get("suggestion") or "").strip()
        cmd = str(item.get("command") or "").strip()
        if obs or sug:
            out.append({"observation": obs, "suggestion": sug, "command": cmd})
    return out


# --------------------------------------------------------------- generate ------

def generate(store, week=None, now_ts=None, run_curator=True):
    """Collect → render → write <data_dir>/recaps/<label>.html. Returns
    {path, label, aggregates, tips, curator, wrote}. `run_curator` gates the opt-in
    curator (also globally OFF unless `recap_curator_cmd` is set)."""
    wk = resolve_week(week, now_ts=now_ts)
    agg = collect(store, wk["start_ts"], wk["end_ts"], wk["label"], now_ts=now_ts)
    tips = deterministic_tips(agg)
    curator = []
    cmd = config.recap_curator_cmd()
    if run_curator and cmd:
        curator = curator_tips(cmd, agg)
    html_doc = render(agg, tips, curator)
    out = recap_path(wk["label"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return {"path": out, "label": wk["label"], "aggregates": agg,
            "tips": tips, "curator": curator, "wrote": True}


# ------------------------------------------------------- auto-weekly throttle --

def _stamp_path():
    return os.path.join(recaps_dir(), ".last-auto")


def _read_stamp():
    try:
        with open(_stamp_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_stamp(label):
    os.makedirs(recaps_dir(), exist_ok=True)
    tmp = _stamp_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(label)
    os.replace(tmp, _stamp_path())


def auto_generate_if_due(store, now_ts=None):
    """The hook entry point: generate the previous complete week's recap AT MOST ONCE
    per week, mirroring the throttled-stamp discipline (compare a stored week label,
    write it on success). Returns the written path or None. Fully fail-open — gated on
    `recap_enabled` AND usage tracking, and swallows every error so the Stop hook is
    never disrupted and costs zero tokens (unless a curator is configured)."""
    try:
        if not config.recap_enabled() or not config.usage_tracking_enabled():
            return None
        target = previous_complete_week_label(now_ts=now_ts)
        if _read_stamp() == target:
            return None                          # already generated this week — cheap no-op
        result = generate(store, week=target, now_ts=now_ts)
        _write_stamp(target)
        return result["path"]
    except Exception:
        return None


# ---------------------------------------------------------------- render -------

def _fmt_tokens(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 10_000:
        return "%.0fK" % (n / 1000)
    if n >= 1000:
        return "%.1fK" % (n / 1000)
    return "{:,}".format(n)


def _fmt_pct(p):
    return "%.0f%%" % (100 * (p or 0))


def _bar(pct, cls="bar"):
    w = max(0.0, min(1.0, pct or 0)) * 100
    return ('<div class="%s"><i style="width:%.1f%%"></i></div>' % (cls, w))


def _num(s):
    return '<span class="num">%s</span>' % _esc(s)


def render(agg, tips=None, curator=None):
    """Render the aggregate dict to the self-contained house-style recap HTML
    (light + dark). Empty sections are skipped — never an empty shell. Pure string
    assembly; no IO."""
    tips = tips or []
    curator = curator or []
    week = agg.get("week") or ""
    parts = []
    parts.append(_hero(agg))
    parts.append(_section_headline(agg))
    parts.append(_section_cost(agg))
    parts.append(_section_models(agg))
    parts.append(_section_where(agg))
    parts.append(_section_patterns(agg))
    # Guidance — STRATEGY FIRST (universal best practice), then the model-role matrix,
    # then this week's concrete flags. Tooling only appears where it serves a practice.
    # Skipped wholesale on a quiet week (nothing observed to guide against).
    if has_data(agg):
        parts.append(_section_strategy())
        parts.append(_section_matrix(agg))
        parts.append(_section_flags(tips, curator))
    parts.append(_section_gaps(agg))
    parts.append(_FOOTER)
    body = "\n".join(p for p in parts if p)
    return _TEMPLATE.replace("{TITLE}", _esc("Weekly recap · %s" % week)) \
                    .replace("{ACCENT_LIGHT}", _ACCENT_LIGHT) \
                    .replace("{ACCENT_DARK}", _ACCENT_DARK) \
                    .replace("{BODY}", body)


def _hero(agg):
    week = _esc(agg.get("week") or "")
    start = agg.get("start_ts")
    sub = ""
    if start is not None:
        d0 = datetime.fromtimestamp(agg["start_ts"])
        d1 = datetime.fromtimestamp(agg["end_ts"]) - timedelta(seconds=1)
        sub = "%s %d – %s %d, %d" % (d0.strftime("%b"), d0.day,
                                     d1.strftime("%b"), d1.day, d1.year)
    empty = "" if has_data(agg) else (
        '<p class="quiet">A quiet week — no tracked LLM activity. Nothing to review.</p>')
    return ('<header>\n'
            '  <div class="eyebrow">Your week in review</div>\n'
            '  <h1>%s</h1>\n'
            '  <p class="sub">%s</p>\n'
            '  <button class="themetoggle" type="button" '
            'onclick="__ttl()" aria-label="Toggle theme">◐</button>\n'
            '%s</header>') % (week, _esc(sub), empty)


def _tile(value, label, hint=""):
    hint_html = ('<div class="hint">%s</div>' % _esc(hint)) if hint else ""
    return ('<div class="tile"><div class="v">%s</div>'
            '<div class="l">%s</div>%s</div>') % (_esc(value), _esc(label), hint_html)


def _section_headline(agg):
    t = agg.get("totals") or {}
    if not has_data(agg):
        return ""
    tiles = []
    cost = t.get("cost_usd")
    cost_str = ("—" if cost is None else "~$%.2f" % cost) + (" *" if t.get("any_unpriced") else "")
    tiles.append(_tile(cost_str, "API-list value", "equivalent, not spend"))
    tiles.append(_tile(_fmt_tokens(t.get("tokens_out")), "output tokens",
                       "%s in · %s cached" % (_fmt_tokens(t.get("tokens_in")),
                                              _fmt_tokens(t.get("tokens_cache_read")))))
    tiles.append(_tile("%.1fh" % (t.get("active_hours") or 0), "active hours",
                       "longest focus %.0f min" % (t.get("longest_focus_min") or 0)))
    tiles.append(_tile(str(t.get("tasks_touched") or 0), "tasks touched",
                       "%d closed" % (t.get("tasks_closed") or 0)))
    busiest = t.get("busiest_day")
    if busiest:
        tiles.append(_tile(busiest["day"], "busiest day",
                           "%s that day" % _fmt_tokens(busiest.get("out"))))
    tiles.append(_tile(str(t.get("sessions") or 0), "sessions",
                       "%d messages" % (t.get("msgs") or 0)))
    star = ('<p class="note">* some models were unpriced; the value excludes them.</p>'
            if t.get("any_unpriced") else "")
    return ('%s\n<div class="tiles">\n%s\n</div>%s'
            % (_h2("The headline"), "\n".join(tiles), star))


def _section_models(agg):
    models = agg.get("models") or []
    if not models:
        return ""
    fit = _model_fit_note(agg)
    rows = []
    for m in models:
        cost = "—" if m["cost"] is None else "$%.2f" % m["cost"]
        rows.append(
            '<div class="row">'
            '<div class="rl">%s</div>%s'
            '<div class="rv">%s · %s · %s msgs</div></div>'
            % (_esc(m["family"]), _bar(m["pct"]),
               _num(_fmt_pct(m["pct"])), _num(cost), _num("{:,}".format(m["msgs"]))))
    fit_html = ('<p class="note">%s</p>' % _esc(fit)) if fit else ""
    return "%s\n%s\n%s" % (_h2("Model mix"), "\n".join(rows), fit_html)


def _model_fit_note(agg):
    metas = agg.get("sessions_meta") or []
    mech = [m for m in metas if m.get("family_top") in ("opus", "fable")
            and (m.get("mechanical_pct") or 0) >= 0.6]
    if mech:
        return ("%d session(s) spent most of their tokens on mechanical edits while "
                "on a top-tier model — a cheaper tier would read the same."
                % len(mech))
    return ""


def _section_where(agg):
    tasks = agg.get("tasks") or []
    cats = agg.get("categories") or []
    if not tasks and not cats:
        return ""
    blocks = [_h2("Where it went")]
    if tasks:
        top = max((x["out"] for x in tasks), default=1) or 1
        rows = []
        for x in tasks:
            cost = "—" if x["cost"] is None else "$%.2f" % x["cost"]
            rows.append(
                '<div class="row"><div class="rl">'
                '<span class="handle">%s</span> %s</div>%s'
                '<div class="rv">%s · %s</div></div>'
                % (_esc(x["handle"]), _esc(x["title"]), _bar(x["out"] / top),
                   _num(_fmt_tokens(x["out"])), _num(cost)))
        blocks.append('<h3>Top tasks by tokens</h3>\n%s' % "\n".join(rows))
    if cats:
        top = max((c["out"] for c in cats), default=1) or 1
        rows = []
        for c in cats:
            rows.append(
                '<div class="row"><div class="rl">%s</div>%s'
                '<div class="rv">%s</div></div>'
                % (_esc(c["label"]), _bar(c["out"] / top), _num(_fmt_tokens(c["out"]))))
        blocks.append('<h3>By category</h3>\n%s' % "\n".join(rows))
    return "\n".join(blocks)


def _section_patterns(agg):
    p = agg.get("patterns") or {}
    t = agg.get("totals") or {}
    if not has_data(agg):
        return ""
    items = []
    if p.get("session_len_median_min"):
        items.append(("Typical session", "%.0f min (median), %.0f min (mean)"
                      % (p["session_len_median_min"], p["session_len_mean_min"])))
    if p.get("peak_hour") is not None:
        items.append(("Peak hour", "%02d:00–%02d:00" % (p["peak_hour"], (p["peak_hour"] + 1) % 24)))
    if t.get("longest_focus_min"):
        items.append(("Longest focus streak", "%.0f min" % t["longest_focus_min"]))
    deleg = p.get("delegation_pct") or 0
    if (p.get("hub_cost") or 0) or (p.get("worker_cost") or 0):
        items.append(("Delegation vs hub", "%s delegated ($%.2f worker · $%.2f hub)"
                      % (_fmt_pct(deleg), p.get("worker_cost") or 0, p.get("hub_cost") or 0)))
    if p.get("memo_count"):
        ack = ("%.0f min median ack" % p["memo_ack_median_min"]
               if p.get("memo_ack_median_min") is not None else "none acked")
        items.append(("Memos sent", "%d (%s)" % (p["memo_count"], ack)))
    if not items:
        return ""
    rows = "\n".join('<div class="kv"><div class="k">%s</div><div class="val">%s</div></div>'
                     % (_esc(k), _esc(v)) for k, v in items)
    return "%s\n<div class=\"kvs\">\n%s\n</div>" % (_h2("Work patterns"), rows)


def _fmt_range(lo, hi, unit, sig=2):
    """A DIRECTIONAL '<lo>–<hi> <unit>' range with sensible precision, never false
    precision (small values keep 2 sig figs, larger ones round to whole/one decimal)."""
    def one(v):
        v = float(v or 0)
        if v == 0:
            return "0"
        if v < 1:
            return ("%.3g" % v)
        if v < 10:
            return ("%.1f" % v)
        return ("%.0f" % v)
    return "%s–%s %s" % (one(lo), one(hi), unit)


def _section_cost(agg):
    """Cost as EQUIVALENCE, never spend: what the week's work would cost at API list
    prices, plus a DIRECTIONAL energy/CO2/water estimate with a cited assumptions
    table. All ranges — deliberately order-of-magnitude, labelled as estimates."""
    if not has_data(agg):
        return ""
    ce = agg.get("cost_equivalence") or {}
    eco = agg.get("eco") or {}
    blocks = [_h2("What it cost — in equivalents")]

    val = ce.get("api_list_usd")
    val_str = "—" if val is None else "~$%.2f" % val
    lead = ("This week's work was worth <b>%s</b> at API list prices%s — an "
            "equivalence, not a bill. A flat-rate seat does not pay per token; this is "
            "the marginal API value of the tokens you moved." % (
                val_str, " (excludes unpriced models)" if ce.get("any_unpriced") else ""))
    blocks.append('<p class="lead">%s</p>' % lead)

    kwh = eco.get("kwh") or [0, 0]
    co2 = eco.get("co2_kg") or [0, 0]
    water = eco.get("water_l") or [0, 0]
    eco_tiles = "\n".join([
        _tile(_fmt_range(kwh[0], kwh[1], "kWh"), "energy"),
        _tile(_fmt_range(co2[0], co2[1], "kg"), "CO₂e"),
        _tile(_fmt_range(water[0], water[1], "L"), "water"),
    ])
    blocks.append('<h3>Environmental equivalent — DIRECTIONAL estimate</h3>')
    blocks.append('<p class="note">Order-of-magnitude only, from %s processed tokens. '
                  'Ranges, not precision — real figures depend on hardware, batching, '
                  'and context length. Assumptions (v%s):</p>'
                  % (_fmt_tokens(eco.get("processed_tokens")), _esc(eco.get("version"))))
    blocks.append('<div class="tiles">\n%s\n</div>' % eco_tiles)

    rows = "\n".join(
        '<div class="kv"><div class="k">%s</div><div class="val">%s</div></div>'
        '<div class="assum">%s</div>'
        % (_esc(c["factor"]), _esc(c["value"]), _esc(c["note"]))
        for c in guidance.ECO_CITATIONS)
    blocks.append('<div class="kvs assumptions">\n%s\n</div>' % rows)
    return "\n".join(blocks)


def _section_strategy():
    """Universal LLM strategy — the HEADLINE of the guidance. Natural language,
    applicable in any chat. A cited tool appears only where it serves the practice;
    a model-invoked feature is framed 'ask your assistant', never 'run this'."""
    blocks = [_h2("Getting more from any LLM"),
              '<p class="note">Universal practice first — these apply in any assistant, '
              'not just here. Tools are cited only where they serve a habit.</p>']
    for p in build_strategy():
        aid = ""
        if p["command"]:
            aid = ('<div class="cmd">here: <code>%s</code></div>' % _esc(p["command"]))
        elif p["via_assistant"]:
            aid = ('<div class="cmd via">Your assistant can do this — just ask '
                   '(it runs <code>%s</code> for you).</div>'
                   % _esc((guidance.INVOKED_BY.get(p["feature"]) or {}).get("what") or p["feature"]))
        blocks.append('<div class="practice"><div class="obs">%s</div>'
                      '<div class="sug">%s</div>%s</div>'
                      % (_esc(p["title"]), _esc(p["body"]), aid))
    return "\n".join(blocks)


def _section_matrix(agg):
    """The versioned model-role matrix, highlighting the rows relevant to the observed
    week (observed tier vs recommended, with the delta) and listing the rest as
    reference."""
    rows = build_matrix_rows(agg)
    observed = [r for r in rows if r["observed"]]
    reference = [r for r in rows if not r["observed"]]
    blocks = [_h2("Match the model to the work")]
    blocks.append('<p class="note">%s — matrix v%s.</p>'
                  % (_esc(guidance.MODEL_GENERATION_NOTE), _esc(guidance.MATRIX_VERSION)))

    if observed:
        blocks.append('<h3>This week — observed vs recommended</h3>')
        for r in observed:
            row, ob = r["row"], r["observed"]
            badge = {"over": '<span class="tag over">over-powered</span>',
                     "under": '<span class="tag under">under-powered</span>',
                     "on": '<span class="tag on">on target</span>'}.get(ob["verdict"], "")
            blocks.append(
                '<div class="mrow"><div class="mhead">%s %s</div>'
                '<div class="sug">%s</div>'
                '<div class="mdelta">Recommended: <b>%s</b>, %s effort · '
                'you ran <b>%s</b> (%s of output over %d session%s) — %s</div></div>'
                % (_esc(row["title"]), badge, _esc(row["why"]),
                   _esc(r["tier_label"]), _esc(row["effort"]),
                   _esc(ob["class_label"]), _fmt_tokens(ob["out"]), ob["sessions"],
                   "" if ob["sessions"] == 1 else "s", _esc(ob["phrase"])))

    if reference:
        refrows = "\n".join(
            '<div class="kv"><div class="k">%s <span class="ex">%s</span></div>'
            '<div class="val">%s · %s effort</div></div>'
            % (_esc(r["row"]["title"]), _esc(r["row"]["examples"]),
               _esc(r["tier_label"]), _esc(r["row"]["effort"]))
            for r in reference)
        head = '<h3>Reference — the rest of the matrix</h3>' if observed else ""
        blocks.append('%s\n<div class="kvs">\n%s\n</div>' % (head, refrows))
    return "\n".join(blocks)


def _section_flags(tips, curator):
    """This week's concrete flags (deterministic + optional curator). Comes AFTER the
    universal strategy + matrix — tooling here is a specific next action, never the
    headline."""
    if not tips and not curator:
        return ""
    blocks = [_h2("This week's flags")]

    def _tip_html(tp):
        cmd = ('<div class="cmd"><code>%s</code></div>' % _esc(tp["command"])) if tp.get("command") else ""
        return ('<div class="tip"><div class="obs">%s</div>'
                '<div class="sug">%s</div>%s</div>'
                % (_esc(tp.get("observation")), _esc(tp.get("suggestion")), cmd))

    if tips:
        blocks.append("\n".join(_tip_html(tp) for tp in tips))
    if curator:
        blocks.append('<h3>Tailored (curator)</h3>')
        blocks.append("\n".join(_tip_html(tp) for tp in curator))
    return "\n".join(blocks)


def _section_gaps(agg):
    """The 'what would sharpen next week' footnote — honest about what the recap
    can't yet see, framed as proposed usage-ledger columns."""
    if not has_data(agg):
        return ""
    rlt = agg.get("rate_limit_touches")
    gaps = [
        "A per-session <b>settings snapshot</b> (model / effort / fast-mode at session "
        "start) would let the model-fit tips name the exact wrong-tier sessions.",
        "<b>Compaction counts</b> per session would sharpen the re-explained-context "
        "signal beyond the prompt-timing heuristic.",
        "A per-session <b>file-edit frequency</b> counter would let us flag "
        "<b>retry loops</b> (the same file rewritten many times) — not derivable from "
        "today's ledger.",
        "<b>Permission-prompt counts</b> would surface friction worth allowlisting.",
    ]
    if rlt is None:
        gaps.append("<b>Rate-limit touches</b> weren't found in the HUD snapshots this "
                    "week — enabling the HUD would add a throttling signal.")
    intro = ("These are proposed additive usage-ledger columns — none exist yet, so "
             "the recap notes them rather than guessing.")
    lis = "\n".join("  <li>%s</li>" % g for g in gaps)
    return ('%s\n<p class="note">%s</p>\n<ul class="gaps">\n%s\n</ul>'
            % (_h2("What would sharpen next week"), intro, lis))


def _h2(title):
    return "<h2>%s</h2>" % _esc(title)


_FOOTER = (
    '<footer class="foot">Private to this machine. Never synced, never shared, '
    'excluded from every boundary.</footer>')


# The frozen self-contained template. Light default; dark via prefers-color-scheme
# AND an explicit [data-theme] override (the toggle). One <style>, one tiny inline
# script — NO external assets (same needle discipline as the board/brief).
_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="ts-recap" content="1">
<title>{TITLE}</title>
<style>
  :root{
    --bg:#f7f8fa; --card:#ffffff; --ink:#1d2430; --muted:#66707e; --line:#e6eaf0;
    --track:#edf0f5; --accent:{ACCENT_LIGHT}; --code-bg:#eef1f6;
    --good:#1f8a4c; --good-bg:#edf7f0; --bad:#c0392b; --bad-bg:#fbeeec;
    --warn:#b7791f; --warn-bg:#fbf4e5;
  }
  @media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
      --bg:#12151b; --card:#1a1e26; --ink:#e7ecf3; --muted:#9aa4b2; --line:#2a2f3a;
      --track:#232833; --accent:{ACCENT_DARK}; --code-bg:#232833;
      --good:#4cc38a; --good-bg:#16321f; --bad:#e06a5c; --bad-bg:#3a1f1c;
      --warn:#d6a54a; --warn-bg:#332812;
    }
  }
  :root[data-theme="dark"]{
    --bg:#12151b; --card:#1a1e26; --ink:#e7ecf3; --muted:#9aa4b2; --line:#2a2f3a;
    --track:#232833; --accent:{ACCENT_DARK}; --code-bg:#232833;
    --good:#4cc38a; --good-bg:#16321f; --bad:#e06a5c; --bad-bg:#3a1f1c;
    --warn:#d6a54a; --warn-bg:#332812;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    font-variant-numeric:tabular-nums;}
  .num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;}
  .wrap{max-width:820px;margin:0 auto;padding:44px 24px 80px;}
  header{position:relative;}
  .eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);font-weight:700;}
  h1{font-size:30px;line-height:1.15;margin:4px 0 2px;letter-spacing:-.01em;}
  .sub{color:var(--muted);font-size:14px;margin:0 0 8px;}
  .quiet{color:var(--muted);font-size:15px;margin:18px 0;}
  h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
     margin:40px 0 14px;font-weight:700;}
  h3{font-size:13px;color:var(--muted);margin:20px 0 8px;font-weight:700;}
  .note{color:var(--muted);font-size:13px;margin:10px 0;}
  .themetoggle{position:absolute;top:0;right:0;background:var(--card);color:var(--muted);
    border:1px solid var(--line);border-radius:8px;width:34px;height:34px;font-size:16px;
    cursor:pointer;line-height:1;}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;}
  .tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;}
  .tile .v{font-size:26px;font-weight:700;letter-spacing:-.01em;}
  .tile .l{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-top:2px;}
  .tile .hint{font-size:12px;color:var(--muted);margin-top:6px;}
  .row{display:grid;grid-template-columns:1fr 120px auto;gap:12px;align-items:center;margin:9px 0;}
  .rl{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .rl .handle{color:var(--accent);font-weight:700;}
  .rv{font-size:13px;color:var(--muted);text-align:right;white-space:nowrap;}
  .bar{background:var(--track);border-radius:6px;height:10px;overflow:hidden;}
  .bar>i{display:block;height:100%;background:var(--accent);border-radius:6px;}
  .kvs{display:grid;grid-template-columns:1fr;gap:0;border:1px solid var(--line);
    border-radius:12px;overflow:hidden;background:var(--card);}
  .kv{display:flex;justify-content:space-between;gap:16px;padding:11px 16px;border-top:1px solid var(--line);}
  .kv:first-child{border-top:none;}
  .kv .k{color:var(--muted);font-size:14px;} .kv .val{font-weight:600;font-size:14px;text-align:right;}
  .lead{font-size:15px;margin:8px 0 4px;}
  .tip,.practice,.mrow{background:var(--card);border:1px solid var(--line);
    border-left:4px solid var(--accent);border-radius:10px;padding:14px 18px;margin:12px 0;}
  .tip .obs,.practice .obs,.mrow .mhead{font-weight:700;}
  .tip .sug,.practice .sug,.mrow .sug{color:var(--muted);font-size:14px;margin-top:4px;}
  .tip .cmd,.practice .cmd{margin-top:10px;font-size:13px;color:var(--muted);}
  .practice .cmd.via{font-style:italic;}
  .mrow .mhead{display:flex;gap:10px;align-items:center;}
  .mdelta{font-size:13px;margin-top:8px;}
  .tag{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;
    border-radius:20px;padding:1px 9px;}
  .tag.over{background:var(--bad-bg,#fbeeec);color:var(--bad,#c0392b);}
  .tag.under{background:var(--warn-bg,#fbf4e5);color:var(--warn,#b7791f);}
  .tag.on{background:var(--good-bg,#edf7f0);color:var(--good,#1f8a4c);}
  .kv .ex{color:var(--muted);font-weight:400;font-size:12px;}
  .assumptions .assum{color:var(--muted);font-size:12px;padding:0 16px 11px;background:var(--card);}
  code{background:var(--code-bg);border-radius:5px;padding:2px 7px;font-size:.85em;
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
  .gaps{color:var(--muted);font-size:14px;padding-left:20px;} .gaps li{margin:6px 0;}
  .gaps b{color:var(--ink);}
  .foot{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);
    color:var(--muted);font-size:12.5px;}
  a{color:var(--accent);}
</style>
</head>
<body>
<div class="wrap">{BODY}</div>
<script>
  (function(){
    try{ var s=localStorage.getItem("ts-recap-theme"); if(s) document.documentElement.setAttribute("data-theme",s); }catch(e){}
    window.__ttl=function(){
      var r=document.documentElement, cur=r.getAttribute("data-theme");
      var dark=cur?cur==="dark":matchMedia("(prefers-color-scheme: dark)").matches;
      var next=dark?"light":"dark"; r.setAttribute("data-theme",next);
      try{ localStorage.setItem("ts-recap-theme",next); }catch(e){}
    };
  })();
</script>
</body>
</html>
"""
