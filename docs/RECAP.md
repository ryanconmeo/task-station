# Weekly recap — your private LLM-usage digest

`task-station recap` renders a **strictly private, local** one-pager summarizing a
week of your LLM-assisted work: what you did, what it cost, and concrete guidance to
use LLMs more effectively. Think Spotify Wrapped, but weekly and useful.

```bash
task-station recap                 # the current ISO week, so far
task-station recap --week 2026-W29 # a specific ISO week
task-station recap --open          # also open it in your browser (macOS)
task-station recap --json          # print the privacy-safe aggregate stats instead
```

Output lands at `<data_dir>/recaps/<YYYY-Www>.html` — a self-contained HTML file
(no external assets, `file://`-openable, light + dark via `prefers-color-scheme`
with a theme toggle).

## What it reads (all local, read-only)

- **`session_usage` + `prompts` tables** (the usage ledger, `lib/usage.py`) — per
  session/model token counts, derived `$`, work-phase mix, and per-prompt metadata.
- **The task store** — task titles, `#seq` handles, categories, status/close times,
  memos + acks, and delegation roles (hub vs worker).
- **The Tasktrail event stream** (`<data_dir>/stream/`) — event cadence.
- **HUD snapshots** (`<data_dir>/hud/`) — rate-limit touches, best-effort; skipped
  gracefully when absent.

It reads only what is already persisted — it runs no transcript scan of its own
(the CLI does an incremental `usage scan-all` first, purely to freshen the ledger;
`--no-scan` skips even that).

## What it NEVER reads into output (privacy hard rule)

- **No raw prompt text.** Prompts are used only for counts and slash-command feature
  detection; not one character of prompt content reaches the HTML or the aggregate
  stats.
- **No task summaries** — only titles and `#seq` handles.
- **No path outside the data dir.**

The optional curator (below) receives the **same** privacy floor: a deliberate
allowlist of counts / ratios / titles, never text.

## Sections

1. **Headline** — output tokens, API-list-price value, active hours, tasks
   touched/closed, busiest day, sessions.
2. **What it cost — in equivalents** — cost as *equivalence, not spend* (below), plus a
   DIRECTIONAL energy/CO₂/water estimate with a cited assumptions table.
3. **Model mix** — share by family (opus/fable/sonnet/haiku) with per-model value and a
   fit note.
4. **Where it went** — top tasks by tokens (`#handle` + title) and a category
   breakdown.
5. **Work patterns** — typical session length, peak hour, longest focus streak,
   delegation-vs-hub split, memo/ack turnaround.
6. **Getting more from any LLM** — the guidance, **strategy first**: universal best
   practice you can apply in any assistant (be specific; build context once; retrieve
   before you re-derive; match the model to the job; small verifiable steps; mind the
   context window; make prompts repeatable). Tooling is cited only where it serves a
   habit — and **never a model-invoked feature as a "run this"** (see below).
7. **Match the model to the work** — the versioned model-role matrix, rendered against
   your observed week (observed tier vs recommended, with the delta) plus the rest as
   reference.
8. **This week's flags** — the concrete, signal-driven tips (model-fit from the matrix,
   no-checkpoint sessions, re-explaining after compaction, cost outliers, an unused
   *human* feature, no delegation). Each is *observation → suggestion → exact next
   action*. Then, if a curator is configured, up to 3 tailored tips.
9. **What would sharpen next week** — honest data gaps, framed as proposed additive
   usage-ledger columns (settings snapshot, compaction counts, per-session file-edit
   frequency for retry-loop detection, permission-prompt counts).

Sections with no data are skipped — never an empty shell. An entirely quiet week
renders a short "nothing to review" page (no guidance shown).

## Cost is equivalence, not spend

A flat-rate/team seat does not pay per token, so the recap never claims a dollar
*spend*. It shows what the week's work **would cost at API list prices** ("worth
~$X"), computed from the same rate tables the usage ledger uses. Numbers are
approximate (`~`), and the environmental figures are **DIRECTIONAL ranges**, never
point values.

The eco assumptions live in `lib/recap_guidance.py` (`ECO_*`, version-stamped) and are
cited inline in the recap:

| factor | value (range) | note |
| --- | --- | --- |
| energy / token | 0.02–1.2 kWh per 1M tokens (by model class) | order-of-magnitude; no vendor publishes per-token energy |
| grid intensity | 0.35–0.45 kg CO₂e / kWh | global-average electricity mix |
| water | 0.2–3.9 L / kWh | data-center cooling + the electricity's own water footprint |

## The model-role matrix (versioned)

`lib/recap_guidance.py` holds `MODEL_ROLE_MATRIX` + `MATRIX_VERSION` + a generation
note. Tiers name capability **classes** (cheapest-capable / mid / strongest), not fixed
model ids — when a new generation ships, remap the classes in that one file and the
guidance follows. The recap renders the rows relevant to your observed week with an
observed-vs-recommended delta (e.g. "you ran the strongest tier on mechanical edits —
over-powered").

## Feature-invocation registry (who runs what)

`INVOKED_BY` in `lib/recap_guidance.py` records, per task-station feature, whether it is
invoked by a `human`, `both`, the `model`, or `human-audit`. Guidance generation filters
on it: **a model-invoked feature is never recommended to a human as a "run this".**
`search`, for example, is something the assistant does — the strategy practice says
"ask your assistant to search", never "run `task-station search`". This closes the
W29 bug class where a model-only command was suggested to a person.

## Auto-weekly (opt-in, default OFF)

```bash
task-station config --recap on     # generate last week's recap once per week
task-station config --recap off
```

When on, the `Stop` hook generates the **previous complete week's** recap at most
once per week, throttled by a stamp file (`<data_dir>/recaps/.last-auto`). It is
fail-open (never disrupts a turn) and costs **zero tokens** unless a curator is
configured. The `task-station recap` command works regardless of this toggle.

## Curator (optional, default OFF)

```bash
task-station config --recap-curator-cmd '<command>'   # set
task-station config --recap-curator-cmd                # clear (back to OFF)
```

The command receives the privacy-safe aggregate stats as JSON on **stdin** and must
print a JSON list of up to 3 `{observation, suggestion, command}` objects on stdout.
Any failure, timeout, or malformed output is swallowed — the recap still renders with
the deterministic tips. Because the aggregates carry no prompt text, a curator never
sees anything private.

## Cleanup

Recaps are disposable build outputs. To remove them all:

```bash
rm -rf "<data_dir>/recaps"
```

That is the entire footprint — recaps are added to no sync boundary, no export, and
no manifest.
