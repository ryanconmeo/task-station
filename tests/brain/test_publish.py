"""brain.publish — the publish engine (private vault -> shared brain mirror).

Covers: opt-IN eligibility (`publish: true` and nothing else), each publish-lint
block class (local home path / UUID session id / secret), mirror
add+update+delete (true mirror semantics), the three-way deletion sweep and the
withdrawn-but-kept safety property, INDEX regeneration, README-once, byte-exact
+ idempotence (unchanged input = zero writes), single git commit, and the
no-mirror no-op.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4b) from the brain source tree's
``tests/test_publish.py`` @ 0.14.0. All 20 source cases port. Mechanical
differences:

  * modules import normally — ``brain.publish`` / ``brain.config``;
  * the two env overrides use chunk 1's ``TASK_STATION_BRAIN_*`` namespace (read
    off ``base.ENV_KEYS``), not the source's retired prefix;
  * the source's ``HealGateTest`` is ``IfConfiguredGateTest`` here — the name
    would otherwise collide, for a reader, with
    ``tests/brain/test_heal_gate.py::HealGateTest`` (the daily heal cadence),
    which is a different thing entirely. Both cases are unchanged.

ADDED here (no source counterpart): the secret pattern set is heal_lint's ONE
source of truth and every alternation of it blocks; a note that BECOMES dirty is
swept out of the mirror; the no-notes and missing-vault no-ops; and a real git
failure is loud.
"""
import contextlib
import io
import os
import re
import subprocess
import unittest

from tests.brain.base import BrainTestCase, ENV_KEYS

import brain.config as config
import brain.heal_lint as heal_lint
import brain.publish as publish


class PublishBase(BrainTestCase):
    """Vault + mirror fixture. ZERO test methods — contributes no cases."""

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        # base.setUp cleared this and restores it on cleanup. Named against
        # ENV_KEYS so a renamed config key fails as "not in ENV_KEYS" rather than
        # as a publish that silently read the DEFAULT vault path.
        self.assertIn("TASK_STATION_BRAIN_VAULT", ENV_KEYS)
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.vault)
        self.mirror = self.home / "shared-brain"

    def write_note(self, slug, *, description="a note", body="body text",
                   publish="true", promote=None, type="reference"):
        """Write a source note. ``publish`` DEFAULTS TO ``"true"`` so every suite
        below that is about something else (lint, INDEX, git, idempotence) gets a
        publishable note without saying so. The default-OFF property is what
        :class:`EligibilityTest` asserts explicitly — pass ``publish=None``."""
        fm = [f"name: {slug}", f"description: {description}", f"type: {type}"]
        if publish is not None:
            fm.append(f"publish: {publish}")
        if promote is not None:
            fm.append(f"promote: {promote}")
        (self.vault / "notes" / f"{slug}.md").write_text(
            "---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n")

    def do_publish(self, **kw):
        # NB: not named run() — that would shadow unittest.TestCase.run.
        kw.setdefault("mirror", self.mirror)
        kw.setdefault("today", "2026-07-14")
        return publish.run(config.load(), **kw)

    def mirror_notes(self):
        return sorted(p.name for p in self.mirror.glob("*.md")
                      if p.name not in ("INDEX.md", "README.md"))


class EligibilityTest(PublishBase):
    """Opt-IN. The default is private, and only a literal ``true`` changes that."""

    def test_a_note_with_no_switches_never_reaches_the_mirror(self):
        """Brief test 1. Two of the three notes are marked; the unmarked one must
        not leak, and the run must still publish exactly the two that ARE marked
        (a positive count — "nothing published" would pass a bare not-in check)."""
        self.write_note("unmarked", publish=None)
        self.write_note("marked-one")
        self.write_note("marked-two")
        res = self.do_publish()
        self.assertEqual(sorted(res["published"]), ["marked-one", "marked-two"])
        self.assertEqual(len(res["published"]), 2)
        self.assertEqual(self.mirror_notes(), ["marked-one.md", "marked-two.md"])
        self.assertFalse((self.mirror / "unmarked.md").exists())

    def test_publish_true_reaches_the_mirror_byte_exact(self):
        """Brief test 2."""
        self.write_note("shared", body="the exact bytes")
        res = self.do_publish()
        self.assertEqual(res["published"], ["shared"])
        self.assertEqual((self.vault / "notes/shared.md").read_bytes(),
                         (self.mirror / "shared.md").read_bytes())

    def test_true_is_read_in_any_case_and_through_quotes(self):
        for i, raw in enumerate(("true", "True", "TRUE", '"true"', "'True'")):
            with self.subTest(value=raw):
                self.write_note(f"cased{i}", publish=raw)
        res = self.do_publish()
        self.assertEqual(len(res["published"]), 5)
        self.assertEqual(self.mirror_notes(),
                         [f"cased{i}.md" for i in range(5)])

    def test_anything_that_is_not_true_is_off(self):
        """Never guess, never warn-and-enable: an unrecognised value is private."""
        for i, raw in enumerate(("false", "yes", "1", "maybe", "tru", "")):
            with self.subTest(value=raw):
                self.write_note(f"odd{i}", publish=raw)
        self.write_note("control")          # so the run is not vacuously empty
        res = self.do_publish()
        self.assertEqual(res["published"], ["control"])
        self.assertEqual(self.mirror_notes(), ["control.md"])

    def test_promote_true_alone_does_not_publish(self):
        """Brief test 4 (the publish half). The two switches are independent: a
        note bound for the org brain does not pass through the shared mirror
        unless it separately says so."""
        self.write_note("org-bound", publish=None, promote="true")
        self.write_note("also-shared")      # positive count, not an empty scan
        res = self.do_publish()
        self.assertEqual(res["published"], ["also-shared"])
        self.assertEqual(self.mirror_notes(), ["also-shared.md"])

    def test_underscore_stems_never_publish(self):
        """The promote queue (notes/_org_brain-queue.md) is org-bound content
        WAITING for its reviewed PR — publishing it would push org knowledge
        to the org-readable mirror without the human gate. `_`-stems are
        internal bookkeeping to every notes/ consumer; publish included
        (hub decision, 444-15; the source missed the skip)."""
        (self.vault / "notes" / "_org_brain-queue.md").write_text(
            "# queued for org brain\n\n## pending-node\n\nstripped body\n")
        self.write_note("ok")
        res = self.do_publish()
        self.assertEqual(res["published"], ["ok"])
        self.assertEqual(self.mirror_notes(), ["ok.md"])

    def test_never_touches_other_dirs(self):
        # memory/raw/plans/reports must NEVER publish, even with eligible content.
        for d in ("raw", "plans", "reports"):
            (self.vault / d).mkdir(parents=True, exist_ok=True)
            (self.vault / d / "leak.md").write_text(
                "---\nname: leak\ndescription: x\n---\n\nshould not publish\n")
        self.write_note("ok")
        self.do_publish()
        self.assertEqual(self.mirror_notes(), ["ok.md"])


class LintBlockTest(PublishBase):
    def _blocked_reasons(self, res, name):
        for n, reasons in res["blocked"]:
            if n == name:
                return reasons
        return None

    def test_block_local_home_path(self):
        self.write_note("pathy", body="see /Users/ryan/secret/notes.md for detail")
        res = self.do_publish()
        self.assertEqual(self.mirror_notes(), [])          # never synced
        reasons = self._blocked_reasons(res, "pathy.md")
        self.assertTrue(reasons and any("path" in r.lower() for r in reasons))

    def test_block_uuid_session_id(self):
        """A UUID in session context blocks; a bare reference GUID does not."""
        self.write_note("sess", body="resume: claude --resume 9e159c79-cd67-4f35-a3fd-6caf99a6b86b")
        self.write_note("ref", body="ADO Git namespace: 2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87")
        res = self.do_publish()
        self.assertTrue(any("UUID-shaped" in r for r in self._blocked_reasons(res, "sess.md")))
        self.assertFalse(self._blocked_reasons(res, "ref.md"))
        self.assertTrue((self.mirror / "ref.md").exists()
                        or (self.mirror / "notes" / "ref.md").exists())

    def test_block_secret(self):
        self.write_note("secrety", body="api_key = ABCDEF0123456789abcdef")
        res = self.do_publish()
        self.assertEqual(self.mirror_notes(), [])
        reasons = self._blocked_reasons(res, "secrety.md")
        self.assertTrue(reasons and any("secret" in r.lower() for r in reasons))

    def test_blocked_note_never_transformed_and_run_continues(self):
        self.write_note("clean1")
        self.write_note("dirty", body="path /Users/x/y here")
        self.write_note("clean2")
        res = self.do_publish()
        # the run continues past the block; the two clean notes still publish
        self.assertEqual(self.mirror_notes(), ["clean1.md", "clean2.md"])
        self.assertEqual([n for n, _ in res["blocked"]], ["dirty.md"])
        # the blocked source is never rewritten/transformed in the vault
        self.assertIn("/Users/x/y", (self.vault / "notes/dirty.md").read_text())


class MirrorSemanticsTest(PublishBase):
    def test_add_update_delete(self):
        self.write_note("keep", body="v1")
        self.write_note("goaway", body="v1")
        self.do_publish()
        self.assertEqual(self.mirror_notes(), ["goaway.md", "keep.md"])

        # update keep, delete goaway's SOURCE, add fresh
        self.write_note("keep", body="v2 updated")
        (self.vault / "notes/goaway.md").unlink()
        self.write_note("fresh", body="new")
        res = self.do_publish()
        self.assertEqual(self.mirror_notes(), ["fresh.md", "keep.md"])   # goaway deleted
        self.assertIn("v2 updated", (self.mirror / "keep.md").read_text())
        self.assertIn("goaway", res["removed"])
        self.assertEqual(res["withdrawn"], [])   # a gone source is not a withdrawal

    def test_delete_when_source_removed(self):
        """Brief test 6: mirror semantics survive the opt-in flip. A mirror note
        whose SOURCE FILE is gone is still deleted — the withdrawn-but-kept rule
        protects live sources only, never orphans."""
        self.write_note("temp", body="x")
        self.write_note("stays", body="y")       # positive count on the rerun
        self.do_publish()
        self.assertEqual(self.mirror_notes(), ["stays.md", "temp.md"])
        (self.vault / "notes/temp.md").unlink()
        res = self.do_publish()
        self.assertEqual(self.mirror_notes(), ["stays.md"])
        self.assertEqual(res["removed"], ["temp"])
        self.assertEqual(res["withdrawn"], [])
        self.assertEqual(res["published"], ["stays"])

    def test_byte_exact_copy(self):
        self.write_note("exact", body="precise bytes")
        self.do_publish()
        self.assertEqual((self.vault / "notes/exact.md").read_bytes(),
                         (self.mirror / "exact.md").read_bytes())


class IndexAndReadmeTest(PublishBase):
    def test_index_regenerated_grouped_by_type(self):
        self.write_note("howto-note", description="how you do it", type="how-to")
        self.write_note("ref-note", description="a reference", type="reference")
        self.do_publish()
        idx = (self.mirror / "INDEX.md").read_text()
        self.assertIn("[[howto-note]]", idx)
        self.assertIn("how you do it", idx)
        self.assertIn("## how-to", idx)
        self.assertIn("## reference", idx)

    def test_index_updates_when_notes_change(self):
        self.write_note("one", description="first")
        self.do_publish()
        self.write_note("two", description="second")
        self.do_publish()
        idx = (self.mirror / "INDEX.md").read_text()
        self.assertIn("[[one]]", idx)
        self.assertIn("[[two]]", idx)

    def test_readme_written_once(self):
        self.write_note("n")
        self.do_publish()
        readme = self.mirror / "README.md"
        self.assertTrue(readme.exists())
        text = readme.read_text()
        self.assertIn("read-only", text.lower())
        self.assertIn("generated", text.lower())
        # a hand-edit survives a re-publish (README written only if absent)
        readme.write_text("# custom\n")
        self.do_publish()
        self.assertEqual(readme.read_text(), "# custom\n")


# --------------------------------------------------------------------------- #
# The safety property of the opt-out -> opt-in flip. Mirror semantics alone would
# mean the FIRST run after the flip erases the entire shared brain, because no
# note carries `publish: true` yet. The third sweep case is what makes that
# impossible by accident, so it gets its own suite.
# --------------------------------------------------------------------------- #
class WithdrawalTest(PublishBase):
    def _publish_then_unmark(self):
        self.write_note("was-shared", body="published once")
        self.write_note("still-shared", body="stays marked")
        self.do_publish()
        self.assertEqual(self.mirror_notes(), ["still-shared.md", "was-shared.md"])
        self.write_note("was-shared", body="published once", publish=None)

    def test_an_unmarked_note_is_reported_and_left_in_the_mirror(self):
        """Brief test 5 (default half): reported in ``withdrawn``, and the FILE IS
        STILL THERE afterwards."""
        self._publish_then_unmark()
        res = self.do_publish()
        self.assertEqual(res["withdrawn"], ["was-shared"])
        self.assertEqual(res["removed"], [])
        self.assertTrue((self.mirror / "was-shared.md").exists())
        self.assertEqual(self.mirror_notes(), ["still-shared.md", "was-shared.md"])
        self.assertEqual(res["published"], ["still-shared"])   # the run still works

    def test_withdraw_flag_removes_it_and_counts_it_as_removed(self):
        """Brief test 5 (opt-in half): with ``withdraw=True`` it is deleted, and it
        appears in BOTH lists — ``removed`` (it left) and ``withdrawn`` (why)."""
        self._publish_then_unmark()
        res = self.do_publish(withdraw=True)
        self.assertEqual(res["withdrawn"], ["was-shared"])
        self.assertEqual(res["removed"], ["was-shared"])
        self.assertFalse((self.mirror / "was-shared.md").exists())
        self.assertEqual(self.mirror_notes(), ["still-shared.md"])

    def test_a_withdrawn_note_survives_every_default_rerun(self):
        """The failure this guards against is a slow bleed: reported once, then
        quietly dropped on the next run. It must stay until someone opts in."""
        self._publish_then_unmark()
        for i in range(3):
            with self.subTest(run=i):
                res = self.do_publish()
                self.assertEqual(res["withdrawn"], ["was-shared"])
                self.assertTrue((self.mirror / "was-shared.md").exists())

    def test_a_withdrawn_note_costs_no_writes_on_a_rerun(self):
        """Idempotence must hold WITH a withdrawn note present: the first run
        after the un-marking rewrites INDEX.md (the note leaves the listing), and
        every run after that writes nothing at all."""
        self._publish_then_unmark()
        self.assertGreater(self.do_publish()["writes"], 0)   # INDEX.md drops the line
        self.assertEqual(self.do_publish()["writes"], 0)

    def test_a_blocked_note_is_deleted_not_withdrawn(self):
        """Row two of the sweep table beats row three: a leak comes out of the
        mirror immediately, even though its source is present."""
        self.write_note("leaky", body="clean for now")
        self.do_publish()
        self.write_note("leaky", body="now with /Users/ada/x in it", publish=None)
        res = self.do_publish()
        self.assertEqual(res["removed"], ["leaky"])
        self.assertEqual(res["withdrawn"], [])
        self.assertEqual(self.mirror_notes(), [])

    def test_the_summary_names_the_notes_and_the_exact_command(self):
        """A withdrawn note that is not surfaced is a note nobody re-publishes."""
        self._publish_then_unmark()
        res = self.do_publish()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            publish._print_summary(res, self.mirror)
        out = buf.getvalue()
        self.assertIn("WITHDRAWN-BUT-KEPT", out)
        self.assertIn("- was-shared", out)                 # one per line, by name
        self.assertIn("NOT deleted", out)
        self.assertIn("`publish: true`", out)              # how to keep it
        self.assertIn("--withdraw", out)                   # how to drop it

    def test_the_summary_does_not_claim_a_withdrawn_note_was_kept(self):
        """After ``--withdraw`` the notes ARE gone; printing the keep-it banner
        would be a lie the user acts on."""
        self._publish_then_unmark()
        res = self.do_publish(withdraw=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            publish._print_summary(res, self.mirror)
        out = buf.getvalue()
        self.assertNotIn("WITHDRAWN-BUT-KEPT", out)
        self.assertIn("removed was-shared", out)

    def test_the_cli_exposes_withdraw(self):
        os.environ["TASK_STATION_BRAIN_PUBLISH_MIRROR"] = str(self.mirror)
        self._publish_then_unmark()
        self.assertEqual(publish.main(["--mirror", str(self.mirror), "--quiet"]), 0)
        self.assertTrue((self.mirror / "was-shared.md").exists())      # kept
        self.assertEqual(
            publish.main(["--mirror", str(self.mirror), "--withdraw", "--quiet"]), 0)
        self.assertFalse((self.mirror / "was-shared.md").exists())     # dropped


class IdempotenceTest(PublishBase):
    def test_unchanged_input_zero_writes(self):
        """Brief test 7."""
        self.write_note("stable", body="unchanging")
        first = self.do_publish()
        self.assertGreater(first["writes"], 0)
        self.assertEqual(first["published"], ["stable"])
        second = self.do_publish()
        self.assertEqual(second["writes"], 0)   # nothing rewritten
        self.assertEqual(second["removed"], [])
        self.assertEqual(second["withdrawn"], [])
        self.assertEqual(second["published"], ["stable"])


class GitCommitTest(PublishBase):
    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.mirror), *args],
                              capture_output=True, text=True)

    def test_single_commit_when_mirror_is_repo(self):
        self.mirror.mkdir(parents=True, exist_ok=True)
        self._git("init")
        self._git("config", "user.email", "t@e.com")
        self._git("config", "user.name", "T")
        self.write_note("committed", body="x")
        before = self._git("rev-list", "--count", "HEAD").stdout.strip() or "0"
        res = self.do_publish()
        after = self._git("rev-list", "--count", "HEAD").stdout.strip()
        self.assertEqual(int(after) - int(before or 0), 1)          # exactly one commit
        self.assertTrue(res["committed"].startswith("publish: "))
        # nothing to commit on an unchanged rerun -> no new commit, no crash
        self.do_publish()
        after2 = self._git("rev-list", "--count", "HEAD").stdout.strip()
        self.assertEqual(after2, after)


class NoMirrorTest(PublishBase):
    def test_no_mirror_configured_is_clean_noop(self):
        self.write_note("x")
        res = publish.run(config.load(), mirror=None, today="2026-07-14")
        self.assertEqual(res["status"], "no-mirror")
        self.assertEqual(res["published"], [])
        self.assertEqual(res["writes"], 0)
        self.assertFalse(self.mirror.exists())


class IfConfiguredGateTest(PublishBase):
    """The ``/brain-heal`` auto-step's opt-in gate (the source's HealGateTest —
    renamed here only to keep it distinct from the heal-cadence gate's own test
    class in ``tests/brain/test_heal_gate.py``)."""

    def test_if_configured_skips_when_not_configured(self):
        # default location is not "configured" -> --if-configured is a clean no-op
        self.write_note("x")
        rc = publish.main(["--if-configured", "--quiet"])
        self.assertEqual(rc, 0)
        self.assertFalse((self.home / "brains" / "shared-brain").exists())

    def test_if_configured_runs_when_env_set(self):
        self.assertIn("TASK_STATION_BRAIN_PUBLISH_MIRROR", ENV_KEYS)
        os.environ["TASK_STATION_BRAIN_PUBLISH_MIRROR"] = str(self.mirror)
        self.write_note("x")
        rc = publish.main(["--if-configured", "--quiet"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.mirror_notes(), ["x.md"])


# --------------------------------------------------------------------------- #
# ADDED — the secret gate. `test_block_secret` above proves ONE alternation of
# SECRET_RX fires. The contract this module actually carries is stronger and
# entirely invisible: publish must not own a secret pattern of its own, and every
# shape the vault linter recognises must block a publish too. A copied regex that
# fell one alternation behind would leak exactly the shapes the linter was taught
# most recently, on a green suite.
# --------------------------------------------------------------------------- #
class SecretSourceOfTruthTest(PublishBase):
    # one fixture per alternation of heal_lint.SECRET_RX, in its order:
    # <keyword>=<value> | ghp_… | sk-ant-… | BEGIN … PRIVATE KEY
    SHAPES = {
        "keyword-assignment": "api_key = ABCDEF0123456789abcdef",
        "forge-token": "token here: ghp_" + "A" * 24,
        "model-key": "key here: sk-ant-" + "a" * 16,
        "pem-header": "-----BEGIN RSA PRIVATE KEY-----",
    }

    def test_every_secret_shape_blocks_the_note(self):
        for label, body in self.SHAPES.items():
            self.write_note(f"blocked-{label}", body=body)
        res = self.do_publish()
        blocked = {n for n, _ in res["blocked"]}
        for label in self.SHAPES:
            with self.subTest(shape=label):
                name = f"blocked-{label}.md"
                self.assertIn(name, blocked)
                reasons = next(r for n, r in res["blocked"] if n == name)
                self.assertTrue(any("secret" in x.lower() for x in reasons))
        self.assertEqual(self.mirror_notes(), [])          # nothing leaked

    def test_the_pattern_set_is_heal_lints_and_is_read_live(self):
        """publish holds no SECRET_RX of its own: swapping the linter's pattern
        changes what publish blocks. This is the "one source of truth" comment in
        publish.py, as a test."""
        self.write_note("canary", body="zzz-canary appears here")
        self.assertEqual(self.do_publish()["blocked"], [])   # not a secret today
        self.assertEqual(self.mirror_notes(), ["canary.md"])

        self.addCleanup(setattr, heal_lint, "SECRET_RX", heal_lint.SECRET_RX)
        heal_lint.SECRET_RX = re.compile("zzz-canary")
        res = self.do_publish()
        self.assertEqual([n for n, _ in res["blocked"]], ["canary.md"])
        self.assertEqual(self.mirror_notes(), [])            # and swept back out

    def test_ordinary_prose_about_passwords_is_not_a_secret(self):
        self.write_note("policy", body="the password policy is documented elsewhere")
        res = self.do_publish()
        self.assertEqual(res["blocked"], [])
        self.assertEqual(self.mirror_notes(), ["policy.md"])


# --------------------------------------------------------------------------- #
# ADDED — the remaining branches of run()/main(), each of which is a silent
# clean-looking no-op in production.
# --------------------------------------------------------------------------- #
class BlockedNoteSweepTest(PublishBase):
    def test_a_note_that_becomes_dirty_is_swept_out_of_the_mirror(self):
        """The deletion sweep's comment claims "private / removed / BLOCKED"; the
        source covers the first two. Without the third a note that grows a leak
        after it was published stays readable in the mirror forever, while the run
        that blocked it reports success."""
        self.write_note("wasclean", body="fine")
        self.do_publish()
        self.assertEqual(self.mirror_notes(), ["wasclean.md"])

        self.write_note("wasclean", body="now with /Users/ada/x/y in it")
        res = self.do_publish()
        self.assertEqual([n for n, _ in res["blocked"]], ["wasclean.md"])
        self.assertEqual(self.mirror_notes(), [])
        self.assertIn("wasclean", res["removed"])


class EmptyVaultTest(PublishBase):
    def test_a_vault_without_a_notes_dir_is_a_clean_no_notes(self):
        bare = self.home / "bare-vault"
        bare.mkdir()
        res = publish.run({"vault": bare}, mirror=self.mirror, today="2026-07-14")
        self.assertEqual(res["status"], "no-notes")
        self.assertEqual(res["writes"], 0)
        self.assertFalse(self.mirror.exists())   # the mirror is not even created

    def test_main_returns_zero_when_the_vault_is_missing(self):
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.home / "gone")
        rc = publish.main(["--mirror", str(self.mirror), "--quiet"])
        self.assertEqual(rc, 0)
        self.assertFalse(self.mirror.exists())


class GitFailureTest(PublishBase):
    def test_a_real_git_failure_is_loud(self):
        """The no-op ("nothing to commit") path is swallowed on purpose; a genuine
        git failure must not be. Asserted against a directory that is not a repo,
        so the failure is deterministic rather than environment-dependent."""
        plain = self.home / "not-a-repo"
        plain.mkdir()
        with self.assertRaises(publish.PublishError):
            publish._git_commit_all(plain, "publish: 0 notes (2026-07-14)")


if __name__ == "__main__":
    unittest.main()
