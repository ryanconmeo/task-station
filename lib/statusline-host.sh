#!/usr/bin/env bash
# claude-statusline-host:task-station
# task-station status-line HOST — the embedded compose conductor (see
# docs/STATUSLINE.md). Reads the Claude Code statusLine JSON on stdin, runs every
# executable provider in ${CLAUDE_CONFIG_DIR:-~/.claude}/statusline.d/ (lexical
# order) with that JSON piped to each on stdin + CLAUDE_STATUSLINE_WIDTH set,
# collects non-empty stdout, and joins the segments with a separator. Errors are
# isolated — a provider that exits non-zero / prints nothing / crashes is skipped
# and never breaks the bar. Self-sufficient: it composes task-station's OWN
# provider (50-task-station.sh) plus any other providers, with no external
# conductor dependency.
input=$(cat)
cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# Visible columns for provider truncation: the terminal width if we have one,
# else 0 (no limit / unknown). statusLine.command often runs without a tty.
w=$(tput cols 2>/dev/null) || w=""
[ -n "$w" ] || w=0

sep="${CLAUDE_STATUSLINE_SEP:-  │  }"

out=""
for f in "$cfg"/statusline.d/*; do
  [ -x "$f" ] || continue
  seg=$(printf '%s' "$input" | CLAUDE_STATUSLINE_WIDTH="$w" bash "$f" 2>/dev/null)
  status=$?
  # Skip on non-zero exit OR empty output — never let a provider break the bar.
  [ "$status" -eq 0 ] || continue
  [ -n "$seg" ] || continue
  if [ -z "$out" ]; then out="$seg"; else out="$out$sep$seg"; fi
done

[ -n "$out" ] && printf '%s\n' "$out"
exit 0
