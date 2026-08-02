#!/usr/bin/env bash
# _ts_lib.sh — shared helpers for the task-station hooks. SOURCE it, don't run it.
#
# WHY THIS EXISTS. A hook must never fail or slow a session, so every task-station
# hook call is masked. That part of the design is correct. The cost was that a
# masked call is also INVISIBLE: any one of them could be permanently broken and
# nobody would ever find out (the orphan sweep was only proven working by running
# it by hand with the mask removed).
#
# ts_run / ts_capture keep failures harmless AND visible. They run the command,
# discard stdout exactly as the old masks did (ts_capture passes it through, for
# the `x=$(...)` call sites), capture stderr, and on a NON-ZERO exit append one
# line to <data_dir>/logs/hook-health.log:
#
#     <iso-8601 utc>\t<label>\t<exit code>\t<last non-blank line of stderr>
#
# …then still return success, so the hook carries on regardless. `task-station
# hook-health` reads that log, and SessionStart nags when it holds recent entries.
#
# Labels are stable and descriptive (sweep-orphans, obsidian-flush, usage-flush …)
# because they are the only handle a human gets on which call site broke.

# Max lines kept in the health log — it can never grow without limit. Overridable
# (tests set it low); a caller's exported value wins.
: "${TS_HOOK_LOG_MAX:=200}"

ts_data_dir() {
  # Mirror of lib/paths.py::data_dir(), same precedence. Duplicated in shell on
  # purpose: logging a failure must not cost a python3 start-up. (Like the inline
  # resolution the hooks already do, a literal `~` in an override isn't expanded.)
  if [ -n "${TASK_STATION_HOME:-}" ]; then printf '%s\n' "$TASK_STATION_HOME"
  elif [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then printf '%s\n' "$CLAUDE_CONFIG_DIR/task-station-data"
  elif [ -n "${XDG_STATE_HOME:-}" ]; then printf '%s\n' "$XDG_STATE_HOME/task-station"
  else printf '%s\n' "$HOME/.claude/task-station-data"
  fi
}

ts_health_log() { printf '%s\n' "$(ts_data_dir)/logs/hook-health.log"; }

# ts_health_record <label> <exit-code> <stderr-file>
# Append one bounded, single-line record. Every step is best-effort: a hook must
# never be harmed by the machinery that reports its failures.
ts_health_record() {
  local label="$1" code="$2" errfile="$3" log last n tmp
  log=$(ts_health_log) || return 0
  mkdir -p "$(dirname "$log")" 2>/dev/null || return 0
  # Last NON-BLANK stderr line, tabs flattened to spaces so it can never forge a
  # field, CRs dropped. One failure is always exactly one line.
  last=""
  if [ -s "$errfile" ]; then
    last=$(tr '\t\r' '  ' < "$errfile" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -n 1)
  fi
  printf '%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$label" "$code" "$last" >> "$log" 2>/dev/null || return 0
  # Bound it: keep only the newest TS_HOOK_LOG_MAX lines.
  n=$(wc -l < "$log" 2>/dev/null | tr -d ' ')
  case "$n" in ''|*[!0-9]*) return 0;; esac
  if [ "$n" -gt "$TS_HOOK_LOG_MAX" ]; then
    tmp="$log.$$"
    if tail -n "$TS_HOOK_LOG_MAX" "$log" > "$tmp" 2>/dev/null; then
      mv -f "$tmp" "$log" 2>/dev/null || rm -f "$tmp" 2>/dev/null
    else
      rm -f "$tmp" 2>/dev/null
    fi
  fi
  return 0
}

# ts_run <label> <command...>   — stdout DISCARDED (the old `>/dev/null 2>&1 || true`)
# ts_capture <label> <command...> — stdout PASSED THROUGH (for `x=$(...)` sites)
# Both: stderr captured, non-zero exit logged, ALWAYS return 0. stdin is inherited,
# so a logged command can still sit at the end of a pipeline.
_ts_run_impl() {
  local keep_stdout="${1:-0}" label="${2:-}"
  [ $# -ge 3 ] || return 0            # label with no command → nothing to run
  shift 2
  local err rc=0
  # A template is REQUIRED: BSD mktemp (macOS) rejects a bare `mktemp`.
  err=$(mktemp "${TMPDIR:-/tmp}/ts-hook.XXXXXX" 2>/dev/null) || err=""
  if [ -z "$err" ]; then                      # no temp file → old behaviour verbatim
    if [ "$keep_stdout" = "1" ]; then "$@" 2>/dev/null || rc=$?
    else "$@" >/dev/null 2>&1 || rc=$?; fi
  elif [ "$keep_stdout" = "1" ]; then
    "$@" 2>"$err" || rc=$?
  else
    "$@" >/dev/null 2>"$err" || rc=$?
  fi
  [ "$rc" -ne 0 ] && ts_health_record "$label" "$rc" "$err"
  [ -n "$err" ] && rm -f "$err" 2>/dev/null
  return 0
}

ts_run() { _ts_run_impl 0 "$@"; }
ts_capture() { _ts_run_impl 1 "$@"; }
