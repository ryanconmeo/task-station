"""THE ORG-SETUP WIZARD — four read-only scans, six answers, one valid profile.

WHAT THESE TESTS ARE ACTUALLY PROTECTING. The wizard is the first thing a leader
at a company this toolchain has never heard of ever runs, which makes three of
its properties load-bearing in a way that ordinary correctness is not:

  1. **THE DIRECTORY SCAN CANNOT READ A USER OBJECT.** Not "does not" — *cannot*.
     A directory holds people, and a scan that merely declines to look at them is
     one refactor away from looking at them. So the tests assert the refusal is
     at the door (`screen_group_entries` RAISES on a user attribute and on a
     Graph user type) and that the scan below it accepts strings only, which is
     what makes the crossing structurally impossible rather than well-behaved.

     **And they assert where that guarantee stops.** It covers OBJECTS. A bare
     string has no attribute to refuse and no type to check, so a person's name
     typed into one reaches the profile — and no heuristic is applied, because a
     person's name and a department's name are the same shape and a guess wrong
     in either direction is worse than the gap. What is asserted instead is that
     a bare string never passes SILENTLY: every one is counted, the count reaches
     `provenance.directory.unscreened_entries` and the printed summary, and a
     profile that fails to state it does not validate.

  2. **AN INVALID PROFILE NEVER REACHES DISK.** A config the platform refuses to
     parse does not degrade to default rules — it means NO rules. So the negative
     tests matter more than the happy path: `write_profile` must raise *and leave
     no file*, because a half-written profile on disk reads as configured.

  3. **THE MIRROR PATTERN IS A TEMPLATE, NEVER A LITERAL** (binding ruling,
     2026-08-15). A ruling that lives only in prose is a ruling that gets typed
     around, so the test asserts the *schema* rejects a literal — the mechanism,
     not the manners.

And one property that is about the product rather than the code: **every word in
the emitted profile is traceable to the fixture or to shipped generic
vocabulary**. That is the positive form of "carries no fingerprint of the
organisation this toolchain grew up in", and it is the form worth writing: a
denylist of foreign org terms is itself a fingerprint (which is why the repo's
push gate keeps its pattern list out of the tree), and it can only catch the
names someone thought to list. A provenance check catches any literal that came
from neither input. It runs against the committed golden, not a temp-dir run,
because a scan over a file that was never written is trivially clean.

No test here needs a credential, a network, or a database: every scan is a pure
function over the fixture bundle in `tests/fixtures/fake-org/`.
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.brain.base import BrainTestCase, LIB  # noqa: F401  (LIB pins sys.path)

import brain.org_setup as org_setup  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fake-org"
BUNDLE = FIXTURE / "scan-bundle.json"
ANSWERS = FIXTURE / "answers.json"
GOLDEN = FIXTURE / "config.json"



def shipped_vocabulary():
    """Every word the wizard is allowed to contribute on its own: the naming
    contract's generic areas (with every pack), the role-tier vocabulary, the
    area hints, and the four words `_case_style` can name a convention with. Read
    off the module, so a new shipped word is allowed the moment it ships and a
    word from nowhere never is."""
    words = set(org_setup.generic_areas(None))
    for pack in org_setup.vertical_packs():
        words |= set(org_setup.generic_areas(pack))
    words |= set(org_setup.ROLE_TIER_WORDS)
    words |= set(org_setup.AREA_HINTS)
    for hinted in org_setup.AREA_HINTS.values():
        words |= set(hinted)
    words |= {"kebab", "title", "upper", "mixed", "unknown"}
    return words


def strings_in(node):
    """Every string value anywhere in the profile. Keys are not walked — they are
    fixed by the schema, and it is the VALUES that could carry a name."""
    if isinstance(node, dict):
        for value in node.values():
            for s in strings_in(value):
                yield s
    elif isinstance(node, list):
        for value in node:
            for s in strings_in(value):
                yield s
    elif isinstance(node, str):
        yield node


def bundle():
    return json.loads(BUNDLE.read_text())


def answers():
    return json.loads(ANSWERS.read_text())


# ============================================================ SCAN 1 ====
class DatabaseScanReadsSchemasAndHeaders(unittest.TestCase):
    def test_system_schemas_never_become_domains(self):
        """`dbo` and `INFORMATION_SCHEMA` name the database's own plumbing. The
        second is the one that matters: it normalises to `information-schema`, so
        a drop-list held in raw spelling lets the most universal system schema
        there is straight into an org's registry."""
        out = org_setup.scan_database(
            ["dbo", "sys", "INFORMATION_SCHEMA", "db_owner", "Billing"], [])
        self.assertEqual(out["domains"], ["billing"])

    def test_a_header_word_counts_only_when_it_recurs(self):
        """A schema name is a deliberate partition, so once is evidence. A header
        word is prose — counting it once would make every adjective a developer
        ever typed a business domain."""
        headers = [
            {"file": "a.sql", "text": "-- Dispatch: shipment legs"},
            {"file": "b.sql", "text": "-- Billing: shipment charges"},
            {"file": "c.sql", "text": "-- Billing: a thoroughly idiosyncratic remark"},
        ]
        out = org_setup.scan_database([], headers)
        self.assertIn("shipment", out["domains"])
        self.assertNotIn("idiosyncratic", out["domains"])

    def test_only_comment_lines_are_read(self):
        """DDL below the header names tables, not domains. Reading it would put
        the physical schema into the vocabulary."""
        out = org_setup.scan_database([], [
            {"file": "a.sql", "text": "-- Billing: invoices\nCREATE TABLE tblXyzzy (id INT);"},
            {"file": "b.sql", "text": "-- Billing: credit notes\nCREATE TABLE tblXyzzy (id INT);"},
        ])
        self.assertIn("billing", out["domains"])
        self.assertNotIn("xyzzy", out["domains"])

    def test_the_only_statement_is_a_constant_read(self):
        """Read-only is structural, not a promise: the whole database side of the
        wizard is one module constant with no interpolation and no parameters."""
        sql = org_setup.INFORMATION_SCHEMA_SQL
        self.assertTrue(sql.upper().startswith("SELECT"))
        self.assertNotIn(";", sql)
        self.assertNotIn("%", sql)
        self.assertNotIn("{", sql)
        for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "EXEC"):
            self.assertNotIn(verb, sql.upper())


# ============================================================ SCAN 2 ====
class DirectoryScanReadsGroupDisplayNamesOnly(unittest.TestCase):
    """The group-only restriction, asserted from both sides: what the door
    refuses, and what the scan below it is even able to accept."""

    #: Four object-shaped attacks, one per way a directory response can hand over
    #: a person: the classic identifier, a mail-only record, a record whose only
    #: user-ish field is a job title, and one that carries NO user attribute at
    #: all and is recognisable only by its declared type.
    USER_OBJECT_ATTACKS = (
        {"displayName": "Ada Lovelace", "userPrincipalName": "ada@example.com"},
        {"displayName": "Ada Lovelace", "mail": "ada@example.com"},
        {"displayName": "Ada Lovelace", "jobTitle": "Analyst"},
        {"@odata.type": "#microsoft.graph.user", "displayName": "Ada Lovelace"},
    )

    def test_every_object_shaped_user_record_is_refused_at_the_door(self):
        """The last case is the one that needs saying: an entry can declare itself
        a user and carry nothing but a name. The type alone is enough — the screen
        must never depend on an attribute being PRESENT to notice it is holding a
        person's record."""
        for attack in self.USER_OBJECT_ATTACKS:
            for door in (org_setup.screen_group_entries,
                         org_setup.read_group_display_names):
                with self.assertRaises(org_setup.DirectoryScopeError):
                    door([attack])

    def test_a_group_object_is_projected_down_to_its_display_name(self):
        """Everything else about a group is discarded HERE, not downstream. Past
        this function the data is a list of strings, so there is nothing left for
        a later change to accidentally start reading."""
        names = org_setup.read_group_display_names([
            {"@odata.type": "#microsoft.graph.group", "displayName": "SG-Billing-Owners",
             "id": "0000", "description": "irrelevant", "members": ["x"]},
            "SG-Billing-Readers",
        ])
        self.assertEqual(names, ["SG-Billing-Owners", "SG-Billing-Readers"])
        self.assertTrue(all(isinstance(n, str) for n in names))

    def test_a_bare_string_is_counted_because_it_cannot_be_screened(self):
        """A bare string has no attribute to refuse and no type to check. It is
        passed through — and TALLIED, which is the whole mechanism: the number is
        what stops an un-inspectable entry from looking inspected."""
        out = org_setup.screen_group_entries([
            {"@odata.type": "#microsoft.graph.group", "displayName": "SG-Billing-Owners"},
            "SG-Billing-Readers",
        ])
        self.assertEqual(out["names"], ["SG-Billing-Owners", "SG-Billing-Readers"])
        self.assertEqual(out["screened"], 1)
        self.assertEqual(out["unscreened"], 1)

    def test_no_person_shape_heuristic_is_applied(self):
        """A person's name and a department's name are the same shape, so the
        screen treats them identically and says so with the count. Guessing would
        be worse than the gap in both directions: a wrongly-refused group silently
        loses vocabulary, and a wrongly-admitted person is the very leak the count
        is there to declare."""
        person = org_setup.screen_group_entries(["Dana Okonkwo"])
        department = org_setup.screen_group_entries(["Fleet Maintenance"])
        self.assertEqual(person["unscreened"], department["unscreened"])
        self.assertEqual(person["screened"], department["screened"])

    def test_the_scan_itself_accepts_nothing_but_strings(self):
        """The type at the boundary is `str`. Hand the scan a user object and it
        is a TypeError here, not a privacy incident later."""
        with self.assertRaises(TypeError):
            org_setup.scan_directory([{"displayName": "SG-Billing-Owners"}])

    def test_tiers_departments_and_function_words_come_apart(self):
        out = org_setup.scan_directory([
            "SG-Billing-Approvers", "SG-Billing-Owners", "SG-Billing-Readers",
            "SG-Dispatch-Owners", "SG-Dispatch-Contributors",
            "SG-Warehouse-Readers", "SG-Fleet-Members", "SG-People-Admins",
        ])
        self.assertEqual(out["function_words"], ["sg"])
        self.assertIn("billing", out["departments"])
        self.assertIn("owners", out["role_tiers"])
        self.assertNotIn("owners", out["departments"])
        self.assertEqual(out["scope"], "group-display-names-only")


# ==================================================== SCANS 3 AND 4 ====
class ForgeAndWikiScans(unittest.TestCase):
    def test_repo_shape_words_are_not_systems(self):
        """Every org has an `…-api` and a `…-web`. Neither is a domain, and a
        repo word has to recur before it counts as a system at all."""
        out = org_setup.scan_forge(
            ["dispatch-api", "dispatch-web", "one-off-spike"], ["Billing Platform"])
        self.assertIn("dispatch", out["domains"])
        self.assertIn("billing-platform", out["domains"])
        self.assertNotIn("api", out["domains"])
        self.assertNotIn("spike", out["domains"])

    def test_wiki_reads_the_leading_segment_and_the_habit(self):
        out = org_setup.scan_wiki([
            "Billing - Invoice approval", "Billing - Credit notes",
            "Dispatch - Route planning", "Dispatch - Driver handover",
            "Fleet - Maintenance windows",
        ])
        self.assertEqual(out["separator"], " - ")
        self.assertEqual(out["leading_segments"], ["billing", "dispatch"])
        self.assertEqual(out["case"], "title")


# ================================================== THE SIX ANSWERS ====
class TheSixUndiscoverableAnswers(unittest.TestCase):
    def test_all_six_are_required(self):
        """Each one is a decision written down nowhere a scan can reach. Guessing
        any of them would be worse than asking, because a wrong guess is
        invisible."""
        for missing in org_setup.LEADER_ANSWERS:
            a = answers()
            a.pop(missing)
            with self.assertRaises(org_setup.ProfileInvalid) as ctx:
                org_setup.validate_answers(a)
            self.assertIn(missing, str(ctx.exception))

    def test_ado_needs_a_project_where_github_does_not(self):
        a = answers()
        a["forge_kind"] = "ado"
        a["forge_url"] = "https://example.com/your-org"
        with self.assertRaises(org_setup.ProfileInvalid) as ctx:
            org_setup.validate_answers(a)
        self.assertIn("forge_project", str(ctx.exception))
        a["forge_project"] = "Knowledge"
        self.assertIs(org_setup.validate_answers(a), a)

    def test_an_unknown_vertical_pack_is_refused(self):
        a = answers()
        a["vertical_pack"] = "not-a-shipped-pack"
        with self.assertRaises(org_setup.ProfileInvalid):
            org_setup.validate_answers(a)

    def test_every_error_is_reported_at_once(self):
        """A leader fixing one field per round trip re-runs four scans each time,
        so one report has to name everything that is wrong."""
        a = answers()
        a["org_slug"] = "Not A Slug"
        a["promotion_approvers"] = []
        with self.assertRaises(org_setup.ProfileInvalid) as ctx:
            org_setup.validate_answers(a)
        self.assertGreaterEqual(len(ctx.exception.errors), 2)


class TheMirrorPatternIsATemplate(unittest.TestCase):
    """Binding ruling, 2026-08-15 — mechanized rather than documented."""

    def test_a_literal_is_refused_by_the_resolver(self):
        with self.assertRaises(org_setup.ProfileInvalid) as ctx:
            org_setup.resolve_mirror_template("some-persons-brain")
        self.assertIn("literal", str(ctx.exception))

    def test_a_literal_is_refused_by_the_schema_too(self):
        """The resolver is a function anyone can route around; the schema is the
        gate every profile passes through."""
        a = answers()
        a["mirror_template"] = "some-persons-brain"
        with self.assertRaises(org_setup.ProfileInvalid):
            org_setup.validate_answers(a)

    def test_it_resolves_from_the_host_identity(self):
        self.assertEqual(
            org_setup.resolve_mirror_template(org_setup.DEFAULT_MIRROR_TEMPLATE, "jdoe"),
            "jdoe-brain")


# ================================================ VALIDATE, THEN WRITE ====
class AnInvalidProfileNeverReachesDisk(BrainTestCase):
    """A config the platform refuses to parse means NO rules, not default rules —
    so the ordering (validate, THEN open the file) is the property under test,
    not merely that a validator exists."""

    def _profile(self):
        return org_setup.build_profile(answers(), org_setup.run_scans(bundle()))

    def test_write_profile_raises_and_writes_nothing(self):
        target = Path(self.home) / "out" / "config.json"
        broken = self._profile()
        del broken["forge"]
        with self.assertRaises(org_setup.ProfileInvalid):
            org_setup.write_profile(target, broken)
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())

    def test_a_domain_mapped_to_an_unknown_area_is_refused(self):
        """An unknown area is not a visible error — it is a filter that quietly
        stops matching. Refusing is the only way it stays visible."""
        broken = self._profile()
        broken["vocabulary"]["domains"]["billing"] = "not-an-area"
        with self.assertRaises(org_setup.ProfileInvalid) as ctx:
            org_setup.validate_profile(broken)
        self.assertIn("not a known area", str(ctx.exception))

    def test_a_valid_profile_round_trips(self):
        target = Path(self.home) / "out" / "config.json"
        written = org_setup.write_profile(target, self._profile())
        self.assertEqual(written, target)
        self.assertEqual(json.loads(target.read_text()), self._profile())

    def test_an_unmapped_domain_is_listed_not_guessed(self):
        """The org half of the naming registry is PR-gated so a person assigns an
        area. A guess would land a domain the org schema rejects."""
        profile = self._profile()
        self.assertIn("fleet", profile["vocabulary"]["unmapped_domains"])
        self.assertNotIn("fleet", profile["vocabulary"]["domains"])


# ================================================ END TO END, NO CREDS ====
class TheWizardRunsAgainstAFakeOrg(unittest.TestCase):
    def test_end_to_end_reproduces_the_committed_profile(self):
        """Byte for byte against the golden. Sorted keys and a stable tie-break
        inside every scan are what make that possible — and what make a real
        change to the wizard show up as a readable diff."""
        profile, written = org_setup.run(answers(), bundle(), out=None, dry_run=True)
        self.assertIsNone(written)
        self.assertEqual(org_setup.render(profile), GOLDEN.read_text())

    def test_it_needs_no_credential_network_or_database(self):
        """Every scan is a pure function over the bundle. The bundle is the whole
        point: whoever has access produces it once, and the wizard reads it."""
        scans = org_setup.run_scans(bundle())
        self.assertEqual(sorted(scans), ["database", "directory", "forge", "wiki"])
        for name, scan in scans.items():
            self.assertTrue(scan["read_only"], name)
            self.assertGreater(scan["inputs"], 0, name)

    def test_a_name_in_a_bare_string_reaches_the_profile_and_is_declared(self):
        """THE LIMIT, ASSERTED RATHER THAN HIDDEN. A person's name typed into a
        bare string does reach the emitted profile — the screen cannot see it, and
        nothing guesses. What must be true is that the profile SAYS so: the tally
        rises, and the wizard prints it in front of whoever approves the profile.

        The provenance test in this file cannot catch this case, deliberately:
        those words DID come from the supplied input, which is exactly what it
        asserts. This is the test that covers it."""
        b = bundle()
        clean = org_setup.build_profile(answers(), org_setup.run_scans(b))
        before = clean["provenance"]["directory"]["unscreened_entries"]

        b["directory"]["groups"] = list(b["directory"]["groups"]) + ["Dana Okonkwo"]
        scans = org_setup.run_scans(b)
        leaky = org_setup.build_profile(answers(), scans)

        # the limit itself — the name is in the artifact, unguessed at
        self.assertIn("okonkwo", leaky["vocabulary"]["departments"])
        # …and the artifact declares that an entry went un-inspected
        self.assertEqual(leaky["provenance"]["directory"]["unscreened_entries"], before + 1)
        # …and a human reading the wizard's output is told, not left to notice
        summary = org_setup._summary(leaky, scans)
        self.assertIn("bypassed the object screen", summary)
        self.assertIn("bare strings", summary)

    def test_a_profile_that_does_not_state_the_tally_fails_validation(self):
        """An unstated count is silence, and silence is the failure this closes.
        `scan_directory` defaults the tally to None rather than 0 precisely so a
        caller that skipped the screen cannot claim everything was screened."""
        scans = org_setup.run_scans(bundle())
        scans["directory"] = org_setup.scan_directory(["SG-Billing-Owners"])
        with self.assertRaises(org_setup.ProfileInvalid) as ctx:
            org_setup.build_profile(answers(), scans)
        self.assertIn("unscreened_entries", str(ctx.exception))

    def test_an_all_object_directory_reports_a_zero_tally(self):
        """Zero is a claim somebody made, and it has to be reachable — otherwise
        the count would only ever read as "some", and a reviewer would learn to
        ignore it."""
        b = bundle()
        b["directory"]["groups"] = [
            {"@odata.type": "#microsoft.graph.group", "displayName": n}
            for n in org_setup.read_group_display_names(b["directory"]["groups"])]
        scans = org_setup.run_scans(b)
        profile = org_setup.build_profile(answers(), scans)
        self.assertEqual(profile["provenance"]["directory"]["unscreened_entries"], 0)
        self.assertNotIn("bare strings", org_setup._summary(profile, scans))

    def test_a_bundle_carrying_a_user_object_is_refused(self):
        """The refusal is not only in the collector — a hand-written bundle goes
        through the same door."""
        b = bundle()
        b["directory"]["groups"].append(
            {"displayName": "Ada Lovelace", "mail": "ada@example.com"})
        with self.assertRaises(org_setup.DirectoryScopeError):
            org_setup.run_scans(b)

    def test_every_word_in_the_emitted_profile_is_traceable(self):
        """The positive form of "carries no foreign org's fingerprint", and the
        stronger one: a denylist can only catch the names someone thought to
        list, and the list is itself a fingerprint. Here every word must come
        from the fixture the leader supplied or from vocabulary this toolchain
        ships — a literal from anywhere else fails, named."""
        self.assertTrue(GOLDEN.exists(), "the golden profile must be committed")
        text = GOLDEN.read_text()
        self.assertGreater(len(text), 0)

        allowed = shipped_vocabulary()
        allowed |= set(org_setup._words(ANSWERS.read_text()))
        allowed |= set(org_setup._words(BUNDLE.read_text()))

        untraceable = sorted({
            word
            for value in strings_in(json.loads(text))
            for word in org_setup._words(value)
            if word not in allowed
        })
        self.assertEqual(untraceable, [], "emitted profile carries words that came "
                                          "from neither the fixture nor shipped vocabulary")

    def test_the_profile_is_what_brain_init_consumes(self):
        """The artifact must be directly usable by `brain-init --profile`, or the
        wizard has produced a document rather than a configuration."""
        import brain.init_home as init_home
        cfg = init_home._apply_profile({}, json.loads(GOLDEN.read_text()))
        self.assertEqual(cfg["forge_kind"], "github")
        self.assertEqual(cfg["forge_org"], "https://github.com/northwind-logistics")
        self.assertEqual(cfg["forge_repo"], "northwind-org-brain")
        self.assertEqual(cfg["org_label"], "Northwind Brain")
        self.assertTrue(cfg["inject_keywords"])


class TheCliRunsTheWholeWizard(BrainTestCase):
    def _main(self, argv):
        """Run the CLI with its streams captured — a suite that prints one org's
        summary per run trains everyone to stop reading the output."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = org_setup.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_it_writes_a_validated_profile(self):
        out = Path(self.home) / "emitted" / "config.json"
        code, stdout, _ = self._main(["--scan-bundle", str(BUNDLE),
                                      "--answers", str(ANSWERS), "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(out.read_text(), GOLDEN.read_text())
        self.assertIn("group display names only", stdout)

    def test_a_broken_answers_file_exits_nonzero_and_writes_nothing(self):
        bad = Path(self.home) / "bad-answers.json"
        a = answers()
        a["mirror_template"] = "some-persons-brain"
        bad.write_text(json.dumps(a))
        out = Path(self.home) / "emitted" / "config.json"
        code, _, stderr = self._main(["--scan-bundle", str(BUNDLE),
                                      "--answers", str(bad), "--out", str(out)])
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())
        self.assertIn("mirror_template", stderr)


class TheBoardRoutesToTheWizardWithoutDrifting(unittest.TestCase):
    """`task-station org-setup` restates the wizard's flags on the board's own
    parser, because argparse cannot capture a leading `--flag` into a REMAINDER
    positional and `org-setup -- --scan-bundle …` is a UX nobody types correctly
    the first time. A restated flag set is a thing that drifts — silently, since
    a forgotten flag is simply never forwarded and the wizard sees a default. So
    the two sets are compared, in both directions."""

    def _board(self):
        import importlib
        return importlib.import_module("board.cmds.manage")

    def test_the_flag_sets_are_identical(self):
        wizard = set()
        for action in org_setup.build_parser()._actions:
            wizard.update(o for o in action.option_strings if o.startswith("--"))
        wizard.discard("--help")
        board = {flag for _, flag, _ in self._board().ORG_SETUP_FLAGS}
        self.assertEqual(board, wizard)

    def test_the_namespace_rebuilds_the_wizard_argv(self):
        class NS(object):
            scan_bundle, answers, out, dry_run = "b.json", "a.json", "c.json", True

        self.assertEqual(
            self._board().org_setup_argv(NS()),
            ["--scan-bundle", "b.json", "--answers", "a.json",
             "--out", "c.json", "--dry-run"])

    def test_unset_flags_are_left_out_rather_than_forwarded_empty(self):
        """An empty `--out ''` would make the wizard write to a path that is not
        a path; absent means absent."""
        class Bare(object):
            scan_bundle, answers, out, dry_run = "b.json", None, None, False

        self.assertEqual(self._board().org_setup_argv(Bare()), ["--scan-bundle", "b.json"])


if __name__ == "__main__":
    unittest.main()
