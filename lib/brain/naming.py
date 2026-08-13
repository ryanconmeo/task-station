"""brain-station — the knowledge-node naming contract.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 2) from the brain source tree's
``scripts/naming.py`` @ 0.14.0, with its shipped data file moving alongside it to
``lib/brain/data/naming-contract.json``.

WHAT CHANGED AND WHY. This module used to derive a slug from a free-text title
and warn, advisorily, about three shallow things. That is deterministic in the
useless sense: ``normalize()`` maps the same title to the same slug every time,
but the title was whatever a language model invented. The property actually
needed is CONVERGENCE —

    the same fact, captured twice, by different sessions, from different
    phrasings, lands on the SAME node.

Measured over a 126-node corpus before this rewrite: 19% of names stated a
verdict rather than a subject (``never-apply-flyway-to-dev-directly``), one
subject cluster carried three different prefixes, and one system had two names.
None of that was detectable by the old checks, and the "prefer at least two
segments" warning was dead code — 1 node of 126 tripped it, while 37 had
subjects longer than the shape intends.

THE SHAPE IS NOW COMPOSED, NOT DERIVED::

    <domain>[-<subdomain>]-<subject>

``domain`` is CLOSED and is the only hard gate. ``subdomain`` is free.
``subject`` is at most three words and names the THING, never the verdict.

WHERE THE VOCABULARY LIVES, and why it is split in two:

  * The GENERIC half ships — ``lib/brain/data/naming-contract.json``: the areas,
    the shape, the detector vocabularies. Company-agnostic by construction, so
    this repo carries no organisation's fingerprints.
  * The ORG half does NOT ship — it is read from the org brain's
    ``schemas/node-types.json`` (``domains.registry``), which is PR-gated. One
    owner for the registry, and a governance gate that already exists.

A fresh install with no org clone still works: every generic area doubles as a
domain, so the contract is useful before anyone configures anything.

Layer rule: brain may import core and its own siblings, never board. Stdlib only
here (plus a function-local ``notes`` for the corpus reader).

Pure stdlib.
"""
import difflib
import json
import re
from collections import Counter
from pathlib import Path

# The shipped generic half of the contract, next to this module (the source
# anchored one level up, from scripts/ to its repo root; here the data dir is a
# child of the package).
_DATA = Path(__file__).resolve().parent / "data" / "naming-contract.json"

# A run of non-alphanumerics collapses to a single hyphen when normalizing.
_NONALNUM_RX = re.compile(r"[^a-z0-9]+")
# Year-month is enough to be a date. The original regex demanded YYYY-MM-DD and
# so let `verification-incidents-2026-07` through a check that reported zero.
_DATE_RX = re.compile(r"\d{4}-\d{2}(-\d{2})?|\b\d{8}\b")


# --------------------------------------------------------------------------- #
# contract loading
# --------------------------------------------------------------------------- #
def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_contract(org_brain_clone=None, packs=("industrial",)):
    """The merged contract: shipped generic half + the org registry when reachable.

    ``org_brain_clone`` is a path to the org brain repo (callers pass
    ``brain.config``'s resolved clone). Absent or unreadable ⇒ generic only,
    which is a working contract rather than a failure: every area doubles as a
    domain.
    """
    c = _read_json(_DATA, {})
    a = c.get("areas", {})
    areas = list(a.get("business", [])) + list(a.get("technical", [])) + list(a.get("toolchain", []))
    for p in packs or ():
        areas += a.get("packs", {}).get(p, [])
    c["_areas"] = areas

    # generic areas are always valid domains, so a fresh install is usable
    registry = {x: x for x in areas}
    if org_brain_clone:
        nt = _read_json(Path(org_brain_clone) / "schemas" / "node-types.json", {})
        org = (nt.get("domains") or {}).get("registry") or {}
        for dom, area in org.items():
            if isinstance(dom, str) and isinstance(area, str):
                registry[dom] = area
    c["_registry"] = registry
    return c


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def normalize(text):
    """Collapse arbitrary text to a canonical kebab slug. Deterministic."""
    return _NONALNUM_RX.sub("-", (text or "").strip().lower()).strip("-")


def _tokens(text, contract=None):
    stop = set((contract or {}).get("stopwords", []))
    return [t for t in normalize(text).split("-") if t and t not in stop]


def resolve_domain(slug, contract):
    """``(domain, token_count)`` for ``slug``, or ``(None, 0)``.

    Longest prefix wins, so a two-word domain matches before its first word
    alone — otherwise ``task-station-…`` would resolve to a ``task`` domain that
    does not exist.
    """
    registry = contract.get("_registry", {})
    parts = slug.split("-")
    for n in (2, 1):
        cand = "-".join(parts[:n])
        if cand in registry:
            return cand, n
    return None, 0


def area_for(slug, contract):
    """The generic area a slug rolls up to, or ``None``."""
    dom, _ = resolve_domain(slug, contract)
    return contract.get("_registry", {}).get(dom) if dom else None


def compose(domain, subject, subdomain=None):
    """Build a slug from its parts — the inverse of reading one."""
    bits = [normalize(domain)]
    if subdomain:
        bits.append(normalize(subdomain))
    bits.append(normalize(subject))
    return "-".join(b for b in bits if b)


# --------------------------------------------------------------------------- #
# claim shape — the detector that made the 19% visible
# --------------------------------------------------------------------------- #
def claim_shape(slug, contract):
    """Why ``slug`` reads as a verdict rather than a subject, or ``None``.

    A claim-shaped name is unstable *by construction*: correct the claim and the
    name lies.
    """
    cs = contract.get("claimShape", {})
    parts = [p for p in (slug or "").split("-") if p]
    if parts and parts[0] in set(cs.get("leadingImperative", [])):
        return ("leading imperative %r — name the subject; the imperative belongs "
                "in type: rule" % parts[0])
    for p in parts[1:]:
        if p in set(cs.get("embeddedCopula", [])):
            return "embedded copula %r — the name states a claim" % p
    for p in parts:
        if p in set(cs.get("negation", [])):
            return "negation %r — the name states a claim" % p
    return None


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #
def _finding(check, severity, detail, fix=None):
    return {"check": check, "severity": severity, "detail": detail, "fix": fix}


def _nearest_domain(word, contract):
    """The closest registered domain to ``word`` — a refusal must be helpful."""
    if not word:
        return None
    best, score = None, 0.0
    for d in contract.get("_registry", {}):
        r = difflib.SequenceMatcher(None, word, d).ratio()
        if r > score:
            best, score = d, r
    return best if score >= 0.55 else None


def slug_findings(slug, folder="notes", contract=None):
    """Every finding for ``slug``, each carrying its own severity.

    Only the domain check is an ``error`` in practice, because a refusal makes an
    author drop the fact or fake a name to get past the gate — this project has
    the evidence: a hard cap on pinned decisions produced a crowding-out
    workaround rather than better pinning. Everything else warns and the write
    proceeds.
    """
    contract = contract or load_contract()
    sev = contract.get("severity", {})
    out = []
    parts = [p for p in (slug or "").split("-") if p]

    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        out.append(_finding("illegal-characters", sev.get("illegal-characters", "error"),
                            "only a-z, 0-9 and hyphen are allowed"))
    if slug in set(contract.get("reservedStems", [])):
        out.append(_finding("reserved-stem", sev.get("reserved-stem", "error"),
                            "%r is a reserved stem" % slug))
    n = len(slug or "")
    if n and not (contract.get("minLength", 3) <= n <= contract.get("maxLength", 80)):
        out.append(_finding("length", "warn", "slug is %d characters" % n))

    # Only KNOWLEDGE NODES carry a domain. A report is `2026-07-14-lint` and a
    # plan is `2026-08-02-naming-spec` — dated artifacts, not standing claims —
    # so requiring a domain there would flag correct names.
    needs_domain = folder in set(contract.get("domainRequiredIn", ["notes", "memory"]))
    dom, dn = resolve_domain(slug, contract)
    if dom is None and needs_domain:
        near = _nearest_domain(parts[0] if parts else "", contract)
        out.append(_finding(
            "unregistered-domain", sev.get("unregistered-domain", "error"),
            "%r is not a registered domain" % (parts[0] if parts else ""),
            fix=("closest registered domain: %r" % near) if near else
                "register it in the org brain's schemas/node-types.json (domains.registry)"))
    elif dom is not None:
        rest = parts[dn:]
        # a subdomain is allowed, so the subject is what follows an optional one
        subj_len = max(0, len(rest) - 1) if len(rest) > 1 else len(rest)
        cap = contract.get("subjectMaxWords", 3)
        if subj_len > cap:
            out.append(_finding("subject-too-long", sev.get("subject-too-long", "warn"),
                                "subject is %d words (cap %d)" % (subj_len, cap),
                                fix="name the thing, not the mechanism"))

    cs = claim_shape(slug, contract)
    if cs:
        out.append(_finding("claim-shaped", sev.get("claim-shaped", "warn"), cs))

    generic = sorted(set(parts) & set(contract.get("genericTokens", [])))
    if generic:
        out.append(_finding("generic-token", sev.get("generic-token", "warn"),
                            "meaningless token(s) %s" % generic, fix="name the subject"))

    allowed = set(contract.get("datesAllowedIn", []))
    if _DATE_RX.search(slug or "") and folder not in allowed:
        out.append(_finding("date-in-slug", sev.get("date-in-slug", "warn"),
                            "a date belongs in verified:, or in %s/"
                            % "/, ".join(sorted(allowed))))
    return out


def has_error(findings):
    """Whether any finding blocks the write."""
    return any(f["severity"] == "error" for f in findings)


def slug_warnings(slug, folder="notes"):
    """Back-compat shim: the old advisory list of strings.

    Kept because the lint pass and the promote pipeline used to call it. New
    callers want :func:`slug_findings`, which carries severity.
    """
    return ["%s: %s" % (f["check"], f["detail"]) for f in slug_findings(slug, folder)]


# --------------------------------------------------------------------------- #
# find-target — the lookup is mandatory, the ACTION is graded
# --------------------------------------------------------------------------- #
def _iter_nodes(search_dirs):
    """``(path, stem, name, description)`` for every node under ``search_dirs``."""
    from . import notes  # local import: keep this module importable without the write path
    for d in search_dirs:
        p = Path(d)
        if not p.exists():
            continue
        for f in sorted(p.rglob("*.md")):
            if f.stem.startswith("_"):
                continue
            try:
                fm, _ = notes.parse_note(f.read_text(errors="ignore"))
            except OSError:
                fm = {}
            yield f, f.stem, str(fm.get("name") or f.stem), str(fm.get("description") or "")


def _similarity(norm_in, in_tokens, in_text, stem, name, description):
    """``(overall, description_only)`` — the caller grades on the second.

    Slug text may SUGGEST a candidate but must never drive an update: two real
    nodes scored 0.595 on slug text and were entirely different facts (one said a
    reload leg is ADF rather than a pipeline, the other that the same reload
    needs four pre-existing framework tables). A false merge is worse than a
    fork, because a fork is visible and a bad merge is not.
    """
    name_sim = max(difflib.SequenceMatcher(None, norm_in, stem).ratio(),
                   difflib.SequenceMatcher(None, norm_in, normalize(name)).ratio())
    desc_sim = (difflib.SequenceMatcher(None, in_text.lower(), description.lower()).ratio()
                if description else 0.0)
    node_tokens = set(_tokens(stem)) | set(_tokens(name))
    jac = (len(in_tokens & node_tokens) / len(in_tokens | node_tokens)
           if (in_tokens or node_tokens) else 0.0)
    return max(name_sim, desc_sim, jac), desc_sim


def find_target(text, search_dirs, contract=None, threshold=None):
    """The best merge-target for proposed content, WITH the action to take.

    Returns ``None`` when there is no target — **the contract every caller
    already relies on** (the promote pipeline does ``if target:``, the search CLI
    does ``if not hit:``). An always-truthy return would silently make promote
    reconcile into the nearest node every time.

    Otherwise ``{"action", "slug", "path", "score", "descScore", "reason"}``:

      ``update`` — description similarity at or above 0.90, i.e. the same fact.
      ``choose`` — a candidate exists but the call needs judgement, and the
                   choice must be RECORDED (``converged-with:`` /
                   ``distinct-from:``) so the next session does not re-litigate.

    An exact or name-normalized slug hit is ``update`` regardless of description:
    the slug IS the identity, so hitting it exactly is the match.
    """
    contract = contract or load_contract()
    ft = contract.get("findTarget", {})
    up = ft.get("updateAtOrAbove", 0.9)
    ch = threshold if threshold is not None else ft.get("chooseAtOrAbove", 0.6)

    raw = (text or "").strip().lower()
    norm_in = normalize(text)
    in_tokens = set(_tokens(text, contract))
    in_text = (text or "").strip()

    best = None
    for path, stem, name, description in _iter_nodes(search_dirs):
        if stem.lower() == raw:
            return {"action": "update", "slug": stem, "path": str(path),
                    "score": 1.0, "descScore": 1.0, "reason": "exact-name"}
        if normalize(stem) == norm_in or normalize(name) == norm_in:
            return {"action": "update", "slug": stem, "path": str(path),
                    "score": 0.97, "descScore": 0.97, "reason": "normalized"}
        overall, desc = _similarity(norm_in, in_tokens, in_text, stem, name, description)
        if best is None or overall > best["score"]:
            best = {"slug": stem, "path": str(path), "score": round(overall, 3),
                    "descScore": round(desc, 3), "reason": "similarity"}

    if not best:
        return None
    if best["descScore"] >= up:
        best["action"] = "update"
    elif best["score"] >= ch:
        best["action"] = "choose"
    else:
        return None          # below threshold: genuinely new
    return best


# --------------------------------------------------------------------------- #
# meaning guard — why a rename is proposed and never auto-applied
# --------------------------------------------------------------------------- #
def word_frequency(search_dirs):
    """How many node slugs use each word — the input to :func:`dropped_rare_words`."""
    freq = Counter()
    for d in search_dirs:
        p = Path(d)
        if not p.exists():
            continue
        for f in p.rglob("*.md"):
            if f.stem.startswith("_"):
                continue
            for w in set(f.stem.split("-")):
                freq[w] += 1
    return freq


def dropped_rare_words(old_slug, new_slug, freq, contract=None):
    """Words ``new_slug`` drops that are rare enough to be identifying.

    Non-empty ⇒ do NOT auto-apply. Rarity is a GUARD here, never a name
    generator: tested as a generator it discarded a proper noun on an
    alphabetical tie-break and demoted cluster terms, which are frequent
    precisely because they name something real.
    """
    contract = contract or load_contract()
    limit = contract.get("meaningGuard", {}).get("rareWordThreshold", 2)
    lost = set((old_slug or "").split("-")) - set((new_slug or "").split("-"))
    return sorted(w for w in lost if w and freq.get(w, 0) <= limit)
