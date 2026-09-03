"""CLI seam: main() — the full argparse tree and the dispatch to each subcommand's cmd_* handler. Top layer of the split; every other board module sits below it and none may import it."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
from board.memos import *
from board.sessions import *
from board.render import *
from board.graph import *
from board.boardio import *
from board.cmds import *
from board.cliguard import LoudParser
import argparse

import decisions as _dec
from board.prose_input import annotate_prose_help, resolve_prose_args

g, set_g = _shared.g, _shared.set_g

__all__ = ["main"]


# ------------------------------------------------------------------- main ----

def main(argv=None):
    """`argv=None` reads sys.argv, exactly as before. The explicit-list form exists so
    a caller already holding this module can run a subcommand through the REAL parser
    and dispatch without paying another interpreter start-up — lib/stop_steps.py runs
    the Stop hook's seven best-effort steps that way."""
    # LoudParser, not ArgumentParser: a usage error must land on STDOUT and still
    # exit non-zero. argparse's stderr-only default made a mistyped or unwired
    # subcommand indistinguishable from a command that ran and had nothing to say —
    # see board.cliguard. Subparsers inherit the class, so every subcommand's own
    # flag errors are covered by this one line.
    p = LoudParser(prog="task-station")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("create"); sp.add_argument("--session", default=None)
    sp.add_argument("--title", required=True); sp.add_argument("--summary", default="")
    sp.add_argument("--color", default=None); sp.add_argument("--effort", default=None)
    sp.add_argument("--goal", default=None,
                    help="one line: what 'done' looks like (digest)")
    sp.add_argument("--step", action="append", default=None,
                    help="seed a checklist step (repeatable)")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-attach", dest="no_attach", action="store_true",
                    help="create unattached (empty sessions) — /todo <n> -s fresh-starts")
    sp.add_argument("--attach", action="store_true",
                    help="force-bind --session even if it's a substantive tracked session")
    sp.add_argument("--active", action="store_true",
                    help="start the task active (●) instead of the default new (○)")
    sp.set_defaults(fn=cmd_create)

    sp = sub.add_parser("attach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", required=True); sp.add_argument("--color", default=None)
    sp.add_argument("--note", default=None,
                    help="append this text to the task's activity log (fold a prompt in)")
    sp.add_argument("--force-key", dest="force_key", action="store_true",
                    help="confirm an attach whose prompt/--note identity keys "
                         "(PR/work-item #) don't match the target task's (F9 soft-guard)")
    sp.set_defaults(fn=cmd_attach)

    sp = sub.add_parser("detach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", default=None,
                    help="task to detach from (default: the session's linked task)")
    sp.set_defaults(fn=cmd_detach)

    sp = sub.add_parser("bump"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_bump)

    sp = sub.add_parser("skip"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_skip)

    sp = sub.add_parser("done"); sp.add_argument("--session", default=None)
    sp.add_argument("--task", default=None)   # close any task by seq/id from anywhere
    sp.set_defaults(fn=cmd_done)

    # HARD-delete a task. Hidden (help=SUPPRESS) — not in --help's command list,
    # the config board, or the README; lifecycle is close-not-delete (use `done`).
    # Discoverable only via `guidance`'s maintenance line. See cmd_delete.
    sp = sub.add_parser("delete", help=argparse.SUPPRESS)
    sp.add_argument("--task", required=True)
    sp.set_defaults(fn=cmd_delete)

    sp = sub.add_parser("mark-edited"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_mark_edited)   # PostToolUse(Write|Edit|NotebookEdit) one-shot reminder

    sp = sub.add_parser("touch-file"); sp.add_argument("--session", required=True)
    sp.add_argument("--file", dest="file", required=True)
    sp.set_defaults(fn=cmd_touch_file)    # PostToolUse: append an edited path to the task's briefing

    sp = sub.add_parser("board")
    sp.add_argument("--open", dest="open", action="store_true",
                    help="best-effort: open the written board.html in a browser (macOS)")
    sp.add_argument("--refresh-if-live", dest="refresh_if_live", action="store_true",
                    help="Stop-hook path: silently regen board.html only when auto-refresh is on AND the file exists")
    sp.set_defaults(fn=cmd_board)         # /todo board — ONE board; no engine choice

    sp = sub.add_parser("brains")         # Interbrain brains & sharing config (brains.json)
    sp.add_argument("action", nargs="?", default="show",
                    help="list | add | edit | rename | archive | share | unshare | "
                         "assign | suggest | show")
    sp.add_argument("args", nargs="*", help="positional args for the action")
    sp.add_argument("--with", dest="with_", default=None, help="audience for share/unshare")
    sp.add_argument("--tag", default=None, help="optional category tag scope for a share rule")
    sp.add_argument("--task", default=None, help="task ref for `suggest` (the scoring audit)")
    # Definable-brain fields (add/edit) — the auto-attach signals. List fields accept a
    # comma/space-separated value and REPLACE (an empty string clears them).
    sp.add_argument("--description", default=None, help="one-line brain description")
    sp.add_argument("--purpose", default=None, help="what the brain is for")
    sp.add_argument("--keywords", default=None, help="auto-attach keywords (comma/space list)")
    sp.add_argument("--repos", default=None, help="auto-attach repos (comma/space list)")
    sp.add_argument("--category-affinity", dest="category_affinity", default=None,
                    help="auto-attach category tags (comma/space list)")
    sp.set_defaults(fn=cmd_brains)

    # The org-setup wizard lives in the BRAIN plane and owns its own parser; the
    # board only routes to it. The flags are restated here rather than swallowed
    # as a REMAINDER because argparse cannot capture a leading `--flag` into a
    # positional — the alternative UX is `org-setup -- --scan-bundle …`, which
    # nobody types correctly the first time. Restating them is a drift risk, so
    # it is guarded: tests/brain/test_org_setup.py asserts this flag set IS the
    # wizard's flag set.
    sp = sub.add_parser("org-setup",
                        help="four read-only scans + six answers -> a valid OrgProfile")
    sp.add_argument("--scan-bundle", dest="scan_bundle", metavar="JSON",
                    help="already-fetched read-only inputs for the four scans")
    sp.add_argument("--answers", metavar="JSON",
                    help="the six leader answers as JSON; omit to be asked")
    sp.add_argument("--out", metavar="PATH", help="where to write config.json")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="validate and print the profile; write nothing")
    sp.set_defaults(fn=cmd_org_setup)

    sp = sub.add_parser("hook-health",
                        help="failures the (deliberately non-fatal) hooks recorded")
    sp.add_argument("--clear", action="store_true",
                    help="empty the log and re-arm the SessionStart nag")
    sp.set_defaults(fn=cmd_hook_health)

    # claims — bind a plan document to a task and register the commands that settle what
    # it asserts, so the plan checks itself. See cmd_claims and lib/checker.py.
    sp = sub.add_parser("claims",
                        help="bind a document to a task and register/run the commands "
                             "that verify what it claims")
    # The action is a bare positional with no argparse `choices`, matching `brains`:
    # cmd_claims validates it itself so an unknown word gets a sentence saying what the
    # two actions are, rather than argparse's usage dump and exit code 2.
    sp.add_argument("action", nargs="?", default="show",
                    help="show (default: the bound doc, the claims, the last result — "
                         "runs nothing) | verify (RUN the registered commands; exits 0 "
                         "green, 1 on a refuted claim, 3 when NOTHING RAN — so a gate "
                         "can tell a broken claim from a task that registered none)")
    sp.add_argument("--task", default=None,
                    help="task by seq/id (default: the attached task)")
    sp.add_argument("--session", default=None)
    sp.add_argument("--bind", default=None, metavar="PATH",
                    help="set/replace the document these claims are about. ABSOLUTE "
                         "path — a relative one would name a different file from every "
                         "directory. The pointer check stats it every session start; it "
                         "never opens it.")
    sp.add_argument("--unbind", action="store_true",
                    help="forget the bound document, KEEPING the registered claims "
                         "(a renamed or split plan is the common case)")
    sp.add_argument("--register", action="append", default=None,
                    metavar="'ID|CMD|EXPECTED[|EXPECTED…]'",
                    help="register one claim: an id, the shell command that settles it, "
                         "and every substring that must appear in its combined "
                         "stdout+stderr. Repeatable, and UPSERTS by id — re-registering "
                         "C1 rewrites C1 and leaves the rest alone. A literal pipe "
                         "inside the command is written `\\|`. At least one expected "
                         "substring is required: a claim asserting nothing would pass "
                         "forever. WRITE IT AS A DIRECTION, NOT A LITERAL — put a floor "
                         "or a ceiling in the COMMAND and expect its PASS token, because "
                         "`test COUNT = LITERAL` is falsified by any legitimate release. "
                         "Copy tools/checker-template.sh.")
    sp.add_argument("--replace", action="store_true",
                    help="with --register: this invocation's claims REPLACE the whole "
                         "list, instead of upserting into it")
    sp.add_argument("--remove", action="append", default=None, metavar="ID",
                    help="drop a registered claim by id (repeatable)")
    sp.add_argument("--none", default=None, metavar="REASON",
                    help="record that this task DELIBERATELY registers no claims, and "
                         "why. Turns `verify`'s exit 3 into a pass that prints the "
                         "reason. The reason is mandatory and is a sentence — it is what "
                         "the next reader gets instead of a command they can run, and "
                         "\"n/a\" teaches them nothing. Use it when the commands you "
                         "ran cannot run unattended, need a human-only step (an "
                         "interactive command, a merge, an approval), or assert only "
                         "what a permanent test already covers. Registering a claim "
                         "retracts it.")
    sp.add_argument("--id", default=None, metavar="ID",
                    help="with verify: run just this one claim")
    sp.add_argument("--timeout", type=int, default=None, metavar="SECONDS",
                    help="per-claim timeout for this run (default: the configured "
                         "checker_claim_timeout, 600s)")
    sp.set_defaults(fn=cmd_claims)

    # ---- the loop: exit conditions · the wave scan · invoke · the graded gate ----
    #
    # EXIT CONDITIONS make a checklist item settle ITSELF: a runnable command plus the
    # substrings its output must contain, in the shape `claims` already uses, so DONE is
    # computed rather than asserted. See lib/exits.py for the evidence this is built on
    # (seventeen claims stayed honest for a year; thirteen prose steps silently became
    # true and nobody noticed).

    sp = sub.add_parser("exit-add",
                        help="attach the command that settles one checklist step")
    sp.add_argument("--task", default=None, help="task by seq/id (default: the attached task)")
    sp.add_argument("--session", default=None)
    sp.add_argument("--step", type=int, required=True, metavar="N",
                    help="the 1-based step number, as `/todo <n>` prints it")
    sp.add_argument("--cmd", required=True, metavar="SHELL",
                    help="the shell command that settles this step")
    sp.add_argument("--expect", action="append", default=None, metavar="SUBSTR",
                    help="a substring that must appear in the command's combined "
                         "stdout+stderr (repeatable). AT LEAST ONE IS REQUIRED: a "
                         "condition asserting nothing would pass forever, whatever the "
                         "command printed. WRITE IT AS A DIRECTION, NOT A LITERAL — put "
                         "a floor or a ceiling in the COMMAND and expect its PASS token, "
                         "because a literal count goes red on any legitimate release. "
                         "Copy tools/checker-template.sh.")
    sp.add_argument("--merge-gated", dest="merge_gated", action="store_true",
                    help="declare that this condition READS THE MERGE TARGET (origin/main "
                         "or similar), so it cannot go green until this work lands there. "
                         "Nothing is softened — an unmet merge-gated condition is still "
                         "unmet, still a gate finding, and still blocks the release. What "
                         "changes is that the loop can say DONE PENDING MERGE instead of "
                         "reporting a finished child as unfinished. Declared rather than "
                         "inferred: the author knows at registration, and a branch probe's "
                         "usual answer is `unprobed`.")
    sp.add_argument("--force", action="store_true",
                    help="register the condition even though the self-check flagged it "
                         "(a shape that can be satisfied by something other than the "
                         "work, or a command the shell cannot parse). The problems are "
                         "printed either way — a flagged condition stored silently is "
                         "the one outcome nobody could debug.")
    sp.set_defaults(fn=cmd_exit_add)

    sp = sub.add_parser("exit-rm", help="drop a step's exit condition (the step stays)")
    sp.add_argument("--task", default=None)
    sp.add_argument("--session", default=None)
    sp.add_argument("--step", type=int, required=True, metavar="N")
    sp.set_defaults(fn=cmd_exit_rm)

    sp = sub.add_parser("exit-show",
                        help="what each step's exit condition is and how it last went "
                             "(reads only — runs nothing)")
    sp.add_argument("--task", default=None)
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_exit_show)

    sp = sub.add_parser("exit-tick",
                        help="RUN the exit conditions and tick the steps that passed")
    sp.add_argument("--task", default=None)
    sp.add_argument("--session", default=None)
    sp.add_argument("--step", type=int, default=None, metavar="N",
                    help="run just this step's condition")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="run the commands and report, but move no ticks")
    sp.add_argument("--untick", action="store_true",
                    help="also UNTICK a ticked step whose condition now fails. Off by "
                         "default: rewriting somebody's record of finished work on one "
                         "command's exit status is a bigger claim than a tick, and a "
                         "moved file or a missing binary presents identically. A "
                         "condition that did not RUN never moves a tick either way.")
    sp.add_argument("--timeout", type=int, default=None, metavar="SECONDS",
                    help="per-command timeout for this run (default: the configured "
                         "exit_command_timeout, 120s)")
    sp.add_argument("--build-wait", dest="build_wait", type=int, default=None,
                    metavar="SECONDS",
                    help="how long to wait for the MACHINE-WIDE build slot before "
                         "refusing (default: the exit_command_timeout). A suite run is "
                         "a build, and loop_builds_max caps how many run at once on "
                         "this machine; 0 asks once and refuses.")
    sp.set_defaults(fn=cmd_exit_tick)

    # scan — the ZERO-TOKEN half of the loop driver. Waves over `depends-on`, the
    # exit-condition rollup, and the stopping condition. No model, and (without --run)
    # no shell either.
    sp = sub.add_parser("scan",
                        help="compute waves over depends-on and print what is unblocked")
    sp.add_argument("--task", default=None,
                    help="plan this task's WHOLE SUBTREE — every descendant, not just "
                         "the child row (default: the attached task). A child that has "
                         "become an orchestrator itself holds no work; the startable "
                         "unit is below it, and a scan stopping at depth one would "
                         "never say so.")
    sp.add_argument("--session", default=None)
    sp.add_argument("--depth", type=int, default=None, metavar="N",
                    help="cap the subtree walk at N levels (1 = direct children only)")
    sp.add_argument("--all", dest="all", action="store_true",
                    help="plan every open/active task on the board instead")
    sp.add_argument("--run", action="store_true",
                    help="re-run every node's exit conditions first, instead of reading "
                         "the stored results. Costs whatever those commands cost.")
    sp.add_argument("--build-wait", dest="build_wait", type=int, default=None,
                    metavar="SECONDS",
                    help="with --run: how long to wait for the MACHINE-WIDE build slot "
                         "before refusing (default: the exit_command_timeout)")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="the structured report — the same object the text view renders")
    sp.set_defaults(fn=cmd_scan)

    # turn — ONE PASS OF THE LOOP: scan -> invoke -> mechanical gate -> grade -> release,
    # with the command that performs each step. The composition A4 exists for; like `scan`
    # it calls no model, runs no shell and writes nothing.
    sp = sub.add_parser("turn",
                        help="one driven pass of the loop: what to do now, in order, "
                             "with the command for each step")
    sp.add_argument("--task", default=None, metavar="ORCH",
                    help="the orchestrator whose children this turn plans (default: the "
                         "attached task)")
    sp.add_argument("--session", default=None)
    sp.add_argument("--depth", type=int, default=None, metavar="N",
                    help="cap the subtree walk at N levels (1 = direct children only)")
    sp.add_argument("--ask", default=None,
                    help="the request to put in the invoke/relaunch command it prints "
                         "(default: a `<the request>` blank — the ask is judgement)")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="the structured report — the same object the text view renders")
    sp.set_defaults(fn=cmd_turn)

    # invoke — spawn a child session ALREADY ATTACHED to its own task, so the child can
    # FETCH that task's record in one command and the ask carries the REQUEST only.
    # ATTACHMENT IS A POINTER, NOT A DELIVERY (#583): this comment used to say the hooks
    # handed the digest over. They do not — SessionStart prints the task's title and
    # status and nothing else — so the launch prompt names the read instead.
    sp = sub.add_parser("invoke",
                        help="spawn a child session pre-attached to its own task")
    sp.add_argument("--task", default=None, metavar="CHILD",
                    help="the child task to invoke (seq/id)")
    sp.add_argument("--from", dest="from_ref", default=None, metavar="ORCH",
                    help="the orchestrator task making the request (default: the "
                         "session's attached task)")
    sp.add_argument("--ask", default=None,
                    help="the REQUEST, and only the request. The child reads its own "
                         "context from its own task record at session start, so "
                         "anything here restating that context is a lossy copy of it.")
    sp.add_argument("--role", default=None,
                    help="scout | implementer | reviewer | grader — sets the child's "
                         "model and permission mode from the role table")
    sp.add_argument("--model", default=None,
                    help="override the role's model (an alias like opus/sonnet/haiku, "
                         "never a pinned id)")
    sp.add_argument("--permission-mode", dest="permission_mode", default=None,
                    help="override the role's permission mode (plan | acceptEdits | "
                         "default | bypassPermissions)")
    sp.add_argument("--effort", default=None,
                    help="override the role's effort level (low | medium | high | "
                         "xhigh | max)")
    sp.add_argument("--force", action="store_true",
                    help="launch even though the orchestrator is already at "
                         "loop_children_max. Recorded on the orchestrator as FORCED "
                         "with the numbers it overrode — a deliberate override is "
                         "sometimes right, an invisible one never is.")
    sp.add_argument("--cwd", default=None,
                    help="directory the child starts in (default: where the task's most "
                         "recent session ran)")
    sp.add_argument("--print-command", dest="print_command", action="store_true",
                    help="hand the launch to a human instead of opening a window: the "
                         "session is still pre-attached, and the trail records a MANUAL "
                         "LAUNCH rather than an invoke")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="print the command this WOULD run and write nothing at all — "
                         "no session minted, no event recorded, no workspace touched")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_invoke)

    # relay — session succession. Bare it REPORTS (occupancy, verdict, what blocks a
    # handoff) and writes nothing; --spawn hands the task to a fresh session pre-attached
    # to THE SAME record. See lib/succession.py for the two-number policy and why
    # "unknown" is not "keep going".
    sp = sub.add_parser("relay",
                        help="report this session's context pressure, and hand off to a "
                             "successor on the same task")
    sp.add_argument("--task", default=None,
                    help="task by seq/id (default: the attached task)")
    sp.add_argument("--session", default=None)
    sp.add_argument("--spawn", action="store_true",
                    help="actually hand off: mint a session pre-attached to THIS task, "
                         "launch it with the generated continuation prompt, and record "
                         "the handoff for the gate. Without it this verb only reports, "
                         "and writes nothing at all.")
    sp.add_argument("--force", action="store_true",
                    help="hand off despite a keep-going/unknown verdict or an incomplete "
                         "record. Recorded as FORCED with the gaps it overrode — a "
                         "degraded handoff is sometimes right, an invisible one never is.")
    sp.add_argument("--model", default=None,
                    help="override the successor's model (default: the predecessor's own "
                         "selection, `[1m]` marker and all)")
    sp.add_argument("--cwd", default=None,
                    help="directory the successor starts in (default: where this task's "
                         "most recent session ran)")
    sp.add_argument("--print-command", dest="print_command", action="store_true",
                    help="hand the launch to a human: the session is still pre-attached, "
                         "and the trail records a MANUAL LAUNCH rather than a window")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="the structured report — the same object the text view renders")
    sp.set_defaults(fn=cmd_relay)

    # grade — record one pass of the graded acceptance gate on a child task. The engine
    # does the arithmetic; the SKILL supplies the judgment.
    sp = sub.add_parser("grade",
                        help="grade a child's work against the six rubric dimensions")
    sp.add_argument("--task", default=None, metavar="CHILD")
    sp.add_argument("--session", default=None)
    sp.add_argument("--dim", action="append", default=None, metavar="G1=A-",
                    help="one dimension's grade (repeatable). ACCEPTANCE IS "
                         "PER-DIMENSION, not an average — an average lets a failed "
                         "gate-integrity dimension hide behind five strong ones. A "
                         "dimension left ungraded is not a pass either.")
    sp.add_argument("--threshold", default=None, metavar="GRADE",
                    help="override the configured accept threshold for this grading "
                         "(default: loop_accept_threshold, A-)")
    sp.add_argument("--handoff", type=int, default=None, metavar="N",
                    help="grade session HANDOFF N (as `relay` numbered it) instead of the "
                         "task's work — same rubric, same threshold, linked so a task "
                         "that relayed more than once keeps its verdicts separate")
    sp.add_argument("--note", default=None, help="one line of judgment, stored with the grade")
    sp.add_argument("--park", default=None, metavar="REASON",
                    help="record that this child does NOT come back to the loop: "
                         "human-gate | blocked-external | retries-exhausted. A parked "
                         "child is never retried.")
    sp.add_argument("--why", default=None,
                    help="with --park: MANDATORY. A park with no reason is "
                         "indistinguishable later from work somebody quietly dropped.")
    sp.add_argument("--no-decision", dest="no_decision", action="store_true",
                    help="do not also append the grade as a decision on the task")
    sp.add_argument("--no-memo", dest="no_memo", action="store_true",
                    help="do not send the verdict to the child as a memo. Off by "
                         "default: a rejection recorded on the task and nowhere else is "
                         "one the child never reads, because nobody types into an invoked "
                         "child again and by gate time it has usually stopped.")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(fn=cmd_grade)

    # decompose — a task holds WORK or holds CHILDREN, never both. This is the one move
    # that turns the first into the second, in one command instead of four.
    sp = sub.add_parser("decompose",
                        help="split a task into child tasks and flag it orchestrator-only")
    sp.add_argument("--task", default=None)
    sp.add_argument("--session", default=None)
    sp.add_argument("--into", action="append", default=None, metavar="TITLE",
                    help="a child task to create (repeatable, in order)")
    sp.add_argument("--chain", action="store_true",
                    help="also make each child depend-on the previous one, so the scan "
                         "releases them one wave at a time")
    sp.add_argument("--add", action="store_true",
                    help="append to children this task already has. Without it a task "
                         "that already has children is REFUSED — decomposing twice by "
                         "accident is quiet, and shows up only as duplicated work in "
                         "the scan.")
    sp.set_defaults(fn=cmd_decompose)

    # orchestrator-check — the guard delegate.py consults before it spawns anything.
    # Silent + exit 0 when delegation is allowed; the refusal + exit 3 when it is not.
    sp = sub.add_parser("orchestrator-check",
                        help="refuse (exit 3) if this task is orchestrator-only, naming "
                             "the child that should own the work")
    sp.add_argument("--task", default=None)
    sp.add_argument("--session", default=None)
    sp.add_argument("--verb", default=None,
                    help="how the caller is spelled in the refusal (default 'delegate run')")
    sp.set_defaults(fn=cmd_orchestrator_check)

    # sync — the two-machine transport. Owner-partitioned: this station writes ONE
    # directory in the exchange and reads every other, so a merge conflict between two
    # machines is structurally impossible. See lib/board/sync.py.
    sp = sub.add_parser("sync",
                        help="exchange tasks with this owner's other stations — "
                             "owner-partitioned, so no merge conflict is possible")
    sp.add_argument("--init", nargs="?", const="", default=None, metavar="DIR",
                    help="create the BACKUP exchange + this station's partition (and a "
                         "LOCAL git repo when git is available). With DIR, also records "
                         "it as `sync_dir`. Creates NO remote and contacts no network — "
                         "provisioning a remote is a deliberate human step.")
    sp.add_argument("--init-share", dest="init_share", nargs="?", const="", default=None,
                    metavar="DIR",
                    help="create a SHARE exchange instead — a chosen subset, readable "
                         "by others. A task appears there ONLY when a sharing rule on "
                         "its brain names an audience, so a fresh install shares "
                         "NOTHING. With DIR, records it as `share_dir`.")
    sp.add_argument("--dir", default=None, metavar="DIR",
                    help="use this exchange directory for THIS run, ignoring config")
    sp.add_argument("--status", action="store_true",
                    help="show the exchange, this station, and every partition present "
                         "— runs no sync")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="report what a sync WOULD do; writes nothing, stores nothing")
    sp.add_argument("--no-net", dest="no_net", action="store_true",
                    help="never pull or push, even when the exchange has a remote")
    sp.add_argument("--check", action="store_true",
                    help="ask which peer partitions have MOVED since this machine last "
                         "pulled them — reads `rev.json` in each partition, syncs "
                         "nothing, records nothing. Exits 3 when nothing changed, so a "
                         "hook or a timer can branch on it.")
    sp.add_argument("--if-changed", dest="if_changed", action="store_true",
                    help="sync only when --check says a peer moved. The cheap cadence "
                         "form: safe to run often, does nothing almost every time.")
    sp.add_argument("--preview", action="store_true",
                    help="show EXACTLY what would become visible to peers — every task, "
                         "its audience, its trail level and the field names published — "
                         "and write nothing at all")
    sp.add_argument("--confirm-share", dest="confirm_share", action="store_true",
                    help="accept a widening. A share run that would make something "
                         "NEWLY visible is HELD and prints the preview instead; "
                         "narrowing never asks, because taking something back is safe.")
    sp.set_defaults(fn=cmd_sync)

    # heal — the RECONCILE pass: turn the append-only decision log into current state.
    # Per-task by default; a DRY RUN by default. See cmd_heal and lib/heal.py.
    sp = sub.add_parser("heal",
                        help="reconcile a task's append-only decision log into current "
                             "state (dry run by default)")
    sp.add_argument("--session", default=None)
    sp.add_argument("ref", nargs="?", default=None, metavar="TASK",
                    help="the task to reconcile, named POSITIONALLY: `heal --scan 12` is "
                         "exactly `heal --scan --task 12`, resolved by the same lookup. "
                         "It exists because /heal passes $ARGUMENTS straight through, so "
                         "a bare `/heal 12` arrives here as a positional. REFUSED "
                         "alongside --all (they name different scopes) or alongside a "
                         "--task naming a DIFFERENT task; the same ref in both places is "
                         "accepted.")
    sp.add_argument("--task", default=None,
                    help="task to reconcile by seq/id (default: the attached task). May "
                         "also be given positionally — `heal --scan 12`.")
    sp.add_argument("--scan", action="store_true",
                    help="layer 1 ONLY: the deterministic scan. Zero tokens, and it "
                         "never modifies the task.")
    sp.add_argument("--apply", action="store_true",
                    help="perform the mechanical plan. Backs the task blob up first and "
                         "REFUSES if that backup cannot be written. Without this, heal "
                         "is a dry run and changes nothing. Prints ONLY what it did — "
                         "not the scan, the decision list or the judgment list, which "
                         "the dry run already showed you. An --apply that performs at "
                         "least one operation STAMPS the heal; one that performs NONE is "
                         "refused rather than stamping a reconcile that never happened "
                         "(use --mark-healed for a judgement-only pass).")
    sp.add_argument("--verbose", action="store_true",
                    help="with --apply: print the FULL block (scan, current decisions, "
                         "judgment list) as well as what was applied. Off by default "
                         "because that block is ~94%% decision text and the caller has "
                         "just read it in the dry run.")
    sp.add_argument("--mark-healed", dest="mark_healed", action="store_true",
                    help="record a JUDGEMENT-ONLY heal: the log was read and nothing "
                         "needed changing. Performs no operation, backs the blob up "
                         "first, and is the only way to say so — without it the record "
                         "still reads `last heal never` and every session opens on a "
                         "false alarm.")
    sp.add_argument("--note", default=None, metavar="WHY",
                    help="with --mark-healed: one line saying what was checked and why "
                         "nothing changed (stored on the task, shown by the scan)")
    sp.add_argument("--dispose-acks", dest="dispose_acks", default=None,
                    metavar="ID8,…|all",
                    help="retro-fill the disposition of acks recorded before one was "
                         "required (needs --apply to write). Takes memo id8s or `all` — "
                         "`all` is legitimate here, since those acking sessions no "
                         "longer exist. Pass exactly ONE of --decision/--memory/--noop. "
                         "Every retro-fill is MARKED retro with who filled it and when, "
                         "the original ack's session/timestamp are never rewritten, and "
                         "a disposition the acker chose is never overwritten.")
    sp.add_argument("--decision", nargs="?", const=True, default=None,
                    help="with --dispose-acks: the memo became a decision (optional TEXT "
                         "says which). Records the disposition only — a heal never mints "
                         "a decision dated to a session that no longer exists.")
    sp.add_argument("--memory", default=None, metavar="SLUG",
                    help="with --dispose-acks: it was folded into that agent-memory note")
    sp.add_argument("--noop", default=None, metavar="REASON",
                    help="with --dispose-acks: no durable change was needed — the reason "
                         "is MANDATORY and is recorded on the ledger")
    sp.add_argument("--all", dest="all", action="store_true",
                    help="sweep every open/active task instead of one — warns about its "
                         "scope before doing anything")
    sp.add_argument("--split", default=None, metavar="N|TASK:N",
                    help="mark decision N as SPLIT into the decisions named by --into "
                         "(add those first with `update --decision`). Takes a bare `<n>` "
                         "or the qualified `<task>:<n>` the decision list prints; a "
                         "qualified ref naming a DIFFERENT task is refused, never "
                         "resolved. NAMES THE RULING BACK, with its first sentence, "
                         "before it marks anything, and an unreadable item refuses the "
                         "WHOLE batch instead of being dropped.")
    sp.add_argument("--merge", default=None, metavar="N1,N2,…",
                    help="mark these decisions as MERGED into the one named by --into "
                         "(add that summary first with `update --decision`). Same list "
                         "grammar as --reassign: bare or qualified, all-or-nothing. A "
                         "batch with one unreadable or out-of-range item changes NOTHING "
                         "— a merge that quietly dropped a member would write a summary "
                         "claiming to replace N rulings while N-1 moved.")
    sp.add_argument("--into", default=None, metavar="N1,N2,…",
                    help="the decision(s) a --split became, or the ONE that a --merge "
                         "was absorbed into. Bare or qualified numbers; an unreadable "
                         "item refuses the whole command.")
    sp.add_argument("--reassign", default=None, metavar="N1,N2,…",
                    help="move OWNERSHIP of these decisions to the task named by --to. "
                         "The decision does NOT move: one copy, one store, at its "
                         "original index. What moves is which task renders it in full — "
                         "this task keeps a one-line REFERENCE STUB carrying the title, "
                         "the owner and the pointer. Takes a bare `<n>` or the qualified "
                         "`<task>:<n>` the decision list prints; a qualified ref naming a "
                         "DIFFERENT task is refused, never resolved. NAMES EACH RULING "
                         "BACK, with its first sentence, before it moves anything. "
                         "REFUSED for a PINNED decision (a pin briefs every session, so a "
                         "ruling that binds the programme belongs to the programme), for "
                         "a decision with no text to reference (a reassign leaving no "
                         "stub is a delete with extra steps), for one already owned "
                         "elsewhere, and — unless the source task is CLOSED and the "
                         "acting --session is attached to its PARENT — when that session "
                         "is not attached to the SOURCE task, so a child cannot claim "
                         "rulings it does not own. Prints the one command that undoes it.")
    sp.add_argument("--to", dest="to", default=None, metavar="TASK",
                    help="with --reassign: the task that will OWN (render in full) those "
                         "decisions, by seq/id/handle")
    sp.add_argument("--stub", default=None, metavar="TEXT",
                    help="with --reassign: the one-line reference this task renders in "
                         "place of the prose. Defaults to the decision's first sentence; "
                         "pass it when that sentence is not the subject.")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="with --reassign/--unassign/--split/--merge: NAME what would be "
                         "marked and mark nothing. The batch is validated exactly as the "
                         "real run validates it, so a refusal here is the refusal you "
                         "would get. (All four verbs write without --apply — the close "
                         "report that names `--reassign` has to be true as printed — so "
                         "this is how you look before you leap.)")
    sp.add_argument("--unassign", default=None, metavar="N1,N2,…",
                    help="the ONE inverse of --reassign: bring these decisions' ownership "
                         "back to the task that holds them, which renders them in full "
                         "again and drops the stub.")
    sp.add_argument("--dismiss", action="append", default=None, metavar="CHECK:REF",
                    help="adjudicate ONE finding away (repeatable; needs --apply and "
                         "--why). It leaves the findings, the issue count and the due "
                         "calculus. The ruling covers that finding's EXACT text, so editing "
                         "the entry it names makes the finding re-report — a dismissal "
                         "adjudicates one state, never a category. Nothing is deleted.")
    sp.add_argument("--undismiss", action="append", default=None, metavar="CHECK:REF",
                    help="retire a dismissal and restore full reporting (repeatable; needs "
                         "--apply). The ledger entry stays, marked retired.")
    sp.add_argument("--why", default=None, metavar="REASON",
                    help="with --dismiss: MANDATORY. Why that finding is not a defect. A "
                         "dismissal with no reason is indistinguishable later from a "
                         "finding somebody buried, so one without this is refused.")
    sp.add_argument("--dismissals", action="store_true",
                    help="list the adjudication ledger — every dismissal with its why, its "
                         "date, and whether it is still silencing anything (an ACTIVE ruling "
                         "whose text has since changed reads EXPIRED). Read-only.")
    sp.add_argument("--candidates", action="store_true",
                    help="the CHEAP merge-only dry run: the goal line, the pinned "
                         "decisions, and each candidate group's members IN FULL — and "
                         "nothing else. The full dry run is ~94%% decision corpus; this is "
                         "the same reading with the corpus removed. Read-only.")
    sp.add_argument("--goal-reviewed", dest="goal_reviewed", action="store_true",
                    help="record that the GOAL LINE was re-read and is still true, resetting "
                         "the goal-review count WITHOUT rewriting it. The only thing that "
                         "resets it: --mark-healed deliberately does not, because a stamp "
                         "saying the record was read is not one saying this line was ruled "
                         "on. May be combined with --mark-healed.")
    sp.add_argument("--probe-links", dest="probe_links", action="store_true",
                    help="opt in to ONE unauthenticated HTTP HEAD per stored PR/story link. "
                         "Off by default (a session start must cost no network). Only an "
                         "explicit 404/410 counts as dead; every other answer, including any "
                         "error, stays UNKNOWN and is never reported.")
    sp.add_argument("--probe-ado", dest="probe_ado", action="store_true",
                    help="opt in to RECONCILING THIS TASK AGAINST THE WORK ITEMS IT "
                         "CLAIMS — reads each stored story's real AcceptanceCriteria and "
                         "Description (plus its parent Feature's children) and reports "
                         "criteria no decision acknowledges, criteria the log words "
                         "differently, descriptions that miss the source, and Feature "
                         "children absent from the task. Off by default: it is several "
                         "authenticated round trips per work item. Without it those five "
                         "checks report `not probed`, never `clean`.")
    sp.set_defaults(fn=cmd_heal)

    # THE ONE SANCTIONED WAY TO OPEN A NEW TERMINAL WINDOW. It exists because the
    # alternative is a session hand-writing `osascript -e 'tell application
    # "Terminal"'` while it is sitting in iTerm — which happened, opened a window
    # nobody could see, and reported success.
    sp = sub.add_parser("terminal", help="identify the host terminal, or open a new "
                                         "window in it")
    sp.add_argument("--open", dest="open_cmd", metavar="CMD", default=None,
                    help="open a NEW window in THIS terminal running CMD. Refuses "
                         "(and prints CMD) on a terminal it cannot drive rather than "
                         "opening one somewhere you are not looking.")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="emit the resolution as JSON")
    sp.set_defaults(fn=cmd_terminal)

    sp = sub.add_parser("stop-gate"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_stop_gate)     # Stop hook: block ending an untracked edit session

    sp = sub.add_parser("post-compact"); sp.add_argument("--session", required=True)
    sp.add_argument("--trigger", default="")
    sp.set_defaults(fn=cmd_post_compact)  # PostCompact hook: stash the compaction summary to history (stdin)

    sp = sub.add_parser("stop-nudge"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_stop_nudge)    # Stop hook: non-blocking staleness nudge (opt-in auto-checkpoint)

    # timing — the work-boundary scheduler's verdict. A READ: it writes nothing at all, so
    # the policy can be inspected before `--boundary-maintenance` is allowed to act on it.
    sp = sub.add_parser("timing",
                        help="is this a work boundary, what is owed, and what would the "
                             "AUTO maintenance class do about it")
    sp.add_argument("--task", default=None,
                    help="task by seq/id (default: the attached task)")
    sp.add_argument("--session", default=None)
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="the structured verdict — the same object the text view renders")
    sp.set_defaults(fn=cmd_timing)

    # window — WHICH CONTEXT WINDOW this session is measured against, and which source said
    # so. A bare integer cannot be questioned, which is how a 5x-wrong denominator survived
    # for months; this prints its provenance and names a divergence out loud.
    sp = sub.add_parser("window",
                        help="the context window this session is measured against, and "
                             "which source supplied it")
    sp.add_argument("--session", default=None)
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(fn=cmd_window)

    sp = sub.add_parser("render"); sp.add_argument("--session", required=True)
    sp.add_argument("--arg", default="")
    sp.add_argument("--format", choices=["ascii", "md"], default="ascii",
                    help="list output format: ascii (default) or md (GitHub tables, printed verbatim)")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("add-project"); sp.add_argument("--task", required=True)
    sp.add_argument("--project", required=True); sp.set_defaults(fn=cmd_add_project)

    # search — ranked cross-task FTS search (tier-1 hit list) + --detail digest.
    sp = sub.add_parser("search")
    sp.add_argument("terms", nargs="*", help="terms to search task text for")
    sp.add_argument("--session", default=None)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--open", dest="open", action="store_true",
                   help="only open + active tasks")
    g.add_argument("--closed", dest="closed", action="store_true",
                   help="only closed tasks")
    g.add_argument("--all", dest="all", action="store_true",
                   help="all tasks (the default)")
    sp.add_argument("--detail", default=None,
                    help="print one task's full digest (read-only) by seq/id instead of searching")
    sp.set_defaults(fn=cmd_search)

    # history — the on-demand FULL trail for one task: every decision (including the
    # replaced ones, marked and NUMBERED), the retired steps, the dated milestone log,
    # memos, activity, worker provenance. The same view `/todo <n> history` renders, but
    # reachable without a session id — which is what a reader who just wants to read has.
    # It was advertised in ten places and wired to nothing; `history --task 444` printed
    # zero bytes and exited 2. See cmds/view.py cmd_history for the exit codes.
    sp = sub.add_parser("history",
                        help="the complete trail for one task (read-only): decisions "
                             "numbered, replaced ones marked, log + memos + activity")
    sp.add_argument("ref", nargs="?", default=None,
                    help="the task (seq/id) — same as --task, whichever you type first")
    sp.add_argument("--task", default=None, help="the task to show (seq/id)")
    sp.add_argument("--session", default=None,
                    help="acting session; with no ref its attached task is used")
    sp.set_defaults(fn=cmd_history)

    # add-cost — accumulate a delegate run's worker cost onto a task (called by
    # delegate.py so per-run cost lands on the linked /todo task, not just workers.json).
    sp = sub.add_parser("add-cost"); sp.add_argument("--task", required=True)
    sp.add_argument("--usd", required=True, help="this run's total_cost_usd")
    # Optional per-run detail — when any is given, a record is appended to task['runs'].
    sp.add_argument("--model", default=None, help="model id this run used (e.g. claude-opus-4-8)")
    sp.add_argument("--session", default=None, help="worker session id for this run")
    sp.add_argument("--seq-label", dest="seq_label", default=None,
                    help="concurrent-worker label discriminator for this run")
    sp.add_argument("--usage-json", dest="usage_json", default=None,
                    help='JSON token usage {in,out,cache_read,cache_creation}')
    sp.add_argument("--category", default="real", choices=["real", "wasted"],
                    help="real (successful run, default) | wasted (crashed/timed-out spend)")
    sp.set_defaults(fn=cmd_add_cost)

    # add-event — append one entry to a task's bounded event feed (delta-brief source).
    # Quiet bookkeeping called by delegate.py so worker/child milestones land on the
    # linked /todo task (no attach, no activity-log entry, like add-cost).
    sp = sub.add_parser("add-event"); sp.add_argument("--task", required=True)
    sp.add_argument("--kind", required=True,
                    help="event kind: log|decision|milestone|summary|status|run|worker|child")
    sp.add_argument("--text", default="",
                    help="event text (truncated to %d chars)" % EVENT_TEXT_MAX)
    sp.add_argument("--session", default=None, help="session id to attribute the event to")
    sp.set_defaults(fn=cmd_add_event)

    # add-ledger — append a hub<->worker interaction to a task's provenance ledger
    # (unbounded append-only; delegate.py posts spawn/resume/stop/adopt/finish/crash).
    sp = sub.add_parser("add-ledger", help="append a hub<->worker interaction to a task's provenance ledger")
    sp.add_argument("--task", required=True)
    sp.add_argument("--action", required=True,
                    choices=["spawn", "resume", "iterate", "modify", "stop",
                             "adopt", "finish", "crash", "timeout", "stalled"])
    sp.add_argument("--worker", default=None, help="worker session uuid")
    sp.add_argument("--session", default=None, help="acting HUB session uuid")
    sp.add_argument("--detail", default=None)
    sp.set_defaults(fn=cmd_add_ledger)

    # register-worker-session — roster a worker session on a task record (name/model/
    # harness/status). Quiet bookkeeping; delegate.py posts it on spawn + terminal.
    sp = sub.add_parser("register-worker-session",
                        help="roster a worker session on a task record (#463)")
    sp.add_argument("--task", required=True)
    sp.add_argument("--session", required=True, help="worker session uuid")
    sp.add_argument("--name", default=None, help="worker display slug")
    sp.add_argument("--model", default=None)
    sp.add_argument("--harness", default="claude")
    sp.add_argument("--status", default="running")
    sp.set_defaults(fn=cmd_register_worker)

    # memo — hand a fact/decision to a task's working session(s). One subcommand
    # (send|ack|show); a shared, visible ack ledger lets multiple sessions on one task
    # coordinate without double-implementing. --task accepts any seq/id-prefix.
    sp = sub.add_parser("memo")
    sp.add_argument("sub", choices=["send", "ack", "show"], help="memo action")
    sp.add_argument("--task", default=None,
                    help="target task (seq or id-prefix); ack/show default to the "
                         "session's attached task")
    sp.add_argument("--text", default="", help="memo body (send)")
    sp.add_argument("--id", default=None, help="memo id-prefix (ack/show)")
    sp.add_argument("--session", default=None,
                    help="acting session id (signs a send; REQUIRED to ack)")
    sp.add_argument("--corrects", action="append", default=None, metavar="TARGET",
                    help="on send: declare what this memo REPLACES (repeatable) — a "
                         "memory-note slug, `decision:<n>` on the target task, or another "
                         "memo's id8. A memo that declares corrections cannot be acked "
                         "without a disposition that engages them.")
    # An ack must carry EXACTLY ONE disposition — a bare ack is an error. An ack is a
    # receipt; treating it as an integration is how a correction never lands.
    sp.add_argument("--decision", nargs="?", const=True, default=None,
                    help="ack disposition: promote the memo to a decision (optional TEXT "
                         "overrides the memo body)")
    sp.add_argument("--memory", default=None, metavar="SLUG",
                    help="ack disposition: record that it was folded into that "
                         "agent-memory note")
    sp.add_argument("--noop", default=None, metavar="REASON",
                    help="ack disposition: no durable change needed — the reason is "
                         "MANDATORY and is recorded on the ledger")
    sp.set_defaults(fn=cmd_memo)

    # channel — THE CHILD CONTROL CHANNEL (A5). A memo is recorded; an order is
    # DELIVERED, at the receiving session's next turn boundary, because that is the one
    # moment a running session reaches without a human typing. lib/board/channel.py holds
    # the reasoning, including why the permission boundary is enforced here rather than
    # in the receiver's conscience.
    sp = sub.add_parser("channel",
                        help="reach a RUNNING session on a task (orders, stand-down, "
                             "and the denial that may never be laundered)")
    sp.add_argument("sub", choices=["reach", "orders", "stand-down", "settle", "deny"],
                    help="reach (who is running) | orders (the queue) | stand-down "
                         "(wrap up + hand back) | settle (answer one) | deny (record a "
                         "refusal this session was handed)")
    sp.add_argument("--task", default=None,
                    help="the task whose channel this is (seq/id; default: the session's "
                         "attached task)")
    sp.add_argument("--session", default=None,
                    help="the acting session id — REQUIRED to settle (an order is "
                         "addressed to a session) and to deny")
    sp.add_argument("--id", default=None, metavar="ID8",
                    help="order id-prefix (settle)")
    sp.add_argument("--why", default=None,
                    help="stand-down: why, in one line. The child reads it verbatim.")
    sp.add_argument("--report", default=None, metavar="TEXT",
                    help="settle: what this session wrote — MANDATORY for a stand-down, "
                         "and sent back to whoever ordered it")
    sp.add_argument("--action", default=None, metavar="TEXT",
                    help="deny: the action this session was refused, as it was attempted")
    sp.add_argument("--by", default=None, metavar="WHO",
                    help="deny: who refused it (e.g. 'permission classifier')")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="orders: the raw records")
    sp.set_defaults(fn=cmd_channel)

    # pickup — THE CHANNEL POINTING THE OTHER WAY. An order reaches DOWN to a running
    # child; a pickup is a CHILD reaching UP, and it is a record on the PARENT's task
    # rather than an order addressed to a session — a child usually finishes while its
    # parent is between sessions, and an order queued to nobody is a fact never recorded.
    # The parent's Stop gate refuses to end a turn while one is waiting, which is the
    # whole signal: before it, an orchestrator's only move was to poll `sessions --task
    # <child>`, which says "busy" about a child that finished an hour ago.
    sp = sub.add_parser("pickup",
                        help="children of this task that handed work back and nobody "
                             "has picked up (the parent's half of the channel)")
    sp.add_argument("sub", choices=["list", "take"],
                    help="list (what is waiting, and how to read each report) | take "
                         "(this one is dealt with — stop holding the turn for it)")
    sp.add_argument("--task", default=None,
                    help="the PARENT task (seq/id; default: the session's attached task)")
    sp.add_argument("--session", default=None,
                    help="the acting session id — recorded as who took it")
    sp.add_argument("--id", default=None, metavar="ID8",
                    help="pickup id-prefix (take)")
    sp.add_argument("--all", action="store_true",
                    help="list: include the ones already taken · take: take every one "
                         "waiting")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="list: the raw records")
    sp.set_defaults(fn=cmd_pickup)

    # F5 correspondence: link a peer pair · fork a peer node into my own task ·
    # subscribe to a peer's feed (mints memos when it advances).
    sp = sub.add_parser("link")
    sp.add_argument("--task", required=True, help="my task (seq or id-prefix)")
    sp.add_argument("--peer", required=True, help="peer task ref <alias>-<n|uuid8>")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_link)

    sp = sub.add_parser("fork")
    sp.add_argument("--from", dest="from_ref", required=True,
                    help="peer task ref to fork <alias>-<n|uuid8>")
    sp.add_argument("--title", default=None, help="title for my forked task (default: peer's)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_fork)

    sp = sub.add_parser("subscribe")
    sp.add_argument("--task", required=True, help="my task (seq or id-prefix)")
    sp.add_argument("--peer", required=True, help="peer task ref <alias>-<n|uuid8>")
    sp.add_argument("--on", dest="on", default="checkpoint,decision,trail",
                    help="event kinds to watch (comma list: checkpoint,decision,trail)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_subscribe)

    sp = sub.add_parser("subscriptions")
    sp.add_argument("sub", nargs="?", default="check",
                    help="check (diff peer feeds, mint memos) | list")
    sp.add_argument("--throttle", action="store_true",
                    help="hook path: self-throttle + stay silent (skip if run recently)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_subscriptions)

    # F6 PostToolUse artifact capture — scans a tool RESULT (stdin) for PR/work-item URLs.
    sp = sub.add_parser("capture-artifacts")
    sp.add_argument("--session", required=True)
    sp.add_argument("--text", default=None,
                    help="text to scan (default: read the tool result from stdin)")
    sp.set_defaults(fn=cmd_capture_artifacts)

    sp = sub.add_parser("status"); sp.add_argument("--task", required=True)
    sp.add_argument("value", nargs="?", default=None,
                    help="new|active to set (new = the stored open); omit to report the "
                         "current status (close via /done)")
    sp.add_argument("--session", default=None, help="session id to attribute the transition to")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("session-title"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_title)

    sp = sub.add_parser("whoami"); sp.add_argument("--session", required=True)
    sp.add_argument("--porcelain", action="store_true",
                    help="print only the attached task's seq (empty if none) for scripts")
    sp.add_argument("--statusline", action="store_true",
                    help="print a colored '#seq <dot> [TAG] title' status-bar segment (empty if no task)")
    sp.add_argument("--width", type=int, default=0,
                    help="with --statusline, truncate the title so the segment fits N columns (0 = no limit)")
    sp.set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("update"); sp.add_argument("--task", required=True)
    sp.add_argument("--title", default=None); sp.add_argument("--summary", default=None)
    sp.add_argument("--append-summary", dest="append_summary", default=None)
    sp.add_argument("--restore-summary", dest="restore_summary", nargs="?", const="",
                    default=None, metavar="N",
                    help="bring back a PRESERVED previous summary — bare restores the "
                         "most recent, `<n>` an older one (1-based, as numbered by "
                         "`/todo <n> history`). `--summary` replaces wholesale, so the "
                         "text it overwrites is kept append-only; this is the inverse "
                         "that makes the replace safe. The restore is itself reversible: "
                         "the text it replaces is preserved too, and nothing is deleted.")
    sp.add_argument("--state", default=None,
                    help="set the briefing's 'where it stands / next step' line "
                         "(model-curated; '' clears it)")
    sp.add_argument("--goal", default=None,
                    help="one line: what 'done' looks like ('' clears it)")
    sp.add_argument("--step-add", dest="step_add", action="append", default=None,
                    help="append a checklist step (repeatable)")
    sp.add_argument("--step-done", dest="step_done", action="append", type=int, default=None,
                    metavar="N", help="tick step N (1-based; repeatable)")
    sp.add_argument("--step-undone", dest="step_undone", action="append", type=int, default=None,
                    metavar="N", help="untick step N (1-based; repeatable)")
    sp.add_argument("--step-supersede", dest="step_supersede", action="append", type=int,
                    default=None, metavar="N",
                    help="retire STALE step N from the checklist (1-based; repeatable). "
                         "The checklist's one reconcile verb, shaped like --supersedes: "
                         "non-destructive, so the step keeps its text in `/todo <n> "
                         "history` marked with what replaced it, and it counts in NEITHER "
                         "side of the n/m progress number. A --step-add in the same "
                         "update is recorded as the replacement. There is deliberately no "
                         "--step-edit: supersede the stale step and add a corrected one.")
    sp.add_argument("--step-restore", dest="step_restore", action="append", type=int,
                    default=None, metavar="N",
                    help="UNDO --step-supersede on step N (1-based; repeatable) — it "
                         "returns to the active checklist with its text and tick intact")
    sp.add_argument("--decision", action="append", default=None,
                    help="append a decision note (repeatable, append-only). Every "
                         "still-current decision renders in the digest — there is no age "
                         "or count limit. Past %d chars you get an advisory suggesting "
                         "`heal --split`; it never refuses, the entry is stored in full."
                         % _dec.LONG_DECISION_CHARS)
    sp.add_argument("--supersedes", action="append", default=None, metavar="N|TASK:N",
                    help="mark decision N (1-based, as numbered by `/todo <n> history`) as "
                         "REPLACED by the --decision in this same update; repeatable, so one "
                         "decision may replace several. A superseded decision vanishes from "
                         "the default digest and every other present-tense surface, and "
                         "survives only in `history`, marked with its replacement. "
                         "`<task>:<n>` supersedes a ruling ON ANOTHER TASK — a child "
                         "refuting a parent's. Decision numbers are PER-TASK, so a bare "
                         "number always means this task and anything aimed elsewhere must "
                         "say where. Both directions are written or neither: the other "
                         "task learns what refuted it, and this decision records what it "
                         "refuted.")
    sp.add_argument("--pin", action="store_true", default=False,
                    help="pin the --decision in this same update. A pin is READING ORDER, "
                         "not visibility: every still-current decision renders in the "
                         "digest anyway, and pinned ones sort FIRST (marked ★) as the "
                         "architecture spine, ahead of everything else oldest-first. No "
                         "limit on how many are pinned.")
    # DECLARATION — what a decision IS and what it is ABOUT, written by its author and
    # never inferred. Shaped on the pin primitive to the letter, including its three
    # flags: one that ATTACHES to the --decision in this same update, one that targets an
    # EXISTING entry by number, and one that undoes it. NOTE: `add-event --kind` is a
    # DIFFERENT flag on a different subcommand with its own vocabulary (log|decision|
    # milestone|…) — the two are unrelated and neither reads the other's values.
    sp.add_argument("--kind", default=None, choices=list(_dec.KINDS),
                    help="declare what the --decision IN THIS SAME UPDATE is: %s. "
                         "ATTACHES TO THE LAST --decision of this call, exactly as --pin "
                         "does — so a call passing two --decision flags and one --kind "
                         "types the SECOND one. Pass one --kind per update, or the "
                         "declaration lands on a decision you did not mean. Optional and "
                         "stays optional: an undeclared decision is valid forever."
                         % ", ".join(_dec.KINDS))
    sp.add_argument("--subject", action="append", default=None, metavar="TYPE:VALUE",
                    help="declare what the --decision IN THIS SAME UPDATE is ABOUT "
                         "(repeatable). ATTACHES TO THE LAST --decision of this call, as "
                         "--kind and --pin do. Every ref is QUALIFIED — %s — and a bare "
                         "number is refused; `pr` and `story` also carry their repo, as "
                         "`pr:<repo>#<n>`. All the refs in one update are one subject, "
                         "and a single bad ref refuses the whole set rather than storing "
                         "half of it." % "/".join(_dec.SUBJECT_TYPES))
    sp.add_argument("--kind-decision", dest="kind_decision", action="append",
                    default=None, metavar="N=KIND",
                    help="declare the kind of EXISTING decision N (1-based; repeatable), "
                         "e.g. `--kind-decision 7=ruling`. The hand-classification path, "
                         "ONE ENTRY AT A TIME with a human-named value — there is no "
                         "batch backfill and there will not be one. Re-declaring is "
                         "allowed: the author is the only correction mechanism the design "
                         "permits, so it stays a single command.")
    sp.add_argument("--subject-decision", dest="subject_decision", action="append",
                    default=None, metavar="N=REF[,REF]",
                    help="declare the subject of EXISTING decision N (1-based; "
                         "repeatable), e.g. `--subject-decision 7=task:596,pr:repo#43`. "
                         "REPLACES whatever that entry declared — a subject is the whole "
                         "answer to what a decision is about, not a growing pile.")
    sp.add_argument("--clear-kind", dest="clear_kind", action="append", type=int,
                    default=None, metavar="N",
                    help="retract the kind declaration on decision N (1-based; "
                         "repeatable) — the single-command inverse of --kind. Errors on "
                         "an entry that declares none, rather than reporting a success "
                         "that retracted nothing.")
    sp.add_argument("--clear-subject", dest="clear_subject", action="append", type=int,
                    default=None, metavar="N",
                    help="retract the declared subject of decision N (1-based; "
                         "repeatable) — the single-command inverse of --subject")
    sp.add_argument("--pin-decision", dest="pin_decision", action="append", type=int,
                    default=None, metavar="N",
                    help="pin EXISTING decision N (1-based; repeatable) — sorts it into "
                         "the digest's leading spine block")
    sp.add_argument("--unpin-decision", dest="unpin_decision", action="append", type=int,
                    default=None, metavar="N",
                    help="unpin existing decision N (1-based; repeatable) — it returns to "
                         "the oldest-first narrative block; it does NOT stop rendering")
    sp.add_argument("--restore-decision", dest="restore_decision", action="append",
                    type=int, default=None, metavar="N",
                    help="UNDO the reconcile mark on decision N (1-based; repeatable): "
                         "clears a supersede, split or merge and returns it to the "
                         "default digest. The inverse of all three verbs — nothing was "
                         "ever deleted, so any heal is reversible.")
    sp.add_argument("--log", action="append", default=None,
                    help="append a dated milestone/finding to the task's history "
                         "(repeatable, append-only). Off the default resume path — "
                         "surfaced only by `/todo <n> history`.")
    sp.add_argument("--pr", action="append", default=None,
                    help="store a PR URL on the task (repeatable, upsert by url)")
    sp.add_argument("--pr-desc", dest="pr_desc", default=None,
                    help="description for the --pr url in this update "
                         "(or the most-recent stored pr when no --pr is given)")
    sp.add_argument("--story", action="append", default=None,
                    help="store a story/work-item URL on the task (repeatable, upsert by url)")
    sp.add_argument("--story-desc", dest="story_desc", default=None,
                    help="description for the --story url in this update "
                         "(or the most-recent stored story when no --story is given)")
    # Reconciling the repos a task NAMES. `projects` had only `add-project` (machine-called
    # by delegate) so it was append-only, and since 3.15.0 an unresolvable name keeps the
    # cited-commit check UNKNOWN for the life of the task. These two are the way out.
    sp.add_argument("--project-rm", dest="project_rm", action="append", default=None,
                    metavar="NAME",
                    help="drop NAME from the repos this task names (repeatable) — the repo "
                         "is GONE. Use this to clear an unresolvable name that `heal` "
                         "reports as `stale-project`; the cited-commit check resumes once "
                         "every remaining name has a local clone.")
    sp.add_argument("--project-rename", dest="project_rename", action="append",
                    default=None, metavar="OLD=NEW",
                    help="the repo is STILL HERE under a new name (repeatable). Renames in "
                         "place, keeping the task pointed at the work rather than "
                         "forgetting it, and COLLAPSES onto NEW if the task already names "
                         "it — one repo under two identities is one repo. Both halves are "
                         "required; use --project-rm to drop a name outright.")
    sp.add_argument("--color", default=None); sp.add_argument("--effort", default=None)
    sp.add_argument("--orchestrator", default=None, choices=["on", "off"],
                    help="flag this task ORCHESTRATOR-ONLY: it plans and grades, it does "
                         "not hold work. `delegate run --seq <this>` then REFUSES and "
                         "names the child that should own the work instead. Explicit "
                         "rather than inferred from having children — plenty of parents "
                         "legitimately hold their own work, and a guard that fires on "
                         "every parent gets switched off.")
    sp.add_argument("--trail-visibility", dest="trail_visibility", default=None,
                    choices=["private", "checkpoints", "full"],
                    help="F5: how much of this task's trail its feed exports — private "
                         "(default, trails never leave), checkpoints (digest only), full "
                         "(include the prompt/response trail)")
    sp.add_argument("--relate", action="append", default=None,
                    help="record a relation edge to another task by seq/id (repeatable, "
                         "idempotent). The related task's event feed hears about it too.")
    # The TYPED edge flags. One rule covers all of them: the SUBORDINATE side stores
    # the edge — the dependent, the child, the absorbed task — so every one of these
    # writes on the task being updated, and the reverse direction is derived.
    sp.add_argument("--depends-on", dest="depends_on", action="append", default=None,
                    metavar="TASK",
                    help="THIS task depends on TASK — TASK must land first (repeatable, "
                         "idempotent). Stored on the dependent, which is this task. "
                         "There is no --blocks: that is this edge read backwards, and "
                         "reverse edges are always derived, never stored. Local tasks "
                         "only. A cycle warns and still stores.")
    sp.add_argument("--parent", default=None, metavar="TASK",
                    help="TASK is THIS task's parent — at most ONE, because a task under "
                         "two parents double-counts in every roll-up. Writing a second "
                         "one REPLACES the first and says which it replaced. Stored on "
                         "the child, which is this task. Local tasks only.")
    sp.add_argument("--absorbed-by", dest="absorbed_by", default=None, metavar="TASK",
                    help="THIS task's work became part of TASK, so THIS task CLOSES. "
                         "Absorbing inherits work, so it prints a reconcile handoff for "
                         "TASK — steps are never merged automatically, and children are "
                         "never moved. (Compare --replaces, which closes the OTHER task.)")
    sp.add_argument("--replaces", action="append", default=None, metavar="TASK",
                    help="THIS task replaces TASK, so TASK CLOSES — its approach was "
                         "dropped, not absorbed, so nothing is inherited and no reconcile "
                         "is needed (repeatable). Note the direction: --replaces closes "
                         "the OTHER task, --absorbed-by closes THIS one. Spelled "
                         "`replaces`, not `supersedes`, because --supersedes already "
                         "retires a DECISION and both are valid in one command.")
    sp.add_argument("--duplicates", action="append", default=None, metavar="TASK",
                    help="THIS task and TASK are the same work (repeatable). Symmetric — "
                         "either side may declare it, it is stored once, and the reverse "
                         "reads the same. Closes nothing and decides nothing: it makes "
                         "duplication a warning instead of something someone must notice.")
    sp.add_argument("--unrelate", action="append", default=None, metavar="TASK",
                    help="remove EVERY edge this task stores to TASK, whatever the kind "
                         "(repeatable). An edge states present structure, not a "
                         "historical belief, so it is corrected rather than superseded. "
                         "Removing nothing is reported, not an error. Only touches this "
                         "task's own edges — a derived reverse edge belongs to the task "
                         "that stored it.")
    sp.add_argument("--session", default=None,
                    help="session id to attribute --relate / --summary events to (optional)")
    sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("pin"); sp.add_argument("--task", required=False, default=None)
    sp.add_argument("--session", default=None)
    sp.add_argument("--new", action="store_true",
                    help="pin a freshly-minted unborn session (claude --session-id <uuid>)")
    sp.set_defaults(fn=cmd_pin)

    sp = sub.add_parser("unpin"); sp.add_argument("--task", required=False, default=None)
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_unpin)

    sp = sub.add_parser("prompt-tint"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_tint)

    sp = sub.add_parser("session-tint"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_tint)

    sp = sub.add_parser("prompt-title"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_title)

    sp = sub.add_parser("prompt-context"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_prompt_context)

    sp = sub.add_parser("native")
    sp.set_defaults(fn=cmd_native)        # read-only listing of Claude Code's native task lists

    sp = sub.add_parser("adopt")
    sp.add_argument("--native", required=True,
                    help="native task ref <list-prefix>:<id> to promote into a durable station task")
    sp.set_defaults(fn=cmd_adopt)

    sp = sub.add_parser("guidance")
    sp.set_defaults(fn=cmd_guidance)

    sp = sub.add_parser("session-start"); sp.add_argument("--session", required=True)
    sp.add_argument("--source", default=""); sp.set_defaults(fn=cmd_session_start)

    # sweep-orphans — stop background workers whose spawning hub session is gone.
    # Called from the SessionStart hook; logs each reap to stderr, always exits 0.
    sp = sub.add_parser("sweep-orphans",
                        help="reap task-station workers whose spawning hub is gone")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_sweep_orphans)

    # session-end — the SessionEnd hook's exact pass (roster row + feed + reap this
    # session's own workers). Idempotent, always exits 0; the SessionStart sweep above
    # stays as the crash backstop.
    sp = sub.add_parser("session-end",
                        help="record a clean session end and stop the workers it spawned")
    sp.add_argument("--session", required=True)
    sp.add_argument("--reason", default="other",
                    help="why the session ended (clear|resume|logout|prompt_input_exit|"
                         "bypass_permissions_disabled|other); an unknown value is kept verbatim")
    sp.set_defaults(fn=cmd_session_end)

    # config-change — the ConfigChange hook's path validator. Exit 2 (BLOCK) only in
    # enforce mode; the hook-health record is written first either way.
    sp = sub.add_parser("config-change",
                        help="report config-declared paths that no longer resolve")
    sp.add_argument("--session", default=None)
    sp.add_argument("--source", default="",
                    help="user_settings | project_settings | local_settings")
    sp.add_argument("--file", dest="file", default=None,
                    help="the config file that changed")
    sp.set_defaults(fn=cmd_config_change)

    # file-changed — the FileChanged hook. Acts ONLY on files inside the data dir
    # (the manifest matcher is basename-level); re-arms the checker gate.
    sp = sub.add_parser("file-changed",
                        help="re-arm the checker gate when a station config file changes")
    sp.add_argument("--session", default=None)
    sp.add_argument("--file", dest="file", default=None)
    sp.add_argument("--change", dest="change", default="",
                    help="modified | created | deleted")
    sp.set_defaults(fn=cmd_file_changed)

    # worktree-create — the OPT-IN WorktreeCreate hook (installed into the user's own
    # settings.json by `config --worktree-hook on`, never in the plugin manifest).
    # Payload on stdin; the worktree's absolute path is the first stdout line.
    sp = sub.add_parser("worktree-create",
                        help="create + provision a worktree for the WorktreeCreate hook")
    sp.set_defaults(fn=cmd_worktree_create)

    sp = sub.add_parser("repos")
    sp.add_argument("terms", nargs="*",
                    help="terms to rank repos by; omit (or 'show') to print the index. "
                         "Also: include/exclude/enrich <name>, config")
    sp.add_argument("--refresh", action="store_true", help="rescan roots + rewrite the index")
    sp.add_argument("--json", action="store_true", help="emit the structured list for the skill")
    sp.add_argument("--quiet", action="store_true", help="with --refresh, print only a one-line summary")
    sp.add_argument("--no-llm", dest="no_llm", action="store_true",
                    help="with --refresh, skip model enrichment — deterministic summary/keywords only")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="with --refresh, report which enrich:true repos WOULD be sent — send nothing")
    sp.add_argument("--re-summarize", dest="re_summarize", action="store_true",
                    help="with --refresh, regenerate summaries even when one already exists")
    sp.add_argument("--detect-roots", dest="detect_roots", action="store_true",
                    help="propose candidate discovery roots for first-run setup")
    sp.add_argument("--set-roots", dest="set_roots", default=None,
                    help="persist a comma-separated list of discovery roots")
    sp.set_defaults(fn=cmd_repos)

    sp = sub.add_parser("obsidian")
    sp.add_argument("--sync-all", dest="sync_all", action="store_true",
                    help="(re)export every task into the configured Obsidian vault")
    sp.add_argument("--flush", dest="flush", action="store_true",
                    help="re-export ONLY the pending-resync (previously-failed) tasks "
                         "and clear their flags — cheaper than --sync-all; run from an "
                         "unsandboxed shell to drain a sandboxed-export backlog")
    sp.add_argument("--quiet", dest="quiet", action="store_true",
                    help="with --flush: suppress happy-path output (used by the hooks)")
    sp.add_argument("--status", dest="status", action="store_true",
                    help="report the Obsidian export status (default when no flag given)")
    sp.set_defaults(fn=cmd_obsidian)

    sp = sub.add_parser("usage")
    sp.add_argument("mode", nargs="?", default=None,
                    choices=["scan-all", "import-costbar"],
                    help="scan-all: ledger every transcript · import-costbar: one-time costbar cache import")
    sp.add_argument("--task", default=None)
    sp.add_argument("--refresh", action="store_true")
    sp.add_argument("--flush", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp.add_argument("--path", default=None,
                    help="with import-costbar: path to session_totals.json (default: ~/.claude/cache/)")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(fn=cmd_usage)         # WS1 usage ledger: per-task model mix + derived $

    sp = sub.add_parser("export")         # WS8 generic episodic export → any directory
    sp.add_argument("--dir", default=None, help="destination directory (created if absent)")
    sp.add_argument("--task", default=None, help="export one task (seq/id)")
    sp.add_argument("--all", dest="all", action="store_true", help="export every task (default)")
    sp.add_argument("--status", default=None, choices=["open", "active", "closed", "new"],
                    help="export only tasks in this status")
    sp.add_argument("--include", default=None,
                    help="sections to render: usage,prompts,history (default usage,history)")
    sp.add_argument("--since", default=None, help="only tasks updated at/after this ISO date")
    sp.add_argument("--prune", dest="prune", action="store_true",
                    help="reconcile --dir against live tasks: remove notes whose task "
                         "no longer exists (or was redacted) + update index.md")
    sp.set_defaults(fn=cmd_export)

    sp = sub.add_parser("sessions")       # WS5 live-session viewer: running Claude processes
    sp.add_argument("--task", default=None,
                    help="filter to one task's live sessions (seq/id)")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(fn=cmd_sessions)

    sp = sub.add_parser("prompts")        # WS6 tasks-by-prompt view: the exact prompt trail
    sp.add_argument("--task", default=None)
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.add_argument("--md", dest="as_md", action="store_true",
                    help="the shareable Markdown artifact (full text + timestamps)")
    sp.add_argument("--all", action="store_true",
                    help="the complete RAW trail (every kind: commands, compaction rows, "
                         "wrappers) with no replies; default is human prompts + Claude's reply")
    sp.set_defaults(fn=cmd_prompts)

    sp = sub.add_parser("config")
    _add_config_args(sp)
    sp.set_defaults(fn=lambda a: __import__("config").cmd_config(a))

    sp = sub.add_parser("glossary")       # WS3 per-task canonical vocabulary
    _add_glossary_args(sp)
    sp.set_defaults(fn=cmd_glossary)

    sp = sub.add_parser("brief")          # WS3 deterministic house-style brief
    _add_brief_args(sp)
    sp.set_defaults(fn=cmd_brief)

    sp = sub.add_parser("recap")          # task 444: private weekly usage recap
    sp.add_argument("--week", default=None, metavar="YYYY-Www",
                    help="the ISO week to summarize (default: the current week)")
    sp.add_argument("--open", dest="open", action="store_true",
                    help="open the rendered recap in your browser (macOS)")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="print the privacy-safe aggregate stats instead of the path")
    sp.add_argument("--no-scan", dest="no_scan", action="store_true",
                    help="skip the pre-render ledger scan (use the stored numbers as-is)")
    sp.add_argument("--auto-if-due", dest="auto_if_due", action="store_true",
                    help=argparse.SUPPRESS)   # hook entry point: gated + silent
    sp.add_argument("--quiet", dest="quiet", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(fn=cmd_recap)

    sp = sub.add_parser("glossary-context")   # WS3 adapter hook: inject the block
    sp.add_argument("--task", default=None)
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_glossary_context)

    sp = sub.add_parser("stream")         # A-2 durable JSONL event ledger
    sp.add_argument("--since", default=None,
                    help="read events after this cursor (0-based global index)")
    sp.add_argument("--tail", nargs="?", type=int, const=20, default=None,
                    metavar="N", help="the last N events (default 20)")
    sp.add_argument("--json", action="store_true", help="emit raw JSONL envelopes")
    sp.add_argument("--backfill", action="store_true",
                    help="emit a task.snapshot per still-unstreamed task (idempotent)")
    sp.add_argument("--verify", action="store_true",
                    help="check per-task n continuity + shard order")
    sp.set_defaults(fn=cmd_stream)

    sp = sub.add_parser("redact",          # right-to-be-forgotten
                        help="scrub a task's payloads from the stream ledger")
    sp.add_argument("--task", required=True, help="task to redact (seq/id)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_redact)

    # THE PROSE-INPUT CONVENTION, in full, because this is the seam that applies it.
    #
    # Every prose-bearing flag — `--decision`, `--log`, `--note`, `--title`,
    # `--summary`, `--append-summary`, `--state`, `--goal`, `--ask`, `--why`,
    # `memo send --text` and the rest of board.prose_input.PROSE_FLAGS (31 flags
    # across 13 subcommands) — takes its value three ways:
    #
    #   --decision 'text'    the plain string, used verbatim (unchanged, always)
    #   --decision -         read it from stdin
    #   --decision @PATH     read it from a file
    #   --decision @@text    a literal value that begins with @ (one @ dropped)
    #
    # WHY THE LAST THREE EXIST. A shell word is not a string; it is a string the
    # shell has already rewritten. Backticks inside a double-quoted argument run as
    # command substitution, so `--decision "the `turn` command found it"` stored
    # `the  command found it` — the word gone before argv existed — and the write
    # then reported SUCCESS and exited 0. Reading from stdin or from a file are the
    # only input paths a shell cannot touch.
    #
    # Both halves are driven by ONE table in board.prose_input, so the help text a
    # caller reads and the behaviour they get cannot drift apart. The annotate call
    # must precede parse_args (it edits help); the resolve call must sit between
    # parse_args and dispatch, so no handler needs to know any of this exists.
    annotate_prose_help(sub)
    a = p.parse_args(argv)
    resolve_prose_args(a)
    # THE HANDLER'S RETURN VALUE IS THE PROCESS STATUS (3.60.0). Every cmd_* returned
    # None and this line threw it away, so a command that REFUSED to act exited 0 —
    # indistinguishable, to anything reading the status, from one that did the work. That
    # was harmless until 3.49.0 made `returncode == 0` a required conjunct for exit
    # conditions and claims; after it, a condition wrapping a refusing `heal` verb went
    # green on the refusal. `heal` returns `HEAL_REFUSED` now (see board.cmds.maintain);
    # every other handler still returns None, which `sys.exit(None)` reports as 0.
    #
    # RETURNED RATHER THAN `sys.exit`ed INSIDE THE HANDLER, because cmd_* functions are
    # called IN PROCESS — by the whole of tests/test_heal.py and by lib/stop_steps.py's
    # `main(argv)` — and raising SystemExit where none was raised before would make every
    # one of those callers catch an exception to read a status.
    return a.fn(a)
