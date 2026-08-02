"""Demo peer-feed seeder — install the persistent fake-peer feeds into the feed root and
rewrite their cross-brain sentinels to REAL signals from this machine's self feed.

WHY THIS STAYS. Demo feeds are the stand-in for real peers until the two-machine sync
transport (J-track) lands and starts dropping canonical feeds into `feeds/peers/`. Seeding
them populates the feed root, flips `--interbrain auto` ON (the resolver counts feed
files), and renders every demo brain as read-only foreign rows + cross-brain graph edges:

    python3 tools/seed_demo.py && python3 lib/task-station.py board --open

THE FIXTURES ARE CANONICAL (#444). They must stay in the exact wire form
`feeds._feed_js` writes — a ONE-LINE `window.__TSFEED_<alias> = {json};` plus the
__TSFEEDS registration, pure data with no functions or variables. They were previously
IIFE-wrapped, which only a browser could evaluate, so the SERVER-side path skipped all
four and demo federation rendered nowhere once the client shell was retired. Guarded by
`tests/test_feeds.py:ShippedFixtureTest` — break the form and those fail loudly instead of
silently yielding zero peers.

The committed fixtures live in `fixtures/demo-feeds/` (jpark ~10 tasks, kosei ~6, org
~5, rnguyen-demo 4 [DEMO] tasks). `seed(data_dir, self_feed)`:
  1. copies each fixture to `<data_dir>/feeds/demo/<name>.js` — but ONLY if not already
     present, so demo data is PERSISTENT across re-runs (edit it and it survives);
  2. on first copy, rewrites the sentinels
       __XREF_PR_1__ / __XREF_PR_2__   → real PR signal ids from the self feed
       __XREF_STORY_1__                → a real story id from the self feed
       __REAL_UUID8_1__ / _2__         → real task uuid8s from the self feed
     so cross-brain shared-signal edges + memo pulses form against the user's REAL
     tasks. jpark carries the 2-3 signal rewrites; memo targets across all feeds get
     real uuid8s. With no self feed yet, benign demo fallbacks keep the JSON valid and
     the edges demo-internal.
  3. returns the ordered list of feed paths relative to `<data_dir>`.

DEMO DATA NEVER ENTERS THE STORE. This module only reads the already-exported self feed
(`lib/feeds.py` owns that format — this file does not re-implement it) and writes under
`feeds/demo/`; it never opens the store. CLEANUP: `seed_demo.py --clean` (or delete
`<data_dir>/feeds/demo/`).

stdlib-only. Usable as a library or standalone:
    python3 tools/seed_demo.py [--data-dir DIR] [--clean]
"""
import argparse
import os
import sys

_TOOLS = os.path.dirname(os.path.realpath(__file__))
_LIB = os.path.join(os.path.dirname(_TOOLS), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import feeds  # noqa: E402  — the ONE owner of the feed format (root, wire form, parser)

FIXTURE_ORDER = ["jpark.js", "kosei.js", "org.js", "rnguyen-demo.js"]

_SENTINELS = ("__XREF_PR_1__", "__XREF_PR_2__", "__XREF_STORY_1__",
              "__REAL_UUID8_1__", "__REAL_UUID8_2__")


def _fixtures_dir():
    return os.path.join(os.path.dirname(_TOOLS), "fixtures", "demo-feeds")


def _demo_dir(data_dir):
    """The demo feed dir under the ONE feed root: `<data_dir>/feeds/demo`."""
    return os.path.join(feeds.feeds_dir(data_dir), "demo")


def _dedupe(seq):
    out, seen = [], set()
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _atomic_write(path, text):
    tmp = path + ".tmp." + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _collect_reals(self_feed):
    """(prs, stories, uuids) real values from the self feed, order-preserving."""
    tasks = (self_feed or {}).get("tasks") or []
    prs, stories, uuids = [], [], []
    for t in tasks:
        if t.get("uuid8"):
            uuids.append(t["uuid8"])
        sig = t.get("signals") or {}
        prs += sig.get("prs") or []
        stories += sig.get("stories") or []
    return _dedupe(prs), _dedupe(stories), _dedupe(uuids)


def _pick(lst, i, fallback):
    if len(lst) > i:
        return lst[i]
    return lst[0] if lst else fallback


def _rewrite(text, prs, stories, uuids):
    """Replace every sentinel with a real value (or a benign demo fallback so the JSON
    stays valid + edges just stay demo-internal when the store has no matching signal).
    Returns (text, n_replaced)."""
    repl = {
        "__XREF_PR_1__": _pick(prs, 0, "demo/xref#1"),
        "__XREF_PR_2__": _pick(prs, 1, "demo/xref#2"),
        "__XREF_STORY_1__": _pick(stories, 0, "demo-story-1"),
        "__REAL_UUID8_1__": _pick(uuids, 0, "00000000"),
        "__REAL_UUID8_2__": _pick(uuids, 1, "00000000"),
    }
    n = 0
    for k, v in repl.items():
        if k in text:
            text = text.replace(k, v)
            n += 1
    return text, n


def seed(data_dir, self_feed=None, org_label="Org brain"):
    """Copy + sentinel-rewrite the demo fixtures into `<data_dir>/feeds/demo/`. Returns
    the ordered list of feed paths relative to `<data_dir>`. Existing demo files are left
    untouched (persistent). The org feed's label is set to `org_label` (e.g.
    "Company Brain") on copy. Never touches the store."""
    fx = _fixtures_dir()
    demo_dir = _demo_dir(data_dir)
    os.makedirs(demo_dir, exist_ok=True)
    prs, stories, uuids = _collect_reals(self_feed)
    srcs = []
    for name in FIXTURE_ORDER:
        src = os.path.join(fx, name)
        dst = os.path.join(demo_dir, name)
        if not os.path.exists(src):
            continue
        if not os.path.exists(dst):
            with open(src, encoding="utf-8") as f:
                text = f.read()
            text, _ = _rewrite(text, prs, stories, uuids)
            if name == "org.js" and org_label and org_label != "Org brain":
                text = text.replace('"Org brain · org"', '"%s · org"' % org_label)
            _atomic_write(dst, text)
        srcs.append("feeds/demo/%s" % name)
    return srcs


def clean(data_dir):
    """Remove the demo feeds (the whole `feeds/demo/` dir). Returns True if it existed."""
    import shutil
    demo_dir = _demo_dir(data_dir)
    if os.path.isdir(demo_dir):
        shutil.rmtree(demo_dir, ignore_errors=True)
        return True
    return False


def _main(argv=None):
    ap = argparse.ArgumentParser(
        description="Seed/clean the demo peer feeds under <data-dir>/feeds/demo/.")
    ap.add_argument("--data-dir", default=None,
                    help="task-station data dir (default: paths.data_dir())")
    ap.add_argument("--clean", action="store_true",
                    help="delete <data-dir>/feeds/demo/ and exit")
    a = ap.parse_args(argv)
    ddir = a.data_dir
    if not ddir:
        import paths
        ddir = paths.data_dir()
    if a.clean:
        removed = clean(ddir)
        print("removed feeds/demo/" if removed else "no feeds/demo/ to remove")
        return
    srcs = seed(ddir, feeds.read_self_feed(ddir))
    print("seeded %d demo feed(s): %s" % (len(srcs), ", ".join(srcs)))
    print("run `/todo board` (with --interbrain on/auto) to render them as peer rows")


if __name__ == "__main__":
    _main()
