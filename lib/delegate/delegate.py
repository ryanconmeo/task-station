#!/usr/bin/env python3
"""delegate — spawn / resume an in-project Claude worker.

The "hub" session is usually launched from a directory outside the repo and
therefore lacks every directory-scoped thing: a project's ./CLAUDE.md, its
.claude/settings.json permissions + env, its hooks, its project-scoped
.mcp.json servers, and its project-local skills. Those load ONLY in a `claude`
process whose cwd is inside the repo. This helper spawns such a process so the
work runs with the project's full machinery, keeps ONE persistent worker per
project (resuming it across turns), and relays the worker's result back to the
hub.

Spawn model (task #463): a `claude` worker launches as `claude --bg` — a DETACHED
background agent that survives the hub dying AND appears as a row in Claude Code's
Agent View, so a human can attach and watch it live (left-arrow → select → attach).
`--bg` gives no stdout stream, so liveness/exit are derived by polling
`claude agents --json` (status `idle` = turn complete = ok; the row going absent
before idle = crash; the wall-clock watchdog = timeout) — see run_worker_bg /
_classify_exit_bg. Other harnesses degrade behind the HarnessAdapter capability
flags: `--harness codex` runs `codex exec --json` as a detached fallback with NO
Agent-View row (task-station renders its own board; tracking is intact,
display-loss only). The legacy `claude -p` / `codex exec` NDJSON-streaming path is
retained for adapters whose `supports_bg` is False.

The decision of *when* to delegate lives in your CLAUDE.md (always in context);
this script is the *how*. A `--bg` worker spawns with --permission-mode **dontAsk**
by default — fail-closed: non-allowlisted tools are auto-denied so it NEVER blocks
on a prompt while unattended, with NO `--dangerously-skip-permissions`, exactly like
the old `-p` workers. The author-only edit toolset is granted via --allowedTools
(dontAsk, unlike acceptEdits, won't auto-approve edits); git / network / arbitrary
Bash stay denied. `bypassPermissions` is OPT-IN only (config
`delegate_bypass_permissions`, default OFF; needs a one-time
`claude --dangerously-skip-permissions` acceptance) for users who want workers to
run anything unattended.

Worktree policy: write work NEVER runs on a repo's main checkout. Pass
--worktree <name> and the worker runs in <repo>-worktrees/<name>, resolving it
or creating it on the fly (off the repo's default branch) via the bundled
worktree-up.sh. The naming convention is the story id + slug or fix-<PR#> for
PR-fix branches; --branch overrides the branch (default = the worktree name)
and --base overrides the new-branch base (default: the repo's default branch).
Omit --worktree only for read-only delegations.

Usage:
  delegate.py run  --repo <path> --task "<instructions>" [--worktree NAME] [--branch BR] [--base REF] [--seq N] [--solo] [--label L] [--fresh] [--timeout S] [--harness claude|codex]
  delegate.py run  --project <name> --task "<instructions>" [--worktree NAME] [--branch BR] [--base REF] [--seq N] [--solo] [--label L] [--fresh] [--timeout S] [--harness claude|codex]
    --repo takes an absolute path to a git repo and bypasses project-name→workspace resolution.
    --project <name> scans directories from TASK_STATION_WORKSPACE_DIRS (colon-separated on Unix).
    For write work (--worktree) with no --seq, the calling session's attached /todo
    seq is inherited automatically (use --solo to opt out for ad-hoc work).
  delegate.py list
  delegate.py dir  --project <name> [--worktree NAME]   # resolve & print the repo (or worktree) path
  delegate.py dir  --repo <path>    [--worktree NAME]

Lives inside the task-station repo; the registry sits beside this script and it
links back to the tracker via the sibling task-station.py.
"""
import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HOME = os.path.expanduser("~")

# delegate.py lives one dir deeper than paths.py, so add the plugin root to sys.path before importing it
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
# harness.py is a sibling module (lib/delegate/harness.py); add this dir so the
# in-process import resolves (tests add it too via their own bootstrap).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

REG_DIR = paths.data_dir()                                     # data dir (e.g. ~/.claude/task-station-data) — survives /plugin update
REG = os.path.join(REG_DIR, "workers.json")
TASK_STATION_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "task-station.py")     # plugin root → sibling task-station.py (delegate.py is one dir deeper)
WORKTREE_UP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worktree-up.sh")
# Where Claude Code writes session transcripts: ~/.claude/projects/<enc-cwd>/<sid>.jsonl.
# A module global so tests can repoint it (mirrors usage.PROJECTS_ROOT).
PROJECTS_ROOT = os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "projects")
# Where the ClaudeCode.app supervisor persists a `--bg` agent's session so it can
# RESPAWN it after a kill: <config>/sessions/<something>.json. A module global so
# tests can repoint it. The path + schema are Claude-Code-INTERNAL and may change —
# every reader of it is best-effort and version-tolerant (see _remove_bg_session_file).
SESSIONS_DIR = os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "sessions")
# The CURRENT store (measured 2026-08-14 on this machine, 1.4k files): nested
# <root>/<org-uuid>/<user-uuid>/local_<uuid>.json, where the file's own
# `sessionId` is the local_<uuid> name and `cliSessionId` is the id the agents
# list keys on. A module global so tests can repoint it; every reader stays
# best-effort and version-tolerant.
SESSIONS_STORE_ROOT = os.path.join(
    HOME, "Library", "Application Support", "Claude", "claude-code-sessions")
# The harness JOB records — measured 2026-08-14: <config>/jobs/<short-sid>/
# state.json is what `claude agents --json` actually renders for background
# agents (state/tempo/detail/needs/tokens/output.result). Store-file removal and
# process kills alone leave a ghost row; flipping `state` to a terminal value is
# what clears it. A module global so tests can repoint it.
JOBS_ROOT = os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "jobs")


def _now():
    return int(time.time())


def load_reg():
    try:
        with open(REG) as f:
            return json.load(f)
    except Exception:
        return {}


def save_reg(d):
    os.makedirs(REG_DIR, exist_ok=True)
    # Per-process tmp suffix so two concurrent workers can't clobber one tmp
    # (B3): worker A's write-then-rename would otherwise race worker B's.
    tmp = REG + ".%d.tmp" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, REG)


class _reg_lock:
    """Exclusive fcntl.flock over a dedicated lockfile beside the registry, so a
    load→mutate→save cycle is atomic across concurrent workers (B3). A separate
    lockfile (not REG itself) survives save_reg's atomic os.replace of REG."""

    def __enter__(self):
        os.makedirs(REG_DIR, exist_ok=True)
        self._fh = open(REG + ".lock", "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()


def _touch_heartbeat(key, **fields):
    """Reload-merge-single-key registry update (§2). Under the registry lock,
    re-read the reg fresh from disk, merge `fields` into reg[key] ONLY, and save
    — so a heartbeat can never clobber another worker's just-written entry. A
    missing key is a no-op (the worker was de-registered). Used for the ~1/sec
    live heartbeat (last_event_ts/phase) AND the terminal write (pid=None/exit)."""
    with _reg_lock():
        reg = load_reg()
        if key not in reg:
            return
        reg[key].update(fields)
        save_reg(reg)


def _workspace_roots():
    """Return the list of workspace root dirs from config.json (falling back to
    the TASK_STATION_WORKSPACE_DIRS env var). Non-existent dirs are silently
    dropped. Returns an empty list when neither is set.
    """
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config
    return [p for p in config.workspace_dirs() if os.path.isdir(p)]


def _candidates():
    out = []
    for parent in _workspace_roots():
        for name in sorted(os.listdir(parent)):
            # A '<repo>-worktrees' dir holds worktrees, it is NOT a project —
            # never let --project resolve to one (that would run on a worktree
            # tree as if it were the repo).
            if name.endswith("-worktrees"):
                continue
            full = os.path.join(parent, name)
            if os.path.isdir(full):
                out.append((name, full))
    return out


def _validate_repo(path):
    """Raise SystemExit with a clear message if path is not a git repo."""
    if not os.path.isdir(path):
        raise SystemExit("delegate: --repo %r is not a directory." % path)
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "--git-dir"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(
            "delegate: --repo %r does not appear to be a git repository "
            "(git rev-parse failed)." % path
        )


def resolve_dir(project=None, repo=None):
    """Map a project name or explicit repo path to a repo dir.

    Pass repo= for an explicit path (bypasses workspace scanning).
    Pass project= to scan the configured workspace roots.
    """
    if repo is not None:
        path = os.path.abspath(os.path.expanduser(repo))
        _validate_repo(path)
        return path

    if project is None:
        raise SystemExit("delegate: must pass either --project or --repo.")

    p = project.strip()
    # Allow passing an explicit path as --project too.
    if os.path.isdir(os.path.expanduser(p)):
        return os.path.abspath(os.path.expanduser(p))

    roots = _workspace_roots()
    if not roots:
        raise SystemExit(
            "delegate: --project %r given but TASK_STATION_WORKSPACE_DIRS is not set.\n"
            "  Either pass --repo /absolute/path/to/repo  OR  set TASK_STATION_WORKSPACE_DIRS\n"
            "  to a %s-separated list of directories that contain your repos."
            % (project, os.pathsep)
        )

    cand = _candidates()
    exact = [f for (n, f) in cand if n.lower() == p.lower()]
    if exact:
        return exact[0]
    subs = [(n, f) for (n, f) in cand if p.lower() in n.lower()]
    if len(subs) == 1:
        return subs[0][1]
    if not subs:
        raise SystemExit(
            "delegate: no project in %r matching %r.\n  available: %s"
            % ([r for r in roots], project, ", ".join(n for n, _ in cand))
        )
    raise SystemExit(
        "delegate: %r is ambiguous — matches: %s. Be more specific."
        % (project, ", ".join(n for n, _ in subs))
    )


def detect_base_branch(repo_root):
    """Detect the repo's default remote branch.

    Strategy:
      1. git symbolic-ref refs/remotes/origin/HEAD  (strip leading 'origin/')
      2. origin/main if that ref exists
      3. 'main' as a last resort
    """
    r = subprocess.run(
        ["git", "-C", repo_root, "symbolic-ref", "--quiet", "--short",
         "refs/remotes/origin/HEAD"],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        ref = r.stdout.strip()
        if ref.startswith("origin/"):
            ref = ref[len("origin/"):]
        if ref:
            return "origin/" + ref

    # Check whether origin/main exists
    r2 = subprocess.run(
        ["git", "-C", repo_root, "show-ref", "--verify", "--quiet",
         "refs/remotes/origin/main"],
        capture_output=True
    )
    if r2.returncode == 0:
        return "origin/main"

    return "main"


def worktrees_parent(repo_root):
    """The sibling '<repo>-worktrees' dir, e.g. /path/to/Repo -> /path/to/Repo-worktrees."""
    return repo_root.rstrip("/") + "-worktrees"


def worktree_path(repo_root, name):
    """The would-be path for worktree <name> (no side effects)."""
    return os.path.join(worktrees_parent(repo_root), name)


def resolve_worktree(repo_root, name, branch=None, base=None):
    """Find or create <repo>-worktrees/<name>; return its path.

    If base is None the repo's default branch is auto-detected.
    A missing worktree is built with the bundled worktree-up.sh. Falls back to
    a bare `git worktree add` if the script is unavailable or fails.
    """
    wt = worktree_path(repo_root, name)
    if os.path.isdir(wt):
        return wt
    if base is None:
        base = detect_base_branch(repo_root)
    branch = branch or name
    os.makedirs(worktrees_parent(repo_root), exist_ok=True)
    if os.path.exists(WORKTREE_UP):
        proc = subprocess.run(["bash", WORKTREE_UP, wt, branch, base],
                              cwd=repo_root, capture_output=True, text=True)
        if os.path.isdir(wt):
            return wt
        sys.stderr.write("[delegate] worktree-up.sh did not produce %s; "
                         "falling back to bare git worktree add.\n%s\n"
                         % (wt, (proc.stderr or "").strip()))
    # Bare fallback: reuse an existing local/remote branch, else cut a new one.
    subprocess.run(["git", "fetch", "origin", "--quiet"],
                   cwd=repo_root, capture_output=True)
    add = subprocess.run(["git", "worktree", "add", wt, branch],
                         cwd=repo_root, capture_output=True, text=True)
    if not os.path.isdir(wt):
        add = subprocess.run(["git", "worktree", "add", wt, "-b", branch, base],
                             cwd=repo_root, capture_output=True, text=True)
    if not os.path.isdir(wt):
        raise SystemExit("delegate: could not create worktree %s:\n%s"
                         % (wt, (add.stderr or "").strip()))
    return wt


def _build_worker_cmd(task, model="sonnet"):
    """Base headless-worker `claude` command (pure; no session/resume args).

    A worker does author-only mechanical edits, so it defaults to the cheaper
    `sonnet` model rather than inheriting the account default (opus). An empty/None
    `model` omits `--model`, falling back to the account default.
    """
    cmd = ["claude", "-p", task,
           "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits"]
    if model:
        cmd += ["--model", model]
    return cmd


def _classify_exit(returncode, result_event, timed_out):
    """B4 terminal-state rule → (exit_label, is_abnormal).

    Abnormal = `timed_out OR returncode != 0 OR result_event is None`. This
    REPLACES the old `returncode != 0 and not out` test, which mis-classified a
    worker that streamed events then died (rc 0, no terminal result) as success.
    `timeout` wins the label; otherwise any abnormality is `crash`; else `ok`."""
    if timed_out:
        return "timeout", True
    if returncode != 0 or result_event is None:
        return "crash", True
    return "ok", False


# ---------------------------------------------------------------- streaming ----
# stream-json print mode emits one JSON event per line. These pure helpers turn
# that raw NDJSON into a compact human-readable activity feed; they are kept free
# of any subprocess so they unit-test against synthetic lines.

_TOOL_ARG_MAX = 60
_TEXT_MAX = 80
# salient keys tried in order when summarising a tool_use `input` dict
_SALIENT_KEYS = ("file_path", "path", "command", "pattern", "url",
                 "query", "description", "prompt", "old_string")


def _iter_stream_events(lines):
    """Yield dict events from an iterable of raw NDJSON lines.

    Blank lines, non-JSON / partial lines, and JSON that isn't an object are
    skipped (a truncated tail line on an interrupted worker must not crash the
    feed). Pure: `lines` is any iterable of strings (a real `proc.stdout`, a
    list in tests)."""
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            yield obj


def _summarize_tool_input(inp):
    """One-line, newline-flattened, truncated summary of a tool_use `input`.
    Prefers a salient key (file_path/command/…) so `→ Edit: /a/b.py` reads well;
    falls back to a compact JSON dump."""
    if isinstance(inp, dict):
        val = None
        for k in _SALIENT_KEYS:
            if inp.get(k):
                val = inp[k]
                break
        if val is None:
            try:
                val = json.dumps(inp, separators=(",", ":"))
            except Exception:
                val = str(inp)
    else:
        val = inp
    s = str(val).replace("\n", " ").replace("\r", " ").strip()
    if len(s) > _TOOL_ARG_MAX:
        s = s[:_TOOL_ARG_MAX - 1] + "…"
    return s


def _progress_line(event):
    """Map ONE stream event to a compact progress line, or None to skip it.

    WHITELIST (Assumptions A — a trivial task emits mostly skippable noise):
      - assistant message w/ a tool_use block → `→ <ToolName>: <arg summary>`
        (tool_use preferred over text in the same message — the action is salient)
      - assistant message w/ a text block     → `· <first ~80 chars>`
      - user message w/ a tool_result carrying is_error → `  ✗ <err snippet>`
    Everything else (all `system` subtypes, `result`, `rate_limit_event`,
    unknown types, thinking-only messages) → None."""
    if not isinstance(event, dict):
        return None
    etype = event.get("type")
    msg = event.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return None
    if etype == "assistant":
        text_line = None
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name") or "tool"
                return "→ %s: %s" % (name, _summarize_tool_input(block.get("input")))
            if btype == "text" and text_line is None:
                txt = (block.get("text") or "").replace("\n", " ").replace("\r", " ").strip()
                if txt:
                    if len(txt) > _TEXT_MAX:
                        txt = txt[:_TEXT_MAX - 1] + "…"
                    text_line = "· " + txt
        return text_line
    if etype == "user":
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" \
                    and block.get("is_error"):
                err = block.get("content")
                if isinstance(err, list):
                    err = " ".join(str(b.get("text", "")) if isinstance(b, dict) else str(b)
                                   for b in err)
                err = str(err or "").replace("\n", " ").replace("\r", " ").strip()
                if len(err) > _TEXT_MAX:
                    err = err[:_TEXT_MAX - 1] + "…"
                return ("  ✗ " + err).rstrip()
    return None


def _kill_group(proc, grace=3):
    """SIGTERM then (after a grace period) SIGKILL the worker's whole process
    group (B1). `claude` spawns bash/npm grandchildren; a bare `proc.kill()`
    would orphan them to keep mutating the worktree AFTER the WIP commit. The
    worker leads its own group (`start_new_session=True`), so `os.killpg` reaches
    the grandchildren too. Every step is swallowed (an already-dead group is fine)."""
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except Exception:
        pass
    deadline = time.time() + max(0.0, grace)
    while time.time() < deadline:
        try:
            if proc.poll() is not None:
                break
        except Exception:
            break
        time.sleep(0.05)
    try:
        if proc.poll() is None:
            os.killpg(pgid, signal.SIGKILL)
    except Exception:
        pass


def run_worker(dirpath, task, session_id=None, resume=False, timeout=None, name=None,
               model="sonnet", key=None, adapter=None):
    """Launch a headless worker in `dirpath` and STREAM its stream-json output,
    emitting a compact activity feed to stderr as it works.

    resume=True  -> `--resume <id>`: continue an existing session (id unchanged).
    resume=False -> `--session-id <id>`: create a session with a KNOWN id. The
                    caller pre-registers that id BEFORE launching, so even if this
                    process or the worker is killed mid-run the session is on disk
                    under a resumable id (no lost conversation link).

    Returns the (B4) contract `(returncode, result_event_json_or_None,
    stderr_text, timed_out)`:
      - `result_event_json` is the terminal `result` event re-serialised to a
        JSON string (fed to the EXISTING _parse_result unchanged), or None when
        the stream ended without one (worker died mid-flight → abnormal).
      - `timed_out` is set by the watchdog Timer, not a per-line deadline
        (readline() blocks forever on a quiet worker, so a deadline never fires).

    I/O plumbing: stdout is a streamed pipe read line-by-line; stderr goes to a
    TEMP FILE, not a second pipe — reading only stdout while stderr fills its
    ~64KB pipe buffer would deadlock both processes (B2). When `key` is given,
    the real worker pid + a throttled (~1/sec) heartbeat (last_event_ts/phase)
    are written to the registry so `delegate status` sees true liveness."""
    # Legacy detached NDJSON-streaming path — the non-bg adapters (Codex). The claude
    # harness now spawns via run_worker_bg; a codex worker uses the adapter's own
    # `codex exec --json` command and its event→result mapping (uses_ndjson_result).
    # With no adapter (or a claude adapter), fall back to the self-contained `-p`
    # command so the direct-run_worker tests keep exercising the claude stream shape.
    ndjson_adapter = adapter if getattr(adapter, "uses_ndjson_result", False) else None
    if ndjson_adapter is not None:
        cmd = ndjson_adapter.spawn_cmd(task, name=name, model=model,
                                       session_id=session_id, resume=resume)
    else:
        cmd = _build_worker_cmd(task, model)
        if session_id and resume:
            cmd += ["--resume", session_id]
        elif session_id:
            cmd += ["--session-id", session_id]
            if name:
                cmd += ["-n", name]
        elif name:
            cmd += ["-n", name]
    # Workers are headless children: silence the /todo hooks so each worker turn
    # doesn't get nudged to track its own task (that's the hub's job).
    env = dict(os.environ, TASK_STATION_SUPPRESS="1")

    stderr_fh = tempfile.TemporaryFile(mode="w+", errors="replace")
    proc = subprocess.Popen(cmd, cwd=dirpath, stdout=subprocess.PIPE,
                            stderr=stderr_fh, text=True, env=env,
                            start_new_session=True)

    state = {"timed_out": False}
    timer = None
    if timeout:
        def _on_timeout():
            state["timed_out"] = True
            _kill_group(proc)
        timer = threading.Timer(timeout, _on_timeout)
        timer.daemon = True
        timer.start()

    # Feed header (§1). The `system`/init stream event itself is skippable noise
    # (Assumptions A), so announce the start directly rather than via _progress_line.
    print("· worker started (%s)" % (model or "account default"),
          file=sys.stderr, flush=True)

    # Record the REAL worker pid at launch. Liveness confirms `ps comm` contains
    # `claude`, so the stored pid must be the claude child (not delegate's python).
    if key:
        try:
            _touch_heartbeat(key, pid=proc.pid, started_ts=_now(), exit=None,
                             phase="worker started")
        except Exception:
            pass

    result_event = None
    ndjson_events = [] if ndjson_adapter is not None else None
    last_hb = 0
    try:
        for ev in _iter_stream_events(proc.stdout):
            if ndjson_adapter is not None:
                ndjson_events.append(ev)     # codex: terminal built post-stream from events
            elif ev.get("type") == "result":
                result_event = ev            # claude: terminal — captured, not printed as progress
                continue
            line = _progress_line(ev)
            if line:
                print(line, file=sys.stderr, flush=True)
                if key:
                    now = _now()
                    if now - last_hb >= 1:    # throttle heartbeats to ~1/sec
                        try:
                            _touch_heartbeat(key, last_event_ts=now, phase=line)
                        except Exception:
                            pass
                        last_hb = now
    finally:
        if timer:
            timer.cancel()

    proc.wait()
    stderr_text = ""
    try:
        stderr_fh.flush()
        stderr_fh.seek(0)
        stderr_text = stderr_fh.read()
    except Exception:
        pass
    finally:
        try:
            stderr_fh.close()
        except Exception:
            pass

    if ndjson_adapter is not None and result_event is None:
        result_event = ndjson_adapter.result_from_events(ndjson_events)
    result_json = json.dumps(result_event) if result_event is not None else None
    return proc.returncode, result_json, stderr_text, state["timed_out"]


# ---- --bg background-worker lifecycle (task #463) ----------------------------
# A `claude --bg` worker is a detached agent: it survives the hub, appears in
# `claude agents --json`, and is attach-inspectable in Agent View. There is NO
# stdout pipe and NO transcript `result` record (spike-verified), so liveness and
# the terminal verdict come from the agents-row `status`: `idle` = turn complete
# (ok), the row going absent before reaching idle = died (crash), the wall-clock
# watchdog = timeout. `--bg` runs FULL session init, so a worker is `busy` for a
# noticeable startup window — the poll loop must tolerate a long initial busy.
IDLE_AGENT_STATES = {"idle"}          # spike 1b: 'idle' = turn complete / waiting
# Parked, not progressing (444-17: five `blocked` launches burned ~3h because the
# poll loop had no exit for them). A worker in one of these states is NOT working
# and will not start on its own — the loop fails FAST with a diagnosis instead of
# spinning to the watchdog, and status renders it as stalled, never "running".
STALLED_AGENT_STATES = {"blocked", "stalled", "needs-input"}


def _kill_pid_group(pid, grace=3):
    """SIGTERM→SIGKILL a bg agent's process group BY PID (the `claude agents --json`
    pid). `claude agents` has no scriptable stop subcommand — the TUI-only stop
    can't be automated — so the watchdog kills the group directly. Same swallowing
    discipline as _kill_group; a gone/again-reaped group is a no-op."""
    try:
        pgid = os.getpgid(int(pid))
    except Exception:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except Exception:
        pass
    time.sleep(max(0.0, grace))
    try:
        os.killpg(pgid, signal.SIGKILL)
    except Exception:
        pass


def _find_agent_pids(sid):
    """PIDs of live bg-agent processes whose command line carries `sid`.
    Background agents run as daemon-spawned `--bg-pty-host` processes (their own
    process-group leaders, measured pid==pgid) whose argv embeds the session
    transcript path — the agents JSON rows for them carry NO pid, so the pid must
    be resolved from `ps`. Only lines that are recognizably bg agents are
    accepted (`--bg-pty-host`, or a task-station/wk worker --name): a user
    interactively resuming the same sid must never match. Best-effort []."""
    if not sid:
        return []
    try:
        r = subprocess.run(["ps", "-axo", "pid=,command="],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    pids = []
    for line in (r.stdout or "").splitlines():
        if sid not in line:
            continue
        if ("--bg-pty-host" not in line and "--name task-station-" not in line
                and "--name wk-" not in line):
            continue
        head = line.strip().split(None, 1)[0]
        if head.isdigit() and int(head) != os.getpid():
            pids.append(int(head))
    return pids


def _job_record(sid):
    """The harness job record for a bg agent: JOBS_ROOT/<short-sid>/state.json as
    a (path, dict) tuple, or (None, None). Prefix-tolerant in both directions —
    the dir name is the SHORT id. Never raises."""
    if not sid:
        return None, None
    try:
        dirs = os.listdir(JOBS_ROOT)
    except Exception:
        return None, None
    for d in dirs:
        if not (sid.startswith(d) or d.startswith(sid)):
            continue
        p = os.path.join(JOBS_ROOT, d, "state.json")
        try:
            with open(p) as f:
                doc = json.load(f)
        except Exception:
            return None, None
        return (p, doc) if isinstance(doc, dict) else (None, None)
    return None, None


def _job_result(sid):
    """The worker's final result text from its job record (`output.result`) —
    for a bg agent this outlives the process and is cheaper and cleaner than the
    transcript tail. None when absent."""
    _, doc = _job_record(sid)
    if not doc:
        return None
    out = doc.get("output")
    txt = out.get("result") if isinstance(out, dict) else None
    return txt if (isinstance(txt, str) and txt.strip()) else None


def _job_diagnosis(sid):
    """One diagnostic phrase from the job record for a parked agent — the record's
    `needs` field literally says what it is waiting for (the 444-17 blocked
    workers were waiting on denied tool actions). '' when unknowable."""
    _, doc = _job_record(sid)
    if not doc:
        return ""
    bits = []
    if doc.get("detail"):
        bits.append("detail: %s" % doc["detail"])
    if doc.get("needs"):
        bits.append("needs: %s" % doc["needs"])
    return "; ".join(str(b)[:300] for b in bits)


def _mark_job_done(sid):
    """Flip a parked agent's job record to state 'done' so the agents list stops
    serving the row — measured 2026-08-14: THIS file is what `claude agents
    --json` renders; store-file removal + process kill alone leave a ghost row.
    Schema-preserving (state/tempo only, atomic replace), keeps output.result
    (the worker's final report). True when flipped; silently False on any
    surprise or when already terminal. Never raises."""
    p, doc = _job_record(sid)
    if not p or not doc:
        return False
    try:
        if doc.get("state") in ("done", "failed"):
            return False
        doc["state"] = "done"
        doc["tempo"] = "done"
        tmp = "%s.ts-tmp.%d" % (p, os.getpid())
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def run_worker_bg(adapter, dirpath, task, session_id=None, resume=False,
                  timeout=None, name=None, model=None, key=None, poll_secs=5,
                  permission_mode="dontAsk", on_launch=None, stall_grace=45):
    """Launch a DETACHED `--bg` worker and poll `claude agents --json` until it
    reaches `idle` (turn complete → ok), leaves the list (`gone` → died), parks in
    a STALLED_AGENT_STATES state for `stall_grace` seconds (→ fail fast with a
    diagnosis line), or the wall-clock watchdog fires (→ killed, timed_out). No
    stdout pipe exists under --bg: the id comes from the launch print;
    liveness/phase come from the agents row; the human inspects by attaching in
    Agent View.

    LIVENESS TRUTH (444-17, B1): the heartbeat only advances `last_event_ts` on a
    state that is evidence of progress (busy/idle) — a poll that observes a PARKED
    state must not manufacture freshness, that is exactly the lie that hid five
    blocked workers for ~3h. Every poll records the observed `agent_state`
    verbatim so `status` can render the truth even after this loop is gone. A
    parked state is judged against transcript-existence when the grace trips, and
    the diagnosis line SAYS the state and the transcript verdict.

    Returns (sid, final_state, timed_out). final_state is the LAST observed agents
    state ('idle' when it completed, 'gone' once unlisted, a stalled state when
    the grace tripped). Raises SystemExit when the launch itself failed (no id
    printed)."""
    env = dict(os.environ, TASK_STATION_SUPPRESS="1")
    sid = adapter.spawn_worker(task, dirpath, model=model, name=name,
                               session_id=session_id, resume=resume, env=env,
                               permission_mode=permission_mode)
    # Register NOW — the id is known before any real work (the agent has barely
    # booted), so a mid-run hub death still leaves a tracked, resumable/adoptable
    # entry (crash-resume survivability, per the brief's spike log).
    if on_launch:
        on_launch(sid)
    print("· worker backgrounded: %s (%s) — inspect via Agent View"
          % (sid, model or "account default"), file=sys.stderr, flush=True)
    started = _now()
    deadline = (started + timeout) if timeout else None
    state, pid = "unknown", None
    parked_since = None
    if key:
        _touch_heartbeat(key, pid=None, started_ts=started, exit=None,
                         phase="bg worker launched", agent_state="launched")
    # Settle: give the agent a moment to appear in the list before the first poll.
    time.sleep(min(poll_secs, 2))
    while True:
        st = adapter.worker_status(sid)
        state, pid = st["state"], (st.get("pid") or pid)
        parked = state in STALLED_AGENT_STATES
        if key:
            hb = {"pid": pid, "phase": "agent status: %s" % state,
                  "agent_state": state}
            if not parked:                    # progress evidence only — never a
                hb["last_event_ts"] = _now()  # parked poll (the 444-17 lie)
            _touch_heartbeat(key, **hb)
        if state in IDLE_AGENT_STATES:            # turn complete → ok
            return sid, state, False
        if state == "gone":                       # unlisted → died / killed
            return sid, "gone", False
        if parked:
            parked_since = parked_since or _now()
            if _now() - parked_since >= max(0, stall_grace):
                t = _find_transcript(sid)
                verdict = ("transcript ABSENT — the session never started a turn"
                           if not t else "transcript exists: %s" % t)
                jd = _job_diagnosis(sid)
                print("delegate: worker %s is PARKED in agents state '%s' "
                      "(%ds and not progressing; %s%s). Treating it as STALLED "
                      "instead of waiting for the watchdog — attach via Agent "
                      "View, fix the cause (trust/grants preflight output above), "
                      "or `delegate reap-parked`."
                      % (sid, state, _now() - parked_since, verdict,
                         ("; " + jd) if jd else ""),
                      file=sys.stderr, flush=True)
                return sid, state, False
        else:
            parked_since = None                   # recovered — reset the grace
        if deadline and _now() >= deadline:       # wall-clock watchdog
            if pid:
                _kill_pid_group(pid)
            return sid, state, True
        time.sleep(poll_secs)


def _classify_exit_bg(final_state, timed_out):
    """B4 terminal rule re-derived for --bg (spike 1b): there is NO stdout `result`
    event and NO transcript `result` record, so terminal truth = the agents-row
    status the poll loop last saw. `timeout` wins; a session that reached `idle` is
    `ok`; a parked STALLED_AGENT_STATES status — including `blocked`, the state the
    five 444-17 launches sat in — is `stalled` (its own label for the ledger/notify,
    abnormal for accounting); anything else (`gone` before idle, an error status) is
    `crash`. -> (label, is_abnormal)."""
    if timed_out:
        return "timeout", True
    if final_state in STALLED_AGENT_STATES:
        return "stalled", True
    if final_state in IDLE_AGENT_STATES:
        return "ok", False
    return "crash", True


# A worker whose `claude agents --json` status is any of these is actively WORKING —
# NEVER reaped, even if it otherwise qualifies (a busy worker mid-turn is left alone).
# Anything else (idle / blocked / finished / an unknown parked status) is reapable
# ONCE the identity predicate below confirms it's genuinely THIS task's worker.
BUSY_AGENT_STATES = {"busy", "running", "working", "active", "in-progress", "in_progress"}


def _is_ts_worker_name(name):
    """True when `name` matches the task-station worker naming convention — the
    display name delegate spawns workers under: `task-station-<seq>-<ordinal>-<project>`
    (seq runs, <ordinal> = the spawning hub's roster number; the ordinal segment is
    absent on runs where it couldn't be resolved) or `wk-<project>-<worktree>` (no-seq
    worktree runs), each optionally `-<label>`. A session whose name matches neither is
    NOT a task-station worker and is never reaped (safety predicate (c)).

    This is a PREFIX test, and every naming variant keeps the `task-station-` / `wk-`
    prefix for exactly that reason — a format that moved the prefix would make the
    reaper blind to every worker it ever spawned."""
    return bool(name) and (name.startswith("task-station-") or name.startswith("wk-"))


def _remove_bg_session_file(worker_sid):
    """Best-effort: delete the ClaudeCode.app supervisor's session-store file for a
    `--bg` worker so it can't be RESPAWNED after its process group is killed (kill
    ALONE is insufficient — the supervisor restarts a killed --bg agent from this
    file, and reclaims no RAM). TWO store layouts are scanned, oldest first:

      * legacy flat  <config>/sessions/<name>.json — match on `sessionId`;
      * current nested  SESSIONS_STORE_ROOT/<org>/<user>/local_<uuid>.json —
        the file's own `sessionId` is the local_<uuid> filename; the id the
        agents list keys on is `cliSessionId`, so BOTH keys are matched
        (measured 2026-08-14: reaping matched 0 files until cliSessionId was
        read — the "reaped 40" that changed nothing).

    Prefix-tolerant in both directions (a stored id may be short). Removes at
    most one matching file. The store path/schema is Claude-Code-INTERNAL and may
    change — EVERYTHING here is guarded: a missing dir, a non-JSON/odd-schema
    file, or a remove that fails is a silent no-op. NEVER raises (a reaping
    helper must never block or break a task close)."""
    if not worker_sid:
        return

    def _match(sid):
        return (isinstance(sid, str) and sid and
                (sid == worker_sid or sid.startswith(worker_sid)
                 or worker_sid.startswith(sid)))

    try:
        names = os.listdir(SESSIONS_DIR)
    except Exception:
        names = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, fn)
        try:
            with open(path) as f:
                obj = json.load(f)
        except Exception:
            continue                                    # unreadable / non-JSON → skip
        if isinstance(obj, dict) and _match(obj.get("sessionId")):
            try:
                os.remove(path)
            except Exception:
                pass
            return
    # Nested current store. Walk shallowly (root/org/user/*.json) and stop at
    # the first match — one agent has one store file.
    try:
        walk = os.walk(SESSIONS_STORE_ROOT)
    except Exception:
        return
    for dirpath, _dirs, files in walk:
        for fn in files:
            if not fn.endswith(".json"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path) as f:
                    obj = json.load(f)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if _match(obj.get("cliSessionId")) or _match(obj.get("sessionId")):
                try:
                    os.remove(path)
                except Exception:
                    pass
                return


def reap_task_workers(seq, adapter=None, roster=None, current_sid=None,
                      only_sids=None):
    """Stop this task's still-LIVE `--bg` worker sessions when the task is closed, so
    finished/idle workers don't linger — or get RESPAWNED by the ClaudeCode.app
    supervisor — in Agent View (#464/#465).

    AIRTIGHT SAFETY — a session is reaped ONLY IF ALL of the following hold, so a real
    working/hub session is NEVER killed even if it mis-attached to a task that then
    closes:
      (a) it is in the delegate registry (workers.json) with `seq` == this task's seq
          (it was delegate-spawned FOR THIS task — roster attachment ALONE never
          qualifies a session), AND
      (b) it is role==worker in the caller's `roster` (task.session_meta) — never
          role==hub (hub sessions are the user's real sessions); a candidate absent
          from the roster, or present as a hub, is excluded, AND
      (c) its `claude agents --json` / roster name matches the task-station worker
          naming (`task-station-<seq>-…` / `wk-<project>-…`, see _is_ts_worker_name), AND
      (d) it is NOT busy/working in `claude agents --json` (only idle/blocked/finished
          is reapable; a busy worker mid-turn is left alone), AND
      (e) it is not the closing/current session (`current_sid`), AND
      (f) when `only_sids` is given, it is IN that set.
    The candidate set is therefore the INTERSECTION of the seq-matched registry and the
    role==worker roster — never the union, and never roster-attachment alone.

    `only_sids` NARROWS the candidate set and can never widen it: every rule above
    still has to hold. The SessionStart orphan sweep passes it because it has already
    decided WHICH of a task's workers are orphaned — reaping this task's other workers,
    whose hubs are still alive, would be exactly the bug it is trying to avoid.

    For each confirmed candidate: remove its session-store file FIRST (so the
    supervisor can't restart it), THEN SIGTERM→SIGKILL its process group when the
    agents row carries a pid (background rows usually don't — the store row IS the
    agent then). Returns the list of sids actually reaped (so the caller can ledger
    each `stop`).

    Wholly best-effort: the whole reap is gated OFF by config `reap_workers_on_done`
    (default ON) → []; an unimportable/failing adapter, an unreadable registry, a
    missing/odd session store, or a kill that raises must NEVER propagate — closing a
    task can't fail because reaping failed. Returns [] on any such failure."""
    reaped = []
    # Config kill-switch (#465): OFF → the reap is a complete no-op.
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import config
        if not config.reap_workers_on_done():
            return reaped
    except Exception:
        pass                                            # config unavailable → default ON
    seq_s = str(seq)
    roster = roster or {}
    try:
        adapter = adapter or harness.ClaudeAdapter()
    except Exception:
        return reaped
    # (a) candidate sids come ONLY from seq-matched registry entries — a session that
    # merely attached to the task (roster-only, not delegate-registered) is NOT a
    # candidate. Carry each entry's recorded name as a name-match fallback.
    try:
        reg = load_reg()
    except Exception:
        reg = {}
    only = set(only_sids) if only_sids is not None else None
    cands, seen = [], set()
    for e in reg.values():
        if str(e.get("seq")) != seq_s:
            continue
        sid = e.get("session_id")
        if only is not None and sid not in only:
            continue                                    # (f) explicit narrowing
        if sid and sid not in seen:
            seen.add(sid)
            cands.append((sid, e))
    if not cands:
        return reaped
    # One agents snapshot for the whole batch (avoids N subprocess calls).
    try:
        idx = adapter.agents_index()
    except Exception:
        return reaped
    if not isinstance(idx, dict):
        return reaped
    for sid, entry in cands:
        # (e) never the closing/current session.
        if current_sid and sid == current_sid:
            continue
        # (b) must be role==worker in the roster — excludes hubs AND any candidate the
        # roster doesn't vouch for as a worker (intersection, not union).
        rmeta = roster.get(sid) or {}
        if rmeta.get("role") != "worker":
            continue
        # Resolve the live agents row (tolerate a stored SHORT id via unique prefix).
        row = idx.get(sid)
        if row is None:
            hits = [r for s, r in idx.items() if s.startswith(sid)]
            row = hits[0] if len(hits) == 1 else None
        if not row:
            continue                                    # gone/pruned → nothing to kill
        full_sid = row.get("sessionId") or sid
        if current_sid and full_sid == current_sid:     # (e) again, on the full id
            continue
        # (c) name must match the task-station worker naming — agents-row name first,
        # then the roster name, then the registry entry's recorded name/label.
        name = (row.get("name") or rmeta.get("name")
                or entry.get("name") or entry.get("label"))
        if not _is_ts_worker_name(name):
            continue
        # (d) a busy/working worker is left alone. Background rows carry the state
        # in `state` (no `status`, no pid — the two agents-list row shapes), so
        # both keys are read; requiring a pid here is what let parked bg agents
        # accumulate forever (B4) — the store file IS the agent when pid is absent.
        status = str(row.get("status") or row.get("state") or "").strip().lower()
        if status in BUSY_AGENT_STATES:
            continue
        try:
            _remove_bg_session_file(full_sid)           # FILE FIRST — block a respawn…
            pids = [row["pid"]] if row.get("pid") else _find_agent_pids(full_sid)
            for pid in pids:                            # …THEN kill the process group(s)
                _kill_pid_group(pid)                    # (bg rows carry no pid in JSON)
            _mark_job_done(full_sid)                    # …THEN the rendered job record
            reaped.append(sid)
        except Exception:
            pass                                        # a kill failure never aborts the close
    return reaped


def _wip_commit(dirpath, reason, task):
    """Auto-checkpoint an abnormal worker's in-progress edits so a killed worker
    never leaves the worktree uncommitted (§4). No-op on a clean tree. NEVER
    pushes. Uses an explicit `-c` identity (harmless when repo config exists) and
    `--no-verify` so a repo pre-commit hook can't veto the checkpoint. On a
    commit failure after `add -A`, un-stage (`git reset`) so the tree isn't left
    half-staged. Any git failure is logged to stderr and swallowed — it must
    NEVER mask the worker's original error. Returns the short sha, or None."""
    if not dirpath:
        return None
    try:
        st = subprocess.run(["git", "-C", dirpath, "status", "--porcelain"],
                            capture_output=True, text=True)
        if st.returncode != 0:
            return None
        if not (st.stdout or "").strip():
            return None                     # clean tree → nothing to checkpoint
        add = subprocess.run(["git", "-C", dirpath, "add", "-A"],
                            capture_output=True, text=True)
        if add.returncode != 0:
            sys.stderr.write("[delegate] auto-WIP `git add` failed: %s\n"
                             % (add.stderr or "").strip())
            return None
        snippet = (task or "").replace("\n", " ").replace("\r", " ").strip()[:80]
        msg = "wip(delegate): auto-checkpoint on %s — %s" % (reason, snippet)
        commit = subprocess.run(
            ["git", "-C", dirpath,
             "-c", "user.name=task-station delegate",
             "-c", "user.email=delegate@task-station.local",
             "commit", "--no-verify", "-m", msg],
            capture_output=True, text=True)
        if commit.returncode != 0:
            subprocess.run(["git", "-C", dirpath, "reset"],
                          capture_output=True, text=True)   # un-stage the half-staged tree
            sys.stderr.write("[delegate] auto-WIP commit failed (tree un-staged): %s\n"
                             % (commit.stderr or "").strip())
            return None
        sha = subprocess.run(["git", "-C", dirpath, "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True)
        return (sha.stdout or "").strip() or None
    except Exception as e:
        sys.stderr.write("[delegate] auto-WIP commit error: %s\n" % e)
        return None


def _attached_seq():
    """The /todo task seq the CALLING (hub) session is attached to, or None.
    Read from CLAUDE_CODE_SESSION_ID (set in the worker's parent env) via
    `task-station.py whoami --porcelain`. Lets write work inherit the right seq so the
    worktree binding is deterministic without the hub remembering to pass it."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return None
    try:
        out = subprocess.run(["python3", TASK_STATION_PY, "whoami", "--porcelain",
                              "--session", sid], capture_output=True, text=True, timeout=20)
        # whoami --porcelain now prints '<seq>\t<seq>-<n>\t<kind>' (#463); field 1
        # is still the bare seq. Split on whitespace and take it, tolerating the
        # old single-field shape too.
        return ((out.stdout or "").strip().split() or [None])[0]
    except Exception:
        return None


def _whoami_porcelain(sid):
    """Raw `whoami --porcelain` stdout for `sid` — '<seq>\\t<seq>-<n>\\t<kind>' — or ""
    on any failure. Split out so this one subprocess call can be stubbed without
    reaching into the stdlib subprocess module."""
    try:
        out = subprocess.run(["python3", TASK_STATION_PY, "whoami", "--porcelain",
                              "--session", sid],
                             capture_output=True, text=True, timeout=20)
    except Exception:
        return ""
    return out.stdout or ""


def _spawner_ordinal(seq):
    """The roster ordinal (`<seq>-<n>` → n) of the hub session spawning this worker,
    or None when it can't be resolved.

    Read at spawn time from CLAUDE_CODE_SESSION_ID via `whoami --porcelain`. The label
    is trusted ONLY when its seq matches the task we are spawning for: an explicit
    `--seq` can name a different task than the caller's attached one, and borrowing
    that task's ordinal would misrecord the worker's provenance. A worker caller has no
    ordinal (field 2 is empty) → None.

    Returns None on ANY failure (no env var, no python3, odd output) so the caller
    falls back to the ordinal-free name rather than emitting a broken segment."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid or not seq:
        return None
    fields = _whoami_porcelain(sid).rstrip("\n").split("\t")
    if len(fields) < 2:
        return None
    head, _sep, tail = fields[1].strip().partition("-")
    if head != str(seq) or not tail.isdigit():
        return None
    return int(tail)


def _worker_name(seq, project, label, worktree, entry=None, resuming=False):
    """The worker's DISPLAY name — what `--name` hands the CLI, what Agent View shows,
    and what the reaper's identity predicate reads.

    `task-station-<seq>-<ordinal>-<project>[-<label>]`, where <ordinal> is the roster
    number of the hub session that SPAWNED this worker. The ordinal is PROVENANCE, not
    current ownership: on a resume the name already recorded on the registry entry is
    reused verbatim, so a different hub picking the worker up never renames it (the
    resuming actor is recorded separately, as the ledger's `actor_ordinal`).

    Degrades rather than emitting a broken name: no resolvable ordinal → today's
    `task-station-<seq>-<project>`, never an empty or doubled segment. The no-seq
    worktree form stays `wk-<project>-<worktree>`, and the `task-station-` form is
    preferred whenever a seq is available.

    The ordinal is inserted AFTER the seq so the `task-station-` prefix
    `_is_ts_worker_name` gates all reap logic on stays intact."""
    if resuming:
        recorded = (entry or {}).get("name")
        if recorded:
            return recorded                  # provenance is fixed at spawn time
    if seq:
        n = _spawner_ordinal(seq)
        name = ("task-station-%s-%s-%s" % (seq, n, project) if n is not None
                else "task-station-%s-%s" % (seq, project))
    else:
        name = ("wk-%s-%s" % (project, worktree)) if worktree else None
    if label:
        name = (name or project) + "-%s" % label
    return name


# streaming/liveness fields carried across an entry rebuild (pre-register sets
# them; a post-run refresh must NOT drop the heartbeat state).
_STREAM_KEYS = ("pid", "started_ts", "last_event_ts", "phase", "exit",
                "agent_state",     # last agents-list state observed (bg truth, B1)
                "report_path",     # durable child report artifact (B3)
                "trust_ok_ts",     # last successful trust preflight (B2)
                "grants_probed")   # grants surfaced once per slot (B2)


def _save_entry(reg, key, project, seq, label, dirpath, sid, model=None, name=None,
                **extra):
    """Persist a worker-slot registry entry. `model` (the requested model on
    pre-register, upgraded to the concrete id `claude -p` reports on refresh) is
    stored when truthy so `/todo` and `delegate list` can show what ran — absent
    on an account-default (empty-model) run, so guarded rather than always written.

    `spawner` (the hub session that launched this worker, from
    CLAUDE_CODE_SESSION_ID) is captured when present so the /todo session tree can
    nest the worker under its spawning hub. On a resume refresh where the env var
    is missing, an already-recorded spawner is preserved rather than dropped.

    `name` (the worker's display name, which encodes the spawning hub's ordinal) is
    carried forward the same way: the entry is rebuilt from scratch on every write, so
    a refresh that doesn't pass a name must not drop the recorded one — that name is
    what a later resume reuses to keep provenance stable.

    `**extra` writes streaming/liveness fields (pid/started_ts/exit at
    pre-register). The whole cycle runs under the registry lock and re-reads the
    reg fresh (B3), so it never clobbers a concurrent worker's entry; streaming
    state already on the entry (heartbeats) is carried forward across the rebuild
    unless `extra` overrides it. The passed `reg` snapshot is updated in place so
    the caller stays coherent."""
    with _reg_lock():
        fresh = load_reg()
        prev = fresh.get(key, {})
        entry = {"project": project, "seq": seq, "label": label,
                 "dir": dirpath, "session_id": sid, "ts": _now()}
        if model:
            entry["model"] = model
        spawner = os.environ.get("CLAUDE_CODE_SESSION_ID") or prev.get("spawner")
        if spawner:
            entry["spawner"] = spawner
        wname = name or prev.get("name")
        if wname:
            entry["name"] = wname
        for k in _STREAM_KEYS:               # carry forward heartbeat/liveness state
            if k in prev:
                entry[k] = prev[k]
        entry.update(extra)                  # explicit pre-register fields win
        fresh[key] = entry
        save_reg(fresh)
    reg[key] = entry


def _pick_model(obj):
    """Best model id for a worker run from a `claude -p --output-format json` blob:
    an explicit top-level `model` if present, else the heaviest entry in `modelUsage`
    (by total tokens). None when neither is present (older CLIs) so callers fall back
    to the requested model."""
    m = obj.get("model")
    if isinstance(m, str) and m:
        return m
    mu = obj.get("modelUsage")
    if isinstance(mu, dict) and mu:
        def _tot(v):
            if not isinstance(v, dict):
                return 0
            return sum(int(v.get(k) or 0) for k in
                       ("inputTokens", "outputTokens",
                        "cacheReadInputTokens", "cacheCreationInputTokens"))
        return max(mu, key=lambda k: _tot(mu[k]))
    return None


def _norm_usage(obj):
    """Normalise a result blob's `usage` into the task-runs shape
    {in,out,cache_read,cache_creation}. None when no `usage` object is present
    (older CLIs) so the run record simply omits token detail rather than lying with
    zeros."""
    u = obj.get("usage")
    if not isinstance(u, dict):
        return None

    def _int(k):
        try:
            return int(u.get(k) or 0)
        except (TypeError, ValueError):
            return 0
    return {"in": _int("input_tokens"), "out": _int("output_tokens"),
            "cache_read": _int("cache_read_input_tokens"),
            "cache_creation": _int("cache_creation_input_tokens")}


def _find_transcript(sid):
    """Locate a session transcript `<PROJECTS_ROOT>/<enc-cwd>/<sid>.jsonl` by walking
    the project buckets (the launch cwd is unknown here). None when absent/unreadable."""
    if not sid:
        return None
    try:
        buckets = os.listdir(PROJECTS_ROOT)
    except OSError:
        return None
    for b in buckets:
        p = os.path.join(PROJECTS_ROOT, b, sid + ".jsonl")
        if os.path.isfile(p):
            return p
    return None


def _transcript_usage_summary(sid):
    """Sum a worker transcript's `assistant`-message usage for the REPORTED cost
    channel under --bg (spike: there is NO stdout `result` event / transcript
    `result` record, so the old `_parse_result(total_cost_usd)` path is dead). Sums
    the same usage keys the DERIVED channel (usage.py) uses and prices each message
    via lib/pricing.message_cost — so the reported figure matches the derivation
    method, cache-read/write included.

    Returns (usage_dict|None, model|None, cost_usd|None): `usage_dict` is the add-cost
    `--usage-json` shape {in,out,cache_read,cache_creation}; `model` is the dominant-
    by-output-tokens model (mirrors _pick_model); `cost_usd` is the priced sum (None
    when pricing is unavailable or every message was an unknown/unpriced model). A
    missing/unreadable/empty transcript → (None, None, None). Best-effort — never
    raises."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import pricing as _pricing
    except Exception:
        _pricing = None
    path = _find_transcript(sid)
    if not path:
        return None, None, None
    agg = {}                       # model -> {in,out,cache_read,cache_creation,cost}
    total_cost, any_priced = 0.0, False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue                       # skip a malformed line mid-file
                if (not isinstance(obj, dict) or obj.get("type") != "assistant"
                        or obj.get("isSidechain")):
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                u = msg.get("usage") or {}
                model = msg.get("model") or "?"
                b = agg.setdefault(model, {"in": 0, "out": 0, "cache_read": 0,
                                           "cache_creation": 0, "cost": 0.0})
                b["in"] += u.get("input_tokens") or 0
                b["out"] += u.get("output_tokens") or 0
                b["cache_read"] += u.get("cache_read_input_tokens") or 0
                b["cache_creation"] += u.get("cache_creation_input_tokens") or 0
                if _pricing is not None:
                    c = _pricing.message_cost(msg.get("model"), u,
                                              _iso_to_epoch(obj.get("timestamp")))
                    if c is not None:
                        b["cost"] += c
                        total_cost += c
                        any_priced = True
    except OSError:
        return None, None, None
    if not agg:
        return None, None, None
    summed = {"in": sum(b["in"] for b in agg.values()),
              "out": sum(b["out"] for b in agg.values()),
              "cache_read": sum(b["cache_read"] for b in agg.values()),
              "cache_creation": sum(b["cache_creation"] for b in agg.values())}
    dominant = max(agg.items(), key=lambda kv: kv[1]["out"])[0]
    model = dominant if dominant != "?" else None
    cost = round(total_cost, 6) if any_priced else None
    return summed, model, cost


def _iso_to_epoch(s):
    """ISO-8601 timestamp → epoch seconds (float), or None — feeds the date-dependent
    pricing sheet. Tolerant of a trailing 'Z'."""
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _parse_result(out):
    """Pull (result_text, session_id, cost, model, usage) from a `claude -p
    --output-format json` blob, tolerating leading control chars / trailing lines
    some shells inject. `model`/`usage` are None on older CLIs that omit
    `modelUsage`/`usage` — never fabricated."""
    result_text, sid, cost, model, usage = out, None, None, None, None
    brace = out.find("{")
    if brace != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(out[brace:])
            if isinstance(obj, dict):
                result_text = obj.get("result", out)
                sid = obj.get("session_id")
                cost = obj.get("total_cost_usd")
                model = _pick_model(obj)
                usage = _norm_usage(obj)
        except Exception:
            pass
    return result_text, sid, cost, model, usage


# ---- B3: the durable child report ------------------------------------------
# The 3.0.0 migration's worktree HANDOFF-*.md files are the proven prototype:
# an untracked worktree-root artifact with the same named sections every time.
# delegate formalizes exactly that shape — the worker is CONTRACTED to write it,
# and when it doesn't (or the run was backgrounded, where stdout is lost —
# task-station-backgrounded-delegate-loses-report), the worker's final message
# is harvested into the same file so the report always survives the process.

REPORT_SECTIONS = ("What was done", "Deviations (each with WHY)",
                   "Gates run vs NOT run",
                   "Unverified (mandatory — every claim you could not verify; "
                   "write 'none' only if truly none)",
                   "Suspicious / decisions for the hub",
                   "What the next chunk inherits")

REPORT_CONTRACT = """

--- REPORT CONTRACT (task-station delegate) ---
Before you finish, WRITE your final report to %s (create or overwrite) with EXACTLY these sections:
%s
The FILE is the durable report — keep your final chat message to a short summary of it."""


def _report_slug(seq, label):
    raw = str(label or (seq if seq else "") or "worker")
    keep = "".join(c if (c.isalnum() or c in "._-") else "-" for c in raw)
    return keep.strip("-") or "worker"


def _report_path(dirpath, repo_root, seq, label, project=None):
    """Where the durable child report lives: the WORKTREE root (the HANDOFF
    prototype's home — untracked, survives the session, reviewable by the hub).
    A main-checkout (read-only) run must never drop files into the user's
    checkout, so its harvest lands under the data dir instead."""
    if _is_main_checkout(dirpath, repo_root):
        d = os.path.join(REG_DIR, "reports")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return os.path.join(d, "HANDOFF-REPORT-%s-%s.md"
                            % (project or os.path.basename(dirpath),
                               _report_slug(seq, label)))
    return os.path.join(dirpath, "HANDOFF-REPORT-%s.md" % _report_slug(seq, label))


def _with_report_contract(task, rpath):
    sections = "\n".join("## " + s for s in REPORT_SECTIONS)
    return task + (REPORT_CONTRACT % (rpath, sections))


def _transcript_final_text(sid):
    """The transcript's last assistant-message text — under --bg the only copy of
    the worker's final report (no stdout result event exists there). None when the
    transcript is absent/unreadable or holds no assistant text. Never raises."""
    p = _find_transcript(sid)
    if not p:
        return None
    try:
        with open(p, errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            txt = "\n".join(b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text").strip()
            if txt:
                return txt
    return None


def _persist_report(rpath, started_ts, sid, result_text=None):
    """Ensure the durable child report exists. A worker-authored file (mtime at or
    after this run's start) wins untouched; otherwise the worker's final message —
    the stdout result on the streaming path, the transcript tail under --bg — is
    harvested into the file WITH a banner saying so. Returns (path_or_None, how)
    where how ∈ worker-authored | harvested | none. Best-effort: never raises."""
    try:
        if os.path.isfile(rpath) and os.path.getmtime(rpath) >= (started_ts or 0):
            return rpath, "worker-authored"
    except OSError:
        pass
    source = "result event"
    text = (result_text or "").strip()
    if not text:
        source, text = "job record", (_job_result(sid) or "").strip()
    if not text:
        source, text = "session transcript", (_transcript_final_text(sid) or "").strip()
    if not text:
        return (rpath if os.path.isfile(rpath) else None), "none"
    banner = ("# HANDOFF (delegate-harvested)\n\n"
              "> The worker did not write the contracted report file; this is its\n"
              "> final message, harvested from the %s by delegate so the report\n"
              "> survives backgrounding. session: %s · %s\n\n"
              % (source, sid or "?", time.strftime("%Y-%m-%d %H:%M")))
    try:
        with open(rpath, "w", encoding="utf-8") as f:
            f.write(banner + text + "\n")
        return rpath, "harvested"
    except OSError:
        return None, "none"


def _post_worker_event(seq, project, label, sid, ok, result_text):
    """Best-effort: append a `worker` event to the /todo task's feed so a resumed/
    attached session learns a delegated run finished or failed. Fired via WS1's
    `add-event` CLI over subprocess — the SAME pattern as the add-cost write-back
    (`cmd_run`), so a sibling tree without that subcommand (non-zero exit / unknown
    arg) is swallowed exactly like the other post-run write-backs. No `seq` → no
    task to attribute to → no-op.

    Text = `worker finished|failed: <project>[:<label>] — <≤160-char result snippet
    (newlines flattened)>`, hard-capped to 200 chars (the receiving `add_event`
    additionally trims to EVENT_TEXT_MAX)."""
    if not seq:
        return
    verb = "finished" if ok else "failed"
    snippet = (result_text or "")[:160].replace("\n", " ")
    txt = ("worker %s: %s%s — %s"
           % (verb, project, (":" + label) if label else "", snippet))[:200]
    cmd = ["python3", TASK_STATION_PY, "add-event", "--task", str(seq),
           "--kind", "worker", "--text", txt]
    if sid:
        cmd += ["--session", sid]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        pass


def _ledger(seq, action, worker_sid, detail=None):
    """Best-effort post of one hub↔worker interaction to the task's provenance
    ledger (#463 add-ledger CLI). The acting HUB session is read from
    CLAUDE_CODE_SESSION_ID so any hub sees which hub spawned/resumed/finished the
    worker. No `seq` → no task → no-op; same swallow-everything discipline as the
    other write-backs."""
    if not seq:
        return
    cmd = ["python3", TASK_STATION_PY, "add-ledger", "--task", str(seq),
           "--action", action]
    if worker_sid:
        cmd += ["--worker", worker_sid]
    actor = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if actor:
        cmd += ["--session", actor]
    if detail:
        cmd += ["--detail", detail[:160]]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        pass


def _post_add_cost(seq, worker_sid, seq_label, category):
    """Reported-channel cost write-back for a --bg worker: sum the transcript's
    assistant-message usage, price it (lib/pricing), and post add-cost with the right
    `--category` — `real` on a clean finish, `wasted` on crash/timeout (RESOLVED #4:
    burned tokens are recorded, never skipped, never folded into real spend). Fires
    whenever ANY usage/cost is found (an unpriced run still records its tokens with
    cost None). No `seq` → no task → no-op; best-effort, swallowed."""
    if not seq:
        return
    usage, model, cost = _transcript_usage_summary(worker_sid)
    if usage is None and cost is None:
        return
    cmd = ["python3", TASK_STATION_PY, "add-cost", "--task", str(seq),
           "--usd", str(cost) if cost is not None else "0",
           "--category", category]
    if model:
        cmd += ["--model", model]
    if worker_sid:
        cmd += ["--session", worker_sid]
    if usage:
        cmd += ["--usage-json", json.dumps(usage)]
    if seq_label:
        cmd += ["--seq-label", seq_label]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        pass


def _register_worker(seq, sid, name, model, harness_name, status):
    """Best-effort roster write-through: record the worker session on the task
    record (name/model/harness/status) via the #463 register-worker-session CLI, so
    /todo detail + brief show the worker with its live status. No `seq`/`sid` → no-op."""
    if not seq or not sid:
        return
    cmd = ["python3", TASK_STATION_PY, "register-worker-session",
           "--task", str(seq), "--session", sid, "--status", status]
    if name:
        cmd += ["--name", name]
    if model:
        cmd += ["--model", model]
    if harness_name:
        cmd += ["--harness", harness_name]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        pass


def _bg_permission_mode(dirpath):
    """The --permission-mode for a `--bg` worker. DEFAULT `dontAsk` — fail-closed:
    non-allowlisted tools are auto-denied so an unattended worker never hangs on a
    prompt, with NO --dangerously-skip-permissions (the author-only edit toolset is
    granted via --allowedTools in harness.ClaudeAdapter.spawn_cmd). `bypassPermissions`
    is OPT-IN only — config.delegate_bypass_permissions() (default OFF, needs the
    one-time disclaimer) AND enforced worktree-only. Never `acceptEdits`: under an
    unattended `--bg` session acceptEdits HANGS on a non-edit permission prompt."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import config
        on = config.delegate_bypass_permissions()
    except Exception:
        on = False
    return "bypassPermissions" if (on and _under_worktrees(dirpath)) else "dontAsk"


def _worktree_hook():
    """Import lib/board/worktree_hook lazily. Delegate must stay importable when
    the board plane is absent/broken — the preflight then degrades to a warning
    instead of blocking the launch."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from board import worktree_hook
    return worktree_hook


def _trust_state(dirpath):
    """Whether ~/.claude.json marks `dirpath` trusted: True/False, or None when
    the file is missing/unreadable/odd-schema (unknowable ≠ untrusted)."""
    try:
        wh = _worktree_hook()
        with open(wh.claude_json_path(None), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        e = (data.get("projects") or {}).get(dirpath)
        return bool(isinstance(e, dict) and e.get("hasTrustDialogAccepted") is True)
    except Exception:
        return None


def _effective_grants(dirpath):
    """Probe the permission grants a worker launched in `dirpath` will ACTUALLY
    get: the merged permissions.allow/deny of the user settings file, the dir's
    checked-in .claude/settings.json, and its gitignored
    .claude/settings.local.json (the file worktree provisioning copies). Returns
    {"allow": [...], "deny": [...], "sources": [(label, path, n_allow)],
    "missing": ["label:path", ...]}.

    A probe of the settings surface, not a full simulation — managed policy and
    per-launch --allowedTools are not visible here. Across the 13 3.0.0-migration
    worker sessions the granted set varied wildly while every brief guessed at it
    (444-19); this makes the real set printable ONCE so the hub can put the truth
    in the brief."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
    files = [("user", os.path.join(cfg, "settings.json")),
             ("project", os.path.join(dirpath, ".claude", "settings.json")),
             ("local", os.path.join(dirpath, ".claude", "settings.local.json"))]
    allow, deny, sources, missing = [], [], [], []
    for label, p in files:
        if not os.path.isfile(p):
            missing.append("%s:%s" % (label, p))
            continue
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            perms = (doc.get("permissions") or {}) if isinstance(doc, dict) else {}
            a = [x for x in (perms.get("allow") or []) if isinstance(x, str)]
            d = [x for x in (perms.get("deny") or []) if isinstance(x, str)]
        except Exception:
            missing.append("%s:%s (unreadable)" % (label, p))
            continue
        sources.append((label, p, len(a)))
        allow += [x for x in a if x not in allow]
        deny += [x for x in d if x not in deny]
    return {"allow": allow, "deny": deny, "sources": sources, "missing": missing}


def _preflight_launch(dirpath, entry):
    """B2: trust + grant preflight, run before EVERY launch. An untrusted dir
    doesn't prompt under --bg/dontAsk — Claude Code ignores the dir's allowlists
    entirely and the worker parks in agents state 'blocked' with no prompt
    anywhere (the 444-17 failure). So: verify the ~/.claude.json trust entry,
    repair it when absent, ALERT when a previously-verified entry has been wiped
    (that means something rewrote ~/.claude.json), and surface the probed grant
    set once per worker slot so the hub can brief the worker with the REAL
    toolset. Returns registry fields to persist ({} on a fully failed probe);
    never raises and never blocks the launch."""
    fields = {}
    prior = (entry or {}).get("trust_ok_ts")
    trusted = _trust_state(dirpath)
    if trusted is not True:
        added = False
        try:
            added = _worktree_hook().add_trust_entry(dirpath)
        except Exception:
            pass
        trusted = _trust_state(dirpath)
        if trusted:
            if prior:
                print("delegate: TRUST WIPE — the ~/.claude.json trust entry for "
                      "%s was verified %s ago but is gone now (something rewrote "
                      "~/.claude.json); re-added." % (dirpath, _fmt_age(_now() - prior)),
                      file=sys.stderr)
            elif added:
                print("delegate: added the ~/.claude.json trust entry for %s"
                      % dirpath, file=sys.stderr)
        else:
            print("delegate: WARNING — %s is UNTRUSTED and the repair failed "
                  "(~/.claude.json missing or unwritable). The worker will very "
                  "likely park in agents state 'blocked': untrusted dirs ignore "
                  "every allowlist. Open the dir interactively once, or fix "
                  "~/.claude.json." % dirpath, file=sys.stderr)
    if trusted:
        fields["trust_ok_ts"] = _now()
    # The repo's own .mcp.json needs a one-time per-project approval that a
    # headless worker cannot answer — it parks in 'blocked' on the dialog (the
    # 444-17 class, root-caused via the job record's `needs` field). Settle it
    # for exactly what the target repo declares, respecting an explicit choice.
    try:
        approved = _worktree_hook().approve_project_mcp(dirpath)
        if approved:
            print("delegate: pre-approved this tree's project MCP server(s) so the "
                  "worker can't park on the approval dialog: %s (declared by "
                  "%s/.mcp.json; wrote enableAllProjectMcpServers to its "
                  ".claude/settings.local.json)" % (", ".join(approved), dirpath),
                  file=sys.stderr)
    except Exception:
        pass
    if not (entry or {}).get("grants_probed"):
        g = _effective_grants(dirpath)
        srcs = ", ".join("%s(%d)" % (label, n) for label, _, n in g["sources"]) or "none"
        print("delegate: worker grants probe — allow[%d]: %s%s · deny[%d] · sources: %s"
              % (len(g["allow"]), ", ".join(g["allow"][:12]),
                 " …" if len(g["allow"]) > 12 else "", len(g["deny"]), srcs),
              file=sys.stderr)
        if g["missing"]:
            print("delegate:   no grant file at: %s" % "; ".join(g["missing"]),
                  file=sys.stderr)
        fields["grants_probed"] = _now()
    return fields


def _under_worktrees(dirpath):
    """True when `dirpath` is inside a `<repo>-worktrees/` sandbox tree (any path
    segment ending in '-worktrees')."""
    parts = os.path.abspath(dirpath or "").split(os.sep)
    return any(p.endswith("-worktrees") for p in parts)


# ------------------------------------------------------------ notifications ----
# Worker-lifecycle notifications: a fire-and-forget signal when a delegated run
# finishes OK or fails/times out. TWO independent channels, each opt-in via config
# (lib/config.py): a macOS banner (notify=on) and a webhook POST (notify_webhook
# set). EVERYTHING here is best-effort — a missing config, absent osascript, or a
# dead webhook can NEVER break a delegation, so every branch is guarded/swallowed.


def _osa_quote(s):
    """A double-quoted AppleScript string literal for `s` — backslashes and quotes
    escaped so an arbitrary repo/label can't break out of the osascript one-liner."""
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _notify_settings():
    """(notify_on, webhook_url) read from config — guarded so a missing/broken
    config just disables notifications rather than raising into the run."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import config
        return config.notify_enabled(), config.notify_webhook()
    except Exception:
        return False, None


def _macos_notify(event, project, label):
    """Fire-and-forget macOS banner: `display notification "<repo>/<label>:
    finished|failed"` titled "task-station worker". Darwin-only; spawned detached
    with output discarded and every error swallowed (a notification never blocks)."""
    if sys.platform != "darwin":
        return
    verb = "finished" if event == "worker_finished" else "failed"
    who = ("%s/%s" % (project, label)) if label else project
    body = "%s: %s" % (who, verb)
    script = "display notification %s with title %s" % (
        _osa_quote(body), _osa_quote("task-station worker"))
    try:
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _webhook_notify(url, event, seq, project, label, worktree, cost,
                    sid=None, name=None):
    """POST the event as a plain-JSON body to `url` (Slack/Teams/ntfy-style
    receivers all accept this). 3s timeout; any failure is reported to stderr and
    swallowed — a down webhook must never break delegation."""
    import urllib.request
    payload = {
        "event": event,
        "task_seq": seq,
        "repo": project,
        "label": label,
        "worktree": worktree,
        "cost_usd": cost,
        "ts": _now(),
        # Agent-View provenance (#463): which worker, and which hub acted.
        "worker_session": sid,
        "worker_name": name,
        "actor_session": os.environ.get("CLAUDE_CODE_SESSION_ID"),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).close()
    except Exception as e:
        sys.stderr.write("[delegate] notify webhook failed: %s\n" % e)


def notify_event(event, seq, project, label, worktree, cost, sid=None, name=None):
    """Dispatch a worker-lifecycle notification (`worker_finished`/`worker_failed`)
    on every configured channel. Wholly best-effort — each channel is separately
    guarded so a broken one never affects the other or the caller. `sid`/`name`
    identify the worker session for the webhook's Agent-View provenance (#463); the
    macOS banner text is unchanged."""
    on, webhook = _notify_settings()
    if on:
        try:
            _macos_notify(event, project, label)
        except Exception:
            pass
    if webhook:
        try:
            _webhook_notify(webhook, event, seq, project, label, worktree, cost,
                            sid, name)      # positional: keeps *a-style callers/mocks working
        except Exception:
            pass


def _resolve_dir_from_args(a):
    """Resolve repo root from either --repo or --project on the parsed args."""
    repo = getattr(a, "repo", None)
    project = getattr(a, "project", None)
    if repo:
        return resolve_dir(repo=repo)
    return resolve_dir(project=project)


def _is_main_checkout(dirpath, repo_root):
    """True when dirpath IS the repo's main checkout (not a <repo>-worktrees/ tree).
    Write work never lives in the main checkout, so a worktree-slot worker that
    points here is a stale/clobbered entry."""
    if not dirpath:
        return False
    try:
        return os.path.realpath(dirpath) == os.path.realpath(repo_root)
    except Exception:
        return dirpath.rstrip("/") == repo_root.rstrip("/")


def _maybe_inherit_seq(a):
    """Bind a no-seq delegation to the calling session's attached /todo task, so a
    resume self-routes to the right worktree worker even when --seq AND --worktree
    are both omitted. Runs for read-only and write delegations alike; --solo opts
    out for genuine ad-hoc work unrelated to the attached task."""
    if a.seq or a.solo:
        return
    inherited = _attached_seq()
    if inherited:
        a.seq = inherited
        sys.stderr.write("[delegate] inheriting --seq %s from the attached session "
                         "(pass --solo for ad-hoc work unrelated to that task).\n"
                         % inherited)


def _select_slot(a, project, repo_root, reg):
    """Pick the registry KEY for this call and return (key, entry).

    Worktree workers and read-only/main-checkout workers are kept in SEPARATE
    slots for a tracked seq:
      - `seq:project`        — the canonical WORKTREE worker (write work)
      - `seq:project@main`   — a read-only worker that ran in the main checkout
    so a read-only (no---worktree) run can NEVER clobber the worktree binding,
    and a no---worktree RESUME self-routes to the worktree worker. (--label
    suffixes both, for a second concurrent tree in the same task.)

    Raises SystemExit when the canonical seq slot is left pointing at the main
    checkout (a stale / pre-1.14.2 clobbered entry) and no --worktree was given to
    rebind it — refusing rather than silently resuming stale main-checkout context.

    Un-tracked (no --seq) ad-hoc calls keep the original project[@worktree] keying."""
    seq, label = a.seq, a.label
    if not seq:
        key = ("%s@%s" % (project, a.worktree)) if a.worktree else project
        if label:
            key += ":%s" % label
        return key, reg.get(key, {})
    suf = (":%s" % label) if label else ""
    wk_key = "%s:%s%s" % (seq, project, suf)            # worktree worker (canonical seq slot)
    main_key = "%s:%s@main%s" % (seq, project, suf)     # read-only / main-checkout worker
    if a.worktree:
        return wk_key, reg.get(wk_key, {})
    wk_entry = reg.get(wk_key, {})
    if not a.fresh and wk_entry.get("session_id"):
        if _is_main_checkout(wk_entry.get("dir"), repo_root):
            raise SystemExit(
                "delegate: the saved worker for %r points at the MAIN checkout\n"
                "  %s\n"
                "  — a stale or pre-1.14.2 clobbered entry (write work never lives in the "
                "main checkout).\n  Refusing to resume there. Pass --worktree <name> to bind "
                "an isolated tree (the worker rebinds to it), or --fresh to start over."
                % (wk_key, wk_entry.get("dir")))
        return wk_key, wk_entry          # prefer the existing worktree worker
    return main_key, reg.get(main_key, {})   # no worktree worker → read-only @main slot


def _orchestrator_guard(seq, force=False, session=None):
    """B6 — REFUSE to delegate from a task flagged orchestrator-only, naming the child
    that should own the work instead.

    The rule was prose twice and broken twice on 2026-08-11, both times the same way: a
    hub session sitting on the parent delegated from the parent, so the work landed with
    no child owning it and nothing for the gate to grade. Prose could not enforce it.

    The VERDICT is task-station's (`orchestrator-check` — it needs the wave computation,
    and there must be exactly one of those), so this is a subprocess call, exactly like
    every other cross-plane question delegate asks. THE GUARD FAILS OPEN: any failure to
    reach the checker — a missing interpreter, a timeout, an unexpected exit code —
    returns silently. A guard that blocked delegation because it could not run would be
    worse than the rule being unenforced.

    `--force` is a DELIBERATE, RECORDED override rather than a silent one: it prints the
    refusal it is overriding and writes an event onto the task, so the exception is in
    the record where the next reader will find it."""
    if not seq:
        return
    try:
        out = subprocess.run(["python3", TASK_STATION_PY, "orchestrator-check",
                              "--task", str(seq)],
                             capture_output=True, text=True, timeout=30)
    except Exception:
        return
    if out.returncode != 3:
        return
    message = (out.stdout or "").strip() or (
        "delegate run refused: task #%s is flagged orchestrator-only." % seq)
    if not force:
        raise SystemExit(message)
    sys.stderr.write("delegate: --force overriding the orchestrator guard on #%s.\n%s\n"
                     % (seq, message))
    try:
        subprocess.run(["python3", TASK_STATION_PY, "add-event", "--task", str(seq),
                        "--kind", "log", "--text",
                        "delegate --force: overrode the orchestrator-only guard"]
                       + (["--session", session] if session else []),
                       capture_output=True, text=True, timeout=20)
    except Exception:
        pass


def cmd_run(a):
    # Bind a no-seq delegation to the calling session's attached /todo task so the
    # worktree worker is found even when --seq/--worktree are omitted (--solo opts out).
    _maybe_inherit_seq(a)
    # B6: the orchestrator guard runs FIRST — before the adapter, the repo resolution and
    # every side effect. A delegation from an orchestrator-only task is wrong no matter
    # how the repo resolves, and answering "which repo did you mean" before "this task
    # must not hold work" would make the reader fix the wrong thing.
    _orchestrator_guard(a.seq, force=getattr(a, "force", False),
                        session=getattr(a, "session", None))
    # Resolve the harness adapter (Phase 2 seam). ClaudeAdapter is the default and
    # behaves exactly as today; the registry/task record stay authoritative.
    adapter = harness.get_adapter(getattr(a, "harness", None))
    repo_root = _resolve_dir_from_args(a)
    project = os.path.basename(repo_root)          # key/name stay the repo's
    seq, label = a.seq, a.label

    # Pick the registry slot. Worktree workers and read-only/main-checkout workers
    # live in SEPARATE slots (see _select_slot), so a read-only run can never clobber
    # a worktree binding and a no---worktree resume self-routes to the worktree worker.
    reg = load_reg()
    key, entry = _select_slot(a, project, repo_root, reg)
    sid = None if a.fresh else entry.get("session_id")
    # Worker DISPLAY name (independent of the registry key above) — resolved AFTER the
    # slot so a RESUME can reuse the name already recorded there, keeping the spawning
    # hub's ordinal stable no matter which hub resumes the worker.
    name = _worker_name(seq, project, label, a.worktree, entry=entry, resuming=bool(sid))
    saved_dir = entry.get("dir")
    requested_model = getattr(a, "model", "sonnet")   # persisted pre-run; upgraded to the concrete id post-run

    # Decide the worker's cwd. On RESUME the worktree it was created in is the
    # source of truth — we never silently relocate a resumed session, and never
    # auto-recreate a removed worktree under the guise of a resume.
    if sid and saved_dir:
        if a.worktree and os.path.basename(saved_dir.rstrip("/")) != a.worktree:
            raise SystemExit(
                "delegate: worker %r is pinned to %s but --worktree=%s requests a "
                "different tree.\n  Refusing to resume it elsewhere. Use --fresh for a "
                "new worker, or drop --worktree to resume in place."
                % (key, saved_dir, a.worktree))
        if not os.path.isdir(saved_dir):
            raise SystemExit(
                "delegate: worker %r was created in %s, which no longer exists "
                "(worktree removed?).\n  Not recreating it silently — use --fresh to "
                "start a new worker." % (key, saved_dir))
        dirpath = saved_dir
    else:
        # New worker: resolve-or-create its worktree now (base auto-detected if not given).
        dirpath = (resolve_worktree(repo_root, a.worktree, branch=a.branch,
                                    base=a.base) if a.worktree else repo_root)

    # B2: trust + grant preflight — before EVERY launch path. An untrusted dir
    # doesn't prompt under --bg/dontAsk; the worker just parks in agents state
    # 'blocked' with no prompt anywhere (444-17). Repair + alert happen here.
    preflight_fields = _preflight_launch(dirpath, entry)
    if preflight_fields and key in reg:
        _touch_heartbeat(key, **preflight_fields)
    # B3: the durable child report — contract the worker to write it, and fix
    # where it lands so every exit path below can harvest into the same file.
    report_file = _report_path(dirpath, repo_root, seq, label, project=project)
    task_text = _with_report_contract(a.task, report_file)
    run_started = _now()

    # ---- --bg background-worker lifecycle (claude harness; #463) -------------
    # Adapters WITHOUT bg (Codex until Phase 6) fall through to the legacy `-p`
    # streaming path below. Under --bg the id is NOT chooseable (--session-id is
    # ignored; the agent mints + prints its own), so registration happens INSIDE
    # run_worker_bg via on_launch, right after the launch print.
    if adapter.supports_bg:
        resume = bool(sid)
        pmode = _bg_permission_mode(dirpath)

        def _on_launch(new_sid):
            _save_entry(reg, key, project, seq, label, dirpath, new_sid,
                        model=requested_model, name=name, bg=True, harness=adapter.name,
                        pid=None, started_ts=_now(), exit=None, agent_state="launched",
                        **preflight_fields)
            _register_worker(seq, new_sid, name, requested_model, adapter.name, "running")
            _ledger(seq, "resume" if resume else "spawn", new_sid,
                    detail="%s%s" % (project, (":" + label) if label else ""))

        sid, final_state, timed_out = run_worker_bg(
            adapter, dirpath, task_text, session_id=sid, resume=resume,
            timeout=a.timeout, name=name, model=requested_model, key=key,
            permission_mode=pmode, on_launch=_on_launch,
            stall_grace=getattr(a, "stall_grace", 45))
        final_sid = sid
        exit_label, abnormal = _classify_exit_bg(final_state, timed_out)
        _save_entry(reg, key, project, seq, label, dirpath, sid,
                    model=requested_model, name=name, bg=True, harness=adapter.name,
                    agent_state=final_state)

        if abnormal:
            # Auto-WIP the WORKTREE only — never the main checkout.
            sha = None if _is_main_checkout(dirpath, repo_root) \
                else _wip_commit(dirpath, exit_label, a.task)
            # B3: even an abnormal exit keeps whatever final text exists — a
            # stalled/crashed worker's partial report still beats stdout that no
            # one captured.
            rp, rhow = _persist_report(report_file, run_started, sid)
            _touch_heartbeat(key, pid=None, exit=exit_label,
                             phase=("auto-wip %s" % sha) if sha else exit_label,
                             **({"report_path": rp} if rp else {}))
            _register_worker(seq, sid, name, requested_model, adapter.name, exit_label)
            _ledger(seq, exit_label, sid)
            # Crashed/timed-out tokens still cost money → record them in the WASTED
            # bucket (separate from real spend) so accounting stays accurate (#4).
            _post_add_cost(seq, sid, label, "wasted")
            wip_feed = (" (auto-WIP %s)" % sha) if sha else ""
            wip_note = ("\n  auto-WIP commit %s in %s (not pushed)." % (sha, dirpath)) if sha else ""
            _post_worker_event(seq, project, label, sid, False,
                               "%s%s" % (exit_label, wip_feed))
            notify_event("worker_failed", seq, project, label, dirpath, None,
                         sid=sid, name=name)
            raise SystemExit(
                "delegate: worker %s — session %s saved in %s; resume with the same "
                "--seq/--project, or `delegate adopt` from another hub.%s%s"
                % (exit_label, sid, dirpath, wip_note,
                   ("\n  report: %s (%s)." % (rp, rhow)) if rp else ""))

        # OK: the agent reached `idle` (turn complete). The REPORTED cost channel
        # (transcript-usage sum) lands in Phase 5 — there is no stdout result event
        # under --bg; the DERIVED usage ledger (usage.py scan) already prices the
        # worker transcript unchanged, so per-task cost is not lost.
        #
        # Same clean-finish probe as the streaming path below: a worker that exits
        # cleanly having committed nothing is REPORTED, never auto-committed. The
        # _save_entry above already dropped any count from a previous run.
        uncommitted = None if _is_main_checkout(dirpath, repo_root) \
            else _uncommitted_total(dirpath)
        unc_phrase = _uncommitted_phrase(uncommitted)
        # B3: secure the durable report — worker-authored file honored, else the
        # transcript tail is harvested (the bg channel's only copy of the report).
        rp, rhow = _persist_report(report_file, run_started, sid)
        extra_hb = {}
        if unc_phrase:
            extra_hb["uncommitted"] = uncommitted
        if rp:
            extra_hb["report_path"] = rp
        _touch_heartbeat(key, pid=None, exit="ok", phase="idle", **extra_hb)
        _register_worker(seq, sid, name, requested_model, adapter.name, "ok")
        _ledger(seq, "finish", sid,
                detail="%s%s" % (project, (":" + label) if label else ""))
        if seq:
            try:
                subprocess.run(["python3", TASK_STATION_PY, "add-project",
                                "--task", str(seq), "--project", project],
                               capture_output=True, text=True, timeout=20)
            except Exception:
                pass
            if a.worktree:
                try:
                    subprocess.run(["python3", TASK_STATION_PY, "status",
                                    "--task", str(seq), "active"],
                                   capture_output=True, text=True, timeout=20)
                except Exception:
                    pass
            # Reported per-worker cost, sourced from the transcript (no result event
            # under --bg) and priced per-model incl. cache tokens — REAL-work bucket.
            _post_add_cost(seq, sid, label, "real")
            _post_worker_event(seq, project, label, sid, True,
                               "%sbackgrounded worker finished"
                               % (("%s — " % unc_phrase) if unc_phrase else ""))
        banner = _uncommitted_banner(dirpath, uncommitted)
        if banner:
            print(banner, file=sys.stderr)   # no stdout result under --bg
        foot = "— worker '%s'  dir: %s" % (key, dirpath)
        if sid:
            foot += "  session: %s  (resume: claude --resume %s)" % (sid, sid)
        if name:
            foot += "  attach: Agent View → %s" % name
        if rp:
            foot += "  report: %s (%s)" % (rp, rhow)
        if unc_phrase:
            foot += "  !! %s" % unc_phrase
        print(foot, file=sys.stderr)
        notify_event("worker_finished", seq, project, label, dirpath, None,
                     sid=sid, name=name)
        return

    # Launch. A resume reattaches to the existing id; a brand-new worker gets a
    # UUID we choose AND PRE-REGISTER before launching, so a mid-run kill (timeout
    # or SIGKILL) still leaves the session on disk under a known, resumable id —
    # the next same-key delegate call reattaches to it instead of losing the chat.
    # run_worker now STREAMS and returns the (B4) contract
    # (returncode, result_event_json_or_None, stderr_text, timed_out).
    rc = timed_out = None
    result_json = stderr_text = None
    if sid:
        _register_worker(seq, sid, name, requested_model, adapter.name, "running")
        _ledger(seq, "resume", sid,
                detail="%s%s" % (project, (":" + label) if label else ""))
        rc, result_json, stderr_text, timed_out = run_worker(
            dirpath, task_text, session_id=sid, resume=True, timeout=a.timeout,
            name=name, model=requested_model, key=key, adapter=adapter)
        # Resume that couldn't even start (nonzero, no events, not a timeout) →
        # the saved id is unresumable; start a fresh pre-registered worker. A
        # partial feed may already have streamed to stderr (harmless — stderr).
        if rc != 0 and result_json is None and not timed_out:
            sys.stderr.write("[delegate] resume of %s failed; starting a fresh "
                             "pre-registered worker.\n" % sid)
            sid = None
    if not sid:
        sid = str(uuid.uuid4())
        _save_entry(reg, key, project, seq, label, dirpath, sid,
                    model=requested_model, name=name, harness=adapter.name,
                    pid=None, started_ts=_now(), exit=None)  # pre-register
        _register_worker(seq, sid, name, requested_model, adapter.name, "running")
        _ledger(seq, "spawn", sid,
                detail="%s%s" % (project, (":" + label) if label else ""))
        rc, result_json, stderr_text, timed_out = run_worker(
            dirpath, task_text, session_id=sid, resume=False, timeout=a.timeout,
            name=name, model=requested_model, key=key, adapter=adapter)

    result_text, echoed_sid, cost, result_model, usage = _parse_result(result_json or "")
    run_model = result_model or requested_model     # concrete id when the CLI reports it, else what we asked for
    exit_label, abnormal = _classify_exit(rc, result_json, timed_out)

    # Abnormal exit (timeout, non-zero, OR streamed-then-died / no terminal
    # result — B4). Auto-WIP-commit the worktree FIRST (after the process group
    # is fully killed by run_worker's watchdog, so no live grandchild is still
    # writing), then record the terminal state, feed, notify, and exit.
    if abnormal:
        # Auto-WIP the WORKTREE only — never the main checkout (a read-only run's
        # cwd), where it would sweep the user's own uncommitted work into a commit.
        sha = None if _is_main_checkout(dirpath, repo_root) \
            else _wip_commit(dirpath, exit_label, a.task)
        # B3: keep whatever final text exists even on an abnormal exit.
        rp, rhow = _persist_report(report_file, run_started, sid,
                                   result_text=(result_text if result_json else None))
        _touch_heartbeat(key, pid=None, exit=exit_label,
                         phase=("auto-wip %s" % sha) if sha else "abnormal exit",
                         **({"report_path": rp} if rp else {}))
        _register_worker(seq, sid, name, run_model, adapter.name, exit_label)
        _ledger(seq, exit_label, sid)
        wip_feed = (" (auto-WIP %s)" % sha) if sha else ""
        wip_note = ("\n  auto-WIP commit %s in %s (not pushed)." % (sha, dirpath)) if sha else ""
        err = (stderr_text or "").strip()
        if timed_out:
            _post_worker_event(seq, project, label, sid, False, "timed out%s" % wip_feed)
            notify_event("worker_failed", seq, project, label, dirpath, cost,
                         sid=sid, name=name)
            raise SystemExit(
                "delegate: worker timed out after %ss — session %s saved in %s, resume "
                "with the same --seq/--project.%s\n%s"
                % (a.timeout, sid, dirpath, wip_note, err))
        _post_worker_event(seq, project, label, sid, False,
                           "%s%s" % (err[:120] or "crashed", wip_feed))
        notify_event("worker_failed", seq, project, label, dirpath, cost,
                     sid=sid, name=name)
        raise SystemExit(
            "delegate: worker failed (exit %s) — session %s saved in %s, resume "
            "with the same --seq/--project.%s\n%s"
            % (rc, sid, dirpath, wip_note, err))

    # `claude` echoes the session id; honor it if it ever differs from ours
    # (e.g. a forked session) so the registry tracks the real on-disk session.
    if echoed_sid and echoed_sid != sid:
        sys.stderr.write("[delegate] worker reported session %s (expected %s); "
                         "tracking the reported one.\n" % (echoed_sid, sid))
        sid = echoed_sid
    final_sid = sid
    # A clean finish that left the worktree dirty is its own terminal state. The
    # incident: a worker authored three features + 57 tests, exited ok, committed
    # NOTHING, and `status` said `finished (ok)` — the work was found only by a human
    # running `git log origin/main..HEAD`. Probed BEFORE the terminal write so the
    # count lands on the entry `status` reads. Skipped on a main-checkout (read-only)
    # run, where the dirt is the USER's own work — the same guard `_wip_commit` uses.
    uncommitted = None if _is_main_checkout(dirpath, repo_root) \
        else _uncommitted_total(dirpath)
    unc_phrase = _uncommitted_phrase(uncommitted)
    _save_entry(reg, key, project, seq, label, dirpath, sid, model=run_model,
                name=name, harness=adapter.name)   # refresh ts + sid + model + harness
    # B3: secure the durable report — the worker-authored file wins; else the
    # stdout result is harvested into it so backgrounding the delegate process
    # itself can never lose the report.
    rp, rhow = _persist_report(report_file, run_started, sid, result_text=result_text)
    # `uncommitted` is deliberately NOT in _STREAM_KEYS: the _save_entry rebuild above
    # drops any count from a previous run, so it is only ever written when true NOW.
    extra_hb = {}
    if unc_phrase:
        extra_hb["uncommitted"] = uncommitted
    if rp:
        extra_hb["report_path"] = rp
    _touch_heartbeat(key, pid=None, exit="ok",    # terminal state: finished OK, resumable-distinct
                     **extra_hb)
    _register_worker(seq, final_sid, name, run_model, adapter.name, "ok")
    _ledger(seq, "finish", final_sid,
            detail="%s%s" % (project, (":" + label) if label else ""))

    # Link the repo to the /todo task so its detail view lists this worker.
    if seq:
        try:
            subprocess.run(["python3", TASK_STATION_PY, "add-project", "--task", str(seq),
                            "--project", project],
                           capture_output=True, text=True, timeout=20)
        except Exception:
            pass
        # Write work (--worktree) means this task's work has actually started —
        # promote it from open (○) to active (●). Idempotent on the tracker side.
        if a.worktree:
            try:
                subprocess.run(["python3", TASK_STATION_PY, "status", "--task", str(seq), "active"],
                               capture_output=True, text=True, timeout=20)
            except Exception:
                pass
        # Accumulate this run's worker cost onto the task so per-task spend shows in
        # the /todo digest (not just this footer / workers.json), AND append a per-run
        # record (model + token usage + cost) to task["runs"]. Fires whenever the run
        # yielded ANY of cost/model/usage — an older CLI may report only some. The
        # running-total accumulation still no-ops on a missing/zero cost. Best-effort.
        if cost is not None or result_model or usage:
            add_cost_cmd = ["python3", TASK_STATION_PY, "add-cost", "--task", str(seq),
                            "--usd", str(cost) if cost is not None else "0"]
            if run_model:
                add_cost_cmd += ["--model", run_model]
            if final_sid:
                add_cost_cmd += ["--session", final_sid]
            if usage:
                add_cost_cmd += ["--usage-json", json.dumps(usage)]
            if label:
                add_cost_cmd += ["--seq-label", label]
            try:
                subprocess.run(add_cost_cmd, capture_output=True, text=True, timeout=20)
            except Exception:
                pass
        # A worker finishing is always news for the task feed — fire regardless of
        # whether the CLI reported cost/model/usage (unlike add-cost above). The
        # uncommitted count goes in as a PREFIX so the receiving 160-char snippet
        # trim can never be what drops it.
        _post_worker_event(seq, project, label, final_sid, True,
                           ("%s — %s" % (unc_phrase, result_text)) if unc_phrase
                           else result_text)

    print(result_text)
    banner = _uncommitted_banner(dirpath, uncommitted)
    if banner:
        print(banner)                    # stdout, after the result: the last thing read
    foot = "— worker '%s'  dir: %s" % (key, dirpath)
    if final_sid:
        foot += "  session: %s  (resume: cd %s && claude --resume %s)" % (final_sid, dirpath, final_sid)
    if rp:
        foot += "  report: %s (%s)" % (rp, rhow)
    if cost is not None:
        foot += "  cost: $%.4f" % cost
    if run_model:
        foot += "  model: %s" % run_model
    if unc_phrase:
        foot += "  !! %s" % unc_phrase
    print("\n" + foot, file=sys.stderr)

    # Worker run completed OK — fire the finished notification (best-effort).
    notify_event("worker_finished", seq, project, label, dirpath, cost,
                 sid=final_sid, name=name)


# ---------------------------------------------------------------- liveness ----


def _pid_alive(pid):
    """True when `pid` is a live `claude` worker. `os.kill(pid, 0)` probes
    existence (ProcessLookupError = gone; PermissionError = alive but another
    user's), then `ps -p <pid> -o comm=` must contain `claude` — this box OOMs
    and reboots often, and macOS reuses low pids, so a bare existence check can
    falsely read an unrelated reused pid as a running worker (S2)."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass                                 # exists, owned by another user
    except (ValueError, OverflowError, TypeError):
        return False
    except OSError:
        return False
    try:
        r = subprocess.run(["ps", "-p", str(int(pid)), "-o", "comm="],
                          capture_output=True, text=True, timeout=5)
        return "claude" in (r.stdout or "").lower()
    except Exception:
        return False


def _fmt_age(secs):
    """Compact human age: 5s / 3m / 2h / 1d."""
    secs = max(0, int(secs))
    if secs < 60:
        return "%ds" % secs
    if secs < 3600:
        return "%dm" % (secs // 60)
    if secs < 86400:
        return "%dh" % (secs // 3600)
    return "%dd" % (secs // 86400)


def _uncommitted_phrase(n):
    """`<n> UNCOMMITTED` for a positive count, else "". THE one wording shared by
    all three clean-finish surfaces (status line, run relay, task feed) so they
    read alike. Never raises — the count round-trips through the registry JSON, so
    a legacy string or garbage degrades to "" rather than breaking `status`."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    return "%d UNCOMMITTED" % n if n > 0 else ""


def _uncommitted_banner(dirpath, n):
    """The loud block `delegate run` relays when a CLEAN finish left work behind
    ("" when it didn't). Says explicitly that nothing was committed FOR the worker:
    a brief may legitimately forbid committing, so this reports, it never acts."""
    phrase = _uncommitted_phrase(n)      # also normalises the count; never raises
    if not phrase:
        return ""
    return ("\n!! %s file(s) in %s — the worker finished CLEANLY but committed "
            "nothing.\n"
            "!! Nothing was auto-committed (report-only, by design). Inspect with "
            "`git -C %s status`, then commit or discard." % (phrase, dirpath, dirpath))


def _liveness(entry, now=None, live_state=None):
    """Classify a registry entry → (glyph, text) for status/list (S2).

    exit recorded            → ○ finished (<exit>) <age> ago
    bg, agents say parked     → ○ STALLED (agents state '<s>') — named, never "running"
    bg, agents say busy       → ● running [bg] (last progress <N>s ago)
    pid present + alive       → ● running (quiet <N>s)
    pid present but gone      → ○ not running — session resumable
    legacy entry (no pid key) → ? unknown

    `live_state` is a JUST-PROBED agents state for a bg entry (cmd_status passes
    it); when absent the entry's recorded `agent_state` heartbeat is used. A bg
    worker's liveness is judged from the agents state — NEVER from pid (bg agents
    rows carry none, which made a live bg worker render "not running" and a parked
    one "running"; 444-17/B1) and never from a poll-touched timestamp.

    A CLEAN exit that left the worktree dirty renders its count INSIDE the parens
    — `finished (ok — 6 UNCOMMITTED)`. Only a clean exit does: an abnormal one has
    already been auto-checkpointed by `_wip_commit`, so a count left on the entry
    by an earlier clean finish must not resurface on a later crash."""
    now = _now() if now is None else now
    if entry.get("exit"):
        age = _fmt_age(now - (entry.get("last_event_ts") or entry.get("ts") or now))
        unc = (_uncommitted_phrase(entry.get("uncommitted"))
               if entry["exit"] == "ok" else "")
        return "○", "finished (%s%s) %s ago" % (
            entry["exit"], (" — " + unc) if unc else "", age)
    astate = live_state or entry.get("agent_state")
    if entry.get("bg") and astate:
        quiet = now - (entry.get("last_event_ts") or entry.get("started_ts") or now)
        if astate in STALLED_AGENT_STATES:
            return "○", ("STALLED — agents state '%s', no progress for %s "
                         "(attach in Agent View, resume, or `delegate reap-parked`)"
                         % (astate, _fmt_age(max(0, quiet))))
        if astate == "gone":
            return "○", "not running — session resumable"
        if astate in IDLE_AGENT_STATES:
            return "○", "turn complete (idle) — collect/resume"
        return "●", "running [bg %s] (last progress %ds ago)" % (astate, max(0, quiet))
    if "pid" not in entry:
        return "?", "unknown"                # pre-1.82 entry, no liveness info
    pid = entry.get("pid")
    if pid and _pid_alive(pid):
        quiet = now - (entry.get("last_event_ts") or entry.get("started_ts") or now)
        return "●", "running (quiet %ds)" % max(0, quiet)
    return "○", "not running — session resumable"


def _worktree_dirty_counts(dirpath):
    """`(dirty, untracked)` path counts for `dirpath`, or None when it is missing /
    not a readable git tree. THE one implementation of the porcelain tally — the
    status one-liner below and the clean-finish uncommitted report both read it.
    Best-effort: never raises."""
    if not dirpath or not os.path.isdir(dirpath):
        return None
    try:
        st = subprocess.run(["git", "-C", dirpath, "status", "--porcelain"],
                            capture_output=True, text=True)
        if st.returncode != 0:
            return None
        dirty = untracked = 0
        for line in (st.stdout or "").splitlines():
            if line.startswith("??"):
                untracked += 1
            elif line.strip():
                dirty += 1
        return dirty, untracked
    except Exception:
        return None


def _uncommitted_total(dirpath):
    """Total uncommitted paths (dirty + untracked) in `dirpath`, or None when the
    tree is gone / unreadable. The clean-finish probe: a worker that exits cleanly
    having committed nothing gets REPORTED, never auto-committed — a brief may
    legitimately tell a worker not to commit, so the bug is the silence, not the
    missing commit."""
    counts = _worktree_dirty_counts(dirpath)
    return None if counts is None else counts[0] + counts[1]


def _worktree_git_state(dirpath):
    """One-line git state of a worktree: `branch — <subject> (<age>)  <N> dirty / <M>
    untracked`. None when dirpath is missing / not a git tree. Best-effort."""
    if not dirpath or not os.path.isdir(dirpath):
        return None
    try:
        br = subprocess.run(["git", "-C", dirpath, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        if br.returncode != 0:
            return None
        branch = (br.stdout or "").strip()
        lg = subprocess.run(["git", "-C", dirpath, "log", "-1", "--pretty=%s\t%cr"],
                           capture_output=True, text=True)
        subject, when = "(no commits)", ""
        if lg.returncode == 0 and (lg.stdout or "").strip():
            parts = lg.stdout.strip().split("\t", 1)
            subject = parts[0]
            when = parts[1] if len(parts) > 1 else ""
        dirty, untracked = _worktree_dirty_counts(dirpath) or (0, 0)
        commit_bit = "%s%s" % (subject, (" (%s)" % when) if when else "")
        return "%s — %s  %d dirty / %d untracked" % (branch, commit_bit, dirty, untracked)
    except Exception:
        return None


def _status_matches(key, entry, a):
    """Filter a registry (key, entry) against status args on seq/project/label
    directly — NOT via _select_slot (which needs a full args namespace, applies
    slot-preference rules, and RAISES on a stale main-checkout entry — all wrong
    for a read-only query, S6). No filters (or --all) matches everything."""
    if getattr(a, "seq", None) and str(entry.get("seq")) != str(a.seq):
        return False
    if getattr(a, "label", None) and (entry.get("label") or None) != a.label:
        return False
    proj = getattr(a, "project", None) or getattr(a, "repo", None)
    if proj:
        want = proj
        if os.path.sep in proj:
            want = os.path.basename(os.path.abspath(os.path.expanduser(proj)).rstrip("/"))
        want = want.lower()
        if want not in (entry.get("project") or "").lower() and want not in key.lower():
            return False
    return True


def cmd_status(a):
    reg = load_reg()
    if not reg:
        print("delegate: no workers on record.")
        return
    now = _now()
    matched = [(k, e) for k, e in sorted(reg.items(), key=lambda kv: -kv[1].get("ts", 0))
               if _status_matches(k, e, a)]
    if not matched:
        print("delegate: no workers match that filter.")
        return
    # One agents snapshot for every live bg entry (B1): their liveness is judged
    # from the CURRENT agents state + transcript, never from the recorded
    # heartbeat alone — the recorded state freezes the moment the polling hub dies.
    agents_idx = None
    if any(e.get("bg") and not e.get("exit") for _, e in matched):
        try:
            agents_idx = harness.get_adapter(None).agents_index()
        except Exception:
            agents_idx = None
    for key, e in matched:
        live_state = None
        if agents_idx is not None and e.get("bg") and not e.get("exit"):
            sid = e.get("session_id") or ""
            row = agents_idx.get(sid)
            if row is None and sid:
                hits = [r for s, r in agents_idx.items() if s.startswith(sid)]
                row = hits[0] if len(hits) == 1 else None
            live_state = ((row.get("status") or row.get("state") or "running")
                          if row else "gone")
        glyph, live = _liveness(e, now, live_state=live_state)
        print("%s %s  %s" % (glyph, key, live))
        if live_state in STALLED_AGENT_STATES:
            t = _find_transcript(e.get("session_id"))
            jd = _job_diagnosis(e.get("session_id"))
            print("    truth: agents state '%s'; transcript %s%s"
                  % (live_state, ("ABSENT — the session never started a turn"
                                  if not t else "exists (%s)" % t),
                     ("; " + jd) if jd else ""))
        gs = _worktree_git_state(e.get("dir"))
        if gs:
            print("    git:   %s" % gs)
        phase = e.get("phase")
        if phase:
            hb = e.get("last_event_ts")
            phase_age = ("  (%s ago)" % _fmt_age(now - hb)) if hb else ""
            print("    phase: %s%s" % (phase, phase_age))
        rp = e.get("report_path")
        if rp:
            print("    report: %s%s" % (rp, "" if os.path.isfile(rp) else "  (missing!)"))
        sid = e.get("session_id")
        if sid and e.get("dir"):
            print("    resume: cd %s && claude --resume %s" % (e["dir"], sid))


def cmd_reap_parked(a):
    """`delegate reap-parked` — sweep task-station bg agents PARKED in a stalled
    agents state (B4: 39 had accumulated on this machine by 444-17, oldest 16
    days, because nothing ever removed a blocked bg agent — the task-close reaper
    wants a registry ∩ roster match and used to want a pid, and parked bg rows
    reliably have neither). Predicate — ALL must hold:
      kind == background · task-station worker name (unless --all-names) ·
      agents state in STALLED_AGENT_STATES · older than --min-age-mins ·
      not the current session.
    Reap = remove the supervisor's session-store file FIRST (blocks a respawn),
    then kill the pid group when a pid exists (parked bg rows usually carry none —
    the store row IS the agent then). --dry-run prints the verdicts and changes
    nothing."""
    adapter = harness.get_adapter(getattr(a, "harness", None))
    idx = adapter.agents_index()
    if not idx:
        print("delegate: agents list unavailable or empty — nothing to reap.")
        return
    now_ms = _now() * 1000
    current = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    hit = kept = 0
    for sid, row in sorted(idx.items(), key=lambda kv: kv[1].get("startedAt") or 0):
        if row.get("kind") != "background":
            continue
        name = row.get("name") or ""
        if not getattr(a, "all_names", False) and not _is_ts_worker_name(name):
            continue
        state = row.get("status") or row.get("state")
        if state not in STALLED_AGENT_STATES:
            continue
        if current and (sid == current or sid.startswith(current)
                        or current.startswith(sid)):
            continue
        age_min = max(0, (now_ms - (row.get("startedAt") or now_ms)) / 60000.0)
        age_txt = _fmt_age(int(age_min * 60))
        if age_min < a.min_age_mins:
            kept += 1
            print("· kept        %s  '%s' %s old — under --min-age-mins %d  %s"
                  % (sid[:8], state, age_txt, a.min_age_mins, name))
            continue
        if getattr(a, "dry_run", False):
            hit += 1
            print("· would reap  %s  '%s' %s old  %s" % (sid[:8], state, age_txt, name))
            continue
        try:
            _remove_bg_session_file(sid)              # FILE FIRST — block a respawn…
            pids = [row["pid"]] if row.get("pid") else _find_agent_pids(sid)
            for pid in pids:                          # …THEN the process(es); parked bg
                _kill_pid_group(pid)                  # rows carry no pid in the JSON
            _mark_job_done(sid)                       # …THEN the job record — the row
            hit += 1                                  # the agents list actually renders
            print("· reaped      %s  '%s' %s old%s  %s"
                  % (sid[:8], state, age_txt,
                     ("  (killed pid %s)" % ",".join(map(str, pids))) if pids else "",
                     name))
        except Exception as e:
            print("· FAILED      %s  (%s)" % (sid[:8], e.__class__.__name__))
    print("delegate: %s %d parked agent(s)%s."
          % ("would reap" if getattr(a, "dry_run", False) else "reaped", hit,
             (", kept %d under the age floor" % kept) if kept else ""))


def cmd_grants(a):
    """`delegate grants` — print the probed trust + grant surface for a repo or
    worktree, so the hub can paste the REAL toolset into a brief instead of
    guessing (B2; across 13 migration worker sessions the granted set varied
    wildly and every brief guessed)."""
    repo_root = _resolve_dir_from_args(a)
    dirpath = repo_root
    if getattr(a, "worktree", None):
        dirpath = worktree_path(repo_root, a.worktree)
        if not os.path.isdir(dirpath):
            raise SystemExit("delegate: worktree %s does not exist (grants probes "
                             "never create one)." % dirpath)
    g = _effective_grants(dirpath)
    trusted = _trust_state(dirpath)
    if getattr(a, "json", False):
        print(json.dumps({"dir": dirpath, "trusted": trusted, **g}, indent=2))
        return
    tr = {True: "yes", False: "NO — a worker here will park 'blocked'; a delegate "
                              "run repairs it", None: "unknown (~/.claude.json unreadable)"}
    print("dir:     %s" % dirpath)
    print("trusted: %s" % tr[trusted])
    print("allow (%d):" % len(g["allow"]))
    for x in g["allow"]:
        print("  %s" % x)
    if g["deny"]:
        print("deny (%d):" % len(g["deny"]))
        for x in g["deny"]:
            print("  %s" % x)
    for label, p, n in g["sources"]:
        print("source: %s (%d allow) %s" % (label, n, p))
    for m in g["missing"]:
        print("absent: %s" % m)


def cmd_list(a):
    reg = load_reg()
    if not reg:
        print("delegate: no workers on record.")
        return
    now = _now()
    for key, e in sorted(reg.items(), key=lambda kv: -kv[1].get("ts", 0)):
        age = now - e.get("ts", 0)
        glyph, _live = _liveness(e, now)
        model = ("  model %s" % e["model"]) if e.get("model") else ""
        print("%s %-28s %s%s\n    session %s  (%ds ago)\n    resume: cd %s && claude --resume %s"
              % (glyph, key, e.get("dir", "?"), model, e.get("session_id", "?"), age,
                 e.get("dir", "?"), e.get("session_id", "?")))


def cmd_dir(a):
    root = _resolve_dir_from_args(a)
    if getattr(a, "worktree", None):
        print(worktree_path(root, a.worktree))   # path only; no create
    else:
        print(root)


def _add_repo_or_project(p, *, project_required=False):
    """Add mutually exclusive --repo / --project args to a subparser."""
    g = p.add_mutually_exclusive_group(required=project_required)
    g.add_argument(
        "--repo",
        metavar="PATH",
        help="absolute path to a git repo — bypasses workspace scanning, "
             "no TASK_STATION_WORKSPACE_DIRS needed."
    )
    g.add_argument(
        "--project",
        metavar="NAME",
        help="repo name to find inside your configured workspace dirs "
             "(TASK_STATION_WORKSPACE_DIRS env var, colon-separated)."
    )


def main():
    ap = argparse.ArgumentParser(prog="delegate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="spawn/resume a worker and relay its result")
    _add_repo_or_project(r, project_required=True)
    r.add_argument("--task", required=True, help="self-contained instructions for the worker")
    r.add_argument("--worktree", default=None,
                   help="worktree dir name under <repo>-worktrees/; resolve-or-create and run there. "
                        "Required for write work (use the story id+slug or fix-<PR#>).")
    r.add_argument("--branch", default=None,
                   help="branch for the worktree (default: same as --worktree name)")
    r.add_argument("--base", default=None,
                   help="base ref for a NEW branch (default: the repo's default branch)")
    r.add_argument("--seq", default=None,
                   help="/todo task number to link this worker to (persistent per-(task,repo) worker + naming)")
    r.add_argument("--label", default=None,
                   help="discriminator for a SECOND concurrent worker in the same (task,repo)")
    r.add_argument("--solo", action="store_true",
                   help="ad-hoc: do NOT auto-inherit --seq from the calling session's attached task")
    r.add_argument("--fresh", action="store_true", help="ignore any saved worker session; start new")
    r.add_argument("--model", default="sonnet",
                   help="model for the delegated worker (default: sonnet — workers do "
                        "author-only mechanical edits; pass a stronger model e.g. opus for "
                        "genuinely hard work)")
    r.add_argument("--timeout", type=int, default=None, help="seconds before giving up on the worker")
    r.add_argument("--stall-grace", type=int, default=45,
                   help="seconds a --bg worker may sit in a parked agents state "
                        "(blocked/stalled/needs-input) before delegate fails fast "
                        "with the diagnosis instead of waiting (default 45)")
    r.add_argument("--harness", default="claude", choices=["claude", "codex"],
                   help="AI CLI to run the worker on (default: claude)")
    r.add_argument("--force", action="store_true",
                   help="override the orchestrator-only guard for this run. Deliberate "
                        "and RECORDED: the refusal it overrides is printed, and an event "
                        "saying so is written onto the task.")
    r.set_defaults(func=cmd_run)

    l = sub.add_parser("list", help="list known workers")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("status", help="live status of matching workers "
                                      "(running vs resumable, git state, phase)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--repo", metavar="PATH", help="filter by repo path")
    g.add_argument("--project", metavar="NAME", help="filter by project name")
    s.add_argument("--seq", default=None, help="filter by /todo task number")
    s.add_argument("--label", default=None, help="filter by worker label")
    s.add_argument("--all", action="store_true", help="show every worker (default when no filter)")
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("dir", help="resolve a project name or repo path to its local directory")
    _add_repo_or_project(d, project_required=True)
    d.add_argument("--worktree", default=None,
                   help="print the <repo>-worktrees/<name> path instead (does not create it)")
    d.set_defaults(func=cmd_dir)

    rp = sub.add_parser("reap-parked",
                        help="sweep task-station bg agents parked in a stalled "
                             "agents state (blocked/stalled/needs-input)")
    rp.add_argument("--min-age-mins", type=int, default=360,
                    help="only reap agents parked at least this long (default 360)")
    rp.add_argument("--dry-run", action="store_true",
                    help="print the verdicts; remove/kill nothing")
    rp.add_argument("--all-names", action="store_true",
                    help="drop the task-station-name safety filter (DANGEROUS: "
                         "can reap bg agents other tools spawned)")
    rp.add_argument("--harness", default="claude", choices=["claude", "codex"])
    rp.set_defaults(func=cmd_reap_parked)

    gr = sub.add_parser("grants",
                        help="probe the trust + permission grants a worker in a "
                             "repo/worktree will actually have (for briefs)")
    _add_repo_or_project(gr, project_required=True)
    gr.add_argument("--worktree", default=None,
                    help="probe the <repo>-worktrees/<name> tree instead (never creates it)")
    gr.add_argument("--json", action="store_true", help="machine-readable output")
    gr.set_defaults(func=cmd_grants)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
