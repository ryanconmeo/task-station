# store.py
"""Storage backend for Task Station — the read/write layer behind the public
primitives in task-station.py (`load_task`, `save_task`, `all_tasks`, the link
and counter helpers, …).

Two interchangeable backends sit behind one interface:

  * `SqliteBackend` — a single indexed `<store>/tasks.db`. This is the default.
    The old store kept one JSON file per task and one file per session link, so
    `all_tasks()` (called on EVERY user message via the hooks) read and parsed
    hundreds of files. SQLite turns listing/counting/sorting into indexed queries
    and makes `live_session_count` / stale-link GC single statements.

  * `JsonBackend` — the original one-file-per-task / one-file-per-link store,
    kept verbatim as a fallback for the (theoretical) case where `sqlite3` is
    unavailable. `sqlite3` is stdlib, so the guard mirrors the optional
    `try: import categories` pattern in task-station.py — belt and suspenders.

Both backends are parameterised by a `store_dir` (the `<TASK_STATION_HOME>/store`
path task-station.py resolves via paths.data_dir()). They never read the
environment themselves, so the tests' temp-home isolation — which repoints
task-station.py's STORE global — flows through unchanged.
"""
import json
import os
import sys

# Guarded, belt-and-suspenders: sqlite3 is stdlib, but mirror the optional-import
# pattern so the tool still runs (on the JSON store) if it's ever missing. Tests
# monkeypatch this attribute to None to exercise the fallback path.
try:
    import sqlite3
except Exception:  # pragma: no cover - sqlite3 is part of the stdlib
    sqlite3 = None

# Link value marking a session intentionally untracked (mirrors task-station.py's
# SKIP_SENTINEL). Kept here so migration's stale-link GC doesn't drop skip markers.
SKIP_SENTINEL = "__skip__"

# Env opt-in that authorises auto-migration of an existing JSON store into SQLite.
# The installed plugin's hook entrypoints export TASK_STATION_MIGRATE=1, so real
# users upgrade seamlessly; a bare `python3 lib/task-station.py ...` from a dev
# checkout has it unset and therefore NEVER migrates as a side effect.
MIGRATE_OPT_IN_ENV = "TASK_STATION_MIGRATE"


def _migrate_opted_in():
    val = os.environ.get(MIGRATE_OPT_IN_ENV, "").strip().lower()
    return val not in ("", "0", "false", "no", "off")


# ---------------------------------------------------------------- JSON store ---

class JsonBackend:
    """The original file-per-task / file-per-link store. Used only when sqlite3
    is unavailable. Behaviour is identical to the pre-SQLite task-station.py."""

    def __init__(self, store_dir):
        self.store_dir = store_dir
        self.tasks_dir = os.path.join(store_dir, "tasks")
        self.links_dir = os.path.join(store_dir, "links")

    def close(self):
        pass

    def ensure(self):
        os.makedirs(self.tasks_dir, exist_ok=True)
        os.makedirs(self.links_dir, exist_ok=True)

    def _atomic_write(self, path, text):
        tmp = path + ".tmp." + str(os.getpid())
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)

    # -- tasks --
    def _task_path(self, task_id):
        return os.path.join(self.tasks_dir, task_id + ".json")

    def load_task(self, task_id):
        try:
            with open(self._task_path(task_id)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def save_task(self, task):
        self.ensure()
        self._atomic_write(self._task_path(task["id"]), json.dumps(task, indent=2))

    def all_tasks(self):
        self.ensure()
        out = []
        for name in os.listdir(self.tasks_dir):
            if name.endswith(".json") and not name.endswith(".tmp"):
                t = self.load_task(name[:-5])
                if t:
                    out.append(t)
        return out

    # -- links --
    def _link_path(self, session):
        return os.path.join(self.links_dir, session)

    def get_link(self, session):
        self.ensure()
        try:
            with open(self._link_path(session)) as f:
                task_id = f.read().strip()
        except OSError:
            return None
        return task_id or None

    def set_link(self, session, task_id):
        self.ensure()
        self._atomic_write(self._link_path(session), task_id)

    def clear_link(self, session):
        try:
            os.remove(self._link_path(session))
        except OSError:
            pass

    def live_session_count(self, task):
        tid = task.get("id")
        return sum(1 for s in task.get("sessions", []) if self.get_link(s) == tid)

    # -- miss counter (.n) --
    def _count_path(self, session):
        return self._link_path(session) + ".n"

    def get_count(self, session):
        try:
            with open(self._count_path(session)) as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def bump_count(self, session):
        n = self.get_count(session) + 1
        self.ensure()
        self._atomic_write(self._count_path(session), str(n))
        return n

    def clear_count(self, session):
        try:
            os.remove(self._count_path(session))
        except OSError:
            pass

    # -- edit / blocked markers --
    def _edited_path(self, session):
        return self._link_path(session) + ".edited"

    def _blocked_path(self, session):
        return self._link_path(session) + ".blocked"

    def mark_edited(self, session):
        self.ensure()
        p = self._edited_path(session)
        if os.path.exists(p):
            return False
        self._atomic_write(p, "1")
        return True

    def has_edited(self, session):
        return os.path.exists(self._edited_path(session))

    def get_blocked(self, session):
        try:
            with open(self._blocked_path(session)) as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def bump_blocked(self, session):
        n = self.get_blocked(session) + 1
        self.ensure()
        self._atomic_write(self._blocked_path(session), str(n))
        return n

    def clear_edit_markers(self, session):
        for p in (self._edited_path(session), self._blocked_path(session)):
            try:
                os.remove(p)
            except OSError:
                pass

    def migrate(self):
        # The JSON files ARE the store here — nothing to migrate into.
        return {"tasks": 0, "links_kept": 0, "links_stale": 0, "backup": None}


# -------------------------------------------------------------- SQLite store ---

class SqliteBackend:
    """Single-file indexed store (`<store>/tasks.db`). The `data` column keeps the
    full task dict as JSON so no field is ever dropped; the typed columns exist
    only to index/sort. The `links` table folds every per-session sidecar file
    (the link itself, the `.n` miss counter, the `.edited`/`.blocked` markers)
    into one row keyed by session."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS tasks (
        id           TEXT PRIMARY KEY,
        seq          INTEGER,
        title        TEXT,
        summary      TEXT,
        status       TEXT,
        color        TEXT,
        effort       TEXT,
        created_ts   REAL,
        updated_ts   REAL,
        pinned       INTEGER,
        sessions     TEXT,
        session_meta TEXT,
        log          TEXT,
        data         TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_seq     ON tasks(seq);
    CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_ts);

    CREATE TABLE IF NOT EXISTS links (
        session TEXT PRIMARY KEY,
        task_id TEXT,
        n       INTEGER NOT NULL DEFAULT 0,
        edited  INTEGER NOT NULL DEFAULT 0,
        blocked INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_links_task ON links(task_id);
    """

    def __init__(self, store_dir):
        self.store_dir = store_dir
        self.db_path = os.path.join(store_dir, "tasks.db")
        self._conn = None
        self._inited = False      # one-time auto-migrate / divergence check done?

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def _raw_connect(self):
        """Open the connection + ensure schema. No migration, no divergence
        check — pure plumbing, safe to call from migrate() itself. CREATES the DB
        file, so it must only be reached when a SQLite store is actually intended
        (the factory never builds this backend for the un-opted-in JSON path)."""
        if self._conn is not None:
            return self._conn
        os.makedirs(self.store_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        # WAL + a busy timeout so concurrent claude sessions/hooks don't lock each
        # other out; NORMAL sync is the standard WAL durability/speed trade-off.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(self._SCHEMA)
        conn.commit()
        self._conn = conn
        return conn

    def _connect(self):
        """The connection every public primitive uses. On first touch it runs a
        one-time init: auto-migrate a JSON store when opted in, then warn if a DB
        and a live JSON store coexist.

        The migration gate is EMPTINESS-based, not file-existence based: migrate
        only when opted in AND this DB holds zero tasks AND a JSON store has
        tasks. So a stray empty/partial tasks.db (e.g. left by an earlier bare
        run) no longer blocks a real migration — the opted-in run self-heals by
        importing the full JSON, then atomically swapping it into the backup."""
        conn = self._raw_connect()
        if not self._inited:
            self._inited = True
            if (_migrate_opted_in() and self._db_task_count() == 0
                    and _json_store_has_tasks(self.store_dir)):
                # Guarded so a migration hiccup never wedges the hook — the
                # explicit `migrate` subcommand re-runs it and surfaces errors.
                try:
                    self.migrate()
                except Exception:  # pragma: no cover - defensive
                    pass
            self._warn_if_diverged()
        return conn

    def _db_task_count(self):
        return self._raw_connect().execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]

    def ensure(self):
        self._connect()

    def _warn_if_diverged(self):
        """Belt-and-suspenders: a DB plus a non-empty JSON tasks/ is an ambiguous
        double store (an un-opted-in dev run that created an empty DB, or a
        half-finished migration). After a real migration the swap removes the
        JSON, so this is silent then. When it isn't, never silently prefer the
        DB — name both stores on stderr so the user can reconcile."""
        tasks_dir = os.path.join(self.store_dir, "tasks")
        try:
            has_json = os.path.isdir(tasks_dir) and any(
                n.endswith(".json") for n in os.listdir(tasks_dir))
        except OSError:
            has_json = False
        if has_json:
            sys.stderr.write(
                "task-station: WARNING - two task stores coexist:\n"
                "  SQLite (in use): %s\n"
                "  JSON   (IGNORED, possibly newer): %s\n"
                "The JSON store is NOT being read. Reconcile them - run "
                "`task-station migrate` to import it, or remove the stale store "
                "- to silence this.\n" % (self.db_path, tasks_dir))

    # -- tasks --
    def load_task(self, task_id):
        conn = self._connect()
        row = conn.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def save_task(self, task):
        conn = self._connect()
        conn.execute(
            """INSERT INTO tasks
                 (id, seq, title, summary, status, color, effort,
                  created_ts, updated_ts, pinned, sessions, session_meta, log, data)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 seq=excluded.seq, title=excluded.title, summary=excluded.summary,
                 status=excluded.status, color=excluded.color, effort=excluded.effort,
                 created_ts=excluded.created_ts, updated_ts=excluded.updated_ts,
                 pinned=excluded.pinned, sessions=excluded.sessions,
                 session_meta=excluded.session_meta, log=excluded.log,
                 data=excluded.data""",
            (
                task["id"], task.get("seq"), task.get("title"), task.get("summary"),
                task.get("status"), task.get("color"), task.get("effort"),
                task.get("created_ts"), task.get("updated_ts"),
                1 if task.get("pinned") else 0,
                json.dumps(task.get("sessions", [])),
                json.dumps(task.get("session_meta", {})),
                json.dumps(task.get("log", [])),
                json.dumps(task),
            ),
        )
        conn.commit()

    def all_tasks(self):
        conn = self._connect()
        return [json.loads(r["data"]) for r in conn.execute("SELECT data FROM tasks")]

    # -- links --
    def get_link(self, session):
        conn = self._connect()
        row = conn.execute("SELECT task_id FROM links WHERE session=?", (session,)).fetchone()
        if not row:
            return None
        return row["task_id"] or None

    def set_link(self, session, task_id):
        conn = self._connect()
        # Only the pointer changes — n/edited/blocked for the session survive.
        conn.execute(
            """INSERT INTO links (session, task_id) VALUES (?, ?)
               ON CONFLICT(session) DO UPDATE SET task_id=excluded.task_id""",
            (session, task_id),
        )
        conn.commit()

    def clear_link(self, session):
        # Drop the pointer but keep the row's counters/markers, mirroring the JSON
        # store where clearing the link removes only the `<session>` file.
        conn = self._connect()
        conn.execute("UPDATE links SET task_id=NULL WHERE session=?", (session,))
        conn.commit()

    def live_session_count(self, task):
        sessions = task.get("sessions", [])
        if not sessions:
            return 0
        conn = self._connect()
        rows = conn.execute(
            "SELECT session FROM links WHERE task_id=?", (task.get("id"),)
        ).fetchall()
        live = {r["session"] for r in rows}
        # Count over the (append-only, possibly duplicated) sessions list so the
        # result is identical to the JSON loop, including duplicate entries.
        return sum(1 for s in sessions if s in live)

    # -- miss counter --
    def get_count(self, session):
        conn = self._connect()
        row = conn.execute("SELECT n FROM links WHERE session=?", (session,)).fetchone()
        return row["n"] if row else 0

    def bump_count(self, session):
        conn = self._connect()
        conn.execute(
            """INSERT INTO links (session, n) VALUES (?, 1)
               ON CONFLICT(session) DO UPDATE SET n=n+1""",
            (session,),
        )
        conn.commit()
        return self.get_count(session)

    def clear_count(self, session):
        conn = self._connect()
        conn.execute("UPDATE links SET n=0 WHERE session=?", (session,))
        conn.commit()

    # -- edit / blocked markers --
    def mark_edited(self, session):
        conn = self._connect()
        row = conn.execute("SELECT edited FROM links WHERE session=?", (session,)).fetchone()
        if row and row["edited"]:
            return False
        conn.execute(
            """INSERT INTO links (session, edited) VALUES (?, 1)
               ON CONFLICT(session) DO UPDATE SET edited=1""",
            (session,),
        )
        conn.commit()
        return True

    def has_edited(self, session):
        conn = self._connect()
        row = conn.execute("SELECT edited FROM links WHERE session=?", (session,)).fetchone()
        return bool(row and row["edited"])

    def get_blocked(self, session):
        conn = self._connect()
        row = conn.execute("SELECT blocked FROM links WHERE session=?", (session,)).fetchone()
        return row["blocked"] if row else 0

    def bump_blocked(self, session):
        conn = self._connect()
        conn.execute(
            """INSERT INTO links (session, blocked) VALUES (?, 1)
               ON CONFLICT(session) DO UPDATE SET blocked=blocked+1""",
            (session,),
        )
        conn.commit()
        return self.get_blocked(session)

    def clear_edit_markers(self, session):
        conn = self._connect()
        conn.execute("UPDATE links SET edited=0, blocked=0 WHERE session=?", (session,))
        conn.commit()

    # -- migration (JSON -> SQLite) --
    def migrate(self):
        """Import an existing JSON store into SQLite, then ATOMICALLY hand the
        DB sole ownership: after the rows are written and verified, the JSON
        `tasks/`/`links/` dirs are moved (os.rename) into a timestamped backup
        dir, so no live JSON is left to shadow the DB. Always works regardless of
        the opt-in flag (it's the explicit, user-intended path). Idempotent: once
        the JSON has been moved away a re-run finds nothing and is a no-op."""
        conn = self._raw_connect()
        self._inited = True   # migrate owns init; don't let _connect re-trigger it
        tasks_dir = os.path.join(self.store_dir, "tasks")
        links_dir = os.path.join(self.store_dir, "links")

        json_tasks = []
        if os.path.isdir(tasks_dir):
            for name in sorted(os.listdir(tasks_dir)):
                if name.endswith(".json") and not name.endswith(".tmp"):
                    try:
                        with open(os.path.join(tasks_dir, name)) as f:
                            json_tasks.append(json.load(f))
                    except (OSError, ValueError):
                        pass

        links_present = os.path.isdir(links_dir) and bool(os.listdir(links_dir))
        if not json_tasks and not links_present:
            return {"tasks": 0, "links_kept": 0, "links_stale": 0, "backup": None}

        task_ids = set()
        for t in json_tasks:
            if t.get("id"):
                task_ids.add(t["id"])
                self.save_task(t)

        kept, stale = self._migrate_links(conn, links_dir, task_ids)

        # Verify every task landed before we move the JSON away. On mismatch we
        # raise WITHOUT swapping, so the JSON store is preserved untouched.
        got = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        if got < len(task_ids):
            raise RuntimeError(
                "migration verify failed: %d of %d tasks in DB" % (got, len(task_ids)))

        backup = self._swap_json_into_backup(json_tasks, tasks_dir, links_dir)
        return {
            "tasks": len(task_ids),
            "links_kept": kept,
            "links_stale": stale,
            "backup": backup,
        }

    def _migrate_links(self, conn, links_dir, task_ids):
        if not os.path.isdir(links_dir):
            return 0, 0
        # Collapse the per-session sidecar files into one record per session.
        sessions = {}
        for name in os.listdir(links_dir):
            path = os.path.join(links_dir, name)
            if not os.path.isfile(path) or ".tmp." in name or name.endswith(".tmp"):
                continue
            if name.endswith(".n"):
                base, kind = name[:-2], "n"
            elif name.endswith(".edited"):
                base, kind = name[:-7], "edited"
            elif name.endswith(".blocked"):
                base, kind = name[:-8], "blocked"
            else:
                base, kind = name, "link"
            rec = sessions.setdefault(base, {"task_id": None, "n": 0, "edited": 0, "blocked": 0})
            try:
                with open(path) as f:
                    content = f.read().strip()
            except OSError:
                continue
            if kind == "link":
                rec["task_id"] = content or None
            elif kind == "edited":
                rec["edited"] = 1
            elif kind in ("n", "blocked"):
                rec[kind] = int(content) if content.lstrip("-").isdigit() else 0

        kept = stale = 0
        for session, rec in sessions.items():
            tid = rec["task_id"]
            if tid is not None and tid != SKIP_SENTINEL and tid not in task_ids:
                rec["task_id"] = None  # stale link: GC the pointer, keep counters
                stale += 1
            if rec["task_id"] is None and not rec["n"] and not rec["edited"] and not rec["blocked"]:
                continue  # nothing worth persisting
            conn.execute(
                """INSERT INTO links (session, task_id, n, edited, blocked)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(session) DO UPDATE SET
                     task_id=excluded.task_id, n=excluded.n,
                     edited=excluded.edited, blocked=excluded.blocked""",
                (session, rec["task_id"], rec["n"], rec["edited"], rec["blocked"]),
            )
            if rec["task_id"] is not None:
                kept += 1
        conn.commit()
        return kept, stale

    def _swap_json_into_backup(self, json_tasks, tasks_dir, links_dir):
        """Move tasks/ and links/ into `<store>/json-backup-<stamp>/` via os.rename
        (atomic per dir). The stamp is the newest task's updated_ts (NOT wall-clock
        — the repo's render paths avoid time.time()/now() for determinism). After
        this the DB is the unambiguous sole store; nothing live remains to shadow
        it. A pre-existing backup dir gets a numeric suffix so nothing is clobbered."""
        stamps = [t.get("updated_ts") or t.get("created_ts") or 0 for t in json_tasks]
        newest = max(stamps) if stamps else 0
        if not newest:
            try:
                newest = os.stat(tasks_dir).st_mtime
            except OSError:
                newest = 0
        base = os.path.join(self.store_dir, "json-backup-%d" % int(newest))
        backup_dir = base
        suffix = 2
        while os.path.exists(backup_dir):
            backup_dir = "%s-%d" % (base, suffix)
            suffix += 1
        os.makedirs(backup_dir)
        for src in (tasks_dir, links_dir):
            if os.path.isdir(src):
                os.rename(src, os.path.join(backup_dir, os.path.basename(src)))
        return backup_dir


# ------------------------------------------------------------ backend factory ---

def _db_has_tasks(db_path):
    """True iff tasks.db exists AND holds >=1 task row. Opens READ-ONLY so the
    probe never creates the file, and tolerates a missing table / empty / corrupt
    DB (any such case counts as 'no data')."""
    if sqlite3 is None or not os.path.exists(db_path):
        return False
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db_path),
                               uri=True, timeout=5.0)
    except Exception:
        return False
    try:
        row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        return bool(row and row[0] > 0)
    except Exception:
        return False
    finally:
        conn.close()


def _json_store_has_tasks(store_dir):
    """True iff a legacy JSON task store (store/tasks/*.json) holds at least one
    task file."""
    tasks_dir = os.path.join(store_dir, "tasks")
    try:
        return os.path.isdir(tasks_dir) and any(
            n.endswith(".json") and not n.endswith(".tmp")
            for n in os.listdir(tasks_dir))
    except OSError:
        return False


def _select_backend(store_dir):
    """State-based backend selection (NOT file-existence based). In priority:
      1. sqlite3 unavailable          -> JsonBackend (fallback).
      2. DB already holds task data   -> SqliteBackend (steady state).
      3. a legacy JSON store has tasks:
           - opted in  -> SqliteBackend (its emptiness gate then migrates).
           - not opted -> JsonBackend: read JSON in place, create NO tasks.db.
             This is the dev / bare-invocation path — non-destructive and
             litter-free (no empty DB, no swap, no warning).
      4. neither                      -> SqliteBackend (fresh install)."""
    if sqlite3 is None:
        return JsonBackend(store_dir)
    if _db_has_tasks(os.path.join(store_dir, "tasks.db")):
        return SqliteBackend(store_dir)
    if _json_store_has_tasks(store_dir):
        return SqliteBackend(store_dir) if _migrate_opted_in() else JsonBackend(store_dir)
    return SqliteBackend(store_dir)


# Single-slot cache: keep one live backend (and, for SQLite, its connection) per
# resolved store dir + selected backend type. Selection is recomputed each call
# (the probe is cheap and read-only); when the resolved type changes — e.g. an
# opted-in run migrates JSON into the DB, flipping case 3 to case 2 — the old
# backend is closed and a fresh one cached.
_cache = {"key": None, "backend": None}


def get_backend(store_dir):
    backend = _select_backend(store_dir)
    key = (os.path.abspath(store_dir), type(backend).__name__)
    if _cache["key"] == key and _cache["backend"] is not None:
        return _cache["backend"]   # reuse the live one; discard the unconnected probe
    if _cache["backend"] is not None:
        try:
            _cache["backend"].close()
        except Exception:
            pass
    _cache["key"] = key
    _cache["backend"] = backend
    return backend


def migrate(store_dir):
    """Run the explicit JSON->SQLite migration regardless of the opt-in flag or
    current backend selection — the user asked for it directly. No-op when
    sqlite3 is unavailable."""
    if sqlite3 is None:
        return {"tasks": 0, "links_kept": 0, "links_stale": 0, "backup": None}
    b = SqliteBackend(store_dir)
    try:
        return b.migrate()
    finally:
        b.close()


def reset_cache():
    """Drop the cached backend (closing it). Tests call this when toggling the
    sqlite3 guard or store state so the next get_backend() reselects from scratch."""
    if _cache["backend"] is not None:
        try:
            _cache["backend"].close()
        except Exception:
            pass
    _cache["key"] = None
    _cache["backend"] = None
