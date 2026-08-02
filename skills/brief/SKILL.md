---
name: brief
description: Build a task's house-style HTML one-pager — gather the glossary + ADO tree + a short decision narrative into a structured brief-spec (JSON), then let the core render it deterministically. Use on `/brief` or "make/regenerate the brief for this task". NEVER hand-write HTML or CSS — the core owns the frozen house style.
---

# Brief — the deterministic house-style one-pager

A **brief** explains a task in seconds: one named decision, a forced vocabulary, a before→after, one or two diagrams, one rule, a numbered plan, a hyperlinked ADO tree, and a provenance footer — all in the frozen house style.

**The key move:** you produce a structured **brief-spec (JSON)**; the core (`task-station brief render`) templates the frozen HTML from `lib/brief_template.html`. This renders identically under any host, never drifts, and is trivially regenerable.

## Hard rule — never write HTML or CSS

You emit **only** the brief-spec JSON. The core owns every tag, class, and color. Do **not** put HTML/CSS in any spec field (it is HTML-escaped, not injected) — the one exception is a diagram with `"type": "svg"`, whose `svg` string is passed through verbatim. Glossary terms are highlighted **mechanically** by the core; never hand-tag a `<span>`.

## Flow

1. **Gather.**
   - Read the task's vocabulary: `task-station glossary --task <n>`. The brief-spec references it as `"glossary": "auto"` — the core pulls the live terms at render time.
   - Walk the ADO tree from the task's stored PR/story seeds (and any `--feature <id>`) using the host's ADO tool. For each node, note its type (Feature/Story/PR), id, url (correct `/companyname/<project>/_git/…` or `/_workitems/…` segment), title, a verb (**add**/**change**/**remove**), and a one-line "what it does" worded in glossary terms.
   - Draft the soft content: the decision sentence, the before→after in plain terms, which diagram pattern(s) fit, the one load-bearing rule, and the numbered plan.
2. **Emit** the brief-spec JSON (schema below) to a file.
3. **Render:** `python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" brief render --task <n> --spec <file>` (or pipe the JSON on stdin). The core writes the HTML under the artifacts dir, records `brief_path` on the task, and prints the path. Surface that path to the user.

## Brief-spec schema

All sections are optional **except** `title`, `decision`, `glossary`, and `provenance`.

```json
{
  "title": "LEGACY Key Collation — Strategy",
  "subtitle": "Projectname domains DB · 2026-07-01 · Feature 3049",
  "decision": { "label": "Decision", "body": "One or two plain sentences naming the choice." },
  "transition": {
    "today": { "label": "Today", "name": "Patch-and-Guard", "lines": ["Store — …", "State — …"] },
    "goal":  { "label": "Goal",  "name": "Rebuild + Centralize", "lines": ["Store — …", "State — …"] }
  },
  "diagrams": [
    { "type": "svg", "title": "Two choices → four combinations",
      "intro": "optional lead line", "svg": "<svg …>…</svg>", "caption": "optional" },
    { "type": "matrix", "title": "…", "x_label": "SEARCH", "y_label": "STORAGE",
      "x": ["col0","col1"], "y": ["row0","row1"],
      "quadrants": [ { "pos": "tl|tr|bl|br", "title": "…", "note": "…",
                       "kind": "today|goal|avoid|neutral" } ] },
    { "type": "architecture", "title": "…",
      "today": { "label": "TODAY", "box": "…" }, "goal": { "label": "GOAL", "box": "…" } }
  ],
  "glossary": "auto",
  "one_rule": "The single load-bearing constraint, one or two sentences.",
  "plan": [
    { "state": "done", "title": "Bridge (now).", "body": "what's already true" },
    { "state": "1", "title": "Build the layer", "body": "the big step" }
  ],
  "ado_tree": [
    { "type": "Feature", "id": 3049, "url": "https://dev.azure.com/companyname/Projectname/_workitems/edit/3049",
      "title": "LEGACY Key Collation", "verb": "change", "does": "reshape the store …", "state": "active",
      "children": [
        { "type": "Story", "id": 3133, "url": "…", "title": "Collation Gate", "verb": "add",
          "does": "block bypass", "children": [
            { "type": "PR", "id": 931, "url": "…", "title": "Shipped fix", "verb": "remove",
              "does": "drop the worst inline-collation spots" } ] } ] }
  ],
  "provenance": "Shipped fix: PR 931 (2 approvals). Based on a 2026-07-01 survey of apps/api."
}
```

Field notes:
- **decision.body / transition lines / one_rule / plan.body / ado_tree.does / provenance** — plain prose. Any glossary term appearing in them is highlighted automatically.
- **plan[].state** — `"done"` (or `shipped`/`✓`) renders a done badge; anything else is shown as the step number/label.
- **ado_tree[].verb** — `add` / `change` / `remove` map to the colored pills; other verbs render un-colored.
- **diagrams** — prefer `type: "svg"` when you have exact SVG; `matrix` / `architecture` are parametrized templates for the common shapes.

## --publish

`--publish` mints a shareable **Claude Artifact** from the rendered HTML — a host-side step (the template is self-contained, so it passes the Artifact CSP). It is **not** an org-wiki filing and touches nothing outside the host. The source of truth remains the task (glossary) + ADO (tree); losing the HTML is a non-event — re-run `/brief` to rebuild it.
