---
description: Checkpoint the current task for a seamless resume — amend what the gap report says is missing or stale.
argument-hint: ""
allowed-tools: Bash
disable-model-invocation: true
---

```!
TS_RC=0
TS_OUT="$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" render --arg save --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}" 2>&1)" || TS_RC=$?
[ -n "$TS_OUT" ] && printf '%s\n' "$TS_OUT"
[ "$TS_RC" -eq 0 ] || printf '%s\n' "[task-station] THE SKILL WAS NOT INVOKED. /save exited $TS_RC without producing the save block; nothing was read and nothing was changed. Any text above this line is the failure, not the save block."
:
```

> **If the block above is not the command's own output** — it is empty, it is a raw shell error, or it carries `THE SKILL WAS NOT INVOKED` — then `/save` **DID NOT RUN**. Say exactly that to the user in one line, show the failure verbatim, and stop. Do not reconstruct the output by hand, and do not describe anything as done.

The block above (`[SAVE]`) means the user wants to **checkpoint the current task for a seamless resume**. If no task is attached, relay the block verbatim and do nothing else.

**A save is an AMENDMENT, not a rewrite, and the block tells you exactly what to amend.** It does not reprint the digest — you have been working this task all along, so you already have it; echoing it back once cost 71,516 characters of which 99.7% was the dump. What you do not have is the list of what is *missing*. That list is the **GAP REPORT**. Work these seven steps in order.

### 1. Read the GAP REPORT — not the digest

It names the **EMPTY** slots (goal · state · summary · steps · decisions · links), the **STALE** ones, what has landed **since the last full checkpoint**, and what the digest currently **costs** a fresh session to load. A few hundred tokens. If you genuinely lack the task's state — a fresh session, a post-compaction one — `/todo save --verbose` dumps the whole digest; otherwise never ask for it.

### 2. Fill ONLY what is missing or stale

Do **not** rewrite a slot the report did not name. An accurate goal, an accurate checklist and an accurate decision set are already doing their job, and rewriting them burns tokens to produce the same text with new mistakes in it.

### 3. Write the summary as PRESENT TRUTH

`--summary` **REPLACES** the summary wholesale, so what you write must stand alone as the current state — files and **PATHS**, the branch / worktree / environment and its quirks, the exact build-test-run commands, the gotchas ("watch out for X", "never do Y"), the open questions, and the user's most recent intent in their words. Lean. **Never a history dump** — the why-trail belongs in decisions and `--log`, retrievable with `/todo <n> history`.

The summary you replace is **not** destroyed: it is preserved append-only, and `update --task <n> --restore-summary` puts it back (`--restore-summary <k>` for an older version). So a thin summary is recoverable — but write a good one anyway.

### 4. Record this session's new choices

One `--decision` per choice, with its **why**, including the approaches **tried and rejected** so the resume never re-explores a dead end. When a new decision replaces an earlier one, add it with `--supersedes <n>` (the number from `/todo <n> history`, repeatable) — otherwise the refuted one goes on briefing every future session. Use `--pin` for a rule the rest of the work must obey; a pin is **reading order** (pinned decisions sort first, marked ★), not survival — nothing current is ever hidden.

### 5. Append exactly ONE dated `--log` milestone

One line. It is the append-only history trail and it does not load on a normal resume.

### 6. Then the COLD-READ CHECK — mechanical, not a feeling

Two conditions are decidable, so do not merely assert them: **every named slot is non-empty** and **`state` begins with `NEXT:`**. The stamping `update` reports any that still fail; `/todo save --check` re-runs the same check on demand. Patch each failure with another `update`. Then the judgement half: re-read the digest as if you have no memory of this conversation, and patch anything ambiguous or assumed.

### 7. Confirm in one line

What you filled and what you left alone. Do not re-render the digest or the board.

---

**The stamp belongs to the write.** Emitting this block does **not** record a checkpoint — it only records that one was started. `last_full_save_ts` is written by the `update` that carries a `--summary` **and** a `--state` together, because that pair *is* the checkpoint. No flag declares it, so nobody can claim one without capturing it.

**One call is the target.** The block prints a template with every flag filled in — `--goal`, `--state`, `--step-add` / `--step-done`, `--decision`, `--log`, `--pr` / `--story`, `--summary` — but drop the flags step 2 said to leave alone. The summary and the state must travel in the same call, or nothing stamps.

Do **NOT** pin a session or open / resume anything: this only captures the digest. If a detail is ever missing afterwards, the trail is recoverable — `/todo <n> history` (decisions, milestones, and every preserved summary version) or `/todo <n> -s` (this session's full transcript).
