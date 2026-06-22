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
   tints the **whole terminal** to the active **theme's** palette for that category
   by writing standard OSC escape sequences. **No Terminal.app profiles and no shell
   aliases are required**; iTerm2 and Apple Terminal both honour it out of the box.

   > Profile-switching (`zsh -ic '<Color> Sands'` aliases) was **removed in 1.7.0**.
   > Tinting is now always the direct escape below.

## Themes & the full-palette escape

The colour itself comes from a **THEME** — a named, full-palette set (see
[Themes](#themes)). For each category a theme supplies `bg` (background), `fg`,
`bold`, `cursor`, `sel` (selection), and a 16-element `ansi` list. The category key
is the join: `THEMES[<active theme>][<category key>]`. `categories.tint_escape`
resolves the active theme's palette for the category, concatenates it into one
escape string, and the hooks write it to the *originating* window:

| Element | Escape | iTerm2 | Apple Terminal |
|---|---|:--:|:--:|
| background | `ESC ] 11 ; <hex> BEL` | ✓ | ✓ |
| foreground | `ESC ] 10 ; <hex> BEL` | ✓ | ✓ |
| cursor | `ESC ] 12 ; <hex> BEL` | ✓ | ✓ |
| ANSI 0–15 | `ESC ] 4 ; <n> ; <hex> BEL` | ✓ | ✓ |
| selection | `ESC ] 17 ; <hex> BEL` | ✓ | ✓ |
| bold colour | `ESC ] 1337 ; SetColors=bold=<hexNoHash> BEL` | ✓ | — (iTerm-only) |

A palette that defines only a background still emits just the background (back-compat
for minimal themes); an unknown colour, a category with no palette in the active
theme, or an unsupported terminal emits nothing.

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
**permanent** — always enabled, cannot be disabled (see *Enabled set* below).

### The dot is slot-canonical — "you pick the colour; the colour determines the icon"

Each colour slot **owns** an emoji. When you override a category or add a new one, you
supply only `tag` + `label` — the **dot is inherited from the slot automatically** (an
explicit `dot` is still allowed but optional). The **colour is no longer part of the
category**: every theme already defines a palette for each slot key, so
`{"green": {"tag": "VOLT", "label": "volt work"}}` keeps 🟢 and the active theme's green
palette while relabelling the slot. To change colours, edit the **theme** (see below),
not the category.

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

## Themes

A **theme** is a named, full-palette colour set. For each category key it defines
`bg`, `fg`, `bold`, `cursor`, `sel`, and a 16-element `ansi` list; the **active theme**
supplies the tint for every category. The taxonomy (dot/tag/label) is theme-independent —
only the colours change between themes. Two ship:

- **`dusk`** — the default: dark, muted.
- **`sands`** — vibrant.

The active theme is `config.active_theme()` — config key `theme`, validated against the
available themes (shipped + user), falling back to `dusk`.

```text
config --theme                 # list shipped + user themes, mark the active one (also: list)
config --theme sands           # select a theme as active
config --theme save my-theme   # snapshot the effective active palette into config.json
config --theme edit            # print the config.json path (edit user themes there)
config --theme preview         # render an HTML gallery → <data_dir>/themes-preview.html
```

`--theme` is **verb-first**: the first token is a verb if it's one of
`save·edit·preview·list`; otherwise it's a theme **name** to select.

### Overriding & adding themes

`config.json` `themes` is **deep-merged** over the shipped `THEMES`, per theme → per
category → per field — so you can tweak one colour, or add a whole new named theme, and
it survives `/plugin update`. `effective_themes()` does the merge (on a deep copy; the
shipped themes are never mutated). Examples:

```jsonc
{
  "theme": "sands",                                 // active theme
  "themes": {
    "dusk":  { "red": { "bg": "#1a0e10" } },        // tweak one field of a shipped theme
    "ocean": {                                       // a brand-new named theme
      "green": { "bg": "#001a22", "fg": "#dfeef2", "bold": "#5fd0dc",
                 "cursor": "#5fd0dc", "sel": "#04323a",
                 "ansi": ["#0b1416", "..."] }         // 16 entries
    }
  }
}
```

**Reserved theme names** (cannot be saved): `save`, `edit`, `preview`, `list`, `show`,
`default`. A saved/added theme name must match `^[a-z0-9][a-z0-9_-]*$`. `config --theme
save <name>` snapshots the **effective active** palette (full per-category set) under
that name; it refuses reserved or malformed names.

## Enabled set (lean default that grows)

The **enabled set** governs what shows on the board and in the legend — it is *display*
only; a task can be assigned any of the 12 taxonomy slots regardless. It is stored in
`config.json` as `enabled_categories` (a list of colour keys).

**The board starts lean and grows.** When `enabled_categories` is unset, the enabled set
is **CORE** — just `🔴 BUG`, `🟢 FEATURE`, and `⚫ GENERAL` — so a new install isn't
cluttered with twelve slots you don't use yet. (Auto-enable then grows the set on its own
as you categorise tasks — see *Auto-enable* below.)

**⚫ GENERAL is permanent**: always enabled, cannot be disabled.

- `task-station config --categories` (no arg) — show the current enabled set.
- `task-station config --enable <key>` / `--disable <key>` — toggle a single slot
  (accepts a key, emoji, or `[TAG]`). Disabling `black`/`GENERAL` is refused.
- Editing the raw `categories` override map in `config.json` still works.

### Auto-enable (the board grows itself)

The categoriser always considers the **full 12-slot taxonomy** so it can pick the most
accurate category — even one that isn't on the board yet. When **`auto_categories`** is on
(the default) and a task is assigned to a slot that isn't in the enabled set, that slot is
**enabled automatically** (persisted to `enabled_categories`) and prints a one-line notice,
e.g. `enabled new category 🔵 [INFRA]`. From then on it shows on the board and legend like
any other. This applies to every assignment path: `create --color`, `attach --color`,
`update --color`, and the Desktop bridge's create tool.

The enabled set governs **display only** — assignment can target any taxonomy slot
regardless of what's enabled. So the board converges on exactly the categories you use,
starting from CORE, with no manual curation.

Turn it off to keep a fixed set:

- `task-station config --auto-categories off` (or `--auto-categories-get` to read it),
  or the env escape `TASK_STATION_AUTO_CATEGORIES=off`.
- With auto-enable off, assignment no longer grows the board; the legend/picker restrict
  to the currently-enabled slots, and you curate the set by hand with `--enable`/`--disable`.

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

`lib/categories.py` exposes: `CATEGORIES` + `DEFAULT` (the dot/tag/label taxonomy),
`THEMES` + `DEFAULT_THEME` (the named full palettes), `effective_themes` /
`available_themes` / `theme_palette` (the merged registry + accessors), `normalize`,
`label`, `tag`, `summary`, `legend`, `compact_legend`, `tint_escape` (the active-theme
full-palette escape string, `""` when tinting is off / the colour or terminal is
unsupported), and `picker_lines` (the colour-choosing guidance, served via
`task-station.py guidance`). The active theme name is resolved by `config.active_theme()`.

**Do not edit `lib/categories.py` directly** — changes are overwritten on `/plugin
update`. Customize via `config.json` instead (path shown by `task-station config
--categories edit`):

```
${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/config.json
```

JSON shape (all keys optional — only what you set is stored):

```jsonc
{
  "categories": {
    "green": { "tag": "VOLT", "label": "volt work" }   // dot + colour inherited
  },
  "theme": "sands",                                     // active theme (default: dusk)
  "themes": {
    "dusk":  { "green": { "bold": "#d7f528" } },        // tweak one shipped-theme field
    "ocean": {                                           // a brand-new named theme
      "green": { "bg": "#001a22", "fg": "#dfeef2", "bold": "#5fd0dc",
                 "cursor": "#5fd0dc", "sel": "#04323a",
                 "ansi": ["#000000", "#c23621", "#25bc24", "#adad27", "#492ee1",
                          "#d338d3", "#33bbc8", "#cbcccd", "#818383", "#fc391f",
                          "#31e722", "#eaec23", "#5833ff", "#f935f8", "#14f0f0",
                          "#e9ebeb"] }
    }
  },
  "enabled_categories": ["red", "white", "pink", "black", "green"],
  "tint_terminal": false,
  "skill_colors": [ ["regexpattern", "color"] ],
  "workspace_dirs": ["/path/to/repos"]
}
```

- **`categories`** merges over (and can override) the shipped taxonomy. Each entry needs
  only `tag` + `label` — the `dot` is inherited from the slot (see *slot-canonical*); an
  explicit `dot` overrides it. Colour is **not** here — it lives in `themes`. Entries
  missing `tag` or `label` are silently skipped; any invalid JSON leaves the shipped
  defaults entirely intact.
- **`theme`** — the active theme name (default `dusk`); validated against the available
  themes, falling back to `dusk`. Set via `config --theme <name>`.
- **`themes`** — per-theme → per-category → per-field overrides deep-merged over the
  shipped `THEMES`; brand-new named themes are allowed. Fields per category: `bg`, `fg`,
  `bold`, `cursor`, `sel`, `ansi` (16 entries). Survives `/plugin update`.
- **`enabled_categories`** — the list of "on" colour keys (see *Enabled set*).
  Absent ⇒ CORE (`BUG · FEATURE · GENERAL`); `⚫ GENERAL` is always forced in. Usually
  set via `config --enable` / `--disable` (or grown automatically by auto-enable).
- **`tint_terminal`** toggles tinting globally. Set `false` to keep the `<emoji> [TAG]`
  decoration without any terminal tinting.
- **`tint_theme`** — `"auto"` (follow OS appearance), `"dark"`, or `"light"`. See
  *Dark / light* above.
- **`skill_colors`** entries are **prepended** to the shipped list, so your patterns win;
  each is `["regex", "color"]`, first match wins.

To remove categories entirely, delete `lib/categories.py` from the installed plugin
directory; `task-station.py` runs as a plain, colourless tracker. `task-station.py` never
names a colour — it only calls into `categories.py`.
