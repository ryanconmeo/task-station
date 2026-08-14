# Knowledge-node naming rules

A knowledge node's **slug** is its identity. The property that matters is
**convergence**:

> The same fact, captured twice, by different sessions, from different phrasings,
> lands on the **same node**.

Without it, the node forks into `foo`, `foo-2`, `foo-notes` siblings and what you
know fragments. Convergence is also the precondition for promotion: the
merge-target finder can only work if names are predictable.

The executable half of this document is `lib/brain/naming.py`; the *data* half is
`lib/brain/data/naming-contract.json` (generic, ships) plus the org brain's
`schemas/node-types.json` → `domains.registry` (org-specific, PR-gated). Keeping
the vocabulary as data is what stops the client form and the linter from
disagreeing.

## The shape

```
<domain>[-<subdomain>]-<subject>
```

- **kebab-case** — lowercase, digits, hyphens only. No spaces, camelCase, or `_`.
- **domain** — **closed.** Either a generic area or one of your organisation's
  own domain words. This is the only hard gate.
- **subdomain** — optional and free. `finance-ap-…`, `repo-ci-…`.
- **subject** — at most **3 words**, naming the **thing**, never the verdict.

Examples: `finance-ap-invoice-approval`, `repo-pr-isdraft`,
`ai-mcp-server-scope`, `cloud-sql-contained-users`.

### Why the slug is composed, not derived from a title

It used to be derived from a free-text title. That is deterministic in the
useless sense — the same title always gave the same slug — but the title was
whatever the author invented, so two people capturing one fact invented two
slugs. Composing from a closed domain removes the freedom that caused the fork.

## Where the vocabulary lives

| half | file | ships |
|---|---|---|
| generic areas, shape, detectors | `lib/brain/data/naming-contract.json` | **yes** |
| your organisation's domains + area mapping | org brain `schemas/node-types.json` | **never** |

Three families of generic area:

- **Business** — `strategy` `finance` `customer` `sales` `pricing` `product`
  `service` `procurement` `people` `it` `risk`
- **Technical** — `repo` (code host *and* its pipelines) `cloud` `data` `ai`
- **Toolchain** — `task-station` `brain-station`

Plus opt-in vertical packs; the `industrial` pack adds `production` `inventory`
`quality` `assets` `safety`.

**Vendor names are never registry entries** — they're org words that map into a
generic area. That's what lets the registry ship while your notes keep naming
real systems.

**A fresh install works with no configuration:** every generic area doubles as a
domain until an org registry exists.

### What the domain buys you: `area:` and `plane:`

Every knowledge node carries two frontmatter fields, and **neither is asked for**:

| field | value | derived from |
|---|---|---|
| `area:` | the generic area the domain rolls up to | the slug's domain |
| `plane:` | `knowledge` for `notes/` and `projects/` | the folder |

`brain.search new` stamps both (`brain.notes.knowledge_stamp`). Asking an author
for the area as well as the domain is how the two drift apart — the domain already
determines it. A slug whose domain is unregistered has *no* area, so the write is
**refused** rather than landing a node the org schema rejects; `--area` overrides
the derivation when you need the escape hatch.

`reports/`, `plans/`, `raw/` and `references/` are stamped with neither: they hold
dated artifacts, undigested capture and org pointers, not standing claims.

### Adding a domain

Add a line to the org brain's `schemas/node-types.json` and open a PR — the same
gate org knowledge already goes through, and git rejects duplicate claims via
merge conflict. Anyone may propose; a lead approves. Every domain must map to a
known area, and CI should enforce that, so a filter keeps working for a domain
this toolchain has never heard of.

## Severity — one error, everything else warns

| finding | severity |
|---|---|
| unregistered domain | **error** |
| reserved stem, illegal characters | **error** |
| subject longer than 3 words | warn |
| claim-shaped name | warn |
| generic token | warn |
| date in a note slug | warn |

**Only the domain refuses, deliberately.** A refusal makes an author drop the
fact or invent a name to get past the gate. This project has the evidence: a hard
cap on pinned decisions produced a crowding-out workaround rather than better
pinning. And the refusal must be *helpful* — it names the closest registered
domain.

## Forbidden patterns

- **Claim-shaped names** — a name that states a verdict is unstable by
  construction: correct the claim and the name lies. Detected three ways —
  leading imperative (`never-apply-…`), embedded copula (`…-is-version-gated`),
  negation (`…-not-goal`). Measured at **19%** of a 126-node corpus.
  The imperative belongs in `type: rule`, not in the filename.
- **Dates** — legal in `reports/`, `plans/` and `raw/`, which are dated by
  design. Not in a note slug: the date belongs in `verified:`.
- **Person names** — knowledge is about subjects, not people. Not mechanically
  detectable, so this one is on you.
- **Generic tokens** — `misc`, `stuff`, `tmp`, `untitled`, `draft`, `wip`, `todo`.

Note that **only knowledge nodes carry a domain.** A report is `2026-07-14-lint`
and a plan is `2026-08-02-naming-spec` — dated artifacts, not standing claims.

## Collision rule (the point of all this)

**Same fact ⇒ the same node, updated — never a `-2` sibling.** The lookup is
mandatory; no node is created without a result in hand:

```
python3 -m brain.search find-target "<proposed title or description>"
```

The **action is graded**, and slug text alone never triggers an update:

| description similarity | action | what `brain.search new` does |
|---|---|---|
| ≥ 0.90 | **update** that node — same fact | prints the target and exits **without creating** |
| 0.60 – 0.90 | **choose**, and *record* the choice | stops and requires `--new` (writes `distinct-from:`) or `--update <slug>` (appends to that node, writes `converged-with:`) |
| < 0.60 | **create** — genuinely new | creates |

An exact or name-normalized slug hit is an update regardless of description: the
slug *is* the identity, so hitting it exactly is the match.

`find_target` returns `None` when there is no target — the contract
`brain.promote` and `brain.search` both rely on.

### Why slug text can't drive a merge

Two real nodes scored **0.595** on slug text and were entirely different facts:
one said a store-rebuild reload leg runs on a different service than assumed, the
other said that same reload needs four pre-existing framework tables. Grading
reads the *description*, where they score far apart. **A false merge is worse than
a fork**, because a fork is visible and a bad merge is not.

The grey band earns its place: recording `distinct-from:` makes the *decision*
durable, so the next session doesn't re-litigate it.

## Renaming: the meaning guard

A rename is **proposed, never auto-applied**, when it would drop a word appearing
in 2 or fewer nodes — those are the identifying words.

Rarity was tried as a name *generator* and rejected. With six words tied at
frequency 1 the tie-break went alphabetical and discarded a proper noun; and
cluster terms are frequent *precisely because* they name something real, so
rarity demoted the very words that identify a subject area. It works as a guard.

Also: **a rename moves five things together** — the file, the `name:` field,
every inbound `[[wikilink]]`, the `INDEX.md` line, and hub links.
`brain.heal_lint` verifies the result by finding what broke.
