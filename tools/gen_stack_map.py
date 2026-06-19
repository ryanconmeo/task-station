#!/usr/bin/env python3
# gen_stack_map.py — DEV-TIME generator for lib/stack_map.py.
"""Generate `lib/stack_map.py` from GitHub Linguist's `languages.yml`.

Linguist's `languages.yml` (https://github.com/github-linguist/linguist,
MIT-licensed) is the data behind GitHub's per-repo language bar. We distill it
into two plain-dict lookups — extension -> stack label and filename -> stack
label — so the hub repo-index can detect a vastly wider set of languages
(Swift, Kotlin, Ruby, PHP, …) than the old hand-rolled list.

DESIGN
  * Dependency-free parse: a targeted line parser tracks the current top-level
    `^Name:` language key, its `^  type:`, and the items under its
    `^  extensions:` / `^  filenames:` blocks. We do NOT require PyYAML so the
    generator runs anywhere; the GENERATED output is pure stdlib (plain dict
    literals, no imports, no runtime YAML, no network).
  * Keep only `type: programming` plus a small allowlist of useful non-programming
    routing signals (SQL and its dialects). Prose/data/markup noise (json,
    markdown, csv, …) is dropped.
  * Ergonomic-label overlay: an explicit alias dict maps Linguist language NAMES
    to the stack labels the tool already uses, so existing behaviour doesn't
    regress (Python->python, JavaScript->node, C#/F#/VB.NET->dotnet, HCL->
    terraform, Dockerfile->docker, SQL dialects->sql, …). Everything else falls
    through to a slugified Linguist name (Swift->swift, Kotlin->kotlin).
  * Conflict resolution: many languages claim the same extension (`.sql` is
    claimed by 5 languages, `.cgi` by 3). A fixed CURATED_PRIORITY list grabs
    extensions first so the curated labels win; the long tail is then processed
    in alphabetical order for determinism (first writer wins).

USAGE
    python3 tools/gen_stack_map.py [languages.yml] [-o lib/stack_map.py]

Defaults read `<repo>/languages.yml` and rewrite `<repo>/lib/stack_map.py`.
Running it twice on the same input produces byte-identical output.
"""
import argparse
import os
import re

# Linguist NAME -> ergonomic stack label. Preserves the labels the tool already
# uses; extend sensibly. Anything not listed slugifies its Linguist name.
ALIASES = {
    "Python": "python",
    "Shell": "shell",
    "SQL": "sql",
    "PLpgSQL": "sql",
    "PLSQL": "sql",
    "SQLPL": "sql",
    "TSQL": "sql",
    "TypeScript": "typescript",
    "JavaScript": "node",
    "C#": "dotnet",
    "F#": "dotnet",
    "Visual Basic .NET": "dotnet",
    "Go": "go",
    "Rust": "rust",
    "HCL": "terraform",          # Linguist folds Terraform's .tf/.tfvars into HCL
    "Dockerfile": "docker",
    "Makefile": "make",
    "C++": "cpp",                # avoid the C/C++ slug collision on "c"
}

# Non-programming languages we nonetheless keep for routing (everything else is
# filtered to type: programming). SQL is `type: data` in Linguist.
NAME_ALLOWLIST = {"SQL", "PLSQL", "SQLPL"}

# Languages whose extension claims must win conflicts, in priority order. These
# are written first so curated labels own their shared extensions (e.g. `.sql`
# -> sql, not plpgsql/tsql; `.cgi` -> python before shell/perl).
CURATED_PRIORITY = [
    "SQL", "TSQL", "PLpgSQL", "PLSQL", "SQLPL",
    "TypeScript", "JavaScript",
    "C#", "F#", "Visual Basic .NET",
    "Python", "Go", "Rust", "Shell",
    "PHP", "Ruby",               # claimed by Hack/Faust etc.; keep ergonomic labels
    "HCL", "Dockerfile",
]


def parse_languages(text):
    """Parse Linguist YAML into {name: {"type", "extensions", "filenames"}}.

    A dependency-free line parser: top-level `Name:` keys (column 0), their
    2-space-indented `type:` value, and the list items under `extensions:` /
    `filenames:` blocks. List items under other fields (aliases, interpreters…)
    are ignored because `field` is only armed by the two block headers.
    """
    langs = {}
    cur = None
    field = None
    for line in text.splitlines():
        if line.startswith("#") or line.strip() == "---" or not line.strip():
            continue
        # Top-level language key: non-indented `Name:` (names may contain spaces
        # and dots, e.g. "Visual Basic .NET", "1C Enterprise").
        m = re.match(r"^(\S[^:]*):\s*$", line)
        if m and not line[0].isspace():
            cur = m.group(1)
            langs[cur] = {"type": None, "extensions": [], "filenames": []}
            field = None
            continue
        if cur is None:
            continue
        mt = re.match(r"^  type:\s*\"?([^\"\n]+?)\"?\s*$", line)
        if mt:
            langs[cur]["type"] = mt.group(1).strip()
            field = None
            continue
        if re.match(r"^  extensions:\s*$", line):
            field = "extensions"
            continue
        if re.match(r"^  filenames:\s*$", line):
            field = "filenames"
            continue
        item = re.match(r"^  - \"?(.+?)\"?\s*$", line)
        if item and field:
            langs[cur][field].append(item.group(1))
            continue
        # Any other 2-space field (`aliases:`, `color:`, …) ends the current list.
        if re.match(r"^  \S", line):
            field = None
    return langs


def slugify(name):
    """Slugify a Linguist language name into a routing label."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def label_for(name):
    return ALIASES.get(name) or slugify(name)


def _kept_names(langs):
    """Names we keep (programming + allowlist), curated-priority first then the
    rest alphabetically — a deterministic order for first-writer-wins."""
    kept = {n for n, v in langs.items()
            if v.get("type") == "programming" or n in NAME_ALLOWLIST}
    ordered = [n for n in CURATED_PRIORITY if n in kept]
    ordered += sorted(n for n in kept if n not in CURATED_PRIORITY)
    return ordered


def build_maps(langs):
    """Return (ext_to_stack, filename_to_stack). First writer wins per the
    curated-priority ordering, so shared extensions resolve to curated labels."""
    ext_to_stack = {}
    filename_to_stack = {}
    for name in _kept_names(langs):
        label = label_for(name)
        for ext in langs[name]["extensions"]:
            ext_to_stack.setdefault(ext.lower(), label)
        for fn in langs[name]["filenames"]:
            filename_to_stack.setdefault(fn, label)
    return ext_to_stack, filename_to_stack


_HEADER = '''# stack_map.py — GENERATED FILE, DO NOT EDIT BY HAND.
#
# Extension/filename -> ergonomic stack label, distilled from GitHub Linguist's
# languages.yml (https://github.com/github-linguist/linguist, MIT-licensed) —
# the data behind GitHub's per-repo language bar.
#
# Regenerate with:
#     python3 tools/gen_stack_map.py
# (reads the vendored ./languages.yml, which is gitignored, and rewrites this
# file). Curated labels (python/node/dotnet/sql/typescript/go/rust/terraform/
# docker/...) are preserved via an alias overlay in the generator; everything
# else falls through to a slugified Linguist name (Swift->swift, Kotlin->kotlin).
#
# Pure stdlib: plain dict literals, no imports, no runtime YAML, no network.
"""Linguist-derived extension/filename -> stack-label lookups (generated)."""
'''


def _render_dict(name, mapping):
    lines = ["%s = {" % name]
    for key in sorted(mapping):
        lines.append("    %r: %r," % (key, mapping[key]))
    lines.append("}")
    return "\n".join(lines)


def generate(yml_text):
    """Return the full text of lib/stack_map.py for the given languages.yml text.
    Deterministic: same input -> byte-identical output."""
    langs = parse_languages(yml_text)
    ext_to_stack, filename_to_stack = build_maps(langs)
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
    ap = argparse.ArgumentParser(description="Generate lib/stack_map.py from Linguist languages.yml")
    ap.add_argument("yml", nargs="?", default=os.path.join(repo_root, "languages.yml"),
                    help="path to languages.yml (default: <repo>/languages.yml)")
    ap.add_argument("-o", "--out", default=os.path.join(repo_root, "lib", "stack_map.py"),
                    help="output module path (default: <repo>/lib/stack_map.py)")
    ap.add_argument("--stdout", action="store_true", help="write to stdout instead of --out")
    args = ap.parse_args(argv)

    with open(args.yml, encoding="utf-8") as f:
        text = generate(f.read())
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
