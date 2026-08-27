# heal_ado.py
"""Reconcile a task against the WORK ITEMS it claims — not against its own log.

WHY THIS EXISTS. `heal.py` reconciles a task with itself: decisions against
decisions, steps against decisions, the digest against its own size. Every check it
has reads ONE record and asks whether that record is internally consistent. That is
worth having and it cannot catch the failure this module exists for, because the
failure is not an inconsistency. It is a record that is perfectly coherent and no
longer resembles the thing it describes.

TWICE, MEASURED, THE SAME SHAPE.

  1. 2026-08-26, session 503-12. Story 3614 on one real board carries 33
     Criteria 2, 23, 24 and 28 specify a per-ROW converging applier with a row
     ledger. Task 503's record described 3614 as, effectively, "seeds out of
     chain" — one criterion out of 33, and criterion 29 at that. A relayed session
     read the record (that is the record's entire purpose), designed a file-level
     checksum ledger from first principles over several hours, and shipped
     something strictly weaker than the specification that already existed. It
     surfaced only because Ryan said, in passing, "I thought this was the direction
     when we initially scoped that portion."

  2. Story 3070 and its PR 1116 sat inside Feature 3064 — task 506's OWN Feature —
     for 25 days, unowned, blocking the promotion pipeline. Task 506's story list
     is hand-maintained, and 3070 was filed after the task was created, so it was
     never in it. Nothing ever asked the Feature what its children were.

Same root cause both times: THE TASK RECORD IS TRUSTED AS IF IT WERE THE SOURCE,
AND NOTHING RECONCILES IT AGAINST THE SOURCE. This module is that reconciliation.

WHAT IS MECHANICAL AND WHAT IS JUDGEMENT — the line is drawn on purpose.

One measured quantity does all the mechanical work: COVERAGE, the fraction of a
criterion's significant vocabulary that appears in the task's own text. It is
deliberately ASYMMETRIC, unlike `heal.word_overlap` — a 20-word criterion fully
absorbed into a 300-word decision scores 1.0 here and ~0.07 as a Jaccard ratio, and
that under-report is exactly the direction that would have hidden 3614. Coverage
partitions a work item's criteria three ways:

    >= ACK_COVERAGE          the log acknowledges it
    CONFLICT_FLOOR .. ACK    the log is in this territory and describes it
                             DIFFERENTLY — the checksum-ledger shape
    < CONFLICT_FLOOR         the log has never been near it

The middle band is a CANDIDATE, not a verdict. Whether a decision CONTRADICTS a
criterion or merely words it differently is a judgement, and this layer is
deterministic and zero-token by contract, so it does not pretend to make it. What
it does instead is put the criterion and the overlapping decision side by side in
the report and make the skill rule on the pair. A check that guessed "contradicts"
would be the same class of mistake as the 604-character field it was written to
replace: a plausible answer where an honest "look at this" belonged.

LAYER RULE. `board` never imports `brain`. The work-item reader is
`brain.ado_tree` and it is reached the way the board's skills reach it — as a
subprocess, zero tokens. The prober is injectable, so every test here runs without
a network, an `az` login, or a subprocess.

DEFAULT OFF. Like `--probe-links`, this is a network probe and `heal.scan` never
runs it unless a prober is passed. A session start must stay free.
"""
import json
import os
import re
import subprocess
import sys

from . import decisions as _decisions
from . import heal

# --------------------------------------------------------------------------- knobs
#
# Both thresholds were fitted against the two measured failures (task 503 / story
# 3614, task 506 / story 3070) and then checked against the tasks that were already
# healthy, because a check that fires on a well-kept task is a check people learn to
# ignore. They are module constants rather than literals so a future fit is one edit.
ACK_COVERAGE = 0.55          # at/above: the log acknowledges this criterion
CONFLICT_FLOOR = 0.35        # at/above but below ACK: same territory, different words
LOSSY_COVERAGE = 0.20        # below: the task's own description misses the source
MIN_CRITERION_WORDS = 4      # thinner than this and coverage is noise, so skip it
PROBE_TIMEOUT = 45

# A task with almost nothing recorded has not "failed to acknowledge" anything — it
# has not started. Below this many current decisions the criteria checks stay quiet,
# so attaching a story never manufactures 33 findings on day one.
MIN_DECISIONS_TO_JUDGE = 8

# A Feature's children in one of these states are FINISHED. Reporting them as
# "absent from the task's list" would bury the one that matters: measured on task
# 503, the unfiltered check produced 91 rows, of which the actionable ones were a
# handful. The 3070 failure was an OPEN, UNOWNED story with an open PR sitting in
# the task's own Feature for 25 days — those two words are the filter.
TERMINAL_STATES = {"closed", "done", "removed", "resolved", "ready for uat"}

CHECKS = (
    ("ado-unreachable", "Work items that could not be read"),
    ("ado-summary-lossy", "Task's description of a work item misses the source"),
    ("ado-criteria-unacknowledged", "Criteria no decision acknowledges"),
    ("ado-criteria-conflict", "Criteria the log describes differently"),
    ("ado-sibling-missing", "Feature children absent from the task"),
)
CHECK_ORDER = [c[0] for c in CHECKS]
CHECK_TITLES = dict(CHECKS)

_WI_URL = re.compile(r"/_workitems/edit/(\d+)")


# ----------------------------------------------------------------- what a task claims
def claimed_items(task):
    """The work items a task claims, as `[{"id", "url", "desc"}, …]` in record order.

    Read from `task["stories"]` — the hand-maintained list — because that list IS the
    thing under test. Its omissions are the 3070 failure and its wrong descriptions
    are the 3614 one, so reconstructing it from somewhere better would measure the
    wrong record."""
    out, seen = [], set()
    for ent in (task or {}).get("stories") or []:
        m = _WI_URL.search(str(ent.get("url") or ""))
        if not m:
            continue
        wid = int(m.group(1))
        if wid in seen:
            continue
        seen.add(wid)
        out.append({"id": wid, "url": ent.get("url") or "",
                    "desc": (ent.get("desc") or "").strip()})
    return out


def task_texts(task):
    """Every piece of the task's own record a criterion could be acknowledged in:
    CURRENT decisions, live steps, the summary, the goal and the state line.

    Superseded decisions are excluded on purpose. A criterion whose only mention was
    superseded is NOT acknowledged — that is the record saying it changed its mind,
    which is precisely what a reconcile wants surfaced rather than absorbed."""
    texts = [t for _, t in live_decisions(task)]
    for st in (task or {}).get("steps") or []:
        if isinstance(st, dict):
            if not st.get("superseded"):
                texts.append(str(st.get("text") or ""))
        elif st:
            texts.append(str(st))
    for key in ("summary", "goal", "state"):
        if (task or {}).get(key):
            texts.append(str(task[key]))
    return [t for t in texts if t.strip()]


def live_decisions(task):
    """Current decisions as `[(index1, text), …]`.

    Read through `decisions.text` / `decisions.is_replaced` — the sanctioned
    accessors — because a decision is stored as a bare STRING until something marks
    it, and reaching for `.get("text")` on the log crashes on the first legacy entry.
    Indices are 1-BASED: they are the numbers `/todo <n> history` prints and the
    numbers `--supersedes` takes, so a finding that names one is directly actionable.

    A decision that was superseded, split or merged is not current. A criterion whose
    only mention is inside a retired decision is NOT acknowledged — that is the
    record saying it changed its mind, which is exactly what a reconcile wants
    surfaced rather than absorbed."""
    out = []
    for i, entry in enumerate((task or {}).get("decisions") or [], start=1):
        if _decisions.is_replaced(entry):
            continue
        body = _decisions.text(entry)
        if body.strip():
            out.append((i, body))
    return out


# ---------------------------------------------------------------------- the measure
def coverage(needle, haystack):
    """Fraction of `needle`'s significant vocabulary present in `haystack`, 0..1.

    ASYMMETRIC BY DESIGN — see the module docstring. `heal.word_overlap` is Jaccard
    and systematically under-reports a short text inside a long one; that is the
    exact direction that hid 3614's criteria inside a wall of decisions, so this
    measure divides by the CRITERION alone and never by the union.

    `heal._significant_words` is reused rather than reimplemented: one tokenizer and
    one stopword list for the whole module, so a tune to either cannot drift them."""
    want = set(heal._significant_words(needle))
    if not want:
        return 0.0
    have = set(heal._significant_words(haystack))
    return len(want & have) / float(len(want))


def best_coverage(needle, texts):
    """The highest coverage `needle` reaches against any one text, with that text's
    position. `(0.0, None)` when nothing covers it. Positions are the caller's — the
    caller passes `[(index, text), …]` so a finding can name decision 41 rather than
    "some decision"."""
    best, where = 0.0, None
    for idx, text in texts:
        c = coverage(needle, text)
        if c > best:
            best, where = c, idx
    return best, where


# ------------------------------------------------------------------- reading the source
def criteria_of(node):
    """A work item's acceptance criteria as `[{"n", "numbered", "text"}, …]`.

    Reads the plain text `brain.ado_tree --no-clip` returns, whose HTML→text pass
    keeps `<ol>` numbering — so `n` here means what "criterion 23" means in the ADO
    UI. Two shapes are recognised because both are in real use on this board:

      NUMBERED  `1.` / `1)` — an `<ol>`, or hand-typed. `numbered=True`, and `n` is
                the story's OWN number, which is the number a person will quote.
      BULLETED  `- ` — a `<ul>`. ADO renders no number, so `n` is the ordinal
                position and `numbered=False`; the reporter says "item 3" rather
                than inventing a "criterion 3" nobody can look up.

    A field that enumerates nothing yields one unnumbered entry holding the whole
    text: the criteria exist, they are simply not broken out."""
    text = node.get("acceptance_criteria") or ""
    if not text.strip():
        return []
    items, current, number, numbered = [], [], None, False
    ordinal = 0

    def flush():
        if current and number is not None:
            body = " ".join(x for x in current if x).strip()
            if body:
                items.append({"n": number, "numbered": numbered, "text": body})

    for line in text.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s+(.*)", line)
        b = None if m else re.match(r"\s*[-•*]\s+(.*)", line)
        if m:
            flush()
            ordinal += 1
            number, numbered, current = int(m.group(1)), True, [m.group(2)]
        elif b:
            flush()
            ordinal += 1
            number, numbered, current = ordinal, False, [b.group(1)]
        elif current:
            current.append(line.strip())
    flush()
    if not items:
        return [{"n": 1, "numbered": False, "text": text.strip()}]
    return items


def criterion_label(entry):
    """"criterion 23" for a numbered one, "item 3" for a bulleted one. The
    distinction is not cosmetic: quoting "criterion 3" of a story whose criteria
    ADO renders as bullets sends a reader looking for a number that is not there."""
    return ("criterion %d" if entry.get("numbered") else "item %d") % entry["n"]


def ado_prober(timeout=PROBE_TIMEOUT, runner=None, python=None, lib=None):
    """A callable `(work_item_id, depth) -> node|None` backed by `brain.ado_tree`.

    SUBPROCESS, NOT AN IMPORT. `board` may not import `brain` (see `tests/brain/
    test_layers.py`), and the reader is zero-token as a subprocess anyway — which is
    the whole reason the board's skills already reach for it this way.

    `--no-clip` is not optional. The clipped view is what returned 604 characters of
    a 9,237-character field and started this; a reconciler reading a preview would
    reproduce the bug it exists to catch.

    Results are memoised per prober, because a Feature and its children are asked for
    repeatedly within one pass and each ask is an HTTPS round trip."""
    cache = {}
    run = runner or _run_ado_tree

    def probe(wid, depth=0):
        key = (int(wid), int(depth))
        if key not in cache:
            cache[key] = run(int(wid), int(depth), timeout, python, lib)
        return cache[key]

    return probe


def _run_ado_tree(wid, depth, timeout, python=None, lib=None):
    """One `python3 -m brain.ado_tree <id> --json --no-clip` call. Returns the parsed
    tree, or None on ANY failure — a network error, a missing `az` login, a
    non-zero exit. None is not silence: `reconcile` turns it into an
    `ado-unreachable` FINDING, because "the source could not be read" and "the
    source agrees with the record" must never look the same from the outside."""
    env = dict(os.environ)
    if lib:
        env["PYTHONPATH"] = lib + os.pathsep + env.get("PYTHONPATH", "")
    # The parent lookup is NOT dropped: the Feature a work item hangs under is how
    # `missing_children` learns what siblings exist, and that is the 3070 check.
    argv = [python or sys.executable, "-m", "brain.ado_tree", str(wid),
            "--json", "--no-clip", "--depth", str(depth)]
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not (out.stdout or "").strip():
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


# ------------------------------------------------------------------------- the checks
def classify_criteria(node, texts, decisions):
    """Every criterion of one work item, sorted into the three bands the module
    docstring defines. Returns `{"acknowledged": [...], "conflict": [...],
    "unacknowledged": [...], "total": n}`; each entry carries `n`, `numbered`,
    `label`, `text`, `coverage` and the covering decision's 1-based index.

    Criteria under MIN_CRITERION_WORDS are dropped rather than judged. A four-word
    criterion has too little vocabulary for coverage to mean anything, and counting
    it "unacknowledged" would be a number with no evidence behind it — which also
    quietly discards the sub-bullets of a nested list ("Warehouse", "Ship date"),
    which are columns, not criteria."""
    out = {"acknowledged": [], "conflict": [], "unacknowledged": [], "total": 0}
    for c in criteria_of(node):
        if len(heal._significant_words(c["text"])) < MIN_CRITERION_WORDS:
            continue
        out["total"] += 1
        cov, _ = best_coverage(c["text"], list(enumerate(texts)))
        dcov, didx = best_coverage(c["text"], decisions)
        entry = dict(c, coverage=round(cov, 3), label=criterion_label(c),
                     decision=didx if dcov >= CONFLICT_FLOOR else None)
        if cov >= ACK_COVERAGE:
            out["acknowledged"].append(entry)
        elif cov >= CONFLICT_FLOOR:
            out["conflict"].append(entry)
        else:
            out["unacknowledged"].append(entry)
    return out


def lossy_description(item, node):
    """Is the task's own one-line description of this work item materially lossy
    against the source? Returns a reason string, or None.

    THE 503 CASE, verbatim: eleven of that task's stories carried the identical desc
    "WS4 — deterministic ADF naming; must land BEFORE any Datawiz regenerate",
    3614 among them — and 3614 is "Test data platform: pipeline-owned fixtures,
    per-run disposable data, and cleanup that survives failure". A copy-paste that
    covered NONE of the source's vocabulary, sitting where a reader looks for what
    the story is."""
    title = node.get("title") or ""
    n_crit = len(criteria_of(node))
    desc = (item.get("desc") or "").strip()
    if not desc:
        if n_crit >= 5:
            return ("no description recorded, and the source carries %d criteria — a "
                    "reader has nothing here telling them to go and read them" % n_crit)
        return None
    cov = coverage(title, desc)
    if cov < LOSSY_COVERAGE:
        return ('recorded as "%s", but the source is titled "%s" — the recorded text '
                "covers %d%% of the title's vocabulary" % (_clip(desc, 90),
                                                           _clip(title, 90),
                                                           round(cov * 100)))
    return None


def missing_children(items, probe, parents=None, owned_elsewhere=None):
    """Work items that are children of a Feature this task claims, are still OPEN,
    and are claimed by NO task on the board — the 3070 failure.

    The Feature is asked what its children ARE. Nothing else can answer it: a
    hand-maintained list cannot know about an item filed after the task was created,
    and that is not a discipline problem to be solved with a reminder.

    TWO FILTERS, both of them the difference between a signal and a wall. Finished
    children (`TERMINAL_STATES`) are not omissions. Children another task already
    owns are not orphans — that is the board working. What survives is what 3070
    was: open, in this task's own Feature, and nobody's."""
    have = {it["id"] for it in items}
    owned = set(owned_elsewhere or ())
    found, seen_parents, seen_kids = [], set(), set()
    for it in items:
        tree = probe(it["id"], 0) or {}
        pid = (tree.get("parent") or {}).get("id")
        for candidate in ([pid] if pid else []) + list(parents or []):
            if not candidate or candidate in seen_parents:
                continue
            seen_parents.add(candidate)
            ptree = probe(candidate, 1) or {}
            root = ptree.get("root") or {}
            for child in root.get("children") or []:
                cid = child.get("id")
                state = (child.get("state") or "").strip()
                if (not cid or cid in have or cid in owned or cid in seen_kids
                        or state.lower() in TERMINAL_STATES):
                    continue
                seen_kids.add(cid)
                found.append({"id": cid, "parent": candidate,
                              "parent_title": root.get("title") or "",
                              "title": child.get("title") or "",
                              "state": state, "url": child.get("url") or ""})
    return found


def _label_list(entries, limit=8):
    """"criteria 3, 6, 8 and 5 more" — or "items 2, 13" when the story's criteria are
    a bulleted list ADO renders with no numbers at all.

    The distinction is not cosmetic: quoting "criterion 3" of a bulleted story sends
    a reader looking for a number that is not there."""
    if not entries:
        return ""
    numbered = entries[0].get("numbered")
    word = ("criteria " if numbered else "items ") if len(entries) > 1 else (
        "criterion " if numbered else "item ")
    shown = word + ", ".join(str(e["n"]) for e in entries[:limit])
    rest = len(entries) - limit
    return shown + (" and %d more" % rest if rest > 0 else "")


def _clip(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# -------------------------------------------------------------------------- the pass
def reconcile(task, probe=None, parents=None, owned_elsewhere=None):
    """The whole ADO reconcile for one task: `{"items": [...], "findings": [...]}`.

    NEVER MUTATES THE TASK — same contract as `heal.scan`, which is what makes it
    safe to run from a read-only path.

    `probe=None` means the check did not RUN, and that is reported as such rather
    than as a clean result. There is exactly one thing worse than a reconciler that
    misses a gap, and it is a reconciler that reports "no gaps" because it never
    looked."""
    items = claimed_items(task)
    if probe is None:
        return {"items": [], "findings": [], "ran": False,
                "reason": "no ADO prober wired — pass --probe-ado", "claimed": len(items)}
    texts = task_texts(task)
    decisions = live_decisions(task)
    judging = len(decisions) >= MIN_DECISIONS_TO_JUDGE
    findings, rows = [], []
    for it in items:
        tree = probe(it["id"], 0)
        node = (tree or {}).get("root") or {}
        if not node:
            findings.append(heal._finding(
                "ado-unreachable", "story %d" % it["id"],
                "could not be read from ADO — the task's claim about it is "
                "unverified, not confirmed"))
            rows.append({"id": it["id"], "reachable": False})
            continue
        bands = classify_criteria(node, texts, decisions)
        row = {"id": it["id"], "reachable": True, "url": node.get("url") or it["url"],
               "title": node.get("title") or "", "state": node.get("state") or "",
               "desc": it.get("desc") or "", "bands": bands,
               "criteria_total": bands["total"]}
        rows.append(row)

        reason = lossy_description(it, node)
        if reason:
            findings.append(heal._finding("ado-summary-lossy",
                                          "story %d" % it["id"], reason))
        if judging and bands["total"] and bands["unacknowledged"]:
            findings.append(heal._finding(
                "ado-criteria-unacknowledged", "story %d" % it["id"],
                "%d of %d acceptance criteria are acknowledged by no current decision "
                "or live step (%s). The record describes this work item; the work item "
                "SPECIFIES it — read them before designing anything here."
                % (len(bands["unacknowledged"]), bands["total"],
                   _label_list(bands["unacknowledged"]))))
        if judging and bands["conflict"]:
            findings.append(heal._finding(
                "ado-criteria-conflict", "story %d" % it["id"],
                "%d criteria sit in territory the log HAS decided on but words "
                "differently (%s). Judge each pair against the decision named beside "
                "it in the ADO RECONCILE section: consistent, or does the decision "
                "contradict the criterion?"
                % (len(bands["conflict"]), _label_list(bands["conflict"]))))

    # GROUPED BY FEATURE, one finding each. Per-CHILD findings put 34 rows on task 503
    # and buried 3070 — the one that actually mattered — in the middle of them. A
    # Feature is also the right unit for the judgement being asked: "should this task
    # own these, or is claiming this whole Feature the thing that is wrong?"
    orphans = {}
    for kid in missing_children(items, probe, parents=parents,
                                owned_elsewhere=owned_elsewhere):
        orphans.setdefault(kid["parent"], []).append(kid)
    for pid in sorted(orphans):
        kids = sorted(orphans[pid], key=lambda k: k["id"])
        findings.append(heal._finding(
            "ado-sibling-missing", "feature %d" % pid,
            '%d OPEN work item(s) hang under Feature %d ("%s"), which this task claims, '
            "and are in NO task's list on this board: %s. Either this task owns them or "
            "claiming the whole Feature is what is wrong — a hand-maintained list "
            "cannot know about anything filed after the task was created."
            % (len(kids), pid, _clip(kids[0]["parent_title"], 60),
               "; ".join('%d "%s" [%s]' % (k["id"], _clip(k["title"], 55),
                                           k["state"] or "?") for k in kids))))

    findings.sort(key=lambda f: (CHECK_ORDER.index(f["check"])
                                 if f["check"] in CHECK_ORDER else len(CHECK_ORDER),
                                 f["ref"]))
    return {"items": rows, "findings": findings, "ran": True,
            "judging": judging, "claimed": len(items)}


# ------------------------------------------------------------------------ the report
def report(result, limit=6):
    """The human/model-facing section. Prints the EVIDENCE for the judgement calls —
    each conflict criterion beside the decision index that covers it — because the
    conflict band is a candidate the reader has to rule on, and a count with no text
    under it cannot be ruled on."""
    if not result or not result.get("ran"):
        reason = (result or {}).get("reason") or "not run"
        return "ADO RECONCILE: not run (%s). %d work item(s) claimed and unverified." % (
            reason, (result or {}).get("claimed", 0))
    lines = ["ADO RECONCILE — the task's record against the work items themselves"]
    if not result["items"]:
        lines.append("  no work items claimed by this task.")
        return "\n".join(lines)
    for row in result["items"]:
        if not row.get("reachable"):
            lines.append("  story %d — UNREADABLE. Claim unverified." % row["id"])
            continue
        b = row["bands"]
        lines.append("  story %d [%s] %s" % (row["id"], row.get("state") or "?",
                                             _clip(row.get("title"), 80)))
        lines.append("    recorded here as: %s" % (_clip(row.get("desc"), 90)
                                                   or "(nothing recorded)"))
        lines.append("    %d criteria — %d acknowledged, %d worded differently, "
                     "%d untouched" % (b["total"], len(b["acknowledged"]),
                                       len(b["conflict"]), len(b["unacknowledged"])))
        for entry in b["conflict"][:limit]:
            lines.append("      ~ %s (decision %s covers %.0f%%): %s"
                         % (entry["label"],
                            entry["decision"] if entry["decision"] is not None else "-",
                            entry["coverage"] * 100, _clip(entry["text"], 150)))
        for entry in b["unacknowledged"][:limit]:
            lines.append("      · %s UNTOUCHED: %s"
                         % (entry["label"], _clip(entry["text"], 150)))
        hidden = max(0, len(b["conflict"]) - limit) + max(0, len(b["unacknowledged"]) - limit)
        if hidden:
            lines.append("      (%d more not shown — this is a preview and says so; "
                         "read the story)" % hidden)
    return "\n".join(lines)
