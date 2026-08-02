# task-station "memo" correspondence (v1)

## SPEC

### Purpose
Let any Claude session hand a fact/decision to a task's working session(s) without
copy-paste: `memo send` posts correspondence onto a target task (attached or not); every
session attached to that task passively sees "memo awaiting your ack" on its next turn
(existing hook injection); each session **explicitly acks**, signed with its session id,
on a **shared, visible ack ledger** — so multiple sessions on one task never
double-implement. A memo is NOT a decision; acking MAY optionally promote it to one.

### Data model

**Foundation B — stable event ids** (`add_event`, `lib/task-station.py:983`). Every event
gains a stable id, and `add_event` returns the appended event:
```
ev = {"ts": _now(), "kind": kind, "sid": session,
      "id": uuid.uuid4().hex,                      # NEW — 32-char hex
      "text": (text or "")[:EVENT_TEXT_MAX]}
...
return ev                                           # NEW (was None; no caller reads it today)
```
Display form `id[:8]`. Back-compat: all readers use `.get("id")`; legacy events without
`id`/`sid` render exactly as before. **Delete the dead WS-merge shim** copy of add_event
at ~3139–3150 (behind `if "add_event" not in globals():` — provably unreachable since the
canonical def at 983 precedes it).

**Foundation A — reliable attribution.** Thread `session` end-to-end so authored events
carry `sid`:
- `append_decision(task, text, session=None)` (3601) → `add_event(..., session)` (3608).
- `append_history(task, text, session=None)` (3612) → `add_event(..., session)` (3624);
  caller `cmd_post_compact` (3002) passes `a.session` (3029).
- `set_status(task, status, note=None, session=None)` (1734) → event at 1752 gets sid;
  `promote_active(task, note=None, session=None)` (1756) forwards it; the `status` CLI
  parser (~7070) gains `--session`, `cmd_status` (5462) forwards it.
- `touch` (1719) gains `register=True`. When `register=False`, skip the
  sessions[]/session_meta registration block (1723–1728) but still attribute the log
  event. `_update_one` (5558) hoists `session = getattr(a, "session", None)` above the
  --decision/--log loops (5605–5612), passes it to append_decision/append_history, and
  ends with `touch(task, session=session, note="scope updated: …", register=False)` (5670).
- Audit rule: every `add_event(` call site must pass the best session available. Sites
  verified already-correct: 980, 1154, 2744, 3166, 5572, 5627.

**Memo records — `task["memos"]`** (new field, created on first send):
```
{"id": <uuid hex>,          # SHARED with the announcement event's id
 "ts": <epoch float>,
 "from_sid": <sender session id or None>,          # None = anonymous/Desktop
 "from_task": <sender's attached task id or None>, # via get_link(from_sid)
 "text": <str, ≤ MEMO_TEXT_MAX>,
 "acks": [{"sid": <session id>, "ts": <epoch float>}]}   # shared, visible ledger
```
New constants beside `EVENTS_KEEP` (~59–61):
```
MEMO_TEXT_MAX  = 4000   # full memo body cap (a pasted markdown summary fits)
MEMOS_KEEP     = 50     # trim target: oldest FULLY-ACKED memos beyond this
MEMOS_HARD_CAP = 200    # safety: past this, trim oldest regardless of acks
MEMO_PENDING_MAX = 3    # pending-brief lines per injection
MEMO_LINE_MAX  = 200    # preview chars per pending-brief line
```
Body-length: memo bodies live in `task["memos"]`, NOT the event feed (EVENT_TEXT_MAX=160
is a deliberate injection/privacy cap; EVENTS_KEEP trimming would destroy an unacked
memo). The feed carries only a capped preview.

**Companion events** (ride the existing feed; zero delivery code):
- On send: `ev = add_event(task, "memo", "memo <id8> from <src>: <preview>", from_sid)`
  then `memo["id"] = ev["id"]` — the memo id IS the event id.
- On ack: `add_event(task, "memo-ack", "memo <id8> acked by <sid8>", sid)`.

### Lifecycle & ack model
- No global open/closed memo state — only the per-session ack ledger.
- A memo is "pending for session S" iff `S != from_sid` and `S ∉ {a["sid"] for a in acks}`.
- Ack is explicit, idempotent (duplicate sid → "already acked", no second entry), signed
  `{sid, ts}`. Optional promotion: `--decision [TEXT]` calls `append_decision(task,
  TEXT or memo body, session=sid)`.
- Trim on send: if `len(memos) > MEMOS_KEEP`, drop oldest memos whose pending-set is
  empty for every session in `task["sessions"]`; past `MEMOS_HARD_CAP` drop oldest
  unconditionally. An unacked memo is otherwise never trimmed.

### Delivery & rendering
`memo_pending_brief(task, session)` — new, beside `delta_brief` (~1015). Returns one
bounded block or None. Selects pending-for-`session` memos (ack-gated, **ignores
seen_ts** — re-surfaces every injection until acked). Newest-last, ≤ MEMO_PENDING_MAX
lines, ≤ MEMO_LINE_MAX chars each, ack ledger inline:
```
[task-station] 2 memo(s) awaiting YOUR ack on [ab12cd34]:
  • 3f9c2a1b (2h ago, from 9e01f2·#41) "Use WAL mode because …" — acked by: 1a2b3c4d
  • 77aa01bc (10m ago, from d4e5f6) "Auth header must be …" — acked by: (none yet)
  (+1 more pending)
Read full body:  task-station memo show --task 42 --id <id8>
Acknowledge:     task-station memo ack --task 42 --id <id8> --session <your-session-id>
```
`delta_brief` change (1015): the fresh-event comprehension (~1029–1030) additionally
excludes `kind == "memo"` (arrival owned by pending brief; `memo-ack` events still flow).

Injection sites:
- `cmd_prompt_context` attached+open path (6072–6101): compute `pending =
  memo_pending_brief(task, a.session)` next to `delta` (6077); print it after the delta
  block; pending does NOT call mark_seen. Relax the "delta is the ONLY thing" comment (6093).
- `cmd_session_start` (6908–6935): append `pending` to `msg` alongside delta (6918).
- `/todo <n>` detail (`_format_detail`, digest ~3962/3999): new "Memos:" section after
  Decisions — unacked-for-viewer first (`⚑ unacked by you`), then last few acked; each
  `id8 · age · from · preview · acked-by list`. The detail's mark_seen (4688) does NOT
  clear pending — only ack does.
- `_format_history` (~4080/4115): full "Memos" section (uncapped, full ledger).

### Surfaces
CLI — one new `memo` subparser (beside add-event, ~7059):
```
task-station memo send --task <ref> --text <body> [--session <sid>]
task-station memo ack  [--task <ref>] --id <id-prefix> --session <sid> [--decision [TEXT]]
task-station memo show [--task <ref>] [--id <id-prefix>]
```
`--task` accepts any seq/id-prefix via `resolve_ref` (870) → cross-task send needs no
attachment. `memo ack`/`show` default `--task` to the session's attached task
(`get_link`). `--id` prefix-matches `memos[*]["id"]` (ambiguous → error listing
candidates). `send` prints `memo <id8> → task #<seq> (<title>)`. Bad ref → one error
line, exit 0 (match cmd_add_event, 4760–4772).

MCP (`lib/mcp_server.py`, TOOLS ~361): three tools mirroring the logic-fn + handler
pattern (~112–279, ~314–358): `send_memo {ref, text, from?}` (`from` free-text sig,
default "desktop"); `list_memos {ref}`; `ack_memo {ref, memo_id, by}` (`by` required sig
string — Desktop has no session id).

Slash — `_TODO_SUBCMDS["memo"]` → `_todo_memo(a, rest)` (table ~4481):
```
/todo memo <n> <text…>        → send to task n (from THIS session)
/todo memo ack <id8> [TEXT]   → ack on attached task (TEXT promotes to decision)
/todo memo show [<n>] [<id8>] → list / full body
```
`commands/todo.md`: extend the argument-hint (line 3), add one output-handling bullet,
and one tracking-section line telling the model to ack surfaced memos before acting.

### Back-compat
No `memos` field → every surface silent/no-op; `memo show` → "(no memos)". Legacy events
without `id`/`sid`: delta-brief treats missing sid as "other" (test_delta_brief.py:118); id
read via `.get`; renders unchanged. No migration: `memos` created on first send; SQLite
data column round-trips the dict. Desktop/MCP callers sign with a free-text `by`/`from`
string in the sid slot (display `[:8]`).

### Non-goals (v2)
Layer-A overwrite audit with old→new diffs + `--reason`; memo threads/replies; global
open/closed state; required-ack gating on /done; push delivery; memo edit/delete.

---

## ORDERED TDD PLAN

**Phase 1 — Foundation B: event ids.** Tests first in `tests/test_events_relations.py`:
`add_event` result carries a 32-hex unique `id`; returns the appended dict; a hand-built
legacy event (no id/sid) still renders through `delta_brief` and `_format_history`. Touch
`983–996` (add `id`, `return ev`); delete dead shim 3139–3150. Accept: new + full suite green.

**Phase 2 — Foundation A: attribution.** Tests first, new `tests/test_attribution.py`:
(a) `_update_one` with `--decision`/`--log`/`--state --session S` → those events + the
`scope updated:` log event carry `sid==S`; (b) update `--session S` on a task S is NOT
attached to leaves `sessions`/`session_meta` unregistered (`register=False`); (c)
`cmd_post_compact` history event carries `a.session`; (d) `status --task <n> active
--session S` → status event carries S. Touch append_decision (3601/3608), append_history
(3612/3624), set_status (1734/1752)+promote_active (1756), cmd_status (5462)+parser
(7070), touch (1719, `register=True`), _update_one (hoist session; 5606/5611; 5670),
cmd_post_compact (3029). Accept: new + events/delta/subcommand/status tests green.

**Phase 3 — Memo core (data ops, no surfaces).** Tests first, new `tests/test_memo.py`
(`_repoint` setup): `memo_send(task, text, from_sid)` creates record w/ shared event id,
preview event capped at EVENT_TEXT_MAX, body ≤ MEMO_TEXT_MAX; 4000-char body round-trips
save/load; cross-task send records `from_task`; `memo_ack` appends `{sid,ts}`, idempotent,
posts `memo-ack` event, `--decision` promotion calls append_decision with acking sid;
trim: 60 fully-acked → MEMOS_KEEP, unacked survive; hard cap honored. Touch: constants
~59–74; helpers `memo_send`/`memo_ack`/`memo_pending`/`_memo_by_prefix` after add_event
(~997). Accept: green.

**Phase 4 — Pending brief + delivery.** Tests first, extend `test_delta_brief.py` +
`test_memo.py`: `memo_pending_brief` None on no/legacy; shows unacked-by-me w/ peer ledger
(two-session no-double-implement); re-surfaces AFTER mark_seen (ack-gated persistence);
gone after my ack; caps at MEMO_PENDING_MAX w/ "(+N more pending)"; `delta_brief` excludes
`memo` but carries `memo-ack`; hook-level: cmd_prompt_context + cmd_session_start print
the block (stdout capture, test_delta_brief.py:165–169); detail render does NOT clear
pending. Touch: memo_pending_brief (~1015); delta_brief filter (~1030); cmd_prompt_context
(6072–6101); cmd_session_start (6908–6935); comment fix 6093. Accept: green.

**Phase 5 — CLI + /todo + render.** Tests first, extend test_memo.py + test_todo_subcommands.py:
`cmd_memo` send/ack/show happy + bad-ref/ambiguous-prefix error lines (exit 0); ack/show
default to attached task; `_todo_memo` grammar; `_format_detail` Memos section;
`_format_history` full ledger. Touch: cmd_memo + parser (~7059); _todo_memo + table (4481);
_format_detail (~3999); _format_history (~4115); commands/todo.md. Accept: green.

**Phase 6 — MCP tools.** Tests first, extend `tests/test_mcp_server.py`: tools/list
advertises the 3 tools; send→list→ack round-trip via `_handle_tools_call`; bad ref →
friendly line + isError semantics matching existing tools. Touch: mcp_server.py — 3 logic
fns (after `_add_note` ~269) + 3 handlers + TOOLS entries (~361–486). Accept: green.

**Phase 7 — Docs, versions (spec already saved in FIRST ACTION).** Bump both plugin JSONs
to 1.79.0; add CHANGELOG entry. Accept: full suite green from clean checkout;
`grep -n "add_event(" lib/task-station.py` shows every site passing a session or justified
None; both JSON versions equal.

Sequencing: 1→2→3→4 strictly ordered; 5 and 6 independent after 4; 7 last.
