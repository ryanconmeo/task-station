# Brief diagrams — the four patterns that earned their place

A diagram in a brief has one job: **make a difference in shape visible in one glance.**
Not to decorate a section, not to restate a table.

## When to draw, when not to

Draw when the contrast is fundamentally about **shape**:

| The contrast | Pattern |
|---|---|
| crosses vs stops — one thing travels further than another | **promotion ladder** |
| long vs short — one thing outlives another | **lifetime timeline** |
| a sequence with one authored step and four generated ones | **pipeline flow** |
| wide-and-shallow vs narrow-and-deep | **breadth vs depth** |

**When NOT to draw:** if a two-column table says it completely, don't draw it. A table
with `Thing | What it does` rows is not a shape — it is a list, and a picture of a list
is worse than the list. Two boxes with an arrow between them is also not a diagram; it
is a sentence with extra steps.

The rule that decides it: *could a reader who only saw the picture state the point?* If
no, either the picture is wrong or the point is not visual.

## Mechanics — non-negotiable for every figure

- **No hardcoded hex inside SVG.** Use `var(--promote)` (good / promotes / crosses),
  `var(--hold)` (attention / stays / stops here), `var(--broken)` (broken / absent /
  limit), `currentColor` for neutral ink, `var(--card)` for box fills. That is what makes
  the diagram flip with the theme instead of going invisible in dark mode.
- **`var()` does not work in a presentation attribute.** `fill="var(--promote)"` silently
  fails in some browsers — presentation-attribute values are not parsed as CSS values.
  Put the color in a scoped `<style>` block inside the `<svg>` (patterns below) or an
  inline `style="fill:var(--promote)"`. Never as a bare `fill=` attribute.
- **Scope the `<style>` block with a per-diagram class prefix** (`.dgm-ladder text{…}`).
  A `<style>` inside inline SVG is global to the page; an unprefixed `text{…}` rule would
  restyle every other figure.
- **A class on a `<g>` does not beat a rule matching the `<text>` directly.** If
  `.dgm-x text{font-size:12px}` exists, a `font-size` set on a parent `<g class="env">`
  is *inherited* and loses to that direct match. Write `.dgm-x .env text{…}`, or put the
  class on the `<text>` elements themselves.
- **Unique ids per figure.** `<title id>`, `<desc id>` and any `<marker id>` collide
  across figures in the same document, and a collision breaks `aria-labelledby`. Prefix
  them with the diagram's slug.
- **Accessibility:** `role="img"` on the `<svg>`, a `<title id>` stating the *conclusion*
  (not the subject), a `<desc id>` describing what is drawn, and
  `aria-labelledby="<title-id> <desc-id>"`.
- **Wrap in `<figure>`.** `figure{overflow-x:auto}` is in the stylesheet: the figure
  scrolls, the page never scrolls sideways.
- **Give the `<svg>` a `min-width`** via `class="w700"` (600px floor) or `class="w700s"`
  (520px floor). Without it a wide diagram squishes to illegible on a phone instead of
  becoming a scroll.
- **`viewBox` + no `width`/`height` attributes.** `figure svg{max-width:100%;height:auto}`
  does the scaling.
- Avoid `text-transform` in SVG — support is uneven. Type the label the way you want it.
- Keep text at 10.5px and up in viewBox units, and keep every label horizontal. Rotated
  axis labels cost more to read than they save in width.

---

## 1. Promotion ladder — "crosses vs stops"

Environments across the x-axis, two lanes stacked, a dot where each lane exists, a dashed
vertical boundary, `✗` marks past it. Shows *"schema crosses, data doesn't"* without a
sentence.

Swap in: the lane names, the column names, where the boundary falls.

```html
<figure>
  <svg class="w700 dgm-ladder" viewBox="0 0 700 180" role="img"
       aria-labelledby="ladder-t ladder-d">
    <title id="ladder-t">Schema reaches every environment; data stops after dev</title>
    <desc id="ladder-d">Two lanes across five environments. The schema lane has a dot in
      all five. The data lane has dots in local, ci and dev, then crosses at stage and
      prod, past a dashed boundary.</desc>
    <style>
      .dgm-ladder text{fill:currentColor;font-family:var(--sans);font-size:12px}
      /* the class is on the <g>, so target the <text> children — an inherited
         font-size loses to the `.dgm-ladder text` rule that matches them directly */
      .dgm-ladder .env text{font-size:11px;letter-spacing:.06em;opacity:.72}
      .dgm-ladder .lane{font-weight:620}
      .dgm-ladder .rail{stroke:currentColor;opacity:.18;stroke-width:1}
      .dgm-ladder .bound{stroke:var(--hold);stroke-width:1;stroke-dasharray:4 4;opacity:.8}
      .dgm-ladder .on{fill:var(--promote)}
      .dgm-ladder .off{stroke:var(--broken);stroke-width:2;fill:none}
      .dgm-ladder .note{font-size:11px;fill:var(--hold)}
    </style>
    <g class="env" text-anchor="middle">
      <text x="150" y="24">LOCAL</text><text x="265" y="24">CI</text>
      <text x="380" y="24">DEV</text><text x="495" y="24">STAGE</text>
      <text x="610" y="24">PROD</text>
    </g>
    <text class="lane" x="0" y="82">schema</text>
    <line class="rail" x1="130" y1="78" x2="660" y2="78"/>
    <text class="lane" x="0" y="130">data</text>
    <line class="rail" x1="130" y1="126" x2="660" y2="126"/>
    <line class="bound" x1="437" y1="10" x2="437" y2="148"/>
    <text class="note" x="437" y="166" text-anchor="middle">data stops here</text>
    <g class="on">
      <circle cx="150" cy="78" r="5"/><circle cx="265" cy="78" r="5"/>
      <circle cx="380" cy="78" r="5"/><circle cx="495" cy="78" r="5"/>
      <circle cx="610" cy="78" r="5"/>
      <circle cx="150" cy="126" r="5"/><circle cx="265" cy="126" r="5"/>
      <circle cx="380" cy="126" r="5"/>
    </g>
    <g class="off">
      <path d="M489 120l12 12M501 120l-12 12"/>
      <path d="M604 120l12 12M616 120l-12 12"/>
    </g>
  </svg>
</figure>
```

## 2. Lifetime timeline — "long vs short"

One long bar against several short bars on a shared time axis. Shows *"persistent vs
disposable"* without a sentence. Green = the thing that persists, amber = the thing that
is thrown away.

Swap in: the two lane names, the number and spacing of short bars, the axis end labels.

```html
<figure>
  <svg class="w700 dgm-life" viewBox="0 0 700 152" role="img"
       aria-labelledby="life-t life-d">
    <title id="life-t">Seed data persists; fixture data is disposable</title>
    <desc id="life-d">A time axis. The seed lane is one bar spanning the whole axis. The
      fixture lane is four short bars separated by gaps.</desc>
    <style>
      .dgm-life text{fill:currentColor;font-family:var(--sans);font-size:12px}
      .dgm-life .lane{font-weight:620}
      .dgm-life .cap{font-size:11px;opacity:.72}
      .dgm-life .long{fill:var(--promote-bg);stroke:var(--promote);stroke-width:1}
      .dgm-life .short{fill:var(--hold-bg);stroke:var(--hold);stroke-width:1}
      .dgm-life .axis{stroke:currentColor;opacity:.25}
    </style>
    <text class="lane" x="0" y="48">seed</text>
    <rect class="long" x="130" y="34" width="530" height="20" rx="3"/>
    <text class="cap" x="132" y="72">one row set, alive as long as the environment is</text>
    <text class="lane" x="0" y="106">fixture</text>
    <rect class="short" x="130" y="92" width="72" height="20" rx="3"/>
    <rect class="short" x="242" y="92" width="52" height="20" rx="3"/>
    <rect class="short" x="334" y="92" width="88" height="20" rx="3"/>
    <rect class="short" x="462" y="92" width="60" height="20" rx="3"/>
    <line class="axis" x1="130" y1="128" x2="660" y2="128"/>
    <text class="cap" x="130" y="146">test starts</text>
    <text class="cap" x="660" y="146" text-anchor="end">test ends</text>
  </svg>
</figure>
```

## 3. Pipeline flow — "one authored step, four generated"

Labelled boxes with arrows. **Each box is two lines: name + subtitle.** The box a human
authors is outlined in `var(--hold)`, and the caption names it. A flow whose boxes are all
identical in weight teaches nothing — the point is *where the human touches it*.

Swap in: the box count (4–6 reads well; past 6, split the diagram), the names, which box
is `authored`.

```html
<figure>
  <svg class="w700 dgm-flow" viewBox="0 0 700 112" role="img"
       aria-labelledby="flow-t flow-d">
    <title id="flow-t">A scenario file becomes rows a test can read</title>
    <desc id="flow-d">Five stages left to right: scenario file, builder, seed API,
      database, assertion. Only the scenario file is written by a person.</desc>
    <style>
      .dgm-flow text{fill:currentColor;font-family:var(--sans)}
      .dgm-flow .box{fill:var(--card);stroke:currentColor;stroke-opacity:.28}
      .dgm-flow .box.authored{stroke:var(--hold);stroke-opacity:1;stroke-width:1.5}
      .dgm-flow .nm{font-size:12px;font-weight:620}
      .dgm-flow .sb{font-size:10.5px;opacity:.7}
      .dgm-flow .arw{stroke:currentColor;opacity:.4;stroke-width:1.2;fill:none}
      .dgm-flow .cap{font-size:11px;fill:var(--hold)}
      .dgm-flow marker path{fill:currentColor;fill-opacity:.45}
    </style>
    <defs>
      <marker id="dgm-flow-tip" viewBox="0 0 8 8" refX="7" refY="4"
              markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0 0l8 4-8 4z"/>
      </marker>
    </defs>
    <rect class="box authored" x="20" y="20" width="112" height="52" rx="4"/>
    <text class="nm" x="76" y="42" text-anchor="middle">scenario.ts</text>
    <text class="sb" x="76" y="58" text-anchor="middle">you write this</text>
    <rect class="box" x="157" y="20" width="112" height="52" rx="4"/>
    <text class="nm" x="213" y="42" text-anchor="middle">builder</text>
    <text class="sb" x="213" y="58" text-anchor="middle">fills defaults</text>
    <rect class="box" x="294" y="20" width="112" height="52" rx="4"/>
    <text class="nm" x="350" y="42" text-anchor="middle">seed API</text>
    <text class="sb" x="350" y="58" text-anchor="middle">one call</text>
    <rect class="box" x="431" y="20" width="112" height="52" rx="4"/>
    <text class="nm" x="487" y="42" text-anchor="middle">database</text>
    <text class="sb" x="487" y="58" text-anchor="middle">rows exist</text>
    <rect class="box" x="568" y="20" width="112" height="52" rx="4"/>
    <text class="nm" x="624" y="42" text-anchor="middle">assertion</text>
    <text class="sb" x="624" y="58" text-anchor="middle">test reads them</text>
    <g class="arw" marker-end="url(#dgm-flow-tip)">
      <path d="M132 46h19"/><path d="M269 46h19"/>
      <path d="M406 46h19"/><path d="M543 46h19"/>
    </g>
    <text class="cap" x="20" y="98">A person authors only the first box; everything right
      of it is generated.</text>
  </svg>
</figure>
```

## 4. Breadth vs depth — the conceptual contrast a table cannot carry

A split panel. Left: many marks, each touched once, all the same shallow depth. Right: one
column of stacked steps joined by down-arrows. Use this when the point is *the shape of the
coverage itself* — a table of "smoke: many screens, one check each / e2e: one journey, many
steps" reads as two facts, and the reader does not feel the difference.

Swap in: the two panel headings, the mark count on the left, the step count on the right.

```html
<figure>
  <svg class="w700s dgm-bd" viewBox="0 0 700 224" role="img"
       aria-labelledby="bd-t bd-d">
    <title id="bd-t">Smoke goes wide and shallow; e2e goes narrow and deep</title>
    <desc id="bd-d">Left panel: eighteen short marks in a grid, each the same shallow
      depth, one per screen. Right panel: a single column of four stacked steps joined by
      downward arrows.</desc>
    <style>
      .dgm-bd text{fill:currentColor;font-family:var(--sans);font-size:12px}
      .dgm-bd .hd{font-weight:620}
      .dgm-bd .cap{font-size:11px;opacity:.72}
      .dgm-bd .mark{stroke:var(--promote);stroke-width:2;fill:none}
      .dgm-bd .dot{fill:var(--promote)}
      .dgm-bd .step{fill:var(--hold-bg);stroke:var(--hold);stroke-width:1}
      .dgm-bd .down{stroke:var(--hold);stroke-width:1.2;fill:none}
      .dgm-bd .split{stroke:currentColor;opacity:.2;stroke-dasharray:3 4}
      .dgm-bd marker path{fill:var(--hold)}
    </style>
    <defs>
      <marker id="dgm-bd-tip" viewBox="0 0 8 8" refX="7" refY="4"
              markerWidth="5" markerHeight="5" orient="auto">
        <path d="M0 0l8 4-8 4z"/>
      </marker>
    </defs>
    <line class="split" x1="352" y1="10" x2="352" y2="214"/>

    <text class="hd" x="0" y="24">smoke — every screen, one check each</text>
    <g>
      <!-- 6 columns x 3 rows; every mark the same 16-unit depth -->
      <g class="dot">
        <circle cx="24" cy="60" r="3"/><circle cx="78" cy="60" r="3"/>
        <circle cx="132" cy="60" r="3"/><circle cx="186" cy="60" r="3"/>
        <circle cx="240" cy="60" r="3"/><circle cx="294" cy="60" r="3"/>
        <circle cx="24" cy="120" r="3"/><circle cx="78" cy="120" r="3"/>
        <circle cx="132" cy="120" r="3"/><circle cx="186" cy="120" r="3"/>
        <circle cx="240" cy="120" r="3"/><circle cx="294" cy="120" r="3"/>
        <circle cx="24" cy="180" r="3"/><circle cx="78" cy="180" r="3"/>
        <circle cx="132" cy="180" r="3"/><circle cx="186" cy="180" r="3"/>
        <circle cx="240" cy="180" r="3"/><circle cx="294" cy="180" r="3"/>
      </g>
      <g class="mark">
        <path d="M24 64v16M78 64v16M132 64v16M186 64v16M240 64v16M294 64v16"/>
        <path d="M24 124v16M78 124v16M132 124v16M186 124v16M240 124v16M294 124v16"/>
        <path d="M24 184v16M78 184v16M132 184v16M186 184v16M240 184v16M294 184v16"/>
      </g>
    </g>

    <text class="hd" x="382" y="24">e2e — one journey, all the way down</text>
    <g>
      <rect class="step" x="382" y="40" width="190" height="24" rx="3"/>
      <text class="cap" x="392" y="56">sign in</text>
      <rect class="step" x="382" y="80" width="190" height="24" rx="3"/>
      <text class="cap" x="392" y="96">pick a plan</text>
      <rect class="step" x="382" y="120" width="190" height="24" rx="3"/>
      <text class="cap" x="392" y="136">enter payment</text>
      <rect class="step" x="382" y="160" width="190" height="24" rx="3"/>
      <text class="cap" x="392" y="176">confirm</text>
      <g class="down" marker-end="url(#dgm-bd-tip)">
        <path d="M477 64v12"/><path d="M477 104v12"/><path d="M477 144v12"/>
      </g>
    </g>
  </svg>
</figure>
```

---

## A fifth shape, if you need it: before → after

Not one of the four, and usually **not** worth a picture: two labelled boxes with an arrow
between them is a sentence. Name the change in the section heading instead
(`The proposal: seeds → test-data`) and spend the figure budget on a shape the reader
cannot get from prose.

If you do need it, reuse the **pipeline flow** skeleton with two boxes and mark the
`before` box `.broken` (`stroke:var(--broken)`).
