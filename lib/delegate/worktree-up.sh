#!/usr/bin/env bash
# worktree-up.sh — create a git worktree for a new branch.
#
# Usage:
#   worktree-up.sh <worktree-path> <branch> [base-ref]
#     <worktree-path>  absolute path for the new worktree
#     <branch>         branch name — new, or existing local/remote
#     [base-ref]       base for a NEW branch (default: auto-detected from
#                      origin/HEAD, or origin/main, or main)
#
# Branch resolution:
#   - local branch exists       -> check it out
#   - only remote branch exists -> create a tracking branch
#   - neither                   -> create <branch> from <base-ref>
#
# After creating the worktree this script symlinks any .env.local from the
# main checkout (so gitignored secrets stay in sync without duplication).
# It does NOT install language-specific dependencies — add repo-specific
# bootstrap steps after sourcing this script, or extend it per-project.
set -euo pipefail

WT="${1:?usage: worktree-up.sh <worktree-path> <branch> [base-ref]}"
BRANCH="${2:?missing <branch>}"
BASE="${3:-}"

# Resolve the main worktree (first entry of `git worktree list`).
MAIN="$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')"

# Auto-detect base ref when not supplied.
if [ -z "$BASE" ]; then
  BASE="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [ -z "$BASE" ]; then
    if git show-ref --verify --quiet refs/remotes/origin/main 2>/dev/null; then
      BASE="origin/main"
    else
      BASE="main"
    fi
  fi
fi

git fetch origin --quiet || true

if [ -d "$WT" ]; then
  echo "worktree-up: path already exists, reusing: $WT"
elif git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git worktree add "$WT" "$BRANCH"                               # local branch
elif git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git worktree add "$WT" --track -b "$BRANCH" "origin/$BRANCH"   # remote branch
else
  git worktree add "$WT" -b "$BRANCH" "$BASE"                    # brand-new branch
fi

# Symlink gitignored root env file(s) holding secrets so they stay in sync
# with the main checkout without being copied.
linked=0
for name in .env.local .env; do
  src="$MAIN/$name"
  [ -e "$src" ] || continue
  if [ ! -e "$WT/$name" ]; then
    ln -s "$src" "$WT/$name"
    linked=$((linked + 1))
  fi
done
echo "worktree-up: linked $linked env file(s) from $MAIN"

echo "worktree-up: ready"
echo "  path   : $WT"
echo "  branch : $BRANCH"
echo "  base   : $BASE"
