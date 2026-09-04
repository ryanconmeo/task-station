# treeref.py
"""THE DECLARED EXECUTION CONTEXT — a condition says WHICH REPO and WHICH REF it is
about, as data, and the runner resolves that ref and evaluates the command in a detached
checkout of it.

WHY THIS EXISTS, and it is the half of #595 that shipped without its other half. 3.49.0
made `returncode == 0` a required conjunct, so a green condition means THE COMMAND
SUCCEEDED. It still did not mean "against the tree it was meant to read". The runner
executed every command in whatever working directory it inherited, so:

  * a condition could pass against the MAIN CHECKOUT while claiming to prove something
    about a worktree — observed on #602, where `cd <worktree> || cd <main>` exits 0 from
    main and the probe went green from a tree that did not contain the work;
  * `merge-gated` was SELF-DECLARED. The author typed `--merge-gated` and every reader
    took their word for it, because nothing in the store said which ref the command read;
  * and the workaround people reached for — a hand-written `*-repo.sh` resolver per task,
    NINE of them in ~/.task-station/checker when this was written, reached by 50 live
    stored commands — exists ONLY because a condition inherits its tree. Every one of
    those scripts is a private re-implementation of this module, and two of the nine took
    a `$TS_REPO` environment override, which is the redirection 591:4 ruled against.

THE SHAPE. Two additive keys on the stored condition (or claim), both plain data:

    {"cmd": "…", "expect": ["…"], "repo": "/abs/path", "ref": "origin/main",
     "refname": "refs/remotes/origin/main"}

`repo` and `ref` are what the author declared. `refname` is what the RUNNER resolved that
ref to at registration time — the full symbolic name (`refs/remotes/origin/main`,
`refs/heads/main`) or, for a raw commit, the sha. It is stored because it is the only
thing that lets `merge_gated` be COMPUTED from a pure string at read time.

MERGE-GATED IS COMPUTED, NEVER ASSERTED (this is #604's step 4). The rule is exact and
does no guessing: a ref is a merge target when its resolved name is a REMOTE-TRACKING ref,
`refs/remotes/…`. That is precisely the class of ref an author cannot move by committing
locally — they have to push, and somebody has to merge. A local branch, a tag, a raw
commit sha and `HEAD` are all author-movable and are therefore NOT merge targets. Nothing
here reads a flag the author typed.

WHY THE RULE IS A STRING TEST AND THE NORMALISATION HAPPENS AT REGISTRATION. `exit-show`,
`turn`, the board and the gate all read `merge_gated`, and `checker`'s fourth rule is that
a read surface spawns no git subprocess. Resolving `origin/main` needs git; testing
`refs/remotes/` does not. So git runs ONCE, when the declaration is written, and every
read afterwards is free.

NO REDIRECTION, AND THAT IS THE POINT (591:4). Nothing in this module reads the
environment. There is no `TS_REPO`, no `--ref` on `exit-tick`, no override of any kind:
the declaration is written once, at registration, and the runner obeys it. A run-time
override is exactly what turns a gate into one you can talk into passing, and both
`exit-tick` and `claims verify` STORE their verdict — so a single redirected rehearsal
leaves a green on the record that outlives the report that caveated it.

AND A CHECKOUT THAT FAILS NEVER FALLS BACK TO THE CWD. If the repo has gone, or the ref no
longer resolves, the condition does NOT run: status `error`, which the checker's second
rule turns into `unknown`, which refutes nothing and moves no tick. Falling back to the
inherited directory would be this module's own defect wearing its fix as a disguise.

LEGACY IS TOLERATED INDEFINITELY. A condition with no `repo`/`ref` runs exactly as it did
before this module existed — in the inherited cwd — and its author-typed `merge_gated`
flag is still honoured. Measured across the whole stored corpus the day this landed: 336
stored commands, of which 159 name a repo or a ref in PROSE, 169 hand the decision off to
a hub-side checker script, and 8 simply inherit. NO BACKFILL IS POSSIBLE for any of them —
nothing can recover, from a command string, which tree its author meant — so the benefit
arrives gradually, as new conditions are written.

Stdlib only, and it imports NOTHING from this package — the same leaf discipline
`gating.py` keeps, for the same reason: a condition proving itself out of a git object can
import a leaf and cannot import the store.
"""
import os
import shutil
import subprocess
import tempfile

# The two keys an author declares, and the one the runner derives from them.
DECLARED_KEYS = ("repo", "ref")
RESOLVED_KEY = "refname"

# The prefix that makes a ref a MERGE TARGET. Exact, not a heuristic: git writes every
# remote-tracking ref under this namespace and writes nothing else there.
REMOTE_PREFIX = "refs/remotes/"

# How long any one git invocation here may take. These are local plumbing reads and a
# checkout; a minute is generous and bounds a wedged git rather than tuning anything.
GIT_TIMEOUT = 60

# The prefix every throwaway checkout directory carries, so a leaked one is identifiable
# by name rather than by guesswork.
TMP_PREFIX = "ts-condition-tree-"


def _git(repo, *args, **kw):
    """`(ok, output)` for `git -C <repo> <args>`, output stripped, never raising.

    Fail-CLOSED for the caller's purposes: a git that could not run returns `(False, …)`
    and every caller here turns that into a refusal or a non-run, never into a fallback."""
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                           timeout=kw.get("timeout", GIT_TIMEOUT))
    except (OSError, subprocess.SubprocessError) as exc:      # noqa: BLE001
        return False, str(exc)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, out


# -- reading a declaration ---------------------------------------------------------

def declaration(raw):
    """The declared `{"repo", "ref", "refname"}` on a stored condition/claim dict, or
    None when it declares no tree.

    FILTERS GARBAGE RATHER THAN RAISING, the house rule every reader of the store keeps:
    a half-written declaration cannot name a tree, and a store this module did not write
    is never a reason to break a render. Both `repo` and `ref` are required — a repo with
    no ref would mean "whatever is checked out there", which is the ambiguity this module
    exists to remove."""
    if not isinstance(raw, dict):
        return None
    repo = str(raw.get("repo") or "").strip()
    ref = str(raw.get("ref") or "").strip()
    if not repo or not ref:
        return None
    out = {"repo": repo, "ref": ref}
    refname = str(raw.get(RESOLVED_KEY) or "").strip()
    out[RESOLVED_KEY] = refname or ref
    return out


def is_merge_target(refname):
    """True iff `refname` names a REMOTE-TRACKING ref, which is the whole definition of a
    merge target: a ref whose author cannot move it without pushing, so it can only carry
    this work once somebody has merged.

    A pure string test on the name the runner resolved at registration. No git, no
    environment, no guessing at a remote's name."""
    return str(refname or "").startswith(REMOTE_PREFIX)


def merge_gated(decl):
    """True iff this declaration reads the merge target. `None` in, False out."""
    return bool(decl) and is_merge_target(decl.get(RESOLVED_KEY) or decl.get("ref"))


def label(decl):
    """`'<repo> @ <ref>'` — the one spelling every surface prints, so `exit-show`,
    `claims` and the gate cannot describe the same declaration three ways."""
    if not decl:
        return ""
    return "%s @ %s" % (decl["repo"], decl["ref"])


def long_label(decl):
    """`label`, plus the resolved name in brackets when it says something the author's
    ref did not. A ref that resolved to ITSELF — a raw sha — gets no bracket: printing
    `abc123 (abc123)` teaches the reader nothing and trains them to skip the line."""
    if not decl:
        return ""
    resolved = decl.get(RESOLVED_KEY) or decl["ref"]
    if resolved == decl["ref"]:
        return label(decl)
    return "%s (%s)" % (label(decl), resolved)


# -- writing a declaration ---------------------------------------------------------

def parse(repo, ref):
    """Validate an author's `--repo`/`--ref` into a storable declaration.
    `(declaration, error)` — exactly one of the two is None.

    FOUR REFUSALS, and each one is a condition that would otherwise be worse than absent:

      * ONE WITHOUT THE OTHER. A repo with no ref means "whatever is checked out there",
        which is the inherited-cwd ambiguity again with a path in front of it.
      * A RELATIVE REPO PATH. It would resolve against whatever directory the session
        happened to start in, so the same stored value would name different repos from
        different shells — `claims --bind`'s rule, for the same reason.
      * A PATH THAT IS NOT A GIT REPO. There is no ref to resolve, so the condition could
        only ever fail for a reason having nothing to do with the work.
      * A REF THAT DOES NOT RESOLVE. This one is not merely tidiness: `merge_gated` is
        COMPUTED from the resolved name, and a ref nothing can resolve has no name to
        compute from. There is deliberately no force flag — the moment an author can
        store an unresolvable ref, merge-gatedness is back to being their assertion.

    Resolution runs git ONCE, here, and stores the answer, so every later read is free."""
    r = str(repo or "").strip()
    f = str(ref or "").strip()
    if not r and not f:
        return None, None                       # declared nothing: legitimate and common
    if not r or not f:
        return None, ("--repo and --ref go together: a repo with no ref means whatever "
                      "happens to be checked out there, which is the ambiguity declaring "
                      "a tree exists to remove.")
    r = os.path.expanduser(r)
    if not os.path.isabs(r):
        return None, ("--repo takes an ABSOLUTE path (got %r) — a relative one would name "
                      "a different repo from every directory." % repo)
    r = os.path.normpath(r)
    ok, out = _git(r, "rev-parse", "--git-dir")
    if not ok:
        return None, ("--repo %s is not a git repository (git said: %s). There is no ref "
                      "to resolve there, so the condition could only ever go red for a "
                      "reason having nothing to do with the work." % (r, out or "nothing"))
    ok, full = _git(r, "rev-parse", "--symbolic-full-name", f)
    if not ok or not full:
        # Not a symbolic ref. It may still be a commit-ish (a sha, or HEAD~2).
        ok, sha = _git(r, "rev-parse", "--verify", "--quiet", "%s^{commit}" % f)
        if not ok or not sha:
            return None, ("--ref %r does not resolve in %s. merge-gated is COMPUTED from "
                          "the resolved ref, so a ref nothing can resolve has nothing to "
                          "compute from — and there is no flag to assert it by hand."
                          % (ref, r))
        full = sha.split()[0]
    return {"repo": r, "ref": f, RESOLVED_KEY: full.split()[0]}, None


def store_into(block, decl):
    """Write `decl` onto a stored condition/claim `block`, or clear the keys when it is
    None. The ONE writer of these keys, so a surface cannot half-write a declaration."""
    for key in list(DECLARED_KEYS) + [RESOLVED_KEY]:
        block.pop(key, None)
    if decl:
        block["repo"] = decl["repo"]
        block["ref"] = decl["ref"]
        block[RESOLVED_KEY] = decl[RESOLVED_KEY]
    return block


# -- the detached checkout ---------------------------------------------------------

class Trees(object):
    """Materialises ONE detached checkout per `(repo, refname)` and tears them all down.

    ONE PER PAIR, NOT ONE PER CONDITION. A task with six conditions against the same ref
    would otherwise check the same commit out six times; and, worse, six checkouts could
    in principle be six different commits if the ref moved mid-run, so one run of
    `exit-tick` could report on two trees while saying it reported on one.

    THE MECHANISM IS `git worktree add --detach`, and it is a real checkout rather than a
    `git archive` extraction, because a condition legitimately runs git — the suite, a
    release script, a `git log` assertion. Detached, so nothing here can move a branch.
    The source repo's worktree list is the only thing touched, and it is pruned before and
    removed after; a checkout leaked by a kill -9 is named `ts-condition-tree-*` and is
    cleared by the next run's prune.

    NOTHING IS CACHED ACROSS INVOCATIONS. A stale checkout would be the same lie in a
    different place: the ref is resolved fresh every time the runner runs."""

    def __init__(self, git=None):
        self._git = git or _git
        self._made = {}                 # (repo, refname) -> {"path", "sha"}
        self._roots = []                # every tmpdir this instance created
        self._repos = set()             # every repo it added a worktree to

    def tree(self, decl):
        """`(cwd, sha, error)` for one declaration — the directory the command must run
        in, and the commit it is a checkout of.

        A FAILURE IS NEVER A FALLBACK. Every error path returns `(None, None, reason)` and
        the caller reports the condition as NOT RUN. Returning the inherited cwd here
        would reproduce, inside the fix, the exact defect the fix is for."""
        if not decl:
            return None, None, None
        key = (decl["repo"], decl[RESOLVED_KEY])
        made = self._made.get(key)
        if made:
            return made["path"], made["sha"], None
        repo, ref = decl["repo"], decl[RESOLVED_KEY]
        if not os.path.isdir(repo):
            return None, None, "the declared repo %s is not there" % repo
        ok, sha = self._git(repo, "rev-parse", "--verify", "--quiet", "%s^{commit}" % ref)
        if not ok or not sha:
            return None, None, ("%s does not resolve in %s any more" % (decl["ref"], repo))
        sha = sha.split()[0]
        root = tempfile.mkdtemp(prefix=TMP_PREFIX)
        self._roots.append(root)
        path = os.path.join(root, "tree")
        self._git(repo, "worktree", "prune")        # clear anything a kill -9 left behind
        ok, out = self._git(repo, "worktree", "add", "--detach", "--quiet", path, sha)
        if not ok or not os.path.isdir(path):
            return None, None, ("could not check %s out of %s: %s"
                                % (decl["ref"], repo, out or "git said nothing"))
        self._repos.add(repo)
        self._made[key] = {"path": path, "sha": sha}
        return path, sha, None

    def close(self):
        """Remove every checkout this instance made. Never raises — a teardown that threw
        would turn a finished verification into a crash, and the temp dirs are named so a
        leak is diagnosable rather than mysterious."""
        for (repo, _ref), made in list(self._made.items()):
            self._git(repo, "worktree", "remove", "--force", made["path"])
        for repo in self._repos:
            self._git(repo, "worktree", "prune")
        for root in self._roots:
            shutil.rmtree(root, ignore_errors=True)
        self._made, self._roots, self._repos = {}, [], set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# -- the provenance a run records --------------------------------------------------

def provenance(decl, sha):
    """`{"repo", "ref", "sha"}` — what the runner ACTUALLY read, recorded beside the
    verdict.

    THIS IS THE EVIDENCE HALF, and it is the reason a stored green means something now. A
    verdict on the record used to say only "the command succeeded"; a reader could not
    tell which tree produced it, and neither could the next session. It is written only
    when a tree was declared and checked out — a legacy condition's provenance is
    UNRECORDED, and `None` is what readers get, never an invented default. Reporting an
    unmeasured tree would be putting a claim into the record that nothing measured, which
    is the failure this whole task is named after."""
    if not decl or not sha:
        return None
    return {"repo": decl["repo"], "ref": decl["ref"], "sha": sha}


def provenance_line(rec):
    """One line naming the tree a stored verdict was produced against, or None.

    None rather than "unknown" so a surface prints NOTHING for a legacy result: a line
    saying the tree is unrecorded on every one of 170 conditions is a line readers learn
    to skip, and `exit-show` already says elsewhere that nothing was declared."""
    if not isinstance(rec, dict):
        return None
    repo = str(rec.get("repo") or "").strip()
    ref = str(rec.get("ref") or "").strip()
    sha = str(rec.get("sha") or "").strip()
    if not repo or not ref or not sha:
        return None
    return "read %s @ %s = %s" % (repo, ref, sha[:12])
