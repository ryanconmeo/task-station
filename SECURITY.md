# Security Policy

## Supported versions

Task Station ships as a single rolling plugin; fixes land on the latest release.
Only the most recent published version is supported — please update before
reporting.

| Version | Supported |
|---------|-----------|
| 1.7.x   | ✅ |
| < 1.7   | ❌ (please update) |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Report privately via GitHub's
[**Report a vulnerability**](https://github.com/ryanconmeo/task-station/security/advisories/new)
flow (Security → Advisories → Report a vulnerability), which opens a private
advisory thread with the maintainer.

Please include:

- a description of the issue and its impact,
- steps to reproduce (or a proof-of-concept),
- affected version(s) and platform,
- any suggested remediation.

You can expect an initial acknowledgement within a few days. Once a fix is
available we'll coordinate a release and credit you in the advisory unless you
prefer to remain anonymous.

## Scope notes

Task Station is **local-first** and has **no telemetry** (see
[PRIVACY.md](PRIVACY.md)). The few code paths that can touch the network or your
wider environment are the most security-relevant:

- the opt-in update check (`config --update-check on`) — at most one
  `git ls-remote` to GitHub per day, no task data sent;
- opt-in repo enrichment (`/repos enrich <name>`) — the only model-egress path,
  off by default and gated;
- the delegation-policy block, which edits your global `CLAUDE.md` (100%
  reversible, hash-verified);
- the Desktop bridge, which writes an MCP entry into Claude Desktop's config and
  runs a local stdio server.

Issues in any of these — or any path that could leak task data, execute
unintended commands, or escape the local-only model — are in scope.
