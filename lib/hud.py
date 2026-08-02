# hud.py
"""Cost HUD — the toggleable status-line segment renderer that folds costbar's rows
into task-station's compositing status-line host (see docs/STATUSLINE.md).

This is a PORT of costbar's rows + niceties, NOT its architecture. costbar shipped
two divergent cost engines (a live `total_cost_usd` and a token×hardcoded-rate
history rescan) and a stale rate table that mispriced current models; the HUD keeps
exactly ONE source of truth per figure:

  • Session / 5-hour / week rate-limit fields  → the status-line stdin JSON
    (`cost.total_cost_usd`, `context_window`, `rate_limits.*`) — Anthropic-
    authoritative, zero extra compute.
  • 5-hour $ / Week / Total rows  → the WS1 usage ledger (`session_usage`), priced by
    lib/pricing.py — replacing costbar's compute_jsonl_totals.py + its cache.
  • Task row (new) → the attached task's cumulative derived $ + reported worker $
    via usage.task_usage — the per-task attribution costbar structurally couldn't do.

Everything is defensive: a missing ledger or a missing task degrades the affected row
to absent (never a crash, never a blank line); a missing 5-hour rate-limit field shows
costbar's DIM `—` placeholder (the one row costbar renders unconditionally), and
`main()` swallows every error so a broken segment never breaks the status bar.
"""
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

import config
import paths

# ------------------------------------------------------------------ palette ---
# Truecolor (38;2;r;g;b) ported verbatim from costbar so the HUD reads identically.
GREEN = "\033[38;2;180;220;110m"
YELLOW = "\033[38;2;240;190;80m"
RED = "\033[38;2;230;120;80m"
TOK = "\033[38;2;90;135;175m"            # token counts — muted blue
DIM = "\033[2m"
RESET = "\033[0m"
LABEL = "\033[1m"                        # bold row label + figure
DOT_GREEN = "\033[38;2;100;200;100m"     # week-dot: the reset day
DOT_GRAY = "\033[38;2;90;90;90m"         # week-dot: a day elapsed this window
DOT_WHITE = "\033[38;2;240;240;240m"     # week-dot: a day not yet reached
LABEL_GRAY = "\033[38;2;170;170;170m"    # reset-time suffix
ECO = "\033[38;2;100;170;90m"            # eco comparison text

# Header line (costbar's model badge + the inline task-station whoami segment).
VIOLET = "\033[38;2;180;140;230m"        # ⏺ model badge dot
HDR_SEQ = "\033[38;2;235;215;120m"       # task number (mirrors statusline_segment)
HDR_TAG = "\033[38;2;150;150;160m"       # [CATEGORY] tag text
HDR_TITLE = "\033[38;2;215;215;220m"     # task title

LW = 7                                   # label column width ("Session" is widest)
SEP = "  │  "                       # label separator  "  │  "

# The row keys `config --hud-rows` accepts, in canonical render order (Task under the
# header, then Session/5-hour/Week/Total). MUST stay in sync with config.HUD_ROW_KEYS
# (config can't import hud). The old `limits` row merged into `week` + `fivehour`; the
# `turn` row was removed (Session already carries the live cost).
ROW_KEYS = ("task", "session", "fivehour", "week", "total")
DEFAULT_ROWS = list(ROW_KEYS)

# Fixed threshold fallbacks. Every cost row now derives its bands from a μ/μ+σ
# distribution once ≥3 data points exist and falls back to these fixed pairs below
# that: Session over per-session $, the 5-hour and Week rows over their OWN
# populations of historical CLOSED-window totals (current partial window excluded).
# The 5-hour row reuses the Session pair as its sub-cent fallback; Week keeps
# costbar's fixed weekly defaults.
_SESSION_LO, _SESSION_HI = 0.01, 0.05
_WEEK_LO, _WEEK_HI = 50.0, 150.0

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_WEEK_SECS = 604800
_FIVE_HOUR_SECS = 18000


# --------------------------------------------------------------- formatting ---

def fmt_cost(v):
    """`$` amount: 4 decimals for a sub-cent positive value (so a $0.0032 turn is
    visible), 2 decimals otherwise. Mirrors costbar's fmt_cost."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    if 0 < v < 0.01:
        return "%.4f" % v
    return "%.2f" % v


def fmt_tok(v):
    """Compact token count: `1.5m` ≥1e6, `12k` ≥1e3, else the integer. Mirrors
    costbar's fmt_tok (awk %.4g / %.0fk)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    if v >= 1_000_000:
        return "%.4gm" % (v / 1_000_000)
    if v >= 1000:
        return "%.0fk" % (v / 1000)
    return "%d" % int(v)


def _cost_color(v, lo, hi):
    """costbar's 3-band cost coloring: ≥hi RED, ≥lo YELLOW, else GREEN."""
    if v >= hi:
        return RED
    if v >= lo:
        return YELLOW
    return GREEN


def _used_color(v):
    """Rate-limit used% coloring: ≥80 RED, ≥50 YELLOW, else GREEN (costbar
    used_color). Keyed on the USED percentage even though the row prints `% left`,
    so 0% left (100% used) reads RED."""
    if v >= 80:
        return RED
    if v >= 50:
        return YELLOW
    return GREEN


def _rem_color(v):
    """Remaining% coloring (costbar rem_color): ≤20 RED, ≤50 YELLOW, else GREEN —
    the inverse-direction band used for the session context window `% left`."""
    if v <= 20:
        return RED
    if v <= 50:
        return YELLOW
    return GREEN


def _visible_len(s):
    return len(_ANSI_RE.sub("", s))


def _mean_sd(xs):
    """(mean, population stddev) of a list, or None for <2 points."""
    n = len(xs)
    if n < 2:
        return None
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    return mean, (math.sqrt(var) if var > 0 else 0.0)


def _dist_thresholds(costs, lo_fb, hi_fb, min_n=3):
    """Generic (lo, hi) = (μ, μ+σ) of a cost population once ≥`min_n` points exist
    (costbar's `length >= 3`), else the fixed (lo_fb, hi_fb) fallback. Shared by the
    Session row (per-session $) and the 5-hour / Week rows (per-closed-window $)."""
    if len(costs) >= min_n:
        ms = _mean_sd(costs)
        if ms:
            return ms[0], ms[0] + ms[1]
    return lo_fb, hi_fb


def _session_thresholds(session_costs):
    """(lo, hi) = (μ, μ+σ) of the ledger's per-session derived $ once ≥3 priced
    sessions exist (costbar's `length >= 3`), else the fixed fallback."""
    return _dist_thresholds(session_costs, _SESSION_LO, _SESSION_HI)


# ------------------------------------------------------------- week helpers ---

def _dow(epoch):
    """Day of week Sun=0 … Sat=6 (costbar's `date +%w`), in LOCAL time — the whole
    HUD renders reset times/dots in the user's local zone (Ryan's /usage anchor)."""
    return (datetime.fromtimestamp(epoch).weekday() + 1) % 7


def week_dots(reset_epoch, now_epoch):
    """The SMTWTFS week strip: seven ● glyphs, Sun→Sat. The reset day is GREEN, days
    elapsed since the last reset are GRAY, days not yet reached are WHITE. Handles a
    window that wraps past Saturday. `reset_epoch` absent → Tuesday fallback (2), as
    in costbar; `now_epoch` absent → no strip."""
    if now_epoch is None:
        return ""
    today = _dow(now_epoch)
    reset = _dow(reset_epoch) if reset_epoch else 2
    out = []
    for i in range(7):
        if today >= reset:
            active = reset <= i <= today
        else:
            active = i >= reset or i <= today
        if i == reset:
            out.append(DOT_GREEN + "●" + RESET)
        elif active:
            out.append(DOT_GRAY + "●" + RESET)
        else:
            out.append(DOT_WHITE + "●" + RESET)
    return "".join(out)


def _to_epoch(v):
    """Coerce a rate-limit `resets_at` value to a local epoch float. Claude Code's
    statusline payload sends ISO-8601 UTC strings (e.g. "2026-07-08T01:00:00Z");
    cached anchors and tests may already hold epochs. None/garbage -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        from datetime import datetime, timezone
        iso = str(v).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return None


def _week_start_ts(reset_epoch, now_epoch):
    """The epoch the current usage week began at: costbar derives it from the 7-day
    rate-limit reset (`resets_at − 604800`); absent that, fall back to midnight of
    the current week's Sunday (UTC)."""
    if reset_epoch:
        return reset_epoch - _WEEK_SECS
    if now_epoch is None:
        return None
    dt = datetime.fromtimestamp(now_epoch)
    midnight = datetime(dt.year, dt.month, dt.day).timestamp()
    return midnight - _dow(now_epoch) * 86400


def _five_hour_start_ts(reset_epoch, now_epoch):
    """The epoch the current 5-hour window began: the rate-limit reset − 5h, else
    `now − 5h`, else None (no `now`)."""
    if reset_epoch:
        return reset_epoch - _FIVE_HOUR_SECS
    if now_epoch is None:
        return None
    return now_epoch - _FIVE_HOUR_SECS


def _fmt_reset_5h(epoch):
    """5-hour reset clock in LOCAL time — costbar's `%I:%M %p` with a stripped
    leading zero (bare space before the meridiem, uppercase AM/PM): `4:32 PM`,
    `9:00 PM`, `12:05 AM`."""
    return datetime.fromtimestamp(epoch).strftime("%I:%M %p").lstrip("0")


def _fmt_reset_week(epoch):
    """Weekly reset day + clock in LOCAL time — costbar's `%a %-I:%M %p`, weekday
    then the leading-zero-stripped clock: `Wed 4:30 PM`."""
    dt = datetime.fromtimestamp(epoch)
    return dt.strftime("%a ") + dt.strftime("%I:%M %p").lstrip("0")


def _cal_week(first_epoch, now_epoch):
    """The calendar-week counter for the Week label (costbar cal_week :454-461):
    weeks elapsed between the FIRST-seen session's Sunday and the current week's
    Sunday, +1. Absent a first date → week 1."""
    if not first_epoch or now_epoch is None:
        return 1

    def _week_sunday_midnight(epoch):
        dt = datetime.fromtimestamp(epoch)
        midnight = datetime(dt.year, dt.month, dt.day).timestamp()
        return midnight - _dow(epoch) * 86400

    cur_sunday = _week_sunday_midnight(now_epoch)
    first_sunday = _week_sunday_midnight(first_epoch)
    return int((cur_sunday - first_sunday) / _WEEK_SECS) + 1


def _fmt_since(epoch):
    """`Jul 4, 2026` for the Total row's since-date (LOCAL time)."""
    dt = datetime.fromtimestamp(epoch)
    return dt.strftime("%b ") + "%d, %d" % (dt.day, dt.year)


# ------------------------------------------------------------------- eco -----
# Optional (`--hud-eco`, default off): an "≈ <comparison>" of the row's output
# tokens' environmental footprint, rotating through 30 framings by env_idx (the
# render clock second % 30, injectable for tests). Physical bases per output token
# (costbar's constants): CO2 g, kWh, water mL.

def _eco_metrics(tok):
    return (tok * 0.000772, tok * 0.000002, tok * 0.0036)   # co2_g, kwh, water_ml


def _dist(v):
    if v >= 1000:
        return "%.0fk mi" % (v / 1000)
    if v >= 1:
        return "%.1f mi" % v
    return "%.0f ft" % (v * 5280)


def eco_comparison(tok, env_idx):
    """The comparison phrase for `tok` output tokens under rotation slot `env_idx`
    (0–29), or "" for a non-positive count. Ported from costbar's eco_suffix cases."""
    if tok <= 0:
        return ""
    co2, kwh, water = _eco_metrics(tok)
    i = env_idx % 30
    if i == 0:
        return "driving %s (gas car)" % _dist(co2 / 404)
    if i == 1:
        return "flying %s (economy)" % _dist(co2 / 255)
    if i == 2:
        v = co2 / 27
        s = ("%.1f kg" % (v / 1000)) if v >= 1000 else (
            ("%.0f g" % v) if v >= 1 else ("%.1f g" % v))
        return "%s of beef produced" % s
    if i == 3:
        return "%s cups of coffee" % _count(co2 / 280)
    if i == 4:
        return "%s bananas" % _count(co2 / 80)
    if i == 5:
        return "%s avocados" % _count(co2 / 846)
    if i == 6:
        return "%s cheeseburgers" % _count(co2 / 2500)
    if i == 7:
        return "%s bottles of wine" % _count(co2 / 1200)
    if i == 8:
        v = co2 / 33
        return "%s plastic bags produced" % (("%.0fk" % (v / 1000)) if v >= 1000 else "%.0f" % v)
    if i == 9:
        v = co2 / 2.4
        s = ("%.1f yrs" % (v / 8760)) if v >= 8760 else (
            ("%.0f days" % (v / 24)) if v >= 24 else "%.0f hrs" % v)
        return "1 tree offsetting CO2 for %s" % s
    if i == 10:
        v = co2 / 13
        return "%s candle burning" % (("%.1f hrs" % v) if v >= 1 else "%.0f min" % (v * 60))
    if i == 11:
        return "%s incandescent bulb" % _hrs(kwh / 0.06)
    if i == 12:
        return "%s LED bulb" % _hrs(kwh / 0.01)
    if i == 13:
        v = kwh / 0.007
        return "%s phone charges" % (("%.0fk" % (v / 1000)) if v >= 1000 else "%.0f" % v)
    if i == 14:
        v = kwh / 0.0003
        s = ("%.1fM" % (v / 1_000_000)) if v >= 1_000_000 else (
            ("%.0fk" % (v / 1000)) if v >= 1000 else "%.0f" % v)
        return "%s Google searches" % s
    if i == 15:
        return "%s of Netflix streaming" % _mins(kwh / 0.036 * 60)
    if i == 16:
        return "%s laptop running" % _mins(kwh / 0.05 * 60)
    if i == 17:
        return "%s of PS5 gaming" % _mins(kwh / 0.2 * 60)
    if i == 18:
        v = kwh / 1.2 * 3600
        return "%s of microwave" % (("%.0f min" % (v / 60)) if v >= 60 else "%.0f sec" % v)
    if i == 19:
        return "%s kettle boils" % _count(kwh / 0.1)
    if i == 20:
        v = kwh / 0.5
        return "%s washing machine cycles" % (("%.1f" % v) if v >= 1 else "%.2f" % v)
    if i == 21:
        v = kwh / 1.5
        return "%s dishwasher cycles" % (("%.1f" % v) if v >= 1 else "%.2f" % v)
    if i == 22:
        v = kwh / 1.0 * 60
        return "%s window AC" % (("%.0f hrs" % (v / 60)) if v >= 60 else "%.0f min" % v)
    if i == 23:
        return "%s EV driving" % _dist(kwh / 0.3)
    if i == 24:
        return "powering a home for %s" % _mins(kwh / 1.25 * 60)
    if i == 25:
        s = ("%.1f kL" % (water / 1_000_000)) if water >= 1_000_000 else (
            ("%.2f L" % (water / 1000)) if water >= 1000 else "%.1f mL" % water)
        return "%s water (datacenter cooling)" % s
    if i == 26:
        return "%s glasses of drinking water" % _count(water / 250)
    if i == 27:
        v = water / 8000 * 60
        return "%s of showering" % (("%.0f min" % (v / 60)) if v >= 60 else "%.0f sec" % v)
    if i == 28:
        v = water / 6000
        return "%s toilet flushes" % (("%.1f" % v) if v >= 1 else "%.2f" % v)
    v = water / 5
    return "%s teaspoons of water" % _count(v)


def _count(v):
    if v >= 1000:
        return "%.0fk" % (v / 1000)
    if v >= 1:
        return "%.1f" % v
    return "%.2f" % v


def _hrs(v):
    if v >= 1000:
        return "%.0fk hrs" % (v / 1000)
    if v >= 1:
        return "%.1f hrs" % v
    return "%.0f min" % (v * 60)


def _mins(v):
    if v >= 1440:
        return "%.0f days" % (v / 1440)
    if v >= 60:
        return "%.0f hrs" % (v / 60)
    return "%.0f min" % v


def _eco_suffix(tok, env_idx):
    """The full "  ·  ≈ <comparison>" suffix (colored), or "" when there's nothing
    to compare."""
    cmp = eco_comparison(tok, env_idx)
    if not cmp:
        return ""
    return "  " + DIM + "·" + RESET + "  " + ECO + "≈ " + cmp + RESET


# --------------------------------------------------------------- row bits ----

def _label(name):
    """The fixed-width bold row label + `  │  ` separator (plain bold, uncolored —
    every row's label reads the same; only the figures carry the cost/util bands)."""
    return LABEL + name.ljust(LW) + RESET + SEP


def _payload_get(payload, *path, default=None):
    cur = payload
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


# ------------------------------------------------------------------- render ---

def render(payload, *, store=None, task=None, session_out=0,
           now=None, rows=None, eco=True, billing_mode="api", width=0):
    """Compose the HUD segment for one status-line render.

    `payload` is the parsed status-line stdin JSON. When the HUD is on it owns the
    WHOLE bar: the first line is costbar's model badge (`⏺ <display_name>`) plus the
    task-station whoami segment inline; the separate 50-task-station provider line is
    suppressed (see cmd_whoami) so the task never renders twice.

    `store`/`task` supply the ledger-derived Week/Total/Task rows (omitted when
    absent). `session_out` is the session's accumulated output tokens, `turn` the
    {'delta','out'} baseline figures (both injected by main() from the snapshot, so
    this function does no clock/file IO). `now` is epoch seconds (week dots + eco
    rotation + since-date); `rows` the ordered subset of ROW_KEYS to show; `eco`
    toggles the footprint column (default ON, per Ryan); `billing_mode` frames the
    Total row (subscription → API-equivalent value).

    Cost-band coloring: the Session, 5-hour, and Week $ figures each go stddev-based
    (μ / μ+σ over that row's own distribution) once ≥3 data points exist — per-session
    $ for Session, per-closed-window $ for the 5-hour and Week rows (current partial
    window excluded) — falling back to the fixed bands below 3.

    Returns the multi-line segment (no trailing newline), or "" when nothing renders.
    """
    payload = payload or {}
    rows = rows if rows is not None else DEFAULT_ROWS
    built = []                                   # (line, eco_tokens)

    current = _payload_get(payload, "cost", "total_cost_usd", default=0.0) or 0.0
    ctx_rem = _payload_get(payload, "context_window", "remaining_percentage")
    hour_used = _payload_get(payload, "rate_limits", "five_hour", "used_percentage")
    week_used = _payload_get(payload, "rate_limits", "seven_day", "used_percentage")
    hour_reset = _to_epoch(_payload_get(payload, "rate_limits", "five_hour", "resets_at"))
    week_reset = _to_epoch(_payload_get(payload, "rate_limits", "seven_day", "resets_at"))

    ledger = None
    if store is not None:
        ledger = usage_ledger(store, week_reset, now, hour_reset)
    lo_s, hi_s = _session_thresholds(ledger["session_costs"] if ledger else [])
    # 5-hour / Week $-band thresholds: μ/μ+σ over each row's OWN population of
    # historical closed-window totals once ≥3 windows exist, else the fixed fallback.
    # `.get(...)` guards older cached ledger dicts that predate these bucket keys.
    if ledger:
        lo_5h, hi_5h = _dist_thresholds(
            ledger.get("five_hour_bucket_costs", []), _SESSION_LO, _SESSION_HI)
        lo_wk, hi_wk = _dist_thresholds(
            ledger.get("week_bucket_costs", []), _WEEK_LO, _WEEK_HI)
    else:
        lo_5h, hi_5h = _SESSION_LO, _SESSION_HI
        lo_wk, hi_wk = _WEEK_LO, _WEEK_HI

    header = _header(payload, task, width)
    if header:
        built.append((header, 0))                # header never carries an eco suffix

    for key in rows:
        if key == "task" and store is not None and task is not None:
            r = _row_task(store, task, lo_s, hi_s)
            if r is not None:
                built.append(r)
        elif key == "session":
            built.append(_row_session(current, session_out, lo_s, hi_s, ctx_rem))
        elif key == "fivehour":
            if ledger is not None:
                built.append(_row_fivehour(hour_used, hour_reset,
                                           ledger["five_hour_cost"], ledger["five_hour_out"],
                                           lo_5h, hi_5h))
            else:
                built.append(_row_fivehour(hour_used, hour_reset))
        elif key == "week" and ledger is not None:
            built.append(_row_week(ledger, week_used, week_reset, now, lo_wk, hi_wk))
        elif key == "total" and ledger is not None:
            built.append(_row_total(ledger, now, billing_mode))

    built = [b for b in built if b is not None]
    if not built:
        return ""
    if eco:
        env_idx = int(now) % 30 if now is not None else 0
        return _assemble_with_eco(built, env_idx, width)
    return "\n".join(b[0] for b in built)


# ------------------------------------------------------------------- header ---

def _cat_tag(color):
    """The colored `<emoji> [TAG]` for a task's category (emoji left self-colored,
    bracket text muted) — mirrors task-station's statusline_segment. "" when
    categories are off / unavailable (never raises: the bar must not break)."""
    try:
        import categories as cats
        tag = cats.tag(color)
    except Exception:
        return ""
    if tag and "[" in tag:
        dot, _, rest = tag.partition("[")
        return "%s%s[%s%s" % (dot, HDR_TAG, rest, RESET)
    return tag


def _task_segment(task, width=0, ordinal=None):
    """costbar-style inline whoami segment: `#<seq>  <emoji> [TAG]  <title>`, colored
    like task-station's own status-bar provider. `width` (>0) truncates the title so
    the visible segment fits (rough — the badge preceding it is small). When `ordinal`
    is given (a hub session's number for this task), the number renders
    `#<seq>-<n>` (#463)."""
    seq = task.get("seq")
    seq_disp = ("%s-%s" % (seq, ordinal)
                if seq is not None and ordinal is not None else seq)
    title = task.get("title") or ""
    tag = _cat_tag(task.get("color"))
    plain_prefix = ("#%s  " % seq_disp if seq is not None else "") + \
        (_ANSI_RE.sub("", tag) + "  " if tag else "")
    if width and width > 0:
        avail = width - len(plain_prefix)
        if avail < 1:
            avail = 1
        if len(title) > avail:
            title = title[: max(1, avail - 1)] + "…"
    parts = []
    if seq is not None:
        parts.append("%s#%s%s" % (HDR_SEQ, seq_disp, RESET))
    if tag:
        parts.append(tag)
    parts.append("%s%s%s" % (HDR_TITLE, title, RESET))
    return "  ".join(parts)


def _header(payload, task, width=0):
    """The always-first bar line: costbar's violet `⏺ <model display_name>` badge,
    plus the inline task segment when a task is attached. "" when the payload carries
    no model name (so the row-only pure tests stay clean)."""
    disp = _payload_get(payload, "model", "display_name")
    if not disp:
        return ""
    # Whole badge violet: the ⏺ dot AND the model name (no RESET between them, so the
    # violet carries through; LABEL bolds the name).
    line = VIOLET + "⏺ " + LABEL + str(disp) + RESET
    if task is not None:
        # Hub session ordinal (#463): render '#<seq>-<n>' when the piped session is a
        # rostered hub of this task. Data-gated — bare/worker sessions render '#<seq>'.
        sid = payload.get("session_id") if isinstance(payload, dict) else None
        m = (task.get("session_meta") or {}).get(sid) or {} if sid else {}
        ordinal = m.get("ordinal") if m.get("role") == "hub" else None
        seg = _task_segment(task, width, ordinal=ordinal)
        if seg:
            # A dim vertical-bar separator between the model badge and the task segment.
            line += "  " + DIM + "│" + RESET + "  " + seg
    return line


def usage_ledger(store, week_reset, now, hour_reset=None):
    """Thin wrapper over usage.ledger_totals resolving the week window from the 7-day
    reset + now and the 5-hour window from the 5-hour reset + now. Imported lazily so a
    broken usage import can't take the bar down."""
    import usage
    return usage.ledger_totals(store, _week_start_ts(week_reset, now), now,
                               _five_hour_start_ts(hour_reset, now))


def _sep():
    """costbar's inline `·` between-field separator (`sep=" · "`): a bare
    middle-dot in the default foreground, single spaces — NOT dimmed (the dimmed
    dot is reserved for the eco suffix)."""
    return " · "


def _row_session(current, session_out, lo, hi, ctx_rem=None):
    """Session row: derived $ + output tokens, then costbar's context-window
    `<ctx>% left` (rem_color-banded) when `context_window.remaining_percentage`
    is present."""
    line = (_label("Session") + _cost_color(current, lo, hi) + LABEL + "$"
            + fmt_cost(current) + RESET + " " + TOK + "out " + fmt_tok(session_out) + RESET)
    if ctx_rem is not None:
        line += (_sep() + _rem_color(ctx_rem) + LABEL + "%.0f%% left" % ctx_rem + RESET)
    return (line, int(session_out or 0))


def _row_fivehour(hour_used, hour_reset, five_cost=None, five_out=0,
                  lo=_SESSION_LO, hi=_SESSION_HI):
    """The 5-hour budget row: the derived $ + output tokens spent in the current 5-hour
    window (from the ledger, when available) then the rate-limit remaining `% left`
    (colored by the USED %, so 0% left reads red) + the LOCAL reset in `(↺4:32 PM)`.
    The $ , used %, and reset render independently — used%/reset can come from the cached
    rate-limit anchor on a fresh session (resolve_rate_limits), so the reset shows even
    when the used % hasn't been seen. Only a fully-empty row (no $, no used%, no reset)
    shows costbar's DIM `—` placeholder. Output tokens drive the eco column like the
    other cost rows. `lo`/`hi` are the $-band thresholds — stddev-based (μ/μ+σ over
    historical closed 5-hour windows) once ≥3 exist, else the fixed sub-cent fallback."""
    line = _label("5-hour")
    have_cost = five_cost is not None
    if have_cost:
        line += (_cost_color(five_cost, lo, hi) + LABEL + "$"
                 + fmt_cost(five_cost) + RESET + " " + TOK + "out " + fmt_tok(five_out) + RESET)
    if hour_used is not None:
        line += ((_sep() if have_cost else "") + _used_color(hour_used)
                 + LABEL + "%.0f%% left" % (100 - hour_used) + RESET)
    elif not have_cost and not hour_reset:
        line += DIM + "—" + RESET
    if hour_reset:
        line += " " + LABEL_GRAY + "(↺" + _fmt_reset_5h(hour_reset) + ")" + RESET
    return (line, int(five_out or 0))


def _row_week(ledger, week_used, week_reset, now, lo=_WEEK_LO, hi=_WEEK_HI):
    """The merged Week row: `Week <cal_week>` label + derived week $ + output tokens
    + seven_day remaining `% left` + the LOCAL weekly reset `(↺Wed 4:30 PM)` + the
    SMTWTFS week-dot strip (all anchored to resets_at−7d). Absorbs the old
    standalone weekly-limit row. `lo`/`hi` are the $-band thresholds — stddev-based
    (μ/μ+σ over historical closed weekly windows) once ≥3 exist, else costbar's fixed
    weekly fallback."""
    cost = ledger["week_cost"]
    out = ledger["week_out"]
    line = (_label("Week %d" % _cal_week(ledger.get("first_ts"), now))
            + _cost_color(cost, lo, hi) + LABEL + "$"
            + fmt_cost(cost) + RESET + " " + TOK + "out " + fmt_tok(out) + RESET)
    if week_used is not None:
        line += _sep() + _used_color(week_used) + LABEL + "%.0f%% left" % (100 - week_used) + RESET
    # The reset renders independently of the util % — either can come from the cached
    # rate-limit anchor on a fresh session, so the reset always shows once seen.
    if week_reset:
        line += " " + LABEL_GRAY + "(↺" + _fmt_reset_week(week_reset) + ")" + RESET
    dots = week_dots(week_reset, now)
    if dots:
        line += " " + dots
    return (line, out)


def _row_total(ledger, now, billing_mode):
    grand = ledger["grand_cost"]
    out = ledger["grand_out"]
    line = (_label("Total") + LABEL + "$" + fmt_cost(grand) + RESET + "  "
            + TOK + "out " + fmt_tok(out) + " tokens" + RESET)
    if ledger["first_ts"]:
        line += DIM + " since " + _fmt_since(ledger["first_ts"]) + RESET
    if billing_mode == "subscription":
        line += DIM + " (API-equiv value)" + RESET
    return (line, out)                           # Total DOES get the eco suffix (costbar total_eco)


def _row_task(store, task, lo=_SESSION_LO, hi=_SESSION_HI):
    """The per-task row directly under the header: `Task #<seq>` label (the number is
    also shown here, per Ryan, not just in the header), derived $, reported $ (when
    present), output tokens. The derived-$ colour uses the SAME ledger μ/σ bands
    (`lo`/`hi` from _session_thresholds) as the Session row — so it is stdev-based like
    the other cost figures, not a fixed band. NO `(+unknown)` marker (that unpriced-model
    caveat lives in `usage --task`'s derivation note now)."""
    import usage
    try:
        u = usage.task_usage(store, task)
    except Exception:
        return None
    derived = u.get("total_cost_usd") or 0.0
    reported = u.get("reported_cost_usd") or 0.0
    out = int(u.get("total_out") or 0)
    seq = task.get("seq")
    label = "Task #%s" % seq if seq is not None else "Task"
    line = (_label(label) + _cost_color(derived, lo, hi) + LABEL
            + "$" + fmt_cost(derived) + RESET + DIM + " derived" + RESET)
    if reported > 0:
        line += DIM + " · $" + fmt_cost(reported) + " reported" + RESET
    if out > 0:
        line += DIM + " · " + RESET + TOK + "out " + fmt_tok(out) + RESET
    return (line, out)                           # eco suffix on every row with tokens


def _assemble_with_eco(built, env_idx, width):
    """Right-align an eco suffix into a fixed column past the widest ECO-BEARING row
    (costbar's eco_at_col). The header and the no-token rows (5-hour) don't inflate the
    column — only rows that carry output tokens (Session/Week/Total/Task) do.
    A row with 0 eco tokens gets no suffix. `width` (visible columns, 0 = unknown) caps
    the column so eco never overruns a known-width bar."""
    eco_lines = [b for b in built if b[1] > 0 and eco_comparison(b[1], env_idx)]
    if not eco_lines:
        return "\n".join(b[0] for b in built)
    eco_col = max(_visible_len(b[0]) for b in eco_lines) + 2
    if width and width > 0:
        eco_col = min(eco_col, width)
    lines = []
    for line, toks in built:
        suffix = _eco_suffix(toks, env_idx)
        if suffix:
            pad = eco_col - _visible_len(line)
            if pad < 1:
                pad = 1
            line = line + (" " * pad) + suffix
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------- turn-baseline snapshot ---
# Relocated from costbar's /tmp/claude_* scatter into ONE json per session under
# the data dir. The status-line render observes the running cost/out totals; the
# UserPromptSubmit + Stop hooks finalize the just-ended turn's delta and re-baseline.

def _hud_dir():
    return os.path.join(paths.data_dir(), "hud")


def _snap_path(sid):
    return os.path.join(_hud_dir(), "%s.json" % sid)


# The 5-hour + weekly rows are anchored to the payload's rate_limits (the weekly
# window = seven_day.resets_at − 7d). These are ACCOUNT-level limits that persist
# across sessions, but a FRESH session's first status-line payloads routinely omit
# rate_limits entirely — so the last-seen used% + reset for each window is cached
# under the data dir and folded back in on a miss. That's what lets the 5-hour and
# week rows (including their reset timestamps) render immediately on a new session
# instead of falling back to costbar's `—` / no-reset placeholder.
_RL_WINDOWS = ("five_hour", "seven_day")
_RL_FIELDS = ("used_percentage", "resets_at")


def _rate_limits_path():
    return os.path.join(_hud_dir(), "rate_limits.json")


def _read_json(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_rate_limits(payload):
    """Merge the payload's `rate_limits` with the last-seen cached values so the
    5-hour + week rows keep rendering across a fresh session's rate-limit-less
    payloads. Per field: a present payload value WINS and refreshes the cache; an
    absent one falls back to the cache. Returns the merged {window: {field: value}}
    dict (empty if nothing has ever been seen). Defensive — never raises.

    The cached used% can lag the true account figure (it's a snapshot from whichever
    session last saw a live value), but the reset timestamps are stable anchors and
    the live payload overwrites both the instant it carries them."""
    live = _payload_get(payload or {}, "rate_limits", default={})
    live = live if isinstance(live, dict) else {}
    cached = _read_json(_rate_limits_path())
    merged = {}
    dirty = False
    for w in _RL_WINDOWS:
        lw = live.get(w) if isinstance(live.get(w), dict) else {}
        cw = cached.get(w) if isinstance(cached.get(w), dict) else {}
        m = {}
        for f in _RL_FIELDS:
            lv = lw.get(f)
            if lv is not None:
                m[f] = lv
                if lv != cw.get(f):
                    dirty = True
            elif cw.get(f) is not None:
                m[f] = cw[f]
        if m:
            merged[w] = m
    if dirty:
        _write_snap_json(_rate_limits_path(), merged)
    return merged


def resolve_week_anchor(payload):
    """Back-compat: the seven_day reset epoch to anchor the week window on, resolved
    through the unified rate-limit cache (payload value or last-seen), else None."""
    return (resolve_rate_limits(payload).get("seven_day") or {}).get("resets_at")


def _write_snap_json(path, d):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _read_snap(sid):
    try:
        with open(_snap_path(sid)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_snap(sid, d):
    try:
        os.makedirs(_hud_dir(), exist_ok=True)
        p = _snap_path(sid)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, p)
    except OSError:
        pass


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def observe(sid, payload):
    """Fold one render's live figures into the session's snapshot and return the
    (session_out_acc, turn_delta, turn_out) the renderer needs. Mirrors costbar's
    statusline accumulation: the output-token counter advances only when the request
    output count CHANGES (so intermediate tool-call responses aren't missed), and the
    running-turn delta is measured against the turn baseline; between turns the
    frozen last-turn snapshot is used."""
    snap = _read_snap(sid)
    current = _payload_get(payload, "cost", "total_cost_usd", default=0.0) or 0.0
    cw = payload.get("context_window") or {}
    # Persist the harness-authoritative context-window SIZE (200k vs 1M) so the Stop
    # hook's checkpoint math sizes its % against the REAL window. A runtime `/model`
    # 1M selection is invisible to the transcript (the `[1m]` marker is stripped from
    # message.model) and to settings.json / ANTHROPIC_MODEL, but the statusline
    # payload's context_window_size records it. SESSION-CONTEXT ONLY — never derived
    # from rate_limits (the 5-hour / weekly windows are unrelated to auto-save).
    _size = cw.get("context_window_size")
    if isinstance(_size, (int, float)) and int(_size) > 0:
        snap["context_window_size"] = int(_size)
    req_out = (cw.get("current_usage") or {}).get("output_tokens") \
        or cw.get("total_output_tokens") or 0
    snap["prev_cost"] = current
    if req_out and req_out != snap.get("last_req_out"):
        snap["out_acc"] = (snap.get("out_acc") or 0) + req_out
        snap["last_req_out"] = req_out
    out_acc = snap.get("out_acc") or 0
    if snap.get("running"):
        delta = current - (snap.get("start_cost") or current)
        tout = out_acc - (snap.get("start_out") or 0)
    else:
        delta = snap.get("last_turn_delta") or 0.0
        tout = snap.get("last_turn_out") or 0
    _write_snap(sid, snap)
    return out_acc, max(0.0, delta), max(0, tout)


def _finalize_turn(sid, running):
    snap = _read_snap(sid)
    cur = snap.get("prev_cost") or 0.0
    snap["last_turn_delta"] = max(0.0, cur - (snap.get("start_cost") or cur))
    snap["last_turn_out"] = max(0, (snap.get("out_acc") or 0) - (snap.get("start_out") or 0))
    if running:
        snap["start_cost"] = cur
        snap["start_out"] = snap.get("out_acc") or 0
        snap["running"] = True
        snap.setdefault("session_start_date", _today())
    else:
        snap["running"] = False
    _write_snap(sid, snap)


def turn_start(sid):
    """UserPromptSubmit: finalize the previous turn's delta, then re-baseline + mark
    the new turn running."""
    _finalize_turn(sid, running=True)


def turn_end(sid):
    """Stop: freeze the just-ended turn's delta so the idle bar shows it, and clear
    the running flag."""
    _finalize_turn(sid, running=False)


# --------------------------------------------------------------------- main ---

def _open_store():
    import store
    return store.get_backend(os.path.join(paths.data_dir(), "store"))


def _resolve_task(store, sid):
    if not store or not sid:
        return None
    try:
        tid = store.get_link(sid)
    except Exception:
        return None
    if not tid or (isinstance(tid, str) and tid.startswith("__")):
        return None
    try:
        return store.load_task(tid)
    except Exception:
        return None


def _statusline(width):
    if not config.hud_enabled():
        return
    payload = json.load(sys.stdin)
    sid = payload.get("session_id")
    if not sid:
        return
    session_out, _delta, _tout = observe(sid, payload)   # delta/tout unused since the Turn row was removed

    # Fold the cached rate-limit anchors back into the payload so the 5-hour + week
    # rows (used% + reset) render even on a fresh session whose payloads omit them.
    merged_rl = resolve_rate_limits(payload)
    if merged_rl:
        payload["rate_limits"] = merged_rl

    # Resolve the attached task from the link table regardless of usage tracking, so
    # the header segment always shows it (attachment ≠ ledger). Pass the store to the
    # renderer (enabling the ledger-derived Task/Week/Total rows) ONLY when tracking
    # is on, so those rows stay inert when the ledger is off.
    store = task = None
    try:
        _store = _open_store()
        task = _resolve_task(_store, sid)
        if config.usage_tracking_enabled():
            store = _store
            # HUD-triggered incremental refresh: fold THIS session's transcript into
            # the ledger so Week/Total count the live session between hook flushes
            # (cheap — one file, byte-offset incremental; NULL task when unattached).
            try:
                import usage
                usage.scan_session(store, sid, task["id"] if task else None)
            except Exception:
                pass
    except Exception:
        store = task = None
    import time
    out = render(payload, store=store, task=task, session_out=session_out,
                 now=time.time(), rows=config.hud_rows(), eco=config.hud_eco_enabled(),
                 billing_mode=config.usage_billing_mode(), width=width)
    if out:
        sys.stdout.write(out + "\n")


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="hud")
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("statusline")
    sp.add_argument("--width", type=int, default=0)
    for name in ("turn-start", "turn-end"):
        q = sub.add_parser(name)
        q.add_argument("--session", required=True)
    # Bare invocation (no subcommand) == statusline, for the provider drop-in.
    p.add_argument("--width", type=int, default=0)
    a = p.parse_args(argv)
    try:
        if a.cmd in (None, "statusline"):
            _statusline(getattr(a, "width", 0) or 0)
        elif a.cmd == "turn-start":
            if config.hud_enabled():
                turn_start(a.session)
        elif a.cmd == "turn-end":
            if config.hud_enabled():
                turn_end(a.session)
    except Exception:
        pass                                  # never break the status bar / a hook
    return 0


if __name__ == "__main__":
    sys.exit(main())
