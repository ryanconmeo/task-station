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
   release that patches a 22nd name fails HERE, loudly, instead of failing
   mysteriously later — re-derive the set and route the new name.
2. No module under `lib/board/` reads one of those names bare. Definitions
   (`def mutate(...)`), attribute access (`backend.mutate`) and the string
   literal inside `g("mutate")` are all fine; a bare `mutate` reference is not.

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

# The routed set (PHASE2-ROSTER.json `routed_21`). Nine of these are also the
# facade-resident config globals, which the seams read the same way.
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
}

# The §3 patch-surface regex: an assignment onto the engine module, which every
# test file binds as `ts`.
_PATCH_RE = re.compile(r"\bts\.([A-Za-z_]+) *=")


def _py_files(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class PatchSurfaceTests(unittest.TestCase):

    def test_suite_patch_surface_equals_the_routed_set(self):
        """Every name the suite patches on the engine module is routed, and
        nothing is routed that the suite never patches."""
        patched = set()
        for path in _py_files(TESTS_DIR):
            patched |= set(_PATCH_RE.findall(_read(path)))
        self.assertEqual(
            patched, ROUTED,
            "the suite's patch surface no longer matches the routed set.\n"
            "  patched but NOT routed: %s\n"
            "  routed but NOT patched: %s\n"
            "Re-derive the surface, add the name to ROUTED here AND to "
            "PHASE2-ROSTER.json's routed_21, and rewrite every read of it "
            "inside lib/board/ as g(\"<name>\")."
            % (sorted(patched - ROUTED) or "none",
               sorted(ROUTED - patched) or "none"))

    def test_board_modules_never_read_a_routed_name_bare(self):
        """Inside lib/board/, a routed name is only ever reached through
        g(...)/set_g(...) — never as a bare load, never rebound with `global`."""
        offenders = []
        for path in _py_files(BOARD_DIR):
            rel = os.path.relpath(path, ROOT)
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
