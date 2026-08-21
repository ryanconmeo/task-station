# Re-deriving the patch surface — the two-scan procedure

**Read this before re-deriving the routed set.** It exists because the set was once derived
with one scan when there are two, and the missing scan cost two names.

## What the patch surface is

`lib/task-station.py` is the **facade**. The seams under `lib/board/` are star-imported into
it, so every historical `ts.<something>` still resolves. But a test that **patches** a name —
assigns to it on the engine module — only rebinds it *on the facade*. A split seam that read
that name as its own module global would keep the unpatched value, and the test would pass
while testing nothing.

So every patched name is read late, through `board._shared.g("NAME")`, against the facade's
live namespace. The set of names that needs this treatment is the **patch surface**, and
`tests/test_patch_surface.py` is the structural guard that keeps it exact.

## The two scans

There are **two** forms a test uses to patch the facade, and each scan is blind to the
other's form. Run **both**. Running only the first is the mistake this document exists to
prevent.

### Scan 1 — direct assignment (the "§3 regex" form)

```sh
rg -o '\bts\.[A-Za-z_]+ *=' tests | sort -u
```

The guard compiles exactly this pattern:

```
\bts\.([A-Za-z_]+) *=
```

If that line and `_PATCH_RE` in `tests/test_patch_surface.py` ever disagree, the guard fails
— they are pinned to each other on purpose (`test_the_regex_it_prints_is_the_regex_THE_GUARD_RUNS`).

### Scan 2 — `setattr` (which scan 1 **cannot see** at all)

```sh
rg -n 'setattr\(\s*ts\b' tests
```

Three sub-forms, and only the first is obvious:

```
setattr(ts, "<name>", spy)     # a string literal
setattr(ts, attr, spy)         # through a function parameter
```

The parameter form is the one that bit. `tests/test_transcript_cache.py` has a helper whose
`attr` parameter *defaults* to one name and is *passed* another at its call sites. Scan 1
cannot see any of it — no `ts.` appears — which is exactly how two names were missed when
the routed set was first derived.

**Scope limit, deliberate:** the guard resolves **one hop** — a `setattr(ts, <param>, …)`
back to that parameter's string default and to the string literals its call sites pass.
Deeper indirection is **out of scope**. If a future test needs it, the guard is meant to fail
loudly rather than quietly under-report; do not widen the resolver to make a red go away.

## The procedure

1. Run **scan 1**. Take the distinct names.
2. Run **scan 2**. Take the distinct names, resolving the one-hop parameter form.
3. Union them. That is the patch surface.
4. Compare against `ROUTED` in `tests/test_patch_surface.py`. Any difference is either a name
   to route or a name to drop — decide, then edit `ROUTED`.
5. Run `python3 -m unittest tests.test_patch_surface`. Assertion 1 pins the set; assertion 2
   proves no seam under `lib/board/` reads a routed name bare.

## The numbers, as directions

Measured 2026-08-21 on `main` at 3.17.0:

| measurement | value |
|---|---|
| scan 1 raw sites | 580 |
| scan 1 distinct names | 477 |
| scan 2 sites on `ts` | 7 |
| `ROUTED` set size | 23 |
| seams scanned (`SEAM_FILES`) | 16 |

**These are a FLOOR and a snapshot, never an assertion.** A release that patches another name
raises them, and a condition written as `test COUNT = 580` would go red on the next
legitimate release — the failure mode `tools/checker-template.sh` exists to prevent. The
count that must be exact is `ROUTED`, and `tests/test_patch_surface.py` is what keeps it
exact; it should never shrink silently. Re-measure **both** scans at the start of each phase
rather than trusting the table above.
