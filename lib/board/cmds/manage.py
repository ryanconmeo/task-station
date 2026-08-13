"""Management command seam: update/pin/unpin, the add-* recorders, obsidian/export/usage, repos, and the F6 command surface."""
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
import json
import os
import sys

import decisions as _dec
import heal as _heal
import hook_health
import paths
import save as _save
import steps as _steps

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_split_refs",
    "_add_project_one", "cmd_add_project", "cmd_add_cost", "cmd_add_event",
    "cmd_add_ledger", "cmd_register_worker",
    "_obsidian_vault_or_msg", "_flush_obsidian_dirty", "cmd_obsidian",
    "_live_task_ids", "_export_dirs", "_record_export_dir", "cmd_export",
    "_render_usage", "cmd_usage", "cmd_prompts",
    "_update_one", "cmd_update",
    "_pin_one", "cmd_pin", "_unpin_one", "cmd_unpin",
    "_emit_tint_to_origin", "_emit_title_to_origin",
    "_repos_load", "_repos_render_manifest", "_repos_set_flag",
    "_repos_manifest_action", "cmd_repos",
    "_open_argv", "_open_path",
    "cmd_capture_artifacts", "cmd_board", "cmd_brains", "cmd_hook_health",
]


def _split_refs(ref):
    """Split a `--task` value into individual refs: comma-separated, each
    whitespace-trimmed, empties dropped. A single ref is just a list of one.

    Shared by every batchable mutating subcommand (done / update / pin / unpin /
    add-project) so they all honor the same contract: one result line per ref, a
    bad ref reported but never aborting the rest."""
    return [r.strip() for r in (ref or "").split(",") if r.strip()]


def _add_project_one(ref, project):
    """Record `project` on the task named by `ref` (idempotent). Returns an error
    line for a bad ref, or None on success (success stays silent — this is
    machine-called by delegate)."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "add-project: no task matching %r" % ref
    projs = task.setdefault("projects", [])
    if project not in projs:
        projs.append(project)
        task["updated_ts"] = _now()
        save_task(task)
    return None


def cmd_add_project(a):
    """Record that a task has delegated work into a repo (project). Idempotent.

    Called by delegate.py when a worker is spawned with --seq, so /todo can
    list the task's in-project workers in its detail view. No session attach, no
    activity-log entry — keeps the link bookkeeping quiet. `--task` accepts a
    comma-separated list (record the project on several tasks at once); a bad ref
    is reported on stderr without aborting the rest."""
    refs = _split_refs(a.task)
    if not refs:
        sys.stderr.write("add-project: no task given\n")
        return
    for r in refs:
        err = _add_project_one(r, a.project)
        if err:
            sys.stderr.write(err + "\n")


def cmd_add_cost(a):
    """Accumulate a delegate run's worker cost (USD) onto a task, AND — when the
    optional per-run detail flags are present (--model / --session / --usage-json) —
    append a per-run record to task['runs']. Called by delegate.py after it parses the
    worker's JSON, so per-run cost lands on the linked /todo task's `cost` field (total
    + run count) plus a per-invocation breakdown — not just workers.json. No attach, no
    activity-log entry (quiet bookkeeping, like add-project); a bad ref / non-positive
    amount is a silent no-op on stderr."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("add-cost: no task matching %r\n" % a.task)
        return
    model = getattr(a, "model", None)
    session = getattr(a, "session", None)
    seq_label = getattr(a, "seq_label", None)
    category = getattr(a, "category", None) or "real"   # real | wasted (RESOLVED #4)
    usage_json = getattr(a, "usage_json", None)
    usage = None
    if usage_json:
        try:
            usage = json.loads(usage_json)
        except (TypeError, ValueError):
            usage = None                          # malformed usage → record the run without token detail
    has_detail = bool(model or session or usage or seq_label)
    try:
        amount = float(a.usd)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0 and not has_detail:
        return                                    # nothing to record (unchanged no-op)

    def _apply(t):                                # concurrent-safe: parallel workers'
        add_cost(t, a.usd, category=category)     # costs + run records both accumulate
        if has_detail:
            record_run(t, seq_label=seq_label, session_id=session, model=model,
                       cost_usd=a.usd, usage=usage, category=category)
        t["updated_ts"] = _now()
    g("mutate")(task["id"], _apply)


def cmd_add_event(a):
    """Append one entry to a task's bounded event feed — quiet bookkeeping (loads,
    add_event, save; no touch, no attach, no activity-log entry, like add-cost). Used
    by the delegate to post worker/child milestones onto the linked /todo task so a
    resumed session's delta-brief can surface them. A bad ref is a silent no-op on
    stderr; exits 0 either way so a best-effort caller never fails on it."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("add-event: no task matching %r\n" % a.task)
        return
    kind, text, session = a.kind, getattr(a, "text", "") or "", getattr(a, "session", None)

    def _apply(t):                                # concurrent-safe: two workers'
        add_event(t, kind, text, session)         # events both survive a race
        t["updated_ts"] = _now()
    g("mutate")(task["id"], _apply)
    _stream_emit("task.event", task,
                 {"kind": kind, "text": (text or "")[:EVENT_TEXT_MAX]}, session)


def cmd_add_ledger(a):
    """Quiet bookkeeping (same contract as add-event: no touch, no attach, exit 0
    on a bad ref) — delegate.py posts spawn/resume/stop/adopt/finish/crash here."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("add-ledger: no task matching %r\n" % a.task)
        return
    def _apply(t):
        add_ledger(t, a.action, worker_sid=getattr(a, "worker", None),
                   actor_sid=getattr(a, "session", None),
                   detail=getattr(a, "detail", None))
        t["updated_ts"] = _now()
    g("mutate")(task["id"], _apply)


def cmd_register_worker(a):
    """Quiet bookkeeping (same contract as add-ledger: no touch, no attach, exit 0
    on a bad ref) — delegate.py rosters a worker session on the task record
    (name/model/harness/status) on spawn + each terminal transition (#463)."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("register-worker: no task matching %r\n" % a.task)
        return
    def _apply(t):
        register_worker_session(t, a.session, name=getattr(a, "name", None),
                                model=getattr(a, "model", None),
                                harness=getattr(a, "harness", None) or "claude",
                                status=getattr(a, "status", None) or "running")
        t["updated_ts"] = _now()
    g("mutate")(task["id"], _apply)


def _obsidian_vault_or_msg():
    """The configured, existing vault path — or None after printing WHY (off /
    missing). Shared by --sync-all and --flush so both fail the same way."""
    vault = _obsidian_vault()
    if not vault:
        print("Obsidian export is off. Enable it first:\n"
              '  /task-station:config --obsidian-vault "~/Documents/Obsidian Vault"')
        return None
    if not os.path.isdir(vault):
        print("Obsidian vault path does not exist: %s" % vault)
        return None
    return vault


def _flush_obsidian_dirty(vault, dirty, quiet=False):
    """Re-export each pending-resync task into `vault`. On success clear BOTH the
    dirty and persistent-failure flags; on failure keep dirty and SET
    obsidian_flush_failed — the persistent signal a later mutation reads to decide
    whether to surface the loud hint (this is what makes the mid-turn failure stay
    silent until a hook flush ALSO fails). Returns (flushed, failed). Best-effort per
    task — one task's failure never aborts the rest, nor breaks the calling hook."""
    import obsidian_sync
    flushed = failed = 0
    _begin_subgroups()   # one full-board detection for the whole flush batch
    try:
        for t in dirty:
            try:
                usage, prompts = _obsidian_note_data(t)
                related = _obsidian_related_links(t)
                if obsidian_sync.export_task(t, vault, usage=usage, prompts=prompts,
                                             related=related, owner=_owner()):
                    changed = clear_obsidian_dirty(t)
                    if t.pop("obsidian_flush_failed", None) is not None:
                        changed = True
                    if changed:
                        save_task(t)
                    flushed += 1
                else:
                    if not t.get("obsidian_flush_failed"):
                        t["obsidian_flush_failed"] = True
                        save_task(t)
                    failed += 1
            except Exception as e:
                if not t.get("obsidian_flush_failed"):
                    t["obsidian_flush_failed"] = True
                    save_task(t)
                failed += 1
                if not quiet:
                    sys.stderr.write("task-station: flush of task %s failed: %s\n"
                                     % (t.get("seq", t.get("id", "?")), e))
    finally:
        _end_subgroups()
    return flushed, failed


def cmd_obsidian(a):
    """`task-station obsidian` — manage the opt-in Obsidian export.

      --status    (default) report whether export is on, the vault + folder, note
                  count, the daily-note setting, and how many tasks are pending
                  resync (failed in-session exports awaiting a --flush).
      --sync-all  full resync: (re)write a note for EVERY task into the vault, and
                  clear the pending-resync flag on each that exports cleanly. Use
                  after enabling export, or to repair the vault after edits.
      --flush     re-export ONLY the pending-resync (obsidian_dirty) tasks and clear
                  their flags — the cheap way to drain the sandbox backlog. Run from
                  an unsandboxed shell; ALSO invoked automatically (with --quiet) by
                  the Stop / SessionStart hooks, which run unsandboxed and so heal a
                  protected-folder vault the sandboxed hot path couldn't write.
      --quiet     (with --flush) suppress all happy-path output — for the hooks, which
                  must stay silent; a persistent failure still arms the next mutation's
                  hint. A cheap no-op when export is off or nothing is pending.
    """
    if getattr(a, "flush", False):
        quiet = getattr(a, "quiet", False)
        vault = _obsidian_vault()
        # Cheap pre-checks first so the hooks spawn NO work on the happy path.
        if not vault:
            if not quiet:
                print("Obsidian export is off. Enable it first:\n"
                      '  /task-station:config --obsidian-vault "~/Documents/Obsidian Vault"')
            return
        # Cheap pre-check BEFORE any work: on the happy path (the common hook case)
        # there's nothing dirty, so we return without ensure_seqs / re-scan.
        if not any(obsidian_dirty(t) for t in all_tasks()):
            if not quiet:
                print("Nothing to flush — no tasks pending resync.")
            return
        ensure_seqs()   # every note filename needs a stable seq; build the list AFTER
        dirty = [t for t in all_tasks() if obsidian_dirty(t)]
        import obsidian_sync
        if not os.path.isdir(vault):
            # Vault configured but gone (unmounted / typo) — a genuine PERSISTENT
            # failure: arm the hint on the dirty tasks (the next mutation surfaces it)
            # and stay silent for hook callers; explain for a manual invocation.
            for t in dirty:
                if not t.get("obsidian_flush_failed"):
                    t["obsidian_flush_failed"] = True
                    save_task(t)
            if not quiet:
                print("Obsidian vault path does not exist: %s" % vault)
                sys.stdout.write(_obsidian_persistent_help(vault))
            return
        flushed, failed = _flush_obsidian_dirty(vault, dirty, quiet)
        if flushed:
            _clear_obsidian_perm_marker()   # backlog drained — re-arm the one-shot hint
            _sync_obsidian_category_hubs(vault, _owner())   # refresh hubs for the drained tasks
            _sync_obsidian_story_hubs(vault, _owner())
        if not quiet:
            print("Flushed %d task%s → %s%s"
                  % (flushed, "" if flushed == 1 else "s", obsidian_sync.plugin_dir(vault),
                     "" if not failed else "; %d still failing" % failed))
            if failed:
                sys.stdout.write(_obsidian_persistent_help(vault))
        return
    if getattr(a, "sync_all", False):
        vault = _obsidian_vault_or_msg()
        if not vault:
            return
        import obsidian_sync
        ensure_seqs()                       # every note filename needs a stable seq
        owner = _owner()
        # When an owner is (newly) set, relocate any existing FLAT notes into the owner
        # subtree first, so the resync updates them in place and the old location is
        # left with no managed orphans.
        migrated = obsidian_sync.migrate_to_owner(vault, owner) if owner else 0
        n = 0
        _begin_subgroups()   # one full-board detection for the whole resync loop
        try:
            for t in all_tasks():
                try:
                    usage, prompts = _obsidian_note_data(t)
                    related = _obsidian_related_links(t)
                    if obsidian_sync.export_task(t, vault, usage=usage, prompts=prompts,
                                                 related=related, owner=owner):
                        changed = clear_obsidian_dirty(t)
                        if t.pop("obsidian_flush_failed", None) is not None:
                            changed = True    # a clean resync fully heals any pending flags
                        if changed:
                            save_task(t)
                        n += 1
                except Exception as e:
                    sys.stderr.write("task-station: export of task %s failed: %s\n"
                                     % (t.get("seq", t.get("id", "?")), e))
        finally:
            _end_subgroups()
        if n:
            _clear_obsidian_perm_marker()
        # Regenerate the category-hub pages once, from the now-current sidecar (toggle
        # off prunes them) — a full resync repairs the hubs like it repairs index.md.
        _sync_obsidian_category_hubs(vault, owner)
        _sync_obsidian_story_hubs(vault, owner)
        dest = obsidian_sync.owner_dir(obsidian_sync.plugin_dir(vault), owner)
        print("Synced %d task%s → %s%s"
              % (n, "" if n == 1 else "s", dest,
                 "" if not migrated else " (relocated %d existing note%s into %s/)"
                 % (migrated, "" if migrated == 1 else "s", owner)))
        return
    # default / --status
    import obsidian_sync
    print(obsidian_sync.status_report())
    # Pending-resync count — only meaningful while export is on; a stale flag from a
    # since-disabled vault shouldn't nag with a --flush that can't run.
    if _obsidian_vault():
        pending = sum(1 for t in all_tasks() if obsidian_dirty(t))
        if pending:
            print("  Pending resync: %d task%s — run `obsidian --flush` from an "
                  "unsandboxed shell." % (pending, "" if pending == 1 else "s"))


def _live_task_ids():
    """The set of task ids whose exported notes should SURVIVE a prune/delete
    reconcile: every task that still exists AND has not been redacted (a redacted
    task's content is being forgotten, so its notes go too)."""
    return {t.get("id") for t in all_tasks() if t.get("id") and not t.get("redacted")}


def _export_dirs():
    """The recorded generic-export destinations (config `export_dirs`) — the sidecar
    indexes `delete` can locate to purge a hard-deleted task's notes. Expanded,
    de-duplicated, existing dirs only; a bad/missing entry is skipped."""
    try:
        import config as _cfg
        raw = _cfg.get("export_dirs") or []
    except Exception:
        raw = []
    out, seen = [], set()
    for p in raw:
        d = os.path.expanduser(p or "")
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _record_export_dir(out_dir):
    """Remember `out_dir` as a generic-export destination so a later `delete`/`--prune`
    can find its sidecar index. Stored un-expanded (survives a home move), deduped,
    capped. Best-effort — a config hiccup never breaks the export."""
    try:
        import config as _cfg
        cur = _cfg.get("export_dirs") or []
        if not isinstance(cur, list):
            cur = []
        if out_dir in cur:
            return
        cur = ([out_dir] + [d for d in cur if d != out_dir])[:32]
        _cfg.set("export_dirs", cur)
    except Exception:
        pass


def cmd_export(a):
    """`task-station export --dir <path>` — the generic, vault-independent episodic
    export. Writes the same per-task Markdown notes the Obsidian bridge produces
    (shared render_note) plus a wikilinked `index.md` into ANY directory, with no
    vault config required — a self-sufficient snapshot a second brain can ingest.

      --dir <path>       destination directory (created if absent). REQUIRED.
      --task <ref>       export ONE task (seq/id); or
      --status <s>       export only open|active|closed tasks; or
      --all              export every task (the default when no selector is given).
      --include a,b,c    sections to render: usage,prompts,history (default
                         usage,history — prompts is opt-in, a snapshot may leave
                         the machine).
      --since <ISO date> only tasks updated at/after this date.
      --prune            reconcile --dir against live tasks: drop notes whose task no
                         longer exists (or was redacted) + rebuild index.md.

    Sandbox note: a sandboxed session can only write under its cwd + $TMPDIR, so an
    export into an arbitrary directory must run from the hub (cwd ~) or an
    unsandboxed context — a denied write surfaces with a hint (the same constraint
    as --obsidian-vault into a protected root)."""
    out_dir = getattr(a, "dir", None)
    if not out_dir:
        print("usage: task-station export --dir <path> "
              "[--task <ref> | --all | --status open|active|closed] "
              "[--include usage,prompts,history] [--since <ISO date>] [--prune]")
        return
    out_dir = os.path.expanduser(out_dir)
    import export as _export, obsidian_sync
    ensure_seqs()   # every note filename needs a stable seq
    owner = _owner()
    if getattr(a, "prune", False):
        # Prune reconciles ONLY this owner's subtree — another owner's notes in a
        # shared dir are never touched.
        scoped = obsidian_sync.owner_dir(out_dir, owner)
        if not os.path.isdir(scoped):
            print("Nothing to prune — no export dir at %s." % scoped)
            return
        removed = _export.prune_dir(scoped, _live_task_ids())
        # Reconcile the category hubs too: a category whose last note was pruned loses
        # its hub page (and toggle-off prunes them all). Idempotent when nothing moved.
        _export.sync_category_hubs(scoped, enabled=_category_hubs_on(),
                                   subgroups=_subgroups_on())
        # Story hubs are the orthogonal axis — reconcile them the same way.
        _export.sync_story_hubs(scoped, enabled=_story_groups_on())
        if removed:
            _record_export_dir(out_dir)
            print("Pruned %d orphan note%s from %s (index.md updated)."
                  % (len(removed), "" if len(removed) == 1 else "s", scoped))
        else:
            print("Nothing to prune — %s is already in sync with live tasks." % scoped)
        return
    ref = getattr(a, "task", None)
    if ref:
        t = resolve_ref(ref) or load_task(ref)
        if not t:
            print("No task matching '%s'." % ref)
            return
        tasks = [t]
    else:
        status = getattr(a, "status", None)
        tasks = all_tasks()
        if status:
            want = STATUS_INPUT_ALIASES.get(status, status)
            tasks = [t for t in tasks if task_status(t) == want]
    include = _export.parse_include(getattr(a, "include", None))
    _usage_engine()   # point usage's path globals before note_context reads the ledger
    _begin_subgroups()   # one full-board detection for the whole export
    try:
        try:
            entries = _export.export_tasks(out_dir, tasks, _backend(),
                                           include=include, since=getattr(a, "since", None),
                                           related_fn=_export_related_fn, owner=owner,
                                           category_hubs=_category_hubs_on(),
                                           subgroups=_subgroups_on(),
                                           story_groups=_story_groups_on())
        except OSError as e:
            print("Could not write to %s: %s" % (out_dir, e))
            print("  A sandboxed session can only write under its cwd + $TMPDIR — run "
                  "`export` from the hub (cwd ~) or an unsandboxed shell, or pick a "
                  "directory inside the allowed roots.")
            return
        _record_export_dir(out_dir)   # so a later delete/--prune can locate this sidecar
        print("Exported %d task%s → %s (index.md + %s)"
              % (len(entries), "" if len(entries) == 1 else "s",
                 obsidian_sync.owner_dir(out_dir, owner),
                 ", ".join(sorted(include)) or "notes"))
    finally:
        _end_subgroups()


def _render_usage(task, data):
    """Terminal render of `usage --task`: the per-model mix, a per-session table
    (short sid, role+label, tokens, derived $), totals with the reported $ cross-
    check, and the derivation footnote."""
    seq = task.get("seq")
    out = ["Usage — Task [%s]%s  %s"
           % (task["id"][:8], (" #%s" % seq) if seq else "", task["title"])]
    models = data.get("models") or {}
    if not models:
        out.append("  No usage tracked yet — run `usage --task %s --refresh`, "
                   "or let the Stop/SessionStart hooks flush it."
                   % (seq if seq else task["id"][:8]))
        return "\n".join(out)
    import pricing
    ranked = sorted(models.items(), key=lambda kv: -kv[1].get("pct", 0))
    mix = []
    for m, d in ranked:
        fam = pricing.model_family(m)
        pct = round(d.get("pct", 0) * 100)
        mix.append("%s %d%%%s" % (fam, pct, " (unpriced)" if d.get("cost_usd") is None else ""))
    out.append("  Models:  " + " · ".join(mix))
    phases = data.get("phases") or {}
    ranked_p = sorted(phases.items(), key=lambda kv: -kv[1].get("pct", 0))
    pmix = ["%s %d%%" % (name.capitalize(), round(d.get("pct", 0) * 100))
            for name, d in ranked_p if round(d.get("pct", 0) * 100) > 0]
    if pmix:
        out.append("  Phases:  " + " · ".join(pmix))
    if data.get("sessions"):
        out.append("  Sessions (hub / worker):")
        for s in data["sessions"]:
            label = ("/%s" % s["label"]) if s.get("label") else ""
            # An unpriced (unknown-model) session shows an em-dash, never `$n/a`.
            cost = "—" if s.get("cost_usd") is None else "$%.4f" % s["cost_usd"]
            out.append("    %-8s %-7s%-14s in %d · out %d · cache-read %d · %s"
                       % (s["sid"], s["role"], label, s["in"], s["out"],
                          s["cache_read"], cost))
    # Never-`n/a` total: the priced subtotal reads as a `≥` floor when a model is
    # unknown; the delegate-reported $ stays as the cross-check when present.
    sc = _stats_cost(data)
    rep = data.get("reported_cost_usd") or 0.0
    if sc["kind"] == "reported":
        line = "  Totals:  in %d · out %d · %s" \
            % (data.get("total_in", 0), data.get("total_out", 0), sc["text"])
    else:
        line = "  Totals:  in %d · out %d · %s derived" \
            % (data.get("total_in", 0), data.get("total_out", 0), sc["text"])
        if rep > 0:
            line += " · $%.2f reported" % rep
    out.append(line)
    out.append("  Derivation: %s" % data.get("derived_note", ""))
    return "\n".join(out)


def cmd_usage(a):
    """`task-station usage` — the WS1 usage ledger surface.

      --task <ref>   render per-task usage: model mix, per-session breakdown
                     (hub vs each worker), token/$ totals, derivation footnote.
      --refresh      (re)scan the task's transcripts before rendering.
      --json         emit the raw task_usage() dict instead of the text render.
      --flush [--quiet]  hook entry point: incrementally rescan every open/active
                     task's sessions. Never crashes the hook path (errors → stderr,
                     stale numbers); a cheap no-op when usage tracking is off.
    """
    import config as _cfg
    usage = _usage_engine()
    store = _backend()
    mode = getattr(a, "mode", None)
    if mode == "scan-all":
        if not _cfg.usage_tracking_enabled():
            print("Usage tracking is off (config --usage off) — nothing scanned.")
            return
        n = usage.scan_all(store)
        print("Scanned %d session transcript%s into the usage ledger."
              % (n, "" if n == 1 else "s"))
        return
    if mode == "import-costbar":
        res = usage.import_costbar(store, path=getattr(a, "path", None))
        if res.get("error"):
            print("import-costbar: %s" % res["error"])
            return
        print("Imported %d costbar session%s (%d already in the ledger, %d still "
              "have a live transcript) from %s."
              % (res["imported"], "" if res["imported"] == 1 else "s",
                 res["skipped_in_ledger"], res["skipped_has_transcript"], res["path"]))
        return
    if getattr(a, "flush", False):
        quiet = getattr(a, "quiet", False)
        if not _cfg.usage_tracking_enabled():
            return
        n = 0
        for t in all_tasks():
            if task_status(t) not in STATUS_BOARD:
                continue
            try:
                usage.refresh_task(store, t)   # attributes hub + worker sessions first
                n += 1
            except Exception as e:
                sys.stderr.write("usage --flush: task %s failed: %s\n"
                                 % (t.get("seq", t.get("id", "?")), e))
        # Then sweep every remaining transcript so Week/Total reflect all sessions
        # (unattached ones land task_id NULL). Incremental → cheap; must run AFTER
        # refresh_task so a shared/worker session keeps its task attribution.
        try:
            usage.scan_all(store)
        except Exception as e:
            sys.stderr.write("usage --flush: scan-all failed: %s\n" % e)
        if not quiet:
            print("Usage flushed for %d task%s." % (n, "" if n == 1 else "s"))
        return
    if not getattr(a, "task", None):
        print("usage: task-station usage --task <ref> [--refresh] [--json]")
        return
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    if getattr(a, "refresh", False):
        try:
            usage.refresh_task(store, task)
        except Exception as e:
            sys.stderr.write("usage --refresh failed: %s\n" % e)
    data = usage.task_usage(store, task)
    if getattr(a, "as_json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    print(_render_usage(task, data))


def cmd_prompts(a):
    """`task-station prompts --task <ref> [--json|--md] [--all]` — the session-attributed
    trail of what drove a task, oldest first (hub + every delegated worker). By DEFAULT
    the curated view: only genuine human-typed prompts, each followed by Claude's
    last-bullet reply (read from the transcript) — commands, compaction rows, and
    hook/managed wrappers are filtered out.

      --json   emit the raw prompt-row list (uuid/ts/session/role/kind/text)
      --md     the shareable Markdown artifact (human prompts + replies; --all for raw)
      --all    the complete RAW trail (every kind, no replies) — the escape hatch
    """
    if not getattr(a, "task", None):
        print("usage: task-station prompts --task <ref> [--json|--md] [--all]")
        return
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    include_compact = bool(getattr(a, "all", False))
    prompts = _usage_engine().task_prompts(_backend(), task,
                                           include_compact=include_compact)
    if getattr(a, "as_json", False):
        print(json.dumps(prompts, indent=2, ensure_ascii=False))
        return
    if getattr(a, "as_md", False):
        print(_format_prompts_md(task, prompts, include_compact))
        return
    print(_format_prompts(task, prompts, include_compact))


def _update_one(ref, a):
    """Apply the update flags to the single task named by `ref` and return its
    result line(s). Never raises — a bad ref returns a no-match line — so a caller
    updating a comma list keeps going past it. The SAME flags are applied to every
    task in a batch (e.g. set several tasks' colour at once).

    The self-update runs through store.mutate (optimistic-locked) so two concurrent
    `/todo save`s can't clobber each other — appended decisions/steps/history all
    survive a reload-and-retry. --relate's cross-task `child` posts are side effects,
    so they run AFTER the atomic self-update, each itself optimistic-locked."""
    base = resolve_ref(ref) or load_task(ref)
    if not base:
        return "update: no task matching %r" % ref
    tid = base["id"]
    # The acting session (if any) attributes every event this update authors.
    session = getattr(a, "session", None)

    # Resolve --relate targets up front (reads only); the child-event posts they
    # trigger are deferred to after the self-update.
    relate_targets, relate_warnings = [], []
    for r2 in (getattr(a, "relate", None) or []):
        other = resolve_ref(r2) or load_task(r2)
        if not other:
            relate_warnings.append("update %s: ignoring --relate %s (no such task)" % (ref, r2))
        elif other.get("id") == tid:
            relate_warnings.append("update %s: ignoring --relate %s (a task can't relate to itself)" % (ref, r2))
        else:
            relate_targets.append(other)

    # The TYPED edges, resolved the same way and for the same reason: reads happen
    # here, the writes land inside the optimistic-locked self-update below, and the
    # ONE cross-task write (--replaces closing its target) is a deferred side effect.
    # A single value arrives as a string, a repeatable one as a list — normalise both,
    # and stringify: `resolve_ref` strips its argument, so a caller handing us a bare
    # int seq (a test, a future non-argparse entry point) must not blow up.
    def _reflist(v):
        vals = [] if v is None else (list(v) if isinstance(v, list) else [v])
        return [s for s in (str(x).strip() for x in vals if x is not None) if s]

    edge_targets, unrelate_targets = {}, []
    for flag, kind, vals in (
            ("--depends-on", "depends-on", _reflist(getattr(a, "depends_on", None))),
            ("--parent", "parent", _reflist(getattr(a, "parent", None))),
            ("--absorbed-by", "absorbed-by", _reflist(getattr(a, "absorbed_by", None))),
            ("--replaces", "replaces", _reflist(getattr(a, "replaces", None))),
            ("--duplicates", "duplicates", _reflist(getattr(a, "duplicates", None)))):
        for r2 in vals:
            other, why = _resolve_edge_ref(r2, tid, flag, kind)
            if other is None:
                relate_warnings.append("update %s: %s" % (ref, why))
            else:
                edge_targets.setdefault(kind, []).append(other)
    for r2 in _reflist(getattr(a, "unrelate", None)):
        other = resolve_ref(r2) or load_task(r2)
        if not other:
            relate_warnings.append("update %s: ignoring --unrelate %s (no such task)" % (ref, r2))
        else:
            unrelate_targets.append(other)

    # CYCLE CHECK — a WARNING, never a refusal. Direct precedent: the 600-char decision
    # advisory always stores, because a refusal makes the author drop a fact or fake two
    # entries out of one. The same holds here: refusing the write does not remove the
    # dependency, it only stops it being written down. See `_relation_cycle_path` for why
    # this is structurally incomplete and why every parent-chain walker must be cycle-safe.
    # The scan is lazy, so a plain --title update still reads nothing extra.
    cycle_warnings, _scan, absorb_children = [], None, set()
    for kind in ("depends-on", "parent"):
        for other in edge_targets.get(kind, []):
            if _scan is None:
                _scan = all_tasks()
            chain = _relation_cycle_path(base, other, kind, _scan)
            if chain:
                cycle_warnings.append(
                    "warning: this creates a %s cycle %s. Stored anyway."
                    % (kind, " → ".join("#%s" % s for s in chain)))
    # An absorbed task's children are NAMED in the handoff — never moved. Read before
    # the write, since the reconcile has to know what is now orphaned.
    if edge_targets.get("absorbed-by"):
        if _scan is None:
            _scan = all_tasks()
        absorb_children = {c.get("seq") for c in _parent_children(base, _scan)}

    cap = {}

    def _apply(task):
        msgs = list(relate_warnings)
        changed = []
        # THE UNDO TRAIL for the two verbs that write IMMEDIATELY. `--supersedes` and
        # `--step-supersede` never pass through `heal --apply`, so they take no backup —
        # and since `/heal` stopped asking for approval before it acts, they are the
        # writes with nothing standing in front of them at all. Each successful one
        # records the exact command that takes it back, with the index it really touched;
        # they are printed together below the result line. Every other flag here either
        # names its own reversal already (`--summary` → `--restore-summary`) or is a
        # plain field write with nothing to reverse.
        undos = []
        # Typed-edge targets that earned a post-save notice: a replaced parent, and an
        # absorb (whose reconcile handoff is REQUIRED output, not decoration).
        parent_notes, absorb_notes = [], []
        if a.title is not None:
            task["title"] = a.title.strip(); changed.append("title")
        # `--summary` REPLACES wholesale, and that replacement used to be the one
        # DESTRUCTIVE write left in this codebase: supersede, split, merge and
        # step-supersede all keep the original and offer a restore, while the summary —
        # the FIRST field a resuming session reads — could be lost outright to a thin
        # save. The previous text is now preserved append-only BEFORE the overwrite. An
        # unchanged rewrite is not a version (nothing was lost) and neither is replacing
        # a blank summary (there was nothing to keep).
        if a.summary is not None:
            new_summary = a.summary.strip()
            prev_summary = (task.get("summary") or "").strip()
            version = (_save.push_summary(task, prev_summary, sid=session)
                       if prev_summary and prev_summary != new_summary else None)
            task["summary"] = new_summary; changed.append("summary")
            add_event(task, "summary", "summary rewritten", session)
            if version:
                msgs.append("update %s: the previous summary (%d chars) was PRESERVED as "
                            "version %d — `update --task %s --restore-summary` puts it "
                            "back (`--restore-summary <n>` for an older one), and "
                            "`/todo %s history` lists every version. Nothing was lost."
                            % (ref, len(prev_summary), version, ref, ref))
        if a.append_summary:
            prev = (task.get("summary") or "").rstrip()
            add = a.append_summary.strip()
            task["summary"] = (prev + "\n" + add) if prev else add
            changed.append("summary+")
        # --restore-summary [n]: the inverse of the replace above, and the reason the
        # replace is safe. Bare = the most recent preserved version, because "that save
        # wrote a thin summary, put the good one back" is the overwhelmingly common case
        # and looking a number up first is a step between someone and undoing a loss.
        restore_sum = getattr(a, "restore_summary", None)
        if restore_sum is not None:
            wanted = str(restore_sum).strip()
            kept_before = len(_save.summary_versions(task))
            ok, err, v = _save.restore_summary(task, wanted or None, sid=session)
            if ok:
                changed.append("summary↺")
                add_event(task, "summary", "summary restored to version %d" % v, session)
                kept_now = len(_save.summary_versions(task))
                # Only claim the restore preserved something when it DID: restoring over
                # a blank summary replaces nothing, and saying otherwise would hand the
                # reader a version number that does not exist.
                msgs.append("update %s: restored summary version %d%s"
                            % (ref, v,
                               ("; the text it replaced is itself kept as version %d, so "
                                "the restore is reversible too." % kept_now)
                               if kept_now > kept_before
                               else " (the summary it replaced was empty, so there was "
                                    "nothing to preserve)."))
            else:
                msgs.append("update %s: %s" % (ref, err))
        state = getattr(a, "state", None)
        # Whether the state TEXT actually moved — not merely whether --state was passed.
        # Re-writing the identical line does not make a stale NEXT fresher, and stamping
        # it as if it did would let a task keep one alive by copying it forward.
        state_changed = False
        if state is not None:
            # The model-curated "where it stands / next step" briefing line. Distinct
            # from summary (what the task IS); blank clears it. No LLM — the model in
            # the loop maintains it as it works.
            state_changed = state.strip() != (task.get("state") or "").strip()
            task["state"] = state.strip()
            changed.append("state")
        goal = getattr(a, "goal", None)
        if goal is not None:
            # One-line "what done looks like." Blank clears it.
            goal_moved = goal.strip() != (task.get("goal") or "").strip()
            task["goal"] = goal.strip()
            changed.append("goal")
            if goal_moved:
                # Baseline for heal's GOAL REVIEW: the moment, plus how many decisions
                # the log held then. Without it that section can only say "cannot be
                # counted" — and it must never say "0", which reads as "nothing has
                # happened since" when the truth is "nobody recorded when this was
                # written". Stamped only when the TEXT actually moved, the same rule
                # `state_changed` above follows: re-writing the identical line does not
                # make a goal that reality has overtaken any fresher, and re-baselining
                # on a no-op write would hide exactly the drift this measures. A --goal
                # and a --decision in the SAME call snapshot before that decision lands,
                # so it reads as one already accrued — an over-count of one, in the
                # direction that prompts a look rather than the one that hides it.
                _heal.stamp_goal_touched(task)
        # Granular step ops (stable 1-based indices). --step-add appends; --step-done/
        # --step-undone tick/untick by number; an out-of-range index warns, never crashes.
        # `last_step_idx` is what --step-supersede links to, exactly as
        # `last_decision_idx` is what --supersedes links to below.
        last_step_idx = None
        for text in (getattr(a, "step_add", None) or []):
            if append_step(task, text):
                changed.append("step+")
                last_step_idx = len(task.get("steps") or [])
        task_steps = task.get("steps") or []
        # Every step refusal reads `ignoring <flag> <n> — <why>`: the flag and number
        # first (so the reader sees WHICH op was dropped), then the reason from
        # `steps.py`, which now distinguishes "no such step" from "that step is
        # superseded and off the active checklist".
        for n in (getattr(a, "step_done", None) or []):
            ok, err = _steps.set_done(task_steps, n, True)
            if ok:
                changed.append("step✓")
            else:
                msgs.append("update %s: ignoring %s" % (ref, err))
        for n in (getattr(a, "step_undone", None) or []):
            ok, err = _steps.set_done(task_steps, n, False)
            if ok:
                changed.append("step☐")
            else:
                msgs.append("update %s: ignoring %s" % (ref, err))
        # --step-supersede <n>: the checklist's ONE reconcile verb, shaped exactly like
        # the decision one. A step that has gone stale had no honest exit before this —
        # ticking it done is a lie, deleting it destroys the record, and a "do not
        # execute step 3" warning step is the anti-pattern. Non-destructive: the step
        # leaves the checklist and both sides of the n/m counter, keeps its text in
        # `/todo <n> history` marked with what replaced it, and --step-restore undoes it.
        # A --step-add in the SAME update is recorded as the replacement. There is
        # deliberately no --step-edit: rewriting a step in place mutates the record.
        for n in (getattr(a, "step_supersede", None) or []):
            ok, err = _steps.mark_superseded(task_steps, n, last_step_idx)
            if ok:
                changed.append("step⊘")
                undos.append("retired step %s%s → `update --task %s --step-restore %s`"
                             % (n,
                                (" (replaced by step %d)" % last_step_idx)
                                if last_step_idx else "",
                                ref, n))
                add_event(task, "step",
                          "step %s superseded%s"
                          % (n, (" by step %d" % last_step_idx) if last_step_idx else ""),
                          session)
            else:
                msgs.append("update %s: ignoring %s" % (ref, err))
        for n in (getattr(a, "step_restore", None) or []):
            ok, err = _steps.restore(task_steps, n)
            if ok:
                changed.append("step↺")
                add_event(task, "step", "step %s restored to the checklist" % n, session)
            else:
                msgs.append("update %s: ignoring %s" % (ref, err))
        # --decision, plus the supersession/pin primitive. `--supersedes <n>` (repeatable)
        # and `--pin` attach to the LAST --decision appended in THIS update — one new
        # decision may replace several old ones. Every failure is a loud message, never a
        # silent no-op: a dropped supersession leaves the wrong decision live, which is
        # precisely the bug. `--pin-decision`/`--unpin-decision` act on existing entries.
        last_decision_idx = None
        for text in (getattr(a, "decision", None) or []):
            if append_decision(task, text, session):
                changed.append("decision")
                last_decision_idx = len(task.get("decisions") or [])
                # LENGTH ADVISORY — a suggestion, never a gate. The entry is already
                # stored, in full: refusing a long decision would push the author to
                # drop a fact or fake two entries out of one, which is exactly what
                # the old pin cap's refusal produced.
                warn = _dec.length_warning(task["decisions"][last_decision_idx - 1],
                                           last_decision_idx)
                if warn:
                    msgs.append("update %s: %s" % (ref, warn))
        # NOT setdefault — a task with no decisions must not grow the field just because
        # a bad --supersedes was passed; an empty list makes every index check fail loudly.
        entries = task.get("decisions") or []
        supersedes = [n for n in (getattr(a, "supersedes", None) or [])]
        if supersedes and last_decision_idx is None:
            msgs.append("update %s: --supersedes needs a --decision in the same update "
                        "(the new decision is what replaces the old one)" % ref)
        else:
            for n in supersedes:
                ok, err = _dec.mark_superseded(entries, n, last_decision_idx)
                if ok:
                    changed.append("supersede")
                    undos.append("superseded decision %s (by decision %s) → `update "
                                 "--task %s --restore-decision %s`"
                                 % (n, last_decision_idx, ref, n))
                    add_event(task, "decision",
                              "decision %s superseded by decision %s" % (n, last_decision_idx),
                              session)
                else:
                    msgs.append("update %s: %s" % (ref, err))
        if getattr(a, "pin", False):
            if last_decision_idx is None:
                msgs.append("update %s: --pin needs a --decision in the same update "
                            "(to pin an existing one use --pin-decision <n>)" % ref)
            else:
                ok, err = _dec.set_pin(entries, last_decision_idx, True, flag="--pin")
                if ok:
                    changed.append("pin")
                else:
                    msgs.append("update %s: %s" % (ref, err))
        for n in (getattr(a, "pin_decision", None) or []):
            ok, err = _dec.set_pin(entries, n, True)
            if ok:
                changed.append("pin")
            else:
                msgs.append("update %s: %s" % (ref, err))
        for n in (getattr(a, "unpin_decision", None) or []):
            ok, err = _dec.set_pin(entries, n, False)
            if ok:
                changed.append("unpin")
            else:
                msgs.append("update %s: %s" % (ref, err))
        # --restore-decision <n>: the ONE inverse of all three reconcile verbs
        # (supersede / split / merge), which is what makes each of them reversible.
        # Reads the label BEFORE restoring so the result line can say what it undid.
        for n in (getattr(a, "restore_decision", None) or []):
            label = None
            try:
                label = _dec.replacement_label(entries[int(n) - 1])
            except (TypeError, ValueError, IndexError):
                pass
            ok, err = _dec.restore(entries, n)
            if ok:
                changed.append("restore")
                add_event(task, "decision",
                          "decision %s restored (was %s)" % (n, label or "replaced"),
                          session)
            else:
                msgs.append("update %s: %s" % (ref, err))
        # --log: append a dated milestone/finding to the append-only `history` trail.
        # Off the default resume path — surfaced only by `/todo <n> history`.
        for text in (getattr(a, "log", None) or []):
            if append_history(task, text, session):
                changed.append("log")
        # --relate: record the edge on THIS task (idempotent — append_related dedupes);
        # the reciprocal `child` post on each newly-related task happens after.
        related_new = []
        for other in relate_targets:
            if append_related(task, other, "related"):
                changed.append("relate")
                related_new.append(other)
        # --- the typed edges ---------------------------------------------------
        # Every one writes on THIS task (the subordinate side stores the edge), so the
        # whole block stays inside the one optimistic-locked mutation. The result token
        # each appends to `changed` IS the reported line — `parent #503 (REPLACED #499)`
        # reads back verbatim through the "updated task N: …" join below.
        for kind in ("depends-on", "duplicates", "replaces"):
            for other in edge_targets.get(kind, []):
                if append_related(task, other, kind):
                    changed.append("%s #%s" % (kind, other.get("seq")))
        # --parent: at most ONE. A second write REPLACES the first and NEVER silently
        # swaps — a task under two parents double-counts in every roll-up, so this is a
        # tree, not a DAG.
        for other in edge_targets.get("parent", []):
            replaced = [r.get("seq") for r in (task.get("related") or [])
                        if r.get("kind") == "parent" and r.get("id") != other.get("id")]
            if replaced:
                task["related"] = [r for r in (task.get("related") or [])
                                   if not (r.get("kind") == "parent"
                                           and r.get("id") != other.get("id"))]
            added = append_related(task, other, "parent")
            if added or replaced:
                changed.append("parent #%s%s"
                               % (other.get("seq"),
                                  (" (REPLACED %s)"
                                   % ", ".join("#%s" % s for s in replaced))
                                  if replaced else ""))
                parent_notes.append(other)
        # --absorbed-by: THIS task's work became part of the other one, so THIS task
        # closes — which is what keeps it a single-task write, no cross-task write in
        # either direction. Steps are NOT merged and children are NOT moved; the
        # reconcile handoff printed below is the deliverable, not decoration.
        for other in edge_targets.get("absorbed-by", []):
            was = task_status(task)
            added = append_related(task, other, "absorbed-by")
            shut = close_task_inplace(
                task, note="absorbed by #%s" % other.get("seq"), session=session)
            if added or shut:
                changed.append("absorbed-by #%s%s"
                               % (other.get("seq"),
                                  (" · task CLOSED (was %s)" % status_display(was))
                                  if shut else " · task was already closed"))
                absorb_notes.append((other, shut))
        # --unrelate: the ONE removal verb. Reports what went; removing nothing is a
        # plain report, not an error.
        for other in unrelate_targets:
            gone = remove_related(task, other.get("id"))
            if gone:
                changed.append("unrelated #%s (removed: %s)"
                               % (other.get("seq"), ", ".join(gone)))
            else:
                msgs.append("update %s: no edge to #%s" % (ref, other.get("seq")))
        # --pr [--pr-desc]: the desc applies to the url(s) given in the SAME update;
        # a --pr-desc with NO --pr applies to the most-recent stored pr instead.
        pr_urls = getattr(a, "pr", None) or []
        pr_desc = getattr(a, "pr_desc", None)
        for url in pr_urls:
            if add_pr(task, url, pr_desc or ""):
                changed.append("pr")
        if pr_desc is not None and not pr_urls:
            if set_pr_desc(task, pr_desc):
                changed.append("pr")
        # --story [--story-desc]: mirrors --pr exactly.
        story_urls = getattr(a, "story", None) or []
        story_desc = getattr(a, "story_desc", None)
        for url in story_urls:
            if add_story(task, url, story_desc or ""):
                changed.append("story")
        if story_desc is not None and not story_urls:
            if set_story_desc(task, story_desc):
                changed.append("story")
        tvis = getattr(a, "trail_visibility", None)
        if tvis is not None and task.get("trail_visibility") != tvis:
            task["trail_visibility"] = tvis
            changed.append("trail-visibility")
        if a.color is not None and cats:
            task["color"] = cat_color(a.color); changed.append("color")
        if a.effort is not None:
            e = normalize_effort(a.effort)
            if e is None:
                msgs.append("update: ignoring --effort %r — use xs/s/m/l/xl (or 1–5)." % a.effort)
            else:
                task["effort"] = e; changed.append("effort")
        # WRITE-TIME SNAPSHOTS, taken LAST — after this update's own --decision / --log
        # appends have landed. Taken any earlier, a summary written in the same call as
        # three decisions would immediately read as three decisions out of date, and the
        # staleness check would fire on the freshest summary the task has ever had.
        if a.summary is not None:
            _save.mark_summary_written(task)
        if state_changed:
            _save.mark_state_written(task)
        # THE CHECKPOINT STAMP. `last_full_save_ts` means "a full structured checkpoint
        # was CAPTURED" — the thing that tells one apart from a lighter --state refresh —
        # and it used to be written when the [SAVE] block was PRINTED, so a session that
        # ran /save and wrote nothing left a task claiming a checkpoint with an empty
        # summary. That is heal's zero-operation `--apply` bug one layer earlier. The
        # stamp now belongs to the WRITE, and is INFERRED from the pair that IS a
        # checkpoint rather than declared by a flag nobody has to honour.
        if _save.is_checkpoint_write(a.summary, getattr(a, "state", None)):
            _save.stamp_checkpoint(task)
            changed.append("checkpoint")
        if changed:
            clear_provisional(task)   # the model refined it → real work, not a throwaway
            # A refresh of the structured digest clears the staleness flag — the digest
            # is current again, so the opt-in Stop nudge won't re-fire until new work lands.
            if {"goal", "state", "step+", "step✓", "step☐", "step⊘", "step↺",
                    "decision", "log", "supersede", "pin", "unpin"} & set(changed):
                clear_digest_dirty(task)
            # register=False: a scope update from any session must NOT enroll that session
            # as a worker on this task, but the log event is still attributed to it.
            touch(task, session=session, note="scope updated: " + ", ".join(changed),
                  register=False)
        cap["changed"], cap["msgs"], cap["related_new"] = changed, msgs, related_new
        cap["undos"] = undos
        cap["parent_notes"], cap["absorb_notes"] = parent_notes, absorb_notes

    updated = g("mutate")(tid, _apply)
    if updated is None:
        return "update: no task matching %r" % ref
    changed, msgs, related_new = cap["changed"], cap["msgs"], cap["related_new"]
    undos = cap.get("undos") or []
    label = updated.get("seq", updated["id"][:8])
    # Cycle warnings ride out even when nothing changed (a re-run of an already-stored
    # edge still describes a cycle the reader should know about).
    msgs.extend(cycle_warnings)
    if not changed:
        msgs.append("update %s: nothing to change (pass --title/--summary/--append-summary/"
                    "--restore-summary/--goal/--state/--step-add/--step-done/--step-undone/"
                    "--step-supersede/--step-restore/--decision/--supersedes/--pin/"
                    "--pin-decision/--unpin-decision/--restore-decision/--log/--relate/"
                    "--depends-on/--parent/--absorbed-by/--replaces/--duplicates/"
                    "--unrelate/--pr/--pr-desc/--story/--story-desc/--color/--effort)" % label)
        return "\n".join(msgs)
    # Reciprocal `child` post on each newly-related task — a side effect, so it runs
    # AFTER the self-update, each write itself optimistic-locked.
    for other in related_new:
        g("mutate")(other["id"], lambda o, seq=updated.get("seq"), title=updated.get("title"):
                    add_event(o, "child", "related ← #%s: %s" % (seq, title), session))
    # --replaces closes the TARGET — the opposite direction from --absorbed-by, which
    # closes the task being updated. That makes it the one typed edge with a cross-task
    # write, so it runs HERE (after the atomic self-update) and is itself
    # optimistic-locked, exactly like --relate's reciprocal posts above. Its notices are
    # collected rather than appended, so they print under the result line, not above it.
    replace_notes = []
    for other in edge_targets.get("replaces", []):
        was, shut = task_status(other), {"v": False}

        def _shut(o, _seq=updated.get("seq"), _title=updated.get("title"), _f=shut):
            _f["v"] = close_task_inplace(o, note="replaced by #%s" % _seq, session=session)
            add_event(o, "child", "replaced by #%s: %s" % (_seq, _title), session)
        target = g("mutate")(other["id"], _shut)
        if target is None:
            continue
        if shut["v"]:
            # NO reconcile notice, and that is the point: replacing says the approach was
            # dropped, so nothing was inherited and nothing needs recalculating. The
            # asymmetry with --absorbed-by is the whole reason both verbs exist.
            replace_notes.append("  ↳ #%s CLOSED (was %s) — its approach was replaced, so "
                                 "no work carried over." % (other.get("seq"),
                                                            status_display(was)))
            _obsidian_event(target, "closed")
            _stream_emit("task.status", target,
                         {"status": target.get("status"),
                          "closed_ts": target.get("closed_ts")}, session)
        else:
            replace_notes.append("  ↳ #%s was already closed." % other.get("seq"))
    # F6.2: a manually-added PR/story signal triggers the same cross-person auto-link as
    # capture — pair with any peer task carrying the same signal (idempotent). Only when
    # this update actually touched a signal, so a pure --title edit does no feed scan.
    if {"pr", "story"} & set(changed):
        try:
            if _autolink_task_signals(updated, session):
                updated["updated_ts"] = _now()
                save_task(updated)
        except Exception:
            pass
    _obsidian_sync(updated)
    # Stream: a scope change is task.updated; each new --relate edge is its own
    # task.relation (so a pure --relate emits exactly one relation, no task.updated).
    if [c for c in changed if c != "relate"]:
        _stream_emit("task.updated", updated, _stream_updated_data(updated, changed), session)
    for other in related_new:
        _stream_emit("task.relation", updated,
                     {"kind": "related",
                      "other": {"uuid": other.get("uuid") or other.get("id"),
                                "seq": other.get("seq")}}, session)
    task = updated                       # post-save notices below read the saved task
    msgs.append("updated task %s: %s" % (label, ", ".join(changed)))
    if undos:
        # Printed on the WRITE, not left to a skill to remember. These two verbs land
        # immediately, with no backup and — since `/heal` stopped asking before it acts —
        # nothing standing in front of them, so the report that says a mark was made is
        # the right place to say how to take it back.
        msgs.append("  UNDO — each mark above and the ONE command that reverses it:")
        msgs.extend("    • %s" % u for u in undos)
        msgs.append("    Nothing was deleted: the replacement stays on the record either "
                    "way, and a restore puts the original back BESIDE it.")
    msgs.extend(replace_notes)
    # --parent: warn when the NEW parent carries an authored `state`. A task with
    # children has a COMPUTED state, which authors cannot write — so say now that the
    # hand-written line is going to be replaced rather than folded in.
    for other in (cap.get("parent_notes") or []):
        if (other.get("state") or "").strip():
            msgs.append("note: #%s now has children — its state becomes DERIVED when "
                        "computed state ships;\n      the current hand-written state "
                        "will be replaced, not merged." % other.get("seq"))
    # --absorbed-by: the REQUIRED handoff. Absorbing is a reconcile, not a mechanical
    # move: a survivor whose checklist is the blind union of two plans describes work
    # nobody intends to do. So steps are never merged, children are never moved, and
    # nothing is written on the survivor at all (absorb stays a single-task write) —
    # this notice is the entire deliverable on that side.
    for other, shut in (cap.get("absorb_notes") or []):
        if not shut:
            continue
        osq = other.get("seq")
        msgs.append("RECONCILE NEEDED on #%s: absorbing inherits work, so #%s's steps "
                    "must be recalculated —\n  some of #%s's are already done there, "
                    "some are now redundant, some conflict.\n"
                    "  Run: task-station heal --task %s" % (osq, osq, label, osq))
        kids = sorted(s for s in absorb_children if s is not None)
        if kids:
            msgs.append("  #%s's children were NOT moved and are now orphaned: %s — where "
                        "each belongs is part of the reconcile, not a mechanical reparent."
                        % (label, ", ".join("#%s" % s for s in kids)))
        _obsidian_event(updated, "closed")
        _stream_emit("task.status", updated,
                     {"status": updated.get("status"),
                      "closed_ts": updated.get("closed_ts")}, session)
    # THE COLD-READ CHECK, MECHANICAL. It used to be advice — "re-read the digest as if
    # you have no memory of this conversation" — which is unfalsifiable: no output ever
    # said whether it was done. Two of its conditions are decidable, so the machine
    # decides them, here, on the write that claims the checkpoint. It costs nothing and
    # needs no extra call; `/todo <n> save --check` re-runs the same check on demand.
    if "checkpoint" in changed:
        msgs.append("  CHECKPOINT STAMPED — this task now records that a FULL structured "
                    "checkpoint was captured (a --summary and a --state written "
                    "together). A --state refresh alone does not stamp, which is what "
                    "keeps the two tellable apart.")
        fails = _save.cold_read_failures(task)
        if fails:
            msgs.append("  COLD-READ CHECK: %d condition(s) still FAIL — patch each with "
                        "another `update` before you finish:" % len(fails))
            msgs.extend("    • %s" % f for f in fails)
        else:
            msgs.append("  COLD-READ CHECK: pass — every named slot carries something and "
                        "`state` leads with `NEXT:`.")
    if "color" in changed and cats and hasattr(cats, "auto_enable"):
        notice = cats.auto_enable(task.get("color"))
        if notice:
            msgs.append("  ↳ " + notice)
    if "color" in changed:
        _emit_tint_to_origin(task.get("color"))   # recategorize tints NOW, not next prompt
    if "title" in changed:
        g("_emit_title_to_origin")(task)          # a rename relabels the window NOW, not next prompt
    # A scope change is the moment effort might have grown or shrunk — prompt a
    # re-rate so the column tracks reality, but only when this update touched
    # scope WITHOUT already re-rating (so re-setting effort itself stays quiet).
    if {"title", "summary", "summary+"} & set(changed) and "effort" not in changed:
        cur = task.get("effort")
        shown = ("currently %s %s" % (EFFORT_GAUGE[cur], cur)) if cur in EFFORT_GAUGE else "currently unset"
        msgs.append("  ↳ scope changed (%s). If the work now looks bigger or smaller, re-rate:\n"
                    "      task-station update --task %s --effort <xs|s|m|l|xl>"
                    % (shown, label))
    return "\n".join(msgs)


def cmd_update(a):
    """Amend a task's title / summary / scope / colour after creation.

    Fills the gap that `summary` was otherwise frozen at create — keeps the task
    description current as scope drifts. `--task` accepts a comma-separated list:
    the same flags are applied to each task, one result line per ref, a bad ref
    reported but not aborting the rest."""
    refs = _split_refs(a.task)
    if not refs:
        sys.stderr.write("update: no task given\n")
        return
    for r in refs:
        print(_update_one(r, a))


def _pin_one(ref, a):
    """Pin session `a.session` as the resume target for the task named by `ref`
    and return its result line. A bad ref returns a no-match line (never raises)
    so a comma list keeps going past it."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "pin: no task matching %r" % ref
    # `pin --new`: pre-bind an UNBORN session as the pin. Mints a uuid, records it
    # (preborn) + links it, and emits `claude --session-id <uuid>` so opening it
    # BECOMES the task's session — bypassing the stale-pin "no transcript" guard
    # for this intentional case.
    if getattr(a, "new", False):
        sid, cmd = fresh_resume_command(task, preborn=True)
        task["pinned_session"] = sid
        touch(task, note="pinned a fresh (unborn) session %s" % sid[:8])
        save_task(task)
        label = task.get("seq", task["id"][:8])
        return ("Pinned task %s → fresh session %s (unborn — opens on first launch)\n"
                "  resume: %s" % (label, sid[:8], cmd))
    if not a.session:
        return "pin: task %s needs --session <id> or --new" % task.get("seq", task["id"][:8])
    task["pinned_session"] = a.session
    meta = task.setdefault("session_meta", {})
    if a.session not in meta:
        path = g("_find_session_path")(a.session)
        meta[a.session] = {"cwd": (_session_cwd(path) if path else None) or os.getcwd(),
                           "ts": _now(), "role": "hub"}
    touch(task, note="pinned resume session %s" % a.session[:8])
    save_task(task)
    label = task.get("seq", task["id"][:8])
    if g("_find_session_path")(a.session):
        return ("Pinned task %s → session %s\n  resume: %s"
                % (label, a.session[:8], resume_command(task)))
    return ("Pinned task %s → session %s — note: no transcript found for that id yet; "
            "/todo falls back to the heuristic until it appears." % (label, a.session[:8]))


def cmd_pin(a):
    """Pin a specific session as the task's canonical resume target (PK-style).

    `/todo` then always resumes THIS session, overriding the most-recent-substantive
    heuristic — the cwd is still read live from the transcript, so the pin survives
    directory changes. A pin with no findable live transcript is ignored (falls back
    to the heuristic) so it can't strand you. `--task` accepts a comma-separated
    list (pin the session across several tasks), one result line per ref.

    With NO --task but a --session given (the bare `/pin`), pin THIS session to its
    OWN attached task — resolved via get_link(session) — so /todo always resumes here.
    With neither, the usual "no task given" stderr line."""
    refs = _split_refs(a.task)
    if not refs:
        # bare /pin: pin this session to the task it's attached to.
        if getattr(a, "session", None):
            task_id = get_link(a.session)
            task = load_task(task_id) if task_id else None
            if not task:
                print("No task is attached to this session — nothing to pin.")
                return
            print(_pin_one(str(task.get("seq") or task["id"]), a))
            return
        sys.stderr.write("pin: no task given\n")
        return
    for r in refs:
        print(_pin_one(r, a))


def _unpin_one(ref):
    """Drop the pinned resume session on the task named by `ref` and return its
    result line. A bad ref returns a no-match line (never raises)."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "unpin: no task matching %r" % ref
    if task.pop("pinned_session", None):
        touch(task, note="unpinned resume session")
        save_task(task)
        return ("Unpinned task %s — resume reverts to most-recent-substantive."
                % task.get("seq", task["id"][:8]))
    return "Task %s was not pinned." % task.get("seq", task["id"][:8])


def cmd_unpin(a):
    """Drop a task's pinned resume session — revert to most-recent-substantive.

    `--task` accepts a comma-separated list (unpin several at once), one result
    line per ref, a bad ref reported but not aborting the rest.

    With NO --task but a --session given (the bare `/unpin`), unpin the task THIS
    session is attached to — resolved via get_link(session) — the symmetric inverse
    of the bare `/pin`. With neither, the usual "no task given" stderr line."""
    refs = _split_refs(a.task)
    if not refs:
        # bare /unpin: unpin the task this session is attached to.
        if getattr(a, "session", None):
            task_id = get_link(a.session)
            task = load_task(task_id) if task_id else None
            if not task:
                print("No task is attached to this session — nothing to unpin.")
                return
            print(_unpin_one(str(task.get("seq") or task["id"])))
            return
        sys.stderr.write("unpin: no task given\n")
        return
    for r in refs:
        print(_unpin_one(r))


def _emit_tint_to_origin(color):
    """Best-effort: paint the originating window the moment a colour is assigned
    (create / attach / recategorize) instead of waiting for the next prompt.

    The prompt-tint / session-tint hooks emit the escape to stdout and the shell
    hook redirects it to the origin TTY; but a `create`/`attach` command runs in
    Claude's captured Bash tool, whose stdout is the model-visible RESULT, not the
    terminal — so writing the escape to stdout here would corrupt that result and
    never reach the window. Instead we resolve the origin TTY ourselves (same
    `origin-tty.sh` the hooks use) and write the bytes straight to it.

    Pure best-effort: a no-op (never raises, never writes to stdout) when tinting
    is off, categories/tint are unavailable, no colour, or the TTY can't be
    resolved — the next UserPromptSubmit hook still tints as it did before."""
    if not color:
        return
    if not cats or not getattr(cats, "TINT_TERMINAL", False):
        return
    import config, term
    if not config.tint_enabled():
        return
    esc = cats.tint_escape(color, term.detect())
    if not esc:
        return
    try:
        dev = g("subprocess").check_output(
            ["bash", os.path.join(g("BASE"), "origin-tty.sh")],
            stderr=g("subprocess").DEVNULL).decode().strip()
    except Exception:
        dev = ""
    if not dev:
        return
    try:
        with open(dev, "w") as fh:
            fh.write(esc)
    except Exception:
        pass   # unwritable/vanished TTY — the prompt hook will tint next message


def _emit_title_to_origin(task):
    """Best-effort: relabel the originating window `#<seq>: <title>` the moment a
    task is created / attached / renamed, instead of waiting for the next prompt.

    Same rationale + mechanism as _emit_tint_to_origin: a create/attach/update
    command runs in Claude's captured Bash tool (stdout = the model-visible
    result, not the terminal), so we resolve the origin TTY ourselves (the same
    `origin-tty.sh` the hooks use) and write the OSC-0 escape straight to it.
    Pure best-effort: a no-op (never raises, never writes stdout) when the title
    feature is off or the TTY can't be resolved — the UserPromptSubmit hook still
    relabels the window next message."""
    if not task:
        return
    import config
    if not config.title_enabled():
        return
    ensure_seqs()
    esc = "\033]0;#%s: %s\007" % (task.get("seq", "?"), task.get("title", ""))
    try:
        dev = g("subprocess").check_output(
            ["bash", os.path.join(g("BASE"), "origin-tty.sh")],
            stderr=g("subprocess").DEVNULL).decode().strip()
    except Exception:
        dev = ""
    if not dev:
        return
    try:
        with open(dev, "w") as fh:
            fh.write(esc)
    except Exception:
        pass   # unwritable/vanished TTY — the prompt hook will relabel next message


def _repos_load(repo_index, roots, data_dir):
    """Return the structured index, reading repos.json if present, else building
    it from a fresh scan (so term/--json queries work before a first --refresh)."""
    p = os.path.join(data_dir, "repos.json")
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        # Auto-build on a read path: stay deterministic (no model calls); explicit
        # `repos --refresh` is the only place enrichment runs.
        return repo_index.build_index(roots, data_dir=data_dir, use_llm=False)


def _repos_render_manifest(repo_index, data_dir):
    """Print the full include/exclude manifest — every discovered repo + flags."""
    manifest = repo_index.load_manifest(data_dir)
    print("repo manifest  %s" % os.path.join(data_dir, "repos.config.json"))
    print("  toggle: repos include/exclude <name>  ·  repos enrich <name> [on|off]")
    print("")
    if not manifest:
        print("  (empty — run `repos --refresh` to discover repos)")
        return
    print("  index  enrich  repo")
    for name in sorted(manifest):
        e = repo_index.entry_for(manifest, name)
        print("  %-6s %-7s %s" % (
            "[x]" if e["index"] else "[ ]",
            "[x]" if e["enrich"] else "[ ]",
            name + ("" if e["index"] else "   (excluded)")))


def _repos_set_flag(repo_index, data_dir, name, key, value):
    """Set a manifest flag for `name` (or its basename if a path was given).
    Returns the resolved repo name, or None if it isn't in the manifest."""
    manifest = repo_index.load_manifest(data_dir)
    candidate = name if name in manifest else os.path.basename(os.path.normpath(name))
    if candidate not in manifest:
        return None
    entry = repo_index.entry_for(manifest, candidate)
    entry[key] = value
    manifest[candidate] = entry
    repo_index.save_manifest(data_dir, manifest)
    return candidate


def _repos_manifest_action(repo_index, data_dir, action, terms):
    """Handle the no-JSON-editing toggle subcommands. Returns True if it consumed
    the invocation."""
    if action == "config":
        _repos_render_manifest(repo_index, data_dir)
        return True
    if len(terms) < 2:
        print("usage: repos %s <name>%s" % (
            action, " [on|off]" if action == "enrich" else ""))
        return True
    name = terms[1]
    if action == "include":
        key, value, label = "index", True, "included (index:true)"
    elif action == "exclude":
        key, value, label = "index", False, "excluded (index:false)"
    else:  # enrich
        on = (terms[2].lower() != "off") if len(terms) > 2 else True
        key, value, label = "enrich", on, "enrich:%s" % ("on" if on else "off")
    resolved = _repos_set_flag(repo_index, data_dir, name, key, value)
    if resolved is None:
        print("repos: no repo named %r in the manifest. Run `repos --refresh` to "
              "discover repos, then `repos config` to list them." % name)
    else:
        print("repos: %s → %s" % (resolved, label))
        if action == "enrich" and value:
            print("       (its README + tree NAMES will be sent to the model on the "
                  "next `repos --refresh`)")
    return True


def cmd_repos(a):
    """Hub repo index: `repos [show]` prints repos.md (building it if missing),
    `repos --refresh [--quiet] [--dry-run] [--re-summarize]` rescans +
    rewrites the index, `repos <term...>` ranks matches, `--json` emits the
    structured list. Include/exclude surface: `repos config` lists the manifest;
    `repos include/exclude <name>` and `repos enrich <name> [on|off]` flip flags.
    First-run onboarding via `--detect-roots` + `--set-roots`. Not stored in
    tasks.db; lives at <data_dir>/repos.{md,json} + repos.config.json."""
    import config
    import repo_index
    data_dir = paths.data_dir()
    md_path = os.path.join(data_dir, "repos.md")
    terms = [t for t in (a.terms or []) if t != "show"]

    # --- Onboarding helpers (no scan) ---
    if getattr(a, "detect_roots", False):
        found = repo_index.detect_roots()
        if found:
            print("repos: detected candidate roots:")
            for p in found:
                print("  %s" % p)
            print("")
            print("Enrichment is OFF by default — listing a repo sends nothing to "
                  "Claude unless you turn it on per-repo with `repos enrich <name>`.")
            print("Confirm/adjust, then: repos --set-roots %s" % ",".join(found))
        else:
            print("repos: no candidate roots detected under your home directory.")
            print("Set them explicitly: repos --set-roots <p1,p2,...>")
        return
    if getattr(a, "set_roots", None) is not None:
        chosen = [p.strip() for p in a.set_roots.split(",") if p.strip()]
        config.set_repo_roots(chosen)
        print("repos: roots set → %s" % ", ".join(chosen))
        print("       Enrichment stays OFF until you opt in per repo "
              "(`repos enrich <name>`). Run `repos --refresh` to build the index.")
        return

    # --- Manifest toggle subcommands (no JSON editing) ---
    action = terms[0] if terms else None
    if action in ("include", "exclude", "enrich", "config"):
        _repos_manifest_action(repo_index, data_dir, action, terms)
        return

    roots = config.repo_roots()

    # --- First-run onboarding: no roots configured and no manifest yet ---
    if (not config.repo_roots_configured()
            and not os.path.exists(os.path.join(data_dir, "repos.config.json"))
            and not a.refresh and not terms and not a.json):
        found = repo_index.detect_roots()
        print("repos: first-run setup — no workspace roots configured yet.")
        print("")
        if found:
            print("Detected candidate roots:")
            for p in found:
                print("  %s" % p)
        else:
            print("No candidate roots auto-detected; you can name your own.")
        print("")
        print("Enrichment is OFF by default — listing a repo sends NOTHING to Claude "
              "unless you turn it on per-repo with `repos enrich <name>`.")
        print("")
        print("To proceed: confirm the roots above, then run "
              "`repos --set-roots <p1,p2,...>` followed by `repos --refresh`.")
        return

    repos = None
    if a.refresh:
        # Rescan + rewrite. Enrichment is OPT-IN: a model call fires ONLY for
        # `enrich:true` repos (and only when new/changed). A normal refresh sends
        # NOTHING off-machine. --no-llm (or the repo_enrich config gate) forces the
        # deterministic path; --dry-run reports what WOULD be sent without sending;
        # --re-summarize regenerates summaries even if cached.
        use_llm = config.repo_enrich_enabled() and not getattr(a, "no_llm", False)
        dry_run = getattr(a, "dry_run", False)
        egress = []
        repos = repo_index.build_index(
            roots, data_dir=data_dir, use_llm=use_llm, dry_run=dry_run,
            re_summarize=getattr(a, "re_summarize", False), egress=egress)
        if not a.quiet and not a.json:
            if dry_run:
                if egress:
                    print("repos: --dry-run — WOULD send README+tree for: %s "
                          "(nothing sent)" % ", ".join(sorted(egress)))
                else:
                    print("repos: --dry-run — no repos are enrich:true; nothing "
                          "would be sent.")
            elif egress:
                print("repos: enriching (sending README+tree NAMES): %s"
                      % ", ".join(sorted(egress)))
        if a.quiet and not terms and not a.json:
            sent = (" · sent: %s" % ", ".join(sorted(egress))) if egress else " · sent: nothing"
            print("repos: indexed %d repo(s) → %s%s" % (len(repos), md_path, sent))
            return

    if terms:
        if repos is None:
            repos = _repos_load(repo_index, roots, data_dir)
        q = " ".join(terms)
        hits = [r for r in repo_index.match(q, repos) if repo_index.score(q, r) > 0]
        if a.json:
            print(json.dumps(hits, indent=2, ensure_ascii=False))
        elif hits:
            print(repo_index.render_md(hits, query=q))
        else:
            print("No repos match %r." % q)
        return

    if a.json:
        if repos is None:
            repos = _repos_load(repo_index, roots, data_dir)
        print(json.dumps(repos, indent=2, ensure_ascii=False))
        return

    if repos is not None:
        # Just refreshed (non-quiet) → print what we wrote.
        print(repo_index.render_md(repos))
        return
    if not os.path.exists(md_path):
        repo_index.build_index(roots, data_dir=data_dir, use_llm=False)
    with open(md_path) as f:
        print(f.read())


def _open_argv(path):
    """The macOS `open` argv for `path`, honouring (in priority order) the env var
    TASK_STATION_BROWSER, then config.board_browser(): when one is set, open the board
    in that browser app — `open -a "<App>" <path>`; else `open <path>` (system default).
    Pure / testable — builds the argv list, runs nothing."""
    app = os.environ.get("TASK_STATION_BROWSER") or None
    if not app:
        try:
            import config
            app = config.board_browser()
        except Exception:
            app = None
    if app:
        return ["open", "-a", app, path]
    return ["open", path]


def _open_path(path):
    """Best-effort open of `path` in the configured browser, else the OS default app
    (macOS `open`; see _open_argv). Never raises and returns False off-darwin / on any
    failure — the board is already written, so opening is purely a convenience."""
    if sys.platform != "darwin":
        return False
    try:
        return g("subprocess").run(_open_argv(path), capture_output=True,
                                   timeout=10).returncode == 0
    except Exception:
        return False


# ================= F6 — artifact capture + cross-person auto-link =============

def cmd_capture_artifacts(a):
    """PostToolUse capture: scan a tool RESULT (stdin, or --text) for PR/work-item URLs and
    append them (deduped) to the attached task's prs[]/stories[], with ONE capped event,
    then run the cross-person auto-link. Suppressed in delegate workers (env guard); a
    no-op when the session isn't attached / nothing matches. Fail-open — never raises."""
    try:
        if os.environ.get("TASK_STATION_SUPPRESS"):
            return
        session = getattr(a, "session", None)
        if not session:
            return
        tid = get_link(session)
        if not tid or tid == SKIP_SENTINEL:
            return
        task = load_task(tid)
        if not task:
            return
        data = getattr(a, "text", None)
        if not data:
            try:
                data = sys.stdin.read()
            except Exception:
                data = ""
        import artifacts
        hits = artifacts.scan(data)
        if not hits:
            return
        changed = []
        for h in hits:
            if h["kind"] == "pr":
                if add_pr(task, h["url"]):
                    changed.append(h)
            else:
                if add_story(task, h["url"]):
                    changed.append(h)
        if changed:
            labels = ", ".join((h.get("id") or h["url"]) for h in changed[:5])
            more = "" if len(changed) <= 5 else " (+%d)" % (len(changed) - 5)
            add_event(task, "artifact",
                      "captured %d artifact(s): %s%s" % (len(changed), labels, more), session)
            task["updated_ts"] = _now()
        # Auto-link fires on ANY signal now on the task (idempotent), so a re-capture of an
        # already-stored PR still forms a newly-possible cross-person pair.
        _autolink_task_signals(task, session)
        save_task(task)
        _obsidian_sync(task)
    except Exception:
        return                                     # capture is best-effort; never disrupts


def cmd_board(a):
    """`task-station board [--open]` — write the HTML board and print its path.

    `--refresh-if-live` is the Stop-hook path: regenerate board.html ONLY when board
    auto-refresh is opted in AND the file already exists (the user has opened the
    board at least once). Best-effort and silent — never creates the file for someone
    who never uses the board, never prints, never raises. Gated (in the shared
    maybe_refresh_board helper) so the gating is unit-testable."""
    if getattr(a, "refresh_if_live", False):
        maybe_refresh_board()
        return
    out = write_board()
    print(out)
    if getattr(a, "open", False):
        _open_path(out)


def cmd_brains(a):
    """`task-station brains <action> …` — view/edit the Interbrain brains & sharing
    config in <data_dir>/brains.json (a NEW additive file; NEVER touches tasks.db).
    Actions: list · add <name> [--description/--purpose/--keywords/--repos/
    --category-affinity] · edit <name> [same flags] · rename <a> <b> · archive <name> ·
    share <brain> --with <alias|org> [--tag TAG] · unshare … · assign <task-ref> <brain>
    (manual override — pins) · suggest --task <ref> (the scoring audit table) · show
    [<name>]. This CLI is the ONLY write path for brains.json — a `file://` board page
    cannot write, so any UI can only emit these commands for a human to run."""
    import brains as _brains
    dd = paths.data_dir()
    act = (getattr(a, "action", None) or "show")
    rest = list(getattr(a, "args", None) or [])
    cfg = _brains.load(dd)
    if act == "list":
        for b in _brains.list_brains(cfg):
            sh = ", ".join((r["with"] + (":" + r["tag"] if r.get("tag") else ""))
                           for r in b["shares"]) or "—"
            print("%s%s  shares: %s" % (b["name"],
                  " (archived)" if b["archived"] else "", sh))
        return
    if act == "suggest":
        ref = getattr(a, "task", None) or (rest[0] if rest else None)
        t = resolve_ref(ref) if ref else None
        if not t:
            print("brains suggest: pass --task <ref> (the task to score)")
            return
        res = _brains.score_brains(cfg, _brain_signals(t))
        print("brains suggest — task #%s [%s] %s (threshold %d):"
              % (t.get("seq", t["id"][:8]), t["id"][:8], t.get("title") or "",
                 res["threshold"]))
        print("  %-16s %5s  %s" % ("brain", "score", "repo/keyword/category/skill"))
        for r in res["scores"]:
            bd = r["breakdown"]
            flag = " ←winner" if r["name"] == res["winner"] else ""
            arch = " (archived)" if r.get("archived") else ""
            print("  %-16s %5d  %d/%d/%d/%d%s%s"
                  % (r["name"], r["total"], bd["repo"], bd["keyword"],
                     bd["category"], bd["skill"], arch, flag))
        cur = _brains.brain_for(cfg, t["id"])
        print("  → would attach to: %s (currently: %s%s)"
              % (res["winner"], cur, ", pinned" if _brains.is_pinned(cfg, t["id"]) else ""))
        return
    if act == "show":
        views = _brain_task_views()
        want = rest[0] if rest else None
        counts = {}
        for v in views:
            br = _brains.brain_for(cfg, v["uuid"])
            counts[br] = counts.get(br, 0) + 1
        derived = {}
        for b in _brains.list_brains(cfg):
            if want and b["name"] != want:
                continue
            derived[b["name"]] = _brains.derive(cfg, b["name"], views)
        out = {"brains": ({want: cfg["brains"].get(want)} if want else cfg["brains"]),
               "assign_count": len(cfg["assign"]), "pinned_count": len(cfg.get("pinned") or {}),
               "task_counts": counts, "derived": derived}
        print(json.dumps(out, indent=2, sort_keys=True))
        return
    changed = False
    _field_kwargs = {k: getattr(a, k, None)
                     for k in ("description", "purpose", "keywords", "repos", "category_affinity")}
    if act == "add":
        changed = _brains.add(cfg, rest[0], now=_now(), **_field_kwargs) if rest else False
    elif act == "edit":
        changed = _brains.edit(cfg, rest[0], **_field_kwargs) if rest else False
    elif act == "rename":
        changed = _brains.rename(cfg, rest[0], rest[1]) if len(rest) >= 2 else False
    elif act == "archive":
        changed = _brains.archive(cfg, rest[0]) if rest else False
    elif act == "share":
        changed = _brains.share(cfg, rest[0] if rest else "",
                                getattr(a, "with_", None), getattr(a, "tag", None))
    elif act == "unshare":
        changed = _brains.unshare(cfg, rest[0] if rest else "",
                                  getattr(a, "with_", None), getattr(a, "tag", None))
    elif act == "assign":
        # Manual override — pins the assignment so the scorer respects it forever.
        if len(rest) >= 2:
            t = resolve_ref(rest[0])
            if not t:
                print("brains: no task matches %r" % rest[0])
                return
            changed = _brains.assign(cfg, t["id"], rest[1], pinned=True)
    else:
        print("brains: unknown action %r" % act)
        return
    if changed:
        _brains.save(cfg, dd)
        print("brains: %s ok" % act)
    else:
        print("brains: %s — no change" % act)


def cmd_hook_health(a):
    """`task-station hook-health [--clear]` — what the hooks failed at.

    Hooks are deliberately non-fatal, so a broken call used to be invisible;
    `hooks/_ts_lib.sh::ts_run` records each one instead. This is the human view of
    that log, and `--clear` is the acknowledgement (it also re-arms the nag)."""
    if getattr(a, "clear", False):
        n = hook_health.clear()
        print("Cleared %d hook-failure record(s) from %s."
              % (n, hook_health.log_path()))
        return
    rows = hook_health.summary()
    if not rows:
        print("No hook failures recorded (%s)." % hook_health.log_path())
        return
    print("Hook failures — newest last (%s):" % hook_health.log_path())
    print("\n".join("  " + r for r in rows))
    print("Hooks stayed non-fatal throughout. Clear with `task-station hook-health --clear`.")
