"""Harness adapters: how delegate spawns/inspects a worker on each AI CLI.
The registry (workers.json) + the task record stay authoritative regardless of
harness; anything Agent-View-specific must check the capability flags."""
import json
import re
import subprocess
import time


class HarnessAdapter:
    name = "?"
    supports_bg = False            # detached background-agent spawn
    supports_agent_view = False    # rows in `claude agents` / attach-inspect
    supports_named_sessions = False

    def spawn_cmd(self, brief, name=None, model=None, session_id=None,
                  resume=False):
        """argv for a worker run. session_id/resume semantics are per-harness."""
        raise NotImplementedError

    def spawn_worker(self, brief, worktree, model=None, name=None):
        """Launch detached; return worker_id (session id). Phase-3+ (bg) only."""
        raise NotImplementedError

    def worker_status(self, worker_id):
        """{'state': 'running|finished|unknown', 'pid': int|None, 'raw': dict|None}"""
        raise NotImplementedError

    def worker_result(self, worker_id):
        """{'text': str|None, 'model': str|None, 'usage_by_model': dict|None,
            'cost_usd': float|None} from the harness's own record of the run."""
        raise NotImplementedError

    def resume_cmd(self, worker_id, msg):
        raise NotImplementedError


class ClaudeAdapter(HarnessAdapter):
    name = "claude"
    supports_bg = True               # Phase 3: workers spawn as `claude --bg` agents
    supports_agent_view = True       # …so they appear in `claude agents` / attach-inspect
    supports_named_sessions = True

    # `claude --bg` prints its ASSIGNED session id at launch. The REAL format
    # (spike-verified) is a SHORT 8-hex id wrapped in ANSI color, e.g.
    #   backgrounded · \x1b[36m0a623186\x1b[39m · <name>
    # while `claude agents --json` keys on the FULL uuid (0a623186-77be-...), of
    # which the short id is the prefix. So: strip ANSI, capture the short (or full)
    # hex token, then canonicalize to the full sessionId via agents --json.
    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
    BG_ID_RE = re.compile(r"backgrounded\s*[·:]\s*([0-9a-fA-F][0-9a-fA-F-]{7,})")
    # `claude --bg --permission-mode bypassPermissions` refuses until the machine
    # has accepted the disclaimer once interactively — detect it to advise clearly.
    _DISCLAIMER_RE = re.compile(r"disclaimer|dangerously-skip-permissions", re.I)

    # Author-only worker toolset granted under dontAsk (dontAsk auto-DENIES
    # anything else, so it never hangs unattended). dontAsk — unlike acceptEdits —
    # does NOT auto-approve edits, so the edit/read tools are listed explicitly;
    # git / network / arbitrary Bash are intentionally absent → they fail-closed,
    # exactly like the old `-p` workers, with NO bypassPermissions/disclaimer.
    DONTASK_ALLOW = ("Read", "Grep", "Glob", "LS",
                     "Edit", "Write", "MultiEdit", "NotebookEdit", "TodoWrite")

    def spawn_cmd(self, brief, name=None, model=None, session_id=None,
                  resume=False, permission_mode="dontAsk"):
        """argv for a `claude --bg` background worker (#463 spike results):
        --bg conflicts with --print, IGNORES --session-id (mints its own, printed
        at launch), ACCEPTS --permission-mode/--name/--model, and resumes via
        --resume <id>. `permission_mode` defaults to **dontAsk** — fail-closed:
        auto-denies any non-allowlisted tool (never hangs unattended) with NO
        --dangerously-skip-permissions. Edits are granted via --allowedTools since
        dontAsk (unlike acceptEdits) won't auto-approve them. bypassPermissions is
        an opt-in only (config delegate_bypass_permissions, needs the one-time
        disclaimer). NO -p/--output-format/--session-id."""
        cmd = ["claude", "--bg", "--permission-mode", permission_mode]
        if permission_mode == "dontAsk":
            # variadic --allowedTools; a following flag (delegate always passes
            # --name) terminates the list so the positional brief isn't swallowed.
            cmd += ["--allowedTools", *self.DONTASK_ALLOW]
        if name:
            cmd += ["--name", name]
        if model:
            cmd += ["--model", model]
        if resume and session_id:
            cmd += ["--resume", session_id]    # spike 1c: resume by printed id
        cmd += [brief]
        return cmd

    def spawn_worker(self, brief, worktree, model=None, name=None,
                     session_id=None, resume=False, env=None,
                     permission_mode="dontAsk"):
        """Launch the detached bg agent and return its ASSIGNED session id, parsed
        from the launcher's 'backgrounded · <id> · <name>' print. The launcher
        process exits promptly; the agent lives on detached. Raises SystemExit when
        no id can be parsed (nothing to track = nothing was reliably launched)."""
        cmd = self.spawn_cmd(brief, name=name, model=model, session_id=session_id,
                             resume=resume, permission_mode=permission_mode)
        proc = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True,
                              timeout=120, env=env, start_new_session=True)
        blob = self._ANSI_RE.sub("", (proc.stdout or "") + "\n" + (proc.stderr or ""))
        m = self.BG_ID_RE.search(blob)
        if not m:
            if permission_mode == "bypassPermissions" and self._DISCLAIMER_RE.search(blob):
                raise SystemExit(
                    "delegate: `claude --bg --permission-mode bypassPermissions` "
                    "needs a ONE-TIME disclaimer acceptance on this machine. Run "
                    "`claude --dangerously-skip-permissions` once interactively "
                    "(accept the prompt), then retry. To avoid it, set config "
                    "delegate_bypass_permissions=false (workers then use acceptEdits "
                    "and may block on a non-allowlisted tool).\n%s" % blob.strip()[:600])
            raise SystemExit(
                "delegate: `claude --bg` printed no session id (rc %s).\n%s"
                % (proc.returncode, blob.strip()[:800]))
        # The print is usually the SHORT id; canonicalize to the full sessionId the
        # agents list keys on (retry briefly — a fresh agent may not be listed yet).
        return self._canonicalize_id(m.group(1), cwd=worktree) or m.group(1)

    def _canonicalize_id(self, partial, cwd=None, tries=5, delay=0.6):
        """Resolve a short/partial launch id to the FULL agents-list sessionId
        (prefix match). Returns None if it never appears (caller keeps the partial)."""
        for _ in range(max(1, tries)):
            idx = self.agents_index(cwd=cwd)
            if partial in idx:
                return partial
            hit = [sid for sid in idx if sid.startswith(partial)]
            if len(hit) == 1:
                return hit[0]
            time.sleep(delay)
        return None

    def agents_index(self, cwd=None):
        """{sessionId: row} from `claude agents --json` (global; --cwd filters).
        {} on any failure — callers treat absence as 'unknown', never 'dead'."""
        cmd = ["claude", "agents", "--json"]
        if cwd:
            cmd += ["--cwd", cwd]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            rows = json.loads(r.stdout or "[]")
        except Exception:
            return {}
        return {row["sessionId"]: row for row in rows
                if isinstance(row, dict) and row.get("sessionId")}

    def worker_status(self, worker_id):
        """{'state': <agents status | 'gone'>, 'pid': int|None, 'raw': row|None}.
        'gone' = the sid is not in the agents list (unlisted → finished/killed).
        Matches the full sessionId, falling back to a unique prefix match so a
        stored SHORT id still resolves."""
        idx = self.agents_index()
        row = idx.get(worker_id)
        if row is None:
            hit = [r for sid, r in idx.items() if sid.startswith(worker_id)]
            row = hit[0] if len(hit) == 1 else None
        if row is None:
            return {"state": "gone", "pid": None, "raw": None}
        return {"state": row.get("status") or "running",
                "pid": row.get("pid"), "raw": row}


class CodexAdapter(HarnessAdapter):
    """OpenAI Codex CLI worker via `codex exec --json` — a DETACHED-subprocess
    fallback (the pre-`--bg` spawn model): the worker runs to completion, streaming
    NDJSON events on stdout, with NO Agent-View row and NO attach. Capability flags
    stay False so every Agent-View surface (claude agents / bg poll / adopt) is
    skipped — task-station renders its OWN board instead (display-loss only; the
    registry + task record stay authoritative, so tracking is intact).

    UNVERIFIED (codex CLI is not installed on the authoring machine): the exec flags
    and the NDJSON event/field names below are best-effort and MUST be frozen from a
    real `codex exec --json "say hi"` capture before relying on the live path. Every
    guess is marked `# UNVERIFIED`. The shim tests pin the SHAPE this adapter expects,
    so the freeze is a matter of matching real field names to these."""
    name = "codex"
    supports_bg = False
    supports_agent_view = False
    supports_named_sessions = False       # UNVERIFIED — no known --name equivalent
    uses_ndjson_result = True             # run_worker builds the result from events()

    def spawn_cmd(self, brief, name=None, model=None, session_id=None,
                  resume=False):
        # `codex exec` = non-interactive; `--json` = NDJSON events on stdout.
        # `--skip-git-repo-check` lets it run in a worktree without a fresh repo probe.
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check"]  # UNVERIFIED flags
        if model:
            cmd += ["-m", model]                                    # UNVERIFIED flag
        if resume and session_id:
            cmd += ["resume", session_id]                           # UNVERIFIED subcmd
        cmd += [brief]
        return cmd

    def resume_cmd(self, worker_id, msg):
        return self.spawn_cmd(msg, session_id=worker_id, resume=True)

    def result_from_events(self, events):
        """Map codex NDJSON events → the SAME result blob shape `_parse_result`
        consumes ({result, session_id, total_cost_usd, usage, model}), or None when
        the stream carried no usable terminal event (→ abnormal, like a claude run
        with no result). cost is ALWAYS None: lib/pricing has no codex rates, so
        codex tokens are recorded UNPRICED — the existing unpriced/`≥$` display chain
        renders that correctly. Defensive: unknown shapes degrade to None fields.

        UNVERIFIED event/field names (freeze from a real capture):
          - session id:      a `session.created`/`session_configured` event's `session_id`/`id`
          - final text:      the last `item.completed`/`agent_message`/`message` event's text
          - token usage:     a `token_count`/`usage` event's input/output/cached fields
        """
        sid = text = None
        usage = {}
        for ev in events or []:
            if not isinstance(ev, dict):
                continue
            # session id — first non-empty of the known keys wins
            if sid is None:
                for k in ("session_id", "sessionId", "conversation_id", "id"):
                    v = ev.get(k)
                    if isinstance(v, str) and v:
                        sid = v
                        break
            # token usage — last one wins (codex reports a running/final count)
            u = ev.get("usage") or ev.get("token_count") or ev.get("tokens")
            if isinstance(u, dict):
                usage = u
            # assistant/final text — last non-empty wins
            t = self._event_text(ev)
            if t:
                text = t
        if sid is None and text is None and not usage:
            return None
        return {"type": "result", "result": text, "session_id": sid,
                "total_cost_usd": None,          # unpriced — no codex rate sheet
                "usage": self._norm_codex_usage(usage),
                "model": None}                   # left to the requested model

    @staticmethod
    def _event_text(ev):
        """Best-effort assistant text out of a codex event (UNVERIFIED shapes)."""
        for k in ("text", "message", "content", "delta"):
            v = ev.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, dict):
                inner = v.get("text") or v.get("content")
                if isinstance(inner, str) and inner.strip():
                    return inner
        return None

    @staticmethod
    def _norm_codex_usage(u):
        """Map a codex usage dict → the claude-shaped usage `_parse_result`/`_norm_usage`
        read (input_tokens/output_tokens/cache_read_input_tokens). UNVERIFIED keys."""
        if not isinstance(u, dict):
            return {}
        def _pick(*keys):
            for k in keys:
                v = u.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
            return 0
        return {
            "input_tokens": _pick("input_tokens", "prompt_tokens", "input"),
            "output_tokens": _pick("output_tokens", "completion_tokens", "output"),
            "cache_read_input_tokens": _pick("cached_input_tokens",
                                             "cache_read_input_tokens", "cached_tokens"),
        }


_ADAPTERS = {"claude": ClaudeAdapter, "codex": CodexAdapter}


def get_adapter(name):
    try:
        return _ADAPTERS[(name or "claude").lower()]()
    except KeyError:
        raise SystemExit("delegate: unknown harness %r (known: %s)"
                         % (name, ", ".join(sorted(_ADAPTERS))))
