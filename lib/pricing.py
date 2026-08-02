# pricing.py
"""Per-message cost derivation for Claude Code usage — the rate table behind the
WS1 usage ledger.

There is NO per-message costUSD in a transcript; cost is always derived from the
raw `message.usage` token counts times a $/MTok rate sheet. The rate sheet is
keyed on (model family/version, speed) and — for Sonnet 5's intro window — on the
message timestamp. Cache writes/reads and the `inference_geo` uplift are folded in
by `message_cost`, which reads `usage.speed` / `usage.inference_geo` itself.

Rates verified 2026-07-04 vs platform.claude.com/docs/en/about-claude/pricing
(standard + fast sheets, uniform cache multipliers, 1M-at-standard, inference_geo).
"""
import re
from datetime import datetime, timezone

# Sonnet 5 ships at intro pricing THROUGH 2026-08-31; from 2026-09-01 it bills at
# the standard Sonnet sheet. Priced by the MESSAGE timestamp — a message written
# before this instant gets intro rates, on/after gets standard.
SONNET5_INTRO_END = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()

# $/MTok. Cache multipliers are uniform across every model: a 5m cache write is
# 1.25× the input rate, a 1h write 2×, and a cache read (hit/refresh) 0.1× — so
# each tier is fully determined by its (in, out) pair. `_sheet` bakes that in so
# the numbers below match the docs table row-for-row without hand-copied cache
# columns that could drift.
def _sheet(rin, rout):
    return {"in": rin, "out": rout,
            "w5m": round(rin * 1.25, 6), "w1h": round(rin * 2.0, 6),
            "read": round(rin * 0.1, 6)}


# Standard-speed sheets, one per (family, version-band).
_FABLE       = _sheet(10.0, 50.0)    # fable / mythos (5)
_OPUS        = _sheet(5.0, 25.0)     # opus 4.5–4.8 (current)
_OPUS_LEGACY = _sheet(15.0, 75.0)    # opus 4.1 / 4.0
_SONNET_INTRO = _sheet(2.0, 10.0)    # sonnet 5, through 2026-08-31
_SONNET       = _sheet(3.0, 15.0)    # sonnet 5 (from 2026-09-01) / 4.6 / 4.5 / 4
_HAIKU        = _sheet(1.0, 5.0)     # haiku 4.5
_HAIKU_LEGACY = _sheet(0.8, 4.0)     # haiku 3.5

# Fast mode is a SEPARATE price sheet (not a separate model), defined only for the
# Opus versions that offer it: 4.8 fast = 10/50 (same headline numbers as Fable 5
# standard — different tier). Cache multipliers stack on top of the fast base, so
# _sheet() applies unchanged.
_OPUS48_FAST = _sheet(10.0, 50.0)


def _opus_version(model_id):
    """(major, minor) parsed from an opus id like `claude-opus-4-8` / `opus-4.1`,
    or (None, None) when the id carries no version (bare `opus`)."""
    m = re.search(r"opus[-.]?(\d+)[-.](\d+)", model_id)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _family_version(prefix, model_id):
    """Leading integer version after `<prefix>-` (e.g. `sonnet-5` → 5,
    `haiku-4-5` → 4), or None."""
    m = re.search(prefix + r"[-.]?(\d+)", model_id)
    return int(m.group(1)) if m else None


def rates_for(model_id, speed="standard", ts=None):
    """The $/MTok rate sheet for a (model, speed) pair as
    `{"in","out","w5m","w1h","read"}`, or None for an unknown pair (never silently
    priced at another tier — an unknown model renders as `$n/a`).

    `ts` (epoch seconds) resolves date-dependent pricing: Sonnet 5 bills at intro
    rates before 2026-09-01 and standard rates on/after. An absent `ts` is treated
    as the intro window (the pricing in force as of the 2026-07-04 rate check)."""
    mid = (model_id or "").lower().strip()
    if not mid:
        return None
    fast = (speed or "standard").lower() == "fast"

    # Fast mode: a dedicated Opus 4.8 sheet; every other (model, fast) pair is
    # unknown (fast is only offered on that Opus version).
    if fast:
        if "opus" in mid:
            major, minor = _opus_version(mid)
            if (major, minor) == (4, 8):
                return dict(_OPUS48_FAST)
        return None

    # Standard speed.
    if "fable" in mid or "mythos" in mid:
        return dict(_FABLE)
    if "opus" in mid:
        major, minor = _opus_version(mid)
        if major == 4 and minor in (0, 1):
            return dict(_OPUS_LEGACY)
        return dict(_OPUS)              # 4.5–4.8 and bare/newer opus
    if "sonnet" in mid:
        major = _family_version("sonnet", mid)
        if major == 5:
            intro = ts is None or ts < SONNET5_INTRO_END
            return dict(_SONNET_INTRO if intro else _SONNET)
        return dict(_SONNET)            # 4 / 4.5 / 4.6 (and bare sonnet)
    if "haiku" in mid:
        major = _family_version("haiku", mid)
        if major == 3:
            return dict(_HAIKU_LEGACY)
        return dict(_HAIKU)             # 4.5 (and bare/newer haiku)
    return None


DEFAULT_CONTEXT_WINDOW = 200000
LARGE_CONTEXT_WINDOW = 1000000

# A 1M-context variant is flagged by an explicit `[1m]` / delimited `1m` marker in
# the model id (e.g. `claude-opus-4-8[1m]`) — the only reliable signal, so we never
# infer 1M from family/version alone.
_ONEM_RE = re.compile(r"(?:^|[-_:\[/])1m(?:$|[\]\-_:/])")


def context_window_for(model_id):
    """The context-window size (tokens) for a model id — the denominator for the
    checkpoint-pressure %/tokens math. 1,000,000 for a 1M-context variant (an explicit
    `[1m]`/`1m` marker in the id); 200,000 otherwise. Model-aware so the checkpoint
    trigger and the displayed %/tokens match the model actually in use: a fixed 200k
    denominator makes an Opus-1M session read ~5x over-full and trips the nudge almost
    immediately. Unknown/empty id → the 200k default."""
    mid = (model_id or "").lower().strip()
    if not mid:
        return DEFAULT_CONTEXT_WINDOW
    if "[1m]" in mid or _ONEM_RE.search(mid):
        return LARGE_CONTEXT_WINDOW
    return DEFAULT_CONTEXT_WINDOW


def _cache_write_split(usage):
    """(1h_tokens, 5m_tokens) cache-write split for one usage object. The 1h bucket
    is `min(ephemeral_1h, total_write)`; the remainder is billed at the cheaper 5m
    rate. Falls back to the ephemeral sub-total when the flat total is absent."""
    total = usage.get("cache_creation_input_tokens") or 0
    cc = usage.get("cache_creation") or {}
    e1h = cc.get("ephemeral_1h_input_tokens") or 0
    e5m = cc.get("ephemeral_5m_input_tokens") or 0
    if not total:
        total = e1h + e5m
    w1h = min(e1h, total)
    w5m = max(0, total - w1h)
    return w1h, w5m


def message_cost(model_id, usage, ts=None):
    """Derived USD cost of one assistant message from its raw `message.usage`, or
    None when the model is unknown (unpriced — never guessed at another tier).

      cost = geo·[ in·r.in + out·r.out + cache_read·r.read
                   + w1h·r.w1h + w5m·r.w5m ]/1e6  +  web_search·$0.01

    geo = 1.1 when `usage.inference_geo == "us"`, else 1.0 (applies to the token
    categories only, never the flat web-search fee). Speed + geo are read from the
    usage object; `ts` feeds the date-dependent Sonnet 5 rate."""
    usage = usage or {}
    rates = rates_for(model_id, usage.get("speed", "standard"), ts)
    if rates is None:
        return None
    w1h, w5m = _cache_write_split(usage)
    tokens = (
        (usage.get("input_tokens") or 0) * rates["in"]
        + (usage.get("output_tokens") or 0) * rates["out"]
        + (usage.get("cache_read_input_tokens") or 0) * rates["read"]
        + w1h * rates["w1h"]
        + w5m * rates["w5m"]
    ) / 1_000_000.0
    geo = 1.1 if (usage.get("inference_geo") == "us") else 1.0
    web = (usage.get("server_tool_use") or {}).get("web_search_requests") or 0
    return geo * tokens + web * 0.01


def model_family(model_id):
    """The short family label for a model id (`fable` / `opus` / `sonnet` / `haiku`),
    used by the compact stats-line model mix. Falls back to the raw id when it
    matches no known family."""
    mid = (model_id or "").lower()
    if "fable" in mid or "mythos" in mid:
        return "fable"
    for fam in ("opus", "sonnet", "haiku"):
        if fam in mid:
            return fam
    return model_id or "?"
