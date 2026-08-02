#!/usr/bin/env python3
"""Tasktrail conformance validator — stdlib only (no pip `jsonschema`).

Hand-rolled checks that MIRROR spec/tasktrail.event.schema.json and
spec/task-note.frontmatter.schema.json (Tasktrail spec_version 1.0). Kept in
lockstep with those schema files by hand: the schemas are the human-readable
contract, this module is the enforceable one.

Two roles:
  * CLI —  `python spec/validate.py <tasktrail-dir>`  validates a whole bundle
    (tasktrail.json manifest + events/*.jsonl + notes/*.md + index.md) and exits
    non-zero with one actionable line per problem. A single file argument
    (.jsonl / .md / .json) validates just that file.
  * Library — import and call validate_dir / validate_events / validate_note /
    validate_manifest / parse_frontmatter (used by the CI self-conformance tests).

Every check returns a list of human-readable error strings (empty ⇒ conformant),
each prefixed with the file (and line/index) it came from, so a failure points at
exactly what to fix.
"""
import json
import os
import re
import sys

SPEC_VERSION = "1.0"

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EVENT_TYPES = (
    "task.created", "task.updated", "task.status", "task.event", "task.relation",
    "task.checkpoint", "task.snapshot", "task.deleted", "task.redacted",
)

_STUB = {"redacted": True}

# Frontmatter keys required on every managed note (mirrors the schema `required`).
_FM_REQUIRED = (
    "managed-by", "schema-version", "uuid", "seq", "status", "category", "effort",
    "repos", "story", "pr", "created", "updated", "closed", "title", "models",
    "cost-usd", "time-spent",
)
_FM_KNOWN = set(_FM_REQUIRED) | {"owner", "glossary", "brief_path"}
_FM_LIST_KEYS = ("repos", "story", "pr", "models", "glossary")


# ------------------------------------------------------------------ helpers ---

def _is_str(x):
    return isinstance(x, str)


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _str_list(x):
    return isinstance(x, list) and all(_is_str(i) for i in x)


# ------------------------------------------------------------------ events ---

def _check_data(event, data, where):
    """Mirror the per-event `data` subschemas. `data` may always be the redaction
    stub {"redacted": true} EXCEPT on a task.redacted marker row."""
    errs = []
    if not isinstance(data, dict):
        return ["%s: data must be an object" % where]
    if data == _STUB and event != "task.redacted":
        return errs                                    # valid stub for any event
    if "redacted" in data and event != "task.redacted":
        return ["%s: redaction stub must be exactly {\"redacted\": true}" % where]

    if event == "task.created":
        if not _is_str(data.get("title")):
            errs.append("%s: task.created.data.title must be a string" % where)
        if not (_is_str(data.get("status")) or data.get("status") is None):
            errs.append("%s: task.created.data.status must be a string or null" % where)
    elif event == "task.updated":
        if not _str_list(data.get("changed")):
            errs.append("%s: task.updated.data.changed must be a string array" % where)
        f = data.get("fields")
        if not isinstance(f, dict):
            errs.append("%s: task.updated.data.fields must be an object" % where)
        else:
            allowed = {"title", "summary", "state", "goal", "effort", "color"}
            extra = set(f) - allowed
            if extra:
                errs.append("%s: task.updated.data.fields has unknown key(s): %s"
                            % (where, ", ".join(sorted(extra))))
    elif event == "task.status":
        if not _is_str(data.get("status")):
            errs.append("%s: task.status.data.status must be a string" % where)
        cts = data.get("closed_ts")
        if not (cts is None or _is_num(cts)):
            errs.append("%s: task.status.data.closed_ts must be a number or null" % where)
    elif event == "task.event":
        if not _is_str(data.get("kind")):
            errs.append("%s: task.event.data.kind must be a string" % where)
        if not _is_str(data.get("text")):
            errs.append("%s: task.event.data.text must be a string" % where)
    elif event == "task.relation":
        if not _is_str(data.get("kind")):
            errs.append("%s: task.relation.data.kind must be a string" % where)
        other = data.get("other")
        if not isinstance(other, dict) or not _is_str(other.get("uuid")) \
                or not (_is_int(other.get("seq")) or other.get("seq") is None):
            errs.append("%s: task.relation.data.other must be {uuid:str, seq:int|null}"
                        % where)
    elif event in ("task.checkpoint", "task.snapshot"):
        errs.extend(_check_digest(data, where))
    elif event == "task.deleted":
        if data:
            errs.append("%s: task.deleted.data must be empty {}" % where)
    elif event == "task.redacted":
        g = data.get("generation")
        if not _is_int(g) or g < 1:
            errs.append("%s: task.redacted.data.generation must be an integer >= 1"
                        % where)
        if set(data) - {"generation"}:
            errs.append("%s: task.redacted.data has unexpected key(s)" % where)
    return errs


def _check_digest(d, where):
    errs = []
    for k in ("goal", "state", "summary"):
        if not _is_str(d.get(k)):
            errs.append("%s: digest.%s must be a string" % (where, k))
    for k in ("steps", "prs", "stories"):
        if not isinstance(d.get(k), list):
            errs.append("%s: digest.%s must be an array" % (where, k))
    if not _str_list(d.get("decisions")):
        errs.append("%s: digest.decisions must be a string array" % where)
    if "glossary" in d:
        errs.extend(_check_glossary(d["glossary"], where))
    if "brief_path" in d and not _is_str(d["brief_path"]):
        errs.append("%s: digest.brief_path must be a string" % where)
    return errs


def _check_glossary(g, where):
    errs = []
    if not isinstance(g, list):
        return ["%s: glossary must be an array" % where]
    for i, term in enumerate(g):
        if not isinstance(term, dict):
            errs.append("%s: glossary[%d] must be an object" % (where, i))
            continue
        for k in ("name", "layer", "state", "def"):
            if not _is_str(term.get(k)):
                errs.append("%s: glossary[%d].%s must be a string" % (where, i, k))
    return errs


def validate_event(env, where="event"):
    """Validate one decoded event envelope. Returns a list of error strings."""
    errs = []
    if not isinstance(env, dict):
        return ["%s: event must be a JSON object" % where]
    if env.get("v") != 1:
        errs.append("%s: v must be 1 (got %r)" % (where, env.get("v")))
    ts = env.get("ts")
    if not _is_str(ts) or not _TS_RE.match(ts):
        errs.append("%s: ts must be UTC ISO8601 seconds (YYYY-MM-DDThh:mm:ss+00:00), "
                    "got %r" % (where, ts))
    if not _is_int(env.get("n")) or env.get("n") < 1:
        errs.append("%s: n must be an integer >= 1 (got %r)" % (where, env.get("n")))
    ev = env.get("event")
    if ev not in EVENT_TYPES:
        errs.append("%s: event must be one of %s (got %r)"
                    % (where, ", ".join(EVENT_TYPES), ev))
    task = env.get("task")
    if not isinstance(task, dict) or not _is_str(task.get("uuid")) or not task["uuid"]:
        errs.append("%s: task.uuid must be a non-empty string" % where)
    elif not (_is_int(task.get("seq")) or task.get("seq") is None):
        errs.append("%s: task.seq must be an integer or null" % where)
    actor = env.get("actor")
    if not isinstance(actor, dict):
        errs.append("%s: actor must be an object" % where)
    else:
        if "session" not in actor or not (_is_str(actor["session"]) or actor["session"] is None):
            errs.append("%s: actor.session must be a string or null" % where)
        if set(actor) - {"session", "owner"}:
            errs.append("%s: actor has unknown key(s)" % where)
    known = {"v", "ts", "n", "event", "task", "actor", "data"}
    extra = set(env) - known
    if extra:
        errs.append("%s: unknown envelope key(s): %s" % (where, ", ".join(sorted(extra))))
    if ev in EVENT_TYPES:
        errs.extend(_check_data(ev, env.get("data"), where))
    return errs


def _continuity(events):
    """Mirror stream.verify: per-task n must be gapless 1..N and non-decreasing in
    file order. `events` is a list of (where, env) in global append order."""
    errs = []
    per_task = {}
    for where, env in events:
        u = (env.get("task") or {}).get("uuid")
        per_task.setdefault(u, []).append(env.get("n"))
    for u, ns in sorted(per_task.items(), key=lambda kv: str(kv[0])):
        clean = [x for x in ns if _is_int(x)]
        if clean != sorted(clean):
            errs.append("task %s: events out of order in shards: %s" % (u, ns))
        s = sorted(clean)
        if s != list(range(1, len(s) + 1)):
            errs.append("task %s: n not gapless (expected 1..%d): %s" % (u, len(s), ns))
    return errs


def validate_events(path):
    """Validate one events shard file (.jsonl). Returns (errors, [(where, env)])."""
    errs, decoded = [], []
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                s = line.strip()
                if not s:
                    continue
                where = "%s:%d" % (name, lineno)
                try:
                    env = json.loads(s)
                except ValueError as e:
                    errs.append("%s: invalid JSON: %s" % (where, e))
                    continue
                errs.extend(validate_event(env, where))
                decoded.append((where, env))
    except OSError as e:
        return (["%s: cannot read shard: %s" % (name, e)], [])
    return (errs, decoded)


# --------------------------------------------------------------- manifest -----

def validate_manifest(m, where="tasktrail.json"):
    errs = []
    if not isinstance(m, dict):
        return ["%s: manifest must be a JSON object" % where]
    sv = m.get("spec_version")
    if not _is_str(sv):
        errs.append("%s: spec_version must be a string (e.g. \"1.0\")" % where)
    elif not re.match(r"^\d+\.\d+$", sv):
        errs.append("%s: spec_version must be MAJOR.MINOR (got %r)" % (where, sv))
    elif sv.split(".")[0] != SPEC_VERSION.split(".")[0]:
        errs.append("%s: spec_version major %r != validator major %r"
                    % (where, sv, SPEC_VERSION))
    if not _is_str(m.get("producer")) or not m.get("producer"):
        errs.append("%s: producer must be a non-empty string" % where)
    g = m.get("generation")
    if not _is_int(g) or g < 1:
        errs.append("%s: generation must be an integer >= 1" % where)
    if "owner" in m and (not _is_str(m["owner"]) or not m["owner"]):
        errs.append("%s: owner, when present, must be a non-empty string" % where)
    return errs


# -------------------------------------------------------------- frontmatter ---

def _unquote(s):
    """Reverse obsidian_sync._q: strip wrapping double quotes and un-escape."""
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


def _scalar(raw):
    """Type a bare frontmatter scalar the way the flat-YAML render emits it: a
    quoted value is a string; a bare int/float keeps its numeric type; anything
    else is a bare string."""
    raw = raw.strip()
    if raw.startswith('"'):
        return _unquote(raw)
    if re.match(r"^-?\d+$", raw):
        return int(raw)
    if re.match(r"^-?\d+\.\d+$", raw):
        return float(raw)
    return raw


def parse_frontmatter(text):
    """Parse the flat-YAML frontmatter block of a managed note into a dict.

    Handles exactly the subset render_note emits: `key: scalar`, `key: []`, and a
    block list (`key:` then `  - item` lines). Returns (frontmatter_dict, error)
    where error is None on success or a message when there is no frontmatter."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return (None, "no frontmatter block (missing opening ---)")
    fm, i, cur_key = {}, 1, None
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            return (fm, None)
        if line.startswith("  - ") and cur_key is not None:
            fm.setdefault(cur_key, [])
            if isinstance(fm[cur_key], list):
                fm[cur_key].append(_unquote(line[4:]))
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):(.*)$", line)
        if m:
            key, rest = m.group(1), m.group(2)
            rest_stripped = rest.strip()
            if rest_stripped == "":
                cur_key = key
                fm[key] = []                    # provisional; a block list may follow
            elif rest_stripped == "[]":
                fm[key] = []
                cur_key = None
            else:
                fm[key] = _scalar(rest_stripped)
                cur_key = None
        i += 1
    return (None, "unterminated frontmatter block (missing closing ---)")


def validate_frontmatter(fm, where="note"):
    errs = []
    if not isinstance(fm, dict):
        return ["%s: frontmatter did not parse to a mapping" % where]
    for k in _FM_REQUIRED:
        if k not in fm:
            errs.append("%s: missing required frontmatter key '%s'" % (where, k))
    if fm.get("managed-by") != "task-station":
        errs.append("%s: managed-by must be 'task-station'" % where)
    if fm.get("schema-version") != 2:
        errs.append("%s: schema-version must be 2 (got %r)" % (where, fm.get("schema-version")))
    if not _is_str(fm.get("uuid")) or not fm.get("uuid"):
        errs.append("%s: uuid must be a non-empty string" % where)
    seq = fm.get("seq")
    if not (_is_int(seq) or seq == ""):
        errs.append("%s: seq must be an integer or \"\"" % where)
    for k in ("status", "category", "effort", "title"):
        if not _is_str(fm.get(k)):
            errs.append("%s: %s must be a string" % (where, k))
    for k in _FM_LIST_KEYS:
        if k in fm and not _str_list(fm.get(k)):
            errs.append("%s: %s must be a list of strings" % (where, k))
    for k in ("created", "updated", "closed"):
        v = fm.get(k)
        if not (v == "" or (_is_str(v) and _DATE_RE.match(v))):
            errs.append("%s: %s must be YYYY-MM-DD or \"\" (got %r)" % (where, k, v))
    cu = fm.get("cost-usd")
    if not (_is_num(cu) or cu == ""):
        errs.append("%s: cost-usd must be a number or \"\"" % where)
    ts = fm.get("time-spent")
    if not (_is_int(ts) and ts >= 0):
        errs.append("%s: time-spent must be an integer >= 0" % where)
    if "owner" in fm and (not _is_str(fm["owner"]) or not fm["owner"]):
        errs.append("%s: owner, when present, must be a non-empty string" % where)
    if "brief_path" in fm and not _is_str(fm["brief_path"]):
        errs.append("%s: brief_path must be a string" % where)
    extra = set(fm) - _FM_KNOWN
    if extra:
        errs.append("%s: unknown frontmatter key(s): %s" % (where, ", ".join(sorted(extra))))
    return errs


def validate_note(path):
    """Validate one managed note file (.md)."""
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return ["%s: cannot read note: %s" % (name, e)]
    fm, err = parse_frontmatter(text)
    if err:
        return ["%s: %s" % (name, err)]
    return validate_frontmatter(fm, name)


# ---------------------------------------------------------------- bundle ------

def validate_dir(path):
    """Validate a whole tasktrail/ bundle. Returns a list of error strings."""
    errs = []
    man_path = os.path.join(path, "tasktrail.json")
    if not os.path.isfile(man_path):
        errs.append("tasktrail.json: manifest not found in %s" % path)
    else:
        try:
            with open(man_path, encoding="utf-8") as f:
                man = json.load(f)
            errs.extend(validate_manifest(man))
        except ValueError as e:
            errs.append("tasktrail.json: invalid JSON: %s" % e)

    ev_dir = os.path.join(path, "events")
    all_events = []
    if os.path.isdir(ev_dir):
        for name in sorted(n for n in os.listdir(ev_dir) if n.endswith(".jsonl")):
            e, decoded = validate_events(os.path.join(ev_dir, name))
            errs.extend(e)
            all_events.extend(decoded)
    errs.extend(_continuity(all_events))

    notes_dir = os.path.join(path, "notes")
    if os.path.isdir(notes_dir):
        for name in sorted(n for n in os.listdir(notes_dir) if n.endswith(".md")):
            errs.extend(validate_note(os.path.join(notes_dir, name)))
    return errs


def validate_path(path):
    """Dispatch a CLI path arg to the right validator by kind/extension."""
    if os.path.isdir(path):
        return validate_dir(path)
    if path.endswith(".jsonl"):
        return validate_events(path)[0]
    if path.endswith(".md"):
        return validate_note(path)
    if path.endswith(".json"):
        try:
            with open(path, encoding="utf-8") as f:
                return validate_manifest(json.load(f), os.path.basename(path))
        except (OSError, ValueError) as e:
            return ["%s: %s" % (os.path.basename(path), e)]
    return ["%s: unrecognised path (expected a dir, .jsonl, .md, or .json)" % path]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: validate.py <tasktrail-dir | file.jsonl | note.md | "
                         "tasktrail.json>\n")
        return 2
    total = 0
    for target in argv:
        errs = validate_path(target)
        if errs:
            total += len(errs)
            sys.stderr.write("FAIL %s (%d issue(s)):\n" % (target, len(errs)))
            for e in errs:
                sys.stderr.write("  - %s\n" % e)
        else:
            sys.stdout.write("OK %s (Tasktrail spec_version %s)\n" % (target, SPEC_VERSION))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
