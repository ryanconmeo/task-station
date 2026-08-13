"""brain-station — shared brain repo + ACL setup.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``scripts/publish_setup.py`` @ 0.14.0.

PRINTS (default) or executes (``--execute``) the exact three-step setup for a
per-person shared brain mirror repo in Azure DevOps:

  1. create the repo (``az repos create``);
  2. replace its ACL via the ``SetAccessControlLists`` POST — inheritance OFF,
     ACEs **allow-only** (owner full, Contributors read, Readers read), NO
     denies;
  3. set the default branch after the first push.

Why allow-only + inheritance off (the deny-wins rationale) — see ``--help``.

``--execute`` uses ``az rest`` / the ADO REST API with a bearer token the
CALLER supplies (env ``AZURE_DEVOPS_EXT_PAT`` or ``--token-cmd``); this module
never acquires credentials itself and never runs the network in tests.

Layer rule: brain may import core and its own siblings, never board. Stdlib
only — this module reads no config and touches no vault. Python 3.9+.
"""
import argparse
import json
import os
import subprocess
import sys

# The Git Repositories security namespace (stable ADO GUID).
GIT_REPO_NAMESPACE = "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87"
# Public Azure DevOps app id — the --resource for az rest. An identifier, not a
# secret: it is the same value on every tenant.
ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"

# Permission bitmasks for the Git namespace.
OWNER_ALLOW = 65535        # all bits — full control for the mirror owner
CONTRIBUTORS_ALLOW = 2     # read
READERS_ALLOW = 2          # read

DENY_WINS_RATIONALE = """\
Deny-wins rationale (why the ACL is allow-only with inheritance OFF)
-------------------------------------------------------------------
In Azure DevOps a DENY always beats an ALLOW when a principal's permission is
resolved across all of its group memberships. If you tried to hide this repo
from other groups by adding denies, anyone who is BOTH a Reader and (say) a
project admin would be denied too — the deny wins over their admin allow. And
denies interact unpredictably as memberships change.

The safe construction is therefore:
  * inheritPermissions = false  — break inheritance so project-wide grants do
    not leak into this repo;
  * ACEs are ALLOW-ONLY         — grant exactly the three principals you want
    (owner full, Contributors read, Readers read) and NO ONE else; add no
    denies at all.
With inheritance broken and only allows present, the effective permission for
any principal not listed is "no access" by default — no deny needed, and no
deny to backfire.
"""


def ace(descriptor, allow):
    """One allow-only ACE (deny explicitly 0 — never a positive deny bit)."""
    return {"descriptor": descriptor, "allow": allow, "deny": 0}


def acl_body(project_id, repo_id, owner_desc, contributors_desc, readers_desc):
    """The exact ``SetAccessControlLists`` POST body: inheritance off, allow-only
    ACEs for owner/Contributors/Readers, scoped to this one repo's token."""
    return {
        "value": [
            {
                "token": f"repoV2/{project_id}/{repo_id}",
                "merge": False,
                "inheritPermissions": False,
                "acesDictionary": {
                    owner_desc: ace(owner_desc, OWNER_ALLOW),
                    contributors_desc: ace(contributors_desc, CONTRIBUTORS_ALLOW),
                    readers_desc: ace(readers_desc, READERS_ALLOW),
                },
            }
        ]
    }


def build_plan(args):
    """Return ``(steps, acl_json_body)``. ``steps`` is a list of
    ``(title, [lines])``. Placeholders are used for ids/descriptors not yet
    known at print time (the repo does not exist until step 1)."""
    org_url = f"https://dev.azure.com/{args.org}"
    project, repo, owner = args.project, args.repo, args.owner
    project_id = args.project_id or "<projectId>"
    repo_id = args.repo_id or "<repoId>"
    owner_desc = args.owner_descriptor or f"<identity descriptor for {owner}>"
    contributors_desc = args.contributors_descriptor or f"<descriptor for [{project}]\\Contributors>"
    readers_desc = args.readers_descriptor or f"<descriptor for [{project}]\\Readers>"

    body = acl_body(project_id, repo_id, owner_desc, contributors_desc, readers_desc)
    acl_uri = f"{org_url}/_apis/accesscontrollists/{GIT_REPO_NAMESPACE}?api-version=7.1"

    steps = [
        ("1. Create the repo", [
            f"az repos create --name {repo} --project {project} --org {org_url}",
            "# then capture: your projectId and the new repoId "
            "(the ACL token is repoV2/<projectId>/<repoId>),",
            "# and resolve identity descriptors for the owner + the "
            f"[{project}]\\Contributors and [{project}]\\Readers groups.",
        ]),
        ("2. Replace the ACL — inheritance OFF, allow-only, NO denies", [
            f"POST {acl_uri}",
            "body:",
            json.dumps(body, indent=2),
            "",
            "# e.g. via az rest (write the body above to acl.json first):",
            f"az rest --method POST --resource {ADO_RESOURCE} \\",
            f"  --uri '{acl_uri}' \\",
            "  --headers 'Content-Type=application/json' --body @acl.json",
        ]),
        ("3. Set the default branch (after the first push)", [
            f"az repos update --repository {repo} --project {project} "
            f"--org {org_url} --default-branch main",
        ]),
    ]
    return steps, body


def _print_plan(steps):
    for title, lines in steps:
        print(title)
        for ln in lines:
            print(f"  {ln}" if ln else "")
        print()


def _token(args):
    """Bearer token for --execute: env AZURE_DEVOPS_EXT_PAT, or run --token-cmd."""
    tok = os.environ.get("AZURE_DEVOPS_EXT_PAT")
    if tok:
        return tok.strip()
    if args.token_cmd:
        r = subprocess.run(args.token_cmd, shell=True, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def _execute(args, steps, body):
    """Best-effort executor (never exercised in tests). Requires a token."""
    tok = _token(args)
    if not tok:
        sys.exit("brain-publish-setup --execute: no token "
                 "(set AZURE_DEVOPS_EXT_PAT or pass --token-cmd)")
    # az respects AZURE_DEVOPS_EXT_PAT in the environment for auth.
    env = dict(os.environ, AZURE_DEVOPS_EXT_PAT=tok)
    org_url = f"https://dev.azure.com/{args.org}"
    print("brain-publish-setup --execute: creating repo …")
    subprocess.run(["az", "repos", "create", "--name", args.repo, "--project", args.project,
                    "--org", org_url], env=env, check=False)
    print("brain-publish-setup --execute: replacing ACL … (review the printed body first)")
    _print_plan(steps)
    print("NOTE: rerun with resolved --project-id/--repo-id/--*-descriptor to POST a concrete ACL, "
          "then set the default branch (step 3).")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="brain-publish-setup", description=__doc__, epilog=DENY_WINS_RATIONALE,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", required=True, help="ADO org (the <org> in dev.azure.com/<org>)")
    ap.add_argument("--project", required=True, help="ADO project")
    ap.add_argument("--repo", required=True, help="the <alias>-brain-shared repo name to create")
    ap.add_argument("--owner", required=True, help="owner UPN (gets full control)")
    ap.add_argument("--project-id", help="resolved projectId (for a concrete ACL token)")
    ap.add_argument("--repo-id", help="resolved repoId (for a concrete ACL token)")
    ap.add_argument("--owner-descriptor", help="resolved identity descriptor for the owner")
    ap.add_argument("--contributors-descriptor", help="resolved descriptor for the Contributors group")
    ap.add_argument("--readers-descriptor", help="resolved descriptor for the Readers group")
    ap.add_argument("--execute", action="store_true", help="run the steps (default: print only)")
    ap.add_argument("--token-cmd", help="shell command that prints a bearer token (else AZURE_DEVOPS_EXT_PAT)")
    a = ap.parse_args(argv)

    steps, body = build_plan(a)
    if a.execute:
        _execute(a, steps, body)
    else:
        print("# Shared brain setup — DRY RUN (pass --execute to run). "
              "See --help for the deny-wins rationale.\n")
        _print_plan(steps)
    return 0


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the entry point
    sys.exit(main())
