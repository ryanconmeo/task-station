---
description: Render a house-style HTML one-pager for a task — decision, vocabulary, before→after, ADO tree, provenance.
argument-hint: "[task # — omit for the current session's task] [--feature <id>] [--publish]"
allowed-tools: Bash, Read, Write, Edit
---

The user wants a **brief** — a deterministically-rendered HTML one-pager in the frozen house style, built from the task's glossary + a hyperlinked ADO tree + a short decision narrative.

**Use the `brief` skill** (`skills/brief/SKILL.md`) to produce it. In short:

1. **Gather** — read the task's glossary (`task-station glossary --task <n>`), walk the ADO tree from the task's stored PR/story seeds (+ any `--feature`) with the host's ADO tool, and draft the soft content (one decision sentence, plain-terms before→after, chosen diagram(s), a one-line add/change/remove verb per ADO node).
2. **Emit** a **brief-spec (JSON)** — the structured intermediate documented in the skill. **Never hand-write HTML or CSS**; the core owns the house style.
3. **Render** — write the spec to a file and run `task-station brief render --task <n> --spec <file>` (or pipe it on stdin). The engine templates the frozen HTML, writes it under the artifacts dir, records `brief_path` on the task, and prints the path. Surface that path to the user.

`--publish` mints a shareable Claude Artifact from the rendered HTML (host-side; the template is self-contained so it passes CSP). No org-wiki filing.

If no task is attached and no task # is given, say so and stop.
