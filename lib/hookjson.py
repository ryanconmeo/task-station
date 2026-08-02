#!/usr/bin/env python3
"""Extract one string field from the hook's JSON stdin — the jq-free replacement
for `jq -r '.path // default'` in hooks/*.sh.

`python3` is already a hard requirement of Task Station; `jq` was the only other
one, so the hooks parse their stdin with this instead (see hooks/*.sh). Usage:

    <hook-json-on-stdin> | python3 hookjson.py <dotted.path> [default]

Prints the string at <dotted.path> (e.g. "session_id", "tool_input.file_path"),
or <default> ("" when omitted) whenever the input is absent, malformed, or the
value is missing / null / not a string. It NEVER raises and always exits 0, so a
malformed or empty payload is a silent no-op — exactly the `// default` semantics
the jq calls had. No trailing newline is added; command substitution strips one
anyway, and a piped value (e.g. the compaction summary) must pass through verbatim."""
import json
import sys


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    default = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        node = json.load(sys.stdin)
        for key in path.split("."):
            node = node[key]                    # KeyError/TypeError → default
        out = node if isinstance(node, str) else default
    except Exception:
        out = default
    sys.stdout.write(out)


if __name__ == "__main__":
    main()
