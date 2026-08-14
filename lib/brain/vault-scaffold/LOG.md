# LOG — private brain chronicle

Append-only. Format: `- YYYY-MM-DD HH:MM · <op> · <message>` (ops: bootstrap, ingest, note, report, heal, lint, promote). Append via `PYTHONPATH=<lib> python3 -m brain.search log <op> <message>`.
