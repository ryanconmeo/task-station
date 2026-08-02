#!/usr/bin/env bash
# SessionStart hook: surface open tasks (or the already-attached task) so Claude
# can recognise a resume. Emitted as SessionStart additionalContext JSON.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
# Shared helper: ts_run/ts_capture keep every masked call non-fatal (exactly as
# before) but record a non-zero exit to <data_dir>/logs/hook-health.log so a
# permanently-broken call can no longer hide. The stubs below mean a missing or
# corrupt lib degrades to the old verbatim behaviour instead of breaking the hook.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]:-$0}")/_ts_lib.sh" 2>/dev/null || true
if ! command -v ts_run >/dev/null 2>&1; then      # no lib → old behaviour verbatim
  ts_run() { shift; "$@" >/dev/null 2>&1 || true; }
  ts_capture() { shift; "$@" 2>/dev/null || true; }
fi
# Parse stdin with python3 (hard requirement) instead of jq; hookjson.py mirrors
# `jq -r '.path // default'` and is a silent no-op on malformed input.
session_id=$(printf '%s' "$input" | python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookjson.py" session_id unknown)
source=$(printf '%s' "$input" | python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookjson.py" source)

# Publish a stable, version-independent handle to this plugin so non-plugin
# callers (delegate invocations, the status line) don't chase the versioned
# plugin-cache dir. Refreshed every session, so it self-heals across updates.
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
  _cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  ts_run engine-symlink ln -sfn "$CLAUDE_PLUGIN_ROOT/lib" "$_cfg/task-station-engine"
  # Self-register a composable status-line segment — ONLY when the opt-in status
  # bar is on (config --statusline on). Off by default, so we never write into a
  # user's statusline.d/ unbidden. The plugin path is frozen in at write time
  # (re-frozen each session) because $CLAUDE_PLUGIN_ROOT does not resolve in the
  # status-line command context. See docs/STATUSLINE.md for the provider contract.
  if [ "$(ts_capture statusline-get python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" config --statusline-get)" = "on" ]; then
    ts_run statusline-dir mkdir -p "$_cfg/statusline.d"
    # A heredoc redirect can't be an argv command, so this one write stays masked;
    # the generated segment runs OUTSIDE the hook (no lib to source) and so does its
    # own `2>/dev/null` below.
    cat > "$_cfg/statusline.d/50-task-station.sh" <<SEG 2>/dev/null
#!/usr/bin/env bash
# task-station-managed statusline provider (SessionStart). Regenerated each session; do not edit.
sid=\$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
[ -n "\$sid" ] || exit 0
exec python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" whoami --session "\$sid" --statusline --width "\${CLAUDE_STATUSLINE_WIDTH:-0}"
SEG
    ts_run statusline-chmod chmod +x "$_cfg/statusline.d/50-task-station.sh"
  fi
fi

if [ -n "$CLAUDE_PLUGIN_ROOT" ] && [ "$(ts_capture bare-cmds-get python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" config --bare-cmds-get)" = "on" ]; then
  _cmds="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/commands"; ts_run bare-cmds-dir mkdir -p "$_cmds"
  for c in todo 'done' repos pin unpin save heal history prompts glossary brief; do
    _dst="$_cmds/$c.md"; _src="$CLAUDE_PLUGIN_ROOT/commands/$c.md"
    # The head|grep predicate stays masked on purpose: a non-zero exit here is
    # normal control flow (no file / not ours), not a broken hook.
    if [ ! -e "$_dst" ] || head -1 "$_dst" 2>/dev/null | grep -q 'task-station-managed'; then
      { printf '%s\n' "<!-- task-station-managed: bare alias for /task-station:$c -->";
        ts_capture bare-alias-render sed 's#${CLAUDE_PLUGIN_ROOT}/lib#'"${CLAUDE_CONFIG_DIR:-$HOME/.claude}"'/task-station-engine#g' "$_src"; } > "$_dst" 2>/dev/null
    fi
  done
fi

ctx=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" session-start --session "$session_id" --source "$source")
_seen="${TASK_STATION_HOME:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/task-station-data}/.setup-nudged"
nudge=""
if [ ! -e "$_seen" ] && [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
  if [ -z "$(ts_capture workspace-dirs-get python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" config --workspace-dirs-get)" ]; then
    nudge='task-station installed. Finish optional setup any time with `task-station config`.'
  fi
  ts_run setup-stamp-dir mkdir -p "$(dirname "$_seen")"; : > "$_seen"
fi
[ -n "$nudge" ] && ctx="${ctx}${ctx:+$'\n'}$nudge"

# Tint the originating window to the attached task's category on attach/resume —
# the full-palette escape, written to the real TTY (same rail as the title; not
# stdout, which carries the SessionStart JSON). No-op when unattached or no tint.
tint=$(ts_capture session-tint python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" session-tint --session "$session_id")
if [ -n "$tint" ]; then
  # origin-tty.sh exits 1 whenever the tty is undeterminable — normal, not a
  # failure — and the tty write is a redirect, so both stay masked as before.
  _dev=$(bash "${CLAUDE_PLUGIN_ROOT}/lib/origin-tty.sh" 2>/dev/null)
  printf '%s' "$tint" > "${_dev:-/dev/tty}" 2>/dev/null
fi

# Auto-label the window for an attached task (task-station-<seq> · <title>) — the hub can't
# be programmatically renamed, but its title CAN be set via the SessionStart hook.
# Obsidian sandbox auto-flush (Fix B): heal any exports the SANDBOXED hot path
# couldn't write (vault under ~/Documents/iCloud). Hooks run UNSANDBOXED, so this
# write succeeds. Silent + self-gating (--quiet: no-op unless a task is pending);
# routed to /dev/null so it never pollutes the SessionStart JSON on stdout.
ts_run obsidian-flush python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" obsidian --flush --quiet
# Usage ledger auto-flush (WS1): same rail as the obsidian flush — rescan open/active
# tasks' transcripts so a resumed session's /todo detail shows current derived $/mix.
# Local-only, self-gating on `usage_tracking`, error-swallowing; routed to /dev/null
# so it never pollutes the SessionStart JSON on stdout.
ts_run usage-flush python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" usage --flush --quiet
# Orphan sweep: stop background workers whose SPAWNING HUB SESSION IS GONE (a hub that
# crashed leaves its workers running forever). Same rail as the flushes above — stdout
# is routed away so it can never pollute the SessionStart JSON, and a failed sweep
# never fails the session start (it is now RECORDED instead of silently swallowed).
# Reaps nothing whenever liveness is unknown; `--session` is passed so it can never
# reap this session's own workers.
ts_run sweep-orphans python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" sweep-orphans --session "$session_id"

title=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" session-title --session "$session_id")
if [ -n "$ctx" ] || [ -n "$title" ]; then
  # Build the SessionStart output JSON with python3 (jq-free). ctx/title arrive as
  # argv, so embedded quotes/newlines need no escaping; empty fields are omitted,
  # matching the old jq construction exactly.
  python3 -c 'import json, sys
ctx, title = sys.argv[1], sys.argv[2]
inner = {"hookEventName": "SessionStart"}
if ctx: inner["additionalContext"] = ctx
if title: inner["sessionTitle"] = title
print(json.dumps({"hookSpecificOutput": inner}))' "$ctx" "$title"
fi
exit 0
