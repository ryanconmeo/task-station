# Contributing to Task Station

Thanks for helping improve Task Station! This is a Claude Code plugin with a
strict simplicity constraint — please read the ground rules before opening a PR.

## Ground rule: stdlib-only, system python

Task Station has **zero third-party dependencies**. Everything runs on the
**system `python3` (3.9+)** with no `pip install` step — that's a hard design
constraint, not an accident:

- No `requirements.txt`, no virtualenv, no build step.
- Use only the Python standard library. If you think you need a dependency,
  open an issue first to discuss — the answer is usually "we can do it with
  stdlib."
- Generated code must also be stdlib-only and import-free at runtime (see
  `lib/stack_map.py`).
- Shell hooks are POSIX `bash` + standard CLI tools (`jq`, etc.).

## Running the tests

The suite is plain `unittest`:

```bash
python3 -m unittest discover -s tests -v
```

If you happen to have `pytest` installed, `pytest.ini` points it at the same
tests (no plugins required):

```bash
python3 -m pytest tests -q
```

**State isolation:** `tests/conftest.py` pins `TASK_STATION_HOME` to a throwaway
temp dir before any test imports the engine, so the suite never touches your real
`~/.claude/task-station-data`. Individual tests that need per-test isolation
additionally repoint the path globals in `setUp`. When you write tests, set
`TASK_STATION_HOME` (and, for the Desktop bridge, `TASK_STATION_DESKTOP_CONFIG`)
to tmp paths — never write to the real config dir.

CI runs the same `unittest` command on `ubuntu-latest` + `macos-latest` across
Python 3.11 and 3.12 (`.github/workflows/ci.yml`).

## Regenerating `lib/stack_map.py`

`lib/stack_map.py` is a **generated file — do not edit it by hand.** It's
distilled from GitHub Linguist's `languages.yml`, which is a **gitignored input**
(not committed). To regenerate:

```bash
# 1. Fetch the Linguist data (MIT-licensed) into the repo root as languages.yml:
curl -fsSL -o languages.yml \
  https://raw.githubusercontent.com/github-linguist/linguist/main/lib/linguist/languages.yml

# 2. Regenerate the committed module (reads ./languages.yml, rewrites lib/stack_map.py):
python3 tools/gen_stack_map.py
```

The generator is dependency-free (it parses the YAML with a targeted line
parser — no PyYAML) and emits pure stdlib dict literals. Commit the regenerated
`lib/stack_map.py`; leave `languages.yml` untracked.

## Branch & PR flow

1. Fork (or branch off `main`).
2. Make focused changes; keep unrelated refactors out of the PR.
3. Add/extend tests for any behaviour change and run the full suite (above).
4. Add a `CHANGELOG.md` entry under the next version; bump
   `.claude-plugin/plugin.json` **and** `.claude-plugin/marketplace.json`
   versions together when releasing.
5. Open a PR against `main`. The PR template's checklist covers the essentials
   (stdlib-only, tests green, state isolation, stack-map regenerated if touched,
   docs/changelog updated).

## Reporting bugs & security issues

- Bugs / features: use the GitHub issue templates.
- Security vulnerabilities: **do not** open a public issue — see
  [SECURITY.md](SECURITY.md) for private reporting.

By contributing you agree your contributions are licensed under the repository's
[MIT License](LICENSE).
