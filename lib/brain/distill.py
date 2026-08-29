"""brain-station auto-distill — the Stop-hook continuous-ingestion tier (decided 2026-07-08).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 3) from the brain source tree's
``scripts/auto-distill.py`` @ 0.14.0 (hyphen -> underscore, per the monorepo's
module-name rule). Behaviour is unchanged; the imports are relative siblings and
the recursion-guard env var is namespaced (see :data:`DISTILL_ENV`).

When a Claude Code session ends, extract 0-3 durable atomic facts from the
transcript with a cheap Haiku pass and drop them into the vault's inbox/ dir
(provenance-headed) for the next heal pass to distill into notes.

Guards (ALL mandatory — this module must be impossible to loop or spam):
  1. env TASK_STATION_BRAIN_DISTILL_ACTIVE=1 -> exit (recursion guard; we set it on the claude call we make)
  2. payload stop_hook_active               -> exit (harness loop guard)
  3. config auto_distill == false           -> exit
  4. transcript shorter than MIN_MSGS       -> exit (not worth a distill)
  5. state file distill-<session>.done      -> exit (once per session)

Never fails loudly: every path exits 0. --dry-run prints the decision plan.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``brain.config`` / ``brain.errorlog`` only.
"""
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import config
from . import errorlog
from . import notes as _notes

MIN_MSGS = 30          # ~15 exchanges
TAIL_CHARS = 24000     # transcript tail given to Haiku
MODEL = "claude-haiku-4-5-20251001"

# The recursion guard's env var. Set on the `claude -p` child we spawn so the
# session that child ends cannot spawn another distill. Named ...DISTILL_ACTIVE
# rather than ...DISTILL so it can never be mistaken for the *config* toggle
# TASK_STATION_BRAIN_AUTO_DISTILL, which is a different lever entirely. Spelled
# once, here, because the guard, its skip message and the child env must agree.
DISTILL_ENV = "TASK_STATION_BRAIN_DISTILL_ACTIVE"

PROMPT = """You are a knowledge-capture pass over the tail of a finished Claude Code session.
Extract 0-3 DURABLE atomic facts a developer would want in a personal wiki:
decisions made (with why), gotchas discovered, state changes, how-tos verified.
Skip: routine execution logs, secrets (never output a credential), speculation,
anything only meaningful inside this one session.
Output format: for each fact, a markdown block:
### <short-kebab-slug>
<2-4 sentence fact, absolute dates, no first person>
If nothing qualifies, output exactly: NONE
Transcript tail follows:
"""


def _decide(payload, cfg, state):
    if os.environ.get(DISTILL_ENV) == "1":
        return f"skip: recursion guard ({DISTILL_ENV}=1)"
    if payload.get("stop_hook_active"):
        return "skip: stop_hook_active"
    if not cfg["auto_distill"]:
        return "skip: auto_distill disabled in config"
    if not cfg["vault"].exists():
        return "skip: no vault"
    tp = payload.get("transcript_path")
    if not tp or not Path(tp).exists():
        return "skip: no transcript"
    if state.exists():
        return "skip: already distilled this session"
    n = sum(1 for line in open(tp, errors="ignore")
            if '"type":"user"' in line or '"type":"assistant"' in line
            or '"role":"user"' in line or '"role":"assistant"' in line)
    if n < MIN_MSGS:
        return f"skip: transcript too short ({n} < {MIN_MSGS} messages)"
    return "distill"


def _transcript_tail(tp):
    texts = []
    for line in open(tp, errors="ignore"):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") or obj
        content = msg.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return "\n".join(texts)[-TAIL_CHARS:]


def main():
    dry = "--dry-run" in sys.argv
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        cfg = config.load()
        session = (payload.get("session_id") or "unknown")[:8]
        state = config.state_dir() / f"distill-{session}.done"
        decision = _decide(payload, cfg, state)
        if dry:
            print(f"auto-distill decision: {decision}")
            sys.exit(0)
        if decision != "distill":
            sys.exit(0)
        state.touch()  # before the call: even a failed distill counts (never spam retries)
        tail = _transcript_tail(payload["transcript_path"])
        if not tail.strip():
            sys.exit(0)
        env = dict(os.environ, **{DISTILL_ENV: "1"})
        r = subprocess.run(
            ["claude", "-p", PROMPT + tail, "--model", MODEL],
            capture_output=True, text=True, timeout=180, env=env,
        )
        out = (r.stdout or "").strip()
        if not out or out == "NONE" or "### " not in out:
            sys.exit(0)
        today = datetime.date.today().isoformat()
        dest = cfg["vault"] / _notes.INBOX_DIR / f"{today}-auto-{session}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        header = (f"<!-- auto-distill: session {session}, {today}. "
                  "Untrusted until distilled by /brain-heal. -->\n\n")
        dest.write_text(header + re.sub(r"\n{3,}", "\n\n", out) + "\n")
    except Exception as e:
        errorlog.record("auto-distill", e)  # a hook must never break the session
    sys.exit(0)


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the hook entry point
    main()
