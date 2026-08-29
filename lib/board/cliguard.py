"""The CLI's failure surface: an argparse parser that cannot fail silently.

WHY THIS EXISTS — A READ FAILURE THAT LOOKS EXACTLY LIKE AN EMPTY RESULT.
argparse writes every usage error to STDERR and exits 2. Almost nothing that
drives this engine reads stderr: the hook wrappers pipe stdout into the session,
the MCP server returns stdout as the tool result, `$(task-station …)` in a script
captures stdout, and an agent reading a tool result sees stdout. So a mistyped or
never-wired subcommand reached every one of those callers as ZERO BYTES on stdout
plus a non-zero exit — which is what a command that ran fine and had nothing to
say looks like from the outside.

`history --task 444` was the live case. Ten places in the board advertised the
command; it was never wired to a parser; and the only trace anybody got was
silence. Wiring `history` alone would have left the trap armed for the next typo,
so the trap itself is what this module removes.

THE CONTRACT, which is narrow and total:

    A USAGE ERROR ALWAYS WRITES A NON-EMPTY MESSAGE TO STDOUT,
    AND ALWAYS EXITS NON-ZERO.

STDOUT, not stderr, and not both. Both would duplicate every error in a terminal
that merges the two streams, and the whole defect is that the message was going
where the reader is not looking. `--help` is untouched: it was never an error, it
already prints to stdout, and it still exits 0.

WHAT ELSE THIS BUYS. Once the error is on the reader's stream it is worth making
it answer the reader's actual question, so an unknown subcommand gets a
did-you-mean line built from the real command list, and skips argparse's usage
blob — a wall of seventy command names is not an answer, and on this stream it
would land in a session's context.

Stdlib only. Imported by board.cli and by nothing else; it imports no board
module, so it stays a leaf and cannot participate in an import cycle.
"""
import argparse
import difflib
import re
import sys

__all__ = ["LoudParser", "did_you_mean"]


# argparse's own wording, which is what we have to read the offending word back
# out of. Matching the message rather than re-deriving the error keeps this in one
# place: whatever argparse decided was invalid is what we suggest against.
_INVALID_CHOICE = re.compile(r"argument ([^:]+): invalid choice: '?([^'\s,)]+)'?")


def did_you_mean(word, choices, limit=3):
    """The plausible commands for a mistyped `word`, best first, at most `limit`.

    Three passes, because a typo and a half-remembered name fail differently:
    an exact prefix (`hist` → `history`) is the strongest signal, a substring
    (`tint` → `prompt-tint`, `session-tint`) is next, and only then does edit
    distance get a say (`histry` → `history`). Deduped, order preserved. Empty
    when nothing is close enough — an empty list means "say nothing", never
    "guess anyway"."""
    w = (word or "").strip().lower()
    if not w:
        return []
    names = [str(c) for c in (choices or [])]
    out = []
    for pick in (
        [c for c in names if c.lower().startswith(w)],
        [c for c in names if w in c.lower()],
        difflib.get_close_matches(w, names, n=limit, cutoff=0.6),
    ):
        for c in pick:
            if c not in out:
                out.append(c)
    return out[:limit]


class LoudParser(argparse.ArgumentParser):
    """An ArgumentParser whose usage errors land on STDOUT and still exit 2.

    Subparsers inherit it for free — `add_subparsers` defaults `parser_class` to
    `type(self)` — so making the top-level parser one of these covers every
    subcommand's own flag errors too, and nothing at the call site has to
    remember."""

    #: Exit status for a usage error — argparse's own, kept so a caller that
    #: already distinguishes 2 (bad invocation) from 1 (ran, said no) is unaffected.
    USAGE_EXIT = 2

    def error(self, message):
        """Print the error to stdout, then exit non-zero. Never returns."""
        sys.stdout.write(self._error_text(message) + "\n")
        sys.stdout.flush()
        # exit() with no message: argparse would otherwise re-emit it on stderr.
        self.exit(self.USAGE_EXIT)

    # -- text ---------------------------------------------------------------

    def _error_text(self, message):
        """The whole message, as one block, without the trailing newline."""
        choices, is_command = self._offending_choice(message)
        if is_command:
            # The subcommand slot: the usage blob is seventy names wide and answers
            # nothing, so it is replaced by the one thing the reader wants.
            word = _INVALID_CHOICE.search(message).group(2)
            lines = ["%s: error: no such command: '%s'" % (self.prog, word)]
            near = did_you_mean(word, choices)
            if near:
                lines.append("Did you mean: %s" % ", ".join(near))
            lines.append("Run `%s --help` for the full command list." % self.prog)
            return "\n".join(lines)
        lines = [self.format_usage().rstrip(),
                 "%s: error: %s" % (self.prog, message)]
        if choices:
            word = _INVALID_CHOICE.search(message).group(2)
            near = did_you_mean(word, choices)
            if near:
                lines.append("Did you mean: %s" % ", ".join(near))
        return "\n".join(lines)

    def _offending_choice(self, message):
        """`(choices, is_command)` for the argument an invalid-choice error names.

        `choices` is that argument's full option list (None when the error is not
        an invalid choice, or names an argument this parser does not own).
        `is_command` is True only for the SUBCOMMAND slot — the one whose option
        list is too long to print."""
        m = _INVALID_CHOICE.search(message or "")
        if not m:
            return None, False
        named = m.group(1).strip()
        for action in self._actions:
            if action.choices is None:
                continue
            labels = set(action.option_strings)
            labels.add(action.dest)
            if action.metavar:
                labels.add(str(action.metavar))
            if named in labels:
                # Duck-typed rather than `isinstance(_SubParsersAction)`: only the
                # subparsers action can mint parsers, and it is a private class.
                return list(action.choices), hasattr(action, "add_parser")
        return None, False
