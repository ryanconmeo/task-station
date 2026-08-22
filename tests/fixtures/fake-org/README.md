# `fake-org` — the org-setup wizard's end-to-end fixture

A wholly invented organisation ("Northwind Logistics"), here so `task-station
org-setup` can be run end to end **with no live credentials and no access to
anyone's real systems**. Nothing in this directory names a real company, person,
product or repository.

| file | what it is |
|---|---|
| `scan-bundle.json` | the four sections of already-fetched, READ-ONLY input the four scans consume — schema names + migration headers, group display names, repo/project names, wiki page names |
| `answers.json` | the six answers a leader must supply, because no scan can discover them |
| `config.json` | the emitted OrgProfile — a **golden**: `tests/brain/test_org_setup.py` asserts the wizard reproduces it byte for byte |

**Why the emitted profile is committed.** It is the artifact the whole task is
about, and a golden makes two things checkable that a temp-dir run cannot: that
the output does not drift silently, and that it carries no fingerprint of the
organisation this toolchain grew up in. A scan over a file that is generated and
thrown away proves nothing, because a missing file scans clean.

**The directory section is groups only.** `scan-bundle.json` deliberately mixes
plain display-name strings with Graph-shaped group objects, so the fixture
exercises the one door into the directory scan
(`read_group_display_names`). There is no user object here and there cannot be
one: the reader raises on any entry carrying a user attribute.

Regenerate the golden after an intended change:

```
python3 -m brain.org_setup \
  --scan-bundle tests/fixtures/fake-org/scan-bundle.json \
  --answers     tests/fixtures/fake-org/answers.json \
  --out         tests/fixtures/fake-org/config.json
```
