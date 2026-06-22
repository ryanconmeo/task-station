# Task categories & terminal colours

> **Status glyph vs category emoji.** Each not-closed `/todo` row leads with a
> single-width **status glyph** — `○` **open** (a topic merely raised) or `●`
> **active** (work has started) — *before* the task number; closed tasks sit in
> their own section with no glyph. This is distinct from the category **emoji**
> documented here, which stays in its own `<emoji> [TAG]` column after the title.
> Status is one field tracking the task's lifecycle — open (○) → active (●) →
> closed; the emoji/colour names its category. See the `status` field, `status
> --task <ref> [open|active]`, and `create --active` in the README.

Every task carries a `color` — one of the keys below. The colour does two jobs:

1. **List rendering.** `/todo` appends the category's `<emoji> [TAG]` after each
   task title — the emoji dot carries the colour, the bracketed tag names it. (ANSI
   tag-tinting was tried and dropped: the slash-command output pipe strips escape
   sequences, so the emoji conveys colour instead.)
2. **Full-palette terminal tinting (zero-setup).** When a session attaches to,
   creates, or resumes a task — and the instant a known skill runs — the engine
   tints the **whole terminal** to the category's baked palette by writing standard
   OSC escape sequences. **No Terminal.app profiles and no shell aliases are
   required**; iTerm2 and Apple Terminal both honour it out of the box.

   > Profile-switching (`zsh -ic '<Color> Sands'` aliases) was **removed in 1.7.0**.
   > Tinting is now always the direct escape below.

## The full-palette escape

Each category ships a complete **Sands** palette — `hex` (background), `fg`, `bold`,
`cursor`, `selbg` (selection), and a 16-element `ansi` list. `categories.tint_escape`
concatenates these into one escape string and the hooks write it to the *originating*
window:

| Element | Escape | iTerm2 | Apple Terminal |
|---|---|:--:|:--:|
| background | `ESC ] 11 ; <hex> BEL` | ✓ | ✓ |
| foreground | `ESC ] 10 ; <hex> BEL` | ✓ | ✓ |
| cursor | `ESC ] 12 ; <hex> BEL` | ✓ | ✓ |
| ANSI 0–15 | `ESC ] 4 ; <n> ; <hex> BEL` | ✓ | ✓ |
| selection | `ESC ] 17 ; <hex> BEL` | ✓ | ✓ |
| bold colour | `ESC ] 1337 ; SetColors=bold=<hexNoHash> BEL` | ✓ | — (iTerm-only) |

A slot that defines only a background still emits just the background (back-compat for
minimal taxonomies); an unknown colour or an unsupported terminal emits nothing.

**Targeting the right window.** The hooks resolve the *originating* TTY with
`lib/origin-tty.sh` and write the escape there (not to stdout), so tinting is
focus-proof. Resolution order: **`$CLAUDE_TTY`** (export it in your shell rc — the most
reliable) → on iTerm, the session UUID in `$TERM_SESSION_ID` mapped to its `tty`. The
**resume one-liner** that `/todo <n>` prints is now a clean `cd <dir> && claude --resume
<id>` with no tint prefix — the resumed/attached window tints itself via the
`SessionStart` hook. Turn tinting off with `"tint_terminal": false` in `config.json` (or
`TASK_STATION_TINT=off`). Tinting is a no-op on platforms / terminals it doesn't support.

## The taxonomy

| Color  | Dot | Tag          | Category                          |
|--------|-----|--------------|-----------------------------------|
| red    | 🔴  | `[BUG]`       | bug                               |
| orange | 🟠  | `[REVIEW]`    | code review                       |
| yellow | 🟡  | `[FIX]`       | fixing PR review feedback         |
| green  | 🟢  | `[FEATURE]`   | feature work                      |
| blue   | 🔵  | `[INFRA]`     | CI/CD, pipelines, cloud, deploy   |
| purple | 🟣  | `[RESEARCH]`  | spikes / investigation            |
| black  | ⚫  | `[GENERAL]`   | general (the default, permanent)  |
| pink   | 🩷  | `[PERSONAL]`  | personal projects                 |
| white  | 🎨  | `[DESIGN]`    | design                            |
| silver | 🪩  | `[TOOLING]`   | dev/AI tooling, config, env       |
| gold   | 📖  | `[DOCS]`      | documentation, writing            |
| brown  | 🟤  | `[DATA]`      | databases, schemas, ETL, migrations |

Each task is rendered as `<dot> [TAG]` — e.g. `🔴 [BUG]`.

`black` / general is the fallback for anything that doesn't fit a category, and is
**permanent** — always enabled, cannot be disabled (see *Enabled set & presets* below).

### The dot is slot-canonical — "you pick the colour; the colour determines the icon"

Each colour slot **owns** an emoji and a palette. When you override a category or add a
new one, you supply only `tag` + `label` + the colour slot — the **dot and the full
palette (`hex`/`hex_light`/`fg`/`bold`/`cursor`/`ansi`) are inherited from the slot
automatically**. An explicit `dot` (or any palette field) is still allowed to override,
but it's optional. So `{"green": {"tag": "VOLT", "label": "volt work"}}` keeps 🟢 and
green's full Sands palette while relabelling the slot.

## Choosing a colour

Pick from the *nature of the work*, not the surface keywords:

- **red** — fixing a defect / broken behaviour (e.g. "balance sheet plant columns bug").
- **orange** — reviewing someone's code / a PR (running a review, leaving review threads).
- **yellow** — fixing PR *review feedback* on your own PR (addressing threads, pushing fixes, replying/resolving).
- **green** — feature / product coding.
- **blue** — CI/CD, pipelines, cloud, deploys, DNS, domains, environment setup (🔵 INFRA).
- **white** — UI/UX, theming, dark mode, layout, visual design (🎨).
- **pink** — personal projects / side work (🩷).
- **silver** — dev/AI tooling, config & env: skills, slash commands, hooks, memory, this task-station system (🪩 TOOLING).
- **brown** — data work: databases, schemas, queries, SQL, ETL, **and data migrations** (moving/transforming data between systems) (🟤 DATA).
- **purple** — spikes / investigation: research, prototypes, one-off exploration (🟣 RESEARCH).
- **gold** — documentation & writing: READMEs, guides, changelogs, docs (📖 DOCS).
- **black** — general / catch-all when nothing above fits.

## Dark / light

The palette appearance is chosen by `tint_theme` (`config --tint-theme auto|dark|light`).
`auto` follows the OS appearance (on macOS via `defaults read -g AppleInterfaceStyle`);
`dark`/`light` force it without detection. A slot may define `hex_light` for a separate
light-mode background — but the **shipped Sands palettes are theme-independent**
(`hex == hex_light`), so the setting mainly matters for your own `hex_light` overrides.
A slot/override that defines only `hex` still tints under either theme.

## Enabled set & presets

Not every taxonomy fits every workflow, so the active set of categories is
**seeded-but-removable**, stored in `config.json` as `enabled_categories` (a list of
colour keys). The legend, the auto-classification nudge, and the colour picker all
consider **only enabled categories**. When `enabled_categories` is unset, the **full
set** of all 12 shows (back-compat).

**⚫ GENERAL is permanent**: always enabled, cannot be disabled.

### Presets

`task-station config --categories preset <name>` sets `enabled_categories` to a named
preset. The **universal core** — `red BUG`, `silver TOOLING`, `pink PERSONAL`,
`black GENERAL` — is seeded in **every** preset (removable except GENERAL):

| preset    | enabled slots                                                   |
|-----------|-----------------------------------------------------------------|
| `minimal` | core only (BUG · TOOLING · PERSONAL · GENERAL)                   |
| `web`     | core + FEATURE, DESIGN, INFRA, REVIEW, FIX                       |
| `data`    | core + DATA, FEATURE, INFRA, REVIEW                              |
| `ops`     | core + INFRA, DATA, REVIEW, FIX, RESEARCH                        |
| `full`    | all 12 (the default)                                            |

- `task-station config --categories` (no arg) — show the current enabled set + presets.
- `task-station config --enable <key>` / `--disable <key>` — toggle a single slot
  (accepts a key, emoji, or `[TAG]`). Disabling `black`/`GENERAL` is refused.
- Editing the raw `categories` override map in `config.json` still works.

## Skill → colour (immediate tinting)

The `skill_colors` array in `config.json` is an ordered list of `["regex", "color"]`
entries, prepended to the shipped defaults (in `lib/categories.py`). On every prompt the
`UserPromptSubmit` hook runs `task-station.py prompt-tint`, which pulls the invoked
command name out of the prompt (`<command-name>/myplugin:review-pr</command-name>`, or a
hand-typed `/foo …`) and, for the **first** regex that matches the name — with its
`plugin:` prefix kept — emits that category's tint escape **synchronously, before Claude
responds**. So running `/review` turns the terminal orange instantly. Current map:

| Pattern (regex, matched on the command name)        | Colour | Example skills                                  |
|-----------------------------------------------------|--------|-------------------------------------------------|
| `fix-pr`                                            | yellow | `my-fix-pr` (fixing PR review feedback)         |
| `review`, `security-review`                         | orange | any review skill — e.g. `review`, `security-review`, `code-review`, or any plugin-namespaced `<plugin>:review-*` |
| tooling: `update-config`, `keybindings`, `permission`, `schedule`, `statusline`, `init`, `claude-api`, `loop`, `deep-research`, `simplify`, `verify` | silver | `update-config`, `keybindings-help`             |

Order matters: list more specific patterns before broader ones so the right rule wins.
A prompt that invokes no skill, or one no pattern matches, tints nothing. When a skill
*does* match, the `prompt-context` guidance tells Claude the terminal is already tinted
and to reuse that colour for the task. Entries in your `skill_colors` override are
prepended and take priority over the shipped list.

## How it's wired — `categories.py` is an optional plugin

All category/colour logic lives in **`lib/categories.py`**, not in the core.
`task-station.py` imports it defensively (`try: import categories as cats / except: cats
= None`), so the tracker degrades gracefully:

- **No `categories.py`** (deleted from the plugin's `lib/`) → a plain, colourless
  tracker: no `[TAG]` column, no legend, `--color` accepted but ignored, no tint.
- **`categories.py` present, `tint_terminal: false`** → tasks still get the
  `<emoji> [TAG]` decoration and labels, but no terminal tinting.
- **`categories.py` present, `tint_terminal: true`** (the default) → full behaviour:
  tags, legend, and full-palette tinting on create/attach/resume and on skill runs.

`lib/categories.py` exposes: `CATEGORIES` + `DEFAULT` (the taxonomy + baked palettes),
`normalize`, `label`, `tag`, `summary`, `legend`, `compact_legend`, `hex_for`,
`tint_escape` (the full-palette escape string, `""` when tinting is off / the terminal is
unsupported), and `picker_lines` (the colour-choosing guidance, served via
`task-station.py guidance`).

**Do not edit `lib/categories.py` directly** — changes are overwritten on `/plugin
update`. Customize via `config.json` instead (path shown by `task-station config
--categories edit`):

```
${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/config.json
```

JSON shape (all keys optional — only what you set is stored):

```json
{
  "categories": {
    "green": {
      "tag": "VOLT",
      "label": "volt work",
      "fg": "#f3e2b2",
      "bold": "#d7f528",
      "cursor": "#ffffff",
      "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1", "#d338d3",
               "#33bbc8", "#cbcccd", "#818383", "#fc391f", "#31e722", "#eaec23",
               "#5833ff", "#f935f8", "#14f0f0", "#e9ebeb"]
    }
  },
  "enabled_categories": ["red", "white", "pink", "black", "green"],
  "tint_terminal": false,
  "tint_theme": "auto",
  "skill_colors": [ ["regexpattern", "color"] ],
  "workspace_dirs": ["/path/to/repos"]
}
```

- **`categories`** merges over (and can override) the shipped defaults. Each entry needs
  only `tag` + `label` — the `dot` and the **full palette** (`hex`/`hex_light`/`fg`/
  `bold`/`cursor`/`ansi`/`selbg`) are inherited from the slot (see *slot-canonical*).
  Supply any of those fields to override just that part of the palette. Entries missing
  `tag` or `label` are silently skipped; any invalid JSON leaves the shipped defaults
  entirely intact.
- **`enabled_categories`** — the list of "on" colour keys (see *Enabled set & presets*).
  Absent ⇒ the full set; `⚫ GENERAL` is always forced in. Usually set via `config
  --categories preset <name>` / `--enable` / `--disable`.
- **`tint_terminal`** toggles tinting globally. Set `false` to keep the `<emoji> [TAG]`
  decoration without any terminal tinting.
- **`tint_theme`** — `"auto"` (follow OS appearance), `"dark"`, or `"light"`. See
  *Dark / light* above.
- **`skill_colors`** entries are **prepended** to the shipped list, so your patterns win;
  each is `["regex", "color"]`, first match wins.

To remove categories entirely, delete `lib/categories.py` from the installed plugin
directory; `task-station.py` runs as a plain, colourless tracker. `task-station.py` never
names a colour — it only calls into `categories.py`.
