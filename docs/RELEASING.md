# Releasing task-station

task-station's version lives in four files that must never drift (plugin.json,
marketplace.json, the README badge, and the CHANGELOG heading). `scripts/release.py`
rewrites all four; `scripts/ship.sh` wraps the whole mechanical release.

## One-shot

From the primary checkout, on the integration branch (`main`):

```sh
scripts/ship.sh --from <feature-branch> --bump minor
# fill the CHANGELOG entry it scaffolds, then:
git commit -am "release X.Y.Z: <summary>" && git push origin main
```

Pass `--yes` to commit + push automatically once the CHANGELOG has no unfilled
`- …` placeholder. `scripts/ship.sh --help` lists every flag.

## Load it (no restart)

A release is "bump the version + push"; the version-gated marketplace auto-update
pulls it. To pick it up **in a running Claude Code session**, without a restart:

```
/plugin update      # fetch the new version from the marketplace + hot-swap it
/reload-plugins     # reload skills / commands / hooks
/todo board         # regenerate board.html — then hard-reload the browser tab
```

An on-prompt hook re-points the `task-station-engine` handle at the active install,
so the bare `/todo` aliases follow the update mid-session.

## Seeing the board update

`board.html` is a static local page — it only changes when something regenerates it
(`/todo board`, `task-station board`, or, with `--board-autorefresh on`, the Stop
hook keeping an already-open board fresh each turn). Its self-reload triggers on task
**data** changes, not on a plugin **code/version** change — so after a release,
regenerate once and hard-reload the tab.

Regenerate from a shell at any time (version-stable handle):

```sh
python3 ~/.claude/task-station-engine/task-station.py board
```

Turn on continuous refresh (data changes appear without re-running the command):

```
/todo config --board-autorefresh on
```
