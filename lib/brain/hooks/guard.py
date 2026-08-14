"""brain.hooks.guard — PreToolUse guard for the Bash tool.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``hooks/guard.py`` @ 0.14.0. Every pattern, threshold and decision string is
verbatim; the module simply moved into the ``brain.hooks`` package. It has no
siblings and no config — pure stdlib, which is why it is the one hook module
that also runs correctly BY PATH (``python3 lib/brain/hooks/guard.py``) as well
as through ``-m``. Keep it that way: a relative import here would silently take
the by-path invocation away from Phase 5.

Blocks Bash commands that would write a secret into the session transcript
(which is sent to the model API and stored on disk). Two detections:

  (A) A secret LITERAL embedded in the command text — a JWT, or a long opaque
      high-entropy token passed to a secret-bearing flag (--value, --token,
      --deployment-token, --password, --client-secret, …), or any standalone
      base64-ish blob.

  (B) A secret-READING command whose output is NOT contained — e.g.
      `az … secrets list … -o tsv`, `az account get-access-token`,
      `az keyvault secret show`, or a `--query` referencing a key/secret/token
      field — when the command neither suppresses output (`--output none` /
      `-o none`) nor captures it into a shell variable (`VAR=$(…)`).

Fail-open: any parsing/logic error allows the command (a buggy guard must not
brick the shell). The reason message never echoes the matched secret.

WIRED in Phase 5, exactly as the source wired it: ``hooks/hooks.json`` registers
``python3 "${CLAUDE_PLUGIN_ROOT}/lib/brain/hooks/guard.py"`` under
``PreToolUse`` with matcher ``Bash``. It is the ONE hook that skips the board's
hook mux — PreToolUse is a brain-only event, so there is nothing to merge — and
the ONE hook invoked by PATH, which is why the stdlib-only rule above is
load-bearing.
"""
import json
import re
import sys

SAFE_HINT = (
    "Blocked by secret-guard: this command would expose a secret in the "
    "transcript (which is sent to the model API and saved on disk). Keep the "
    "secret in a shell variable and suppress output, e.g.  "
    'TOK="$(az ... -o tsv)"; cmd --value "$TOK" --output none  '
    "— or use a native secret store (ADO secret pipeline variable / Key "
    "Vault-linked variable group)."
)

# A secret-bearing flag followed by its value.
SECRET_FLAGS = (
    r"--deployment[-_]?token|--value|--password|--client[-_]secret|--secret|"
    r"--token|--api[-_]?key|--account[-_]key|--connection[-_]string|--sas[-_]?token"
)
# flag <sep> value   where value may be quoted; capture the raw value token.
FLAG_VALUE_RE = re.compile(
    r"(?:" + SECRET_FLAGS + r")[=\s]+(?P<q>['\"]?)(?P<val>[^\s'\"]+)(?P=q)",
    re.IGNORECASE,
)

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*")

# Standalone high-entropy blob: >=40 chars of base64/token alphabet.
# NB: '/' is deliberately excluded so filesystem paths and URLs (which are full
# of '/') break into short segments and don't trip the >=40 length test.
BLOB_RE = re.compile(r"[A-Za-z0-9+=_-]{40,}")

# A bare blob is too weak a signal on its own (base64 data, hashes, encoded IDs,
# long opaque names all trip it). Only treat it as a secret when a secret-ish
# keyword sits within NEAR_WINDOW chars of the blob — that context is what
# distinguishes a pasted credential from arbitrary high-entropy data.
NEAR_SECRET_RE = re.compile(
    r"token|secret|key|password|passwd|auth|bearer|credential|sas|"
    r"connection[-_]?string",
    re.IGNORECASE,
)
NEAR_WINDOW = 40

# Secret-reading command signatures.
SECRET_READ_RES = [
    re.compile(r"\bsecrets\s+list\b", re.IGNORECASE),
    re.compile(r"\bget-access-token\b", re.IGNORECASE),
    re.compile(r"\bkeyvault\s+secret\s+show\b", re.IGNORECASE),
    re.compile(r"\bsecrets\s+reset-api-key\b.*--query", re.IGNORECASE),
    re.compile(
        r"--query\s+['\"]?[^'\"|;&]*(api[-_]?key|[._-]key\b|secret|password|token)",
        re.IGNORECASE,
    ),
]


def char_classes(s: str) -> int:
    classes = 0
    if re.search(r"[A-Z]", s):
        classes += 1
    if re.search(r"[a-z]", s):
        classes += 1
    if re.search(r"[0-9]", s):
        classes += 1
    if re.search(r"[+/=_-]", s):
        classes += 1
    return classes


def looks_like_secret_value(val: str) -> bool:
    """A literal value passed to a secret-bearing flag.

    The flag name (--token/--password/--deployment-token/…) already tells us the
    value is a secret, so ANY inline literal of meaningful length is flagged —
    no entropy test (catches long hex tokens, which are only 2 char classes).
    Shell variables, file refs, paths and URLs are treated as safe.
    """
    if not val or val[0] in "$@":
        return False  # shell var ($X) / file ref (@file) — safe
    if val.startswith(("/", "./", "../", "~", "http://", "https://")):
        return False  # path or URL, not an inline secret
    return len(val) >= 16


def output_contained(cmd: str) -> bool:
    """True if a secret read is suppressed or captured into a variable."""
    if re.search(r"(?:^|\s)(?:--output\s+none|-o\s+none)\b", cmd):
        return True
    if re.search(r"=\s*\$\(", cmd):  # VAR=$( ... )
        return True
    if re.search(r"=\s*`", cmd):  # VAR=` ... ` (legacy backticks)
        return True
    return False


def detect(cmd: str):
    # (A) literal secret in the command text
    if JWT_RE.search(cmd):
        return "JWT literal in command"
    for m in FLAG_VALUE_RE.finditer(cmd):
        if looks_like_secret_value(m.group("val")):
            return "opaque token passed as a literal flag value"
    for m in BLOB_RE.finditer(cmd):
        blob = m.group(0)
        if char_classes(blob) < 3:
            continue
        # context-gate: only a secret if a secret-ish keyword is nearby
        lo = max(0, m.start() - NEAR_WINDOW)
        hi = min(len(cmd), m.end() + NEAR_WINDOW)
        if NEAR_SECRET_RE.search(cmd[lo:hi]):
            return "high-entropy literal (base64/token) next to a secret keyword"
    # (B) secret read whose output is not contained
    for rx in SECRET_READ_RES:
        if rx.search(cmd):
            if not output_contained(cmd):
                return "secret-reading command without --output none / variable capture"
            break
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open
    if data.get("tool_name") != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return 0
    try:
        hit = detect(cmd)
    except Exception:
        return 0  # fail-open
    if hit:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{SAFE_HINT} [{hit}]",
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
