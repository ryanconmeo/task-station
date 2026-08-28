"""Slash-surface seam: the /todo keyword dispatch (_TODO_SUBCMDS + its handlers), the brief/recap commands, the save block, the memo/search/status/whoami/sessions surfaces, and the argparse specs shared with main()."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
from board.memos import *
from board.sessions import *
from board.render import *
from board.graph import *
from board.boardio import *
from board.cmds.maintain import *
from board.cmds.manage import *
from board.cmds.view import *
from board.cmds.sub import *
import argparse
import json
import os
import re
import sys

import channel as _channel
import decisions as _dec
import heal as _heal
import save as _save

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_brief_provenance_sessions", "_brief_provenance_ledger", "_brief_persist_path",
    "cmd_brief", "cmd_recap",
    "_save_flags", "_save_check_block",
    "_todo_save", "_todo_pin", "_todo_unpin", "_todo_done", "_todo_config",
    "_todo_search", "_todo_native", "_todo_adopt",
    "_memo_ns", "_todo_memo", "_todo_glossary", "_todo_brief", "_todo_heal",
    "_TODO_SUBCMDS",
    "_search_core", "_numeric_ref_detail", "cmd_search",
    "_memo_target", "cmd_memo",
    "cmd_sessions", "cmd_status", "cmd_session_title", "cmd_whoami",
    "_add_glossary_args", "_add_brief_args", "_add_config_args",
]


# ---------------------------------------------------------------- brief ---------

def _brief_provenance_sessions(task):
    """Roster rows for the brief's Sessions table (#463), derived from session_meta:
    hubs first (by ordinal) then workers (newest spawned first). [] when the task has
    no sessions — so the brief's provenance section stays absent (data-gated)."""
    meta = task.get("session_meta") or {}
    if not meta:
        return []
    ensure_ordinals(task)
    def _key(kv):
        _sid, m = kv
        if m.get("role") == "hub":
            o = m.get("ordinal")
            return (0, o if o is not None else float("inf"))
        return (1, -(m.get("spawned_at") or m.get("ts") or 0))
    rows = []
    for sid, m in sorted(meta.items(), key=_key):
        role = m.get("role") or "unknown"
        if role == "hub":
            rows.append({"ordinal": ordinal_label(task, sid) or "",
                         "kind": "hub", "name": sid[:8], "model": m.get("model") or "",
                         "status": m.get("status") or "",
                         "spawned": rel_time(m.get("spawned_at") or m.get("ts"))})
        else:
            rows.append({"ordinal": "", "kind": role,
                         "name": m.get("name") or sid[:8], "model": m.get("model") or "",
                         "status": m.get("status") or "",
                         "spawned": rel_time(m.get("spawned_at") or m.get("ts"))})
    return rows


def _brief_provenance_ledger(task, limit=5):
    """The last `limit` hub<->worker interactions for the brief (#463), oldest→newest.
    [] when the task has no ledger (data-gated)."""
    led = task.get("ledger") or []
    if not led:
        return []
    meta = task.get("session_meta") or {}
    out = []
    for e in led[-limit:]:
        actor = ("%s-%s" % (task.get("seq"), e["actor_ordinal"])
                 if e.get("actor_ordinal") is not None
                 else (e.get("actor") or "?")[:8])
        worker = ((meta.get(e.get("worker")) or {}).get("name")
                  or (e.get("worker") or "")[:8])
        out.append({"when": rel_time(e.get("ts")), "actor": actor,
                    "action": e.get("action"), "worker": worker,
                    "detail": e.get("detail")})
    return out


def _brief_persist_path(task, out, session):
    """Persist task['brief_path'] = out through mutate(), then emit task.updated and
    sync Obsidian. Shared by the `render` and `path` actions so a brief is findable
    the same way however it was produced — the contract-v2 note frontmatter carries
    brief_path automatically once it is on the record."""
    def _apply(t):
        t["brief_path"] = out
    updated = g("mutate")(task["id"], _apply)
    if updated is not None:
        _stream_emit("task.updated", updated,
                     _stream_updated_data(updated, ["brief_path"]), session)
        _obsidian_sync(updated)


def cmd_brief(a):
    """`task-station brief [render|path] [--task|--session] [--spec FILE]`.

    **path** — resolve the task, create the artifact dir, persist and print
    brief_output_path(task). Reads no spec. This is the model-authored flow: the
    `/brief` skill asks for the path, then writes its own HTML there.

    **render** (default, retained for back-compat) — read a brief-spec (JSON) from
    --spec FILE or stdin, lazy-import lib/brief (pure stdlib, host-agnostic), render
    it against the task's glossary into the frozen house-style template, write to
    brief_output_path (makedirs), persist task['brief_path'] and print the path."""
    task = _resolve_glossary_task(a)
    if not task:
        print("brief: no task — attach a session or pass --task <ref>.")
        return

    session = getattr(a, "session", None)

    if (getattr(a, "action", None) or "").strip().lower() == "path":
        out = brief_output_path(task)
        try:
            os.makedirs(os.path.dirname(out), exist_ok=True)
        except OSError as e:
            print("brief: cannot create %s: %s" % (os.path.dirname(out), e))
            return
        _brief_persist_path(task, out, session)
        print(out)
        return

    src = getattr(a, "spec", None)
    try:
        if src:
            with open(os.path.expanduser(src), encoding="utf-8") as f:
                raw = f.read()
        else:
            raw = sys.stdin.read()
    except OSError as e:
        print("brief: cannot read spec: %s" % e)
        return
    raw = (raw or "").strip()
    if not raw:
        print("brief: empty spec — pass --spec FILE or pipe the brief-spec JSON on stdin.")
        return
    try:
        spec = json.loads(raw)
    except ValueError as e:
        print("brief: spec is not valid JSON: %s" % e)
        return

    import brief as _brief   # lazy: keep the renderer off the hot engine paths
    glossary = _normalize_glossary(task.get("glossary"))
    # Inject the task's session roster + worker ledger tail (#463) unless the spec
    # already supplies them. Data-gated in the renderer: empty lists → no section.
    if isinstance(spec, dict):
        spec.setdefault("sessions", _brief_provenance_sessions(task))
        spec.setdefault("ledger", _brief_provenance_ledger(task))
    html = _brief.render_brief(spec, glossary)

    out = brief_output_path(task)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        print("brief: cannot write %s: %s" % (out, e))
        return

    _brief_persist_path(task, out, session)
    print(out)


def cmd_recap(a):
    """`task-station recap [--week YYYY-Www] [--open] [--json] [--no-scan]
    [--auto-if-due]` — build the strictly-private weekly usage recap: a local,
    self-contained HTML one-pager under <data_dir>/recaps/<week>.html summarizing what
    you did, what it cost, and concrete guidance to use LLMs more effectively.

    Reads only the persisted ledger + task store (no transcript IO of its own); by
    default it first runs an incremental scan-all so the week's numbers are current
    (--no-scan skips it). --auto-if-due is the hook entry point: it self-gates on the
    `recap` config toggle + a once-per-week stamp and is a silent, fail-open no-op
    otherwise. Output is machine-local and added to NO sync boundary."""
    import config
    import recap as _recap
    store = _backend()

    if getattr(a, "auto_if_due", False):
        # Hook path: strictly gated + silent. auto_generate_if_due swallows all errors.
        path = _recap.auto_generate_if_due(store)
        if path and not getattr(a, "quiet", False):
            print(path)
        return

    if not config.usage_tracking_enabled():
        print("recap: usage tracking is off (config --usage-tracking off) — no data "
              "to summarize.")
        return

    if not getattr(a, "no_scan", False):
        try:
            _usage_engine().scan_all(store)      # freshen the ledger; best-effort
        except Exception:
            pass                                  # stale numbers beat a crashed recap

    try:
        result = _recap.generate(store, week=getattr(a, "week", None))
    except ValueError as e:
        print("recap: %s" % e)
        return

    if getattr(a, "as_json", False):
        print(json.dumps(result["aggregates"], ensure_ascii=False, indent=2))
        return

    print(result["path"])
    if getattr(a, "open", False):
        _open_path(result["path"])


def _save_flags(rest):
    """The two flags `/todo save` takes, read WITHOUT argparse: `(verbose, check)`.

    Deliberately a token scan rather than a parser. `/save` has always ignored trailing
    free text — `/todo SAVE please checkpoint this` is a documented, tested shape — and
    an argparse spec would turn that into a usage error. `rest` also arrives as a list
    from a couple of internal callers, so both shapes are accepted."""
    raw = rest if isinstance(rest, str) else " ".join(rest or [])
    toks = raw.split()
    return ("--verbose" in toks), ("--check" in toks)


def _save_check_block(task, seq, session):
    """`/todo save --check` — the MECHANICAL cold-read verification, and NOTHING else.

    The gap report alone: no capture checklist, no command templates, no digest. It is
    what step 6 of the save flow runs after a write to prove the gaps actually closed,
    and it is READ-ONLY — it stamps nothing, clears no flag and writes no session
    record. That is the same contract `heal --scan` keeps, and for the same reason: a
    verification pass that mutates what it verifies cannot be trusted about either."""
    report = _save.gap_report(task, digest_chars=len(_format_detail(task, session)))
    out = ["[SAVE] Task #%s [%s] — session %s"
           % (seq, task["id"][:8], (session or "")[:8]),
           "COLD-READ CHECK — the gap report, re-run. This is READ-ONLY: nothing was "
           "changed, no checkpoint was stamped."]
    out.extend(_save.gap_lines(report))
    out.append("")
    out.append("VERDICT: %s"
               % ("PASS — every named slot carries something and the state leads with "
                  "`NEXT:`" if _save.is_clean(report)
                  else "FAIL — patch each line above with another `update --task %s …`, "
                       "then re-run `/todo save --check`" % seq))
    return "\n".join(out)


def _todo_save(a, rest):
    """/todo save — checkpoint the CURRENT task's context into its digest so a
    FRESH session can resume with zero context loss. Prints a model-facing [SAVE]
    block: a GAP REPORT of what the digest is MISSING + the named-slot capture
    checklist + the exact `update` templates + the mechanical cold-read check.
    Records THIS session (with its cwd) as a transcript backstop for `/todo <n> -s`.
    Mints NO session and performs NO pin — capture only.

    IT DOES NOT DUMP THE DIGEST, and it does NOT STAMP. Both are deliberate reversals:

      * The caller has been working this task all along, so it already HAS the digest.
        Measured on one real task, echoing it back cost 71,516 characters of which
        71,271 — 99.7% — were the dump. What the caller does not have is the list of
        what is missing, which is what `save.gap_report` computes. `--verbose` still
        prints the full digest; `--check` prints the gap report alone, which is the
        mechanical cold-read re-check after a write.
      * `last_full_save_ts` claims a full checkpoint was CAPTURED. Emitting a prompt
        captures nothing, so this records only that a save was STARTED; the stamp
        belongs to the `update` that writes a summary AND a state (see
        `save.is_checkpoint_write`)."""
    task = _session_task(a.session)
    if not task:
        print(_NO_TASK_ATTACHED)
        return
    verbose, check_only = _save_flags(rest)
    seq = task.get("seq", task["id"][:8])
    if check_only:
        # `--check` is READ-ONLY, and that is its whole contract — the same one
        # `heal --scan` keeps. It runs BEFORE any of the writes below precisely because
        # a verification pass that mutates the thing it is verifying is the bug this
        # release exists to fix, one surface over: it would print "nothing was changed"
        # having just changed something.
        print(_save_check_block(task, seq, a.session))
        return
    # A save has been STARTED — that, and only that, is what emitting the block proves.
    # The two staleness flags still clear here, and they are a different kind of claim:
    # they gate NUDGES ("your digest looks stale", "run /todo save NOW"), and the nudge
    # has been delivered and acted on the moment this block is read. The checkpoint
    # STAMP is a claim about captured content, so it waits for the write.
    _save.mark_save_started(task)
    clear_digest_dirty(task)
    clear_pressure_nudged(task)
    save_task(task)
    # Notifications that a save HAPPENED (note + daily-note entry + feed event) — the
    # record that a checkpoint was CAPTURED is `last_full_save_ts`, and that one waits
    # for the write. These fire here because the export and the feed track activity,
    # not the stamp.
    _obsidian_event(task, "checkpoint")
    _stream_emit("task.checkpoint", task, _stream_digest(task), a.session)
    # Transcript backstop: make sure THIS session is a findable resume candidate so a
    # later fresh session can `/todo <n> -s` back into the FULL transcript if the digest
    # ever misses a detail. Record its cwd in session_meta (authoritative from the
    # transcript when available; os.getcwd() fallback) WITHOUT pinning. Only writes when
    # the entry is missing or lacks a cwd, so an existing record is left intact.
    if a.session:
        meta = task.setdefault("session_meta", {})
        entry = meta.get(a.session)
        if not entry or not entry.get("cwd"):
            path = g("_find_session_path")(a.session)
            cwd = (_session_cwd(path) if path else None) or os.getcwd()
            meta[a.session] = {"cwd": cwd, "ts": _now(), "role": "hub"}
            save_task(task)
    # The digest is rendered ONCE and, by default, only MEASURED — its length is the
    # "what a fresh session loads" number in the gap report. Rendering it costs the same
    # as it always did; what changed is that the 71,271 characters no longer go to the
    # caller, who has been working this task and already has them.
    detail = _format_detail(task, a.session)
    report = _save.gap_report(task, digest_chars=len(detail))
    out = []
    out.append("[SAVE] Task #%s [%s] — session %s"
               % (seq, task["id"][:8], (a.session or "")[:8]))
    out.append("Checkpoint this task so a FRESH session — with NO memory of this "
               "conversation — can resume with ZERO context loss. AMEND what the GAP "
               "REPORT names via the `update` command; do NOT rewrite slots that are "
               "already accurate — a save is an amendment, not a rewrite.")
    # THE SAVE GATE. `--summary` REPLACES the summary wholesale, so writing one from a
    # decision set that still contains refuted entries bakes the drift into the very
    # first field anyone reads. One line, and it does NOT block and does NOT run the
    # heal — this is a warning at a decision point, not a gate. Fail-open.
    #
    # THE SCAN IS RUN ONCE HERE AND HANDED TO BOTH READERS. `gate_line` would run its own
    # otherwise, and the gate's leading clause is "the scan found N issue(s)" — a count
    # with no subject, which left the only way to learn whether it mattered being to go
    # run the scan again, at the moment the reader had decided to checkpoint instead. So
    # the first finding is NAMED in parentheses off the same result: no second corpus
    # pass, one extra clause, and the judgement can be made in place.
    try:
        result = _heal.scan(task)
        gate = _heal.gate_line(task, result=result)
        named = _heal.first_finding_line(result) if gate else None
    except Exception:
        gate = named = None
    if gate:
        out.append("[task-station] %s%s The summary you are about to REPLACE would be "
                   "written from a decision set that has not been reconciled."
                   % (gate, (" (first: %s)" % named) if named else ""))
    out.append("")
    # THE GAP REPORT REPLACED THE DIGEST DUMP. See `_todo_save`'s docstring for the
    # measurement; the short version is that echoing the digest back to the session that
    # wrote it was 99.7% of this block's cost and told it nothing it did not know.
    out.append("GAP REPORT — what the digest is MISSING. The digest itself is NOT "
               "reprinted: you have been working this task, so you already have it "
               "(`/todo save --verbose` dumps it if you genuinely do not).")
    out.extend(_save.gap_lines(report))
    if verbose:
        out.append("")
        out.append("CURRENT DIGEST (--verbose)")
        out.append(detail)
    out.append("")
    out.append("CAPTURE CHECKLIST — the reference for each slot. Write ONLY the ones the "
               "GAP REPORT named; leave an accurate slot alone. Fill EVERY slot you do "
               "write with SPECIFICS (exact paths, names, values, commands — never vague "
               "summaries):")
    out.append("  1. GOAL (--goal): the objective — what \"done\" concretely looks like.")
    out.append("  2. NEXT ACTION (--state): the state line MUST LEAD with "
               "`NEXT: <the concrete first move the resumed session should make>`, then "
               "the current standing. Specific enough to act on immediately.")
    out.append("  3. STEPS (--step-add / --step-done N): the FULL plan as a checklist, "
               "marking done vs not-done accurately — INCLUDE the not-yet-started steps.")
    out.append("     • A step gone STALE (the plan moved on, or it names something "
               "retired) is retired with `--step-supersede <n>` — add the corrected step "
               "in the same call and it is recorded as the replacement. Do NOT tick it "
               "done (nobody did it) and do NOT add a warning step about it. It leaves "
               "the checklist and both sides of the n/m count, stays in `/todo %s "
               "history`, and `--step-restore <n>` undoes it." % seq)
    out.append("  4. DECISIONS + WHY (--decision, one per): every material choice AND its "
               "rationale — INCLUDING approaches TRIED and REJECTED and why, so the resume "
               "never re-explores dead ends.")
    out.append("     • REPLACING an earlier call? Add the new decision with "
               "`--supersedes <n>` (the number from `/todo %s history`, repeatable). The "
               "old one then vanishes from this digest instead of sitting here "
               "contradicting the new one — a refuted decision left visible is worse "
               "than no decision." % seq)
    out.append("     • Nothing is hidden by age: EVERY still-current decision renders in "
               "this digest, however old and however many. A decision leaves the digest "
               "only by ceasing to be true (`--supersedes`, or `heal`'s split/merge).")
    out.append("     • ARCHITECTURE SPINE — a rule the rest of the work must obey? Add it "
               "with `--pin` (or `--pin-decision <n>`). A pin is READING ORDER, not "
               "visibility: pinned decisions sort FIRST (marked ★), then everything else "
               "oldest-first. No limit; keep the pinned set to the spine so leading with "
               "it still means something.")
    out.append("     • ONE decision per --decision, atomic. Past %d chars you get an "
               "advisory suggesting `heal --split` — it is a SUGGESTION and the write "
               "always succeeds in full, so never drop a fact or fake two entries out of "
               "one to get under it." % _dec.LONG_DECISION_CHARS)
    out.append("  5. CONTEXT SNAPSHOT (--summary, REPLACE): rewrite `summary` to the CURRENT "
               "truth — a lean structured snapshot (this REPLACES the summary wholesale; keep "
               "it the present state, NOT a running log — do NOT dump the history into it, the "
               "why-trail lives in decisions + --log, read it back via `/todo <n> history`). It "
               "must EXPLICITLY cover —")
    out.append("       • Files / modules touched or relevant, with PATHS, and how they "
               "fit together.")
    out.append("       • The repo / branch / worktree / environment + any auth / config / "
               "tooling quirks.")
    out.append("       • Commands to build / test / run / reproduce.")
    out.append("       • Constraints & gotchas — \"watch out for X\", \"never do Y\".")
    out.append("       • Open questions / blockers awaiting a decision.")
    out.append("       • The user's most recent intent (what they last asked for), in "
               "their words.")
    out.append("     • The summary you replace is NOT destroyed: it is kept, append-only, "
               "and `update --task %s --restore-summary` puts the previous one back "
               "(`--restore-summary <n>` for an older version; `/todo %s history` lists "
               "them). A thin save can no longer silently lose a good summary." % (seq, seq))
    out.append("  5b. LOG (--log): one dated line for a milestone/finding worth keeping in "
               "history (does not load on normal resume). Exactly ONE per save.")
    out.append("  6. LINKS (--pr / --story): PRs and work-items.")
    out.append("")
    out.append("Command templates (seq %s filled in — one call or several):" % seq)
    out.append("    task-station update --task %s \\" % seq)
    out.append("      --goal '<what done looks like>' \\")
    out.append("      --state 'NEXT: <concrete first move> — <current standing>' \\")
    out.append("      --step-add '<not-yet-started step>' --step-done <N> \\")
    out.append("      --decision '<decision + why — incl. what was tried & rejected>' \\")
    out.append("      --log '<vX.Y.Z shipped: what — or a finding worth keeping in history>' \\")
    out.append("      --pr '<url>' --story '<url>' \\")
    out.append("      --summary '<CURRENT snapshot: files+paths · branch/env · commands · "
               "gotchas · open questions · user's latest intent>'")
    out.append("    (--summary REPLACES the summary wholesale — use it for the current "
               "snapshot; --append-summary only adds. History goes to --decision / --log, "
               "not into --summary.)")
    out.append("    " + _cli_fallback())
    out.append("")
    # THE STAMP BELONGS TO THE WRITE, and the block has to say so — otherwise the next
    # reader assumes running `/save` was the checkpoint, which is precisely the belief
    # that let an empty summary sit under a `last full save just now`.
    out.append("THIS BLOCK DID NOT STAMP A CHECKPOINT. `last_full_save_ts` means \"a full "
               "checkpoint was CAPTURED\", and printing a prompt captures nothing — all "
               "this recorded is that a save was STARTED. The stamp lands on the `update` "
               "that writes a `--summary` AND a `--state` together, because that pair IS "
               "the checkpoint; no flag declares it, so no one can claim one without "
               "writing it.")
    out.append("")
    out.append("COLD-READ CHECK — after the write, and MECHANICAL, not a feeling: every "
               "named slot must be non-empty and `state` must begin with `NEXT:`. The "
               "stamping `update` reports any that still fail; `/todo save --check` "
               "re-runs the same check on demand. Then the judgement half: re-read the "
               "digest as if you have NO memory of this conversation and PATCH anything "
               "ambiguous or assumed with another `update`.")
    out.append("")
    out.append("Not pinned — /todo save only captures. DO NOT pin a session or open / "
               "resume anything. If a detail is ever missing, the trail is recoverable: "
               "`/todo %s history` (the decisions + log record) or `/todo %s -s` "
               "(this session's full transcript)." % (seq, seq))
    print("\n".join(out))


def _todo_pin(a, rest):
    """/todo pin — pin THIS session as the attached task's resume target (same as
    the standalone /pin). No --new: pins the current session."""
    task = _session_task(a.session)
    if not task:
        print(_NO_TASK_ATTACHED)
        return
    print(_pin_one(str(task.get("seq") or task["id"]), a))


def _todo_unpin(a, rest):
    """/todo unpin [n,…] — drop the pinned resume session. With a numeric list,
    unpins those task(s); bare, unpins THIS session's attached task (inverse of
    /todo pin). Reuses cmd_unpin."""
    ns = argparse.Namespace(session=a.session, task=(rest or None))
    cmd_unpin(ns)


def _todo_done(a, rest):
    """/todo done [n,…] — close the current session's attached task, or the
    task(s) named by number. Reuses cmd_done (does NOT close the terminal window —
    you're mid-session; that's intended)."""
    ns = argparse.Namespace(session=a.session, task=(rest or None))
    cmd_done(ns)


def _todo_config(a, rest):
    """/todo config [flags] — route to the config console. Everything after the
    keyword is tokenized (shlex) and parsed by the same argparse spec cmd_config
    uses, then dispatched in-process so config prints verbatim."""
    import shlex
    import config
    parser = argparse.ArgumentParser(prog="/todo config", add_help=False)
    _add_config_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest))
    except SystemExit:
        return   # argparse already reported the bad flag/usage
    config.cmd_config(ns)


def _todo_search(a, rest):
    """/todo search [<--open|--closed|--all>] <terms> — the search surface on the
    /todo command (mirrors the standalone `search` subcommand's tier-1 output).
    Defined here (above _TODO_SUBCMDS) because that dict literal references it at
    module-load; the _search_core/_format_search it calls resolve at runtime."""
    rest = (rest or "").strip()
    want = "all"
    m = re.match(r"^--(open|closed|all)\b\s*(.*)$", rest)
    if m:
        want, rest = m.group(1), m.group(2).strip()
    if not rest:
        print("search: give one or more terms, e.g. /todo search auth token")
        return
    detail = _numeric_ref_detail(rest, a.session)
    if detail is not None:
        print(detail)
        return
    print(_format_search(rest, _search_core(rest, want), want))


# Reserved /todo leading keywords → handler(a, rest). Checked before the numeric/
# ref parsing; each triggers only on the exact leading token (case-insensitive).
def _todo_native(a, rest):
    """/todo native — read-only listing of Claude Code's recent native task lists."""
    print(_format_native())


def _todo_adopt(a, rest):
    """/todo adopt <list-prefix>:<id> — promote a native task into a durable station
    task (read-only on the native side)."""
    cmd_adopt(argparse.Namespace(native=(rest or "").strip() or None))


def _memo_ns(**kw):
    """An argparse.Namespace for cmd_memo with every optional field defaulted, so the
    slash surface never trips a getattr on a flag it doesn't spell."""
    ns = dict(sub=None, task=None, id=None, text=None, session=None,
              decision=None, memory=None, noop=None, corrects=None)
    ns.update(kw)
    return argparse.Namespace(**ns)


def _todo_memo(a, rest):
    """/todo memo — hand a fact/decision to a task's session(s). Grammar:
        /todo memo <n> <text…>        send to task n FROM this session
        /todo memo ack <id8> <TEXT>   ack, promoting the memo to a decision
        /todo memo ack <id8> memory:<slug>   ack, folded into that memory note
        /todo memo ack <id8> noop:<reason>   ack, no durable change needed
        /todo memo show [<n>] [<id8>] list the attached/nth task's memos, or one full body
    An ack MUST carry one of the three dispositions — a bare `/todo memo ack <id8>` is
    an error naming all three. Routes to cmd_memo so the CLI + slash surfaces share one
    code path."""
    toks = (rest or "").split()
    if not toks:
        cmd_memo(_memo_ns(sub="show", session=a.session))
        return
    head = toks[0].lower()
    if head == "ack":
        mid = toks[1] if len(toks) > 1 else None
        # Everything after the id8 is the disposition. `memory:` / `noop:` select the two
        # non-decision dispositions; anything else is the promote-to-decision text.
        text = rest.split(None, 2)[2].strip() if len(toks) > 2 else ""
        kw = {}
        low = text.lower()
        if low.startswith("memory:"):
            kw["memory"] = text[len("memory:"):].strip()
        elif low.startswith("noop:"):
            kw["noop"] = text[len("noop:"):].strip()
        elif text:
            kw["decision"] = text
        cmd_memo(_memo_ns(sub="ack", id=mid, session=a.session, **kw))
        return
    if head == "show":
        rest_toks = toks[1:]
        task_ref = None
        mid = None
        for tk in rest_toks:
            if tk.isdigit() and task_ref is None:
                task_ref = tk
            else:
                mid = tk
        cmd_memo(_memo_ns(sub="show", task=task_ref, id=mid, session=a.session))
        return
    # Default: send — first token is the target task number, the remainder is the body.
    ref = toks[0]
    body = rest[len(toks[0]):].strip()
    cmd_memo(_memo_ns(sub="send", task=ref, text=body, session=a.session))


def _todo_glossary(a, rest):
    """/todo glossary [flags] — route to the glossary console. Everything after the
    keyword is tokenized (shlex) and parsed by the SAME argparse spec cmd_glossary
    uses; this session is injected as --session so task resolution matches /glossary."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo glossary", add_help=False)
    _add_glossary_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return
    ns.session = a.session      # the /todo dispatch owns the session, not `rest`
    cmd_glossary(ns)


def _todo_brief(a, rest):
    """/todo brief [flags] — route to the brief renderer (parity with /brief).
    Tokenized + parsed by the SAME spec cmd_brief uses; this session is injected."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo brief", add_help=False)
    _add_brief_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return
    ns.session = a.session
    cmd_brief(ns)


def _todo_heal(a, rest):
    """/todo heal [--scan|--apply|--all|<n>] — the reconcile pass on the current task
    (or the one named). Everything after the keyword is tokenized and parsed by the same
    spec `cmd_heal` uses, so `/todo heal --scan` and `task-station heal --scan` behave
    identically. A bare `/todo heal` is a DRY RUN and changes nothing."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo heal", add_help=False)
    parser.add_argument("ref", nargs="?", default=None)
    parser.add_argument("--task", default=None)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--all", dest="all", action="store_true")
    parser.add_argument("--split", type=int, default=None)
    parser.add_argument("--merge", default=None)
    parser.add_argument("--into", default=None)
    parser.add_argument("--mark-healed", dest="mark_healed", action="store_true")
    parser.add_argument("--note", default=None)
    parser.add_argument("--dispose-acks", dest="dispose_acks", default=None)
    parser.add_argument("--decision", nargs="?", const=True, default=None)
    parser.add_argument("--memory", default=None)
    parser.add_argument("--noop", default=None)
    # The ledger, the cheap candidate view and the two opt-ins. Mirrored here rather than
    # shared with the subparser because `/todo heal` parses a REST STRING and the top-level
    # `heal` parses argv — but every flag must exist in both, or `/todo heal --candidates`
    # dies on `unrecognized arguments` while `heal --candidates` works, which is the one
    # class of divergence a user actually hits.
    parser.add_argument("--dismiss", action="append", default=None)
    parser.add_argument("--undismiss", action="append", default=None)
    parser.add_argument("--why", default=None)
    parser.add_argument("--dismissals", action="store_true")
    parser.add_argument("--candidates", action="store_true")
    parser.add_argument("--goal-reviewed", dest="goal_reviewed", action="store_true")
    parser.add_argument("--probe-links", dest="probe_links", action="store_true")
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return                       # argparse already reported the bad flag/usage
    ns.session = a.session
    # A bare leading number is the task ref (`/todo heal 12`), matching how the other
    # /todo subcommands take one. The FOLD ITSELF belongs to `cmd_heal`
    # (`_heal_positional_ref`), which the top-level `heal` subparser reaches too — one
    # place deciding what a positional means, and one place refusing the combinations it
    # cannot mean. A second precedence rule here is how two surfaces start disagreeing
    # about which task a command was aimed at.
    cmd_heal(ns)


_TODO_SUBCMDS = {
    "save": _todo_save,
    "heal": _todo_heal,
    "pin": _todo_pin,
    "unpin": _todo_unpin,
    "done": _todo_done,
    "config": _todo_config,
    "native": _todo_native,
    "adopt": _todo_adopt,
    "search": _todo_search,
    "memo": _todo_memo,
    "glossary": _todo_glossary,
    "brief": _todo_brief,
}


def _search_core(query, want="all", limit_shown=SEARCH_HITS_SHOWN):
    """Run the ranked search, load each hit, apply the status filter, and keep the
    top `limit_shown`. Returns [(task, snippet), …] in rank order. `want` is
    all|open|closed (open = the board: open + active)."""
    rows = []
    for h in search_tasks(query, limit=SEARCH_SCAN_LIMIT):
        task = load_task(h["id"])
        if not task:
            continue
        if want == "open" and is_closed(task):
            continue
        if want == "closed" and not is_closed(task):
            continue
        rows.append((task, h.get("snippet") or ""))
        if len(rows) >= limit_shown:
            break
    return rows


def _numeric_ref_detail(query, session):
    """A bare all-digit search query (e.g. `search 362`) is a lookup by the task's
    display number (#seq), not a text search — resolve it to that task's read-only
    digest so a numeric lookup never falsely reports "no match". Returns the
    formatted detail string, or None to fall through to text search (query isn't a
    lone number, or no task carries that number — e.g. a PR/story number)."""
    q = (query or "").strip()
    if not q.isdigit():
        return None
    task = resolve_ref(q)
    if not task:
        return None
    return _format_detail(task, session, attached=False)


def cmd_search(a):
    """`task-station search <terms>` — ranked cross-task search (tier 1), or
    `--detail <seq>` to print one task's full read-only digest."""
    if getattr(a, "detail", None):
        task = resolve_ref(a.detail) or load_task(a.detail)
        if not task:
            print("No task matching '%s'." % a.detail)
            return
        print(_format_detail(task, a.session, attached=False))
        return
    query = " ".join(a.terms).strip()
    if not query:
        print("search: give one or more terms, e.g. task-station search auth token")
        return
    detail = _numeric_ref_detail(query, a.session)
    if detail is not None:
        print(detail)
        return
    want = "open" if getattr(a, "open", False) else \
           "closed" if getattr(a, "closed", False) else "all"
    print(_format_search(query, _search_core(query, want), want))


def _memo_target(a):
    """Resolve the task for a `memo ack`/`show`: an explicit --task (any seq/id-prefix)
    else the acting session's attached task. Returns the task dict or None."""
    ref = getattr(a, "task", None)
    if ref:
        return resolve_ref(ref) or load_task(ref)
    tid = get_link(getattr(a, "session", None))
    if tid and tid != SKIP_SENTINEL:
        return load_task(tid)
    return None


def cmd_memo(a):
    """`task-station memo send|ack|show` — hand a fact/decision to a task's working
    session(s). `send --task <ref>` posts onto any task (attached or not, cross-task via
    resolve_ref); `ack`/`show` default --task to the acting session's attached task.
    A bad ref / unknown id prints ONE error line and returns (exit 0, like add-event),
    so a best-effort caller never aborts."""
    sub = getattr(a, "sub", None)
    if sub == "send":
        ref = getattr(a, "task", None)
        task = (resolve_ref(ref) or load_task(ref)) if ref else None
        if not task:
            print("memo: no task matching %r" % ref)
            return
        text = getattr(a, "text", "") or ""
        if not text.strip():
            print("memo: --text is required (the memo body)")
            return
        corrects = [c for c in (getattr(a, "corrects", None) or []) if str(c).strip()]
        # THE PERMISSION BOUNDARY, CHECKED BEFORE ANYTHING IS WRITTEN. A memo is now a
        # delivery — the channel carries it to a running session — so a memo asking a peer
        # to perform something THIS session was denied is laundering, and it is refused
        # here rather than left to the receiver. Loud (exit 2) on purpose: every other
        # error on this path is a best-effort no-op, and a security refusal that returned
        # 0 would read to a driver exactly like a send that worked.
        sender_sid = getattr(a, "session", None)
        reason = _channel.launder_reason(
            text, from_sid=sender_sid,
            from_task=(get_link(sender_sid) if sender_sid else None))
        if reason:
            print(reason)
            try:
                sender = load_task(get_link(sender_sid)) if sender_sid else None
                if sender:
                    add_event(sender, "channel",
                              "refused a memo send — %s" % reason[:120], sender_sid)
                    sender["updated_ts"] = _now()
                    save_task(sender)
            except Exception:                           # noqa: BLE001
                pass
            sys.exit(2)
        memo = memo_send(task, text, from_sid=sender_sid, corrects=corrects)
        task["updated_ts"] = _now()
        save_task(task)
        print("memo %s → task #%s (%s)"
              % (memo["id"][:8], task.get("seq", task["id"][:8]), task["title"]))
        if corrects:
            print("  corrects: %s — the ack will require a disposition that engages it."
                  % ", ".join(corrects))
        else:
            # M4 backstop: `--corrects` only helps when the sender remembers it. Warn on
            # correction-shaped language; never block — the sender may have good reason.
            hits = correction_language(text)
            if hits:
                print("  ⚠ this reads like a correction (matched: %s) but declares no "
                      "--corrects target." % ", ".join(hits))
                print("    Add `--corrects <memory-slug|decision:N|memo-id8>` so the ack "
                      "has to engage what it replaces.")
        return

    task = _memo_target(a)
    if not task:
        print("memo: no task — pass --task <ref>, or attach this session first.")
        return

    if sub == "ack":
        memo, err = _memo_by_prefix(task, getattr(a, "id", None))
        if err:
            print(err)
            return
        sid = getattr(a, "session", None)
        if not sid:
            print("memo ack: --session <your-session-id> is required.")
            return
        dec = getattr(a, "decision", None)
        # M1: an ack must carry EXACTLY ONE disposition. A bare ack is refused — it was
        # the shape that let a correction be acknowledged and never integrated.
        disp, err = memo_ack_disposition(decision=dec,
                                         memory=getattr(a, "memory", None),
                                         noop=getattr(a, "noop", None))
        if err:
            print(err)
            corrects = memo_corrections(memo)
            if corrects:
                print("memo %s CORRECTS %s — it cannot be acked without saying what you "
                      "did about that." % (memo["id"][:8], ", ".join(corrects)))
            return
        decisions_before = len(task.get("decisions") or [])
        result = memo_ack(task, memo, sid,
                          promote=(disp["kind"] == "decision"),
                          decision_text=(disp["value"] if disp["kind"] == "decision" else None),
                          disposition=disp)
        task["updated_ts"] = _now()
        save_task(task)
        if result == "already":
            print("memo %s already acked by %s." % (memo["id"][:8], sid[:8]))
            return
        tail = {"decision": " → promoted to a decision",
                "memory": " → folded into memory note '%s'" % disp["value"],
                "noop": " → no durable change (%s)" % disp["value"]}[disp["kind"]]
        print("memo %s acked by %s%s." % (memo["id"][:8], sid[:8], tail))
        # A promoted memo body is UNCAPPED, so this is the other way a very long
        # decision gets written. Same advisory, same rule: it is already stored in
        # full, and this only suggests splitting it. Gated on the log actually having
        # GROWN, so a promote that no-oped on blank text can't warn about the entry
        # that was already there.
        n = len(task.get("decisions") or [])
        if n > decisions_before:
            warn = _dec.length_warning(task["decisions"][n - 1], n)
            if warn:
                print("  ⚠ %s" % warn)
        # M4: the memo read like a correction but declared no target — remind the acker
        # to go update the durable stores, which is the step that actually gets missed.
        if not memo_corrections(memo) and correction_language(memo.get("text")):
            print("  ⚠ REMINDER: this memo reads like a correction (matched: %s)."
                  % ", ".join(correction_language(memo.get("text"))))
            print("    An ack is a receipt, not an integration — update the durable store "
                  "it contradicts (agent memory / a task decision) now.")
        return

    if sub == "show":
        mid = getattr(a, "id", None)
        if not (task.get("memos") or []):
            print("(no memos)")
            return
        if mid:
            memo, err = _memo_by_prefix(task, mid)
            if err:
                print(err)
                return
            print(_format_memo_full(task, memo))
        else:
            print(_format_memo_list(task, getattr(a, "session", None)))
        return

    print("memo: use `send`, `ack`, or `show`.")


def cmd_sessions(a):
    """`task-station sessions [--task <ref>] [--json]` — every ACTUALLY-running
    Claude Code session (hub + delegated workers), each with its task, busy/idle
    state, and a one-command resume. Dead/crashed sessions never appear. `--task`
    filters to one task's live sessions; `--json` emits the raw row list."""
    import live_sessions
    rows = live_sessions.running()
    if getattr(a, "task", None):
        t = resolve_ref(a.task) or load_task(a.task)
        rows = [r for r in rows if t and r.get("task_seq") == t.get("seq")]
    if getattr(a, "as_json", False):
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("No live Claude sessions." if not getattr(a, "task", None)
              else "No live Claude sessions for that task.")
        return
    for r in rows:
        print(_format_session_row(r))


def cmd_status(a):
    """Show or set a task's per-task state between the board states (○ new /
    ● active). `status --task <ref>` with no value reports the current state;
    `status --task <ref> new|active` sets it (idempotent — `new` is the input
    alias for the stored `open`). Closing goes through /done, not here — a closed
    task is reported but not settable from here."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    value = getattr(a, "value", None)
    cur = task_status(task)
    if not value:
        glyph = STATUS_GLYPH.get(cur, "")
        print("Task [%s] %s — status: %s %s"
              % (task["id"][:8], task["title"], glyph, status_display(cur)))
        return
    value = normalize_status_input(value)   # `new` → stored `open`
    if value not in STATUS_SETTABLE:
        if value == STATUS_CLOSED:
            print("status: close a task with /done (or `done --task %s`), not `status`."
                  % task.get("seq", task["id"][:8]))
        else:
            print("status: unknown status '%s' — use 'new' or 'active'." % value)
        return
    if is_closed(task):
        print("Task [%s] %s is closed — reopen it via /todo %s first."
              % (task["id"][:8], task["title"], task.get("seq", task["id"][:8])))
        return
    if set_status(task, value, note="status set to %s (manual)" % value,
                  session=getattr(a, "session", None)):
        save_task(task)
        maybe_refresh_board()   # open⇄active flip must show on the board NOW
        print("Task [%s] %s → %s %s"
              % (task["id"][:8], task["title"], STATUS_GLYPH[value], status_display(value)))
    else:
        print("Task [%s] %s already %s %s."
              % (task["id"][:8], task["title"], STATUS_GLYPH[value], status_display(value)))


def cmd_session_title(a):
    """Print the window/title-bar label for an attached session (or nothing).

    The SessionStart hook puts this in hookSpecificOutput.sessionTitle so the
    terminal reads `#<seq>-<ln>: <title>` — the closest we get to auto-labelling
    the hub (the resume-NAME can't be set programmatically on a running session).

    The label carries the session's ROSTER LINE, not just the task number, so two
    sessions on one task read differently (#444-26 vs #444-28) instead of emitting
    byte-identical strings the harness cannot tell apart. When the ln can't be
    resolved the old `#<seq>: <title>` is emitted and a one-line note goes to
    STDERR — stdout is the title the hook captures, so the diagnostic must not
    share it. An unattached or skipped session still prints nothing at all.

    KNOWN LIMIT: this runs from the SessionStart hook, so the title is set ONCE,
    at session start. A session whose ln changes afterwards keeps its original
    title — an ln is assigned at attach and does not normally move, but a reader
    must not assume the title live-updates."""
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task:
        return
    ensure_seqs()
    if ensure_ordinals(task):      # backfill pre-roster hubs, and PERSIST it, so a
        save_task(task)            # title stays the same string on every re-read
    label, ln = session_title_label(task, a.session)
    if ln is None:
        sys.stderr.write(
            "session-title: no roster ln for session %s on task %s — "
            "falling back to the task-only title '%s'\n"
            % ((a.session or "?")[:8], task.get("seq", "?"), label))
    print(label)


def cmd_whoami(a):
    """Map any session id → its task. The backstop that identifies a session
    regardless of whether it was ever named."""
    task_id = get_link(a.session)
    porcelain = getattr(a, "porcelain", False)
    if task_id == SKIP_SENTINEL:
        if not porcelain:
            print("session %s: intentionally untracked (skipped)" % a.session[:8])
        return
    statusline = getattr(a, "statusline", False)
    if statusline:
        # HUD-INDEPENDENT capture: this provider runs whenever the statusline is on.
        # Persist the harness context-window size from the piped payload so the Stop
        # nudge sizes % against the REAL window even when the cost HUD is off. When the
        # HUD is on, its own provider (hud.observe) already captured it — skip here to
        # avoid a redundant read-modify-write race on the shared snapshot.
        try:
            import config as _cfg_cap
            if not _cfg_cap.hud_enabled():
                _cw = _read_statusline_stdin().get("context_window") or {}
                persist_harness_context_window(
                    a.session,
                    _cw.get("context_window_size") if isinstance(_cw, dict) else None)
        except Exception:
            pass
    task = load_task(task_id) if task_id else None
    if not task:
        if not porcelain and not statusline:
            print("session %s: not attached to any task" % a.session[:8])
        return
    ensure_seqs()
    if porcelain:
        # Machine-readable: '<seq>\t<seq>-<n>\t<kind>' (tab-separated). Field 1
        # stays the bare seq so pre-463 `cut -f1`-style consumers keep working;
        # delegate._attached_seq splits on whitespace and takes field 1.
        if ensure_ordinals(task):
            save_task(task)
        m = (task.get("session_meta") or {}).get(a.session) or {}
        kind = m.get("role") or "unknown"
        print("%s\t%s\t%s" % (task.get("seq", ""),
                              ordinal_label(task, a.session) or "", kind))
        return
    if statusline:
        # When the cost HUD is on it owns the whole bar and renders the task inline
        # in its own header (model badge + segment). Emit NOTHING here so the task
        # line never renders twice; the host skips an empty provider. Toggling the
        # HUD off restores this segment on the next render.
        try:
            import config as _cfg
            if _cfg.hud_enabled():
                return
        except Exception:
            pass
        # A ready-to-display, ANSI-colored segment for a status bar —
        # '#<seq>  <dot> [TAG]  <title>'. Self-contained: knows nothing about who
        # renders it. Honors --width (>0) by truncating the title so the whole
        # segment fits that many columns; --width 0 means no limit.
        print(statusline_segment(task, getattr(a, "width", 0),
                                  ordinal=hub_ordinal(task, a.session)))
        return
    print("session %s → task-station %s · %s (%s)"
          % (a.session[:8], task.get("seq", "?"), task["title"],
             status_display(task_status(task))))


def _add_glossary_args(sp):
    """Attach the glossary command's args to a parser/subparser. Shared by main()'s
    `glossary` subcommand AND the `/todo glossary` dispatch, so the two stay identical."""
    sp.add_argument("action", nargs="?", default="list",
                    help="list | add | edit | rm | <task#> (list another task)")
    sp.add_argument("args", nargs="*",
                    help='positional term fields: add "<name>" <layer> <state> "<def>"')
    sp.add_argument("--task", default=None, help="target task (seq/id); else the session's task")
    sp.add_argument("--session", default=None)
    sp.add_argument("--layer", default=None)
    sp.add_argument("--state", default=None)
    sp.add_argument("--def", dest="definition", default=None)
    sp.add_argument("--rename", default=None, help="edit: set a new canonical name")


def _add_brief_args(sp):
    """Attach the brief command's args to a parser/subparser. Shared by main()'s
    `brief` subcommand AND the `/todo brief` dispatch, so the two stay identical."""
    sp.add_argument("action", nargs="?", default="render",
                    help="render | path — render (default) templates a brief-spec JSON; "
                         "path creates + records the output path for a model-authored brief")
    sp.add_argument("--task", default=None, help="target task (seq/id); else the session's task")
    sp.add_argument("--session", default=None)
    sp.add_argument("--spec", default=None, help="brief-spec JSON file (default: read stdin)")


def _add_config_args(sp):
    """Attach the config command's flags to a parser/subparser. Shared by main()'s
    `config` subcommand AND the `/todo config` dispatch (which parses the tokens
    after the keyword with the SAME spec), so the two stay identical."""
    # STATION IDENTITY — who this machine belongs to and which of that owner's
    # machines it is. It arrives from runtime config, NEVER from code, so it can never
    # be baked into the public tree; these flags are the supported way to set it.
    sp.add_argument("--self-alias", dest="self_alias", nargs="?", const="", default=None,
                    help="this OWNER's alias — the prefix of every handle minted here "
                         "and the name of this owner's directory in the sync exchange "
                         "(default: the OS username; no value clears the override)")
    sp.add_argument("--self-alias-get", dest="self_alias_get", action="store_true")
    sp.add_argument("--station-number", dest="station_number", nargs="?", const="",
                    default=None, metavar="N",
                    help="which of this owner's MACHINES this is, numbered FROM 0. Two "
                         "machines of one owner MUST differ — they are separate write "
                         "partitions, and that is what makes a sync conflict impossible "
                         "(no value clears the override, back to 0)")
    sp.add_argument("--station-number-get", dest="station_number_get", action="store_true")
    sp.add_argument("--station-label", dest="station_label", nargs="?", const="",
                    default=None, metavar="NAME",
                    help="friendly name for this machine — DISPLAY ONLY; nothing ever "
                         "computes on it, which is what keeps renaming free (default: "
                         "the device's LocalHostName)")
    sp.add_argument("--station-label-get", dest="station_label_get", action="store_true")
    sp.add_argument("--sync-dir", dest="sync_dir", nargs="?", const="", default=None,
                    metavar="DIR",
                    help="the sync exchange directory. UNSET BY DEFAULT — sync is off "
                         "until you point it somewhere (no value turns it off again)")
    sp.add_argument("--sync-dir-get", dest="sync_dir_get", action="store_true")
    sp.add_argument("--workspace-dirs", dest="workspace_dirs", default=None)
    sp.add_argument("--workspace-dirs-get", dest="workspace_dirs_get", action="store_true")
    sp.add_argument("--artifacts-root", dest="artifacts_root", nargs="?", const="", default=None,
                    help="root dir for rendered /brief artifacts (default: <data_dir>/artifacts; "
                         "TASK_STATION_ARTIFACTS_ROOT env wins; no value clears the override)")
    sp.add_argument("--artifacts-root-get", dest="artifacts_root_get", action="store_true")
    sp.add_argument("--category-pack", dest="category_pack", nargs="*", default=None,
                    help="(no arg / 'list') list packs + active · <name> select the active pack "
                         "(dev · finance · hr · exec · general + org packs; per-slot overrides still win)")
    sp.add_argument("--category-pack-get", dest="category_pack_get", action="store_true")
    sp.add_argument("--categories", dest="categories", nargs="*", default=None,
                    help="(no arg) show enabled set + toggles · 'edit' print config path")
    sp.add_argument("--enable", dest="enable", default=None,
                    help="enable a category slot (key, emoji, or [TAG])")
    sp.add_argument("--disable", dest="disable", default=None,
                    help="disable a category slot (refuses ⚫ GENERAL — permanent)")
    sp.add_argument("--auto-categories", dest="auto_categories", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="auto-enable a category slot the first time a task is assigned to it (default on)")
    sp.add_argument("--auto-categories-get", dest="auto_categories_get", action="store_true")
    sp.add_argument("--bare-cmds", dest="bare_cmds", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--bare-cmds-get", dest="bare_cmds_get", action="store_true")
    sp.add_argument("--update-check", dest="update_check", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--update-check-get", dest="update_check_get", action="store_true")
    sp.add_argument("--stream", dest="stream", nargs="?", choices=["on","off"], const="on", default=None,
                    help="the durable JSONL event ledger (internal, local-only; default on)")
    sp.add_argument("--stream-get", dest="stream_get", action="store_true")
    sp.add_argument("--stream-dir", dest="stream_dir", default=None,
                    help="external tee directory for the stream ('' clears it; default off)")
    sp.add_argument("--stream-dir-get", dest="stream_dir_get", action="store_true")
    sp.add_argument("--board-autorefresh", dest="board_autorefresh", nargs="?", choices=["on","off"], const="on", default=None,
                    help="open /todo board tab stays live via meta-refresh + Stop-hook regen (no network); default off")
    sp.add_argument("--board-autorefresh-get", dest="board_autorefresh_get", action="store_true")
    sp.add_argument("--done-closes-window", dest="done_closes_window", nargs="?", choices=["on","off"], const="on", default=None,
                    help="auto-close the terminal window ~1s after a no-arg /done closes this session's task; opt-in, default off (window stays open)")
    sp.add_argument("--done-closes-window-get", dest="done_closes_window_get", action="store_true")
    sp.add_argument("--board-browser", dest="board_browser", nargs="?", const="", default=None,
                    help='browser app the board opens in (macOS: open -a "<App>", e.g. "Google '
                         'Chrome"); no value clears it (back to the system default browser)')
    sp.add_argument("--board-browser-get", dest="board_browser_get", action="store_true")
    sp.add_argument("--interbrain", dest="interbrain", nargs="?", choices=["on", "off", "auto"],
                    const="on", default=None,
                    help="board Interbrain federation: on · off · auto (default auto → on when >1 brain/peers)")
    sp.add_argument("--interbrain-get", dest="interbrain_get", action="store_true")
    sp.add_argument("--knowledge-plane", dest="knowledge_plane", nargs="?",
                    choices=["on", "off", "auto"], const="on", default=None,
                    help="board knowledge plane: on · off · auto — the vault's notes as a "
                         "second plane above the task plane, read-only (default auto → on "
                         "when a configured vault holds at least one note)")
    sp.add_argument("--knowledge-plane-get", dest="knowledge_plane_get", action="store_true")
    sp.add_argument("--org-label", dest="org_label", nargs="?", const="", default=None,
                    help='display label for the org brain (default "Org brain"; e.g. "Company Brain"); no value clears it')
    sp.add_argument("--org-label-get", dest="org_label_get", action="store_true")
    # RETIRED (#444): there is one board now, so there is nothing to select. Still PARSED —
    # and answered with a one-line notice — so muscle memory and old scripts get an
    # explanation instead of an argparse error. Hidden from --help.
    sp.add_argument("--board-engine", dest="board_engine", nargs="?", const="",
                    default=None, help=argparse.SUPPRESS)
    sp.add_argument("--board-engine-get", dest="board_engine_get", action="store_true",
                    help=argparse.SUPPRESS)
    sp.add_argument("--theme", dest="theme", nargs="*", default=None,
                    help="(no arg) list themes + active · <name> select · save <name> · edit · preview")
    sp.add_argument("--tint-theme", dest="tint_theme", nargs="?", choices=["auto","dark","light"], const="auto", default=None,
                    help="appearance variant: auto follows the OS (dark=Dark Sands, light=Light Sands), or force dark/light")
    sp.add_argument("--tint-theme-get", dest="tint_theme_get", action="store_true")
    sp.add_argument("--tint", dest="tint", nargs="?", choices=["on","off"], const="on", default=None,
                    help="full-palette terminal tint via escape codes (default on; TASK_STATION_TINT env overrides)")
    sp.add_argument("--tint-get", dest="tint_get", action="store_true")
    sp.add_argument("--reset", dest="reset", nargs="?", const="ask", default=None,
                    help="reset ALL config settings to factory defaults — asks to confirm (tasks unaffected)")
    sp.add_argument("--title", dest="title", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--title-get", dest="title_get", action="store_true")
    sp.add_argument("--strict-delegation", dest="strict_delegation", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="install (on) / remove (off) a managed delegation-rules block in CLAUDE.md")
    # Hidden back-compat alias for the former flag name; same dest.
    sp.add_argument("--policy", dest="strict_delegation", nargs="?",
                    choices=["on", "off"], const="on", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--guaranteed-tracking", dest="guaranteed_tracking", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="hook-side deterministic create+attach of a provisional task on a fresh session (default off)")
    sp.add_argument("--guaranteed-tracking-get", dest="guaranteed_tracking_get", action="store_true")
    sp.add_argument("--auto-checkpoint", dest="auto_checkpoint", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="opt-in automatic checkpointing: a PostCompact hook stashes the "
                         "compaction summary into the task history (free), plus a "
                         "post-compaction + a staleness nudge keep the digest fresh (default off)")
    sp.add_argument("--auto-checkpoint-get", dest="auto_checkpoint_get", action="store_true")
    sp.add_argument("--checkpoint-at", dest="checkpoint_at", nargs="?", const="off", default=None,
                    metavar="TOKENS",
                    help="LEGACY/fallback proactive threshold (estimated tokens): with "
                         "--auto-checkpoint on, prompt a full /todo save before auto-compaction "
                         "once the transcript-size ESTIMATE grows past this (default off; prefer "
                         "--checkpoint-pct; 0/off disables it, PostCompact stash still runs)")
    sp.add_argument("--checkpoint-at-get", dest="checkpoint_at_get", action="store_true")
    sp.add_argument("--checkpoint-pct", dest="checkpoint_pct", nargs="?", const="off", default=None,
                    metavar="PCT",
                    help="proactive context-pressure threshold as a %% of --context-window, "
                         "MEASURED from the transcript's real usage block: with --auto-checkpoint "
                         "on, prompt a full /todo save before auto-compaction once measured context "
                         "reaches this %% (default 65; 1-95; 0/off disables)")
    sp.add_argument("--checkpoint-pct-get", dest="checkpoint_pct_get", action="store_true")
    sp.add_argument("--context-window", dest="context_window", nargs="?", default=None,
                    metavar="TOKENS",
                    help="the model's context-window size, the denominator --checkpoint-pct "
                         "measures against (default 200000; raise for a larger window)")
    sp.add_argument("--context-window-get", dest="context_window_get", action="store_true")
    sp.add_argument("--checkpoint-milestone-edits", dest="checkpoint_milestone_edits",
                    nargs="?", const="off", default=None, metavar="COUNT",
                    help="with --auto-checkpoint on, fire the light staleness nudge only after "
                         "this many meaningful events (edits / promotions) since the last digest "
                         "refresh (default 5; 0/off = nudge on any staleness)")
    sp.add_argument("--checkpoint-milestone-edits-get", dest="checkpoint_milestone_edits_get",
                    action="store_true")
    sp.add_argument("--heal-prompt-nag", dest="heal_prompt_nag", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="name a heal that comes DUE mid-session on the prompt rail, once "
                         "per task per session, instead of waiting for the next session "
                         "start (default on)")
    sp.add_argument("--heal-prompt-nag-get", dest="heal_prompt_nag_get", action="store_true")
    sp.add_argument("--save-nudge", dest="save_nudge", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="say when the attached task's checkpoint is going stale, on the "
                         "prompt rail, once per task per session; a never-checkpointed "
                         "task is never nudged (default on)")
    sp.add_argument("--save-nudge-get", dest="save_nudge_get", action="store_true")
    sp.add_argument("--save-nudge-decisions", dest="save_nudge_decisions", nargs="?",
                    default=None, metavar="COUNT",
                    help="with --save-nudge on: decisions + log entries that may land "
                         "after a full checkpoint before the digest reads stale (default 6)")
    sp.add_argument("--save-nudge-decisions-get", dest="save_nudge_decisions_get",
                    action="store_true")
    sp.add_argument("--save-nudge-hours", dest="save_nudge_hours", nargs="?",
                    default=None, metavar="HOURS",
                    help="with --save-nudge on: hours that may pass after a full checkpoint "
                         "before the digest reads stale, and only ever with >= 1 decision "
                         "since (default 12)")
    sp.add_argument("--save-nudge-hours-get", dest="save_nudge_hours_get",
                    action="store_true")
    sp.add_argument("--memo-quiet", dest="memo_quiet", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="keep memos the room has already SETTLED (a decision/memory ack "
                         "from anyone, or --memo-quiet-after distinct dispositions) out of "
                         "the per-prompt awaiting-your-ack nag; `memo show` still lists "
                         "every memo (default on)")
    sp.add_argument("--memo-quiet-get", dest="memo_quiet_get", action="store_true")
    sp.add_argument("--memo-quiet-after", dest="memo_quiet_after", nargs="?",
                    default=None, metavar="COUNT",
                    help="with --memo-quiet on: distinct sessions whose dispositions (any "
                         "kind, noop included) settle a memo for the nag (default 3)")
    sp.add_argument("--memo-quiet-after-get", dest="memo_quiet_after_get",
                    action="store_true")
    sp.add_argument("--desktop-bridge", dest="desktop_bridge", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="wire the dependency-free MCP server into Claude Desktop (on) / remove it (off)")
    sp.add_argument("--statusline", dest="statusline", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="install (on) / remove (off) the opt-in self-sufficient status bar; "
                         "non-destructive, never clobbers an existing statusLine (default off)")
    sp.add_argument("--statusline-get", dest="statusline_get", action="store_true")
    sp.add_argument("--hud", dest="hud", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="install (on) / remove (off) the opt-in cost HUD (turn/session/"
                         "limit/week/total/task $ rows) on the status-bar host; "
                         "non-destructive, priced by the shared usage ledger (default off)")
    sp.add_argument("--hud-get", dest="hud_get", action="store_true")
    sp.add_argument("--hud-rows", dest="hud_rows", nargs="?", const="", default=None,
                    metavar="ROWS",
                    help="comma-separated cost-HUD rows to show, in order (subset of "
                         "turn,session,limits,week,total,task; default all)")
    sp.add_argument("--hud-rows-get", dest="hud_rows_get", action="store_true")
    sp.add_argument("--hud-eco", dest="hud_eco", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="append the eco-footprint column to the cost HUD (default off)")
    sp.add_argument("--hud-eco-get", dest="hud_eco_get", action="store_true")
    sp.add_argument("--worktree-hook", dest="worktree_hook", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="install (on) / remove (off) the opt-in WorktreeCreate "
                         "provisioner in your settings.json: new worktrees get the main "
                         "checkout's .claude/settings.local.json + a trust entry. The hook "
                         "REPLACES worktree creation while installed (default off)")
    sp.add_argument("--worktree-hook-get", dest="worktree_hook_get", action="store_true")
    sp.add_argument("--config-change-enforce", dest="config_change_enforce", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="BLOCK a settings save declaring a path that no longer exists "
                         "(default off — warn via hook-health only; a block is "
                         "transcript-silent). TASK_STATION_CONFIG_ENFORCE overrides")
    sp.add_argument("--config-change-enforce-get", dest="config_change_enforce_get",
                    action="store_true")
    sp.add_argument("--ultracode-hints", dest="ultracode_hints", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="suggest ultracode multi-agent breadth on fan-out-worthy tasks "
                         "(L/XL, or RESEARCH/REVIEW/DATA at M+) for read/think phases "
                         "only — never repo writes (default on)")
    sp.add_argument("--ultracode-hints-get", dest="ultracode_hints_get", action="store_true")
    sp.add_argument("--notify", dest="notify", nargs="?", choices=["on", "off"], const="on", default=None,
                    help="macOS banner when a delegated worker run finishes/fails (default off; "
                         "TASK_STATION_NOTIFY env overrides)")
    sp.add_argument("--notify-get", dest="notify_get", action="store_true")
    sp.add_argument("--delegate-bypass-permissions", dest="delegate_bypass_permissions",
                    nargs="?", choices=["on", "off"], const="on", default=None,
                    help="spawn --bg workers in a worktree with bypassPermissions so they "
                         "never block (default on, enforced worktree-only; "
                         "TASK_STATION_DELEGATE_BYPASS env overrides)")
    sp.add_argument("--delegate-bypass-permissions-get",
                    dest="delegate_bypass_permissions_get", action="store_true")
    sp.add_argument("--reap-workers-on-done", dest="reap_workers_on_done",
                    nargs="?", choices=["on", "off"], const="on", default=None,
                    help="stop this task's live --bg workers when it closes so they don't "
                         "linger/respawn in Agent View (default on; airtight — only a "
                         "registered, role==worker, task-station-named, idle worker is "
                         "reaped; TASK_STATION_REAP_WORKERS_ON_DONE env overrides)")
    sp.add_argument("--reap-workers-on-done-get",
                    dest="reap_workers_on_done_get", action="store_true")
    sp.add_argument("--notify-webhook", dest="notify_webhook", nargs="?", const="", default=None,
                    metavar="URL",
                    help="POST worker finished/failed events to this URL (Slack/Teams/ntfy-style "
                         "JSON receiver); no value clears it (TASK_STATION_NOTIFY_WEBHOOK overrides)")
    sp.add_argument("--notify-webhook-get", dest="notify_webhook_get", action="store_true")
    sp.add_argument("--obsidian-vault", dest="obsidian_vault", nargs="?", const="", default=None,
                    metavar="PATH",
                    help="export tasks (one-way) into this Obsidian vault; files land under "
                         "<vault>/task-station/. No value turns export OFF "
                         '(e.g. --obsidian-vault "~/Documents/Obsidian Vault")')
    sp.add_argument("--obsidian-vault-get", dest="obsidian_vault_get", action="store_true")
    sp.add_argument("--obsidian-sandbox", dest="obsidian_sandbox", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="add (on) / remove (off) the configured vault in the Claude Code "
                         "sandbox write-allowlist (sandbox.filesystem.allowWrite in your "
                         "settings.json) so in-session exports into a protected folder "
                         "(~/Documents, iCloud) write instantly; does NOT force sandbox on")
    sp.add_argument("--obsidian-sandbox-get", dest="obsidian_sandbox_get", action="store_true")
    sp.add_argument("--obsidian-daily-note", dest="obsidian_daily_note", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="append a line to the vault daily note on task close + /todo save (default off)")
    sp.add_argument("--obsidian-daily-note-get", dest="obsidian_daily_note_get", action="store_true")
    sp.add_argument("--obsidian-daily-heading", dest="obsidian_daily_heading", nargs="?",
                    const="", default=None, metavar="HEADING",
                    help='daily-note heading the entries go under (default "## Claude sessions"); '
                         "no value restores the default")
    sp.add_argument("--obsidian-daily-heading-get", dest="obsidian_daily_heading_get", action="store_true")
    sp.add_argument("--obsidian-prompts", dest="obsidian_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="write the full prompt trail (## Prompts) into exported vault notes "
                         "(default off — prompt export is opt-in; TASK_STATION_OBSIDIAN_PROMPTS overrides)")
    sp.add_argument("--obsidian-prompts-get", dest="obsidian_prompts_get", action="store_true")
    sp.add_argument("--obsidian-category-hubs", dest="obsidian_category_hubs", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="cluster the export/vault graph by category (default ON): a "
                         "[[categories/<slug>]] link in each note + a hub page per category "
                         "under <target>/categories/. Off drops the link and prunes the hub "
                         "pages on the next sync. TASK_STATION_OBSIDIAN_CATEGORY_HUBS overrides")
    sp.add_argument("--obsidian-category-hubs-get", dest="obsidian_category_hubs_get", action="store_true")
    sp.add_argument("--obsidian-subgroups", dest="obsidian_subgroups", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="emergent sub-groups within a category (default ON, nested inside "
                         "--obsidian-category-hubs): distinctive recurring title tokens auto-cluster "
                         "into nested categories/<cat-slug>/<token>.md sub-hub pages, and member notes "
                         "link the sub-hub instead of the bare category. Off prunes the sub-hubs and "
                         "reverts members on the next sync. TASK_STATION_OBSIDIAN_SUBGROUPS overrides")
    sp.add_argument("--obsidian-subgroups-get", dest="obsidian_subgroups_get", action="store_true")
    sp.add_argument("--obsidian-story-groups", dest="obsidian_story_groups", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="story hubs (default ON, nested inside --obsidian-category-hubs): tasks that "
                         "share a story id (from the structured `stories` field, referenced by >= 1 "
                         "tasks) get a cross-category stories/<id>.md hub + a [[stories/<id>]] link in "
                         "each member note, IN ADDITION to the category link. Off prunes the hubs and "
                         "drops the link. TASK_STATION_OBSIDIAN_STORY_GROUPS overrides")
    sp.add_argument("--obsidian-story-groups-get", dest="obsidian_story_groups_get", action="store_true")
    sp.add_argument("--knowledge-graph", dest="knowledge_graph", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="second-brain tier (default off): task<->note co-citation edges in the "
                         "board mini-graph + 'Related knowledge' panel, and ## Related wikilink "
                         "emission into the Obsidian mirror. Inert without an --obsidian-vault; "
                         "TASK_STATION_KNOWLEDGE_GRAPH overrides")
    sp.add_argument("--knowledge-graph-get", dest="knowledge_graph_get", action="store_true")
    sp.add_argument("--owner", dest="owner", nargs="?", const="", default=None,
                    metavar="HANDLE",
                    help="owner handle for a SHARED vault: notes nest under <target>/<owner>/ "
                         "and carry the handle (frontmatter/manifest/daily lines); no value "
                         "clears it (single-owner). Run `obsidian --sync-all` after to "
                         "relocate existing notes. TASK_STATION_OWNER overrides")
    sp.add_argument("--owner-get", dest="owner_get", action="store_true")
    sp.add_argument("--usage-tracking", dest="usage_tracking", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="track per-task model usage + derived $ from your local transcripts "
                         "(default on; reads only local files; TASK_STATION_USAGE_TRACKING overrides)")
    sp.add_argument("--usage-tracking-get", dest="usage_tracking_get", action="store_true")
    sp.add_argument("--usage-prompts", dest="usage_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="capture prompt text into the usage ledger (same-machine; default on)")
    sp.add_argument("--usage-prompts-get", dest="usage_prompts_get", action="store_true")
    sp.add_argument("--board-prompts", dest="board_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="show the captured prompt trail on the visual board "
                         "(local-only; default on)")
    sp.add_argument("--board-prompts-get", dest="board_prompts_get", action="store_true")
    sp.add_argument("--usage-billing-mode", dest="usage_billing_mode", nargs="?",
                    choices=["api", "subscription"], const="api", default=None,
                    help="frame the derived $ as metered (api) or flat-rate API-equivalent value "
                         "(subscription); default api")
    sp.add_argument("--usage-billing-mode-get", dest="usage_billing_mode_get", action="store_true")
    sp.add_argument("--recap", dest="recap", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="auto-generate the private weekly usage recap under <data_dir>/recaps/ "
                         "(local-only, never synced; default off; TASK_STATION_RECAP overrides)")
    sp.add_argument("--recap-get", dest="recap_get", action="store_true")
    sp.add_argument("--recap-curator-cmd", dest="recap_curator_cmd", nargs="?", const="", default=None,
                    help="command that turns recap AGGREGATE stats (JSON on stdin; never prompt "
                         "text) into 3 tailored tips; no value clears it (default off)")
    sp.add_argument("--recap-curator-cmd-get", dest="recap_curator_cmd_get", action="store_true")
    sp.add_argument("--editor-scheme", dest="editor_scheme", nargs="?", const="", default=None,
                    help="URI scheme the board uses to open file paths, e.g. cursor/vscode/zed "
                         "→ <scheme>://file/<abs>, or `file` → file://<abs>; no value AUTO-DETECTS "
                         "from your editor ($VISUAL/$EDITOR, then installed editor apps, else file)")
    sp.add_argument("--editor-scheme-get", dest="editor_scheme_get", action="store_true")
