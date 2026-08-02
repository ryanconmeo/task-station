"""artifacts.py — forge-agnostic PR / work-item URL capture (task #444, F6).

The ONE place the PR/work-item URL patterns live. `scan(text)` pulls every PR and
work-item URL out of arbitrary text (an MCP/Bash tool RESULT) → deduped
`[{url, id, repo, kind}]`, so a created artifact becomes searchable + board-rendered
without the model having to remember to record it.

Forge-agnostic by design — the pattern LIST is additive: GitHub, Azure DevOps (dev.azure
+ *.visualstudio.com + the generic `_git/…/pullrequest` form), GitLab, Bitbucket. Add a
forge = add a pattern; everything downstream (capture, cross-person auto-link) follows.

`id` is a STABLE, cross-brain-matchable signal id — for PRs it MUST agree with
`feeds._pr_signal_id` (that's the join key the feeds carry), so a captured PR auto-links
to a peer's task that references the same PR. stdlib-only, pure, never raises."""
import re

# ---- PR (code-review) URL forms --------------------------------------------
PR_PATTERNS = [
    r'https://github\.com/[\w.-]+/[\w.-]+/pull/\d+',
    r'https://dev\.azure\.com/[\w%./+-]+?/pullrequest/\d+',
    r'https://[\w.-]+\.visualstudio\.com/[\w%./+-]+?/pullrequest/\d+',
    r'https://[\w.-]+/[\w%./+-]+?/_git/[\w%./+-]+?/pullrequest/\d+',
    r'https://gitlab\.com/[\w./-]+?/-/merge_requests/\d+',
    r'https://bitbucket\.org/[\w.-]+/[\w.-]+/pull-requests/\d+',
]

# ---- work-item / story / issue URL forms -----------------------------------
STORY_PATTERNS = [
    r'https://dev\.azure\.com/[\w%./+-]+?/_workitems/edit/\d+',
    r'https://[\w.-]+\.visualstudio\.com/[\w%./+-]+?/_workitems/edit/\d+',
    r'https://[\w.-]+/[\w%./+-]+?/_workitems/edit/\d+',
    r'https://github\.com/[\w.-]+/[\w.-]+/issues/\d+',
    r'https://gitlab\.com/[\w./-]+?/-/issues/\d+',
]

_PR_RE = re.compile("|".join("(?:%s)" % p for p in PR_PATTERNS), re.I)
_STORY_RE = re.compile("|".join("(?:%s)" % p for p in STORY_PATTERNS), re.I)


def pr_signal_id(url):
    """Stable PR signal id — MUST match `feeds._pr_signal_id` (the feeds' join key):
    GitHub `.../o/r/pull/3` → `o/r#3`; ADO/generic `.../pullrequest/12` → `ado!12`;
    GitLab MR → `o/r!5`; else the last path segment. (GitLab/Bitbucket forms are
    recognized HERE but fall through to the last-path-segment rule in
    `feeds._pr_signal_id`, whose output is frozen — see the note there.)"""
    u = (url or "").strip()
    if not u:
        return ""
    m = re.search(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)", u, re.I)
    if m:
        return "%s/%s#%s" % (m.group(1), m.group(2), m.group(3))
    m = re.search(r"gitlab\.com/([\w./-]+?)/-/merge_requests/(\d+)", u, re.I)
    if m:
        return "%s!%s" % (m.group(1), m.group(2))
    m = re.search(r"/pull-requests/(\d+)", u, re.I)
    if m:
        return "bb!%s" % m.group(1)
    m = re.search(r"/pullrequest/(\d+)", u, re.I)
    if m:
        return "ado!%s" % m.group(1)
    return u.rstrip("/").rsplit("/", 1)[-1] or u


def story_signal_id(url):
    """Stable work-item signal id: ADO `_workitems/edit/123` → `ado#123`; GitHub/GitLab
    issue `.../issues/9` → `o/r#9`; else the last path segment."""
    u = (url or "").strip()
    if not u:
        return ""
    m = re.search(r"/_workitems/edit/(\d+)", u, re.I)
    if m:
        return "ado#%s" % m.group(1)
    m = re.search(r"github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)", u, re.I)
    if m:
        return "%s/%s#%s" % (m.group(1), m.group(2), m.group(3))
    m = re.search(r"gitlab\.com/([\w./-]+?)/-/issues/(\d+)", u, re.I)
    if m:
        return "%s#%s" % (m.group(1), m.group(2))
    return u.rstrip("/").rsplit("/", 1)[-1] or u


def repo_of(url):
    """A best-effort repo/project label for the artifact ("owner/repo", an ADO project,
    or ""). Display-only — never a join key."""
    u = (url or "").strip()
    m = re.search(r"github\.com/([\w.-]+/[\w.-]+)/(?:pull|issues)/", u, re.I)
    if m:
        return m.group(1)
    m = re.search(r"/_git/([\w%.+-]+)/pullrequest/", u, re.I)
    if m:
        return m.group(1)
    m = re.search(r"gitlab\.com/([\w./-]+?)/-/", u, re.I)
    if m:
        return m.group(1)
    m = re.search(r"bitbucket\.org/([\w.-]+/[\w.-]+)/", u, re.I)
    if m:
        return m.group(1)
    m = re.search(r"dev\.azure\.com/([\w%.+-]+/[\w%.+-]+)/", u, re.I)
    if m:
        return m.group(1)
    return ""


def scan(text):
    """Every PR + work-item URL in `text` → `[{url, id, repo, kind}]`, deduped by url in
    first-seen order (PRs before stories). `kind` is 'pr' | 'story'. Pure; never raises."""
    text = text or ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return []
    out, seen = [], set()
    for rx, kind, idfn in ((_PR_RE, "pr", pr_signal_id),
                           (_STORY_RE, "story", story_signal_id)):
        for m in rx.finditer(text):
            u = m.group(0)
            if u in seen:
                continue
            seen.add(u)
            out.append({"url": u, "id": idfn(u), "repo": repo_of(u), "kind": kind})
    return out
