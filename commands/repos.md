---
description: Hub repo index — list discovered repos, /repos <term> ranks them for routing a fuzzy task, /repos --refresh rescans. Enrichment is OFF by default.
argument-hint: "[term(s) · 'show' · 'config' · include/exclude/enrich <name> · --refresh [--dry-run] [--no-llm] [--re-summarize] · --detect-roots · --set-roots <p1,p2> · --json]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" repos $ARGUMENTS`

The block above is the live output of the hub **repo index** — a deterministic, regenerable map of the repos under your configured workspace roots. It is built **on demand**, lives next to the task store at `<data_dir>/repos.{md,json}` (with an auto-maintained `repos.config.json` manifest), and is **not** part of `/todo` or `tasks.db` — repos aren't tasks.

**Privacy first: enrichment is OFF by default.** A normal `/repos --refresh` sends **nothing** off-machine — it builds the index entirely offline from the filesystem + a couple of cheap `git` calls + the README's first paragraph. A repo's content reaches the model **only** when you explicitly opt it in with `/repos enrich <name>`.

### First-run setup
If the output above says **"first-run setup"** (no roots configured yet), drive a short, conversational setup — do **not** silently pick roots:
1. The block lists **detected candidate roots** (from `repos --detect-roots`: `~/Workspace`, `~/Workspace-Other`, plus any `~` dir with ≥2 git repos). **Propose these to the user** and ask whether to use them, drop any, or add others.
2. **Reassure them:** *enrichment is OFF by default — listing a repo sends nothing to Claude unless you turn it on per-repo with `/repos enrich <name>`.*
3. Once they confirm, persist with **`/repos --set-roots <p1,p2,...>`** (comma-separated), then run **`/repos --refresh`** to build the index. Normal `/repos` behavior resumes afterward.

### Everyday use
- **No argument** (or `show`) → prints `repos.md`: one short block per repo (only `index:true` repos appear). Rebuilt automatically if missing. **Print it verbatim.**
- **`<term...>`** → ranks repos by token overlap against name/keywords/domain/stack/ado_project/path; prints matches best-first. Use the task's own words (e.g. `/repos billing invoices`).
- **`--refresh [--force]`** → rescans the roots, reconciles the manifest (auto-adds new repos as `index:true, enrich:false`; prunes vanished ones), and rewrites the index. Add **`--quiet`** for a one-line summary (used by the delegation routing step). **`--dry-run`** reports which `enrich:true` repos *would* have content sent — and sends nothing. **`--no-llm`** forces deterministic summaries even for `enrich:true` repos. **`--re-summarize`** regenerates summaries even where one already exists (a plain refresh preserves existing ones).
- **`--json`** → emits the structured list (for tooling / the routing step).

### Include / exclude (no JSON editing)
The manifest (`repos.config.json`) is the single surface listing **every** discovered repo with `index`/`enrich` flags — so you flip flags by name instead of typing paths from memory:
- **`/repos config`** → print the full manifest (every repo + its flags) for at-a-glance review.
- **`/repos include <name>`** / **`/repos exclude <name>`** → set `index` true/false. An `index:false` repo disappears from `repos.md`/`repos.json`.
- **`/repos enrich <name> [on|off]`** (default `on`) → opt a repo **in** to model enrichment. Only `enrich:true` repos ever have content sent, and only when `--refresh` actually runs.
- A repo owner can self-exclude by dropping an empty **`.task-station-ignore`** file at the repo root — it removes the repo from discovery entirely, regardless of the manifest, and travels with the repo.

### How cards are filled
The deterministic fields (name, path, remote, ado_project, status) come from the filesystem + cheap `git` calls; **`stack` is detected by content** — a `git ls-files` extension histogram plus config/tooling signals (Dockerfile, `.github/workflows/`, Flyway/`*.sql` migrations, `*.tf`) unioned with root manifests. **`summary` + `keywords`** are deterministic (README first paragraph) by default; for an `enrich:true` repo they're replaced on `--refresh` by a **fingerprint-gated, best-effort** model call (a cheap headless `claude -p` run) that fires only for new/changed repos and **degrades** to the deterministic summary if the model is unavailable. The enrichment input is **bounded** to repo name + ado_project + stack + README top + a `git ls-files` **name** sketch — file **contents** are never read, and a denylist keeps `.env`/`*.pem`/`*.key`/`secrets*`/`credentials*`/`.npmrc` names out of the prompt. Precedence is **override > model > deterministic-fallback**: hand-authored prose in `<data_dir>/repos.overrides.json` (keyed by repo name) **wins** and **survives** every regeneration.
