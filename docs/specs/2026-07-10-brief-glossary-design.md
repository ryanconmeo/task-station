# Design: `/glossary` + `/brief` — task-scoped vocabulary & house-style briefs

> **RECONCILIATION STATUS (2026-07-13, task 394 → 387).** Original 394 design; predates 387's pending folder-structure ruling (memo `ca3d1ba7`, "supersedes the ~/companyname umbrella"). Known-stale — do NOT execute verbatim: **artifacts_root** must DERIVE from the `data_dir`/`TASK_STATION_HOME` seam (per R10 + `ca3d1ba7`), not the hardcoded `~/task-station/artifacts` shown below; **version** references are stale (repo is now 1.82.2). Full consistency checklist + conflicts in 394→387 memos `0c52ad69` / `b6b810d6` / `66dc4b68` / `1d632e00` on task #387. This file is the high-level requirements; the companion `2026-07-10-brief-glossary-plan.md` is the code-level plan.

**Date:** 2026-07-10
**Repo:** `claude-todo` (task-station; personal GitHub, branches off `main`)
**Task:** originated on task-station #394 (closed) → folded into #387
**Style source of truth:** `collation-decision-brief.html` (frozen house style)

## Goal

Two new task-station capabilities:

1. **`/glossary`** — a per-task **canonical vocabulary**, seeded early, auto-injected into every attached session, and grown by proactive capture. Every plan, ADO item, and conversation reuses the same terms.
2. **`/brief`** — a **deterministically-rendered** HTML one-pager in the frozen house style, built from the task's glossary + a hyperlinked ADO tree + a short decision narrative. Regenerable at any time.

Anyone should understand a brief in seconds: one named decision, a forced vocabulary, before→after, one or two hand-drawn diagrams, one rule, a numbered plan, a hyperlinked ADO tree, a provenance footer.

## Non-goals

- **No private brain references anywhere in task-station.** Integration is one-way (private brain → task-station): private brain reads task-station's `export` JSON + artifacts dir. Any "file the decision as a linking note" behavior is a *company-brain* feature, out of scope here.
- **Not a full LLM-agnostic migration of task-station.** This feature is built *portable by construction* (logic in core, thin adapters) and ships **Claude adapters now**; Codex adapters are a documented extension point. The broader migration is 387's scope.
- No ORM/DB changes; glossary + ADO refs live on the existing task JSON record.

## Filing policy (the standing rule this feature embodies)

Three content types, each with a personal and a team home. Audience follows the **task's project**, not who typed it.

| Type | What it is | Personal home | Team home | Promote |
|---|---|---|---|---|
| Knowledge | durable facts/decisions (markdown) | private brain `notes/` | org brain wiki | `/brain-promote` |
| **Artifact** | rendered deliverable (HTML) | **task-station artifacts dir** | shareable link | `/brief --publish` |
| Code | source | GitHub | ADO | PR |

**Artifacts are build outputs, not documents.** Source of truth = task (glossary) + ADO (tree). The HTML is a render; losing it is a non-event — `/brief` rebuilds it. This is why artifacts live in neither the vault (knowledge) nor a repo (versioned source).

## Architecture — portable core + thin adapters

| Layer | Lives in | Portable |
|---|---|---|
| glossary storage/CRUD, injection-content generation, brief-spec → HTML render | `lib/task-station.py` (+ `lib/brief_template.html`) | ✅ host/LLM-agnostic |
| glossary auto-injection wiring | Claude: UserPromptSubmit hook · Codex: its own channel | thin adapter |
| `/glossary`, `/brief` invocation + ADO fetch | Claude: command `.md` + ADO MCP · Codex: equivalent | thin adapter |

**Key move:** the house style renders **deterministically in Python**, not by a model writing HTML. The model produces a structured **brief-spec (JSON)**; the core templates the frozen HTML. → identical under Claude or Codex, no style drift, unit-testable, trivially regenerable.

## Data model (on the task JSON record)

**Glossary term** (`task.glossary: []`):
```json
{ "name": "Binary-Default (BIN2) Store", "layer": "db", "state": "target",
  "def": "All text case-sensitive by default; the error class disappears." }
```
- `name` — canonical, unique per task (case-insensitive key).
- `layer` — short where-tag (`db`/`app`/`CI`/`infra`/…) → left half of the pill.
- `state` — `today`|`target`|`shipped`|`planned` (free) → right half + color (today=neutral, target/planned=accent, shipped=good).
- `def` — one plain sentence.

**ADO refs** — reuse task-station's existing `--pr` / `--story` URL storage as tree seeds; add optional `--feature <id|url>` root. The tree is *expanded live at brief time* by the host's ADO tool, not hand-maintained.

**Brief-spec (JSON)** — the structured intermediate the model fills; the core renders it:
```json
{
  "title": "…", "subtitle": "…",
  "decision": { "label": "Decision", "body": "…" },
  "transition": { "today": {…}, "goal": {…} },
  "diagrams": [ { "type": "matrix|architecture|svg", … } ],
  "glossary": "auto",
  "one_rule": "…",
  "plan": [ { "state": "done|1", "title": "…", "body": "…" } ],
  "ado_tree": [ { "type":"Feature", "id":…, "url":…, "title":…,
                  "verb":"add|change|remove", "does":"…", "state":"…",
                  "children":[ … ] } ],
  "provenance": "…"
}
```

## `/glossary` (deterministic, core)

```
/glossary                       list current task's terms (pills)
/glossary add "<name>" <layer> <state> "<def>"
/glossary edit "<name>" [--layer|--state|--def …]
/glossary rm "<name>"
/glossary <task#>               list another task's terms
```
- Backed by `task-station glossary …` in the core; `commands/glossary.md` is a thin wrapper.
- **Proactive capture:** the model runs `glossary add` itself when it coins a canonical concept and you confirm — driven purely by the injected instruction, no extra code.

**Auto-injection.** Core exposes `task-station glossary-context --task <id>` → a compact block appended by the existing prompt-context render when the attached task has terms:
```
GLOSSARY (task 336) — use these canonical terms verbatim in plans, ADO items, and dialogue:
• Binary-Default (BIN2) Store [db·target] — All text case-sensitive by default…
• …
New canonical concept coined? Add it:  task-station glossary add "<name>" <layer> <state> "<def>"
```
Claude wires this into UserPromptSubmit; Codex adapter emits the same block through its channel.

## `/brief` (skill + core render)

`/brief [task#] [--feature <id>] [--publish]`

**Flow:**
1. **Gather** (model, `skills/brief/SKILL.md`): read the task's glossary; walk the ADO tree from stored PR/story seeds (+ `--feature`) via the host's ADO tool; draft the soft content (decision sentence, plain-terms bullets, chosen diagram pattern(s), one-liner + add/change/remove verb per ADO node).
2. **Emit** a brief-spec JSON.
3. **Render** (core): `task-station brief render <spec> --task <id>` templates the frozen HTML from `lib/brief_template.html`, writes it to the artifacts path, prints the path (+ published URL if `--publish`).

**Frozen section skeleton:** Decision banner → Where-we-are→where-we're-going cards → The vocabulary (from glossary) → diagram(s) → The one rule → The plan (numbered, done/pending badges) → **ADO structure** → Provenance footer. Sections optional except decision + vocabulary + provenance.

**ADO structure section:** nested hyperlinked **Feature → Story → PR** tree. Each node: a verb badge (**add**/**change**/**remove**), the item title (linked, correct `/companyname/Projectname/_git/…` project segment), and a one-line "what it does" worded in glossary terms.

**Deterministic term highlighting:** the core auto-wraps any glossary `name` it finds in the soft prose / ADO one-liners in the `.term` style — consistency enforced mechanically, the model never hand-tags.

**Diagram patterns:** core ships 2 parametrized SVG templates (2×2 `matrix`, before/after `architecture`) reproducing the source file's hand-drawn look; `type:"svg"` allows raw inline SVG passthrough.

**`--publish`:** mints a shareable Claude Artifact (template self-contained → passes CSP). No org-wiki filing; no private brain references.

## Config & output path

- Config key `artifacts_root`, env `TASK_STATION_ARTIFACTS_ROOT`. **Default must derive from `data_dir` seam** (see reconciliation banner — NOT hardcoded `~/task-station/artifacts`).
- Path: `<artifacts_root>/<project>/<task-slug>/brief.html` — `<project>` from category/repo (slugified), `<task-slug>` from `<seq>-<title-slug>`.

## Testing

- Golden-file: render the collation brief-spec → byte-diff against a committed golden HTML.
- Glossary CRUD unit tests (add/edit/rm, unique-name, pill/color mapping).
- Term auto-highlight test; path-derivation test; injection-block test.
