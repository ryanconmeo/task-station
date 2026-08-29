"""brain.init_home — home setup + reversible memory migrate-then-link.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``scripts/init_home.py`` @ 0.14.0. The safety contract, the plan/apply split, the
line-union MEMORY.md merge, the conflict refusal and the CLAUDE.md @import block
are unchanged. What the port changed is named in the chunk-5a handoff; the two
that matter here:

  * every path this module WRITES is now read from :mod:`brain.config` rather
    than re-spelled (the primary config file above all), so init can never write
    a config the loader does not read;
  * the built-in org values are gone. The source shipped one org's keyword list
    and one org's ADO url as defaults; both now arrive at runtime from an
    OrgProfile (``--profile org.json``), exactly as chunk 1 established for the
    rest of the config. No profile ⇒ no ``forge_*``, no ``ado_org``, and an EMPTY
    keyword list, which the injection hook reads as "injection off" until the
    user (or a profile) sets one.

Invoked by the brain-init skill (also runnable directly, via ``python3 -m
brain.init_home`` with ``lib/`` on ``PYTHONPATH``). Lands the KNOWLEDGE
CONTAINER at ``~/knowledge/`` (redesign ratified 2026-08-27)::

    ~/knowledge/
      memory/                        agent memory — beside the brains, in NONE
      config.json                    the home config (the primary file points here)
      brains/
        <org-slug>/private           the work vault, scaffolded on first run
        <org-slug>/shared            the org-readable mirror (empty until published)
        <org-slug>/org               the org-brain clone (empty until cloned)
        personal/<username>-brain    the brain that outlives the employer
        peers/                       teammates' shared brains, read-only

…then migrates the hub's Claude native-memory dir to ``~/knowledge/memory`` and
replaces it with a symlink, so future memory writes flow there.

WHY MEMORY IS NOT IN THE VAULT: memory is about the PERSON, and a person may keep
more than one brain. A brain-local memory dir (what this module used to create)
dies with the brain it sits in — it was considered and rejected on 2026-08-27.

Safety contract:
  * IDEMPOTENT — a correct existing symlink / config is a no-op.
  * NEVER deletes target content; refuses to clobber an unmigrated dir on
    conflict (leaves the native dir intact for manual resolution).
  * REVERSIBLE — see the undo notes in --help.

Reversibility (undo):
  The memory link is a plain symlink. To undo:
      rm ~/.claude/projects/<enc>/memory      # removes ONLY the link
  This never touches the link target. NEVER use a trailing slash with rm -rf on
  a symlink-to-dir (``rm -rf link/``) — that would recurse INTO the store and
  delete your memory. The memory store itself lives at ~/knowledge/memory.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``brain.config`` only.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import config

UNDO_DOC = """\
reversibility / undo:
  The memory setup is a plain symlink (native dir -> ~/knowledge/memory).
    rm ~/.claude/projects/<enc>/memory     removes ONLY the link (never the target)
  NEVER `rm -rf <link>/` with a trailing slash — that recurses into the store
  and deletes your memory. Your memory lives at ~/knowledge/memory and your
  vault at ~/knowledge/brains/<org-slug>/private.
  The pre-init brain config is backed up alongside it as <name>.json.bak.
"""

POINTER = {"config": "~/knowledge/config.json"}


def _scaffold_dir():
    """The bundled vault-scaffold: file-relative first (deterministic for tests
    and the normal layout), then $CLAUDE_PLUGIN_ROOT as a fallback.

    THE SECOND (and, since Phase 5, LAST) SANCTIONED ``__file__`` ANCHOR in the
    brain plane, with ``naming.py`` (its data file). Both point at a data
    directory that ships INSIDE the package, which is the only thing this anchor
    is for; ``orgpull.py`` held a third until Phase 5 replaced its path-based
    spawn with a ``-m`` entry point. The source hopped up TWO dirs (``scripts/`` -> repo root -> ``vault-scaffold/``);
    the scaffold is a child of this package now, so it is one hop. The env
    fallback keeps its role — it just names the in-package location."""
    local = Path(__file__).resolve().parent / "vault-scaffold"
    if local.exists():
        return local
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return Path(root) / "lib/brain/vault-scaffold" if root else local


def _is_empty(p):
    return (not p.exists()) or not any(p.iterdir())


def _native_memory_dir(home):
    """The hub native-memory dir: the ~/.claude/projects entry whose decoded
    path is the home dir itself (Claude encodes cwd by replacing os.sep -> '-')."""
    enc = str(home).replace(os.sep, "-")
    return home / ".claude/projects" / enc / "memory"


def _same_target(link, target):
    return os.path.realpath(str(link)) == os.path.realpath(str(target))


def _same_content(a, b):
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def merge_memory_md(existing, incoming):
    """Union the lines of two MEMORY.md files: keep existing order, append lines
    from ``incoming`` not already present."""
    seen = set()
    merged = []
    for line in existing.splitlines():
        merged.append(line)
        seen.add(line)
    for line in incoming.splitlines():
        if line not in seen:
            merged.append(line)
            seen.add(line)
    text = "\n".join(merged)
    if (existing.endswith("\n") or incoming.endswith("\n")) and not text.endswith("\n"):
        text += "\n"
    return text


def _scaffold(vault):
    scaffold = _scaffold_dir()
    vault.mkdir(parents=True, exist_ok=True)
    for item in sorted(scaffold.iterdir()):
        dest = vault / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    for gk in vault.rglob(".gitkeep"):
        gk.unlink()


# The context-injection keyword list scaffolded into the user's config. It lives
# HERE, never hard-coded in the shipped hook — so a user can edit or clear it
# (empty list disables injection) without patching code. EMPTY by default: the
# source shipped its own org's product names, and org identity arrives at runtime
# from an OrgProfile (``--profile``), never from a literal in this repo.
ORG_KEYWORDS = []


def _load_profile(path):
    """Load an OrgProfile (org.json). Raises ValueError on a broken/missing file."""
    p = Path(os.path.expanduser(str(path)))
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        raise ValueError(f"profile not found: {p}")
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"cannot read profile {p}: {e}")
    if not isinstance(data, dict):
        raise ValueError(f"profile {p} is not a JSON object")
    return data


def _apply_profile(cfg, profile):
    """Wire an OrgProfile's labels / forge / keywords through into the config dict.
    Only keys present in the profile are set — a partial profile is fine."""
    if profile.get("org_label"):
        cfg["org_label"] = profile["org_label"]
    if isinstance(profile.get("labels"), dict):
        cfg["labels"] = profile["labels"]
    if isinstance(profile.get("keywords"), list):
        cfg["inject_keywords"] = [k for k in profile["keywords"] if isinstance(k, str)]
    forge = profile.get("forge")
    if isinstance(forge, dict):
        kind = str(forge.get("kind") or "ado").strip().lower()
        cfg["forge_kind"] = kind
        # github uses owner/repo; ado uses org/project/repo. Map both onto the
        # generic forge_* config keys (forge_org == owner on github).
        org = forge.get("org") or forge.get("owner")
        if org:
            cfg["forge_org"] = org
        if forge.get("project"):
            cfg["forge_project"] = forge["project"]
        if forge.get("repo"):
            cfg["forge_repo"] = forge["repo"]
        if forge.get("target_branch"):
            cfg["forge_target_branch"] = forge["target_branch"]
        # ADO also drives the work-item helper (brain.ado_tree) via ado_org.
        if kind == "ado" and org:
            cfg["ado_org"] = org
    return cfg


def _tilde(path):
    """Render a path under $HOME as ``~/...``. The config is a file a human edits,
    so it is written the way a human writes it — and a ``~`` path also survives a
    moved home dir, which an absolute one does not."""
    p = Path(path)
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


#: The config keys whose stock default is derived from the org slug. Used to tell
#: "the user never changed this" from a real customisation when the slug changes.
_ORG_PATH_DEFAULTS = {
    "vault": config.DEFAULT_VAULT,
    "org_brain_clone": config.DEFAULT_ORG_BRAIN,
    "publish_mirror": config.DEFAULT_PUBLISH_MIRROR,
}


def _resolve_org_slug(old_full, profile=None):
    """The org folder name under ``brains/``: profile > existing config > default.

    The profile wins because applying an OrgProfile is a deliberate act of
    adopting org settings — the same precedence :func:`_build_config` uses."""
    slug = None
    if isinstance(old_full, dict) and old_full.get("org_slug"):
        slug = old_full["org_slug"]
    if profile and profile.get("org_slug"):
        slug = profile["org_slug"]
    return config.slugify(slug) or config.DEFAULT_ORG_SLUG


def _build_config(old_full, profile=None, org_slug=None):
    """The config to write: defaults, overlaid with the user's EXISTING config,
    overlaid with the OrgProfile.

    Precedence is deliberate and in this order:

    * **defaults** — only ever fill a key nobody has set.
    * **existing** — every key already in the user's effective config WINS over a
      default, and keys this module knows nothing about (``publish_mirror``,
      ``tasks_db``, …) are carried through untouched. Re-running init on a
      customised install used to reset `vault`/`memory`/`org_brain_clone` to the
      stock paths and DROP the unknown keys outright — with no backup of the file
      it rewrote — which made a "safe, idempotent" setup command destructive for
      anyone whose layout is not the stock one.
    * **profile** — an explicitly-passed OrgProfile wins over both, because
      applying a profile is a deliberate act of adopting org settings.
    """
    old = dict(old_full) if isinstance(old_full, dict) else {}
    prev_slug = config.slugify(old.get("org_slug")) or config.DEFAULT_ORG_SLUG
    slug = org_slug or _resolve_org_slug(old_full, profile)

    # Every default path is READ FROM brain.config, never re-spelled here: writing
    # a config the loader does not read is the one failure this module must be
    # incapable of. `publish_mirror` and `peers_dir` are deliberately NOT written
    # — their defaults already resolve to the right place, and writing
    # publish_mirror would make `config.publish_mirror_configured()` true for a
    # user who never set a mirror up, silently arming the auto-publish heal step.
    cfg = {
        "org_slug": slug,
        "vault": _tilde(config.DEFAULT_VAULT(slug)),
        "memory": _tilde(config.DEFAULT_MEMORY()),
        "org_brain_clone": _tilde(config.DEFAULT_ORG_BRAIN(slug)),
        "personal_brain": _tilde(config.DEFAULT_PERSONAL_BRAIN()),
        "inject_context": True,
        "auto_distill": True,
        "inject_keywords": list(ORG_KEYWORDS),
    }
    for k, v in old.items():
        # A per-org path still sitting at the PREVIOUS slug's stock default is not
        # a customisation — it follows the slug, rather than pinning the vault to
        # an org folder the profile just renamed. Anything else the user set (a
        # vault somewhere of their own, an unknown key) is carried through as-is.
        if k in _ORG_PATH_DEFAULTS and v == _tilde(_ORG_PATH_DEFAULTS[k](prev_slug)):
            continue
        cfg[k] = v
    if profile:
        _apply_profile(cfg, profile)
    return cfg


def _make_symlink(native, vault_memory, lines, dry_run):
    if sys.platform == "win32":
        lines.append(f"  WINDOWS: create the junction manually: "
                     f'mklink /J "{native}" "{vault_memory}"')
        return
    lines.append(f"  link {native} -> {vault_memory}")
    if not dry_run:
        native.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(vault_memory, native)


def _migrate_memory(home, vault_memory, lines, conflicts, dry_run):
    native = _native_memory_dir(home)

    if vault_memory.is_symlink():
        lines.append(f"WARNING: {vault_memory} is a symlink, expected a real dir — skipping memory migration")
        return
    if not vault_memory.exists():
        lines.append(f"create memory dir: {vault_memory}")
        if not dry_run:
            vault_memory.mkdir(parents=True, exist_ok=True)

    if native.is_symlink():
        if _same_target(native, vault_memory):
            lines.append(f"memory already linked: {native} -> {vault_memory}")
        else:
            lines.append(f"WARNING: {native} is a symlink elsewhere ({os.path.realpath(str(native))}) "
                         f"— leaving it; remove it manually to relink")
        return

    if not native.exists():
        lines.append(f"no native hub memory dir; pre-linking for future writes")
        _make_symlink(native, vault_memory, lines, dry_run)
        return

    # native is a real dir: migrate then link
    lines.append(f"migrate memory: {native} -> {vault_memory}")
    for entry in sorted(native.iterdir()):
        dest = vault_memory / entry.name
        if entry.name == "MEMORY.md" and dest.exists():
            lines.append("  merge MEMORY.md (line-union)")
            if not dry_run:
                dest.write_text(merge_memory_md(
                    dest.read_text(errors="ignore"), entry.read_text(errors="ignore")))
                entry.unlink()
        elif not dest.exists():
            lines.append(f"  move {entry.name}")
            if not dry_run:
                shutil.move(str(entry), str(dest))
        elif entry.is_file() and dest.is_file() and _same_content(entry, dest):
            lines.append(f"  identical; drop native {entry.name}")
            if not dry_run:
                entry.unlink()
        else:
            lines.append(f"  CONFLICT {entry.name}: differs from vault copy — left in native")
            conflicts.append(str(entry))

    if dry_run:
        if conflicts:
            lines.append(f"  would REFUSE to link: {len(conflicts)} conflict(s)")
        else:
            lines.append(f"  would remove emptied {native} and link -> {vault_memory}")
        return
    if _is_empty(native):
        native.rmdir()
        _make_symlink(native, vault_memory, lines, dry_run)
    else:
        lines.append(f"  REFUSED to link: {native} still holds unmigrated files "
                     f"{sorted(p.name for p in native.iterdir())} — resolve conflicts, then re-run")


TEAM_RULES_IMPORT = "~/knowledge/brains/org/org/team-rules.md"  # fallback only; see _team_rules_import
TR_MARKER_START = "<!-- brain-station:team-rules -->"
TR_MARKER_END = "<!-- /brain-station:team-rules -->"


def _team_rules_import(org_brain_clone=None):
    """The path the CLAUDE.md `@import` should point at — derived from the config's
    ``org_brain_clone``, which is where every other reader in the brain plane looks
    for the clone. It used to be a hardcoded stock path, so any install whose clone
    sits elsewhere got an import of a file that does not exist — and a missing
    `@import` is silently inert, so the team rules simply never loaded and nothing
    said so."""
    base = str(org_brain_clone or TEAM_RULES_IMPORT.rsplit("/", 1)[0]).rstrip("/")
    return f"{base}/team-rules.md"


def _team_rules_block(org_brain_clone=None):
    return (f"{TR_MARKER_START}\n"
            "<!-- Auto-managed by /brain-init. Org team-rules live in the org-brain clone; "
            "edit them there (via /brain-promote), not here. Inert until org brain is linked. -->\n"
            f"@{_team_rules_import(org_brain_clone)}\n"
            f"{TR_MARKER_END}\n")


def _ensure_claude_md_import(home, lines, dry_run, no_claude_md, org_brain_clone=None):
    """Idempotently keep a marker-guarded @import of the team-rules file in the
    user-level ~/.claude/CLAUDE.md. Never duplicates.

    The block is declared auto-managed, so when it is present but points somewhere
    other than the configured clone it is REWRITTEN rather than left alone — a
    stale path here fails silently (an unresolvable `@import` is simply inert), so
    "already present" was never enough to be correct. Only the marked block is
    touched; the rest of the file is preserved byte-for-byte."""
    claude_md = home / ".claude/CLAUDE.md"
    if no_claude_md:
        lines.append("skip ~/.claude/CLAUDE.md team-rules @import (--no-claude-md)")
        return
    existing = claude_md.read_text(errors="ignore") if claude_md.exists() else ""
    block = _team_rules_block(org_brain_clone)
    want_import = f"@{_team_rules_import(org_brain_clone)}"
    if TR_MARKER_START in existing:
        start = existing.index(TR_MARKER_START)
        end = existing.find(TR_MARKER_END)
        if end == -1:                       # truncated/hand-mangled block
            lines.append(f"team-rules @import block in {claude_md} is missing its "
                         f"end marker — leaving it alone")
            return
        end += len(TR_MARKER_END)
        current = existing[start:end]
        if want_import in current:
            lines.append(f"team-rules @import already correct in {claude_md}")
            return
        lines.append(f"repoint team-rules @import in {claude_md} -> {want_import}")
        if dry_run:
            return
        tail = existing[end:].lstrip("\n")
        claude_md.write_text(existing[:start] + block + (("\n" + tail) if tail else ""))
        return
    lines.append(f"add team-rules @import block to {claude_md}")
    if dry_run:
        return
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    claude_md.write_text(prefix + block)


def _onboarding_checklist(home, no_claude_md=False):
    """A short, honest post-setup checklist reflecting the real on-disk state.

    Reads the paths back through :func:`config.load`, so an install whose vault or
    memory sits somewhere of its own is described where it actually is rather than
    where the stock layout would have put it."""
    cfg = config.load(_warn=False)
    vault = cfg["vault"]
    memory = cfg["memory"]

    def mark(ok):
        return "✓" if ok else "○"

    native = _native_memory_dir(home)
    keywords = cfg.get("inject_keywords") or []
    cm = home / ".claude/CLAUDE.md"
    tr_wired = (not no_claude_md) and cm.exists() and TR_MARKER_START in cm.read_text(errors="ignore")
    tr_label = ("team-rules @import wired into ~/.claude/CLAUDE.md" if not no_claude_md
                else "team-rules @import skipped (--no-claude-md)")

    return [
        f"{mark((vault / 'INDEX.md').exists())} vault scaffolded  ({vault})",
        f"{mark(native.is_symlink())} native memory linked to {memory}  (beside your brains, inside none)",
        f"{mark(bool(keywords))} injection keywords set  ({len(keywords)} configured)",
        f"{mark(tr_wired) if not no_claude_md else '○'} {tr_label}",
        "• the brain plane ships inside task-station — nothing else to install",
        "• next: restart Claude Code so the hooks + team-rules @import load; then /brain-heal",
    ]


def run(dry_run=False, no_claude_md=False, profile=None):
    """Execute (or, with dry_run, plan) the home setup. Returns (lines, conflicts).

    ``profile`` is an optional OrgProfile dict (from ``--profile org.json``) whose
    labels/forge/keywords are wired into the written config; None keeps defaults."""
    home = Path.home()
    lines = []
    conflicts = []

    # capture the pre-init primary config for backup + preference carryover. The
    # path is config's, never re-spelled here — writing a config the loader does
    # not read is the one failure this module must be incapable of.
    primary = config._primary_config_path()
    old_raw = None
    old_full = None
    old_is_pointer = False
    if primary.exists():
        try:
            old_raw = json.loads(primary.read_text())
        except (json.JSONDecodeError, OSError):
            old_raw = None
        if isinstance(old_raw, dict) and isinstance(old_raw.get("config"), str):
            old_is_pointer = True
            # FOLLOW the pointer. The primary file is a one-line redirect, so the
            # config actually in force lives at the far end of it — reading only
            # the pointer meant every re-run rebuilt from stock defaults and
            # silently discarded the real settings.
            try:
                pointed = Path(os.path.expanduser(old_raw["config"]))
                loaded = json.loads(pointed.read_text())
                if isinstance(loaded, dict):
                    old_full = loaded
            except (json.JSONDecodeError, OSError):
                old_full = None
        elif isinstance(old_raw, dict):
            old_full = old_raw

    # 0. decide the config FIRST, then create exactly the tree it names. Deriving
    # the dirs from the written config (rather than re-spelling them) is what
    # makes "what init creates" and "what the loader reads" the same thing by
    # construction — including for an install whose paths are its own.
    if profile:
        lines.append("apply OrgProfile: labels/forge/keywords from --profile")
    new_cfg = _build_config(old_full, profile)
    slug = new_cfg["org_slug"]

    def _cfg_path(key, default):
        return Path(os.path.expanduser(str(new_cfg.get(key) or default)))

    root = config._home_root()          # ~/knowledge — the container
    brains = config._brains_root()      # ~/knowledge/brains
    vault = _cfg_path("vault", config.DEFAULT_VAULT(slug))
    org_brain = _cfg_path("org_brain_clone", config.DEFAULT_ORG_BRAIN(slug))
    shared = _cfg_path("publish_mirror", config.DEFAULT_PUBLISH_MIRROR(slug))
    personal = _cfg_path("personal_brain", config.DEFAULT_PERSONAL_BRAIN())
    peers = _cfg_path("peers_dir", config.DEFAULT_PEERS_DIR())
    # BESIDE the brains, inside none of them (2026-08-27). _migrate_memory creates it.
    memory = _cfg_path("memory", config.DEFAULT_MEMORY())

    # 1. home dirs
    for d in (root, brains, vault, org_brain, shared, personal, peers):
        if d.exists():
            lines.append(f"exists: {d}")
        else:
            lines.append(f"create dir: {d}")
            if not dry_run:
                d.mkdir(parents=True, exist_ok=True)

    # 1b. scaffold vault if empty
    if _is_empty(vault):
        lines.append(f"scaffold vault from {_scaffold_dir()}")
        if not dry_run:
            _scaffold(vault)
        lines.append(f"  reminder: `cd {_tilde(vault)} && git init && git add -A "
                     "&& git commit -m 'brain: vault scaffold'`")
    else:
        lines.append("vault not empty; leaving existing content as-is")
    lines.append(f"org-brain: clone your org brain into {org_brain} when ready (left empty otherwise)")
    lines.append(f"personal brain: yours to fill, off org storage by construction ({personal})")

    # 2. home config.json
    config_path = config._home_config_path()
    desired = json.dumps(new_cfg, indent=2) + "\n"
    if config_path.exists() and config_path.read_text() == desired:
        lines.append(f"config already correct: {config_path}")
    else:
        # Back it up before rewriting. This file holds the paths everything else
        # resolves through, and until now the only backup taken was of the
        # POINTER — the one file that is trivially reconstructible.
        if config_path.exists():
            bak = config_path.parent / (config_path.name + ".bak")
            lines.append(f"back up existing config: {config_path} -> {bak}")
            if not dry_run:
                shutil.copy2(config_path, bak)
        lines.append(f"write config: {config_path}")
        if not dry_run:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(desired)

    # 3. rewrite primary as the one-line pointer (back up a full config)
    if old_is_pointer and old_raw == POINTER:
        lines.append(f"pointer already correct: {primary}")
    else:
        if primary.exists() and not old_is_pointer:
            bak = primary.parent / (primary.name + ".bak")
            lines.append(f"back up existing config: {primary} -> {bak}")
            if not dry_run:
                shutil.copy2(primary, bak)
        lines.append(f"write pointer: {primary} -> {POINTER['config']}")
        if not dry_run:
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary.write_text(json.dumps(POINTER) + "\n")

    # 4. memory migrate-then-link
    _migrate_memory(home, memory, lines, conflicts, dry_run)

    # 5. wire the team-rules @import into the user-level CLAUDE.md (idempotent)
    _ensure_claude_md_import(home, lines, dry_run, no_claude_md,
                             org_brain_clone=new_cfg.get("org_brain_clone"))

    return lines, conflicts


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="brain-init",
        description=__doc__,
        epilog=UNDO_DOC,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--dry-run", action="store_true", help="print the exact plan; make no changes")
    ap.add_argument("--no-claude-md", action="store_true",
                    help="do NOT add the team-rules @import to ~/.claude/CLAUDE.md")
    ap.add_argument("--profile", metavar="ORG_JSON",
                    help="OrgProfile (org.json) whose labels/forge/keywords theme the config")
    a = ap.parse_args(argv)

    profile = None
    if a.profile:
        try:
            profile = _load_profile(a.profile)
        except ValueError as e:
            sys.exit(f"brain-init: {e}")

    lines, conflicts = run(dry_run=a.dry_run, no_claude_md=a.no_claude_md, profile=profile)
    print(("[dry-run] " if a.dry_run else "") + "brain-station home setup:")
    for ln in lines:
        print("  " + ln)
    if not a.dry_run:
        print("\nonboarding checklist:")
        for it in _onboarding_checklist(Path.home(), no_claude_md=a.no_claude_md):
            print("  " + it)
    if conflicts:
        print(f"\n{len(conflicts)} conflict(s) left in the native memory dir for manual resolution:")
        for c in conflicts:
            print(f"  {c}")
        return 0 if a.dry_run else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
