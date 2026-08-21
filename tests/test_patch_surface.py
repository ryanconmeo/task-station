"""The 3.0.0 engine split's structural guard: the routed-name contract.

`lib/task-station.py` is the FACADE. The seams under `lib/board/` are imported
into it with star-imports, so every historical `ts.<something>` still resolves —
but a test that PATCHES a name (assigns to it on the engine module) only changes
the facade's binding. A split module that read such a name as its own module
global would silently keep the unpatched value, and the test would pass while
testing nothing. So every one of those names is read late, through
`board._shared.g("NAME")`, against the facade's live namespace.

That contract only holds while the two sides agree, which is what these two
assertions pin down:

1. The set of names the suite actually patches equals ROUTED below. A future
   release that patches a 24th name fails HERE, loudly, instead of failing
   mysteriously later — re-derive the set and route the new name.
2. No SPLIT-ENGINE SEAM under `lib/board/` reads one of those names bare (the
   seams are listed in SEAM_FILES below, and only they are scanned). Definitions
   (`def mutate(...)`), attribute access (`backend.mutate`) and the string
   literal inside `g("mutate")` are all fine; a bare `mutate` reference is not.

A test patches the engine module in TWO forms, and assertion 1 must see both:
a plain attribute assignment onto `ts` (the §3 regex form), and a `setattr` on
`ts` whose name is either a string literal or a function parameter. The example
forms are spelled with a placeholder below so this docstring is not itself
scanned as a patch site:

    ts.<name> = fake                   # the §3 regex form
    setattr(ts, "<name>", spy)         # a literal setattr
    setattr(ts, attr, spy)             # setattr through a parameter

The third form is what `tests/test_transcript_cache.py` uses: a `_count_parses`
helper whose `attr` parameter defaults to `"_session_msgcount_uncached"` and is
passed `"_prompt_replies_all"` at four call sites. The §3 regex cannot see any
of it, which is exactly how those two names were missed when the routed set was
first derived (chunk 3). `_setattr_patched` below closes that hole: it resolves
ONE hop — a `setattr(ts, <param>, …)` back to that parameter's string default
and to the string literals its call sites pass. Deeper indirection is out of
scope on purpose; if a future test needs it, this guard should fail loudly
rather than quietly under-report.

RE-DERIVING THE SET? Read `docs/PATCH-SURFACE.md` first — it is the two-scan
procedure, and it exists because this set was once derived with one scan when
there are two. The scans below are the implementation; that document is the
instruction, and it lives where a person starting a phase will actually look.

Stdlib + unittest only, and it reads source rather than importing anything — it
must stay runnable even when the engine itself is mid-surgery.
"""
import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT, "tests")
BOARD_DIR = os.path.join(ROOT, "lib", "board")

# The routed set (PHASE2-ROSTER.json `routed_21` + `routed_setattr_2`). Nine of
# these are also the facade-resident config globals, which the seams read the
# same way. The last two are the setattr-patched pair the §3 regex missed.
ROUTED = {
    "DATA",
    "STORE",
    "TASKS_DIR",
    "LINKS_DIR",
    "PROJECTS_ROOT",
    "DELEGATE_REGISTRY",
    "_LIVE_BG_INDEX",
    "claude_code_model_selection",
    "_delegate_module",
    "subprocess",
    "session_model",
    "reap_own_workers",
    "_session_msgcount",
    "_open_jump_window",
    "_find_session_path",
    "_emit_title_to_origin",
    "_MSGCOUNT_DISK",
    "mutate",
    "_bare_commands",
    "REPLIES_CACHE_MAX",
    "MSGCOUNT_MEM_MAX",
    "_session_msgcount_uncached",
    "_prompt_replies_all",
}

# The §3 patch-surface regex: an assignment onto the engine module, which every
# test file binds as `ts`.
_PATCH_RE = re.compile(r"\bts\.([A-Za-z_]+) *=")

# The split-engine seams — the ONLY files assertion 2 scans, named explicitly
# rather than walked, because `lib/board/` is no longer only the split engine.
#
# The routed-name contract belongs to code that was carved OUT of the facade:
# it still runs against the facade's live namespace, so it must reach every
# patchable name through g(). Phase 3 also moves the flat import-only modules
# (`store.py`, `save.py`, `export.py`, …) under `lib/board/`, and those never
# had that relationship — a test's `ts.subprocess = fake` never reached them
# before the split either, because they always imported `subprocess` into their
# own namespace. A bare read of a routed name inside one of THEM is therefore
# behavior-correct, not a bug, and scanning them would be a false positive.
# So: seams route via g(); moved standalone modules own their imports.
SEAM_FILES = ["_shared.py", "state.py", "model.py", "memos.py", "sessions.py",
              "render.py", "graph.py", "boardio.py", "cli.py",
              "cmds/__init__.py", "cmds/maintain.py", "cmds/manage.py",
              "cmds/view.py", "cmds/sub.py", "cmds/surface.py", "cmds/loop.py"]


def _py_files(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _positional_args(fn):
    return list(getattr(fn.args, "posonlyargs", [])) + list(fn.args.args)


def _is_ts_setattr(node):
    """A `setattr(ts, <name>, …)` call — the patch form the §3 regex can't see."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name) and node.args[0].id == "ts")


def _str_const(node):
    """The node's value when it is a string literal, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _param_defaults(fn, param):
    """String defaults bound to `param` in `fn`'s signature."""
    args = _positional_args(fn)
    defaults = list(fn.args.defaults)
    offset = len(args) - len(defaults)
    out = []
    for i, a in enumerate(args):
        if a.arg == param and i >= offset:
            v = _str_const(defaults[i - offset])
            if v is not None:
                out.append(v)
    for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        if a.arg == param and d is not None:
            v = _str_const(d)
            if v is not None:
                out.append(v)
    return out


def _param_call_strings(tree, fn, param):
    """Every string literal passed as `param` to a call of `fn` in this file."""
    names = [a.arg for a in _positional_args(fn)]
    idx = names.index(param) if param in names else None
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        called = (f.attr if isinstance(f, ast.Attribute)
                  else f.id if isinstance(f, ast.Name) else None)
        if called != fn.name:
            continue
        for kw in node.keywords:
            if kw.arg == param:
                v = _str_const(kw.value)
                if v is not None:
                    out.append(v)
        if idx is None:
            continue
        # A bound-method call (`self._count_parses(x)`) omits `self`, so the
        # caller's positional list starts one slot after the signature's.
        pos = idx - 1 if isinstance(f, ast.Attribute) else idx
        if 0 <= pos < len(node.args):
            v = _str_const(node.args[pos])
            if v is not None:
                out.append(v)
    return out


def _setattr_patched(source, path):
    """Names patched via `setattr(ts, …)` — a string literal, or a parameter
    resolved one hop back to its default and its call sites' literals."""
    names = set()
    tree = ast.parse(source, filename=path)
    for node in ast.walk(tree):
        if _is_ts_setattr(node):
            v = _str_const(node.args[1])
            if v is not None:
                names.add(v)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in _positional_args(fn) + list(fn.args.kwonlyargs)}
        for node in ast.walk(fn):
            if not _is_ts_setattr(node):
                continue
            target = node.args[1]
            if isinstance(target, ast.Name) and target.id in params:
                names.update(_param_defaults(fn, target.id))
                names.update(_param_call_strings(tree, fn, target.id))
    return names


class PatchSurfaceTests(unittest.TestCase):

    def test_suite_patch_surface_equals_the_routed_set(self):
        """Every name the suite patches on the engine module is routed, and
        nothing is routed that the suite never patches. Both patch forms count:
        `ts.<name> = …` and `setattr(ts, …)`."""
        patched = set()
        for path in _py_files(TESTS_DIR):
            source = _read(path)
            patched |= set(_PATCH_RE.findall(source))
            patched |= _setattr_patched(source, path)
        self.assertEqual(
            patched, ROUTED,
            "the suite's patch surface no longer matches the routed set.\n"
            "  patched but NOT routed: %s\n"
            "  routed but NOT patched: %s\n"
            "Re-derive the surface, add the name to ROUTED here AND to "
            "PHASE2-ROSTER.json's routed_21 / routed_setattr_2, and rewrite "
            "every read of it inside lib/board/ as g(\"<name>\")."
            % (sorted(patched - ROUTED) or "none",
               sorted(ROUTED - patched) or "none"))

    def test_board_modules_never_read_a_routed_name_bare(self):
        """Inside the split-engine seams, a routed name is only ever reached
        through g(...)/set_g(...) — never as a bare load, never rebound with
        `global`. Only SEAM_FILES are scanned; see the note beside that list."""
        offenders = []
        for seam in SEAM_FILES:
            path = os.path.join(BOARD_DIR, *seam.split("/"))
            rel = os.path.relpath(path, ROOT)
            # A renamed or deleted seam must fail here, not silently drop out of
            # coverage — the list is hardcoded, so absence is the failure mode.
            self.assertTrue(os.path.isfile(path),
                            "SEAM_FILES names %s, which does not exist — update "
                            "the list when a seam is renamed or removed." % rel)
            tree = ast.parse(_read(path), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in ROUTED:
                    offenders.append("%s:%d bare name %r" % (rel, node.lineno, node.id))
                elif isinstance(node, ast.Global):
                    for name in node.names:
                        if name in ROUTED:
                            offenders.append(
                                "%s:%d global %r" % (rel, node.lineno, name))
        self.assertEqual(
            offenders, [],
            "routed names must be read through g(\"<name>\") inside lib/board/ "
            "(and rebound with _shared.set_g), never bound as a module global "
            "of the seam — the suite patches the FACADE, so a bare read sees "
            "the unpatched value:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# THE TWO-SCAN PROCEDURE HAS TO LIVE WHERE THE NEXT DERIVER WILL LOOK.
#
# The guard above already runs BOTH scans, and that is not the same thing as the procedure
# being recorded. The routed set was first derived with the §3 regex ALONE, which cannot see
# a `setattr(ts, …)` at all, so two names were missed — and the only place that now explains
# why is a docstring inside the test that closed the hole. A person re-deriving the set at
# the start of a phase reads the PLAN, not this file, so the knowledge was recorded exactly
# where it would not be found.
#
# So the procedure is a tracked document, and these assertions are what stop it rotting into
# a lie. A doc that names a DIFFERENT regex from the one the guard actually runs is worse
# than no doc: it sends the next deriver back down the road that lost two names. So the doc
# is pinned to the code, both directions.
# ---------------------------------------------------------------------------

PROCEDURE_DOC = os.path.join(ROOT, "docs", "PATCH-SURFACE.md")


class TheTwoScanProcedureIsRecorded(unittest.TestCase):
    def _doc(self):
        self.assertTrue(os.path.exists(PROCEDURE_DOC),
                        "%s does not exist" % PROCEDURE_DOC)
        return _read(PROCEDURE_DOC)

    def test_the_document_exists_and_is_tracked(self):
        body = self._doc()
        self.assertTrue(body.strip(), "the document is empty")

    def test_it_names_BOTH_scans_and_says_which_one_is_blind(self):
        body = self._doc()
        self.assertIn("setattr", body)
        self.assertIn("ts.", body)
        # The whole point of the record: one scan cannot see the other's form.
        self.assertIn("cannot see", body.lower())

    def test_the_regex_it_prints_is_the_regex_THE_GUARD_RUNS(self):
        """The one assertion that makes this doc trustworthy. A procedure quoting a regex
        the code does not use would send the next deriver back down the road that lost two
        names — so the pattern is pinned, not paraphrased."""
        self.assertIn(_PATCH_RE.pattern, self._doc())

    def test_it_carries_the_routed_set_size_as_a_FLOOR_not_a_literal(self):
        """A count written as an equality is falsified by the next release that routes a
        name. The doc states the direction and names the guard that keeps it exact."""
        body = self._doc()
        self.assertIn("test_patch_surface", body)
        self.assertRegex(body, r"(?i)floor|at least|never shrink")

    def test_it_says_what_the_setattr_scan_resolves_and_what_it_does_NOT(self):
        """The scope limit is half the procedure: ONE hop is resolved, deeper indirection is
        deliberately out of scope and must fail loudly rather than under-report."""
        body = self._doc()
        self.assertIn("one hop", body.lower())
        self.assertIn("out of scope", body.lower())

    def test_the_guard_points_AT_the_document(self):
        """Otherwise the doc is a file nobody is told about — the same rot the checker
        template's pointer test exists to stop.

        Asserted against this module's DOCSTRING and not its source. Scanning the source
        would be satisfied by the literal inside this very assertion — a guard that passes
        because it mentions the thing it is checking for, which is the tautology this suite
        is otherwise careful to avoid."""
        self.assertIn("docs/PATCH-SURFACE.md", __doc__ or "")
