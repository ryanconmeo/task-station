"""brain.org_setup — the org-setup wizard: four read-only scans, six answers, one profile.

WHAT THIS IS FOR. Everything in this toolchain that knows an organisation's own
words — the naming registry's domains, the context-injection keywords, the tier
labels, the forge that gates promotion — reads them at runtime from an
**OrgProfile**. Until now the only way to get one was to write it by hand, which
means a leader at a company that is not the one this toolchain grew up in has to
first learn a schema before they can learn anything else. This module is the
other door: one command, and the org's vocabulary is read off systems it already
runs.

THE SPLIT THE WHOLE DESIGN TURNS ON. An org's vocabulary is already written down
in four places, and its *decisions* are written down nowhere:

  * **Discoverable** — the words. Schema names, group names, repo names, page
    names. Four scans read them (:func:`scan_database`, :func:`scan_directory`,
    :func:`scan_forge`, :func:`scan_wiki`).
  * **Undiscoverable** — the six choices in :data:`LEADER_ANSWERS`. No scan can
    find out which repo *should* hold the org brain or who *may* approve a
    promotion. Guessing them would be worse than asking, because a wrong guess
    is invisible.

EVERY SCAN IS READ-ONLY, AND THE DIRECTORY SCAN IS READ-*NARROW*. Read-only is
structural here, not a promise in a docstring: this module opens no connection
and issues no write of any kind. The database collector's statement is a module
constant (:data:`INFORMATION_SCHEMA_SQL`) with no interpolation and no
parameters; the other three collectors take data a caller already fetched.

The directory scan carries a stronger rule, because a directory holds people.
The requirement is that it be **incapable** of reading a user object, not merely
that it does not — so the only door into it is :func:`screen_group_entries`,
which projects every entry down to a single display-name string and **raises**
:class:`DirectoryScopeError` the moment an entry carries a user attribute.
:func:`scan_directory` accepts strings and nothing else. There is no code path
from a user *object* to a scan result: the type at the boundary is ``str``, so a
user object cannot survive the crossing even if a caller hands one in by mistake.

WHERE THAT GUARANTEE STOPS, STATED PLAINLY. It covers **objects**. A directory
section may also carry **bare strings**, and a bare string is not inspectable:
there is no attribute to refuse and no type to check, so a person's name typed
into one — a distribution list named after somebody, in a hand-assembled bundle —
reaches the profile. Guessing which strings are people is NOT the answer: a
person's name and a department's name are the same shape, and a heuristic wrong
in either direction is worse than the gap.

So the rule is that a bare string must not pass **silently**. Every one is
counted, and the count is carried into ``provenance.directory.unscreened_entries``
and printed by the wizard — in front of the person who approves the profile. The
count is *required* by the schema, so a profile that fails to state it does not
validate: an unstated count is exactly the silence this closes.

VALIDATE, THEN WRITE — never the other way round. :func:`write_profile`
validates before it opens the file and raises :class:`ProfileInvalid` rather
than writing anything. The rule behind that ordering: a config the platform
refuses to parse does not fall back to default rules, it means *no* rules at
all. A profile that fails validation must therefore never reach disk, because a
half-written one on disk reads as configured.

THE MIRROR PATTERN IS A TEMPLATE (binding ruling, 2026-08-15). The per-person
mirror name is derived from the host identity at init —
:func:`resolve_mirror_template` — never typed as a literal by an administrator.
That ruling is mechanized rather than documented: the schema requires the
template to contain :data:`MIRROR_PLACEHOLDER`, so a literal name fails
validation and never reaches a profile.

NO ORGANISATION APPEARS IN THIS FILE. The area hints are generic English
business words, the schema's examples are placeholders, and every real name
arrives at runtime from the leader's answers or a scan.

Layer rule: brain may import core, never board. Stdlib only here.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import config as _config

DATA_DIR = Path(__file__).resolve().parent / "data"
SCHEMA_FILE = DATA_DIR / "org-profile-schema.json"
NAMING_CONTRACT_FILE = DATA_DIR / "naming-contract.json"

PROFILE_VERSION = 1

#: The six things a leader must supply because no scan can discover them. Order
#: is the order the interactive wizard asks in, and the order the docs list.
LEADER_ANSWERS = (
    "org_slug",             # 1. the org's short identity
    "org_brain_repo",       # 2. the repo name of the shared org brain
    "mirror_template",      # 3. the per-person mirror NAMING TEMPLATE
    "forge_url",            # 4. the forge and its org URL (with forge_kind)
    "vertical_pack",        # 5. which opt-in vocabulary pack applies
    "promotion_approvers",  # 6. who may approve a promotion PR
)

#: The placeholder that makes ``mirror_template`` a template. Its presence is
#: enforced by the schema, which is how the 2026-08-15 ruling stops being prose.
MIRROR_PLACEHOLDER = "{username}"
DEFAULT_MIRROR_TEMPLATE = MIRROR_PLACEHOLDER + "-brain"
DEFAULT_TARGET_BRANCH = "main"


class ProfileInvalid(Exception):
    """A profile (or an answer set) failed validation. Carries every finding, not
    just the first — a leader fixing one field at a time re-runs four scans for
    each round trip, so one report has to name everything that is wrong."""

    def __init__(self, what, errors):
        self.what = what
        self.errors = list(errors)
        super().__init__("%s is invalid:\n  - %s" % (what, "\n  - ".join(self.errors)))


class DirectoryScopeError(Exception):
    """A directory entry carried a user-object attribute. The directory scan is
    scoped to GROUP DISPLAY NAMES; anything else is refused at the door rather
    than filtered downstream, because a filter is a thing that can be edited out."""


# ---------------------------------------------------------------- data ----
_SCHEMA = None
_CONTRACT = None


def schema():
    """The shipped OrgProfile schema (answers + profile), loaded once."""
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(SCHEMA_FILE.read_text())
    return _SCHEMA


def naming_contract():
    """The shipped naming contract — areas, packs, stopwords, generic tokens.
    Read rather than duplicated so the wizard and the linter cannot disagree."""
    global _CONTRACT
    if _CONTRACT is None:
        _CONTRACT = json.loads(NAMING_CONTRACT_FILE.read_text())
    return _CONTRACT


def generic_areas(vertical_pack=None):
    """Every generic area name, plus the chosen vertical pack's additions.

    A fresh install works with no configuration because every generic area
    doubles as a domain (see docs/brain-naming.md), so this set is both the list
    of legal areas AND the set of domains that need no registry entry."""
    areas = naming_contract()["areas"]
    out = []
    for family in ("business", "technical", "toolchain"):
        out.extend(areas.get(family, []))
    if vertical_pack:
        out.extend(areas.get("packs", {}).get(vertical_pack, []))
    return out


def vertical_packs():
    """The pack names a leader may choose from (answer 5)."""
    return sorted(naming_contract()["areas"].get("packs", {}))


# ------------------------------------------------------------- text ----
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _words(text):
    """Lowercased word tokens, splitting camelCase and PascalCase as well as the
    usual separators. Directory groups and repo names use both conventions in the
    same org, and a scan that reads only one of them under-counts silently."""
    out = []
    for chunk in _WORD_RE.findall(str(text or "")):
        out.extend(p.lower() for p in _CAMEL_RE.findall(chunk) if p)
    return out


def _noise_words():
    """Words that carry no domain meaning: the contract's stopwords and its
    generic tokens, read from the shipped data rather than restated here."""
    c = naming_contract()
    return set(c.get("stopwords", [])) | set(c.get("genericTokens", []))


def _slug(text):
    """kebab-case slug, matching the naming contract's ``allowed`` character set."""
    return "-".join(_words(text))


def _ranked(counter, minimum=1):
    """Terms with at least ``minimum`` occurrences, most frequent first and
    alphabetical within a tie — so two runs over the same input agree byte for
    byte, which is what makes the emitted profile diffable."""
    items = [(t, n) for t, n in counter.items() if n >= minimum]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in items]


def _count(counter, term):
    counter[term] = counter.get(term, 0) + 1


# ============================================================ SCAN 1 ====
# DATABASE — INFORMATION_SCHEMA schema names + migration-file header comments.

#: The whole database side of the wizard, as one constant. No interpolation, no
#: parameters, no second statement: the scan cannot be made to write by changing
#: an argument, because there is no argument.
INFORMATION_SCHEMA_SQL = "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA"

#: Schemas every engine ships. They name the database's own plumbing, never a
#: business domain, and letting them through would put `dbo` in an org's registry.
#: Held as SLUGS (via :func:`_slug`) because that is the form a scanned name is
#: compared in: `INFORMATION_SCHEMA` normalises to `information-schema`, and a
#: raw-spelling list lets exactly that one — the most universal system schema
#: there is — through into an org's registry.
DATABASE_SYSTEM_SCHEMAS = frozenset(_slug(n) for n in {
    "dbo", "sys", "guest", "information_schema", "public", "pg_catalog",
    "pg_toast", "performance_schema", "mysql", "sysdiagrams", "temp",
    "db_owner", "db_accessadmin", "db_securityadmin", "db_ddladmin",
    "db_backupoperator", "db_datareader", "db_datawriter",
    "db_denydatareader", "db_denydatawriter",
})

#: How many leading lines of a migration file count as its header. A migration's
#: intent is stated at the top; further down is DDL, whose words are table names.
MIGRATION_HEADER_LINES = 12
_COMMENT_RE = re.compile(r"^\s*(?:--+|#+|/\*+|\*+|//+)\s?(.*?)\s*(?:\*/)?\s*$")


def collect_database(cursor, migration_dir=None):
    """Fetch the raw database inputs. READ-ONLY BY CONSTRUCTION: it executes
    :data:`INFORMATION_SCHEMA_SQL` and nothing else, and reads files it never
    opens for writing. Returns the bundle section :func:`scan_database` consumes,
    so a caller with credentials can produce a bundle that a caller without any
    can then scan."""
    cursor.execute(INFORMATION_SCHEMA_SQL)
    names = [row[0] for row in cursor.fetchall()]
    headers = []
    if migration_dir:
        for path in sorted(Path(migration_dir).glob("*")):
            if path.is_file():
                headers.append({"file": path.name, "text": _header_text(path)})
    return {"schema_names": names, "migration_headers": headers}


def _header_text(path):
    lines = []
    try:
        with path.open("r", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= MIGRATION_HEADER_LINES:
                    break
                lines.append(line.rstrip("\n"))
    except OSError:
        return ""
    return "\n".join(lines)


def _comment_lines(text):
    """Only the comment lines of a header block. A migration's first statement is
    often on line 2, and its identifiers are table names — reading them would put
    the physical schema into the vocabulary instead of the business words."""
    out = []
    for line in str(text or "").splitlines():
        m = _COMMENT_RE.match(line)
        if m and m.group(1):
            out.append(m.group(1))
    return out


def scan_database(schema_names=(), migration_headers=()):
    """SCAN 1 — business domains, from schema names and migration header comments.

    Two independent signals, deliberately weighted differently. A **schema name**
    is a deliberate act of partitioning by domain, so one occurrence is evidence.
    A **header word** is prose, so it counts only when it recurs across at least
    two migrations — otherwise every adjective a developer ever typed becomes a
    domain."""
    noise = _noise_words()
    counts = {}
    for name in schema_names:
        slug = _slug(name)
        if not slug or slug in DATABASE_SYSTEM_SCHEMAS or slug in noise:
            continue
        _count(counts, slug)

    header_files = {}
    for entry in migration_headers:
        text = entry.get("text", "") if isinstance(entry, dict) else entry
        seen = set()
        for line in _comment_lines(text):
            for word in _words(line):
                if len(word) < 3 or word in noise or word in DATABASE_SYSTEM_SCHEMAS:
                    continue
                seen.add(word)
        for word in seen:
            header_files[word] = header_files.get(word, 0) + 1

    for word, files in header_files.items():
        if files >= 2:
            counts[word] = counts.get(word, 0) + files

    return {
        "kind": "database",
        "read_only": True,
        "inputs": len(list(schema_names)) + len(list(migration_headers)),
        "domains": _ranked(counts),
        "counts": counts,
    }


# ============================================================ SCAN 2 ====
# DIRECTORY — GROUP DISPLAY NAMES ONLY. Never a user object.

#: Attributes that only ever appear on a *user* object. Their presence on an
#: entry means the caller reached for the wrong endpoint, and the scan refuses
#: rather than quietly dropping the field — a silent drop trains a caller to keep
#: handing over people's records, and the next reader may not drop them.
USER_OBJECT_ATTRS = frozenset({
    "userprincipalname", "mail", "givenname", "surname", "employeeid",
    "jobtitle", "manager", "department", "officelocation", "mobilephone",
    "businessphones", "othermails", "usertype", "accountenabled",
    "samaccountname", "onpremisessamaccountname", "employeetype",
    "preferredlanguage", "faxnumber", "streetaddress", "postalcode",
})

#: The keys an entry may use to state its own display name.
_GROUP_NAME_KEYS = ("displayname", "display_name", "name", "cn")

#: Role words an org appends to a group name to say what a member may do. Shipped
#: generic and English, so the tier axis is recognised before any org configures
#: anything; unrecognised tiers surface as departments and can be promoted later.
ROLE_TIER_WORDS = (
    "owner", "owners", "admin", "admins", "administrator", "administrators",
    "manager", "managers", "lead", "leads", "approver", "approvers",
    "contributor", "contributors", "editor", "editors", "author", "authors",
    "member", "members", "reader", "readers", "viewer", "viewers",
    "readonly", "operator", "operators", "analyst", "analysts", "guest", "guests",
)

#: A token appearing in at least this share of an org's group names is structural
#: — the org's own way of saying "this is a group" — not a department.
FUNCTION_WORD_SHARE = 0.25
#: …but a share is meaningless over three groups, so a floor applies as well.
FUNCTION_WORD_MIN_GROUPS = 4


def screen_group_entries(entries):
    """THE ONLY DOOR INTO THE DIRECTORY SCAN. Projects each entry to one display
    name, and reports how many entries it was actually able to screen.

    Returns ``{"names": [...], "screened": n, "unscreened": n}``.

    Two refusals, both loud, and both for entries shaped as **objects**:

      * an entry carrying any of :data:`USER_OBJECT_ATTRS` raises
        :class:`DirectoryScopeError` — that entry is a person's record and this
        scan has no business holding it;
      * an entry that declares ``"@odata.type": "#microsoft.graph.user"`` (or any
        type ending in ``user``) raises for the same reason. The type alone is
        enough: the screen must never depend on an attribute being *present* to
        notice it is holding a person's record.

    Everything else about a screened entry is discarded here, not downstream.
    That is what makes the scan *incapable* of reading a user object rather than
    merely well-behaved: past this function the data is a list of strings, so
    there is nothing for a later change to accidentally start reading.

    A **bare string** is a different case, and it is counted rather than judged.
    There is no attribute to refuse and no type to check, so the screen cannot
    tell a group's name from a person's — and it does not try, because the two
    are the same shape and a heuristic wrong in either direction is worse than
    the gap. The string is passed through and added to ``unscreened``, which
    travels all the way into the emitted profile so the person approving it can
    see how much of the input the screen never saw."""
    names, unscreened = [], 0
    for entry in entries or ():
        if isinstance(entry, str):
            if entry.strip():
                names.append(entry.strip())
                unscreened += 1     # accepted, but nothing here was inspectable
            continue
        if not isinstance(entry, dict):
            raise DirectoryScopeError(
                "directory entry must be a group display name or a group object, got %r"
                % type(entry).__name__)
        lowered = {str(k).lower(): v for k, v in entry.items()}
        odata = str(lowered.get("@odata.type", "")).lower()
        if odata.endswith("user"):
            raise DirectoryScopeError(
                "refusing a user object: the directory scan reads GROUP display names only")
        offending = sorted(set(lowered) & USER_OBJECT_ATTRS)
        if offending:
            raise DirectoryScopeError(
                "refusing a directory entry carrying user attributes %s: the directory "
                "scan reads GROUP display names only" % offending)
        name = None
        for key in _GROUP_NAME_KEYS:
            if lowered.get(key):
                name = str(lowered[key]).strip()
                break
        if not name:
            raise DirectoryScopeError(
                "group entry has no display name (looked for %s)" % (list(_GROUP_NAME_KEYS),))
        names.append(name)
    return {"names": names, "screened": len(names) - unscreened, "unscreened": unscreened}


def read_group_display_names(entries):
    """The display names alone, for a caller with no use for the screen's tally.
    :func:`screen_group_entries` is the door; this is the same door with the
    count dropped — so it must NOT be used to build a profile, which is required
    to state that count."""
    return screen_group_entries(entries)["names"]


def scan_directory(group_display_names=(), unscreened_entries=None):
    """SCAN 2 — function words, the department set, and role tiers, from GROUP
    DISPLAY NAMES ONLY.

    Takes strings and nothing else. Hand it anything richer and it is a
    :class:`TypeError` here, not a privacy incident later; use
    :func:`screen_group_entries` to get from a directory response to the strings
    this accepts.

    ``unscreened_entries`` is the screen's tally of entries it could not inspect
    (bare strings), carried through to the emitted profile. It defaults to
    ``None`` — meaning *no screen reported* — rather than to ``0``, because a
    zero default would let a caller that skipped the screen entirely claim
    everything was screened. ``None`` fails profile validation; a zero is a claim
    somebody actually made.

    A group name is typically ``<function> <department> <tier>`` in some order.
    The three axes are separated by frequency, not by parsing a format nobody
    agreed on: **tiers** are recognised from a shipped vocabulary, **function
    words** are whatever recurs across a quarter of all groups (that is the org
    saying "group" in its own dialect), and the **departments** are what is left."""
    names = []
    for n in group_display_names or ():
        if not isinstance(n, str):
            raise TypeError(
                "scan_directory takes group DISPLAY NAMES (str); got %r. "
                "Pass directory entries through read_group_display_names first."
                % type(n).__name__)
        if n.strip():
            names.append(n.strip())

    noise = _noise_words()
    tiers = set(ROLE_TIER_WORDS)
    tier_counts, word_files, dept_counts = {}, {}, {}

    for name in names:
        words = [w for w in _words(name) if w not in noise and len(w) > 1]
        for word in set(words):
            word_files[word] = word_files.get(word, 0) + 1
        for word in words:
            if word in tiers:
                _count(tier_counts, word)

    threshold = max(FUNCTION_WORD_MIN_GROUPS, int(len(names) * FUNCTION_WORD_SHARE) + 1)
    function_words = sorted(
        w for w, files in word_files.items()
        if files >= threshold and w not in tiers)

    for name in names:
        for word in _words(name):
            if word in noise or len(word) < 3:
                continue
            if word in tiers or word in function_words:
                continue
            _count(dept_counts, word)

    return {
        "kind": "directory",
        "read_only": True,
        "scope": "group-display-names-only",
        "inputs": len(names),
        "unscreened_entries": unscreened_entries,
        "departments": _ranked(dept_counts),
        "role_tiers": _ranked(tier_counts),
        "function_words": function_words,
        "counts": dept_counts,
    }


# ============================================================ SCAN 3 ====
# FORGE — repo and project names -> system domains.

#: Words that describe a repo's *shape*, not the system it serves. Every org has
#: an ``…-api`` and a ``…-web``; neither is a domain.
REPO_SHAPE_WORDS = frozenset({
    "api", "app", "apps", "web", "webapp", "www", "site", "service", "services",
    "svc", "lib", "libs", "library", "sdk", "cli", "ui", "frontend", "backend",
    "core", "common", "shared", "infra", "infrastructure", "iac", "terraform",
    "tools", "tooling", "scripts", "docs", "doc", "test", "tests", "e2e",
    "demo", "sample", "samples", "template", "templates", "poc", "spike",
    "config", "configs", "deploy", "deployment", "pipeline", "pipelines",
    "main", "master", "legacy", "old", "archive", "archived", "v1", "v2",
})


def scan_forge(repo_names=(), project_names=()):
    """SCAN 3 — system domains, from repo and project names.

    A **project** name is an explicit act of grouping, so it is a domain on
    sight. A **repo** word needs to recur: one repo called after a passing idea
    is not a system, and orgs have many of those."""
    noise = _noise_words()
    counts, repo_words = {}, {}

    for project in project_names or ():
        slug = _slug(project)
        if slug and slug not in noise:
            _count(counts, slug)

    for repo in repo_names or ():
        for word in set(_words(repo)):
            if len(word) < 3 or word in noise or word in REPO_SHAPE_WORDS:
                continue
            repo_words[word] = repo_words.get(word, 0) + 1

    for word, n in repo_words.items():
        if n >= 2:
            counts[word] = counts.get(word, 0) + n

    return {
        "kind": "forge",
        "read_only": True,
        "inputs": len(list(repo_names)) + len(list(project_names)),
        "domains": _ranked(counts),
        "counts": counts,
    }


# ============================================================ SCAN 4 ====
# EXISTING WIKI — leading segments of current page names -> naming habits.

_SEPARATORS = ("/", " - ", " – ", ":", " | ", "_", "-")


def scan_wiki(page_names=()):
    """SCAN 4 — the naming habits already in use, from the LEADING SEGMENT of
    current page names.

    The leading segment is where an org has been putting its top-level
    categorisation all along, which makes it the best evidence of the vocabulary
    people will actually reach for. The separator and the case convention come
    out of the same pass: adopting a shape the org already types is the
    difference between a convention and a rule nobody follows."""
    names = [str(p).strip() for p in (page_names or ()) if str(p).strip()]
    sep_counts = {s: 0 for s in _SEPARATORS}
    for name in names:
        for sep in _SEPARATORS:
            if sep in name:
                sep_counts[sep] += 1
                break

    separator = ""
    if names:
        best = max(sep_counts.items(), key=lambda kv: (kv[1], -_SEPARATORS.index(kv[0])))
        separator = best[0] if best[1] else ""

    segments = {}
    for name in names:
        head = name.split(separator)[0].strip() if separator else name.strip()
        slug = _slug(head)
        if slug:
            _count(segments, slug)

    return {
        "kind": "wiki",
        "read_only": True,
        "inputs": len(names),
        "leading_segments": _ranked(segments, minimum=2) or _ranked(segments),
        "separator": separator,
        "case": _case_style(names),
        "counts": segments,
    }


def _case_style(names):
    """The dominant case convention, named the way a human would say it."""
    if not names:
        return "unknown"
    kebab = sum(1 for n in names if re.fullmatch(r"[a-z0-9/_-]+", n))
    title = sum(1 for n in names if re.search(r"\b[A-Z][a-z]", n))
    upper = sum(1 for n in names if n.isupper())
    best = max((kebab, "kebab"), (title, "title"), (upper, "upper"), key=lambda kv: kv[0])
    return best[1] if best[0] else "mixed"


# ------------------------------------------------------- all four ----
def run_scans(bundle):
    """Run all four scans over one bundle of already-fetched, read-only inputs.

    A bundle is how the wizard runs **with no live credentials**: whoever has
    access produces it once, and the wizard — and every test — reads it. The
    directory section goes through :func:`screen_group_entries` here, so a bundle
    that carries user objects is refused at this level too, not only when a
    collector is used — and the screen's tally of entries it could not inspect
    travels with the scan result rather than being recomputed later."""
    bundle = bundle or {}
    db = bundle.get("database") or {}
    directory = bundle.get("directory") or {}
    forge = bundle.get("forge") or {}
    wiki = bundle.get("wiki") or {}
    groups = directory.get("groups", directory.get("group_display_names", []))
    screened = screen_group_entries(groups)
    return {
        "database": scan_database(db.get("schema_names", []),
                                  db.get("migration_headers", [])),
        "directory": scan_directory(screened["names"],
                                    unscreened_entries=screened["unscreened"]),
        "forge": scan_forge(forge.get("repos", []), forge.get("projects", [])),
        "wiki": scan_wiki(wiki.get("pages", [])),
    }


# ------------------------------------------------- vocabulary ----
#: Generic English words that justify an area. Shipped because they are the
#: language of business, not of any company: `invoice` means finance wherever it
#: appears. A word not listed here is NOT guessed at — see `unmapped_domains`.
AREA_HINTS = {
    "finance": ("invoice", "invoicing", "ledger", "gl", "payable", "payables",
                "receivable", "receivables", "billing", "payment", "payments",
                "accounting", "tax", "budget", "treasury", "payroll", "expense"),
    "sales": ("crm", "lead", "leads", "opportunity", "opportunities", "quote",
              "quotes", "deal", "deals", "pipeline", "commission"),
    "customer": ("customer", "customers", "client", "clients", "account",
                 "accounts", "contact", "contacts", "tenant", "tenants"),
    "pricing": ("price", "pricing", "rate", "rates", "tariff", "discount"),
    "product": ("product", "products", "catalog", "catalogue", "sku", "item",
                "items", "bom", "roadmap"),
    "service": ("ticket", "tickets", "incident", "incidents", "helpdesk",
                "support", "case", "cases", "sla", "dispatch", "scheduling"),
    "procurement": ("vendor", "vendors", "supplier", "suppliers", "purchase",
                    "purchasing", "po", "sourcing", "contract", "contracts"),
    "people": ("hr", "employee", "employees", "staff", "recruiting",
               "onboarding", "training", "workforce", "timesheet"),
    "risk": ("audit", "compliance", "policy", "policies", "control", "controls",
             "governance", "legal", "insurance"),
    "strategy": ("strategy", "okr", "okrs", "portfolio", "planning"),
    "it": ("helpdesk", "identity", "directory", "access", "licence", "license",
           "endpoint", "workstation"),
    "data": ("warehouse", "lakehouse", "lake", "etl", "elt", "reporting",
             "analytics", "bi", "dataset", "datasets", "dimension", "fact",
             "staging", "mart"),
    "ai": ("ml", "model", "models", "llm", "embedding", "embeddings", "agent",
           "agents", "prompt", "inference"),
    "cloud": ("cluster", "kubernetes", "k8s", "network", "networking", "vpc",
              "storage", "hosting", "tenancy", "region"),
    "repo": ("build", "ci", "cd", "release", "releases", "packaging", "monorepo"),
}


def _hint_index():
    out = {}
    for area, words in AREA_HINTS.items():
        for word in words:
            out.setdefault(word, area)
    return out


def merge_vocabulary(scans, vertical_pack=None):
    """Fold the four scans into the vocabulary the profile carries.

    Domains are mapped to a generic area two ways and no third: a word that IS a
    generic area maps to itself, and a word a shipped hint recognises maps to the
    hinted area. **Anything else is listed as unmapped, never guessed.** A wrong
    area is not a visible error — it is a filter that quietly stops matching —
    and the org half of the registry is PR-gated precisely so a person assigns
    it."""
    areas = set(generic_areas(vertical_pack))
    hints = _hint_index()
    domains, unmapped = {}, []

    ordered = []
    ordered.extend(scans.get("database", {}).get("domains", []))
    ordered.extend(scans.get("forge", {}).get("domains", []))
    ordered.extend(scans.get("wiki", {}).get("leading_segments", []))
    ordered.extend(scans.get("directory", {}).get("departments", []))

    for term in ordered:
        if term in domains or term in unmapped:
            continue
        if term in areas:
            domains[term] = term
        elif term in hints and hints[term] in areas:
            domains[term] = hints[term]
        else:
            # try the term's own words: `field-service` maps through `service`
            hit = next((hints[w] for w in _words(term)
                        if w in hints and hints[w] in areas), None)
            hit = hit or next((w for w in _words(term) if w in areas), None)
            if hit:
                domains[term] = hit
            else:
                unmapped.append(term)

    wiki = scans.get("wiki", {})
    directory = scans.get("directory", {})
    return {
        "domains": dict(sorted(domains.items())),
        "unmapped_domains": sorted(unmapped),
        "departments": list(directory.get("departments", [])),
        "role_tiers": list(directory.get("role_tiers", [])),
        "function_words": list(directory.get("function_words", [])),
        "naming_habits": {
            "leading_segments": list(wiki.get("leading_segments", [])),
            "separator": wiki.get("separator", ""),
            "case": wiki.get("case", "unknown"),
        },
    }


# ------------------------------------------------ the mirror template ----
#: The host identity the per-person mirror name is derived from (ruling
#: 2026-08-15: resolved from the host, never typed into a profile). It moved to
#: :mod:`brain.config` when the personal-brain DEFAULT PATH needed the same
#: identity — one resolver, re-exported here so this module's callers and its
#: docs keep the name they always used.
host_username = _config.host_username


def resolve_mirror_template(template=None, username=None):
    """Resolve a mirror TEMPLATE against the host identity — the 2026-08-15
    ruling, executed.

    The template must contain :data:`MIRROR_PLACEHOLDER`; a literal name raises,
    because a literal is exactly what the ruling forbids an administrator typing.
    ``username`` is for tests and for a caller that already knows the identity;
    the default reads :func:`host_username`."""
    tmpl = template or DEFAULT_MIRROR_TEMPLATE
    if MIRROR_PLACEHOLDER not in tmpl:
        raise ProfileInvalid("mirror_template", [
            "%r is a literal, not a template: it must contain %s, which is "
            "resolved from the host identity at init (ruling 2026-08-15)"
            % (tmpl, MIRROR_PLACEHOLDER)])
    name = _slug(username if username is not None else host_username())
    if not name:
        raise ProfileInvalid("mirror_template", [
            "cannot resolve %s: the host identity is empty" % MIRROR_PLACEHOLDER])
    return tmpl.replace(MIRROR_PLACEHOLDER, name)


# ------------------------------------------------------- validation ----
_TYPES = {
    "object": dict, "array": list, "string": str, "integer": int,
    "boolean": bool, "null": type(None),
}


def _type_ok(value, expected):
    for name in (expected if isinstance(expected, list) else [expected]):
        py = _TYPES.get(name)
        if py is None:
            continue
        if name == "integer" and isinstance(value, bool):
            continue          # bool is an int in Python; it is not an integer here
        if isinstance(value, py):
            return True
    return False


def _check(value, spec, path, errors):
    """The hand-rolled validator. Supports exactly the keyword subset the schema
    file declares under ``validator.keywords``; an unknown keyword is an error
    against the SCHEMA, so a mis-authored rule fails loudly instead of silently
    passing everything."""
    unknown = set(spec) - set(schema()["validator"]["keywords"]) - {"$comment"}
    if unknown:
        errors.append("%s: schema uses unsupported keyword(s) %s" % (path, sorted(unknown)))
        return

    if "type" in spec and not _type_ok(value, spec["type"]):
        errors.append("%s: expected %s, got %s" % (path, spec["type"], type(value).__name__))
        return
    if "const" in spec and value != spec["const"]:
        errors.append("%s: must be %r" % (path, spec["const"]))
    if "enum" in spec and value not in spec["enum"]:
        errors.append("%s: %r is not one of %s" % (path, value, spec["enum"]))
    if isinstance(value, str):
        if "minLength" in spec and len(value) < spec["minLength"]:
            errors.append("%s: must be at least %d character(s)" % (path, spec["minLength"]))
        if "pattern" in spec and not re.match(spec["pattern"], value):
            errors.append("%s: %r does not match %s" % (path, value, spec["pattern"]))
        if "mustContain" in spec and spec["mustContain"] not in value:
            errors.append("%s: %r must contain %s" % (path, value, spec["mustContain"]))
    if isinstance(value, list):
        if "minItems" in spec and len(value) < spec["minItems"]:
            errors.append("%s: needs at least %d item(s), has %d"
                          % (path, spec["minItems"], len(value)))
        if "items" in spec:
            for i, item in enumerate(value):
                _check(item, spec["items"], "%s[%d]" % (path, i), errors)
    if isinstance(value, dict):
        for key in spec.get("required", []):
            if key not in value:
                errors.append("%s: missing required key %r" % (path, key))
        props = spec.get("properties", {})
        for key, sub in props.items():
            if key in value:
                _check(value[key], sub, "%s.%s" % (path, key), errors)
        if "valueSchema" in spec:
            for key, item in sorted(value.items()):
                if key in props or str(key).startswith("$"):
                    continue
                _check(item, spec["valueSchema"], "%s.%s" % (path, key), errors)


def validate_answers(answers):
    """Validate the six leader answers. Raises :class:`ProfileInvalid` listing
    every problem, so one round trip fixes them all."""
    errors = []
    if not isinstance(answers, dict):
        raise ProfileInvalid("answers", ["expected a JSON object, got %s"
                                         % type(answers).__name__])
    _check(answers, schema()["answers"], "answers", errors)
    errors.extend(_semantic_answer_errors(answers))
    if errors:
        raise ProfileInvalid("answers", errors)
    return answers


def _semantic_answer_errors(answers):
    """The two rules the declarative schema cannot state: the pack must be one
    the shipped contract actually defines, and ADO needs a project segment where
    GitHub has none."""
    errors = []
    pack = answers.get("vertical_pack")
    if pack is not None and pack not in vertical_packs():
        errors.append("answers.vertical_pack: %r is not a shipped pack (%s), or null"
                      % (pack, ", ".join(vertical_packs()) or "none"))
    if answers.get("forge_kind") == "ado" and not answers.get("forge_project"):
        errors.append("answers.forge_project: required when forge_kind is 'ado' "
                      "(an ADO repo URL has an org AND a project segment)")
    return errors


def validate_profile(profile):
    """Validate an emitted OrgProfile. Raises :class:`ProfileInvalid` with every
    finding. Called by :func:`write_profile` BEFORE the file is opened — an
    invalid profile must never reach disk, because a config the platform refuses
    to parse means no rules at all, not default rules."""
    errors = []
    if not isinstance(profile, dict):
        raise ProfileInvalid("profile", ["expected a JSON object, got %s"
                                         % type(profile).__name__])
    _check(profile, schema()["profile"], "profile", errors)
    pack = profile.get("vertical_pack")
    if pack is not None and pack not in vertical_packs():
        errors.append("profile.vertical_pack: %r is not a shipped pack (%s), or null"
                      % (pack, ", ".join(vertical_packs()) or "none"))
    forge = profile.get("forge")
    if isinstance(forge, dict) and forge.get("kind") == "ado" and not forge.get("project"):
        errors.append("profile.forge.project: required when forge.kind is 'ado'")
    areas = set(generic_areas(pack if isinstance(pack, str) else None))
    vocab = profile.get("vocabulary")
    if isinstance(vocab, dict) and isinstance(vocab.get("domains"), dict):
        for domain, area in sorted(vocab["domains"].items()):
            if area not in areas:
                errors.append("profile.vocabulary.domains.%s: %r is not a known area "
                              "— every domain must map to one, or a filter silently "
                              "stops matching" % (domain, area))
    if errors:
        raise ProfileInvalid("profile", errors)
    return profile


# ---------------------------------------------------------- build ----
def build_profile(answers, scans):
    """Assemble the OrgProfile from the six answers and the four scans.

    Answer names and profile keys differ on purpose. ``forge_url`` and
    ``org_brain_repo`` are what a human is asked; ``forge.org`` and ``forge.repo``
    are what ``brain.init_home._apply_profile`` reads. The mapping lives here, in
    one place, so the artifact stays directly consumable by ``brain-init
    --profile`` and the answers stay in human words. The values are NOT repeated
    under both names — one artifact, one place each value is written."""
    validate_answers(answers)
    pack = answers.get("vertical_pack")
    vocab = merge_vocabulary(scans, pack)

    forge = {
        "kind": answers["forge_kind"],
        "org": answers["forge_url"],
        "repo": answers["org_brain_repo"],
        "target_branch": answers.get("forge_target_branch") or DEFAULT_TARGET_BRANCH,
    }
    if answers.get("forge_project"):
        forge["project"] = answers["forge_project"]

    profile = {
        "profile_version": PROFILE_VERSION,
        "org_slug": answers["org_slug"],
        "org_label": answers.get("org_label") or _default_org_label(answers["org_slug"]),
        "mirror_template": answers["mirror_template"],
        "vertical_pack": pack,
        "promotion_approvers": list(answers["promotion_approvers"]),
        "forge": forge,
        # Context-injection keywords: the org's own domain words, which is
        # exactly what the scans just found. An empty list disables injection,
        # so an org with nothing to inject gets silence rather than noise.
        "keywords": sorted(vocab["domains"]),
        "vocabulary": vocab,
        "provenance": {
            name: _provenance(name, s) for name, s in sorted(scans.items())
        },
    }
    if answers.get("labels"):
        profile["labels"] = dict(answers["labels"])
    return validate_profile(profile)


def _provenance(name, scan):
    """What a scan says about itself, in the artifact. The directory scan says one
    thing more: how many of its entries the object screen could not inspect. That
    number is not decoration — it is the only thing standing between a bare string
    carrying a person's name and a profile that looks fully screened. It is copied
    through verbatim, `None` included, so a scan built without a screen fails
    validation rather than silently reporting zero."""
    out = {"kind": scan.get("kind", name),
           "read_only": bool(scan.get("read_only")),
           "inputs": int(scan.get("inputs", 0))}
    if name == "directory":
        out["unscreened_entries"] = scan.get("unscreened_entries")
    return out


def _default_org_label(org_slug):
    """A display name derived from the slug — `north-wind` -> `North Wind Brain`.
    Derived, not asked: one more question for a value the slug already implies is
    how a wizard becomes a form."""
    return " ".join(w.capitalize() for w in org_slug.split("-") if w) + " Brain"


def render(profile):
    """The profile's on-disk text. Sorted keys and a trailing newline: the
    artifact is committed and diffed, and key order churn would bury the change
    that matters."""
    return json.dumps(profile, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_profile(path, profile):
    """VALIDATE, THEN WRITE. Raises :class:`ProfileInvalid` before touching the
    filesystem; on success writes atomically (temp file + replace) so a reader
    never sees a half-written profile. Returns the path written."""
    validate_profile(profile)
    path = Path(os.path.expanduser(str(path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render(profile))
    os.replace(str(tmp), str(path))
    return path


# ------------------------------------------------------------ CLI ----
def load_bundle(path):
    """Read a scan bundle — the four sections of already-fetched, read-only
    input. This is how the wizard runs with NO LIVE CREDENTIALS."""
    p = Path(os.path.expanduser(str(path)))
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        raise ProfileInvalid("scan bundle", ["not found: %s" % p])
    except (json.JSONDecodeError, OSError) as e:
        raise ProfileInvalid("scan bundle", ["cannot read %s: %s" % (p, e)])
    if not isinstance(data, dict):
        raise ProfileInvalid("scan bundle", ["%s is not a JSON object" % p])
    return data


def load_answers(path):
    p = Path(os.path.expanduser(str(path)))
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        raise ProfileInvalid("answers", ["not found: %s" % p])
    except (json.JSONDecodeError, OSError) as e:
        raise ProfileInvalid("answers", ["cannot read %s: %s" % (p, e)])
    return data


_PROMPTS = (
    ("org_slug", "1/6  org slug (kebab-case, e.g. north-wind)", None),
    ("org_brain_repo", "2/6  org brain repo name", "org-brain"),
    ("mirror_template", "3/6  per-person mirror TEMPLATE (must contain %s)"
                        % MIRROR_PLACEHOLDER, DEFAULT_MIRROR_TEMPLATE),
    ("forge_kind", "4a/6 forge (ado | github)", "github"),
    ("forge_url", "4b/6 forge org URL", None),
    ("forge_project", "4c/6 ADO project (blank for github)", ""),
    ("vertical_pack", "5/6  vertical pack (%s, or blank for none)", ""),
    ("promotion_approvers", "6/6  promotion approvers (comma-separated)", None),
)


def prompt_answers(reader=None, writer=None):
    """Ask the six questions. Split from :func:`build_profile` so the wizard is
    testable without a terminal and runnable without one."""
    reader = reader or (lambda p: input(p))
    writer = writer or (lambda s: sys.stdout.write(s))
    writer("The six answers no scan can discover:\n")
    answers = {}
    for key, label, default in _PROMPTS:
        if "%s" in label:
            label = label % (", ".join(vertical_packs()) or "none available")
        suffix = " [%s]: " % default if default else ": "
        value = (reader(label + suffix) or "").strip() or (default or "")
        if key == "promotion_approvers":
            answers[key] = [v.strip() for v in value.split(",") if v.strip()]
        elif key == "vertical_pack":
            answers[key] = value or None
        elif key == "forge_project" and not value:
            continue
        else:
            answers[key] = value
    return answers


def run(answers, bundle, out=None, dry_run=False):
    """The wizard, end to end: scan, build, validate, write. Returns
    ``(profile, written_path_or_None)``."""
    scans = run_scans(bundle)
    profile = build_profile(answers, scans)
    if dry_run or not out:
        return profile, None
    return profile, write_profile(out, profile)


def _summary(profile, scans):
    lines = ["org-setup: profile for %s" % profile["org_slug"]]
    for name in ("database", "directory", "forge", "wiki"):
        s = scans[name]
        detail = {
            "database": lambda: "%d business domain(s)" % len(s["domains"]),
            "forge": lambda: "%d system domain(s)" % len(s["domains"]),
            "wiki": lambda: "%d leading segment(s), separator %r"
                            % (len(s["leading_segments"]), s["separator"]),
            "directory": lambda: "%d department(s), %d role tier(s) "
                                 "(group display names only; %s of %d entries "
                                 "bypassed the object screen)"
                                 % (len(s["departments"]), len(s["role_tiers"]),
                                    s.get("unscreened_entries"), s["inputs"]),
        }[name]()
        lines.append("  scan %-9s %2d input(s) -> %s" % (name, s["inputs"], detail))
    unscreened = scans["directory"].get("unscreened_entries") or 0
    if unscreened:
        lines.append("  NOTE         %s. The screen refuses user"
                     % ("1 directory entry was a bare string" if unscreened == 1
                        else "%d directory entries were bare strings" % unscreened))
        lines.append("               OBJECTS; it cannot inspect a plain string, so a person's "
                     "name in one")
        lines.append("               reaches the profile. Supply group objects to have them "
                     "screened.")
    vocab = profile["vocabulary"]
    lines.append("  vocabulary   %d domain(s) mapped to an area, %d unmapped"
                 % (len(vocab["domains"]), len(vocab["unmapped_domains"])))
    if vocab["unmapped_domains"]:
        lines.append("               unmapped (assign an area in the org brain PR): %s"
                     % ", ".join(vocab["unmapped_domains"]))
    lines.append("  mirror       %s -> %s"
                 % (profile["mirror_template"],
                    resolve_mirror_template(profile["mirror_template"])))
    return "\n".join(lines)


def build_parser():
    """The wizard's parser, built separately from :func:`main` so the board's
    routing seam can be checked against it. `task-station org-setup` restates
    these flags (argparse cannot capture a leading ``--flag`` into a REMAINDER
    positional), and a restated flag set drifts unless something compares them."""
    ap = argparse.ArgumentParser(
        prog="task-station org-setup",
        description="Four read-only scans + six answers -> a schema-valid OrgProfile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Feed the result to `python3 -m brain.init_home --profile <out>`.")
    ap.add_argument("--scan-bundle", required=True, metavar="JSON",
                    help="already-fetched read-only inputs for the four scans "
                         "(database/directory/forge/wiki) — no live credentials needed")
    ap.add_argument("--answers", metavar="JSON",
                    help="the six leader answers as a JSON file; omit to be asked")
    ap.add_argument("--out", metavar="PATH", default=None,
                    help="where to write config.json (omit with --dry-run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and print the profile; write nothing")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)

    try:
        bundle = load_bundle(a.scan_bundle)
        answers = load_answers(a.answers) if a.answers else prompt_answers()
        scans = run_scans(bundle)
        profile = build_profile(answers, scans)
    except (ProfileInvalid, DirectoryScopeError) as e:
        sys.stderr.write("org-setup: %s\n" % e)
        return 2

    print(_summary(profile, scans))
    if a.dry_run or not a.out:
        print(render(profile), end="")
        if not a.out and not a.dry_run:
            sys.stderr.write("org-setup: no --out given; nothing written.\n")
        return 0
    written = write_profile(a.out, profile)
    print("  wrote        %s" % written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
