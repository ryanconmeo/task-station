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
import shutil

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

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def _connect(self):
        if self._conn is not None:
            return self._conn
        os.makedirs(self.store_dir, exist_ok=True)
        db_existed = os.path.exists(self.db_path)
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
        if not db_existed:
            # First time we materialise the DB: pull in any existing JSON store.
            # Guarded so a migration hiccup never wedges the hook — the explicit
            # `migrate` subcommand re-runs it (idempotently) and surfaces errors.
            try:
                self.migrate()
            except Exception:  # pragma: no cover - defensive
                pass
        return conn

    def ensure(self):
        self._connect()

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
        """Idempotently import an existing JSON store. Backs the JSON up first,
        upserts every task, folds every link/counter/marker into the `links`
        table, and GCs links whose target task no longer exists. Re-running is a
        no-op set of upserts (no duplicates)."""
        conn = self._connect()
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

        backup = self._backup_json_store(json_tasks)

        task_ids = set()
        for t in json_tasks:
            if t.get("id"):
                task_ids.add(t["id"])
                self.save_task(t)

        kept, stale = self._migrate_links(conn, links_dir, task_ids)
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

    def _backup_json_store(self, json_tasks):
        """Copy tasks/ and links/ to `<store>/json-backup-<stamp>/`. The stamp is
        the newest task's updated_ts (NOT wall-clock — the repo's render paths
        avoid time.time()/now() for determinism) so a re-run reuses the same dir
        rather than piling up backups."""
        stamps = [t.get("updated_ts") or t.get("created_ts") or 0 for t in json_tasks]
        newest = max(stamps) if stamps else 0
        if not newest:
            try:
                newest = os.stat(os.path.join(self.store_dir, "tasks")).st_mtime
            except OSError:
                newest = 0
        backup_dir = os.path.join(self.store_dir, "json-backup-%d" % int(newest))
        for sub in ("tasks", "links"):
            src = os.path.join(self.store_dir, sub)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(backup_dir, sub), dirs_exist_ok=True)
        return backup_dir


# ------------------------------------------------------------ backend factory ---

# Single-slot cache: keep one live backend (and, for SQLite, its connection) per
# resolved store dir. Tests repoint task-station.py's STORE between cases, so when
# the key changes we close the previous backend before opening the next.
_cache = {"key": None, "backend": None}


def get_backend(store_dir):
    use_sqlite = sqlite3 is not None
    key = (os.path.abspath(store_dir), use_sqlite)
    if _cache["key"] == key and _cache["backend"] is not None:
        return _cache["backend"]
    if _cache["backend"] is not None:
        try:
            _cache["backend"].close()
        except Exception:
            pass
    backend = SqliteBackend(store_dir) if use_sqlite else JsonBackend(store_dir)
    _cache["key"] = key
    _cache["backend"] = backend
    return backend


def reset_cache():
    """Drop the cached backend (closing it). Tests call this when toggling the
    sqlite3 guard so the next get_backend() rebuilds from scratch."""
    if _cache["backend"] is not None:
        try:
            _cache["backend"].close()
        except Exception:
            pass
    _cache["key"] = None
    _cache["backend"] = None
