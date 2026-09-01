"""/todo dispatcher subcommands: `save` (checkpoint the current task's full context
into its digest, NO auto-pin) plus `pin` / `done` / `config` routed through /todo.
1.51.0 hardens `save` — the `[SAVE]` block is a named-slot capture checklist (next
action, tried/rejected approaches, files/paths, branch/env, commands, gotchas, open
questions, user intent) + a cold-read self-check, and it records this session's cwd
as a `/todo <n> -s` transcript backstop WITHOUT pinning. 2.16.0 drops the CURRENT
DIGEST dump that used to lead the block in favour of a GAP REPORT (`--verbose`
restores it) — see tests/test_save_ux.py for that whole surface. Reserved leading keywords
trigger ONLY on the exact leading token, so the existing /todo · /todo <n> · closed ·
all · board behaviours stay unchanged. Stdlib-only, no LLM."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)
import config  # noqa: E402  (config store shares TASK_STATION_HOME)
import decisions as _dec  # noqa: E402


class _Args:
    """render-namespace stand-in: cmd_render reads .session / .arg / .format."""
    def __init__(self, session=None, arg="", fmt=None):
        self.session = session
        self.arg = arg
        self.format = fmt


class TodoSubcommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_TINT", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_TINT", None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------

    def _task(self, title="A task", **kw):
        t = ts.new_task(title, "summary for " + title, **kw)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _attach(self, session, task):
        """Link a session to a task the way an open/attach would."""
        ts.set_link(session, task["id"])
        return ts.load_task(task["id"])

    def _render(self, session, arg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(session=session, arg=arg))
        return buf.getvalue()

    # -- /todo save (NEW) ------------------------------------------------------

    def test_save_prints_playbook_with_gap_report_and_templates(self):
        """2.16.0 replaced the CURRENT DIGEST dump with a GAP REPORT. The block used to
        echo the whole digest back to the session that had just written it — 71,271 of
        one real save's 71,516 characters — so the dump is gone and what remains is the
        list of what is MISSING. `--verbose` still dumps (below)."""
        t = self._task(title="Saveable")
        t["goal"] = "ship 1.50"
        t["state"] = "next: wire the dispatch"
        ts.save_task(t)
        self._attach("sess-A", t)
        out = self._render("sess-A", "save")
        # marker + task ref (seq) + session id
        self.assertIn("[SAVE]", out)
        self.assertIn("#%s" % t["seq"], out)
        self.assertIn("sess-A"[:8], out)
        # the GAP REPORT, and NOT the digest it replaced
        self.assertIn("GAP REPORT", out)
        self.assertIn("DIGEST SIZE", out)
        self.assertNotIn("CURRENT DIGEST", out)
        self.assertNotIn("ship 1.50", out)            # the goal is set, so not echoed
        self.assertNotIn("summary for Saveable", out)  # nor is the summary
        # …and a state that does not lead with `NEXT:` is named as stale
        self.assertIn("STALE", out)
        self.assertIn("NEXT:", out)
        # the capture checklist + the update command templates (resolved seq)
        self.assertIn("CAPTURE CHECKLIST", out)
        self.assertIn("--append-summary", out)
        self.assertIn("update --task %s" % t["seq"], out)
        # explicit no-pin / no-open instruction
        self.assertIn("DO NOT pin", out)

    def test_save_verbose_restores_the_digest_dump(self):
        t = self._task(title="Verbose")
        t["goal"] = "ship 1.50"
        ts.save_task(t)
        self._attach("sess-V", t)
        out = self._render("sess-V", "save --verbose")
        self.assertIn("CURRENT DIGEST (--verbose)", out)
        self.assertIn("ship 1.50", out)
        self.assertIn("Summary:", out)
        self.assertIn("GAP REPORT", out)              # the report is still there too

    def test_save_checklist_covers_every_named_slot(self):
        """The hardened [SAVE] block forces specifics into every capture slot."""
        t = self._task(title="Hardened save")
        self._attach("sess-slots", t)
        out = self._render("sess-slots", "save")
        # NEXT ACTION slot — the state line must LEAD with a concrete NEXT: move
        self.assertIn("NEXT ACTION", out)
        self.assertIn("NEXT:", out)
        # tried & rejected approaches → never re-explore dead ends
        self.assertIn("TRIED and REJECTED", out)
        self.assertIn("dead ends", out)
        # files/paths, branch/worktree/environment, commands
        self.assertIn("Files", out)
        self.assertIn("PATHS", out)
        self.assertIn("branch", out)
        self.assertIn("worktree", out)
        self.assertIn("environment", out)
        self.assertIn("Commands to build", out)
        # gotchas / watch out, open questions, the user's latest intent
        self.assertIn("gotchas", out)
        self.assertIn("watch out", out)
        self.assertIn("Open questions", out)
        self.assertIn("user's most recent intent", out)
        # the cold-read self-check
        self.assertIn("COLD-READ CHECK", out)
        # the transcript backstop closing line names `/todo <n> -s`
        self.assertIn("/todo %s -s" % t["seq"], out)

    def test_save_records_session_cwd_for_transcript_backstop(self):
        """save records THIS session (with a cwd) as a resume candidate WITHOUT pinning,
        so a fresh session can `/todo <n> -s` back into the full transcript later."""
        t = self._task(title="Backstop")
        self._attach("sess-bk", t)
        self._render("sess-bk", "save")
        reloaded = ts.load_task(t["id"])
        meta = reloaded.get("session_meta") or {}
        self.assertIn("sess-bk", meta)
        self.assertTrue(meta["sess-bk"].get("cwd"))       # a cwd was captured
        self.assertNotIn("pinned_session", reloaded)      # still NOT pinned
        self.assertIsNone(reloaded.get("pinned_session"))

    def test_save_does_not_pin_or_mint_a_session(self):
        t = self._task(title="No pin on save")
        self._attach("sess-B", t)
        out = self._render("sess-B", "save")
        reloaded = ts.load_task(t["id"])
        self.assertNotIn("pinned_session", reloaded)      # NO pin recorded
        self.assertIsNone(reloaded.get("pinned_session"))
        self.assertNotIn("--session-id", out)             # no minted-session command
        # save also emits no fresh session into the task's session list
        self.assertEqual(reloaded.get("sessions", []), [])

    def test_save_case_insensitive_and_ignores_trailing_text(self):
        t = self._task(title="Trailing")
        self._attach("sess-C", t)
        out = self._render("sess-C", "SAVE please checkpoint this")
        self.assertIn("[SAVE]", out)
        self.assertIn("update --task %s" % t["seq"], out)

    def test_save_with_no_attached_task_prints_guidance(self):
        out = self._render("orphan-sess", "save")
        self.assertNotIn("[SAVE]", out)
        self.assertIn("No task attached", out)
        self.assertIn("/todo save", out)

    def test_save_block_instructs_snapshot_summary_and_log_milestone(self):
        """The split playbook: summary = current snapshot (--summary), plus a dated
        --log milestone; the checklist gains a LOG slot and warns off dumping the
        history into summary."""
        t = self._task(title="Split save")
        self._attach("sess-split", t)
        out = self._render("sess-split", "save")
        # snapshot goes to --summary (REPLACE), a dated milestone to --log
        self.assertIn("--summary", out)
        self.assertIn("--log", out)
        self.assertIn("CONTEXT SNAPSHOT", out)
        self.assertIn("LOG (--log)", out)
        # explicit: do NOT dump the whole history into summary
        self.assertIn("do NOT dump the history", out)
        # history is retrievable via the history view
        self.assertIn("/todo %s history" % t["seq"], out)

    # -- /todo <n> history (read-only full trace) ------------------------------

    def test_history_view_is_read_only_no_attach_no_mutate(self):
        t = self._task(title="Traceable")
        t["decisions"] = ["chose A", "rejected B"]
        ts.append_history(t, "v1 shipped")
        ts.save_task(t)
        before = ts.load_task(t["id"])
        out = self._render("sess-hist", "%s history" % t["seq"])
        # renders the full trace
        self.assertIn("History —", out)
        self.assertIn("chose A", out)
        self.assertIn("v1 shipped", out)
        # READ-ONLY: the session is NOT attached and the task is NOT mutated
        self.assertIsNone(ts.get_link("sess-hist"))
        after = ts.load_task(t["id"])
        self.assertEqual(after.get("updated_ts"), before.get("updated_ts"))

    def test_history_word_first_form_also_works(self):
        t = self._task(title="Order")
        out = self._render("sess-hw", "history %s" % t["seq"])
        self.assertIn("History —", out)
        self.assertIsNone(ts.get_link("sess-hw"))

    def test_history_bad_ref_reports_no_match(self):
        out = self._render("sess-bad", "9999 history")
        self.assertIn("No task matching", out)

    # -- bare `history` (no number) — current session's attached task (1.57.0) --

    def test_bare_history_shows_current_session_task_read_only(self):
        t = self._task(title="Bare history target")
        t["decisions"] = ["chose X"]
        ts.append_history(t, "v2 shipped")
        ts.save_task(t)
        self._attach("sess-bare-hist", t)
        before = ts.load_task(t["id"])
        out = self._render("sess-bare-hist", "history")
        self.assertIn("History —", out)
        self.assertIn("chose X", out)
        self.assertIn("v2 shipped", out)
        # still read-only: link unchanged, task not mutated
        self.assertEqual(ts.get_link("sess-bare-hist"), t["id"])
        after = ts.load_task(t["id"])
        self.assertEqual(after.get("updated_ts"), before.get("updated_ts"))

    def test_bare_history_with_no_attached_task_prints_guidance(self):
        out = self._render("orphan-hist", "history")
        self.assertNotIn("History —", out)
        self.assertIn("No task attached", out)
        self.assertIn("/todo <n> history", out)

    def test_bare_history_case_insensitive(self):
        t = self._task(title="Bare history caps")
        self._attach("sess-bare-caps", t)
        out = self._render("sess-bare-caps", "HISTORY")
        self.assertIn("History —", out)

    # -- /todo pin -------------------------------------------------------------

    def test_pin_pins_current_session(self):
        t = self._task(title="Pinnable")
        self._attach("sess-P", t)
        out = self._render("sess-P", "pin")
        self.assertEqual(ts.load_task(t["id"]).get("pinned_session"), "sess-P")
        self.assertIn("Pinned", out)

    def test_pin_with_no_attached_task_prints_guidance(self):
        out = self._render("nobody", "pin")
        self.assertIn("No task attached", out)

    # -- /todo done ------------------------------------------------------------

    def test_done_closes_current_task(self):
        t = self._task(title="Close me")
        self._attach("sess-D", t)
        out = self._render("sess-D", "done")
        self.assertTrue(ts.is_closed(ts.load_task(t["id"])))
        self.assertIn("Closed task", out)

    def test_done_by_number(self):
        t = self._task(title="Close by number")
        out = self._render("sess-E", "done %s" % t["seq"])
        self.assertTrue(ts.is_closed(ts.load_task(t["id"])))
        self.assertIn("Closed task", out)

    # -- /todo config ----------------------------------------------------------

    def test_config_no_args_prints_board(self):
        out = self._render("sess-F", "config")
        self.assertIn("task-station config", out)

    def test_config_routes_flags_to_handler(self):
        out = self._render("sess-G", "config --tint off")
        self.assertIn("tint = off", out)
        self.assertFalse(config.tint_enabled())

    # -- existing behaviours UNCHANGED (no keyword collision) ------------------

    def test_bare_todo_still_lists(self):
        self._task(title="Listed task")
        out = self._render("sess-H", "")
        self.assertIn("Listed task", out)
        self.assertNotIn("[SAVE]", out)

    def test_todo_number_still_opens_detail_and_attaches(self):
        t = self._task(title="Detail task")
        out = self._render("sess-I", str(t["seq"]))
        self.assertIn("Task [%s]" % t["id"][:8], out)
        self.assertIn("Summary:", out)
        self.assertEqual(ts.get_link("sess-I"), t["id"])   # now attached

    def test_todo_closed_and_all_still_list(self):
        t = self._task(title="Done long ago")
        t["status"] = "closed"
        ts.save_task(t)
        self.assertIn("Done long ago", self._render("s1", "closed"))
        self.assertIn("Done long ago", self._render("s2", "all"))

    # -- /todo memo ------------------------------------------------------------
    def test_todo_memo_send_to_numbered_task(self):
        t = self._task(title="Memo target")
        out = self._render("me", "memo %s Use WAL mode for the cache" % t["seq"])
        self.assertIn("memo ", out)
        r = ts.load_task(t["id"])
        self.assertEqual(len(r.get("memos", [])), 1)
        self.assertIn("Use WAL mode for the cache", r["memos"][-1]["text"])
        self.assertEqual(r["memos"][-1]["from_sid"], "me")

    def test_todo_memo_ack_on_attached_task(self):
        # M1: `/todo memo ack <id8>` now needs a disposition — `noop:<reason>` is the
        # explicit "nothing durable to change" one. This test covers the attached-task
        # default and the ledger write; the bare-ack refusal is covered in
        # tests/test_supersession.py.
        t = self._task(title="Ack target")
        ts.memo_send(t, "a peer fact", from_sid="peer")
        ts.save_task(t)
        self._attach("acker", t)
        memo = ts.load_task(t["id"])["memos"][-1]
        out = self._render("acker", "memo ack %s noop:nothing to change" % memo["id"][:8])
        self.assertIn("acked by", out)
        r = ts.load_task(t["id"])
        self.assertEqual([a["sid"] for a in r["memos"][-1]["acks"]], ["acker"])

    def test_todo_memo_ack_with_text_promotes_to_decision(self):
        t = self._task(title="Promote target")
        ts.memo_send(t, "raw fact", from_sid="peer")
        ts.save_task(t)
        self._attach("acker", t)
        memo = ts.load_task(t["id"])["memos"][-1]
        self._render("acker", "memo ack %s curated decision wording" % memo["id"][:8])
        # By TEXT: an ack promotion declares kind=process-note, so the entry is rich.
        self.assertIn("curated decision wording",
                      _dec.live_texts(ts.load_task(t["id"]).get("decisions", [])))

    def test_todo_memo_show_lists_memos(self):
        t = self._task(title="Show target")
        ts.memo_send(t, "listed memo body", from_sid="peer")
        ts.save_task(t)
        out = self._render("viewer", "memo show %s" % t["seq"])
        self.assertIn("listed memo body", out)


if __name__ == "__main__":
    unittest.main()
