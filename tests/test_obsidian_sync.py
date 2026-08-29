"""Tests for the opt-in Obsidian export (lib/obsidian_sync.py).

Every test runs against a THROWAWAY vault dir so nothing touches a real vault.
Covers: frontmatter correctness, write atomicity (no partial / leftover temp
file), vault-missing degrades without crashing, filename stability across a title
change, daily-note append idempotence per event, and the managed .base view not
being clobbered once the user edits it.
"""
import os, sys, glob, tempfile, shutil, unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import obsidian_sync


def _task(**over):
    """A representative stored-task dict; override any field per test."""
    t = {
        "id": "abcdef1234",
        "seq": 12,
        "title": "Fix the login bug",
        "summary": "Users can't log in after the token refresh change.",
        "status": "open",
        "color": "red",                       # → category label "bug"
        "effort": "m",
        "created_ts": 1_700_000_000.0,
        "updated_ts": 1_700_100_000.0,
        "projects": ["projectname", "otherproj"],
        "stories": [{"url": "https://ado/story/42", "desc": ""}],
        "prs": [{"url": "https://github.com/x/y/pull/9", "desc": "the fix"}],
        "goal": "Login works again for refreshed tokens.",
        "state": "NEXT: reproduce with an expired token.",
        "decisions": ["Refresh tokens server-side, not client-side."],
        "history": [{"ts": "2026-07-01T10:00:00+00:00", "text": "Root-caused the 401."}],
    }
    t.update(over)
    return t


class ObsidianSync(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="obs-vault-")
        self.pdir = obsidian_sync.plugin_dir(self.vault)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def _read_note(self, fname):
        with open(os.path.join(self.pdir, fname), encoding="utf-8") as f:
            return f.read()

    # --- frontmatter + body correctness ------------------------------------
    def test_frontmatter_and_body(self):
        fname = obsidian_sync.export_task(_task(), self.vault)
        self.assertEqual(fname, "12-fix-the-login-bug.md")
        text = self._read_note(fname)
        # everything lives under the exact plugin folder
        self.assertTrue(os.path.isdir(os.path.join(self.vault, "task-station")))
        # flat frontmatter fields — schema v2 carries the version + immutable uuid
        self.assertIn("managed-by: task-station", text)
        self.assertIn("schema-version: 2", text)
        self.assertIn('uuid: "abcdef1234"', text)
        self.assertIn("seq: 12", text)
        self.assertIn('status: "open"', text)
        self.assertIn('category: "bug"', text)
        self.assertIn('effort: "m"', text)
        self.assertIn('title: "Fix the login bug"', text)
        # list fields as YAML block lists
        self.assertIn("repos:\n  - \"projectname\"\n  - \"otherproj\"", text)
        self.assertIn("story:\n  - \"https://ado/story/42\"", text)
        self.assertIn("pr:\n  - \"https://github.com/x/y/pull/9\"", text)
        # created/updated are ISO local dates; closed empty while open (real close
        # date now comes from closed_ts, not the updated-while-closed proxy)
        self.assertRegex(text, r"created: \d{4}-\d{2}-\d{2}")
        self.assertRegex(text, r"updated: \d{4}-\d{2}-\d{2}")
        self.assertIn('closed: ""', text)
        # body sections + content
        self.assertIn("## Goal", text)
        self.assertIn("Login works again", text)
        self.assertIn("## State", text)
        self.assertIn("NEXT: reproduce", text)
        self.assertIn("## Summary", text)
        self.assertIn("## Decisions", text)
        # The bracketed number is the decision's 1-based index in the task's log — the
        # one `update --supersedes N` takes, so a reader holding the note can act on it.
        self.assertIn("- [1] Refresh tokens server-side", text)
        self.assertIn("## History", text)
        # dated entry, append order preserved (exact date is tz-local, so match loosely)
        self.assertRegex(text, r"- \d{4}-\d{2}-\d{2} — Root-caused the 401\.")

    def test_closed_date_from_closed_ts(self):
        # v2: the `closed` frontmatter date is the REAL close moment (closed_ts),
        # stable across later updates — not the old updated_ts proxy.
        text = self._read_note(obsidian_sync.export_task(
            _task(status="closed", closed_ts=1_700_200_000.0), self.vault))
        self.assertIn('status: "closed"', text)
        self.assertRegex(text, r"closed: \d{4}-\d{2}-\d{2}")
        self.assertNotIn('closed: ""', text)

    def test_closed_date_stable_across_post_close_update(self):
        # A later update (bumped updated_ts) must NOT move the closed date.
        closed_ts = 1_700_200_000.0
        first = self._read_note(obsidian_sync.export_task(
            _task(status="closed", closed_ts=closed_ts, updated_ts=closed_ts), self.vault))
        import re as _re
        stamp = _re.search(r"closed: (\d{4}-\d{2}-\d{2})", first).group(1)
        # resync after a much-later edit — closed date holds
        second = self._read_note(obsidian_sync.export_task(
            _task(status="closed", closed_ts=closed_ts, updated_ts=closed_ts + 5_000_000.0),
            self.vault))
        self.assertIn("closed: %s" % stamp, second)

    def test_empty_lists_render_flat(self):
        text = self._read_note(obsidian_sync.export_task(
            _task(projects=[], stories=[], prs=[]), self.vault))
        self.assertIn("repos: []", text)
        self.assertIn("story: []", text)
        self.assertIn("pr: []", text)

    def test_wikilink_unsafe_title_sanitized(self):
        text = self._read_note(obsidian_sync.export_task(
            _task(title="Fix [broken] #tag | thing"), self.vault))
        # brackets/#/pipe stripped from the stored wikilink-safe title
        self.assertIn('title: "Fix broken tag  thing"', text)

    # --- atomicity ----------------------------------------------------------
    def test_atomic_no_partial_or_temp_leftover(self):
        fname = obsidian_sync.export_task(_task(), self.vault)
        # no leftover temp files beside the note
        self.assertEqual(glob.glob(os.path.join(self.pdir, "*.tmp*")), [])
        # the note is complete (ends with the trailing newline the writer adds)
        text = self._read_note(fname)
        self.assertTrue(text.startswith("---\n"))
        self.assertTrue(text.endswith("\n"))
        self.assertIn("## History", text)   # last section present ⇒ not truncated

    # --- vault missing: no crash -------------------------------------------
    def test_missing_vault_no_crash(self):
        gone = os.path.join(self.vault, "does-not-exist")
        # returns None, writes nothing, does not raise
        self.assertIsNone(obsidian_sync.export_task(_task(), gone))
        self.assertFalse(os.path.exists(gone))

    def test_empty_vault_off_no_crash(self):
        self.assertIsNone(obsidian_sync.export_task(_task(), ""))

    # --- filename stability across a title change --------------------------
    def test_filename_stable_across_title_change(self):
        first = obsidian_sync.export_task(_task(), self.vault)
        self.assertEqual(first, "12-fix-the-login-bug.md")
        # rename the task; the note file must NOT change (no orphan)
        second = obsidian_sync.export_task(_task(title="Completely different name"), self.vault)
        self.assertEqual(second, first)
        # only one note on disk, and it holds the new title
        notes = [f for f in os.listdir(self.pdir) if f.endswith(".md")]
        self.assertEqual(notes, [first])
        self.assertIn('title: "Completely different name"', self._read_note(first))

    # --- daily-note append idempotence -------------------------------------
    def test_daily_note_append_and_idempotent(self):
        when = datetime(2026, 7, 3, 14, 30)
        heading = "## Claude sessions"
        line = obsidian_sync.append_daily_note(
            self.vault, "12-fix-the-login-bug", "closed", "Fix the login bug", heading, when=when)
        day_path = os.path.join(self.vault, "2026-07-03.md")
        self.assertTrue(os.path.exists(day_path))
        with open(day_path, encoding="utf-8") as f:
            text1 = f.read()
        self.assertIn(heading, text1)
        self.assertIn("- 14:30 · [[12-fix-the-login-bug]] — closed: Fix the login bug", text1)
        # same event again (same minute) ⇒ NO duplicate line
        obsidian_sync.append_daily_note(
            self.vault, "12-fix-the-login-bug", "closed", "Fix the login bug", heading, when=when)
        with open(day_path, encoding="utf-8") as f:
            text2 = f.read()
        self.assertEqual(text2, text1)
        self.assertEqual(text2.count("[[12-fix-the-login-bug]]"), 1)

    def test_daily_note_two_events_both_recorded(self):
        when = datetime(2026, 7, 3, 9, 0)
        heading = "## Claude sessions"
        obsidian_sync.append_daily_note(self.vault, "12-a", "checkpoint", "A", heading, when=when)
        obsidian_sync.append_daily_note(self.vault, "13-b", "closed", "B", heading, when=when)
        with open(os.path.join(self.vault, "2026-07-03.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("checkpoint: A", text)
        self.assertIn("closed: B", text)
        # one heading, two entries under it
        self.assertEqual(text.count(heading), 1)

    def test_daily_note_creates_heading_in_existing_file(self):
        day_path = os.path.join(self.vault, "2026-07-03.md")
        with open(day_path, "w", encoding="utf-8") as f:
            f.write("# My day\n\nSome existing notes.\n")
        when = datetime(2026, 7, 3, 8, 15)
        obsidian_sync.append_daily_note(self.vault, "12-x", "closed", "X", "## Claude sessions", when=when)
        with open(day_path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Some existing notes.", text)   # preserved
        self.assertIn("## Claude sessions", text)      # heading created
        self.assertIn("closed: X", text)

    # --- protected-root detection (sandbox/TCC awareness) ------------------
    def test_is_protected_vault_path(self):
        home = os.path.expanduser("~")
        # under each known macOS-protected root ⇒ True
        self.assertTrue(obsidian_sync.is_protected_vault_path(os.path.join(home, "Documents", "Vault")))
        self.assertTrue(obsidian_sync.is_protected_vault_path("~/Desktop/V"))
        self.assertTrue(obsidian_sync.is_protected_vault_path("~/Downloads/V"))
        self.assertTrue(obsidian_sync.is_protected_vault_path(
            os.path.join(home, "Library", "Mobile Documents", "iCloud~md~obsidian", "V")))
        # the root itself also counts
        self.assertTrue(obsidian_sync.is_protected_vault_path(os.path.join(home, "Documents")))
        # a tempdir (outside any protected root), empty, and None ⇒ False
        self.assertFalse(obsidian_sync.is_protected_vault_path(self.vault))
        self.assertFalse(obsidian_sync.is_protected_vault_path(""))
        self.assertFalse(obsidian_sync.is_protected_vault_path(None))
        # a sibling that merely shares a name prefix is NOT protected
        self.assertFalse(obsidian_sync.is_protected_vault_path(os.path.join(home, "Documents-archive", "V")))

    def test_export_reraises_permission_error_from_makedirs(self):
        # A sandbox denial creating the plugin folder must SURFACE (not swallow to
        # None) so the engine can track the task + warn.
        real_makedirs = os.makedirs

        def denied(path, **kw):
            raise PermissionError(13, "Operation not permitted")

        obsidian_sync.os.makedirs = denied
        try:
            with self.assertRaises(PermissionError):
                obsidian_sync.export_task(_task(), self.vault)
        finally:
            obsidian_sync.os.makedirs = real_makedirs

    # --- managed .base not clobbered ---------------------------------------
    def test_base_written_once_and_not_clobbered(self):
        obsidian_sync.export_task(_task(), self.vault)
        base_path = os.path.join(self.pdir, obsidian_sync.BASE_NAME)
        self.assertTrue(os.path.exists(base_path))
        # user edits the base view
        custom = "views:\n  - type: table\n    name: MY CUSTOM VIEW\n"
        with open(base_path, "w", encoding="utf-8") as f:
            f.write(custom)
        # a later export must NOT overwrite it
        obsidian_sync.export_task(_task(title="another change"), self.vault)
        with open(base_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), custom)


if __name__ == "__main__":
    unittest.main()
