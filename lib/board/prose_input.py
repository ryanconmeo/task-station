"""Prose input: how a long, free-form value reaches the record INTACT.

THE FAILURE THIS EXISTS TO STOP. Every prose-bearing flag used to take its value
one way only — as a shell word. A shell word is not a string; it is a string the
shell has already rewritten. Backticks inside a double-quoted argument run as
command substitution, so the word is gone before argv is ever built:

    task-station update --task N --decision "the `turn` command found it"

stores `the  command found it` — the backticks AND the word between them are
already gone by the time this process starts — prints a stray `turn: command not
found` on stderr, and then reports SUCCESS and exits 0. Nothing downstream can
detect it: there is no corruption to find, only a shorter sentence that parses
fine. `$(...)`, `$VAR` and an unbalanced quote fail the same way, and a heredoc
or a file is the only input path a shell cannot touch.

THE CONVENTION. Two spellings, both already familiar, one meaning each:

    -        read the value from stdin   (the codebase's existing spelling —
                                          cmd_post_compact reads the compaction
                                          summary from stdin the same way)
    @PATH    read the value from a file
    @@...    a literal value that really does begin with `@` (one `@` is dropped)

ONE file spelling, not two. There is no `--decision-file` alongside `@PATH`: a
second spelling for the same thing is a second thing to keep correct, and
`@PATH` composes with a repeatable flag (`update --decision` is `action=append`)
where a paired `--<flag>-file` cannot.

WHAT IS DELIBERATELY UNCHANGED. Any other value is used VERBATIM — including one
that starts with a dash (only the single character `-` is the stdin reference) and
one that merely contains an `@` (only a LEADING `@` is the file sigil). stdin is
read only when `-` was actually passed, so an interactive call with no `-` never
blocks on a terminal that will never send EOF.

WHY EVERY FAILURE HERE IS LOUD. The bug above survived because it SUCCEEDED. So
this module refuses rather than guesses: a missing `@PATH` file, a `-` with no
pipe behind it, a second `-` in one command, and an input that read zero bytes
are all exit-2 refusals with a message. Storing `@/tmp/typo.md` as if it were the
prose would be the same class of silent-success bug in a new costume.

WHY THE HELP TEXT IS GENERATED. `annotate_prose_help()` appends the one-line
convention to every flag in PROSE_FLAGS by walking the built parser tree, so the
help a caller reads and the behaviour they get are driven by the SAME table and
cannot drift apart.
"""
import os
import sys

__all__ = ["PROSE_FLAGS", "STDIN_REF", "FILE_SIGIL", "HELP_SUFFIX",
           "resolve_prose_args", "resolve_prose_value", "annotate_prose_help"]

STDIN_REF = "-"          # the whole value, exactly this one character
FILE_SIGIL = "@"         # a LEADING @; `@@` escapes to a literal leading @

# Subcommand -> the dests whose values are PROSE: free-form text, authored by a
# human or a model, stored durably, and long enough that a shell will eventually
# mangle one. Deliberately NOT here: slugs (--memory), enumerations (--park),
# ids (--id), numbers, and refs (--task) — a shell cannot corrupt a token with no
# spaces or metacharacters in it, and `-`/`@` are likelier to be real values there.
PROSE_FLAGS = {
    "create":            ("summary", "goal"),
    "attach":            ("note",),
    "update":            ("summary", "append_summary", "state", "goal", "decision"),
    "turn":              ("ask",),
    "invoke":            ("ask",),
    "grade":             ("note", "why"),
    "heal":              ("note", "decision", "why", "noop"),
    "memo":              ("text", "decision", "noop"),
    "channel":           ("why", "report", "action"),
    "add-event":         ("text",),
    "add-ledger":        ("detail",),
    "capture-artifacts": ("text",),
}

HELP_SUFFIX = ("long prose: `-` reads the value from stdin, `@PATH` from a file "
               "(`@@` = a literal leading @), so shell quoting cannot corrupt it")


def _die(msg):
    """Refuse the way every other handler refuses: one line on stdout, exit 2."""
    print(msg)
    sys.exit(2)


def _strip_one_newline(text):
    """Drop the ONE trailing newline a pipe, a heredoc or an editor adds.

    Exactly one, never more: a file ending in a blank line the author meant to
    keep still keeps it, and every interior newline is untouched — which is the
    whole point of reading prose this way.
    """
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n") or text.endswith("\r"):
        return text[:-1]
    return text


def _read_stdin(flag, state):
    if state.get("stdin_flag"):
        _die("--%s: stdin was already read for --%s. A command has ONE stdin, so "
             "only one flag per call may take `-`; give the others `@PATH`."
             % (flag, state["stdin_flag"]))
    stream = getattr(sys, "stdin", None)
    if stream is None:
        _die("--%s -: this process has no stdin to read." % flag)
    try:
        tty = stream.isatty()
    except Exception:
        tty = False
    if tty:
        _die("--%s -: stdin is an interactive terminal, so there is nothing to "
             "read and waiting would hang. Pipe the text in, use `@PATH`, or "
             "pass the value directly." % flag)
    state["stdin_flag"] = flag
    try:
        return stream.read()
    except Exception as e:
        _die("--%s -: could not read stdin (%s)." % (flag, e))


def _read_file(flag, path):
    if not path:
        _die("--%s: `@` with no path after it. Write `@PATH`, or `@@` for a "
             "value that really is a single @." % flag)
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        _die("--%s @%s: no such file. Refusing rather than storing the literal "
             "text `@%s` as the value — that is how a typo becomes a record."
             % (flag, path, path))
    if os.path.isdir(expanded):
        _die("--%s @%s: that is a directory, not a file." % (flag, path))
    try:
        with open(expanded, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as e:
        _die("--%s @%s: could not read it (%s)." % (flag, path, e))


def resolve_prose_value(raw, flag, state=None):
    """Resolve ONE prose value. Returns `raw` untouched unless it is `-` or `@…`.

    `state` carries the per-command "stdin already consumed" fact; pass the same
    dict for every flag in one command so a second `-` is refused instead of
    silently reading an exhausted stream.
    """
    if not isinstance(raw, str):
        return raw                          # None, or the True of a bare nargs="?" flag
    if state is None:
        state = {}
    if raw == STDIN_REF:
        text = _strip_one_newline(_read_stdin(flag, state))
        source = "stdin"
    elif raw.startswith(FILE_SIGIL * 2):
        return raw[1:]                      # @@foo -> the literal value @foo
    elif raw.startswith(FILE_SIGIL):
        text = _strip_one_newline(_read_file(flag, raw[1:]))
        source = "@" + raw[1:]
    else:
        return raw                          # the plain-string path: verbatim, always
    if not text:
        _die("--%s %s: read 0 bytes. An empty prose value is almost always a "
             "command that produced nothing; pass '' directly if you really "
             "mean to clear the field." % (flag, source))
    return text


def resolve_prose_args(a):
    """Resolve every prose flag on a parsed namespace, in place.

    Called once between `parse_args` and dispatch, so a handler only ever sees a
    real string and no handler needs to know this convention exists. Repeatable
    flags (`update --decision`) resolve element-wise, which is why `@PATH` and
    not `--decision-file` is the file spelling.
    """
    dests = PROSE_FLAGS.get(getattr(a, "cmd", None) or "")
    if not dests:
        return a
    state = {}
    for dest in dests:
        if not hasattr(a, dest):
            continue                        # the table outliving a removed flag is not fatal
        raw = getattr(a, dest)
        flag = dest.replace("_", "-")
        if isinstance(raw, list):
            setattr(a, dest, [resolve_prose_value(v, flag, state) for v in raw])
        else:
            setattr(a, dest, resolve_prose_value(raw, flag, state))
    return a


def annotate_prose_help(subparsers):
    """Append HELP_SUFFIX to the help of every flag in PROSE_FLAGS.

    Driven by the same table that does the resolving, so `--help` can never
    advertise a flag that does not accept `-`, or stay silent about one that
    does. Best-effort by design: this reaches into argparse's action list, and a
    CLI that fails to build is worse than one whose help is a line short.
    """
    try:
        choices = getattr(subparsers, "choices", None) or {}
        for name, dests in PROSE_FLAGS.items():
            parser = choices.get(name)
            if parser is None:
                continue
            wanted = set(dests)
            for action in getattr(parser, "_actions", []):
                if action.dest not in wanted or not action.option_strings:
                    continue
                prior = (action.help or "").rstrip()
                # Several of these flags carry no help at all today, so the suffix
                # becomes the whole line rather than a dangling continuation of one.
                action.help = (prior + " — " + HELP_SUFFIX) if prior else HELP_SUFFIX
    except Exception:
        pass
    return subparsers
