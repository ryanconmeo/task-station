# Task categories & terminal colours

Every task carries a `color` — one of the keys below. The colour does two jobs:

1. **List rendering.** `/todo` appends the category's `<emoji> [TAG]` after each
   task title — the emoji dot carries the colour, the bracketed tag names it
   (see `color_tag()` / `legend_line()` in `todo.py`). ANSI tag-tinting was
   tried and dropped: the slash-command output pipe strips escape sequences,
   leaving raw `\033[…m` codes on screen, so the emoji conveys colour instead.
2. **Terminal tinting.** When a session attaches to, creates, or resumes a task,
   the engine tints the terminal to the task's category. Two modes (set via
   `todo setup --tint-profiles`):
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
| yellow | 🟡  | `[YELLOW]`    | reserved (unassigned)             |
| green  | 🟢  | `[FEATURE]`   | feature work                      |
| blue   | 🔵  | `[DEVOPS]`    | devops                            |
| purple | 🟣  | `[SPECIAL]`   | special                           |
| black  | ⚫  | `[GENERAL]`   | general (the default)             |
| pink   | 🩷  | `[DESIGN]`    | design                            |
| white  | ⚪  | `[SKILLS]`    | skills and memories               |
| silver | 🩶  | `[PERSONAL]`  | personal projects                 |
| gold   | 🟨  | `[FIX PR]`    | fixing PR review feedback         |
| brown  | 🟤  | `[MIGRATION]` | data migration                    |

Each task is rendered as `<dot> [TAG]` — e.g. `🔴 [BUG]`.

`black` / general is the fallback for anything that doesn't fit a category.
`yellow` is reserved — leave it unassigned until it gets a meaning.

## Choosing a colour

Pick from the *nature of the work*, not the surface keywords:

- **red** — fixing a defect / broken behaviour (e.g. "balance sheet plant columns bug").
- **orange** — reviewing someone's code / a PR (running a review, leaving review threads).
- **gold** — fixing PR *review feedback* on your own PR (addressing threads, pushing fixes, replying/resolving).
- **green** — feature / product coding.
- **blue** — infra, deploys, DNS, domains, CI, environment setup.
- **pink** — UI/UX, theming, dark mode, layout, visual design.
- **white** — Claude tooling: skills, slash commands, hooks, memory, this todo system.
- **brown** — data migration work.
- **purple** — anything genuinely special / one-off that warrants standing out.
- **black** — general / catch-all when nothing above fits.

## Skill → colour (immediate tinting)

The `skill_colors` array in `config.json` is an ordered list of `["regex", "color"]` entries, prepended to the shipped defaults (which live in `lib/categories.py`). On
every prompt the hook runs `todo.py prompt-color`, which pulls the invoked
command name out of the prompt (`<command-name>/myplugin:review-pr</command-name>`,
or a hand-typed `/foo …`) and returns the colour of the **first** regex that
matches the name — *with* its `plugin:` prefix kept, so any `<plugin>:`-prefixed
skill matches patterns against the full `plugin:name` string. The hook then runs
`zsh -ic '<colour>'` straight away. Current map:

| Pattern (regex, matched on the command name)        | Colour | Example skills                                  |
|-----------------------------------------------------|--------|-------------------------------------------------|
| `fix-pr`                                            | gold   | `my-fix-pr` (fixing PR review feedback)         |
| `review`, `security-review`                         | orange | any review skill — e.g. `review`, `security-review`, `code-review`, or any plugin-namespaced `<plugin>:review-*` |
| tooling: `update-config`, `keybindings`, `permission`, `schedule`, `statusline`, `init`, `claude-api`, `loop`, `deep-research`, `simplify`, `verify` | white  | `update-config`, `keybindings-help`             |

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
`todo.py` imports it defensively (`try: import categories as cats / except: cats = None`),
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
colour-choosing guidance, served via `todo.py guidance`).

**Do not edit `lib/categories.py` directly** — changes are overwritten on
`/plugin update`. Customize via `config.json` instead (path shown by
`todo config --categories edit`):

```
${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/config.json
```

JSON shape (all keys optional — only what you set is stored):

```json
{
  "categories": { "teal": { "dot": "🟦", "tag": "TEAL", "label": "ops", "hex": "#1a3a3a" } },
  "tint_terminal": false,
  "tint_mode": "auto",
  "skill_colors": [ ["regexpattern", "color"] ],
  "workspace_dirs": ["/path/to/repos"]
}
```

- **`categories`** merges over (and can override) the shipped defaults. Only
  entries with all three of `dot`, `tag`, and `label` are accepted; malformed
  entries are silently skipped. The optional `hex` field (e.g. `"#1a3a3a"`) is
  the background colour used in **auto** tint mode — without it, auto mode
  produces no tint for that category. Any invalid JSON in the file leaves the
  shipped defaults entirely intact.
- **`tint_terminal`** toggles tinting globally. Set to `false` if you like the
  `<emoji> [TAG]` decoration but don't want any terminal tinting.
- **`tint_mode`** — `"auto"` (default, zero-setup direct escapes) or `"profile"`
  (named zsh aliases, set by `todo setup --tint-profiles`).
- **`skill_colors`** entries are **prepended** to the shipped list, so your
  patterns win over the defaults. Each entry is `["regex", "color"]`; first
  match wins.

To remove categories entirely, delete `lib/categories.py` from the installed
plugin directory. `todo.py` will run as a plain, colourless tracker.

`todo.py` never names a colour — it only calls into `categories.py`.

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
