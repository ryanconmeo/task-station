#!/usr/bin/env python3
"""ado-tree — fetch an Azure DevOps work item + its related tree cheaply.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``scripts/ado_tree.py`` @ 0.14.0. Fetch, relation parsing, HTML stripping, node
shaping and rendering are verbatim. Two changes, both about org identity:

  * the built-in org URL default is GONE. Resolution is ``--org`` -> the brain
    config's ``ado_org`` -> ``$ADO_ORG`` -> nothing, and "nothing" is a clear
    error rather than a stranger's organization.
  * the config read goes through the sibling :mod:`brain.config` (the source
    reached for its ``pb_config`` via a function-local import to stay runnable
    standalone; inside the package the sibling is always there).

  ~85-92% fewer tokens: reads a full ADO work-item tree in ONE zero-token call
  instead of ~4 token-billed ADO-MCP round-trips. (Measured on a real 4-node
  story tree.)

Prints a compact tree (parent Feature -> child Stories/Tasks + linked PRs) with
only the fields that matter, so an AI agent (or a human) gets the full picture of
a work item in one external call instead of many token-billed MCP round-trips.

Auth is resolved WITHOUT any hardcoded secret, in this order:
  1. $ADO_PAT or $AZURE_DEVOPS_EXT_PAT   -> HTTP Basic (PAT)
  2. `az account get-access-token`        -> Bearer (uses your existing az login)
  3. otherwise: tell the user to run `az login` (opens a browser), or --login it.

Stdlib only (urllib/json/subprocess) so it runs anywhere Python 3.9+ is, no pip.
No forge adapter: this reads work ITEMS, which ``core.forge`` (repos, branches,
PRs) knows nothing about — the two do not overlap and are not coupled.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``brain.config`` only.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
import urllib.error
import urllib.parse
import urllib.request

from . import config

# Public Azure DevOps app id — this is NOT a secret, it's the well-known resource
# GUID every ADO token is minted against (documented, appears in az docs).
ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
API = "api-version=7.1"

HIER_CHILD = "System.LinkTypes.Hierarchy-Forward"
HIER_PARENT = "System.LinkTypes.Hierarchy-Reverse"

NO_ORG_HINT = ("no ADO organization configured — pass --org <url>, set "
               "\"ado_org\" in the brain config, or export ADO_ORG")

# How much of a long text field the compact view shows before it must declare
# itself a preview. See the block above TEXT_FIELDS for why the declaration is
# not optional.
PREVIEW_CHARS = 600
TRUNCATED_HINT = ("text clipped for the compact view — re-run with --no-clip "
                  "(or --full) for the complete field")


def default_org() -> str | None:
    """--org unset -> brain config ``ado_org`` -> ``$ADO_ORG`` -> None.

    There is deliberately NO built-in default: an organization URL is
    machine-specific, and shipping one means every unconfigured install points at
    somebody else's tenant."""
    try:
        cfg = config.load()
        val = cfg.get("ado_org") if isinstance(cfg, dict) else None
        if val:
            return val
    except Exception:
        pass
    return os.environ.get("ADO_ORG") or None


# --------------------------------------------------------------------------- auth
class Auth:
    def __init__(self, header: str, kind: str):
        self.header = header
        self.kind = kind


def resolve_auth(allow_login: bool) -> Auth:
    pat = os.environ.get("ADO_PAT") or os.environ.get("AZURE_DEVOPS_EXT_PAT")
    if pat:
        raw = base64.b64encode(f":{pat}".encode()).decode()
        return Auth(f"Basic {raw}", "PAT (env)")

    tok = _az_token()
    if tok:
        return Auth(f"Bearer {tok}", "az access token")

    if allow_login:
        sys.stderr.write("No ADO credential found — launching `az login` (browser)...\n")
        subprocess.run(["az", "login", "--allow-no-subscriptions"], check=False)
        tok = _az_token()
        if tok:
            return Auth(f"Bearer {tok}", "az access token (post-login)")

    sys.exit(
        "No ADO credential available.\n"
        "  Fix (pick one):\n"
        "    - run:  az login            (opens a browser; then re-run)\n"
        "    - or:   export ADO_PAT=<your Azure DevOps PAT>\n"
        "    - or re-run this command with --login to auto-open the browser.\n"
    )


def _az_token() -> str | None:
    try:
        out = subprocess.run(
            ["az", "account", "get-access-token", "--resource", ADO_RESOURCE,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    tok = out.stdout.strip()
    return tok or None


# ------------------------------------------------------------------------- fetch
def _get(url: str, auth: Auth) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": auth.header,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        sys.exit(f"ADO API {e.code} on {url}\n  {body}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error reaching ADO: {e.reason}")


def fetch_item(org: str, wid: int, auth: Auth) -> dict:
    # $expand=all == fields + relations + links: the COMPLETE bag the ADO MCP's
    # wit_get_work_item returns. We never fetch a subset — curation is render-only.
    url = f"{org}/_apis/wit/workitems/{wid}?$expand=all&{API}"
    return _get(url, auth)


def batch_items(org: str, ids: list[int], auth: Auth) -> dict[int, dict]:
    """Fetch many work items with ALL fields in one call (no relations — batch
    can't expand relations, so callers needing a node's own links use fetch_item)."""
    if not ids:
        return {}
    out: dict[int, dict] = {}
    for chunk in (ids[i:i + 200] for i in range(0, len(ids), 200)):
        url = f"{org}/_apis/wit/workitemsbatch?{API}"
        payload = json.dumps({"ids": chunk, "$expand": "all"}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Authorization": auth.header,
                     "Content-Type": "application/json",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            sys.exit(f"ADO batch {e.code}: {e.read().decode(errors='replace')[:300]}")
        for it in data.get("value", []):
            out[it["id"]] = it
    return out


def fetch_comments(org: str, project: str, wid: int, auth: Auth) -> list[dict]:
    """All comments for a work item (separate endpoint the MCP exposes as
    wit_list_work_item_comments). Needs the project, read from System.TeamProject."""
    if not project:
        return []
    proj = urllib.parse.quote(project)
    url = f"{org}/{proj}/_apis/wit/workItems/{wid}/comments?api-version=7.1-preview.4"
    data = _get(url, auth)
    return [{"author": (c.get("createdBy") or {}).get("displayName", ""),
             "date": c.get("createdDate", ""),
             "text": _strip_html(c.get("text", ""), limit=10000)}
            for c in data.get("comments", [])]


# ------------------------------------------------------------------- relations
def parse_relations(item: dict) -> dict:
    parent, children, prs = None, [], []
    for rel in item.get("relations", []) or []:
        rtype = rel.get("rel", "")
        url = rel.get("url", "")
        if rtype == HIER_CHILD:
            cid = _id_from_url(url)
            if cid:
                children.append(cid)
        elif rtype == HIER_PARENT:
            parent = _id_from_url(url)
        elif rtype == "ArtifactLink" and "PullRequestId" in url:
            prs.append(_pr_from_artifact(url, rel))
    return {"parent": parent, "children": children, "prs": prs}


def _id_from_url(url: str) -> int | None:
    m = re.search(r"/workItems/(\d+)", url)
    return int(m.group(1)) if m else None


def _pr_from_artifact(url: str, rel: dict) -> dict:
    # vstfs:///Git/PullRequestId/<projGuid>%2F<repoGuid>%2F<prId>
    dec = urllib.parse.unquote(url)
    m = re.search(r"PullRequestId/[^/]+/[^/]+/(\d+)", dec)
    prid = m.group(1) if m else "?"
    name = (rel.get("attributes") or {}).get("name", "Pull Request")
    return {"id": prid, "name": name}


# ---------------------------------------------------------------------- shaping
def _person(field) -> str:
    if isinstance(field, dict):
        return field.get("displayName", "")
    return field or ""


class _ToText(HTMLParser):
    """HTML -> plain text that KEEPS THE NUMBERS.

    The regex strip this replaces threw ordered lists away. ADO's editor writes a
    criteria list as ``<ol><li>...</li></ol>``, so the numbering lives in the
    MARKUP, not the text — and stripping tags turned "criterion 23" into an
    anonymous line in the middle of a wall. Volt stories 3607, 2966 and 3202 all
    number that way; the counter below is what lets a reader (or the heal
    reconciler) say "criterion 23" and have it mean the same thing it means in the
    ADO UI. ``<ol start="n">`` is honoured. Unordered lists become "- ".
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[list] = []          # [kind, next_number]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("ol", "ul"):
            start = 1
            for k, v in attrs:
                if k.lower() == "start":
                    try:
                        start = int(v)
                    except (TypeError, ValueError):
                        pass
            self.stack.append([tag, start])
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n")
            if self.stack and self.stack[-1][0] == "ol":
                frame = self.stack[-1]
                self.parts.append("%d. " % frame[1])
                frame[1] += 1
            else:
                self.parts.append("- ")
        elif tag in ("br", "p", "div", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("ol", "ul"):
            if self.stack:
                self.stack.pop()
            self.parts.append("\n")
        elif tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _plain(s: str) -> str:
    """HTML -> plain text. NEVER truncates. Truncation is a RENDER decision and it
    belongs to the caller that renders, not to the function that reads the field."""
    if not s:
        return ""
    parser = _ToText()
    try:
        parser.feed(s)
        parser.close()
        out = parser.text()
    except Exception:                        # malformed markup must never lose the field
        out = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", s, flags=re.I)
        out = html.unescape(re.sub(r"<[^>]+>", "", out))
    # Trailing spaces are an artifact of the tag boundaries, not content.
    out = "\n".join(line.rstrip() for line in out.splitlines()).strip()
    return re.sub(r"\n{3,}", "\n\n", out)


def _strip_html(s: str, limit: int | None = 600) -> str:
    """Back-compat wrapper: plain text, optionally clipped. `limit=None` means the
    whole field. Callers that must not lose content pass None."""
    out = _plain(s)
    if limit is not None and len(out) > limit:
        out = out[:limit].rstrip() + " ..."
    return out


def count_criteria(text: str) -> int:
    """How many enumerated criteria a plain-text AcceptanceCriteria field declares.

    Counts lines opening with `<n>.` / `<n>)` (an `<ol>`, or hand-typed numbers) and
    lines opening with `- ` (a `<ul>` — several stories on this board bullet their
    criteria instead of numbering them, and a bulleted list of eight is still eight
    criteria). 0 for prose, which is honest: the number is a floor a reader can
    check, never a claim the field is empty."""
    if not text:
        return 0
    return len(re.findall(r"(?m)^\s*(?:\d+[.)]|-)\s+\S", text))


# WHY THE FIELD NAME IS NEVER ALLOWED TO HOLD A PARTIAL VALUE
# ----------------------------------------------------------
# MEASURED 2026-08-26, Volt story 3614. `--json` returned a 604-character
# `acceptance_criteria` for that story -- and for 3607, 2966, 3202 and 3510 too,
# all exactly 604, because `_strip_html`'s 600-char clip plus " ..." lands on the
# same length every time. 3614's real field is 9,237 characters and 33 numbered
# criteria; the clip stopped inside criterion 4. A session read it, believed it had
# the story, and spent hours designing a mechanism criteria 2, 23, 24 and 28
# already specified -- better, and in more detail.
#
# The failure was not the truncation. It was that the truncated value was
# INDISTINGUISHABLE from a complete one: a plausible-looking short field under the
# plain name `acceptance_criteria`, with a trailing " ..." that reads as prose.
# So the rule here is absolute: when the text is clipped, the plain field name is
# ABSENT and the clip lands under `*_preview`, beside a `*_truncated` flag, the
# full character count, the criteria count, and the exact flag that returns the
# rest. A reader keying on `acceptance_criteria` now gets the truth or nothing --
# never a confident fraction.
TEXT_FIELDS = (("description", "System.Description"),
               ("acceptance_criteria", "Microsoft.VSTS.Common.AcceptanceCriteria"))


def _emit_text(node: dict, key: str, raw: str, limit: int | None) -> None:
    """Put one long text field on a node under the rule above. No-op for an empty
    field. `limit=None` emits the complete text under the plain name."""
    text = _plain(raw)
    if not text:
        return
    n_crit = count_criteria(text)
    if limit is None or len(text) <= limit:
        node[key] = text
        if n_crit:
            node[key + "_criteria"] = n_crit
        return
    node[key + "_preview"] = text[:limit].rstrip() + " ..."
    node[key + "_truncated"] = True
    node[key + "_chars"] = len(text)
    if n_crit:
        node[key + "_criteria"] = n_crit
    node.setdefault("truncated", []).append(key)
    node["truncated_hint"] = TRUNCATED_HINT


def node_of(org: str, item: dict, want_desc: bool, full: bool = False,
            clip: int | None = PREVIEW_CHARS) -> dict:
    f = item.get("fields", {})
    wid = f.get("System.Id") or item.get("id")
    n = {
        "id": wid,
        "type": f.get("System.WorkItemType", "?"),
        "title": f.get("System.Title", ""),
        "state": f.get("System.State", ""),
        "assignee": _person(f.get("System.AssignedTo")),
        "project": f.get("System.TeamProject", ""),
        "url": f"{org}/_workitems/edit/{wid}",
        "children": [],
        "prs": [],
    }
    if f.get("System.Tags"):
        n["tags"] = f["System.Tags"]
    if want_desc:
        # `--full` means "nothing dropped", so it must not clip either -- before
        # this, `--full --json` still carried the 604-char clip under the plain
        # name while the truth sat in the raw `fields` bag as HTML.
        limit = None if (full or clip is None) else clip
        for key, field in TEXT_FIELDS:
            _emit_text(n, key, f.get(field, ""), limit)
    if full:
        # Nothing dropped: the complete field bag + relations + links + rev,
        # byte-for-byte what the ADO MCP's wit_get_work_item would return.
        n["fields"] = f
        n["rev"] = item.get("rev")
        if item.get("relations"):
            n["relations"] = item["relations"]
        if item.get("_links"):
            n["_links"] = item["_links"]
    return n


def build_tree(org: str, root_id: int, auth: Auth, depth: int,
               want_desc: bool, include_parent: bool,
               full: bool = False, comments: bool = False,
               clip: int | None = PREVIEW_CHARS) -> dict:
    root = fetch_item(org, root_id, auth)
    rels = parse_relations(root)
    node = node_of(org, root, want_desc, full, clip=clip)
    node["prs"] = rels["prs"]
    if comments:
        node["comments"] = fetch_comments(org, node.get("project", ""), root_id, auth)

    # Descend children breadth-first; each node fetched with $expand=all so its
    # own relations (child PRs, grandchildren) and full field bag are complete.
    frontier = [(node, rels["children"], 1)]
    while frontier:
        parent_node, child_ids, lvl = frontier.pop(0)
        if lvl > depth or not child_ids:
            continue
        for cid in child_ids:
            citem = fetch_item(org, cid, auth)
            crels = parse_relations(citem)
            cnode = node_of(org, citem, want_desc, full, clip=clip)
            cnode["prs"] = crels["prs"]
            if comments:
                cnode["comments"] = fetch_comments(org, cnode.get("project", ""), cid, auth)
            parent_node["children"].append(cnode)
            if crels["children"]:
                frontier.append((cnode, crels["children"], lvl + 1))

    result = {"root": node}
    if include_parent and rels["parent"]:
        pit = fetch_item(org, rels["parent"], auth)
        result["parent"] = node_of(org, pit, want_desc, full, clip=clip)
    return result


# ----------------------------------------------------------------------- render
def _size_note(node: dict, key: str) -> str:
    """"33 criteria, 9237 chars — 604 shown, --no-clip for the rest" — the one line
    that would have stopped the 3614 miss. Empty when there is nothing to warn about
    (short field, no numbered criteria)."""
    bits = []
    n_crit = node.get(key + "_criteria")
    if n_crit:
        bits.append(f"{n_crit} criteria")
    if node.get(key + "_truncated"):
        bits.append(f"{node.get(key + '_chars', '?')} chars, "
                    f"{len(node.get(key + '_preview', ''))} shown — --no-clip for the rest")
    elif bits:
        text = node.get(key) or ""
        bits.append(f"{len(text)} chars, full text present")
    return ", ".join(bits)


STATE_MARK = {"Done": "✓", "Closed": "✓", "Removed": "×", "Resolved": "✓"}


def render_md(tree: dict, org: str) -> str:
    lines = []
    parent = tree.get("parent")
    if parent:
        lines.append(f"^ parent {parent['type']} #{parent['id']} - {parent['title']}  "
                     f"[{parent['state']}]")
        lines.append(f"  {parent['url']}")
        lines.append("")

    def emit(n: dict, indent: str):
        mark = STATE_MARK.get(n["state"], "*")
        who = f" ({n['assignee']})" if n.get("assignee") else ""
        lines.append(f"{indent}{mark} {n['type']} #{n['id']} - {n['title']}  "
                     f"[{n['state']}]{who}")
        lines.append(f"{indent}   {n['url']}")
        for pr in n.get("prs", []):
            lines.append(f"{indent}   -> PR !{pr['id']} - {pr['name']}")
        for key, label in (("description", "..."), ("acceptance_criteria", "AC:")):
            text = n.get(key) or n.get(key + "_preview")
            if not text:
                continue
            lines.append(f"{indent}   {label} {text.splitlines()[0]}")
            # The md view shows ONE line of a field that can run to thousands of
            # characters, so it always says how much it is not showing. A reader
            # who sees "33 criteria" cannot mistake one line for the story.
            note = _size_note(n, key)
            if note:
                lines.append(f"{indent}       [{note}]")
        for c in n.get("children", []):
            emit(c, indent + "  ")

    emit(tree["root"], "")
    return "\n".join(lines)


# ------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(prog="ado-tree",
                                 description="Fetch an ADO work-item tree cheaply.")
    ap.add_argument("id", type=int, help="work item id")
    ap.add_argument("--org", default=None, help="ADO org url (else config/env)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of md tree")
    ap.add_argument("--full", action="store_true",
                    help="JSON with EVERY field/relation/link per node — nothing "
                         "dropped (parity with the ADO MCP). Implies --json.")
    ap.add_argument("--comments", action="store_true",
                    help="also fetch each item's comments (extra call per node)")
    ap.add_argument("--depth", type=int, default=3, help="child recursion depth")
    ap.add_argument("--no-desc", action="store_true", help="omit description/AC")
    ap.add_argument("--no-clip", action="store_true",
                    help="emit description/AC in FULL. Without it the compact view "
                         "clips them to %d chars and says so — the clipped text "
                         "lands under <field>_preview with <field>_truncated, "
                         "<field>_chars and <field>_criteria beside it, and the "
                         "plain field name is absent." % PREVIEW_CHARS)
    ap.add_argument("--no-parent", action="store_true", help="omit parent lookup")
    ap.add_argument("--login", action="store_true", help="auto `az login` if no cred")
    args = ap.parse_args()

    org = args.org or default_org()
    if not org:
        sys.exit(NO_ORG_HINT)
    auth = resolve_auth(allow_login=args.login)
    sys.stderr.write(f"[auth: {auth.kind}]  [org: {org}]\n")
    tree = build_tree(org, args.id, auth, depth=args.depth,
                      want_desc=not args.no_desc, include_parent=not args.no_parent,
                      full=args.full, comments=args.comments,
                      clip=None if args.no_clip else PREVIEW_CHARS)
    if args.json or args.full:
        print(json.dumps(tree, indent=2, ensure_ascii=False))
    else:
        print(render_md(tree, org))


if __name__ == "__main__":
    main()
