---
description: Author a task's HTML design brief — one a reader gets in one pass, with sections derived from the material, evidence in every row, and its limits stated.
argument-hint: "[task # — omit for the current session's task] [--feature <id>] [--publish]"
allowed-tools: Bash, Read, Write, Edit
---

The user wants a **brief** — an HTML design doc that a competent person who was not in the room understands in one read, without backtracking.

**Use the `brief` skill** (`skills/brief/SKILL.md`) to produce it. It carries the full rule set; do not improvise the shape. In short:

1. **Gather** — read the task's glossary (`task-station glossary --task <n>`), walk the ADO tree from the task's stored PR/story seeds (+ any `--feature`) with the host's ADO tool, and **read the code**: every current-state claim needs a file and line number, and every command you write must exist in the repo.
2. **Derive the sections from the material.** There is no fixed section list — each title answers a question the reader actually has. `/brief` is not one template.
3. **Author the HTML directly** against the shipped stylesheet (`skills/brief/assets/brief.css`, inlined in a `<style>` block) and diagram catalogue (`skills/brief/references/diagrams.md`). One or two plain sentences per section, then a table, diagram, or code block carries the detail. Collapse the how, keep the what and why open. A `Limits` section is mandatory.
4. **Get the path** — `task-station brief path --task <n>` creates the artifact dir, records `brief_path` on the task, and prints the absolute path. Write the HTML there and surface the path to the user.
5. **Self-check** against the list at the end of the skill before writing the file.

`--publish` mints a shareable Claude Artifact from the brief (host-side; publish the body-only copy — the Artifact host supplies its own document wrapper). No org-wiki filing.

The older `brief render --spec <file>` path — emit a brief-spec JSON, let the engine template the frozen layout — is retained for back-compat but is no longer the default.

If no task is attached and no task # is given, say so and stop.
