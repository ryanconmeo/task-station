---
name: ado
description: Read an Azure DevOps work item + its tree (parent Feature, child Stories/Tasks, linked PRs) cheaply — ONE zero-token external call via brain.ado_tree instead of many token-billed ADO MCP round-trips. Use when asked to "read/show/open ADO #N", "what's the state of story N", "pull the work-item tree", or whenever a task needs the shape of a work item.
---

# /ado — read an Azure DevOps work item cheaply

> ⚡ ~85–92% fewer tokens: reads a full ADO work-item tree in ONE zero-token call
> instead of ~4 token-billed ADO-MCP round-trips. (Measured on a real 4-node story tree.)

**Input:** `$ARGUMENTS` = an ADO work-item id (plus optional flags).

To READ an Azure DevOps work item or its tree, run `brain.ado_tree` — it collapses
the parent Feature + child Stories/Tasks + linked PRs into ONE external (zero-token)
call. Use it **instead of** the ADO MCP `wit_*` tools for reads. Only fall back to the
MCP for **writes** (create/update/comment) or when the reader errors.

> **Command form.** The reader is a module in task-station's brain plane; every line
> below runs with the plugin's `lib/` directory on `PYTHONPATH`. Written out in full
> here, abbreviated to `python3 -m brain.ado_tree …` after.

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.ado_tree <id>
```

## Flags

| Invocation | What you get |
|---|---|
| `python3 -m brain.ado_tree <id>` | compact markdown tree — the default; read this first |
| `python3 -m brain.ado_tree <id> --json` | the same tree as structured JSON (feed to tools/parsing) |
| `python3 -m brain.ado_tree <id> --full` | JSON with EVERY field/relation/link/rev per node — full parity with the ADO MCP's `wit_get_work_item`, nothing dropped (implies `--json`) |
| `python3 -m brain.ado_tree <id> --comments` | also fetch each item's comments (one extra call per node) |
| `python3 -m brain.ado_tree <id> --depth N` | child recursion depth (default 3) |
| `python3 -m brain.ado_tree <id> --no-parent` | skip the parent lookup |
| `python3 -m brain.ado_tree <id> --no-clip` | description + acceptance criteria **in full** — see the warning below |
| `python3 -m brain.ado_tree <id> --no-desc` | omit description / acceptance criteria |
| `python3 -m brain.ado_tree <id> --org URL` | the organization url (else config `ado_org` → `$ADO_ORG`) |
| `python3 -m brain.ado_tree <id> --login` | auto-run `az login` (opens a browser) if no credential is found |

Reach for `--full` when you need a specific field the compact tree omits; the default
tree is enough for state/assignee/PR/parent-child questions.

### The compact view CLIPS long text, and you must not design against a clip

`description` and `acceptance_criteria` run to thousands of characters on a real
story. The compact view shows the first 600, and it **declares** it: the clipped text
lands under `<field>_preview` with `<field>_truncated`, `<field>_chars` and
`<field>_criteria` beside it, and **the plain field name is absent**. So a reader
keying on `acceptance_criteria` gets the whole field or nothing — never a confident
fraction. The markdown view prints the same in words: `[33 criteria, 9237 chars, 604
shown — --no-clip for the rest]`.

**Before you plan, design, or build anything a work item specifies, read its criteria
in full** — `--no-clip` (or `--full`, which no longer clips either):

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.ado_tree <id> --json --no-clip
```

This exists because the clip was silent. Story 3614's `acceptance_criteria` came back
as 604 characters — as did four other stories', all exactly 604, because 600 plus
`" ..."` lands on the same length every time. The real field is 9,237 characters and
33 criteria; the clip stopped inside criterion 4. A session took the fragment for the
whole story and spent hours building a mechanism criteria 2, 23, 24 and 28 already
specified better. To reconcile a whole task against its work items at once, use
`heal --probe-ado --task <n>`.

**There is no built-in organization.** With none of `--org`, the config key `ado_org`,
or `$ADO_ORG` set, the run stops and names all three ways in rather than pointing at
someone else's tenant. Set `ado_org` once in your brain config and it is answered for
every later call.

## Auth

Zero secrets in the repo. Auth resolves automatically: `$ADO_PAT` (or
`$AZURE_DEVOPS_EXT_PAT`) if set, else your existing `az login` token. First time on a
machine, run `az login` once (opens a browser) or export `$ADO_PAT` — then re-run.

## Measured savings

### Payload size — measured

Subject: a 4-node User Story tree = root story + 3 child tasks + 3 linked PRs. Byte
counts exact (`wc -c`); tokens ≈ bytes ÷ 4 (heuristic, not a tokenizer).

| Method | Bytes | ~Tokens | vs MCP tree-walk |
|---|---:|---:|---:|
| default (compact md) | 2,379 | ~595 | **−92%** |
| `--json` (structured) | 4,486 | ~1,122 | **−85%** |
| `--full` (complete parity) | 40,568 | ~10,142 | **+35%** ⚠️ |
| MCP single item (`wit_get_work_item`, `$expand=all`) | 14,345 | ~3,586 | one node only |
| MCP naive tree-walk (4 nodes, `$expand=all`) | 30,054 | ~7,514 | baseline |

⚠️ `--full` is LARGER than the MCP walk, not smaller — it returns all nodes' complete
field bags + the parent + the curated wrapper. It exists for guaranteed MCP field-parity
when every field is needed; its only saving there is round-trip collapse, not payload.
The payload win is in the default / `--json` modes.

### Round-trips / billed input — estimated

An MCP tree-walk is ~4 assistant turns (get root → relations → fetch children → resolve
PRs); each turn re-bills the whole context as input. The reader does it in 1 turn.
Modeled (not measured), assuming ~4 turns → 1:

| Context size | MCP walk (~4 turns billed) | Reader (1 turn) | Est. saved |
|---:|---:|---:|---:|
| 40k | ~160k | ~40k | **~120k** |
| 80k | ~320k | ~80k | **~240k** |
| 150k | ~600k | ~150k | **~450k** |

*Payload numbers are measured; these are modeled (realistic range 2–5 turns).*
