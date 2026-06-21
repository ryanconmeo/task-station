<!-- Thanks for contributing to Task Station! Keep PRs focused. -->

## What & why
Brief description of the change and the motivation.

Closes #<!-- issue number, if any -->

## Type
- [ ] Bug fix
- [ ] New feature
- [ ] Docs / meta only
- [ ] Breaking change

## Checklist
- [ ] **Stdlib-only** — no third-party Python dependencies added; runs on system `python3` (3.9+).
- [ ] Tests added/updated and the full suite passes: `python3 -m unittest discover -s tests -v`.
- [ ] Tests isolate state (use `TASK_STATION_HOME` / a tmpdir; no writes to the real `~/.claude`).
- [ ] If `lib/stack_map.py` changed, it was **regenerated** via `python3 tools/gen_stack_map.py` (not hand-edited).
- [ ] Docs updated where relevant (`README.md`, `CHANGELOG.md`, command `*.md`).
- [ ] `CHANGELOG.md` entry added under the next version.

## Notes for reviewers
Anything that needs context — design trade-offs, follow-ups, manual test steps.
