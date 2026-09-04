"""THE RUNNER OWNS THE TREE — a condition declares its repo and ref as data, and the
runner evaluates it in a detached checkout of that ref.

WHAT THIS COVERS AND WHY EACH ONE IS A WAY THE MECHANISM COULD BE WORSE THAN ABSENT:

  1. THE DECLARATION DECIDES, NOT THE CWD. The discriminating test builds a repo whose
     COMMITTED content differs from its WORKING TREE, runs the condition from inside that
     working tree, and requires the committed content. Nothing but a real checkout of the
     declared ref passes it. This is #602's failure in test form: a probe that exits 0
     from a tree not containing the work.
  2. A CHECKOUT THAT FAILS IS A CONDITION THAT DID NOT RUN. Never a fallback to the
     inherited directory — that would be the defect hiding inside its own fix — and never
     `unmet`, because nothing was refuted.
  3. MERGE-GATED IS COMPUTED, IN BOTH DIRECTIONS. A remote-tracking ref computes True with
     nothing typed; a local branch, a tag and a raw sha compute False. A rule that only
     ever says yes is not a rule.
  4. THE VERDICT RECORDS WHICH COMMIT PRODUCED IT, and a legacy verdict that recorded none
     reads as UNRECORDED rather than as an invented default.
  5. AN UNDECLARED CONDITION IS UNCHANGED, PERMANENTLY. It runs in the inherited cwd and
     keeps its author's `merge_gated` flag. That is the negative control, and it is the
     common case — 336 stored commands declared nothing when this shipped.
  6. NO REDIRECTION EXISTS. Not a flag on the running verbs, not an environment variable,
     not a fallback. `exit-tick` and `claims verify` both STORE their verdict, so one
     redirected rehearsal would leave a false green on the record permanently (591:4).

The git repos here are real but tiny — three commits at most, made in a temp dir — because
the thing under test IS git resolution and a fake would prove nothing about it.
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-treeref-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import checker                # noqa: E402
import exits                  # noqa: E402
import steps as steps_mod     # noqa: E402
import store                  # noqa: E402
import treeref                # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def make_repo(root, committed="COMMITTED", working=None):
    """A one-commit repo whose working tree may DISAGREE with what was committed.

    The disagreement is the whole instrument: a runner that reads the cwd sees `working`,
    a runner that checks the ref out sees `committed`, and no other mechanism separates
    them."""
    os.makedirs(root, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    with open(os.path.join(root, "marker.txt"), "w") as f:
        f.write(committed + "\n")
    _git(root, "add", "marker.txt")
    _git(root, "commit", "-qm", "committed")
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    if working is not None:
        with open(os.path.join(root, "marker.txt"), "w") as f:
            f.write(working + "\n")
    return sha


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, task=None, step=None, cmd=None, expect=None,
                        dry_run=False, untick=False, timeout=None, force=False,
                        merge_gated=False, repo=None, ref=None, build_wait=0)
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


def _repoint(tmp):
    """The isolation idiom tests/test_exits.py uses, copied rather than invented so the
    two suites cannot drift about what "a clean store" means."""
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


class _Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ts-treeref-case-")
        _repoint(os.path.join(self.home, "data"))

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.home, ignore_errors=True)

    def repo(self, name="probe", **kw):
        return os.path.join(self.home, name), make_repo(
            os.path.join(self.home, name), **kw)

    def task_with(self, cmd, expect, repo=None, ref=None, merge_gated=False,
                  text="a step"):
        decl, err = treeref.parse(repo, ref)
        self.assertIsNone(err, err)
        task = {"steps": [text]}
        ok, err = exits.set_condition(task["steps"], 1, cmd, [expect],
                                      merge_gated=merge_gated, decl=decl)
        self.assertTrue(ok, err)
        return task


# ---------------------------------------------------------------- the declaration ----

class DeclarationTest(_Base):
    def test_declaring_nothing_is_legitimate_and_stores_nothing(self):
        decl, err = treeref.parse(None, None)
        self.assertIsNone(err)
        self.assertIsNone(decl)

    def test_a_repo_without_a_ref_is_refused(self):
        path, _sha = self.repo()
        decl, err = treeref.parse(path, None)
        self.assertIsNone(decl)
        self.assertIn("go together", err)

    def test_a_ref_without_a_repo_is_refused(self):
        decl, err = treeref.parse(None, "origin/main")
        self.assertIsNone(decl)
        self.assertIn("go together", err)

    def test_a_relative_repo_path_is_refused(self):
        decl, err = treeref.parse("some/where", "HEAD")
        self.assertIsNone(decl)
        self.assertIn("ABSOLUTE", err)

    def test_a_path_that_is_not_a_git_repo_is_refused(self):
        plain = os.path.join(self.home, "not-a-repo")
        os.makedirs(plain)
        decl, err = treeref.parse(plain, "HEAD")
        self.assertIsNone(decl)
        self.assertIn("not a git repository", err)

    def test_a_ref_that_does_not_resolve_is_refused_and_says_why(self):
        path, _sha = self.repo()
        decl, err = treeref.parse(path, "origin/nope")
        self.assertIsNone(decl)
        self.assertIn("does not resolve", err)
        # The REASON matters: it is not tidiness, it is that merge-gated is computed.
        self.assertIn("COMPUTED", err)

    def test_a_ref_is_normalised_to_its_full_name(self):
        path, _sha = self.repo()
        _git(path, "update-ref", "refs/remotes/origin/main", "HEAD")
        decl, err = treeref.parse(path, "origin/main")
        self.assertIsNone(err)
        self.assertEqual(decl["ref"], "origin/main")
        self.assertEqual(decl["refname"], "refs/remotes/origin/main")

    def test_a_raw_sha_resolves_to_itself(self):
        path, sha = self.repo()
        decl, err = treeref.parse(path, sha)
        self.assertIsNone(err)
        self.assertEqual(decl["refname"], sha)

    def test_a_home_relative_repo_path_is_expanded_not_refused(self):
        # `~` is absolute once expanded; refusing it would make the flag unusable from a
        # shell where every other path is written that way.
        path, _sha = self.repo()
        decl, err = treeref.parse(path.replace(os.path.expanduser("~"), "~", 1)
                                  if path.startswith(os.path.expanduser("~")) else path,
                                  "HEAD")
        self.assertIsNone(err)
        self.assertTrue(os.path.isabs(decl["repo"]))

    def test_a_garbled_stored_declaration_is_filtered_not_raised(self):
        for raw in (None, {}, {"repo": "/x"}, {"ref": "main"}, {"repo": "", "ref": ""},
                    []):
            self.assertIsNone(treeref.declaration(raw))

    def test_a_stored_declaration_missing_its_refname_falls_back_to_the_ref(self):
        # Written by an older build, or hand-edited. It must still name a tree rather
        # than being discarded, because discarding it would silently return the condition
        # to inheriting the cwd.
        d = treeref.declaration({"repo": "/x", "ref": "origin/main"})
        self.assertEqual(d["refname"], "origin/main")
        self.assertTrue(treeref.merge_gated(d) is False)


# ------------------------------------------------------------- merge-gated computed ----

class ComputedMergeGateTest(_Base):
    def test_a_remote_tracking_ref_computes_merge_gated_with_nothing_typed(self):
        path, _sha = self.repo()
        _git(path, "update-ref", "refs/remotes/origin/main", "HEAD")
        task = self.task_with("echo T", "T", repo=path, ref="origin/main")
        self.assertTrue(exits.merge_gated(task["steps"][0]))

    def test_a_local_branch_a_tag_and_a_sha_all_compute_NOT_merge_gated(self):
        path, sha = self.repo()
        _git(path, "tag", "v1")
        _git(path, "branch", "feature")
        for ref in ("HEAD", "feature", "v1", sha):
            task = self.task_with("echo T", "T", repo=path, ref=ref)
            self.assertFalse(exits.merge_gated(task["steps"][0]),
                             "%r must not compute as a merge target" % ref)

    def test_a_second_remote_is_a_merge_target_too(self):
        # The rule is the refs/remotes/ NAMESPACE, not the word "origin" — a repo whose
        # integration remote is called `upstream` gets the same answer.
        path, _sha = self.repo()
        _git(path, "update-ref", "refs/remotes/upstream/dev", "HEAD")
        task = self.task_with("echo T", "T", repo=path, ref="upstream/dev")
        self.assertTrue(exits.merge_gated(task["steps"][0]))

    def test_declaring_a_ref_and_typing_merge_gated_is_refused(self):
        path, _sha = self.repo()
        decl, _err = treeref.parse(path, "HEAD")
        steps = ["a step"]
        ok, err = exits.set_condition(steps, 1, "echo T", ["T"],
                                      merge_gated=True, decl=decl)
        self.assertFalse(ok)
        self.assertIn("--merge-gated cannot be typed alongside --ref", err)

    def test_a_declared_condition_stores_no_author_flag_at_all(self):
        # The key is ABSENT, not False: a stale assertion sitting beside the computed
        # answer is how the two would come to disagree in the store.
        path, _sha = self.repo()
        task = self.task_with("echo T", "T", repo=path, ref="HEAD")
        rich = steps_mod.as_rich(task["steps"][0])
        self.assertNotIn("merge_gated", rich["exit"])

    def test_an_undeclared_condition_keeps_the_author_flag_forever(self):
        task = self.task_with("echo T", "T", merge_gated=True)
        self.assertTrue(exits.merge_gated(task["steps"][0]))
        self.assertIsNone(exits.declared_tree(task["steps"][0]))


# ------------------------------------------------------- the runner owns the tree ----

class DetachedCheckoutTest(_Base):
    def test_the_command_reads_the_declared_ref_not_the_directory_it_was_run_from(self):
        """THE DISCRIMINATING TEST. The working tree says WORKING, the commit says
        COMMITTED, the process cwd is the working tree, and the condition expects
        COMMITTED. Only a real checkout of the declared ref can pass."""
        path, sha = self.repo(committed="COMMITTED", working="WORKING")
        task = self.task_with("cat marker.txt", "COMMITTED", repo=path, ref=sha)
        here = os.getcwd()
        os.chdir(path)
        try:
            results = exits.evaluate(task)
        finally:
            os.chdir(here)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"], results[0]["got"])
        self.assertNotIn("WORKING", results[0]["got"])

    def test_the_same_condition_undeclared_reads_the_cwd_and_fails(self):
        """THE NEGATIVE CONTROL for the test above. Identical command, identical
        expectation, no declaration — and it now reads the working tree and is refuted.
        Without this, the test above could be passing for some other reason."""
        path, _sha = self.repo(committed="COMMITTED", working="WORKING")
        task = self.task_with("cat marker.txt", "COMMITTED")
        here = os.getcwd()
        os.chdir(path)
        try:
            results = exits.evaluate(task)
        finally:
            os.chdir(here)
        self.assertFalse(results[0]["ok"])
        self.assertIn("WORKING", results[0]["got"])

    def test_a_moved_ref_reads_the_commit_it_names_not_the_branch_tip(self):
        path, first = self.repo(committed="FIRST")
        with open(os.path.join(path, "marker.txt"), "w") as f:
            f.write("SECOND\n")
        _git(path, "commit", "-aqm", "second")
        task = self.task_with("cat marker.txt", "FIRST", repo=path, ref=first)
        results = exits.evaluate(task)
        self.assertTrue(results[0]["ok"], results[0]["got"])

    def test_the_checkout_is_detached_so_no_branch_is_moved(self):
        path, _sha = self.repo()
        before = _git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        task = self.task_with("git rev-parse --abbrev-ref HEAD", "HEAD",
                              repo=path, ref="HEAD")
        results = exits.evaluate(task)
        self.assertTrue(results[0]["ok"], results[0]["got"])
        after = _git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assertEqual(before, after)

    def test_the_checkout_is_removed_when_the_run_finishes(self):
        path, _sha = self.repo()
        task = self.task_with("pwd", os.sep, repo=path, ref="HEAD")
        results = exits.evaluate(task)
        where = results[0]["got"].strip().splitlines()[-1]
        self.assertIn(treeref.TMP_PREFIX, where)
        self.assertFalse(os.path.exists(where),
                         "the throwaway checkout outlived the run: %s" % where)

    def test_two_conditions_on_one_ref_share_a_single_checkout(self):
        """Not merely an optimisation: two checkouts could be two different commits if
        the ref moved mid-run, so one `exit-tick` would report on two trees while saying
        it reported on one."""
        path, _sha = self.repo()
        decl, _err = treeref.parse(path, "HEAD")
        task = {"steps": ["one", "two"]}
        for i in (1, 2):
            exits.set_condition(task["steps"], i, "pwd", [os.sep], decl=decl)
        results = exits.evaluate(task)
        seen = {r["got"].strip().splitlines()[-1] for r in results}
        self.assertEqual(len(seen), 1, seen)

    def test_a_missing_repo_is_a_condition_that_did_not_run(self):
        path, sha = self.repo()
        task = self.task_with("cat marker.txt", "COMMITTED", repo=path, ref=sha)
        shutil.rmtree(path)
        results = exits.evaluate(task)
        self.assertEqual(results[0]["status"], "error")
        self.assertFalse(results[0]["ok"])
        self.assertEqual(exits.item_state(task["steps"][0]), exits.UNKNOWN)

    def test_a_ref_that_vanished_is_a_condition_that_did_not_run(self):
        path, _sha = self.repo()
        _git(path, "update-ref", "refs/remotes/origin/main", "HEAD")
        task = self.task_with("cat marker.txt", "COMMITTED",
                              repo=path, ref="origin/main")
        _git(path, "update-ref", "-d", "refs/remotes/origin/main")
        results = exits.evaluate(task)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("does not resolve", results[0]["got"])

    def test_a_failed_checkout_moves_no_tick_in_either_direction(self):
        path, sha = self.repo()
        task = self.task_with("cat marker.txt", "COMMITTED", repo=path, ref=sha)
        task["steps"][0] = steps_mod.compact(dict(steps_mod.as_rich(task["steps"][0]),
                                                  done=True))
        shutil.rmtree(path)
        results = exits.evaluate(task)
        moved = exits.apply_results(task, results)
        self.assertEqual(moved["unknown"], [1])
        self.assertEqual(moved["unticked"], [])
        self.assertEqual(moved["regressed"], [])
        self.assertTrue(steps_mod.is_done(task["steps"][0]))

    def test_a_failed_checkout_never_falls_back_to_the_cwd(self):
        """The sharpest rule in the module. The cwd contains a marker that WOULD satisfy
        the condition; the declared repo is gone. A fallback would go green here."""
        path, sha = self.repo(committed="COMMITTED")
        elsewhere = os.path.join(self.home, "elsewhere")
        os.makedirs(elsewhere)
        with open(os.path.join(elsewhere, "marker.txt"), "w") as f:
            f.write("COMMITTED\n")
        task = self.task_with("cat marker.txt", "COMMITTED", repo=path, ref=sha)
        shutil.rmtree(path)
        here = os.getcwd()
        os.chdir(elsewhere)
        try:
            results = exits.evaluate(task)
        finally:
            os.chdir(here)
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["status"], "error")


# ------------------------------------------------------------------- provenance ----

class ProvenanceTest(_Base):
    def test_the_run_records_the_commit_it_actually_read(self):
        path, sha = self.repo()
        task = self.task_with("cat marker.txt", "COMMITTED", repo=path, ref="HEAD")
        results = exits.evaluate(task)
        self.assertEqual(results[0]["tree"], {"repo": path, "ref": "HEAD", "sha": sha})
        self.assertEqual(exits.tree_of(task["steps"][0])["sha"], sha)

    def test_an_undeclared_run_records_no_tree_rather_than_inventing_one(self):
        task = self.task_with("echo T", "T")
        results = exits.evaluate(task)
        self.assertIsNone(results[0]["tree"])
        self.assertIsNone(exits.tree_of(task["steps"][0]))

    def test_a_legacy_stored_verdict_reads_as_unrecorded(self):
        step = {"text": "written before 3.66.0", "done": True,
                "exit": {"cmd": "echo T", "expect": ["T"], "merge_gated": True,
                         "last": {"ts": 2.0, "ok": True, "status": "ran", "code": 0,
                                  "missing": [], "got": "T"}}}
        self.assertEqual(exits.item_state(step), exits.MET)
        self.assertTrue(exits.merge_gated(step))
        self.assertIsNone(exits.tree_of(step))
        self.assertIsNone(treeref.provenance_line(None))

    def test_provenance_of_a_half_written_record_is_None_not_a_partial_line(self):
        for rec in ({}, {"repo": "/x"}, {"repo": "/x", "ref": "m"}, None, "nonsense"):
            self.assertIsNone(treeref.provenance_line(rec))

    def test_the_provenance_line_names_repo_ref_and_commit(self):
        line = treeref.provenance_line({"repo": "/r", "ref": "origin/main",
                                        "sha": "0123456789abcdef"})
        self.assertIn("/r", line)
        self.assertIn("origin/main", line)
        self.assertIn("0123456789ab", line)


# ------------------------------------------------------------- history not re-judged ----

class HistoryIsNotReJudgedTest(_Base):
    """595:1 — stored verdicts are NOT retroactively rewritten; the rule applies when a
    condition is RUN. This upgrade inherits that, and these are the ways it could not
    have."""

    LEGACY = {"text": "a step from before 3.66.0", "done": True,
              "exit": {"cmd": "echo T", "expect": ["T"], "merge_gated": True,
                       "added_ts": 1.0,
                       "last": {"ts": 2.0, "ok": True, "status": "ran", "code": 0,
                                "missing": [], "got": "T"}}}

    def test_reading_a_legacy_task_changes_nothing_on_disk(self):
        import copy
        task = {"steps": [copy.deepcopy(self.LEGACY)]}
        before = copy.deepcopy(task)
        for fn in (exits.items, exits.summary, exits.state, exits.coverage,
                   exits.satisfied, exits.merge_gate, exits.last_run_ts):
            fn(task)
        self.assertEqual(task, before)

    def test_a_legacy_met_verdict_stays_met(self):
        import copy
        task = {"steps": [copy.deepcopy(self.LEGACY)]}
        self.assertEqual(exits.state(task), exits.MET)
        self.assertTrue(exits.satisfied(task))

    def test_a_legacy_unmet_verdict_stays_unmet(self):
        import copy
        step = copy.deepcopy(self.LEGACY)
        step["exit"]["last"]["ok"] = False
        self.assertEqual(exits.item_state(step), exits.UNMET)

    def test_the_legacy_merge_gate_tally_is_unchanged(self):
        import copy
        step = copy.deepcopy(self.LEGACY)
        step["exit"]["last"]["ok"] = False
        task = {"steps": [step]}
        tally = exits.merge_gate(task)
        self.assertEqual(tally["declared"], 1)
        self.assertEqual(tally["unmet"], 1)
        self.assertTrue(tally["all_merge_gated"])

    def test_an_ordinary_undeclared_step_still_round_trips_byte_identically(self):
        task = {"steps": ["a plain step"]}
        exits.set_condition(task["steps"], 1, "echo T", ["T"])
        rich = steps_mod.as_rich(task["steps"][0])
        self.assertEqual(sorted(rich["exit"]),
                         ["added_ts", "cmd", "expect", "merge_gated"])


# ------------------------------------------------------------------------ claims ----

class ClaimTreeTest(_Base):
    def test_a_claim_runs_in_the_tree_it_declares(self):
        path, sha = self.repo(committed="COMMITTED", working="WORKING")
        decl, err = treeref.parse(path, sha)
        self.assertIsNone(err)
        task = {}
        added, _u, errors = checker.register(
            task, ["C1|cat marker.txt|COMMITTED"], decl=decl)
        self.assertEqual((added, errors), (1, []))
        here = os.getcwd()
        os.chdir(path)
        try:
            results = checker.verify(task)
        finally:
            os.chdir(here)
        self.assertTrue(results[0]["ok"], results[0]["got"])
        self.assertEqual(results[0]["tree"]["sha"], sha)

    def test_an_undeclared_claim_runs_in_the_cwd_exactly_as_before(self):
        path, _sha = self.repo(committed="COMMITTED", working="WORKING")
        task = {}
        checker.register(task, ["C1|cat marker.txt|WORKING"])
        here = os.getcwd()
        os.chdir(path)
        try:
            results = checker.verify(task)
        finally:
            os.chdir(here)
        self.assertTrue(results[0]["ok"], results[0]["got"])
        self.assertNotIn("tree", results[0])

    def test_a_claim_whose_tree_is_gone_did_not_run(self):
        path, sha = self.repo()
        decl, _err = treeref.parse(path, sha)
        task = {}
        checker.register(task, ["C1|cat marker.txt|COMMITTED"], decl=decl)
        shutil.rmtree(path)
        results = checker.verify(task)
        self.assertEqual(results[0]["status"], "error")
        self.assertFalse(results[0]["ok"])

    def test_the_declaration_survives_the_store_round_trip(self):
        path, sha = self.repo()
        decl, _err = treeref.parse(path, sha)
        task = {}
        checker.register(task, ["C1|echo T|T"], decl=decl)
        item = checker.claim_items(task)[0]
        self.assertEqual(item["decl"]["repo"], path)
        self.assertEqual(item["decl"]["refname"], sha)

    def test_re_registering_without_a_declaration_drops_the_old_one(self):
        """UPSERT MEANS UPSERT. A rewritten claim that silently kept the previous claim's
        tree would be running somewhere its author never named."""
        path, sha = self.repo()
        decl, _err = treeref.parse(path, sha)
        task = {}
        checker.register(task, ["C1|echo T|T"], decl=decl)
        checker.register(task, ["C1|echo T|T"])
        self.assertIsNone(checker.claim_items(task)[0]["decl"])


# ----------------------------------------------------------- the injected runner ----

class InjectedRunnerTest(_Base):
    def test_a_two_argument_fake_is_called_exactly_as_it_always_was(self):
        seen = []

        def fake(cmd, timeout):
            seen.append((cmd, timeout))
            return "T", "ran"

        task = self.task_with("echo T", "T")
        results = exits.evaluate(task, run=fake, timeout=7)
        self.assertEqual(seen, [("echo T", 7)])
        self.assertTrue(results[0]["ok"])

    def test_a_three_argument_runner_is_handed_the_checkout(self):
        path, sha = self.repo()
        seen = []

        def fake(cmd, timeout, cwd=None):
            seen.append(cwd)
            return "T", "ran", 0

        task = self.task_with("echo T", "T", repo=path, ref=sha)
        exits.evaluate(task, run=fake)
        self.assertEqual(len(seen), 1)
        self.assertIn(treeref.TMP_PREFIX, seen[0])

    def test_arity_is_read_from_the_signature_never_from_a_TypeError(self):
        """A TypeError raised INSIDE a runner must not be mistaken for a two-argument
        fake and cause the command to be re-run — for a command with side effects that is
        a bug nobody could see."""
        calls = []

        def raises(cmd, timeout, cwd=None):
            calls.append(1)
            raise TypeError("something inside the runner")

        self.assertTrue(checker._takes_cwd(raises))
        with self.assertRaises(TypeError):
            checker.invoke(raises, "x", 1, cwd="/tmp")
        self.assertEqual(len(calls), 1)

    def test_a_runner_whose_signature_cannot_be_read_is_called_with_two_arguments(self):
        self.assertFalse(checker._takes_cwd(len))


# ------------------------------------------------------------------ no redirection ----

class NoRedirectionTest(_Base):
    def test_no_environment_variable_moves_the_tree(self):
        path, sha = self.repo(committed="COMMITTED", working="WORKING")
        decoy = os.path.join(self.home, "decoy")
        make_repo(decoy, committed="DECOY")
        task = self.task_with("cat marker.txt", "COMMITTED", repo=path, ref=sha)
        saved = {k: os.environ.get(k) for k in
                 ("TS_REPO", "TASK_STATION_REPO", "TASK_STATION_REF")}
        os.environ.update({"TS_REPO": decoy, "TASK_STATION_REPO": decoy,
                           "TASK_STATION_REF": "HEAD"})
        try:
            results = exits.evaluate(task)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertTrue(results[0]["ok"], results[0]["got"])
        self.assertEqual(results[0]["tree"]["sha"], sha)
        self.assertNotIn("DECOY", results[0]["got"])

    def test_treeref_reads_no_environment_at_all(self):
        """The module-level guarantee, asserted against the source: an `os.environ` read
        anywhere in here would be a redirection point, whatever it was called.

        Asserted against the CODE — the module explains at length why it reads no
        environment, and a naive grep would find its own explanation. That is the same
        false positive the blast-radius script hit reading two resolvers' comments."""
        import ast
        with open(os.path.join(LIB, "board", "treeref.py"), encoding="utf-8") as f:
            src = f.read()
        names = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
        self.assertNotIn("environ", names)
        self.assertNotIn("getenv", names)

    def test_exit_tick_offers_no_repo_or_ref_override(self):
        """`exit-tick` STORES its verdict, so a run-time override would leave a green on
        the record that outlives whatever caveat the report carried (591:4). Asserted
        through `--help`, which is the surface a user would find one on."""
        p = subprocess.run([sys.executable, os.path.join(LIB, "task-station.py"),
                            "exit-tick", "--help"], capture_output=True, text=True)
        text = (p.stdout or "") + (p.stderr or "")
        self.assertIn("exit-tick", text)
        self.assertNotIn("--repo", text)
        self.assertNotIn("--ref", text)


# ------------------------------------------------------------------------- the CLI ----

class CliTest(_Base):
    def _run(self, fn, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                fn(_Args(**kw))
            except SystemExit as e:
                buf.write("\n[exit %s]" % e.code)
        return buf.getvalue()

    def _task(self, steps=("a step",)):
        t = ts.new_task("a probe", "summary")
        t["steps"] = [{"text": s, "done": False} for s in steps]
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        return str(t.get("seq") or t["id"][:8])

    def test_exit_add_stores_and_prints_the_declaration(self):
        path, sha = self.repo()
        ref = self._task()
        out = self._run(ts.cmd_exit_add, task=ref, step=1, cmd="echo T", expect=["T"],
                        repo=path, ref=sha)
        self.assertIn("tree:", out)
        self.assertIn(path, out)

    def test_exit_add_refuses_an_unresolvable_ref_and_stores_nothing(self):
        path, _sha = self.repo()
        ref = self._task()
        out = self._run(ts.cmd_exit_add, task=ref, step=1, cmd="echo T", expect=["T"],
                        repo=path, ref="origin/nope")
        self.assertIn("does not resolve", out)
        self.assertIn("[exit 2]", out)
        self.assertEqual(self._run(ts.cmd_exit_show, task=ref).count("no exit conditions"),
                         1)

    def test_exit_add_computes_merge_gated_from_the_ref_with_nothing_typed(self):
        path, _sha = self.repo()
        _git(path, "update-ref", "refs/remotes/origin/main", "HEAD")
        ref = self._task()
        out = self._run(ts.cmd_exit_add, task=ref, step=1, cmd="echo T", expect=["T"],
                        repo=path, ref="origin/main")
        self.assertIn("MERGE-GATED, COMPUTED", out)
        self.assertIn("Nobody typed it", out)

    def test_exit_show_prints_the_tree_the_last_run_read(self):
        path, sha = self.repo()
        ref = self._task()
        self._run(ts.cmd_exit_add, task=ref, step=1, cmd="cat marker.txt",
                  expect=["COMMITTED"], repo=path, ref=sha)
        self._run(ts.cmd_exit_tick, task=ref)
        out = self._run(ts.cmd_exit_show, task=ref)
        self.assertIn("read %s @ %s = %s" % (path, sha, sha[:12]), out)

    def test_exit_show_on_an_undeclared_condition_says_nothing_about_a_tree(self):
        ref = self._task()
        self._run(ts.cmd_exit_add, task=ref, step=1, cmd="echo T", expect=["T"])
        self._run(ts.cmd_exit_tick, task=ref)
        out = self._run(ts.cmd_exit_show, task=ref)
        self.assertNotIn("tree:", out)
        self.assertNotIn("read ", out)


if __name__ == "__main__":
    unittest.main()
