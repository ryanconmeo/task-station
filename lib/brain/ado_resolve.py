#!/usr/bin/env python3
"""ado-resolve — resolve a bare number to its ADO link(s) (work item and/or PR).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``scripts/ado_resolve.py`` @ 0.14.0. Verbatim apart from the import: the
``sys.path`` self-bootstrap is gone and ``from ado_tree import …`` is now the
RELATIVE sibling ``from .ado_tree import …``, so the two halves of the pair stay
one auth/org implementation. One statement is added, for the case the source
could not have: no configured org (it had a built-in default; this repo does
not) still prints exactly one line of JSON.

Feeds a "type a number -> ADO link" popup: work items and pull requests are
separate ID spaces, so a number may be one, the other, both, or neither.

Always prints exactly one line of JSON to stdout, never a traceback:
  {"query": n, "matches": [...], "error": null}
  {"query": n, "matches": [], "error": "no ADO credential", "hint": "run: az login"}

Layer rule: brain may import core and its own siblings, never board. Stdlib +
the sibling ``ado_tree`` only.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from .ado_tree import Auth, NO_ORG_HINT, default_org, resolve_auth

API = "api-version=7.1"


def _try_get(url: str, auth: Auth) -> dict | None:
    """Like ado_tree._get, but non-fatal: a 404 just means "not that kind"."""
    req = urllib.request.Request(url, headers={"Authorization": auth.header,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise  # auth problem — let caller surface it
        return None  # 404/etc -> no such item of this kind
    except urllib.error.URLError:
        return None


def resolve_workitem(org: str, n: int, auth: Auth) -> dict | None:
    url = f"{org}/_apis/wit/workitems/{n}?{API}"
    item = _try_get(url, auth)
    if item is None:
        return None
    f = item["fields"]
    return {
        "kind": "workitem",
        "id": n,
        "title": f["System.Title"],
        "state": f["System.State"],
        "type": f["System.WorkItemType"],
        "url": f"{org}/_workitems/edit/{n}",
    }


def resolve_pr(org: str, n: int, auth: Auth) -> dict | None:
    url = f"{org}/_apis/git/pullrequests/{n}?{API}"
    d = _try_get(url, auth)
    if d is None:
        return None
    repo = d["repository"]["name"]
    proj = d["repository"]["project"]["name"]
    return {
        "kind": "pr",
        "id": n,
        "title": d["title"],
        "state": d["status"],
        "url": f"{org}/{proj}/_git/{repo}/pullrequest/{n}",
    }


def resolve(org: str, n: int, auth: Auth) -> dict:
    matches = []
    wi = resolve_workitem(org, n, auth)
    if wi:
        matches.append(wi)
    pr = resolve_pr(org, n, auth)
    if pr:
        matches.append(pr)
    return {"query": n, "matches": matches, "error": None}


def main() -> None:
    ap = argparse.ArgumentParser(prog="ado-resolve",
                                 description="Resolve a number to its ADO link(s).")
    ap.add_argument("number", type=int)
    ap.add_argument("--org", default=None, help="ADO org url (else config/env)")
    args = ap.parse_args()

    org = args.org or default_org()
    n = args.number
    if not org:  # one line of JSON on every path, including this one
        print(json.dumps({"query": n, "matches": [], "error": "no ADO organization",
                          "hint": NO_ORG_HINT}))
        return
    try:
        auth = resolve_auth(allow_login=False)  # never block the popup on a browser
    except SystemExit:
        print(json.dumps({"query": n, "matches": [], "error": "no ADO credential",
                          "hint": "run: az login"}))
        return

    print(json.dumps(resolve(org, n, auth), ensure_ascii=False))


if __name__ == "__main__":
    main()
