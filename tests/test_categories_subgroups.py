# tests/test_categories_subgroups.py
"""WS11 emergent sub-groups — the pure detection core categories.detect_subgroups.
Within each category's task set it clusters DISTINCTIVE, non-stopword tokens that
appear in >= 3 of that category's tasks, assigns each task to at most one group
(highest in-category frequency; ties alphabetical), and keeps only groups that
still hold >= 3 members. Deterministic: same input -> same output."""
import importlib, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import categories  # noqa: E402


def _item(title, slug=None):
    import obsidian_sync
    return {"title": title, "slug": slug if slug is not None else obsidian_sync.slugify(title)}


class _Clean(unittest.TestCase):
    """Shipped taxonomy under an empty temp HOME (not the dev's config overrides)."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cat-subg-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        importlib.reload(categories)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(categories)


class Detect(_Clean):
    def test_motivating_hammerspoon_example(self):
        # 4 hammerspoon tasks + 2 unrelated personal tasks -> exactly one group.
        personal = [
            _item("Hammerspoon window tiling"),
            _item("Hammerspoon reload config"),
            _item("Hammerspoon spoon install"),
            _item("Hammerspoon menubar clock"),
            _item("Buy groceries for the week"),
            _item("Plan summer vacation trip"),
        ]
        groups = categories.detect_subgroups({"pink": personal})
        self.assertEqual(set(groups), {"pink"})
        self.assertEqual(set(groups["pink"]), {"hammerspoon"})
        members = groups["pink"]["hammerspoon"]
        self.assertEqual(len(members), 4)
        titles = [m["title"] for m in members]
        self.assertNotIn("Buy groceries for the week", titles)

    def test_below_threshold_no_group(self):
        # Only 2 tasks share a token -> under the >= 3 threshold, no group.
        personal = [_item("Hammerspoon window tiling"),
                    _item("Hammerspoon reload config"),
                    _item("Something else entirely")]
        self.assertEqual(categories.detect_subgroups({"pink": personal}), {})

    def test_stopword_token_never_groups(self):
        # 'update' is a stopword task-word -> never forms a group even at >= 3.
        green = [_item("Update the login page"),
                 _item("Update the billing flow"),
                 _item("Update the search index")]
        self.assertEqual(categories.detect_subgroups({"green": green}), {})

    def test_generic_token_across_categories_not_distinctive(self):
        # 'release' (not a stopword) appears in >= 3 personal tasks AND across the
        # feature category -> fails the < 10% distinctiveness test, groups nowhere.
        pink = [_item("Release quarterly newsletter"),
                _item("Release annual budget"),
                _item("Release conference schedule")]
        green = [_item("Release alpha"),
                 _item("Release beta"),
                 _item("Release gamma"),
                 _item("Release delta"),
                 _item("Release omega")]
        groups = categories.detect_subgroups({"pink": pink, "green": green})
        self.assertEqual(groups, {})

    def test_single_assignment_highest_frequency_wins(self):
        # docker appears in 4 tasks, kubernetes in 3; tasks holding both go to the
        # more-frequent docker, so kubernetes drops below 3 and dissolves.
        black = [_item("docker kubernetes deploy"),
                 _item("docker kubernetes setup"),
                 _item("docker kubernetes config"),
                 _item("docker registry push")]
        groups = categories.detect_subgroups({"black": black})
        self.assertEqual(set(groups["black"]), {"docker"})
        self.assertEqual(len(groups["black"]["docker"]), 4)

    def test_tie_broken_alphabetically(self):
        # docker and kubernetes both appear in the same 3 tasks (freq tie) ->
        # alphabetical 'docker' wins the assignment for every task.
        black = [_item("docker kubernetes deploy"),
                 _item("docker kubernetes rollout"),
                 _item("docker kubernetes config")]
        groups = categories.detect_subgroups({"black": black})
        self.assertEqual(set(groups["black"]), {"docker"})
        self.assertEqual(len(groups["black"]["docker"]), 3)

    def test_deterministic(self):
        personal = [_item("Hammerspoon window tiling"),
                    _item("Hammerspoon reload config"),
                    _item("Hammerspoon spoon install")]
        by_cat = {"pink": personal}
        a = categories.detect_subgroups(by_cat)
        b = categories.detect_subgroups(by_cat)
        self.assertEqual(a, b)

    def test_empty_input(self):
        self.assertEqual(categories.detect_subgroups({}), {})

    def test_short_tokens_ignored(self):
        # tokens under length 4 (e.g. 'ci') never cluster even if frequent.
        black = [_item("ci run one"), _item("ci run two"), _item("ci run job")]
        self.assertEqual(categories.detect_subgroups({"black": black}), {})

    def test_category_self_token_never_groups(self):
        # A category's own tag/label token ('feature' from green's FEATURE tag) must
        # never seed a sub-group WITHIN that category — it would be redundant with the
        # category hub itself (live run produced categories/migration/migration.md).
        green = [_item("Feature flag rollout"),
                 _item("Feature toggle polish"),
                 _item("Feature parity audit")]
        self.assertEqual(categories.detect_subgroups({"green": green}), {})

    def _filler(self, n):
        # n tasks whose tokens are all globally unique -> none ever clusters; used to
        # dilute the OUTSIDE pool so a cross-category token still passes distinctiveness,
        # isolating the plurality rule as the sole discriminator.
        return [_item("filleritem%04d" % i) for i in range(n)]

    def test_plurality_picks_single_category(self):
        # 'story' is a valid candidate (>=3, distinctive, non-stopword, non-self) in
        # BOTH design and infra, but occurs MORE in design -> it may group only there.
        design = [_item("Story %s" % w) for w in ("board", "flow", "arcs", "tile", "grid")]
        infra = [_item("Story %s" % w) for w in ("deploybox", "runnerx", "cachewarm")]
        groups = categories.detect_subgroups(
            {"white": design, "blue": infra, "black": self._filler(50)})
        self.assertIn("white", groups)
        self.assertEqual(set(groups["white"]), {"story"})
        self.assertEqual(len(groups["white"]["story"]), 5)
        self.assertNotIn("blue", groups)   # plurality suppresses the weaker category

    def test_plurality_tie_groups_nowhere(self):
        # equal frequency in two categories -> the token groups in NEITHER.
        design = [_item("Story %s" % w) for w in ("board", "flow", "arcs")]
        infra = [_item("Story %s" % w) for w in ("deploybox", "runnerx", "cachewarm")]
        groups = categories.detect_subgroups(
            {"white": design, "blue": infra, "black": self._filler(50)})
        self.assertEqual(groups, {})


if __name__ == "__main__":
    unittest.main()
