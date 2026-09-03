#!/usr/bin/env bash
# WorktreeCreate hook: CREATE the worktree and print its absolute path as the FIRST
# stdout line. This hook REPLACES Claude Code's own creation — any non-zero exit fails
# the operation — so it is installed only on request, into the user's own
# settings.json, by `task-station config --worktree-hook on`. It is NEVER in
# hooks/hooks.json: shipping it in the plugin manifest would put it in front of every
# worktree (including Claude's own subagent isolation) on every install.
#
# THREE THINGS ARE DELIBERATELY DIFFERENT FROM EVERY OTHER HOOK HERE:
#
#   1. NO TASK_STATION_SUPPRESS GUARD. The other hooks exit early inside a delegated
#      worker because task tracking is the hub's job. Exiting early HERE would print
#      no path, which is a failed worktree creation — and a suppressed worker asking
#      for a worktree needs one just as much as anyone else. This hook provisions; it
#      does not track.
#   2. NO CLAUDE_PLUGIN_ROOT. This runs from the user's settings.json, where that
#      variable is not set, so the engine is resolved relative to THIS script. The
#      installed command points at `<config>/task-station-engine/../hooks/…`, and
#      task-station-engine is a symlink to the active lib/ — so `..` from it is the
#      plugin root and this resolution survives a `/plugin update`.
#   3. NOTHING IS MASKED. ts_run exists to make an exit code harmless; here the exit
#      code is the contract. `exec` hands the engine our stdin, stdout and exit status
#      directly — one process, no wrapper that could swallow or reorder the path line.
#
# IF THE ENGINE IS MISSING (plugin uninstalled while this entry is still installed)
# this hook fails, and so does every worktree creation, until you run:
#     task-station config --worktree-hook off
# …or delete the WorktreeCreate entry from settings.json by hand. That is the price of
# a hook that replaces a core operation, and it is why the installer says so too.
#
# NOTE: stdin is NOT read here — the payload is handed straight to the engine by exec.
#
# AND NO `cd`. The installed path deliberately contains `task-station-engine/..`, and
# bash's `cd` (logical by default) collapses `..` TEXTUALLY — it would try
# `<config>/hooks`, which does not exist, and every worktree creation on the machine
# would fail. Left as a plain string, the kernel resolves the symlink first and `..`
# second, which is the whole point of routing through the engine symlink.
here=$(dirname "${BASH_SOURCE[0]:-$0}")
engine="$here/../lib/task-station.py"
if [ ! -f "$engine" ]; then
  echo "task-station: worktree provisioner is installed but its engine is missing at $engine" >&2
  echo 'task-station: restore native worktree creation with: task-station config --worktree-hook off' >&2
  exit 1
fi
exec python3 "$engine" hook worktree-create
