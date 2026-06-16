#!/usr/bin/env bash
# claude-todo: install tint aliases into ~/.zshrc
# Appends per-category shell functions/aliases inside a fenced block so the
# block is removable and idempotent (skipped if the fence already present).
# Each function switches the ORIGINATING window (not the front/focused window)
# to the matching profile via the bundled set-term-profile.sh script.
# Profile creation inside Terminal.app is NOT reliably scriptable without
# manipulating plist internals — this script appends the aliases and prints
# clear manual steps for creating the matching profiles instead.
[[ "$OSTYPE" == darwin* ]] || exit 0

set -euo pipefail

FENCE_OPEN="# >>> claude-todo tint >>>"
FENCE_CLOSE="# <<< claude-todo tint <<<"
ZSHRC="$HOME/.zshrc"

# Colours used by claude-todo categories — derived from categories.py so the
# alias names always match the real category keys (no drift); falls back to the
# shipped 12 keys if python is unavailable.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_FALLBACK=(red orange yellow green blue purple black pink white silver gold brown)
COLORS=($(python3 -c "import sys; sys.path.insert(0, '$SCRIPT_DIR'); import categories; print(' '.join(categories.CATEGORIES))" 2>/dev/null)) || COLORS=("${_FALLBACK[@]}")
[ "${#COLORS[@]}" -gt 0 ] || COLORS=("${_FALLBACK[@]}")

# ----------------------------------------------------------------- idempotent --
if grep -qF "$FENCE_OPEN" "$ZSHRC" 2>/dev/null; then
    echo "claude-todo tint: fence already present in $ZSHRC — nothing to do."
    echo "To reinstall, remove the block between '$FENCE_OPEN' and '$FENCE_CLOSE' and re-run."
    exit 0
fi

# --------------------------------------------------- build alias block content --
BLOCK=""
BLOCK+="$FENCE_OPEN"$'\n'
BLOCK+="# claude-todo Terminal.app tint aliases — managed block, do not edit by hand."$'\n'
BLOCK+="# Each function switches the current Terminal.app tab to the matching profile."$'\n'
for color in "${COLORS[@]}"; do
    # Capitalise first letter for the profile name (e.g. "green" → "Green")
    profile="${color^}"
    BLOCK+="${color}() { bash \"\$HOME/.claude/todo-engine/set-term-profile.sh\" '${profile}' 2>/dev/null; }"$'\n'
done
BLOCK+="$FENCE_CLOSE"

# ----------------------------------------------------- append to ~/.zshrc ------
printf '\n%s\n' "$BLOCK" >> "$ZSHRC"
echo "claude-todo tint: aliases appended to $ZSHRC."
echo "Run 'source $ZSHRC' (or open a new terminal) to activate them."
echo ""

# ------------------------------------------------- manual profile instructions --
echo "========================================================================"
echo "  NEXT STEP — create Terminal.app profiles named after each colour"
echo "========================================================================"
echo ""
echo "Terminal.app profile creation cannot be scripted reliably without"
echo "editing internal plist files. Please create the profiles manually:"
echo ""
echo "  1. Open Terminal → Preferences (⌘,) → Profiles tab."
echo "  2. For each of the following names, duplicate an existing profile"
echo "     (click the gear icon → Duplicate Profile), rename it exactly as"
echo "     shown (case-sensitive), and set the background colour as desired:"
echo ""
for color in "${COLORS[@]}"; do
    echo "       ${color^}"
done
echo ""
echo "  3. The aliases appended to ~/.zshrc will switch to the matching profile"
echo "     by that exact name. For example, running 'green' in a tab will"
echo "     switch it to the 'Green' Terminal.app profile."
echo ""
echo "  Once the profiles exist, test with:  source ~/.zshrc && green"
echo "========================================================================"
