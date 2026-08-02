# Spec: delegate.py worker progress observability (task #395)

Target repo: `~/Workspace-Other/claude-todo` (task-station). File: `lib/delegate/delegate.py` (+ `tests/test_delegate.py`, `skills/delegating-work/SKILL.md`, `CHANGELOG.md`, manifests, README badge).

## Problem
`delegate.py run` launches a worker via a single blocking `subprocess.run(..., capture_output=True)`, so the hub sees **nothing** until the run returns. Consequences:
1. No live progress — the hub can't tell a stalled-but-alive worker from a dead one (mis-read as dead during the memo 1.79.0 build after an OOM outage).
2. `cmd_list` shows only a registry timestamp — no true liveness.
3. Timeout/failure paths `raise SystemExit` leaving the worktree's in-progress edits **uncommitted** → needs manual hub rescue.

## Goal / acceptance criteria
- **Live, inspectable progress feed.** The hub launches `delegate.py run` as a backgrounded Bash task; `delegate` streams a compact human-readable activity feed to stdout as the worker works, so the down-arrow background-task inspector renders it live (the achievable equivalent of Workflow live-inspection; a CLI cannot register into the internal `/workflows` renderer — state that honestly).
- **True liveness.** New `delegate status` + enriched `delegate list` distinguish `running` (pid alive, even if quiet) from `not running — session resumable` (pid gone). Never label a gone worker "dead/lost" — it's resumable.
- **No orphaned work.** Auto-WIP-commit the worktree on **abnormal exit only** (timeout or non-zero/crash); never push; clean finishes untouched.

## Design

> **Revised per Fable review** — §1/§2 pin the four hard mechanics of the `subprocess.run`→`Popen` switch (timeout, stderr, registry concurrency, run_worker contract), progress moved to **stderr**, liveness hardened, terminal-state write added.

### 1. Streaming execution (core)
Replace blocking `subprocess.run` in `run_worker` with `subprocess.Popen` over:
`claude -p <task> --output-format stream-json --verbose --permission-mode acceptEdits [--model M] [--resume ID | --session-id ID [-n NAME]]`
(`--verbose` is REQUIRED by the CLI for stream-json print mode — verify first, see Assumptions.)

**Process group + I/O plumbing (B1/B2):**
- Launch with `start_new_session=True` so the worker leads its own process group; `claude` spawns bash/npm grandchildren that a bare `proc.kill()` would orphan (they'd keep mutating the worktree AFTER the WIP commit). All kills use `os.killpg(os.getpgid(proc.pid), SIG)`.
- `stdout=PIPE` (streamed, line-by-line), **`stderr` → a temp file** (NOT a second pipe — reading only stdout while stderr fills its ~64KB pipe buffer deadlocks both processes). Read the temp file back after the process ends for the footer/error text.
- **Timeout via watchdog, not per-line deadline**: `readline()` blocks indefinitely on a quiet worker (the exact stalled case), so a per-iteration deadline never fires. Use a `threading.Timer(timeout, _kill_group)` armed before the read loop and cancelled on normal completion; `_kill_group` sends SIGTERM then SIGKILL to the group and sets a `timed_out` flag the loop checks after `stdout` closes.

**Progress feed → STDERR (S1):** stdout today is EXACTLY `result_text` (delegate.py:704) and the hub ingests delegate's full stdout as the relayed result. Emitting the activity feed on stdout would corrupt that contract and bloat tokens with worker verbosity. So emit progress lines to **stderr** (`print(..., file=sys.stderr, flush=True)`) — the background-task inspector shows combined stdout+stderr, so it still renders live, while `stdout == result_text` stays byte-for-byte intact. Per-line `flush=True` (backgrounded = not a tty = block-buffered otherwise).

- Iterate `proc.stdout` line-by-line; each line is one JSON event. For each meaningful event emit ONE compact progress line to stderr:
  - `system`/init → `· worker started (<model>)`
  - assistant message with `tool_use` → `→ <ToolName>: <1-line arg summary>` (dig into `event["message"]["content"]` blocks — stream-json wraps the Anthropic message; truncate arg summary, flatten newlines)
  - assistant text block → `· <first ~80 chars>`
  - user message w/ tool_result carrying `is_error` → `  ✗`
  - `result` (terminal) → captured, NOT printed as progress
- Keep the terminal `result` event's raw JSON line so it can be handed to the EXISTING `_parse_result` (same fields: `result`, `session_id`, `total_cost_usd`, `usage`, `modelUsage`; in stream mode it's the same object + `"type":"result"`). **Zero change to result/cost/model/usage relay or post-run write-backs.** After the stream ends, `cmd_run` prints `result_text` to stdout last, then the stderr footer.
- Robustness: skip non-JSON / partial lines; a missing terminal `result` is a signal (see B4 classification), not a crash. Factor into small pure helpers `_iter_stream_events(lines)` + `_progress_line(event)` — unit-testable without a real subprocess.

**New `run_worker` return contract (B4):** return `(returncode, result_event_json_or_None, stderr_text, timed_out)` instead of a `CompletedProcess`. `cmd_run` is rewritten against this shape at all four consumption sites (resume-fallback :624, result parse :644-645, crash path :647-653). **"Abnormal" is redefined as `timed_out OR returncode != 0 OR result_event is None`** — NOT the current `returncode != 0 and not out` test (:647), which with streaming would mis-classify a worker that emitted events then died as *success*. On resume-failure the fallback still works, accepting that a partial progress feed may have already streamed to stderr before the fresh worker starts (harmless — stderr only).

### 2. Heartbeat in the registry (concurrency-safe)
Extend the entry with: `pid`, `started_ts`, `last_event_ts`, `phase` (last tool/activity), `exit` (`null` while running → `ok|timeout|crash` at end).
- **Locking (B3):** wrap every load→mutate→save in `fcntl.flock` (exclusive) on the registry file, and give `save_reg`'s tmp a **unique per-process suffix** (`REG + ".%d.tmp" % os.getpid()`) so two workers can't clobber one tmp. Today there are 2 writes/run; heartbeats add ~1/sec/worker and `--label` explicitly supports concurrent workers (:773) — without the lock, worker A's stale-snapshot heartbeat save erases worker B's just-pre-registered session id, breaking the "mid-run kill still resumable" guarantee.
- **Heartbeat updates are reload-merge-single-key**, NOT `_save_entry` (which rebuilds the entry wholesale and would need project/seq/label/model re-threaded through the loop). New helper `_touch_heartbeat(key, **fields)`: flock → reload reg → `reg[key].update(fields)` → save. Throttle to ≤ ~1/sec.
- Write `pid` + `started_ts` + `exit=null` at launch (pre-register). **Terminal-state write (S3):** at the end of `cmd_run`, set `pid=null` + `exit=ok|timeout|crash` + final phase — so `status` distinguishes "finished OK" from "interrupted, resumable."
- Heartbeats update `last_event_ts`, **never `ts`** — `cmd_list` sorts by `ts` (:723); leave that semantics alone.

### 3. `delegate status` (new) + enriched `list`
`delegate status [--seq N | --project NAME | --repo PATH] [--label L] [--all]`:
Resolve matching registry entries by **filtering keys on seq/project/label directly** — do NOT reuse `_select_slot` (S6): it needs a full args namespace, applies slot-preference rules, and *raises SystemExit* on a stale main-checkout entry, all wrong for a read-only query. Borrow only its key-format constants. For each entry print:
- **Liveness (hardened, S2):** if `exit` is set → `○ finished (<exit>) <age> ago`. Else if `pid` recorded and alive → `● running (quiet <N>s)`. Else → `○ not running — session resumable`. Legacy entries with no `pid` → `? unknown` (not `○`).
  Liveness check: `os.kill(pid,0)` guarded — `ProcessLookupError`=dead, `PermissionError`(EPERM)=alive; AND confirm identity via `ps -p <pid> -o comm=` containing `claude` (this box OOMs + reboots often → macOS reuses low pids → a stale pid could match an unrelated process and falsely read `running`).
- **Worktree git state** (from `dir`): branch, last commit `<subject> (<age>)`, `<N> dirty / <M> untracked` counts.
- **Phase**: last `phase` + `last_event_ts` age.
- **Resume**: `cd <dir> && claude --resume <sid>`.
`--all` (or bare `status`) iterates every entry.
Enrich `cmd_list`: prepend the same liveness glyph + age per row; legacy no-`pid` rows render `?`. Keep existing dir/model/session/resume lines.

### 4. Auto-WIP-commit on abnormal exit
New helper `_wip_commit(dirpath, reason, task)`:
- `git -C dirpath status --porcelain`; if clean → return None (no-op).
- else `git -C dirpath -c user.name="task-station delegate" -c user.email="delegate@task-station.local" add -A && git ... commit --no-verify -m "wip(delegate): auto-checkpoint on <reason> — <≤80-char task snippet>"`. Always pass the `-c` identity (harmless when config exists); `--no-verify` so a repo pre-commit hook can't kill the checkpoint. Return short sha.
- On commit failure after a successful `add -A`, `git reset` to un-stage (don't leave a half-staged tree). Git failure is swallowed and NEVER masks the original error — but log it to stderr.
- NEVER push.
**Ordering & wiring:** run `_wip_commit` BEFORE `_post_worker_event`/`notify_event`/`SystemExit`, and AFTER the process group is fully killed (B1 — else live grandchildren are still writing the tree). Put the sha in BOTH the `_post_worker_event` feed text (what a resumed hub actually reads) and the `SystemExit` message. Fires on **every abnormal exit** per the B4 definition (timeout reason `timeout`; rc!=0 or missing-result reason `crash`) — including rc!=0-with-output (S5), which is abnormal and gets a WIP commit + `worker_failed` event, not a green finish.

### 5. Hub integration + deploy
- `skills/delegating-work/SKILL.md`: add that write/long delegations should be launched with `run_in_background: true` (so they're down-arrow inspectable) and progress polled with `delegate status`; document the new subcommand + the auto-WIP-commit-on-abnormal-exit behavior + the streaming feed.
- Version bump **1.81.0 → 1.82.0** in ALL of: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (must stay equal — `test_manifest.py` guards this), `CHANGELOG.md` (new `## [1.82.0]` entry), and the `README.md` badge (`version-1.59.0` → `version-1.82.0`, fixing the pre-existing drift).

### 6. Tests (unittest + TASK_STATION_HOME isolation, TDD)
Extend `tests/test_delegate.py`:
- `_progress_line`/`_iter_stream_events`: feed synthetic NDJSON (system/assistant-tool_use/assistant-text/user-tool_result-error/result) → assert emitted progress lines dig into `message.content` correctly + terminal result event round-trips through `_parse_result` unchanged (session_id/cost/usage/model). Include an **error-subtype** result event (e.g. `subtype:"error_max_turns"`, no `result` field) → assert graceful handling (relayed verbatim, classified abnormal → missing/None result path).
- Classification: assert the B4 rule — `result_event is None` (stream died after emitting events) → abnormal (was the pre-existing `not out` bug).
- Heartbeat + locking: after a simulated stream, entry has pid/started_ts/last_event_ts/phase; a `_touch_heartbeat` interleaved with a second entry's pre-register does NOT erase the second's session_id (B3 regression test); terminal write sets `pid=null` + `exit`.
- `status` liveness (S2): monkeypatch `os.kill`/`ps` to simulate pid-alive+claude-comm vs `ProcessLookupError` vs EPERM vs pid-alive-but-not-claude → assert `running` / `not running — resumable` / `finished (<exit>)` / legacy `? unknown` strings; temp git worktree for the git-state line.
- Auto-WIP-commit (S4): temp git repo — dirty tree → wip commit created (on timeout AND crash reason), `--no-verify` honored (a failing hook doesn't block), NOT created on a clean tree, no push attempted, commit-failure leaves tree un-staged (reset), sha appears in the feed-event text.
- `list` glyph rendering incl. legacy no-`pid` → `?`.
- **Regression guard:** existing test_delegate.py cases for registry slotting, seq inheritance, resume, notifications, and post-run write-backs must still pass unchanged (mock the new `run_worker` return shape).
Build TDD: red test → implement → green. Run the full suite green before done.

## Assumptions
A. **VERIFIED (probe 2026-07-11, `claude -p "..." --output-format stream-json --verbose --model sonnet`).** Concrete shapes — build against THESE:
   - Terminal event `{"type":"result", ...}` carries `result` (str), `session_id`, `total_cost_usd`, `usage` (dict: input_tokens/output_tokens/cache_*), `modelUsage` (dict keyed by model id), `subtype` (`success` | error subtypes), `is_error` (bool), `stop_reason`, `permission_denials`. → `_parse_result` reuse is sound. `modelUsage` IS present → `_pick_model` works.
   - `assistant` events: `event["message"]["content"]` is a LIST of blocks; block `type` ∈ `thinking` | `text` | `tool_use` (tool_use block has `name` + `input`). This is where `_progress_line` digs.
   - `user` events (tool results): `message.content` blocks of type `tool_result` (with `is_error` on failures) — not seen in the trivial probe (no tools) but this is the documented shape.
   - **SKIP (noise):** every `system` event (subtypes seen: `hook_started`, `hook_response`, `init`, `thinking_tokens`, `commands_changed`) — these have NO `message.content`; plus a top-level `rate_limit_event` type. `_progress_line` must WHITELIST meaningful events (assistant tool_use/text, user tool_result-error) and return None for everything else, NOT print every line (a trivial task emitted 16 events, 13 of them skippable noise).
   - Error subtypes (`subtype != "success"` / `is_error=true`): `result` may hold an error string or be absent → classify abnormal (see B4), relay whatever `_parse_result` yields.
B. Backgrounded-Bash **combined stdout+stderr** is what the down-arrow inspector renders live (progress is on stderr per S1; informs hub-usage guidance, not code). If not, the feed still helps any observer; document accurately. Suggest `PYTHONUNBUFFERED=1` in the SKILL.md guidance.

## Out of scope (YAGNI)
- No watcher-thread fallback (streaming covers it).
- No push / PR automation from within delegate (hub finalizes).
- No `/workflows`-renderer integration (not a public surface for CLIs).
- No auto-WIP on clean finish (user chose abnormal-only).

## Constraints
- Worker does this in a `claude-todo` worktree off `main` (GitHub repo; branches off main, `.claude` gitignored). TDD. Author-only — hub finalizes git/version/merge/deploy.
- Preserve every existing behavior: registry slotting (`_select_slot`), seq inheritance, worktree resolution, notifications, add-cost/add-project/status write-backs, resume semantics.
