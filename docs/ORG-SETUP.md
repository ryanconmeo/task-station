# `task-station org-setup` — the org-setup wizard

**What it does:** reads an organisation's own vocabulary off four systems it
already runs, asks the six things no system can tell it, and writes a
schema-valid **OrgProfile**. That profile is what makes every org-specific part
of this toolchain — the naming registry's domains, the context-injection
keywords, the tier labels, the forge that gates promotion — belong to *your*
company rather than to whoever wrote the tool.

```sh
task-station org-setup --scan-bundle bundle.json --answers answers.json --out config.json
python3 -m brain.init_home --profile config.json      # then adopt it
```

Omit `--answers` and the wizard asks the six questions. Add `--dry-run` to
validate and print without writing.

---

## The split the design turns on

An org's vocabulary is already written down in four places. Its *decisions* are
written down nowhere.

| | |
|---|---|
| **Discoverable — the words** | schema names, group names, repo names, page names. Four scans read them. |
| **Undiscoverable — the choices** | six answers. No scan can find out which repo *should* hold the org brain, or who *may* approve a promotion. |

Guessing an undiscoverable answer would be worse than asking, because a wrong
guess is invisible.

## The four read-only scans

| scan | reads | yields |
|---|---|---|
| **database** | `INFORMATION_SCHEMA.SCHEMATA` names + migration-file **header comments** | business domains |
| **directory** | Entra/AD **group display names only** | function words, departments, role tiers |
| **forge** | repo and project names | system domains |
| **wiki** | the **leading segment** of current page names | the naming habits already in use |

**Read-only is structural, not a promise.** This module opens no connection and
issues no write. The database side is one module constant —
`INFORMATION_SCHEMA_SQL` — with no interpolation and no parameters, so there is
no argument that could turn it into a write. The other three take data a caller
already fetched.

**Two signals, weighted differently, in three of the four scans.** A schema name,
a project name and a wiki leading segment are *deliberate acts of partitioning*,
so one occurrence is evidence. A migration header word and a repo word are prose,
so they count only when they recur — otherwise every adjective a developer ever
typed becomes a business domain.

### The directory scan is read-*narrow*

A directory holds people, so the requirement is stronger: the scan must be
**incapable** of reading a user object, not merely decline to.

- The only door in is `screen_group_entries()`. It projects every entry down to a
  single display-name string, and **raises** `DirectoryScopeError` on any entry
  carrying a user attribute (`userPrincipalName`, `mail`, `employeeId`, …) or
  declaring a Graph user type. The type check earns its place on its own: an
  entry can declare itself a user and carry no user attribute at all, and the
  screen must never depend on an attribute being *present* to notice it is
  holding a person's record.
- Past that function the data is a list of strings. `scan_directory()` accepts
  `str` and raises `TypeError` on anything richer.

So there is no code path from a user **object** to a scan result: the type at the
boundary is `str`, and a user object cannot survive the crossing even if a caller
hands one in by mistake. A refusal at the door, rather than a filter downstream,
is the difference — a filter is a thing a later change can edit out, and a silent
drop trains callers to keep handing over people's records.

### Where that guarantee stops — bare strings

**It covers objects.** A directory section may also carry **bare strings**, and a
bare string is not inspectable: there is no attribute to refuse and no type to
check. So a person's name typed into one — a distribution list named after
somebody, in a hand-assembled bundle — reaches the emitted profile, and its words
land in `vocabulary.departments`.

**No heuristic is applied, on purpose.** A person's name and a department's name
are the same shape, and a guess wrong in either direction is worse than the gap:
a wrongly-refused group silently loses vocabulary, and a wrongly-admitted person
is the very leak the guess was supposed to prevent.

**What is guaranteed instead is that a bare string never passes silently.** Every
one is counted, and the count travels to two places a person actually looks:

| where | what it says |
|---|---|
| `provenance.directory.unscreened_entries` | in the emitted profile — the artifact an org-brain PR reviewer reads |
| the wizard's printed summary | `… 6 of 8 entries bypassed the object screen`, plus a NOTE naming the consequence |

The count is **required by the schema, and required even when zero**. A profile
that does not state it fails validation, because an unstated count is exactly the
silence this closes. `scan_directory()` defaults the tally to `None`, not `0`, for
the same reason: a zero default would let a caller who skipped the screen entirely
claim everything was screened. **Zero is a claim somebody made; missing is nobody
having looked.**

To have entries screened, supply them as objects — `{"displayName": "…"}` is
enough.

## The six answers

| # | answer | why no scan can find it |
|---|---|---|
| 1 | `org_slug` | the org's short identity — a choice, not a fact |
| 2 | `org_brain_repo` | which repo *should* hold the shared org brain |
| 3 | `mirror_template` | the per-person mirror **naming template** |
| 4 | `forge_kind` + `forge_url` (+ `forge_project` on ADO) | which forge gates promotion |
| 5 | `vertical_pack` | which opt-in vocabulary pack applies |
| 6 | `promotion_approvers` | who may approve an org-brain promotion PR |

### Answer 3 is a template, and that is enforced

**Binding ruling, 2026-08-15:** the per-person mirror name is a **template
resolved from the host identity at init**, never a literal an administrator
types. `resolve_mirror_template()` does the resolving; the schema requires the
template to contain `{username}`.

That second half is the point. A ruling that lives only in prose is a ruling that
gets typed around, so a literal name **fails validation and never reaches a
profile**.

## Validate, then write

`write_profile()` validates *before* it opens the file, and raises
`ProfileInvalid` rather than writing anything.

The rule behind the ordering: **a config the platform refuses to parse does not
fall back to default rules — it means no rules at all.** A half-written profile
on disk reads as configured, so an invalid one must never reach disk. Every
finding is reported at once, too: a leader fixing one field per round trip re-runs
four scans each time.

The schema is data (`lib/brain/data/org-profile-schema.json`), for the same
reason the naming contract is: the wizard, the validator and the docs cannot
disagree about what a profile is.

## Domains map to an area, or they are listed — never guessed

Every domain in the naming registry must map to a generic **area**, and the
wizard maps them exactly two ways: a word that *is* an area maps to itself, and a
word a shipped generic-English hint recognises (`invoice` → `finance`) maps to
the hinted area.

Anything else lands in `vocabulary.unmapped_domains` for a person to assign.
A wrong area is not a visible error — it is a filter that quietly stops matching
— and the org half of the registry is PR-gated precisely so that a human assigns
it.

## Running with no live credentials

The four scans consume a **scan bundle**: the already-fetched, read-only inputs,
as one JSON file. Whoever has access produces it once; the wizard — and every
test — reads it. `tests/fixtures/fake-org/` holds a complete worked example for a
wholly invented organisation, and `tests/brain/test_org_setup.py` runs the wizard
end to end against it with no credential, network, or database.

```jsonc
{
  "database":  {"schema_names": [...], "migration_headers": [{"file": ..., "text": ...}]},
  "directory": {"groups": ["SG-Billing-Owners", {"@odata.type": "...group", "displayName": "..."}]},
  "forge":     {"projects": [...], "repos": [...]},
  "wiki":      {"pages": [...]}
}
```

A bundle carrying a user object is refused by the same door a live response would
hit.

## What the profile is worth downstream

The emitted artifact is directly consumable by `brain-init --profile`: its
`org_label`, `labels`, `keywords` and `forge` keys are the shape
`brain.init_home._apply_profile` reads. The answer names and the profile keys
differ on purpose — `forge_url`/`org_brain_repo` is what a human is asked,
`forge.org`/`forge.repo` is what the consumer reads — and the mapping lives in
exactly one place, `build_profile()`, so no value is written twice.

## Where things live

| | |
|---|---|
| the wizard | `lib/brain/org_setup.py` |
| the schema (data) | `lib/brain/data/org-profile-schema.json` |
| the naming contract it reads areas from | `lib/brain/data/naming-contract.json` |
| the fake-org fixture + golden profile | `tests/fixtures/fake-org/` |
| the tests | `tests/brain/test_org_setup.py` |
| the CLI routing seam | `lib/board/cli.py` → `board.cmds.manage.cmd_org_setup` |
