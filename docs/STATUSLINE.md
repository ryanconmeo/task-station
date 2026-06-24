# Claude Code status-line composition convention

> A small, vendor-neutral convention for composing **multiple** status-line
> segments under Claude Code's single `statusLine.command`. This document
> describes a proposed convention, not a feature of any one plugin.
> [task-station](https://github.com/ryanconmeo/task-station) is the reference
> implementation (`task-station config --statusline on`); the convention is
> intended for extraction into its own neutral repository and an upstream feature
> request for native segment composition.

## The problem: one command, many would-be authors

Claude Code exposes exactly **one** status-line hook: `statusLine.command` in
`settings.json`. Whatever you point it at owns the entire bar. That is fine for a
single status line, but it does not compose: a cost tracker, a task tracker, a
git-branch widget, and a model indicator cannot all be `statusLine.command` at
once. The last installer to write the key silently wins, and the others are
either lost or have to detect-and-wrap each other ad hoc — N installers needing
O(N²) awareness of one another.

Composition needs a convention so that any number of independent segment authors
can coexist with **zero coupling**: no shared library, no central registry, no
installer needing to know any other installer exists.

This convention defines two roles and one data contract:

- a **provider** emits one segment and knows nothing about the bar;
- a **host** owns `statusLine.command`, runs every provider, and joins the
  results.

A host needs only the contract below — not a dependency on any particular
provider or conductor. The compose routine is ~30 lines and is **embedded** by
each host, so there is no shared "conductor" package to depend on.

## Provider contract

A **provider** is an executable segment generator.

- **Location.** A provider is an executable file in
  `${CLAUDE_CONFIG_DIR:-~/.claude}/statusline.d/`. Hosts run the providers in
  **lexical filename order**, so the conventional naming is `NN-name`
  (e.g. `20-cost.sh`, `50-task-station.sh`, `80-git.sh`) where the numeric prefix
  orders the segment within the bar. A file must be executable (`chmod +x`) to be
  run; non-executable files are ignored.
- **Input.** The provider receives, on **stdin**, the same status-line JSON object
  that Claude Code pipes to `statusLine.command`. Providers should read all of
  stdin and parse the fields they need, tolerating absent fields. Commonly
  present fields include:
  - `session_id` — the current session's id
  - `cwd` — the session's working directory
  - `model` — model id / display name
  - `cost` — accumulated cost / usage for the session
  - `context_window` — context-window usage
  - `rate_limits` — rate-limit state
  (The exact shape is whatever Claude Code provides; treat every field as
  optional and version-tolerant.)
- **Width hint (optional).** Hosts set the environment variable
  `CLAUDE_STATUSLINE_WIDTH` to the number of **visible columns** available, for
  providers that want to truncate their output to fit. `0` (or unset) means "no
  limit / unknown". Honoring it is optional but recommended for long segments.
- **Output.** A provider prints **one line** to stdout — its segment, ANSI color
  allowed. **Empty output or a non-zero exit code means "no segment this time"**
  and the host skips it. A provider must never assume it is the only segment and
  must never depend on a controlling terminal.

Because the provider's input is exactly the `statusLine.command` input, **a
provider is itself a valid `statusLine.command`** — you can point Claude Code
straight at one provider and it works standalone, with no host.

## Host contract

A **host** owns `statusLine.command` and composes providers.

A conformant host:

1. Reads the status-line JSON from its own stdin.
2. Resolves the visible width (terminal columns if available, else `0`) and
   exports it as `CLAUDE_STATUSLINE_WIDTH`.
3. Runs every executable in `${CLAUDE_CONFIG_DIR:-~/.claude}/statusline.d/` in
   lexical order, piping the **same JSON** to each on stdin.
4. Collects each provider's non-empty stdout; **errors are isolated** — a provider
   that exits non-zero, prints nothing, or crashes is simply skipped and never
   breaks the bar.
5. Joins the collected segments with a **separator** and prints the single
   resulting line. The default separator is `"  │  "` (two spaces, a box-drawing
   vertical bar, two spaces); it is overridable via the `CLAUDE_STATUSLINE_SEP`
   environment variable.
6. Writes a **host marker** comment into its own `statusLine.command` so other
   installers can detect that a conformant host already owns the bar:

   ```
   # claude-statusline-host:<name>
   ```

   where `<name>` identifies the host (e.g. `# claude-statusline-host:task-station`).

The host compose routine is intentionally tiny (~30 lines) and is **embedded** in
each host implementation — there is no shared conductor binary to install or keep
in sync.

## Non-destructive install rule (for any host installer)

A host installer writes to the user's `settings.json`, so it must be
**non-destructive and reversible**. Inspect the existing
`settings.json` → `statusLine.command` and branch:

- **Unset / empty** → install yourself as host: set `statusLine.command` to your
  embedded compose routine, including your `# claude-statusline-host:<name>`
  marker. (Always also register your own provider in `statusline.d/`.)
- **Bears a host marker** (`# claude-statusline-host:…`) → a conformant host
  already owns the bar. **Do not take over.** Just ensure your provider is present
  in `statusline.d/`; it will be composed automatically.
- **An unknown command** (no marker — a foreign/hand-written status line) → **do
  not clobber it.** Register your provider in `statusline.d/`, leave
  `statusLine.command` untouched, and tell the user how to add `statusline.d/`
  composition (point their bar at a conformant host, or have it source the
  providers itself).

Removal must be equally careful: an installer removes only its **own** provider
drop-in and only clears `statusLine.command` when it bears **its own** marker —
never a foreign or unmarked command.

## Reference implementation

[task-station](https://github.com/ryanconmeo/task-station) implements both roles:

- **Provider** — it always registers
  `${CLAUDE_CONFIG_DIR:-~/.claude}/statusline.d/50-task-station.sh`, which reads
  the JSON, pulls `session_id`, and emits the current task segment honoring
  `CLAUDE_STATUSLINE_WIDTH`.
- **Self-sufficient host** — `task-station config --statusline on` installs an
  opt-in status bar (default off, reversible) that embeds the compose routine
  above. It needs no external conductor: it composes its own provider plus any
  other `statusline.d/` providers present. It follows the non-destructive install
  rule exactly — it never clobbers an existing or foreign `statusLine.command`.

This convention is reference-implemented here but is **not** task-station-specific;
it is intended for extraction into a neutral repository and filed upstream as a
feature request for native plugin status-line segment composition.
