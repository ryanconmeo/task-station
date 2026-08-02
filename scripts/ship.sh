#!/usr/bin/env bash
# ship.sh — release helper for task-station (a Claude Code plugin).
#
# Automates the MECHANICAL half of a release, run from the primary checkout:
#   run tests -> optionally fast-forward a feature branch into the integration
#   branch -> bump the version in all four files (via release.py) -> stop for you
#   to fill the CHANGELOG -> commit -> push.
#
# The Claude-side half cannot be scripted (these are Claude Code slash commands).
# After a successful push, run them in your Claude session:
#     /plugin update     # fetch the new version from the marketplace + hot-swap it
#     /reload-plugins    # reload skills / commands / hooks
#     /todo board        # regenerate board.html, then hard-reload the browser tab
# No restart is needed: an on-prompt hook re-points the engine handle at the
# active install, so the bare /todo aliases track the update in-session.
#
# Usage:
#   scripts/ship.sh [--bump patch|minor|major] [--set X.Y.Z]
#                   [--from <feature-branch>] [--into <branch>] [--yes]
#
#   --bump   version bump (default: minor). Ignored when --set is given.
#   --set    set an exact X.Y.Z instead of bumping.
#   --from   feature branch to fast-forward in first (default: none; release HEAD).
#   --into   integration branch to release from (default: main).
#   --yes    commit + push automatically -- ONLY when the CHANGELOG has no unfilled
#            "- ..." placeholder. Without it, ship.sh stops after the bump so you can
#            fill the CHANGELOG and run the commit/push yourself.
#
# Portable + public-repo safe: no hardcoded user paths, private-org references, or
# embedded credentials. The repo root is derived from git; `git push` uses your own
# git credential helper.
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$ROOT"

BUMP=minor SET="" FROM="" INTO=main YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --bump) BUMP="${2:?}"; shift 2;;
    --set) SET="${2:?}"; shift 2;;
    --from) FROM="${2:?}"; shift 2;;
    --into) INTO="${2:?}"; shift 2;;
    --yes) YES=1; shift;;
    -h|--help) sed -n '2,33p' "$0"; exit 0;;
    *) echo "ship.sh: unknown arg: $1" >&2; exit 2;;
  esac
done

die(){ echo "ship.sh: $*" >&2; exit 1; }

# --- preflight -------------------------------------------------------------
[ -z "$(git status --porcelain)" ] || die "working tree not clean -- commit or stash first."
git rev-parse --verify "$INTO" >/dev/null 2>&1 || die "no integration branch '$INTO'."
cur="$(git rev-parse --abbrev-ref HEAD)"
[ "$cur" = "$INTO" ] || die "check out '$INTO' first (currently on '$cur')."

if [ -n "$FROM" ]; then
  echo "==> fast-forward $FROM into $INTO"
  git merge --ff-only "$FROM"
fi

# tests run AFTER the merge so they validate exactly what will ship.
echo "==> tests"
python3 -m unittest discover -s tests -t .

echo "==> version bump"
if [ -n "$SET" ]; then
  python3 scripts/release.py --set "$SET"
else
  python3 scripts/release.py --bump "$BUMP"
fi

VER="$(python3 -c 'import json;print(json.load(open(".claude-plugin/plugin.json"))["version"])')"
echo "==> version is now $VER"

# --- CHANGELOG guard: never ship an unfilled scaffold ----------------------
next="git commit -am \"release $VER: <summary>\" && git push origin $INTO"
if grep -qxF -- '- …' CHANGELOG.md; then
  echo
  echo "CHANGELOG.md still has unfilled '- …' placeholders under [$VER]."
  [ "$YES" = 1 ] && die "refusing to auto-commit an unfilled CHANGELOG."
  echo "Fill them in, then:"
  echo "    $next"
  exit 0
fi

if [ "$YES" = 1 ]; then
  echo "==> commit + push"
  git commit -am "release $VER"
  git push origin "$INTO"
  echo
  echo "Shipped $VER. In your Claude session:  /plugin update -> /reload-plugins -> /todo board  (then hard-reload the board tab)."
else
  echo
  echo "Bumped to $VER (not committed). Review the CHANGELOG, then:"
  echo "    $next"
  echo "Then in Claude:  /plugin update -> /reload-plugins -> /todo board  (hard-reload the tab)."
fi
