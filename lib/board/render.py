"""Ultracode fan-out hints, the commands footer, and the list/search/prompt formatters."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
from board.memos import *
from board.sessions import *
import os
import re

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "fanout_worthy", "ultracode_signal", "ultracode_advisory", "ultracode_steering",
    "_command_label", "_bare_commands", "_commands_footer_note", "_commands_help",
    "commands_footer", "commands_footer_md",
    "_live_marker",
    "_status_dot", "_search_snippet_clean", "_format_search",
    "_format_prompts", "_format_prompts_md", "_format_prompts_view",
    "_tildify", "_format_session_row",
]


def fanout_worthy(task):
    """True when a task genuinely warrants multi-agent BREADTH (the ultracode
    fan-out hint). PURE and DERIVED — reads only the task's effort + category and
    adds no task state, so it can be recomputed anywhere. TRUE when effort is L/XL
    (any category), OR the category is one of the breadth slots
    (REVIEW/RESEARCH/DATA) at M+. FALSE for xs/s, an unset/unknown effort, or
    a plain open (○) question / untracked task (no effort)."""
    eff = task.get("effort") if isinstance(task, dict) else None
    if eff not in EFFORT_ORDER:          # unset / unknown effort → never worthy
        return False
    i = EFFORT_ORDER.index(eff)
    if i >= EFFORT_ORDER.index(_FANOUT_ANY_MIN):     # L / XL — any category
        return True
    color = task.get("color")
    key = cats.resolve(color) if (cats and hasattr(cats, "resolve")) else color
    return key in FANOUT_CATEGORIES and i >= EFFORT_ORDER.index(_FANOUT_BREADTH_MIN)


def ultracode_signal(prompt):
    """True when this turn carries an ultracode opt-in token: the word-boundary
    token `ultracode` (case-insensitive) anywhere in the prompt — the SAME trigger
    Claude Code's harness uses to switch on multi-agent orchestration.

    This is the ONLY reliable per-turn signal available to the UserPromptSubmit
    hook. The hook input (and TASK_STATION_PROMPT) carry no 'standing ultracode
    mode' field or env var, so we deliberately do NOT attempt to detect a standing
    mode here — inventing a fragile detector would mis-steer the model. Standing-
    mode users still get the human advisory; only the model-facing steering is
    keyed to this token. See the design spec."""
    return bool(prompt) and bool(_ULTRACODE_RE.search(prompt))


def ultracode_advisory(task):
    """The HUMAN-facing fan-out advisory (default mode): suggest the human opt into
    Claude Code's `ultracode` multi-agent breadth for a worthy task's read/analyze/
    design/review phases. The human opts in by typing the keyword — this NEVER
    fires orchestration and NEVER suggests pointing a workflow at repo writes.
    Returns '' unless the task is fan-out-worthy AND ultracode hints are enabled."""
    import config
    if not (config.ultracode_hints_enabled() and fanout_worthy(task)):
        return ""
    return ("ultracode: this task is fan-out-worthy (effort %s). For its "
            "read/analyze/design/review phases, running it with `ultracode` gives "
            "multi-agent breadth (if your Claude Code supports it). Repo edits still "
            "go through delegation (worktree + story/PR) — never point a workflow at "
            "writes." % task.get("effort"))


def ultracode_steering():
    """The MODEL-facing steering block, printed by the per-prompt hook ONLY on an
    ultracode turn (signal present) for a fan-out-worthy attached task. The harness
    is ALREADY orchestrating; this just steers breadth to think-phases and keeps
    every repo write on the sanctioned delegation path (worktree + story/PR)."""
    return ("ultracode active on a fan-out-worthy task: fan subagents out for "
            "read/analyze/design/review/verify ONLY (hub context — no repo "
            "CLAUDE.md/hooks/build env). Route every repo MUTATION through "
            "task-station delegation (a worktree worker off the repo's base branch, "
            "with story + PR). Never edit/build/test in workflow subagents.")


def _command_label(label, bare):
    """The label shown for one command. When `bare` is False, rewrite the LEADING
    bare token (/todo, /done, /pin) to its /task-station: form, preserving any
    trailing args (e.g. "/todo <n>" → "/task-station:todo <n>"); an already-
    namespaced token is untouched. When True, return the label as-is. Mirrors
    tools/render_board.py's _command_label so the terminal footer and the HTML
    board agree on how bare-state is shown."""
    if bare:
        return label
    for tok in ("/todo", "/done", "/pin"):
        if label == tok or label.startswith(tok + " "):
            return "/task-station:" + tok[1:] + label[len(tok):]
    return label


def _bare_commands():
    """config.bare_commands(), defaulting to False (the config default) when
    config is unavailable — the same guarded-import pattern used elsewhere in
    this file (e.g. write_board())."""
    try:
        import config
        return config.bare_commands()
    except Exception:
        return False


def _commands_footer_note(bare):
    """The bare-state note appended under the Commands footer — mirrors the HTML
    board's helpnote (tools/render_board.py's _help_panel) so both surfaces agree
    on how to read/change the bare-cmds setting. TWO lines (joined by a newline) so
    the /task-station: prefix statement always stands on its own line."""
    if bare:
        return ("bare-cmds is on — /todo, /done, /save, /heal, /pin, /history, /repos work directly.\n"
                "The /task-station: prefix also always works.")
    return ("bare-cmds is off — use the /task-station: prefix (shown).\n"
            "Enable the short /todo, /done, /save, /heal, /pin, /history, /repos aliases with "
            "/task-station:config --bare-cmds on.")


def _commands_help(bare):
    """The aligned command/description lines + the notation legend (no code fence).
    Column auto-sized to the widest LABEL (after the bare/namespaced rewrite) so
    the descriptions still line up in either state."""
    labeled = [(_command_label(cmd, bare), desc) for cmd, desc in _COMMANDS_HELP]
    w = max(len(cmd) for cmd, _ in labeled) + 4
    lines = ["%s%s" % (cmd.ljust(w), desc) for cmd, desc in labeled]
    return "\n".join(lines) + "\n\n" + _COMMANDS_LEGEND


def commands_footer():
    """The authoritative `/todo` command help as an aligned block (ASCII list
    footer), bare-aware like the HTML board's Commands panel: labels show their
    /task-station: form unless config.bare_commands() is on, followed by a note
    on how to read/change that state. Same command+legend content as
    commands_footer_md(), minus the Markdown code fence."""
    bare = g("_bare_commands")()
    return _commands_help(bare) + "\n\n" + _commands_footer_note(bare)


def commands_footer_md():
    """The same aligned command help, under a **Commands** heading and wrapped in a
    fenced code block so it renders verbatim/monospace in the Markdown `/todo`
    board and the README; the bare-state note sits outside the fence (prose, not
    command syntax). Decoupled from the ASCII one-liner — built from the shared
    _COMMANDS_HELP source, not by splitting commands_footer()."""
    bare = g("_bare_commands")()
    return ("**Commands**\n\n```\n" + _commands_help(bare) + "\n```\n\n"
            + _commands_footer_note(bare))


def _live_marker(task):
    """` ⧉N` when more than one session is concurrently attached to this task,
    else "". Every open task trivially has ≥1 live session, so the marker only
    appears on the interesting case (N > 1) and never clutters the common row."""
    n = live_session_count(task)
    return " ⧉%d" % n if n > 1 else ""


def _status_dot(task):
    """The board glyph for a task: ○ open · ● active · ✕ closed — for the search
    hit list (mirrors the detail/board glyphs)."""
    cur = task_status(task)
    if cur == STATUS_CLOSED:
        return STATUS_GLYPH_CLOSED
    return STATUS_GLYPH.get(cur, STATUS_GLYPH[STATUS_OPEN])


def _search_snippet_clean(s):
    """Whitespace-collapse a snippet to a single line (FTS5 fragments can span the
    multi-line content blob)."""
    return re.sub(r"\s+", " ", s or "").strip()


def _format_search(query, rows, want):
    """Tier-1 search output: a token-economical ranked hit list — `#seq <dot> title`
    plus a one-line match-context snippet each — under a summary header, with a note
    pointing at the digest / full-trail commands. Empty-result case says so plainly."""
    ensure_seqs()   # so every hit has a stable #seq to show / address by
    label = {"all": "all tasks", "open": "open + active tasks",
             "closed": "closed tasks"}.get(want, "all tasks")
    if not rows:
        return ("No %s match '%s'.\n"
                "Try fewer/broader terms, or --all to include closed tasks."
                % (label, query))
    out = ["Search '%s' — %d hit%s (%s), most relevant first:"
           % (query, len(rows), "" if len(rows) == 1 else "s", label)]
    for task, snippet in rows:
        seq = task.get("seq", task["id"][:8])
        out.append("  #%s %s %s" % (seq, _status_dot(task), task.get("title", "")))
        snip = _search_snippet_clean(snippet)
        if snip:
            out.append("      %s" % snip)
    out.append("")
    out.append("task-station search --detail <seq> for the digest; "
               "/todo <n> history for the full trail.")
    return "\n".join(out)


def _format_prompts(task, prompts, include_compact=False):
    """Terminal render of `prompts --task`: by DEFAULT the curated view — only genuine
    human-typed prompts, each followed by Claude's last-bullet reply (`↳ …`), oldest
    first, session-attributed. `--all` (include_compact=True) instead prints the RAW,
    complete trail (every kind: commands, compaction rows, wrappers) with no replies —
    the escape hatch. Long lines are clipped for the terminal (full text in --json/--md)."""
    seq = task.get("seq", task["id"][:8])
    out = ["# Prompts — task %s · %s" % (seq, task.get("title", ""))]
    if not prompts:
        out.append("  No prompts captured for this task yet — prompt capture is on "
                   "by default (see `config --usage-prompts`); the Stop/SessionStart "
                   "hooks flush the ledger.")
        return "\n".join(out)
    if include_compact:
        # RAW trail (every row, no replies) — the --all escape hatch.
        for p in prompts:
            text = " ".join((p.get("text") or "").split())
            if len(text) > 100:
                text = text[:99].rstrip() + "…"
            out.append("%-16s  [%s]  %s"
                       % (_prompt_ts(p.get("ts")), _prompt_session_tag(p), text))
        return "\n".join(out)
    rows = _human_prompts_with_replies(prompts)
    if not rows:
        out.append("  No human-typed prompts captured yet (only commands / generated "
                   "rows). Use `--all` for the complete raw trail.")
        return "\n".join(out)
    for p in rows:
        text = " ".join((p.get("text") or "").split())
        if len(text) > 100:
            text = text[:99].rstrip() + "…"
        out.append("%-16s  [%s]  %s"
                   % (_prompt_ts(p.get("ts")), _prompt_session_tag(p), text))
        reply = " ".join((p.get("reply") or "").split())
        if reply:
            if len(reply) > 100:
                reply = reply[:99].rstrip() + "…"
            out.append("%-16s  %s↳ %s" % ("", " " * (len(_prompt_session_tag(p)) + 4), reply))
    return "\n".join(out)


def _format_prompts_md(task, prompts, include_compact=False):
    """Markdown render of the prompt trail — the SHAREABLE artifact ("show others exactly
    what I prompted to get the end result"). By DEFAULT the curated view: only human-typed
    prompts (full text, as a blockquote), each followed by Claude's last-bullet reply
    (`↳ …`). `--all` (include_compact=True) prints the RAW complete trail (every kind, no
    replies) — commands as a code span, prose as a blockquote. Oldest first."""
    seq = task.get("seq", task["id"][:8])
    out = ["# Prompts — task %s · %s" % (seq, task.get("title", "")), ""]
    if not prompts:
        out.append("_No prompts captured for this task yet._")
        return "\n".join(out)
    if include_compact:
        nsess = len({p.get("session_id") for p in prompts})
        out.append("_%d prompt%s · %d session%s · oldest first · times local._"
                   % (len(prompts), "" if len(prompts) == 1 else "s",
                      nsess, "" if nsess == 1 else "s"))
        out.append("")
        for p in prompts:
            kind = p.get("kind") or "prompt"
            suffix = {"command": " · command",
                      "compact": " · compaction summary"}.get(kind, "")
            out.append("**%s** · `%s`%s"
                       % (_prompt_ts(p.get("ts")), _prompt_session_tag(p), suffix))
            out.append("")
            text = p.get("text") or ""
            if kind == "command":
                out.append("`%s`" % " ".join(text.split()))
            else:
                for line in (text.splitlines() or [""]):
                    out.append("> %s" % line)
            out.append("")
        return "\n".join(out).rstrip() + "\n"
    rows = _human_prompts_with_replies(prompts)
    if not rows:
        out.append("_No human-typed prompts captured yet — use `--all` for the raw trail._")
        return "\n".join(out)
    nsess = len({p.get("session_id") for p in rows})
    out.append("_%d prompt%s · %d session%s · human prompts + Claude's reply · oldest first._"
               % (len(rows), "" if len(rows) == 1 else "s",
                  nsess, "" if nsess == 1 else "s"))
    out.append("")
    for p in rows:
        out.append("**%s** · `%s`" % (_prompt_ts(p.get("ts")), _prompt_session_tag(p)))
        out.append("")
        for line in ((p.get("text") or "").splitlines() or [""]):
            out.append("> %s" % line)
        reply = " ".join((p.get("reply") or "").split())
        if reply:
            out.append("")
            out.append("↳ %s" % reply)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _format_prompts_view(task):
    """The read-only `/todo <n> prompts` terminal view — the default (ascii, no
    compaction rows) prompt trail. Mirrors `/todo <n> history`: it renders, never
    attaches/reopens/mutates."""
    prompts = _usage_engine().task_prompts(_backend(), task)
    return _format_prompts(task, prompts)


def _tildify(path):
    """Collapse the user's home to `~` for a compact cwd column ('—' when empty)."""
    if not path:
        return "—"
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _format_session_row(r):
    """One live-session table line:
    `● <pid> · task <seq> · <role> · <status> · <age> · <cwd~> · <resume>`.
    The leading ● marks it as a real running process; missing joins collapse to '—'."""
    seq = r.get("task_seq")
    task_col = ("task %s" % seq) if seq is not None else "task —"
    return "● %-8s · %s · %-6s · %-4s · %-8s · %s · %s" % (
        r.get("pid", "?"),
        task_col,
        r.get("role") or "—",
        r.get("status") or "—",
        rel_time(r.get("updated_ts")),
        _tildify(r.get("cwd")),
        r.get("resume_command") or "—",
    )
