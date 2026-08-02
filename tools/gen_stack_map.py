#!/usr/bin/env python3
# gen_stack_map.py — DEV-TIME generator for lib/stack_map.py.
"""Generate `lib/stack_map.py` from the curated stack list below.

The repo-index only needs to recognise the common language/tooling stacks a
working repo is actually built in — not the long tail of GitHub Linguist's ~400
languages. So this generator is SELF-CONTAINED: the `STACKS` table below is the
single source of truth (curated-alias overlay + the top ~40 common stacks), and
`lib/stack_map.py` is distilled from it — no external `languages.yml`, no network,
no third-party parser.

DESIGN
  * The GENERATED output is pure stdlib: two plain-dict literals (extension ->
    stack label, filename -> stack label), no imports, no runtime code.
  * First-writer-wins on a shared extension, in `STACKS` order — so C owns `.h`
    before any later entry, and each ergonomic label (python/node/dotnet/sql/
    typescript/go/rust/terraform/docker/...) stays exactly what the tool uses.
  * Only real programming/tooling stacks are listed; prose/data/markup formats
    (`.md`, `.json`, `.yaml`, `.csv`, …) are deliberately absent, so the
    repo-index never mistakes a docs/config file for a source stack. Unknown
    extensions simply aren't in the map — `repo_index` degrades gracefully.

USAGE
    python3 tools/gen_stack_map.py [-o lib/stack_map.py] [--stdout]

Running it produces byte-identical output every time (no external input).
"""
import argparse
import os

# Curated stacks: (label, extensions, filenames). Order matters — the first entry
# to claim an extension wins (so `.h` -> c, not cpp). This table IS the source of
# truth; edit here and re-run to regenerate lib/stack_map.py.
STACKS = [
    ("python",      (".py", ".pyi", ".pyw"),                              ()),
    ("node",        (".js", ".jsx", ".mjs", ".cjs"),                      ()),
    ("typescript",  (".ts", ".tsx", ".mts", ".cts"),                      ()),
    ("go",          (".go",),                                             ("go.mod",)),
    ("rust",        (".rs",),                                             ()),
    ("dotnet",      (".cs", ".fs", ".fsx", ".vb"),                        ()),
    ("java",        (".java",),                                           ()),
    ("kotlin",      (".kt", ".kts"),                                      ()),
    ("swift",       (".swift",),                                          ()),
    ("objective-c", (".m", ".mm"),                                        ()),
    ("c",           (".c", ".h"),                                         ()),
    ("cpp",         (".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx"), ()),
    ("ruby",        (".rb", ".rake"),                                     ("Gemfile", "Rakefile")),
    ("php",         (".php", ".phtml"),                                   ()),
    ("scala",       (".scala", ".sc"),                                    ()),
    ("perl",        (".pl", ".pm", ".perl"),                              ()),
    ("lua",         (".lua",),                                            ()),
    ("r",           (".r",),                                              ()),
    ("haskell",     (".hs", ".lhs"),                                      ()),
    ("elixir",      (".ex", ".exs"),                                      ()),
    ("erlang",      (".erl", ".hrl"),                                     ()),
    ("clojure",     (".clj", ".cljs", ".cljc", ".edn"),                   ()),
    ("dart",        (".dart",),                                           ()),
    ("julia",       (".jl",),                                             ()),
    ("groovy",      (".groovy", ".gradle"),                               ()),
    ("powershell",  (".ps1", ".psm1", ".psd1"),                           ()),
    ("shell",       (".sh", ".bash", ".zsh", ".ksh"),                     ()),
    ("sql",         (".sql",),                                            ()),
    ("terraform",   (".tf", ".tfvars"),                                   ()),
    ("html",        (".html", ".htm"),                                    ()),
    ("css",         (".css",),                                            ()),
    ("scss",        (".scss", ".sass"),                                   ()),
    ("vue",         (".vue",),                                            ()),
    ("svelte",      (".svelte",),                                         ()),
    ("elm",         (".elm",),                                            ()),
    ("ocaml",       (".ml", ".mli"),                                      ()),
    ("zig",         (".zig",),                                            ()),
    ("nim",         (".nim", ".nims"),                                    ()),
    ("crystal",     (".cr",),                                             ()),
    ("solidity",    (".sol",),                                            ()),
    ("protobuf",    (".proto",),                                          ()),
    ("graphql",     (".graphql", ".gql"),                                 ()),
    ("docker",      (),                                                   ("Dockerfile",)),
    ("make",        (".mk",),                                             ("Makefile", "GNUmakefile")),
]


def build_maps():
    """Return (ext_to_stack, filename_to_stack). First writer wins per STACKS
    order, so a shared extension resolves to the earlier (curated) label."""
    ext_to_stack = {}
    filename_to_stack = {}
    for label, exts, filenames in STACKS:
        for ext in exts:
            ext_to_stack.setdefault(ext.lower(), label)
        for fn in filenames:
            filename_to_stack.setdefault(fn, label)
    return ext_to_stack, filename_to_stack


_HEADER = '''# stack_map.py — GENERATED FILE, DO NOT EDIT BY HAND.
#
# Extension/filename -> ergonomic stack label for the repo-index. Curated: the
# top ~40 common language/tooling stacks, with the labels the tool already uses
# (python/node/dotnet/sql/typescript/go/rust/terraform/docker/...).
#
# Regenerate with:
#     python3 tools/gen_stack_map.py
# (the STACKS table in tools/gen_stack_map.py is the source of truth — no external
# input, so this reproduces byte-for-byte). Unknown extensions are intentionally
# absent; repo_index degrades gracefully on anything not listed here.
#
# Pure stdlib: plain dict literals, no imports, no runtime code, no network.
"""Curated extension/filename -> stack-label lookups (generated)."""
'''


def _render_dict(name, mapping):
    lines = ["%s = {" % name]
    for key in sorted(mapping):
        lines.append("    %r: %r," % (key, mapping[key]))
    lines.append("}")
    return "\n".join(lines)


def generate():
    """Return the full text of lib/stack_map.py. Deterministic — no input, so it
    is byte-identical on every run."""
    ext_to_stack, filename_to_stack = build_maps()
    parts = [
        _HEADER,
        _render_dict("EXT_TO_STACK", ext_to_stack),
        "",
        _render_dict("FILENAME_TO_STACK", filename_to_stack),
        "",
    ]
    return "\n".join(parts)


def main(argv=None):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="Generate lib/stack_map.py from the curated STACKS table")
    ap.add_argument("-o", "--out", default=os.path.join(repo_root, "lib", "stack_map.py"),
                    help="output module path (default: <repo>/lib/stack_map.py)")
    ap.add_argument("--stdout", action="store_true", help="write to stdout instead of --out")
    args = ap.parse_args(argv)

    text = generate()
    if args.stdout:
        import sys
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        ext_count = text.split("EXT_TO_STACK = {", 1)[1].split("}", 1)[0].count(": ")
        fn_count = text.split("FILENAME_TO_STACK = {", 1)[1].split("}", 1)[0].count(": ")
        print("wrote %s (%d extensions, %d filenames)" % (args.out, ext_count, fn_count))


if __name__ == "__main__":
    main()
