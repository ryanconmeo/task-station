# Task categories & terminal colours

> **Status glyph vs category emoji.** Each not-closed `/todo` row leads with a
> single-width **status glyph** — `◦` **open** (a topic merely raised) or `●`
> **active** (work has started) — *before* the task number; closed tasks sit in
> their own section with no glyph. This is distinct from the category **emoji**
> documented here, which stays in its own `<emoji> [TAG]` column after the title.
> Status is one field tracking the task's lifecycle — open (◦) → active (●) →
> closed; the emoji/colour names its category. See the `status` field, `status
> --task <ref> [open|active]`, and `create --active` in the README.

Every task carries a `color` — one of the keys below. The colour does two jobs:

1. **List rendering.** `/todo` appends the category's `<emoji> [TAG]` after each
   task title — the emoji dot carries the colour, the bracketed tag names it
   (see `color_tag()` / `legend_line()` in `task-station.py`). ANSI tag-tinting was
   tried and dropped: the slash-command output pipe strips escape sequences,
   leaving raw `\033[…m` codes on screen, so the emoji conveys colour instead.
2. **Terminal tinting.** When a session attaches to, creates, or resumes a task,
   the engine tints the terminal to the task's category. Two modes (set via
   `task-station config --tint-profiles`):
   - **auto** *(default, zero-setup)* — writes a direct escape to set the background
     colour (`hex` field per category): iTerm2 uses `SetColors`, Terminal.app uses
     OSC 11. No profiles or aliases required.
   - **profile** — runs `zsh -ic '<color>'` via named zsh aliases (the author's
     original setup, for `<Color> Sands` Terminal.app profiles).
   The **resume one-liner** that `/todo <n>` prints carries the tint too: it's
   prefixed with the bare alias so the terminal re-colours when you paste it into a
   fresh window — e.g. `green 2>/dev/null; cd <dir> && claude --resume <id>`. The
   prefix is joined with `;` (not `&&`) and redirects stderr to `/dev/null`, so for
   anyone without profile aliases it's a silent no-op and the `cd` + resume still
   runs. The prefix is dropped entirely when tinting is off — `"tint_terminal": false`
   in `config.json`.
3. **Immediate skill tinting.** When a prompt *invokes a skill* (a slash
   command like `/review` or `/security-review`), the `UserPromptSubmit` hook tints the
   terminal to the skill's category **synchronously, before Claude responds** —
   so running a review skill turns the terminal orange instantly, with no wait
   for Claude to pick a colour. The skill→colour map lives in `categories.py`
   (`SKILL_COLORS`); see "Skill → colour" below.

## The taxonomy

| Color  | Dot | Tag          | Category                          |
|--------|-----|--------------|-----------------------------------|
| red    | 🔴  | `[BUG]`       | bug                               |
| orange | 🟠  | `[REVIEW]`    | code review                       |
| yellow | 🟡  | `[FIX]`       | fixing PR review feedback         |
| green  | 🟢  | `[FEATURE]`   | feature work                      |
| blue   | 🔵  | `[DEVOPS]`    | devops                            |
| purple | 🟣  | `[SPECIAL]`   | special                           |
| black  | ⚫  | `[GENERAL]`   | general (the default, permanent)  |
| pink   | 🩷  | `[PERSONAL]`  | personal projects                 |
| white  | 🎨  | `[DESIGN]`    | design                            |
| silver | 🪩  | `[AI CONFIG]` | AI tooling & config               |
| gold   | 🟨  | `[GOLD]`      | reserved (unassigned)             |
| brown  | 🟤  | `[DATABASE]` | database                          |

Each task is rendered as `<dot> [TAG]` — e.g. `🔴 [BUG]`.

`black` / general is the fallback for anything that doesn't fit a category, and is
**permanent** — it is always enabled and cannot be disabled (see *Enabled set &
presets* below). `gold` is reserved — leave it unassigned until it gets a meaning.

### The dot is slot-canonical — "you pick the colour; the colour determines the icon"

Each colour slot **owns** an emoji. When you override a category or add a new one,
you supply only `tag` + `label` + the colour slot — the **dot is taken from the
slot's canonical emoji automatically**, as are the tint `hex`/`hex_light`. An
explicit `dot` is still allowed (power users can override it), but it's optional:
absent ⇒ the slot's emoji. So an override of `{"green": {"tag": "VOLT", "label":
"volt work"}}` keeps 🟢 and green's tint while relabelling the slot.

## Choosing a colour

Pick from the *nature of the work*, not the surface keywords:

- **red** — fixing a defect / broken behaviour (e.g. "balance sheet plant columns bug").
- **orange** — reviewing someone's code / a PR (running a review, leaving review threads).
- **yellow** — fixing PR *review feedback* on your own PR (addressing threads, pushing fixes, replying/resolving).
- **green** — feature / product coding.
- **blue** — infra, deploys, DNS, domains, CI, environment setup.
- **white** — UI/UX, theming, dark mode, layout, visual design (🎨).
- **pink** — personal projects / side work (🩷).
- **silver** — AI tooling & config: skills, slash commands, hooks, memory, this task-station system (🪩).
- **brown** — database work: schema, queries, SQL, DB tuning, **and data migrations** (moving/transforming data between systems counts as database work).
- **purple** — anything genuinely special / one-off that warrants standing out.
- **black** — general / catch-all when nothing above fits.

## Enabled set & presets

Not every taxonomy fits every workflow, so the active set of categories is
**seeded-but-removable**, stored in `config.json` as `enabled_categories` (a list
of colour keys). The legend, the auto-classification nudge, and the colour picker
all consider **only enabled categories**. When `enabled_categories` is unset, the
**full set** of all 12 shows (back-compat) — nothing changes until you opt into a
preset or toggle slots.

**⚫ GENERAL is permanent**: it is always enabled and cannot be disabled.

### Presets

`task-station config --categories preset <name>` sets `enabled_categories` to a
named preset. The **universal core** — `red BUG`, `silver AI CONFIG`,
`pink PERSONAL`, `black GENERAL` — is seeded in **every** preset (removable except
GENERAL):

| preset    | enabled slots                                                   |
|-----------|-----------------------------------------------------------------|
| `minimal` | core only (BUG · AI CONFIG · PERSONAL · GENERAL)                 |
| `web`     | core + FEATURE, DESIGN, DEVOPS, REVIEW, FIX                      |
| `data`    | core + DATABASE, FEATURE, DEVOPS, REVIEW                         |
| `ops`     | core + DEVOPS, DATABASE, REVIEW, FIX, SPECIAL                    |
| `full`    | all 12 (the default)                                            |

- `task-station config --categories` (no arg) — show the current enabled set +
  available presets.
- `task-station config --enable <key>` / `--disable <key>` — toggle a single slot
  (accepts a key, emoji, or `[TAG]`). Disabling `black`/`GENERAL` is refused.
- Editing the raw `categories` override map in `config.json` still works.

## Skill → colour (immediate tinting)

The `skill_colors` array in `config.json` is an ordered list of `["regex", "color"]` entries, prepended to the shipped defaults (which live in `lib/categories.py`). On
every prompt the hook runs `task-station.py prompt-color`, which pulls the invoked
command name out of the prompt (`<command-name>/myplugin:review-pr</command-name>`,
or a hand-typed `/foo …`) and returns the colour of the **first** regex that
matches the name — *with* its `plugin:` prefix kept, so any `<plugin>:`-prefixed
skill matches patterns against the full `plugin:name` string. The hook then runs
`zsh -ic '<colour>'` straight away. Current map:

| Pattern (regex, matched on the command name)        | Colour | Example skills                                  |
|-----------------------------------------------------|--------|-------------------------------------------------|
| `fix-pr`                                            | yellow | `my-fix-pr` (fixing PR review feedback)         |
| `review`, `security-review`                         | orange | any review skill — e.g. `review`, `security-review`, `code-review`, or any plugin-namespaced `<plugin>:review-*` |
| tooling: `update-config`, `keybindings`, `permission`, `schedule`, `statusline`, `init`, `claude-api`, `loop`, `deep-research`, `simplify`, `verify` | silver | `update-config`, `keybindings-help`             |

Order matters: more specific patterns should be listed before broader ones so the
right rule wins. A `<plugin>:review-*` skill matches `review` and stays **orange**
even if a more-specific plugin prefix rule is added to the user override — put it
**above** the `review` entry in your `skill_colors` override to intercept it first.

A prompt that invokes no skill, or a skill no pattern matches, tints nothing —
Claude falls back to the normal pick-a-colour guidance. When a skill *does*
match, the `prompt-context` guidance tells Claude the terminal is already tinted
and to reuse that colour when it creates/attaches the task (no re-tint). To
change the mapping, add a `skill_colors` list to your `config.json` — entries
there are prepended and take priority over the shipped list.

## How it's wired — `categories.py` is an optional plugin

All category/colour logic lives in **`lib/categories.py`**, not in the core.
`task-station.py` imports it defensively (`try: import categories as cats / except: cats = None`),
so the tracker degrades gracefully for anyone who doesn't want your colours:

- **No `categories.py`** (deleted from the plugin's `lib/` dir) → a plain,
  colourless tracker: no `[TAG]` column, no legend, `--color` is accepted but
  ignored, no tint hints. Nothing depends on your aliases.
- **`categories.py` present, `tint_terminal: false`** (via your `config.json`
  override) → tasks still get the `<emoji> [TAG]` decoration and labels, but no
  terminal tinting (for people who want the tags but not the colour changes).
- **`categories.py` present, `tint_terminal: true`** (the author's setup, and the
  default) → full behaviour: tags, legend, and tint suggestions on
  create/attach/resume. **macOS only** — tinting is a no-op on other platforms
  regardless of the setting.

`lib/categories.py` exposes: `CATEGORIES` + `DEFAULT` (the taxonomy), `normalize`,
`label`, `tag`, `summary`, `legend`, `compact_legend` (one-line `key=dotTAG`
form used by the token-lean per-prompt nudge), `tint_command` (returns `None`
when tinting is off or the platform isn't macOS), and `picker_lines` (the
colour-choosing guidance, served via `task-station.py guidance`).

**Do not edit `lib/categories.py` directly** — changes are overwritten on
`/plugin update`. Customize via `config.json` instead (path shown by
`task-station config --categories edit`):

```
${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/config.json
```

JSON shape (all keys optional — only what you set is stored):

```json
{
  "categories": { "green": { "tag": "VOLT", "label": "volt work" } },
  "enabled_categories": ["red", "white", "pink", "black", "green"],
  "tint_terminal": false,
  "tint_mode": "auto",
  "skill_colors": [ ["regexpattern", "color"] ],
  "workspace_dirs": ["/path/to/repos"]
}
```

- **`categories`** merges over (and can override) the shipped defaults. Each entry
  needs only `tag` + `label` — the `dot` and tint `hex`/`hex_light` are inherited
  from the colour slot (see *slot-canonical* above); supply an explicit `dot`/`hex`
  to override them. Entries missing `tag` or `label` are silently skipped. Any
  invalid JSON in the file leaves the shipped defaults entirely intact.
- **`enabled_categories`** is the list of "on" colour keys (see *Enabled set &
  presets*). Absent ⇒ the full set. `⚫ GENERAL` is always forced in. Usually set
  via `config --categories preset <name>` / `--enable` / `--disable` rather than by
  hand.
- **`tint_terminal`** toggles tinting globally. Set to `false` if you like the
  `<emoji> [TAG]` decoration but don't want any terminal tinting.
- **`tint_mode`** — `"auto"` (default, zero-setup direct escapes) or `"profile"`
  (named zsh aliases, set by `task-station config --tint-profiles`).
- **`skill_colors`** entries are **prepended** to the shipped list, so your
  patterns win over the defaults. Each entry is `["regex", "color"]`; first
  match wins.

To remove categories entirely, delete `lib/categories.py` from the installed
plugin directory. `task-station.py` will run as a plain, colourless tracker.

`task-station.py` never names a colour — it only calls into `categories.py`.

- `create` takes `--color`; `attach` takes an optional `--color` to set or
  backfill one. When categories are on they print the category and (if tinting
  is on) the exact `zsh -ic '<color>'` line to run.
- The `UserPromptSubmit` and `SessionStart` hooks surface the colour so a new or
  resumed session knows which alias to run — but only when categories are on.

## The aliases (in `~/.zshrc`)

Each is:

```sh
alias <color>='osascript -e "tell app \"Terminal\" to set current settings of front window to settings set \"<Color> Sands\""'
```

so the Terminal.app profile named `<Color> Sands` must exist for the tint to
take effect. Running `zsh -ic '<color>'` evaluates the alias in an interactive
shell (where aliases are loaded), tinting the front Terminal window.
