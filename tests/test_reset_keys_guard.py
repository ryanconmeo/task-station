"""`config --reset confirm` pops RESET_KEYS, and this is what keeps that list honest.

RESET_KEYS was hand-maintained, so it drifted: `stream`, `stream_dir` and `share_dir`
were all written by board commands and all missing from it, which meant a factory reset
silently left them in force. Counting them by hand once fixes today and nothing else —
so this guard reads the SOURCE and re-derives the answer on every run.

THE CONTRACT. Every config key the board WRITES is either in `RESET_KEYS` (the reset
pops it) or in `RESET_EXCLUDED` (the reset deliberately leaves it, with the reason
written down). A key in neither fails here, naming the file and line that writes it.

THREE WRITE FORMS EXIST and the scan sees all three, because the first version of this
scan saw only one and missed `themes` entirely:

    set("bare_commands", True)          # a bare set()/unset() inside config.py itself
    _cfg.set("export_dirs", cur)        # through an alias, anywhere under lib/
    d["themes"] = themes; _save(d)      # a direct dict write inside config.py

A FOURTH form — `_config.set(key, target)` with a computed key — cannot be resolved by
reading one call site, so it is not guessed at: every such call site must be declared in
`OPAQUE_WRITE_SITES` with the keys it can write. A new one that is not declared fails
here rather than passing invisibly, which is the whole point.

WHAT THIS GUARD DOES NOT CLAIM. It proves the list covers what the board WRITES. A key
that only ever arrives by hand-editing config.json (`skill_colors`) is invisible to any
source scan, so completeness for all time is not on offer — `RESET_EXCLUDED` carries
those by name instead, and the second assertion below stops those notes from rotting.

Stdlib + unittest only, and it reads source rather than importing the engine.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")
CONFIG_PY = os.path.join(LIB, "board", "config.py")

# The names the config module is imported AS across lib/. A `<alias>.set("k", v)` is a
# config write; anything else called `.set(` is a set literal or an unrelated object.
CONFIG_ALIASES = {"config", "cfg", "_cfg", "_config", "ts_config", "boardcfg"}

# Call sites that write a key computed at runtime, and the keys each can write. Read the
# site before adding one: an undeclared computed write is a hole this guard exists to close.
OPAQUE_WRITE_SITES = {
    # `for flag, kind, key in (("init", …, "sync_dir"), ("init_share", …, "share_dir"))`
    ("lib/board/cmds/maintain.py", "cmd_sync"): {"sync_dir", "share_dir"},
}


def _rel(path):
    return os.path.relpath(path, ROOT)


def _py_files():
    for dirpath, dirnames, filenames in os.walk(LIB):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _enclosing_funcs(tree):
    """node -> name of the innermost enclosing def, for OPAQUE_WRITE_SITES lookups."""
    owner = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owner.setdefault(child, node.name)
    return owner


def collect_written_keys():
    """(keys_written, opaque_sites) across lib/, by reading source only."""
    keys, opaque = {}, set()
    for path in _py_files():
        rel = _rel(path)
        is_config = os.path.abspath(path) == os.path.abspath(CONFIG_PY)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        owner = _enclosing_funcs(tree)
        for node in ast.walk(tree):
            # forms 1 and 2: set()/unset() calls
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute) and fn.attr in ("set", "unset"):
                    if not (isinstance(fn.value, ast.Name) and fn.value.id in CONFIG_ALIASES):
                        continue
                elif isinstance(fn, ast.Name) and fn.id in ("set", "unset"):
                    if not is_config:
                        continue          # a bare set() elsewhere is the builtin
                else:
                    continue
                if not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    keys.setdefault(arg.value, []).append("%s:%d" % (rel, node.lineno))
                else:
                    opaque.add((rel, owner.get(node, "<module>")))
            # form 3: a direct dict write into the loaded config, inside config.py
            elif is_config and isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (isinstance(tgt, ast.Subscript)
                            and isinstance(tgt.value, ast.Name)
                            and isinstance(tgt.slice, ast.Constant)
                            and isinstance(tgt.slice.value, str)):
                        keys.setdefault(tgt.slice.value, []).append("%s:%d" % (rel, node.lineno))
    return keys, opaque


def _config_lists():
    """RESET_KEYS and RESET_EXCLUDED, read from source (no engine import)."""
    with open(CONFIG_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        name = getattr(node.targets[0], "id", None)
        if name == "RESET_KEYS":
            out["keys"] = [e.value for e in node.value.elts]
        elif name == "RESET_EXCLUDED":
            out["excluded"] = {k.value: v.value for k, v in
                               zip(node.value.keys, node.value.values)
                               if isinstance(v, ast.Constant)}
    return out


class ResetKeysGuard(unittest.TestCase):

    def setUp(self):
        lists = _config_lists()
        self.reset_keys = lists.get("keys")
        self.excluded = lists.get("excluded")
        self.assertIsNotNone(self.reset_keys, "RESET_KEYS not found in config.py")
        self.assertIsNotNone(self.excluded, "RESET_EXCLUDED not found in config.py")
        self.written, self.opaque = collect_written_keys()

    def test_the_scan_finds_something(self):
        """A scan that silently matched nothing would make every assertion below vacuous —
        the `discover -k <nothing>` failure mode, one layer down."""
        self.assertGreater(len(self.written), 40,
                           "the write scan collapsed; every assertion below is now vacuous")

    def test_every_written_key_is_reset_or_declared_excluded(self):
        """The contract. A board command that writes a new key must say whether a factory
        reset pops it — here, in the source, not in whoever remembers next year."""
        known = set(self.reset_keys) | set(self.excluded)
        missing = {k: v for k, v in self.written.items() if k not in known}
        self.assertEqual({}, missing,
                         "config keys the board WRITES that a factory reset neither pops "
                         "nor declares excluded (add to RESET_KEYS, or to RESET_EXCLUDED "
                         "with the reason): %s" % missing)

    def test_opaque_write_sites_are_declared_and_their_keys_accounted_for(self):
        """A computed key (`set(key, target)`) is invisible to the scan, so the site is
        declared instead. Undeclared = a hole; declared keys obey the same contract."""
        self.assertEqual(set(OPAQUE_WRITE_SITES), self.opaque,
                         "computed-key config writes changed. Read each site, then update "
                         "OPAQUE_WRITE_SITES: found=%s declared=%s"
                         % (sorted(self.opaque), sorted(OPAQUE_WRITE_SITES)))
        known = set(self.reset_keys) | set(self.excluded)
        for site, site_keys in OPAQUE_WRITE_SITES.items():
            for k in site_keys:
                self.assertIn(k, known, "%s writes %r, which is neither reset nor "
                                        "declared excluded" % (site, k))

    def test_the_three_keys_that_were_missing_are_popped(self):
        """The regression this guard was born from: `config --stream off`, a `stream_dir`
        tee and a `share_dir` exchange all survived a factory reset."""
        for k in ("stream", "stream_dir", "share_dir"):
            self.assertIn(k, self.reset_keys)

    def test_excluded_keys_still_exist_somewhere_in_lib(self):
        """An exclusion note for a key nobody uses any more is a fossil. It must name a key
        the code still mentions, or be deleted with the key."""
        chunks = []
        for path in _py_files():
            with open(path, encoding="utf-8") as fh:
                chunks.append(fh.read())
        blob = "".join(chunks)
        for k in self.excluded:
            self.assertIn('"%s"' % k, blob,
                          "RESET_EXCLUDED names %r, which no longer appears in lib/ — "
                          "delete the note with the key" % k)

    def test_every_exclusion_carries_a_reason(self):
        """The list is only a guard if the escape hatch costs a sentence."""
        for k, why in self.excluded.items():
            self.assertGreater(len(why), 40, "RESET_EXCLUDED[%r] must say WHY" % k)

    def test_the_two_lists_are_disjoint_and_have_no_duplicates(self):
        self.assertEqual(len(self.reset_keys), len(set(self.reset_keys)),
                         "RESET_KEYS has a duplicate")
        overlap = set(self.reset_keys) & set(self.excluded)
        self.assertEqual(set(), overlap,
                         "a key cannot be both popped and excluded: %s" % overlap)


if __name__ == "__main__":
    unittest.main()
