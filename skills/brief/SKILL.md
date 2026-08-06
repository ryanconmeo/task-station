---
name: brief
description: Author a task's HTML design brief that a reader gets in one pass — derive the sections from the material, carry detail in tables/diagrams/code rather than prose, collapse the how, and always state the limits. Writes the HTML directly against the shipped stylesheet (assets/brief.css) and diagram catalogue (references/diagrams.md). Use on `/brief` or "make/regenerate the brief for this task".
---

# Brief — a document a reader gets in one pass

A **brief** explains a change to a competent person **who was not in the room**, in one
read, without backtracking. They finish it able to say what is broken, what the change
is, what it does not solve, and what happens next.

That is the only test. Everything below exists because a real brief failed it and had to
be rewritten by hand.

## `/brief` is not one template

There is no fixed section list. There is no house layout you fill in. The material
decides the shape, and the shape is different every time. **If the material's natural
shape is not a decision one-pager, do not force it into one** — a migration brief, a
"here is how this subsystem actually works" brief, and a "we picked B over A" brief are
three different documents.

What is fixed: the **stylesheet** (`assets/brief.css`), the **diagram vocabulary**
(`references/diagrams.md`), the four mandatory slots (thesis · evidence-carrying current
state · Limits · Plan), and the voice.

## Flow

1. **Gather.**
   - `task-station glossary --task <n>` — the task's canonical vocabulary.
   - Walk the ADO tree from the task's stored PR/story seeds (and any `--feature <id>`)
     with the host's ADO tool. Note each node's type, id, url, title, and one line of
     what it does. This becomes the footer, not a section.
   - Read the code. Every claim about current state needs a file and a line number
     (rule: *evidence in the same row*), and every command you write needs to exist
     (rule: *only real commands*).
2. **Derive the sections** — see below. Do this before writing a sentence.
3. **Draft** each section as *one or two plain sentences + the artifact that carries the
   detail*.
4. **Self-check** against the list at the bottom. Fix what fails.
5. **Write** the HTML to the path from `task-station brief path --task <n>`.
6. **Report** the path. `--publish` mints a Claude Artifact.

## Vocabulary is forced, not suggested

The glossary terms are the document's only names for those things. Use them **verbatim**,
including case. Do not introduce a synonym for variety — a reader who meets `fixture` in
one section and "test bundle" in the next has to stop and check whether they are the same
thing, and that is exactly the backtrack this document exists to prevent.

Wrap a term in `<code>` when it is a literal token (a path, a script name, a flag).
Otherwise leave it as plain text. Do not tag terms with decorative spans.

---

## Rule 1 — sections are the reader's questions

Every section title answers something the reader is actually asking, in the reader's
words. Not document furniture ("Background", "Context", "Overview", "Approach", "Design",
"Summary").

Derive them: list the questions this reader will have, in the order they will have them.
That list *is* the section list.

The worked example — the sections a real brief ended up with, after eight rounds:

> `Broken now` · `The proposal: seeds → test-data` · `Fixture vs run` ·
> `Who may write what` · `How to change the demo world` · `How to get test data` ·
> `Smoke vs e2e` · `Limits` · `Plan`

Note what happened there:

- Nine sections, none of them generic.
- **A section titled with a noun lost to one titled with the change itself.** "The split"
  became `The proposal: seeds → test-data`.
- Two of them are how-tos, scoped to *one* audience each (rule 4).
- The comparisons are named as comparisons — `Fixture vs run`, `Smoke vs e2e` — so the
  reader knows before entering what distinction they are about to be handed.

## Rule 2 — name the change in the reader's own before→after tokens

`seeds → test-data`. The actual directory names, the actual script names, the actual
column names. Never "the restructure", "the refactor", "the new approach", "the
migration". A reader who greps their repo for your noun should find it.

Put the arrow in the heading. It is the single highest-value four characters in the
document.

## Rule 3 — the prose budget, stated as shape

**One or two plain sentences per section, then a table, diagram, or code block carries the
detail.** Never a paragraph doing a table's job.

The reference brief: **~1,280 visible words across 9 sections and 4 diagrams.** Do not
treat that as a target to hit. The failure is real at *both* ends:

| Draft | Words | Verdict |
|---|---|---|
| first hand-written version | ~2,600 | "too verbose" |
| hard cut | ~1,540 | "0% verbose" — and unreadable |
| what shipped | ~1,280 | right |

The 1,540-word version was *shorter* than the one called too verbose and read worse. So
word count is not the lever. **The lever is what carries the detail.** Cutting words out
of paragraphs makes dense paragraphs. Moving the detail into a table makes the prose short
*and* the detail scannable, and the word count falls out of that.

If a section is three paragraphs, you have not found its table yet.

## Rule 4 — persona-scope how-to sections, and badge them

When two audiences need different instructions, that is **two sections**, one per
audience — not one section with "if you're QA…" branches inside it.

Badge the heading so a reader can skip what is not theirs:

```html
<h2>How to change the demo world <span class="who dev">dev</span></h2>
<h2>How to get test data <span class="who qa">qa</span></h2>
```

`.who.dev` is green, `.who.qa` is amber. Both are small mono, so they read as labels
rather than headings. Add more personas by reusing `.who` and picking the hue that fits;
do not invent a new visual treatment.

## Rule 5 — diagrams show the mechanism's actual shape

Four bespoke inline SVGs beat two parametrized templates that produce generic boxes.

**Any contrast that is fundamentally about shape — wide vs deep, long vs short, crosses vs
stops, before vs after — gets a picture, not a table.**

The four patterns, with copyable skeletons, are in **`references/diagrams.md`**. Read it
before drawing. In short:

| Pattern | Shows |
|---|---|
| **promotion ladder** | one thing crosses a boundary, another stops at it |
| **lifetime timeline** | one thing persists, another is disposable |
| **pipeline flow** | a sequence, with the one box a human authors marked |
| **breadth vs depth** | wide-and-shallow against narrow-and-deep |

The fourth exists because a **table failed**. The human asked for it explicitly after the
table version of that contrast did not land. That is the signal to draw: not "this section
could use a visual", but "a table said it and the reader still did not feel it".

The inverse rule matters as much: **if a two-column table says it completely, do not
draw it.** Two boxes with an arrow between them is a sentence with extra steps.

Mechanics (all mandatory, all in `references/diagrams.md`): theme colors via
`var(--promote)` / `var(--hold)` / `currentColor` and never a hardcoded hex; `role="img"`
plus `<title id>` + `<desc id>` + `aria-labelledby`; wrapped in `<figure>` so the figure
scrolls and the page never does; `min-width` on the svg so it degrades to a scroll rather
than an illegible squish.

## Rule 6 — every claim about current state carries its source in the same row

The current-state section is a table whose **first column is a mono file path with line
numbers**:

```html
<h2>Broken now</h2>
<div class="tw"><table>
  <thead><tr><th>Where</th><th>What happens</th><th>Why it hurts</th></tr></thead>
  <tbody>
    <tr>
      <th class="m">deploy-api-dev.yml:144, 203</th>
      <td>The seed step runs twice against the same database.</td>
      <td>The second run collides and the deploy goes red on a clean branch.</td>
    </tr>
  </tbody>
</table></div>
```

Not a prose provenance paragraph at the end. A reader who doubts one row must be able to
check *that row* without hunting. A provenance footer answers "where did this document
come from"; it cannot answer "where did this claim come from".

## Rule 7 — implementation detail collapses

**Open = what and why. Collapsed = how.**

```html
<details><summary>the collision, mechanically</summary>
  <div class="db">
    <pre>…</pre>
  </div>
</details>
```

Closed by default; the stylesheet gives it a `+` / `−` marker. What went inside, in the
reference brief: code, route tables, collision mechanics. What stayed outside: the
workflow steps and the rules.

**Exception:** when a file *is* the answer to "how do I define this?", show it open. See
rule 10.

## Rule 8 — a `Limits` section is mandatory

Two things the design does **not** solve, each with a concrete example. Not hedges, not
"future work" — actual holes, named.

```html
<h2>Limits</h2>
<div class="lim">
  <b>Nothing keeps two scenarios from claiming the same email.</b>
  <p>Two suites that both create <code>a@example.com</code> pass alone and fail together.
     Nothing detects it; the second insert just errors.</p>
</div>
```

Why this is not optional: a reader's question exposed a real hole the draft had papered
over. Stating the limits is what earns the reader's trust in everything else — and it is
where a missing constraint surfaces while it is still cheap to add. A brief with no
`Limits` section is not a shorter brief, it is a brief that has not been checked.

## Rule 9 — never let one overloaded verb carry a permission distinction

This sentence shipped and was unreadable:

> "Nobody owns a fixture individually, so no individual can write one."

It became a heading plus a table:

> **Authoring and applying are different things**

| Who | Author the file | Apply to local | Apply to shared | Stage + prod |
|---|---|---|---|---|
| dev | yes | yes | with review | no |
| qa | yes | yes | no | no |

Generalize it: **when a rule constrains who may do what, name each verb and give it its
own column.** One word ("write", "own", "manage", "access") doing the work of three
distinct actions is the defect. The table is not a formatting choice — splitting the
columns is what forced the distinction to be stated.

## Rule 10 — show the artifact the human authors, and annotate the line that links it onward

A how-to section does not land until the reader sees the file they will type. Show it
**open**, and comment the line that connects it to whatever consumes it:

```html
<pre>export const basic = defineScenario(() =&gt; {
  const customer = createCustomer({ plan: 'pro' });
  return { customer };  <span class="c">// ← what the test gets</span>
});</pre>
```

The `<span class="c">` is the stylesheet's comment grey; `.g` / `.a` / `.r` are green /
amber / oxblood for the rare highlighted line. Show the **definition point** — where the
human's input enters the machinery — not the machinery.

## Rule 11 — only use commands that exist

An invented `pnpm test-data fixtures --env local` shipped in a draft and had to be
replaced with the real `pnpm local:start`.

**Grep the repo for the actual script names before writing any command into a brief.**
`package.json` scripts, Makefile targets, CI job names, CLI subcommands — read them, do
not infer them from what the command *ought* to be called. A single wrong command costs
the reader more trust than the whole document buys.

## Rule 12 — load-bearing rules get their own box, numbered

```html
<div class="rule">
  <b>rule 1</b>
  <p>You may write only what you own.</p>
</div>
```

Place each box **in the section it governs**. Do not collect them into a "Rules" section
at the end — a rule read away from the thing it constrains is a rule the reader will not
apply. Two or three across the whole document; a fourth usually means one of them is not
load-bearing.

## Rule 13 — the plan is a 3-column ladder at full width

Marker · short title · detail. Full width — not a narrow measure with a marker column
eating it.

```html
<ol class="plan">
  <li class="done"><b>Split the seed script</b><span>already on main</span></li>
  <li class="now"><b>Point CI at test-data</b><span>one line in the workflow; do this first</span></li>
  <li><b>Delete the old fixtures dir</b><span>after two green runs</span></li>
</ol>
```

Marker states: `.done` → `✓` in green · `.now` → amber, for "do first / cheap" · plain →
everything else. The stylesheet collapses it to two columns under 780px.

Add **one line under the ladder** saying what the amber grouping means — an amber marker
with no key is a color the reader has to guess at:

```html
<p class="sub">Amber steps are cheap and unblock the rest; do them first.</p>
```

## Rule 14 — do not sound like AI

An explicit do-not list. Each of these was cut from a real draft by hand.

**Cut:**

- **Rhetorical setups.** "The boundary was never about X." "This is not a Y problem."
  State the thing; do not stage it.
- **Cadence tricks.** "Two passes, two bug classes, one pipeline." Rhythm the reader
  notices is rhythm that cost them a beat of comprehension.
- **Section tag labels used as decoration.** An eyebrow that says `SECTION 3` or
  `CONTEXT` carries nothing. `.eyebrow` is for one real thing at the top (the project,
  the date, the feature id).
- **Self-congratulating summaries.** "The result is a clean separation of concerns." The
  reader decides whether it is clean.
- **Stacked em-dash asides.** One aside per sentence, at most. Two means the sentence
  wanted to be two sentences.
- **Any sentence whose only job is to introduce the next sentence.** "There are three
  things to consider here." Delete it; the three things introduce themselves.
- **Trailing "next steps" editorializing.** The `Plan` is the next steps. A closing
  paragraph telling the reader how to feel about the plan is not.

**Also:** no emoji, no "let's", no second person plural ("we should"), no bold on whole
sentences. Bold marks the two or three words a skimmer needs.

---

## The document skeleton

Write a complete standalone HTML file — it has to open from disk. Inline the **entire**
contents of `assets/brief.css` in a single `<style>` block; never link it. The file must
be self-contained (that is also what lets `--publish` pass the Artifact CSP).

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test data — how it works after the split</title>
<style>/* ← every line of skills/brief/assets/brief.css, pasted */</style>
</head>
<body>
<div class="wrap">

  <p class="eyebrow">projectname · 2026-08-06 · feature 3049</p>
  <h1>Test data — how it works after the split</h1>
  <p class="thesis">Schema promotes; data does not.</p>
  <p class="sub">One or two sentences of orientation: who this is for and what changes.</p>

  <!-- derived sections here — see rule 1 -->

  <footer>
    <p>Feature <a href="…">3049</a> → Story <a href="…">3133</a> → PR <a href="…">931</a>.</p>
    <p>Task 512 · regenerate with <code>/brief</code>.</p>
  </footer>

</div>
</body>
</html>
```

The four mandatory slots:

| Slot | What it is |
|---|---|
| **thesis** | one serif-italic line, the whole document compressed (`.thesis`) |
| **current state** | evidence-carrying table, source in every row (rule 6) |
| **`Limits`** | two named holes with examples (rule 8) |
| **`Plan`** | the 3-column ladder (rule 13) |

Plus the **footer**: the ADO tree as a single hyperlinked line (Feature → Story → PR),
the task, and how to regenerate. The ADO tree is footer material — it is traceability,
not something the reader needs in order to understand the change.

Use `.thesis` exactly once. It is the only serif in the document, and a second one
destroys the first.

## Writing it and recording the path

```bash
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" brief path --task <n>
```

This resolves the task, creates the artifact directory, records `brief_path` on the task
so the brief is findable later, and prints the absolute path. Write your HTML to that
path with the Write tool, then surface the path to the user.

`brief path` reads no spec and takes no stdin.

## `--publish`

`--publish` mints a shareable **Claude Artifact** from the rendered HTML — a host-side
step. The Artifact host supplies its own `<!doctype>` / `<head>` / `<body>` wrapper, so
publish a **body-only copy**: write everything from `<style>` through `</div>` (the
`.wrap` block) to a scratch file and publish that. Keep the full standalone document at
`brief_path`.

It is not an org-wiki filing and touches nothing outside the host. The sources of truth
stay the task (glossary), the repo (code), and ADO (tree) — losing the HTML is a
non-event, re-run `/brief`.

## Legacy: `brief render --spec`

The old flow — emit a **brief-spec (JSON)** and let `task-station brief render --spec
<file>` template `lib/brief_template.html` — **still works and is still supported**. It
is retained for back-compat and for anything already scripted against it. It renders a
fixed section list into a frozen layout with no dark theme, which is why it is no longer
the default path. Prefer authoring the HTML.

---

## Self-check — run this before writing the file

Answer every line. A "no" means fix it, not note it.

1. Would someone who was not in the room finish this in one pass, without backtracking?
2. Is every section title a question the reader actually has, in their words? No
   "Background" / "Overview" / "Approach"? Is each how-to scoped to **one** audience and
   badged, rather than branching inside one section?
3. Is the change named with real before→after tokens they could grep for?
4. Does any section run to three paragraphs? (Then its table is missing.)
5. Does every current-state claim carry its file and line **in the same row**?
6. Is every command in the document one you found in the repo?
7. Is the how collapsed and the what/why open — with the authored file shown open?
8. Is there a `Limits` section with two concrete, named holes?
9. Does any single verb carry a permission distinction that needs its own columns? Is each
   load-bearing rule in a numbered box **inside the section it governs**?
10. Does every diagram show a *shape*, and would a picture-only reader state the point?
11. Is each diagram theme-safe — `var(--…)` / `currentColor`, no hardcoded hex — with
    `role="img"`, a titled `<desc>`, a `<figure>` wrapper, and a `min-width`?
12. Is the plan a full-width 3-column ladder with a line explaining the amber?
13. Is every glossary term used verbatim, with no synonyms introduced?
14. Read it aloud. Any rhetorical setup, tricolon, self-congratulation, stacked em-dash,
    sentence-introducing-a-sentence, or trailing next-steps editorializing? Cut it.
15. Is the CSS inlined, `.thesis` used exactly once, and the ADO tree in the footer?
