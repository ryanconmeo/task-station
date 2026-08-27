"""The share vocabulary is ONE vocabulary — the retired one is gone everywhere.

ADDED with the publish/promote redesign (task-station #567).

The bug this guards against is not a crash. ``lib/brain/publish.py`` was opt-OUT
— every note published unless it said ``scope: private`` — while the schema
shipped to every new vault (``lib/brain/vault-scaffold/CLAUDE.md``) described the
field as ``scope: personal | team | private``, with ``personal`` as the default.
So the value that SOUNDS like keep-this-to-myself was the one that published, and
the only value that actually blocked was barely documented. Nothing malfunctioned;
two files simply disagreed about who could read a note, and a reader believed the
wrong one.

A half-migrated vocabulary reproduces exactly that. So this scans the shipped
code, the vault schema, the brain skills, the org-brain templates and the brain
doc for the retired words and requires ZERO hits — and, because a scan that finds
nothing because it read nothing is the classic green-on-empty failure, it also
asserts that files were actually read and that the REPLACEMENT vocabulary is
present in what it read.

Deliberately NOT flagged: ``scope`` as an ordinary English word. A scan
descriptor (``"scope": "group-display-names-only"`` in ``brain.org_setup``) and
prose like "scoped to this one repo's token" are not share vocabulary, so the
patterns below match ``scope:`` only when it carries one of the three retired
VALUES. Same for ``canonical``, which is matched on a word boundary so the
retired term ``canon`` (the org brain's old name) is caught and the ordinary
adjective is not.
"""
import re
import unittest

from tests.brain.base import LIB

REPO = LIB.parent

# Every tree that tells a reader — human or model — who can read a note.
SCAN_GLOBS = (
    ("lib/brain", "**/*.py"),
    ("lib/brain/vault-scaffold", "**/*"),
    ("skills", "brain*/**/*"),
    ("templates/org-brain", "**/*"),
)
SCAN_FILES = ("docs/BRAIN.md",)

# The retired share vocabulary. `scope:` alone is not on the list — only `scope:`
# carrying one of the three retired values, so ordinary English survives.
RETIRED = {
    "scope-value": re.compile(r"scope:\s*[\"']?(?:personal|team|private)\b"),
    "scope-flag": re.compile(r"--scope\b"),
    "non-team": re.compile(r"non[-_]team"),
    "canon": re.compile(r"\bcanon\b"),
}

# The vocabulary that replaced it. If the scan cannot find this, it did not read
# the files it thinks it read.
SENTINEL = "publish: true"


def _scan_paths():
    seen, out = set(), []
    for rel, pattern in SCAN_GLOBS:
        root = REPO / rel
        if not root.exists():
            continue
        for p in sorted(root.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.add(p)
                out.append(p)
    for rel in SCAN_FILES:
        p = REPO / rel
        if p.is_file() and p not in seen:
            seen.add(p)
            out.append(p)
    return out


class ShareVocabularyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = _scan_paths()
        cls.texts = {p: p.read_text(errors="ignore") for p in cls.paths}

    # --- the guard's own guards: a scan of nothing must not read as clean ----
    def test_the_scan_actually_read_files(self):
        self.assertGreater(len(self.paths), 0, "the vocabulary scan read no files")
        # every named tree contributed, or a moved directory silences the guard
        for rel, _ in SCAN_GLOBS:
            with self.subTest(tree=rel):
                self.assertTrue(any(str(p).startswith(str(REPO / rel)) for p in self.paths),
                                f"{rel} contributed no files to the scan")
        for rel in SCAN_FILES:
            with self.subTest(file=rel):
                self.assertIn(REPO / rel, self.paths)

    def test_the_replacement_vocabulary_is_present_in_what_was_scanned(self):
        hits = [p for p, t in self.texts.items() if SENTINEL in t]
        self.assertGreater(len(hits), 0,
                           f"no scanned file mentions {SENTINEL!r} — the scan is "
                           "reading the wrong tree, or the migration did not land")
        # the schema new installs get, and the doc the org reads, both say it
        for rel in ("lib/brain/vault-scaffold/CLAUDE.md",
                    "templates/org-brain/routing-spec.md"):
            with self.subTest(file=rel):
                self.assertIn(SENTINEL, self.texts[REPO / rel])

    def test_promote_true_is_documented_too(self):
        hits = [p for p, t in self.texts.items() if "promote: true" in t]
        self.assertGreater(len(hits), 0)

    # --- the guard itself ----------------------------------------------------
    def test_no_retired_share_vocabulary_survives(self):
        found = []
        for label, rx in RETIRED.items():
            for path, text in self.texts.items():
                for n, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        rel = path.relative_to(REPO)
                        found.append(f"{label}  {rel}:{n}: {line.strip()[:100]}")
        self.assertEqual(found, [], "retired share vocabulary still shipped:\n  "
                                    + "\n  ".join(found))

    def test_the_scan_fails_when_a_retired_term_is_reintroduced(self):
        """The negative control for the whole file. ``test_no_retired_...``
        passing proves nothing unless the same walk, over the same corpus, FAILS
        when one real shipped file grows one bad line. Run against a poisoned
        COPY of the scanned text — nothing on disk is touched."""
        victim = REPO / "lib/brain/heal_tier.py"
        self.assertIn(victim, self.texts)
        poisoned = dict(self.texts)
        poisoned[victim] = self.texts[victim] + "\n# scope: team\n"
        hits = [(p, rx_name)
                for rx_name, rx in RETIRED.items()
                for p, text in poisoned.items()
                if rx.search(text)]
        self.assertEqual(hits, [(victim, "scope-value")])

    def test_each_retired_pattern_would_actually_fire(self):
        """The patterns are asserted against known-bad text, so a typo in one of
        them cannot make this suite pass by matching nothing anywhere."""
        samples = {
            "scope-value": "scope: personal",
            "scope-flag": "brain.search new x --scope team",
            "non-team": "pass --non-team to opt it in",
            "canon": "the canon repo",
        }
        for label, rx in RETIRED.items():
            with self.subTest(pattern=label):
                self.assertTrue(rx.search(samples[label]), label)

    def test_ordinary_english_is_not_flagged(self):
        """`scope` and `canonical` still have innocent uses, and a guard that
        forced them out would be paid for in worse prose everywhere else."""
        for text in ('"scope": "group-display-names-only"',
                     "ACEs scoped to this one repo's token",
                     "the canonical note key order",
                     "team-scoped imperative rules"):
            for label, rx in RETIRED.items():
                with self.subTest(text=text, pattern=label):
                    self.assertIsNone(rx.search(text))


if __name__ == "__main__":
    unittest.main()
