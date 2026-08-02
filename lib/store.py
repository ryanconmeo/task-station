# store.py
"""Storage backend for Task Station — the read/write layer behind the public
primitives in task-station.py (`load_task`, `save_task`, `all_tasks`, the link
and counter helpers, …).

One backend sits behind the interface:

  * `SqliteBackend` — a single indexed `<store>/tasks.db`. Listing/counting/sorting
    (`all_tasks()` runs on EVERY user message via the hooks) become indexed queries
    instead of reading hundreds of files. On startup it simply uses an existing
    `tasks.db` or creates a fresh empty one — there is NO migration of any prior
    store.

`sqlite3` is part of the Python standard library and is a hard requirement: if it
is unavailable, `get_backend()` raises a clear RuntimeError rather than silently
degrading. The backend is parameterised by a `store_dir` (the
`<TASK_STATION_HOME>/store` path task-station.py resolves via paths.data_dir()).
It never reads the environment itself, so the tests' temp-home isolation — which
repoints task-station.py's STORE global — flows through unchanged.
"""
import json
import os
import re

# sqlite3 is stdlib and required. The guard keeps the module importable so
# get_backend() can raise a clear RuntimeError instead of an ImportError at load;
# tests monkeypatch this attribute to None to exercise that error path.
try:
    import sqlite3
except Exception:  # pragma: no cover - sqlite3 is part of the stdlib
    sqlite3 = None


class RevConflict(Exception):
    """Raised by save_task(task, expected_rev=N) when the row's rev no longer
    equals N — i.e. another writer committed in between. Callers using store.mutate
    never see this (it reloads + re-runs the mutator); it surfaces only to code that
    passes expected_rev by hand."""


# The transient field load_task attaches to carry the row's optimistic-lock version.
# It is NEVER persisted into the JSON `data` blob (stripped on write) and must be
# stripped from any exported/rendered task dict — see strip_rev().
REV_FIELD = "_rev"


def strip_rev(task):
    """Return `task` without the transient REV_FIELD — for export/serialisation
    surfaces that dump the whole dict. Safe on a dict that never had it; returns a
    shallow copy only when a strip is needed so callers can dump the result."""
    if isinstance(task, dict) and REV_FIELD in task:
        t = dict(task)
        t.pop(REV_FIELD, None)
        return t
    return task


# -------------------------------------------------------------- search core ---
#
# One text model + one LIKE fallback shared by BOTH backends, so a search hits the
# same fields whether it runs through the FTS5 index or the sqlite3-less fallback.

# Snippet window (chars) either side of the first matched term in the LIKE path.
SNIPPET_RADIUS = 44


def task_search_text(task):
    """Flatten one task dict into a single searchable text blob — what both the
    FTS5 index and the LIKE fallback match against. Reaches the title, the human
    summary/goal/next-step, every decision + checklist step, the activity log, the
    dated milestone history, and the linked repos/PRs/stories. Best-effort and
    fully defensive: an odd field shape is coerced, never raised on."""
    parts = []

    def add(v):
        if not v:
            return
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, (list, tuple)):
            for x in v:
                add(x)
        elif isinstance(v, dict):
            # log/history/pr/story entries → pull only the human-text fields.
            for k in ("note", "text", "desc", "title", "url"):
                if v.get(k):
                    parts.append(str(v[k]))
        else:
            parts.append(str(v))

    add(task.get("title"))
    add(task.get("summary"))
    add(task.get("goal"))
    add(task.get("state"))
    add(task.get("decisions"))
    add([s.get("text") for s in (task.get("steps") or []) if isinstance(s, dict)])
    add(task.get("log"))          # per-prompt activity entries {ts, note}
    add(task.get("history"))      # dated milestones {ts, text}
    add(task.get("projects"))
    add(task.get("prs"))
    add(task.get("stories"))
    # Glossary terms: the canonical name + one-line definition of each term (the
    # `layer`/`state` tags are indexing noise, so they are intentionally skipped).
    for g in (task.get("glossary") or []):
        if isinstance(g, dict):
            add(g.get("name"))
            add(g.get("def"))
    return "\n".join(p for p in parts if p)


def _search_terms(query):
    """Lowercased alphanumeric tokens of a free-text query (the unit both search
    paths match on). Punctuation is dropped so a stray quote never breaks a query."""
    return re.findall(r"[a-z0-9]+", (query or "").lower())


def _like_snippet(text, terms):
    """A one-line snippet of `text` centred on the first matched term, whitespace-
    collapsed, with `…` marking elided ends. Falls back to the head of the text
    when no term is located (shouldn't happen — the caller only snippets hits)."""
    flat = re.sub(r"\s+", " ", text).strip()
    low = flat.lower()
    hits = [low.find(t) for t in terms if low.find(t) != -1]
    pos = min(hits) if hits else -1
    if pos < 0:
        head = flat[: 2 * SNIPPET_RADIUS]
        return head + ("…" if len(flat) > len(head) else "")
    start = max(0, pos - SNIPPET_RADIUS)
    end = min(len(flat), pos + SNIPPET_RADIUS)
    snip = flat[start:end]
    if start > 0:
        snip = "…" + snip
    if end < len(flat):
        snip = snip + "…"
    return snip


def _like_search(tasks, query, limit):
    """LIKE-style ranked search over in-memory task dicts — the transparent
    fallback when FTS5 is unavailable (or an FTS query fails). AND semantics: every
    term must appear somewhere in the task's text blob; rank by total term hits,
    then most-recent activity. Returns [{"id", "snippet", "score"}, …]."""
    terms = _search_terms(query)
    if not terms:
        return []
    scored = []
    for task in tasks:
        text = task_search_text(task)
        low = text.lower()
        if not all(term in low for term in terms):
            continue
        hits = sum(low.count(term) for term in terms)
        scored.append((hits, task.get("updated_ts") or 0, task.get("id"),
                       _like_snippet(text, terms)))
    scored.sort(key=lambda r: (-r[0], -r[1]))
    return [{"id": tid, "snippet": snip, "score": float(h)}
            for h, _, tid, snip in scored[:limit]]


# FTS5 feature detection, cached process-wide: sqlite3 is stdlib but SOME builds
# omit the FTS5 module. Detected once with a throwaway in-memory table; tests may
# force the fallback by setting this to False.
_fts5_supported = None


def _fts5_available():
    if globals().get("_fts5_supported") is not None:
        return _fts5_supported
    ok = False
    if sqlite3 is not None:
        try:
            c = sqlite3.connect(":memory:")
            c.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
            c.close()
            ok = True
        except Exception:
            ok = False
    globals()["_fts5_supported"] = ok
    return ok


def _fts_match_query(query):
    """A safe FTS5 MATCH expression from free text: each alphanumeric token as a
    PREFIX term (`tok*`), AND-combined (space = implicit AND). Prefix matching lets
    'auth' find 'authentication'; sanitising to alnum tokens means no user input can
    reach FTS5 as an operator. Empty when there's nothing to match."""
    toks = _search_terms(query)
    return " ".join(t + "*" for t in toks)


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
        sessions     TEXT,
        session_meta TEXT,
        log          TEXT,
        rev          INTEGER NOT NULL DEFAULT 0,
        data         TEXT NOT NULL
    );
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

    -- WS1 usage ledger. Both tables are additive (CREATE IF NOT EXISTS runs on
    -- every connect, so an existing tasks.db grows them on next open — no
    -- migration). `session_usage` is one row per session transcript: the
    -- incremental-scan bookmark (scanned_size/mtime) plus the per-model token +
    -- derived-cost roll-ups. `models`/`sidechain`/`phases` are JSON blobs (the
    -- typed columns exist only to index/attribute). `prompts` is keyed on the
    -- transcript line uuid so a re-scan is idempotent.
    CREATE TABLE IF NOT EXISTS session_usage (
        session_id    TEXT PRIMARY KEY,
        task_id       TEXT,
        path          TEXT,
        cwd           TEXT,
        entrypoint    TEXT,
        role          TEXT,
        label         TEXT,
        scanned_size  INTEGER NOT NULL DEFAULT 0,
        scanned_mtime REAL NOT NULL DEFAULT 0,
        first_ts      REAL,
        last_ts       REAL,
        models        TEXT NOT NULL DEFAULT '{}',
        sidechain     TEXT NOT NULL DEFAULT '{}',
        phases        TEXT NOT NULL DEFAULT '{}',
        source        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_session_usage_task ON session_usage(task_id);

    CREATE TABLE IF NOT EXISTS prompts (
        uuid       TEXT PRIMARY KEY,
        session_id TEXT,
        task_id    TEXT,
        ts         REAL,
        kind       TEXT,
        text       TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_prompts_task ON prompts(task_id, ts);
    """

    # Full-text search index (FTS5). A STANDALONE fts5 table — not external-content
    # — because contentless/external tables can't return `snippet()` text, which the
    # 3-tier search output needs. `task_id` is stored UNINDEXED purely to map a hit
    # back to its task; `content` is the searchable blob (task_search_text). Kept in
    # sync in the write path (save_task/delete_task) rather than via triggers, since
    # the blob is derived from the JSON `data`, not from the typed columns a trigger
    # could see. unicode61 (no stemmer) so prefix queries (`auth*`) stay predictable.
    _FTS_TABLE = "tasks_fts"
    _FTS_VERSION = 1        # PRAGMA user_version once the FTS index exists + is backfilled

    # UNIQUE index on tasks(seq). Created by _ensure_seq_unique (which also dedupes
    # any legacy duplicates first), NOT by _SCHEMA — an existing DB with duplicate
    # seqs would make a plain `CREATE UNIQUE INDEX` in the schema script fail.
    _SEQ_UNIQUE_INDEX = "idx_tasks_seq_unique"

    # PRAGMA application_id once the A-1 task-metadata backfill has run. A slot
    # SEPARATE from user_version (which FTS owns) so the two migrations don't clash.
    _META_VERSION = 1

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
        """Open the connection + ensure schema. Uses an existing tasks.db or
        creates a fresh empty one (new install) — there is NO migration of any
        prior store, ever."""
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
        self._ensure_usage_source(conn)   # additive migration for pre-WS7b DBs
        self._ensure_rev(conn)            # additive: rev column for optimistic locking
        self._ensure_task_meta(conn)      # additive: backfill uuid + closed_ts
        self._ensure_seq_unique(conn)     # dedupe + enforce UNIQUE(seq)
        self._conn = conn          # set before _ensure_fts so save_task/set_link reuse it
        self._ensure_fts(conn)     # build + backfill the FTS index (before any save_task)
        return conn

    def _ensure_usage_source(self, conn):
        """Add the WS7b `source` column to an existing session_usage table (a fresh
        DB gets it from _SCHEMA). Idempotent + fully defensive — a DB that predates
        the column must keep working. Distinguishes `costbar-import` rows from scans."""
        try:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(session_usage)")]
            if "source" not in cols:
                conn.execute("ALTER TABLE session_usage ADD COLUMN source TEXT")
                conn.commit()
        except Exception:
            pass

    def _ensure_rev(self, conn):
        """Add the `rev` optimistic-lock column to an existing tasks table (a fresh
        DB gets it from _SCHEMA). Idempotent + defensive — a pre-rev DB must keep
        working; existing rows default to rev 0."""
        try:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)")]
            if "rev" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
                conn.commit()
        except Exception:
            pass

    def _ensure_task_meta(self, conn):
        """One-time additive backfill (A-1). Stamp `uuid` = the row's id on every
        task missing it — the id has always been a uuid4, so uuid == id — and
        `closed_ts` = the task's updated_ts on already-closed tasks missing it.
        Both live in the JSON `data` blob (neither is a query/sort key, so no typed
        column), so they round-trip through load/save for free once backfilled.

        Versioned via PRAGMA application_id (a slot separate from FTS's
        user_version) so it runs exactly once; idempotent + fully defensive — a
        pre-A1 DB must keep working and any failure leaves the prior state."""
        try:
            if conn.execute("PRAGMA application_id").fetchone()[0] >= self._META_VERSION:
                return
        except Exception:
            return
        try:
            for r in conn.execute("SELECT id, data FROM tasks").fetchall():
                try:
                    task = json.loads(r["data"])
                except Exception:
                    continue
                changed = False
                if not task.get("uuid"):
                    task["uuid"] = r["id"]
                    changed = True
                if task.get("status") == "closed" and task.get("closed_ts") is None:
                    task["closed_ts"] = task.get("updated_ts")
                    changed = True
                if changed:
                    conn.execute("UPDATE tasks SET data=? WHERE id=?",
                                 (json.dumps(task), r["id"]))
            conn.execute("PRAGMA application_id=%d" % self._META_VERSION)
            conn.commit()
        except Exception:
            pass

    def _ensure_seq_unique(self, conn):
        """One-time additive migration enforcing UNIQUE(seq). First dedupe any
        existing duplicate seqs — per group the EARLIEST-created task keeps its seq
        and the rest are reassigned MAX(seq)+1, +2, … in created order (globally
        ascending, so no new collisions) — then swap the legacy non-unique
        idx_tasks_seq for a UNIQUE index. Idempotent (guarded by the unique index's
        presence) and defensive: any failure rolls back and leaves the prior state.

        Deterministic: duplicate groups are processed by seq value, and within a
        group by (created_ts, id), so a given fixture always renumbers identically.
        NULL seqs are left alone (SQLite treats each NULL as distinct)."""
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                            (self._SEQ_UNIQUE_INDEX,)).fetchone():
                return
        except Exception:
            return
        prev = conn.isolation_level
        conn.isolation_level = None                 # manual BEGIN IMMEDIATE / COMMIT
        try:
            conn.execute("BEGIN IMMEDIATE")
            dupes = [r["seq"] for r in conn.execute(
                "SELECT seq FROM tasks WHERE seq IS NOT NULL "
                "GROUP BY seq HAVING COUNT(*) > 1 ORDER BY seq")]
            if dupes:
                nxt = conn.execute("SELECT COALESCE(MAX(seq),0) FROM tasks").fetchone()[0] + 1
                for s in dupes:
                    rows = conn.execute(
                        "SELECT id, data FROM tasks WHERE seq=? ORDER BY created_ts ASC, id ASC",
                        (s,)).fetchall()
                    for r in rows[1:]:              # keep the earliest; renumber the rest
                        task = json.loads(r["data"])
                        task["seq"] = nxt
                        conn.execute("UPDATE tasks SET seq=?, data=? WHERE id=?",
                                     (nxt, json.dumps(task), r["id"]))
                        nxt += 1
            conn.execute("DROP INDEX IF EXISTS idx_tasks_seq")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS %s ON tasks(seq)"
                         % self._SEQ_UNIQUE_INDEX)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        finally:
            conn.isolation_level = prev

    def ensure(self):
        self._connect()

    # -- tasks --
    def load_task(self, task_id):
        conn = self._connect()
        row = conn.execute("SELECT data, rev FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        task = json.loads(row["data"])
        task[REV_FIELD] = row["rev"]     # transient; stripped on write + at export
        return task

    def _row_values(self, task):
        """The positional column values for an INSERT/UPDATE of `task`. The `data`
        blob is the task dict MINUS the transient REV_FIELD (rev lives only in its
        own column, never round-tripped through JSON)."""
        return (
            task["id"], task.get("seq"), task.get("title"), task.get("summary"),
            task.get("status"), task.get("color"), task.get("effort"),
            task.get("created_ts"), task.get("updated_ts"),
            json.dumps(task.get("sessions", [])),
            json.dumps(task.get("session_meta", {})),
            json.dumps(task.get("log", [])),
            json.dumps(strip_rev(task)),
        )

    def _write_row(self, conn, task):
        """Core UNVERSIONED upsert (INSERT ... ON CONFLICT(id) DO UPDATE) + FTS sync,
        shared by save_task and create_with_seq. Last-writer-wins, but every update
        bumps `rev` (a fresh insert starts at 0). The caller owns the transaction
        boundary and the commit."""
        conn.execute(
            """INSERT INTO tasks
                 (id, seq, title, summary, status, color, effort,
                  created_ts, updated_ts, sessions, session_meta, log, data)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 seq=excluded.seq, title=excluded.title, summary=excluded.summary,
                 status=excluded.status, color=excluded.color, effort=excluded.effort,
                 created_ts=excluded.created_ts, updated_ts=excluded.updated_ts,
                 sessions=excluded.sessions,
                 session_meta=excluded.session_meta, log=excluded.log,
                 data=excluded.data, rev=rev+1""",
            self._row_values(task),
        )
        self._sync_fts(conn, task)     # keep the search index in step, same transaction

    def save_task(self, task, expected_rev=None):
        """Persist `task`. Unversioned (expected_rev=None): last-writer-wins upsert
        that bumps rev. Versioned: a conditional UPDATE guarded by rev — if the row's
        rev != expected_rev (a concurrent writer won), nothing is written and
        RevConflict is raised. Use store.mutate() for the reload-and-retry loop."""
        conn = self._connect()
        if expected_rev is None:
            self._write_row(conn, task)
            conn.commit()
            return
        cur = conn.execute(
            """UPDATE tasks SET
                 seq=?, title=?, summary=?, status=?, color=?, effort=?,
                 created_ts=?, updated_ts=?, sessions=?, session_meta=?, log=?,
                 data=?, rev=rev+1
               WHERE id=? AND rev=?""",
            self._row_values(task)[1:] + (task["id"], expected_rev),
        )
        if cur.rowcount == 0:
            conn.rollback()
            raise RevConflict(
                "task %s changed under us (expected rev %s)" % (task.get("id"), expected_rev))
        self._sync_fts(conn, task)
        conn.commit()

    def mutate(self, task_id, mutator_fn, retries=5):
        """Optimistic read-modify-write: load the task, apply `mutator_fn(task)`
        (which mutates the dict in place), then save guarded by the loaded rev. On a
        RevConflict, reload the FRESH task and re-run the mutator, up to `retries`
        times. Returns the saved task, or None if the task doesn't exist.

        `mutator_fn` MUST be PURE: it may only transform the task dict it is given,
        with no external side effects (no I/O, no saving, no reads of state that
        another writer could change) — because a conflict re-runs it on reloaded
        state. This is the mandatory path for any concurrent mutation (see
        CONTRIBUTING)."""
        for attempt in range(retries + 1):
            task = self.load_task(task_id)
            if task is None:
                return None
            expected = task.get(REV_FIELD, 0)
            mutator_fn(task)
            try:
                self.save_task(task, expected_rev=expected)
                task[REV_FIELD] = expected + 1     # keep the returned dict consistent
                return task
            except RevConflict:
                if attempt >= retries:
                    raise

    def create_with_seq(self, task):
        """Transactionally allocate the next seq and INSERT `task` under it. BEGIN
        IMMEDIATE serialises concurrent creators so they can't read the same
        MAX(seq); the UNIQUE(seq) index is the hard backstop, and a racing writer
        that still collides raises sqlite3.IntegrityError — retried up to 3x, each
        time re-reading MAX(seq). Returns `task` with its assigned `seq`."""
        conn = self._connect()
        prev = conn.isolation_level
        conn.isolation_level = None                 # manual BEGIN IMMEDIATE / COMMIT
        try:
            for attempt in range(3):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    task["seq"] = conn.execute(
                        "SELECT COALESCE(MAX(seq),0)+1 FROM tasks").fetchone()[0]
                    self._write_row(conn, task)
                    conn.execute("COMMIT")
                    return task
                except sqlite3.IntegrityError:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    if attempt == 2:
                        raise
        finally:
            conn.isolation_level = prev
        return task

    def delete_task(self, task_id):
        conn = self._connect()
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        if _fts5_available():
            try:
                conn.execute("DELETE FROM %s WHERE task_id=?" % self._FTS_TABLE, (task_id,))
            except Exception:
                pass
        conn.commit()

    def all_tasks(self):
        conn = self._connect()
        return [json.loads(r["data"]) for r in conn.execute("SELECT data FROM tasks")]

    # -- full-text search index --
    def _ensure_fts(self, conn):
        """Create the FTS5 index and backfill it once (schema-version bump). A no-op
        that leaves the LIKE fallback in force when the host sqlite3 lacks FTS5. Fully
        defensive — a search index that can't be built must NEVER break tracking."""
        if not _fts5_available():
            return
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS %s "
                "USING fts5(task_id UNINDEXED, content, tokenize='unicode61')"
                % self._FTS_TABLE)
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            if ver < self._FTS_VERSION:
                # Backfill from whatever tasks already exist (the upgrade path for a
                # populated pre-FTS DB). On a fresh/empty DB this is a no-op and the
                # JSON import + normal save_task keep the index synced from here on.
                conn.execute("DELETE FROM %s" % self._FTS_TABLE)
                for r in conn.execute("SELECT data FROM tasks"):
                    try:
                        task = json.loads(r["data"])
                    except Exception:
                        continue
                    conn.execute(
                        "INSERT INTO %s (task_id, content) VALUES (?, ?)" % self._FTS_TABLE,
                        (task.get("id"), task_search_text(task)))
                conn.execute("PRAGMA user_version=%d" % self._FTS_VERSION)
            conn.commit()
        except Exception:
            pass

    def _sync_fts(self, conn, task):
        """Replace one task's row in the FTS index (delete-then-insert). Runs inside
        the save_task transaction so the index commits atomically with the task.
        Guarded — a search-index failure is swallowed so a write always succeeds."""
        if not _fts5_available():
            return
        try:
            conn.execute("DELETE FROM %s WHERE task_id=?" % self._FTS_TABLE, (task["id"],))
            conn.execute(
                "INSERT INTO %s (task_id, content) VALUES (?, ?)" % self._FTS_TABLE,
                (task["id"], task_search_text(task)))
        except Exception:
            pass

    def search(self, query, limit=50):
        """Ranked search over the store. Uses the FTS5 index (bm25 relevance) when
        available, transparently degrading to the LIKE scan on no-FTS builds OR when
        an individual FTS query errors. Returns [{"id", "snippet", "score"}, …]."""
        conn = self._connect()
        if _fts5_available():
            rows = self._search_fts(conn, query, limit)
            if rows is not None:
                return rows
        return _like_search(self.all_tasks(), query, limit)

    def _search_fts(self, conn, query, limit):
        """FTS5 branch: bm25-ranked hits with a match-context snippet. Returns the
        hit list, or None to tell search() to fall back to LIKE (empty query, or an
        FTS engine error — bm25 lower = more relevant, so ORDER BY score ASC)."""
        match = _fts_match_query(query)
        if not match:
            return None
        try:
            cur = conn.execute(
                "SELECT task_id, snippet(%s, 1, '', '', '…', 12) AS snip, "
                "bm25(%s) AS score FROM %s WHERE %s MATCH ? "
                "ORDER BY score LIMIT ?"
                % (self._FTS_TABLE, self._FTS_TABLE, self._FTS_TABLE, self._FTS_TABLE),
                (match, limit))
            return [{"id": r["task_id"], "snippet": (r["snip"] or "").strip(),
                     "score": r["score"]} for r in cur.fetchall()]
        except Exception:
            return None

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

    # -- usage ledger (WS1) --
    #
    # The JSON blob columns (models/sidechain/phases) are parsed on read and
    # dumped on write so callers deal in plain dicts; the typed columns are for
    # indexing/attribution only. lib/usage.py owns the semantics — this layer is a
    # thin, defensive CRUD over the two tables.
    _USAGE_COLS = ("session_id", "task_id", "path", "cwd", "entrypoint", "role",
                   "label", "scanned_size", "scanned_mtime", "first_ts", "last_ts",
                   "models", "sidechain", "phases", "source")

    @staticmethod
    def _row_to_usage(row):
        """A session_usage sqlite Row → a plain dict with the three JSON blob
        columns parsed back into dicts (defaulting to {} on any decode error)."""
        d = dict(row)
        for k in ("models", "sidechain", "phases"):
            try:
                d[k] = json.loads(d.get(k) or "{}")
            except (ValueError, TypeError):
                d[k] = {}
        return d

    def get_session_usage(self, session_id):
        conn = self._connect()
        row = conn.execute("SELECT * FROM session_usage WHERE session_id=?",
                           (session_id,)).fetchone()
        return self._row_to_usage(row) if row else None

    def upsert_session_usage(self, row):
        """Insert/replace one session_usage row from a plain dict (models/sidechain/
        phases may be dicts — they're JSON-encoded here). Missing keys take their
        column defaults."""
        conn = self._connect()
        vals = {
            "session_id": row.get("session_id"),
            "task_id": row.get("task_id"),
            "path": row.get("path"),
            "cwd": row.get("cwd"),
            "entrypoint": row.get("entrypoint"),
            "role": row.get("role"),
            "label": row.get("label"),
            "scanned_size": int(row.get("scanned_size") or 0),
            "scanned_mtime": float(row.get("scanned_mtime") or 0),
            "first_ts": row.get("first_ts"),
            "last_ts": row.get("last_ts"),
            "models": json.dumps(row.get("models") or {}),
            "sidechain": json.dumps(row.get("sidechain") or {}),
            "phases": json.dumps(row.get("phases") or {}),
            "source": row.get("source"),
        }
        cols = self._USAGE_COLS
        conn.execute(
            "INSERT INTO session_usage (%s) VALUES (%s) "
            "ON CONFLICT(session_id) DO UPDATE SET %s"
            % (", ".join(cols), ", ".join("?" for _ in cols),
               ", ".join("%s=excluded.%s" % (c, c) for c in cols if c != "session_id")),
            tuple(vals[c] for c in cols),
        )
        conn.commit()

    def session_usage_for_task(self, task_id):
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM session_usage WHERE task_id=? ORDER BY first_ts",
            (task_id,)).fetchall()
        return [self._row_to_usage(r) for r in rows]

    def all_session_usage(self):
        """Every session_usage row (no task filter) — the whole-ledger roll-up the
        cost HUD's Week / Total rows aggregate over. Ordered by first activity."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM session_usage ORDER BY first_ts").fetchall()
        return [self._row_to_usage(r) for r in rows]

    def upsert_prompt(self, row):
        """Idempotent prompt upsert keyed on the transcript line uuid. Re-scanning
        the same line refreshes its attribution/text rather than duplicating it."""
        uuid_ = row.get("uuid")
        if not uuid_:
            return
        conn = self._connect()
        conn.execute(
            """INSERT INTO prompts (uuid, session_id, task_id, ts, kind, text)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(uuid) DO UPDATE SET
                 session_id=excluded.session_id, task_id=excluded.task_id,
                 ts=excluded.ts, kind=excluded.kind, text=excluded.text""",
            (uuid_, row.get("session_id"), row.get("task_id"),
             row.get("ts"), row.get("kind"), row.get("text")),
        )
        conn.commit()

    def prompts_for_task(self, task_id, limit=None):
        conn = self._connect()
        sql = "SELECT * FROM prompts WHERE task_id=? ORDER BY ts"
        args = [task_id]
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def prompts_in_window(self, start_ts, end_ts):
        """Every prompt whose ts lands in [start_ts, end_ts) REGARDLESS of task
        attribution (so unattached-session prompts, which carry a NULL task_id and
        can't be reached via prompts_for_task, are included). Read-only; the weekly
        recap uses it for privacy-safe per-session counts + feature detection."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM prompts WHERE ts IS NOT NULL AND ts>=? AND ts<? ORDER BY ts",
            (float(start_ts), float(end_ts))).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------ backend factory ---

# Single-slot cache: keep one live backend and its connection per resolved store
# dir. A mismatch closes the old backend and caches a fresh one.
_cache = {"key": None, "backend": None}


def get_backend(store_dir):
    """The SqliteBackend for `store_dir`. No migration, ever — it uses an existing
    tasks.db or creates a fresh empty one. Raises RuntimeError if sqlite3 (a hard,
    stdlib requirement) is unavailable rather than silently degrading."""
    if sqlite3 is None:
        raise RuntimeError("task-station requires Python built with sqlite3")
    key = os.path.abspath(store_dir)
    if _cache["key"] == key and _cache["backend"] is not None:
        return _cache["backend"]
    if _cache["backend"] is not None:
        try:
            _cache["backend"].close()
        except Exception:
            pass
    backend = SqliteBackend(store_dir)
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
