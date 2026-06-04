# Task categories & terminal colours

Every task carries a `color` — one of the keys below. The colour does two jobs:

1. **List rendering.** `/todo` appends the category's `<emoji> [TAG]` after each
   task title — the emoji dot carries the colour, the bracketed tag names it
   (see `color_tag()` / `legend_line()` in `todo.py`). ANSI tag-tinting was
   tried and dropped: the slash-command output pipe strips escape sequences,
   leaving raw `\033[…m` codes on screen, so the emoji conveys colour instead.
2. **Terminal tinting.** Each colour name is *also* the name of a zsh alias in
   `~/.zshrc` that switches the Terminal.app profile (`<Color> Sands`). When a
   session attaches to, creates, or resumes a task, Claude runs that alias —
   `zsh -ic '<color>'` — so the whole terminal is tinted to the task's category.
3. **Immediate skill tinting.** When a prompt *invokes a skill* (a slash
   command like `/volt:review-pr-auto`), the `UserPromptSubmit` hook tints the
   terminal to the skill's category **synchronously, before Claude responds** —
   so running a review skill turns the terminal orange instantly, with no wait
   for Claude to pick a colour. The skill→colour map lives in `categories.py`
   (`SKILL_COLORS`); see "Skill → colour" below.

## The taxonomy

| Color  | Dot | Tag          | Category                          |
|--------|-----|--------------|-----------------------------------|
| red    | 🔴  | `[BUG]`       | bug                               |
| orange | 🟠  | `[REVIEW]`    | code review                       |
| yellow | 🟡  | `[PERSONAL]`  | personal projects                 |
| green  | 🟢  | `[VOLT]`      | coding for Volt                   |
| blue   | 🔵  | `[DEVOPS]`    | devops                            |
| purple | 🟣  | `[SPECIAL]`   | special                           |
| black  | ⚫  | `[GENERAL]`   | general (the default)             |
| pink   | 🩷  | `[DESIGN]`    | design                            |
| white  | ⚪  | `[SKILLS]`    | skills and memories               |
| silver | 🩶  | `[SILVER]`    | reserved (unassigned)             |
| gold   | 🟨  | `[FIX PR]`    | fixing PR review feedback         |
| brown  | 🟤  | `[MIGRATION]` | ConnX & legacy migration for Volt |

Each task is rendered as `<dot> [TAG]` — e.g. `🔴 [BUG]`.

`black` / general is the fallback for anything that doesn't fit a category.
`silver` is reserved — leave it unassigned until it gets a meaning.

## Choosing a colour

Pick from the *nature of the work*, not the surface keywords:

- **red** — fixing a defect / broken behaviour (e.g. "balance sheet plant columns bug").
- **orange** — reviewing someone's code / a PR (running a review, leaving review threads).
- **gold** — fixing PR *review feedback* on your own PR (addressing threads, pushing fixes, replying/resolving).
- **green** — building Volt product features / coding in the Volt app.
- **blue** — infra, deploys, DNS, domains, CI, environment setup.
- **pink** — UI/UX, theming, dark mode, layout, visual design.
- **white** — Claude tooling: skills, slash commands, hooks, memory, this todo system.
- **brown** — ConnX / ConnxLandingZone building and legacy (VAX) data migration into Volt.
- **purple** — anything genuinely special / one-off that warrants standing out.
- **black** — general / catch-all when nothing above fits.

## Skill → colour (immediate tinting)

`SKILL_COLORS` in `categories.py` is an ordered list of `(regex, colour)`. On
every prompt the hook runs `todo.py prompt-color`, which pulls the invoked
command name out of the prompt (`<command-name>/volt:review-pr-auto</command-name>`,
or a hand-typed `/foo …`) and returns the colour of the **first** regex that
matches the name — *with* its `plugin:` prefix kept, so `volt:` and
`connxlandingzone:` skills match the same patterns. The hook then runs
`zsh -ic '<colour>'` straight away. Current map:

| Pattern (regex, matched on the command name)        | Colour | Example skills                                  |
|-----------------------------------------------------|--------|-------------------------------------------------|
| `fix-pr`                                            | gold   | `my-fix-pr` (fixing PR review feedback)         |
| `review`, `security-review`                         | orange | any review — incl. `connxlandingzone:review-*`, `volt:review-*`, `code-review` |
| `^connxlandingzone:` (after the review rule)        | brown  | non-review ConnX building, e.g. a future `connxlandingzone:build-*` / `:deploy` |
| `story-runner`                                      | green  | `volt:story-runner`, `story-runner-auto`        |
| tooling: `update-config`, `keybindings`, `permission`, `schedule`, `statusline`, `init`, `claude-api`, `loop`, `deep-research`, `simplify`, `verify` | white  | `update-config`, `keybindings-help`             |

Order matters: the `review` rule sits **above** the ConnX rule, so a
`connxlandingzone:review-*` skill matches `review` first and stays **orange** —
only non-review ConnX skills fall through to **brown**.

A prompt that invokes no skill, or a skill no pattern matches, tints nothing —
Claude falls back to the normal pick-a-colour guidance. When a skill *does*
match, the `prompt-context` guidance tells Claude the terminal is already tinted
and to reuse that colour when it creates/attaches the task (no re-tint). To
change the mapping, edit only `SKILL_COLORS`.

## How it's wired — `categories.py` is an optional plugin

All category/colour logic lives in **`categories.py`**, not in the core. `todo.py`
imports it defensively (`try: import categories as cats / except: cats = None`),
so the tracker degrades gracefully for anyone who doesn't want your colours:

- **No `categories.py`** (deleted or never installed) → a plain, colourless
  tracker: no `[TAG]` column, no legend, `--color` is accepted but ignored, no
  tint hints. Nothing depends on your aliases.
- **`categories.py` present, `TINT_TERMINAL = False`** → tasks still get the
  `<emoji> [TAG]` decoration and labels, but no `zsh -ic '<color>'` suggestions
  (for people without the `*Sands*` Terminal profiles / aliases).
- **`categories.py` present, `TINT_TERMINAL = True`** (the author's setup) →
  full behaviour: tags, legend, and tint suggestions on create/attach/resume.

`categories.py` exposes: `CATEGORIES` + `DEFAULT` (the taxonomy), `normalize`,
`label`, `tag`, `summary`, `legend`, `tint_command` (returns `None` when tinting
is off), and `picker_lines` (the colour-choosing guidance for the hook). To
change the taxonomy, edit only that file — `todo.py` never names a colour.

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
