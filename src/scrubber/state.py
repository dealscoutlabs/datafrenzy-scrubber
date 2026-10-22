"""Local SQLite state: the file ledger (for resume/idempotency), shard
checkpoints, the pseudonym table, harvested names, quarantine and the human
review queue. Lives in /state; never exported."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (
    rel TEXT PRIMARY KEY,           -- path relative to /input (or derived, e.g. a .pst folder)
    src TEXT NOT NULL,              -- absolute path to read from
    kind TEXT NOT NULL,
    size INTEGER, fingerprint TEXT, config_hash TEXT,
    status TEXT NOT NULL,           -- pending|processing|done|review|failed|quarantined|rejected
    out_rel TEXT,                   -- scrubbed relative output path
    reason TEXT, detail TEXT,
    harvested INTEGER DEFAULT 0,
    shards INTEGER DEFAULT 1,
    counts TEXT, records INTEGER, years TEXT, verify TEXT,
    parent TEXT,
    updated REAL
);
CREATE TABLE IF NOT EXISTS shards (
    rel TEXT, idx INTEGER, start INTEGER, stop INTEGER,
    status TEXT NOT NULL,           -- pending|processing|done|failed
    progress INTEGER DEFAULT 0,     -- records completed (checkpoint)
    out_offset INTEGER DEFAULT 0,   -- bytes of part file at checkpoint
    counts TEXT,                    -- JSON tally: counts, years, records, issues
    error TEXT,
    PRIMARY KEY (rel, idx)
);
CREATE TABLE IF NOT EXISTS pseudonyms (
    hkey TEXT PRIMARY KEY, etype TEXT NOT NULL, seq INTEGER NOT NULL,
    UNIQUE (etype, seq)
);
CREATE TABLE IF NOT EXISTS known_entities (
    norm TEXT NOT NULL, etype TEXT NOT NULL, surface TEXT NOT NULL, source TEXT,
    PRIMARY KEY (norm, etype, surface)
);
CREATE TABLE IF NOT EXISTS quarantine (
    item TEXT PRIMARY KEY, parent TEXT, reason TEXT, mime TEXT, size INTEGER, updated REAL
);
CREATE TABLE IF NOT EXISTS review (
    rel TEXT PRIMARY KEY, reasons TEXT, status TEXT NOT NULL,   -- pending|approved|rejected
    decided_by TEXT, decided_at REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS review_log (           -- every decision, kept for the audit report
    id INTEGER PRIMARY KEY, rel TEXT, out_rel TEXT, decision TEXT, decided_by TEXT, decided_at REAL, note TEXT,
    reasons TEXT
);
CREATE TABLE IF NOT EXISTS samples (              -- before/after text for the review screen (local only)
    rel TEXT, n INTEGER, original TEXT, scrubbed TEXT, PRIMARY KEY (rel, n)
);
CREATE TABLE IF NOT EXISTS misses (               -- identifiers a reviewer reported as missed (local only)
    id INTEGER PRIMARY KEY, rel TEXT, term TEXT, etype TEXT, note TEXT, reported_by TEXT, reported_at REAL
);
"""

MIGRATIONS = [("files", "messages", "INTEGER"), ("review", "details", "TEXT")]


class Store:
    """One connection per process; safe to construct again after fork()."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pid = None
        self._conn: sqlite3.Connection | None = None
        self.conn.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None or self._pid != os.getpid():
            self._conn = sqlite3.connect(self.path, timeout=120, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._pid = os.getpid()
        return self._conn

    @contextmanager
    def tx(self):
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    # ---- meta
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    # ---- pseudonyms
    def pseudonym_seq(self, hkey: str, etype: str) -> int:
        row = self.conn.execute("SELECT seq FROM pseudonyms WHERE hkey=?", (hkey,)).fetchone()
        if row:
            return row[0]
        with self.tx() as c:
            row = c.execute("SELECT seq FROM pseudonyms WHERE hkey=?", (hkey,)).fetchone()
            if row:
                return row[0]
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM pseudonyms WHERE etype=?", (etype,)).fetchone()[0]
            c.execute("INSERT INTO pseudonyms VALUES (?,?,?)", (hkey, etype, seq))
            return seq

    # ---- known entities (harvested names)
    def add_known(self, rows: list[tuple[str, str, str, str]]) -> None:
        if rows:
            with self.tx() as c:
                c.executemany("INSERT OR IGNORE INTO known_entities VALUES (?,?,?,?)", rows)

    def known_entities(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT norm, etype, surface FROM known_entities").fetchall()

    # ---- files
    def upsert_file(self, rel: str, src: str, kind: str, size: int, fingerprint: str, chash: str,
                    parent: str | None = None) -> str:
        """Register a file. Returns its status; resets it to pending if it changed."""
        row = self.conn.execute("SELECT fingerprint, config_hash, status FROM files WHERE rel=?", (rel,)).fetchone()
        now = time.time()
        if row is None:
            self.conn.execute(
                "INSERT INTO files (rel, src, kind, size, fingerprint, config_hash, status, parent, updated)"
                " VALUES (?,?,?,?,?,?,?,?,?)", (rel, src, kind, size, fingerprint, chash, "pending", parent, now))
            return "pending"
        if row["fingerprint"] != fingerprint:
            # Content changed: start over, including name harvesting.
            self.reset_file(rel, src=src, kind=kind, size=size, fingerprint=fingerprint, chash=chash, harvested=0)
            return "pending"
        if row["config_hash"] != chash and row["status"] != "rejected":
            self.reset_file(rel, src=src, kind=kind, size=size, fingerprint=fingerprint, chash=chash)
            return "pending"
        return row["status"]

    def reset_file(self, rel: str, **fields) -> None:
        fields.setdefault("status", "pending")
        fields.update(reason=None, detail=None, counts=None, records=None, years=None, verify=None,
                      updated=time.time())
        cols = {"chash": "config_hash"}
        sets = ", ".join(f"{cols.get(k, k)}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE files SET {sets} WHERE rel=?", (*fields.values(), rel))
            c.execute("DELETE FROM shards WHERE rel=?", (rel,))
            c.execute("DELETE FROM quarantine WHERE parent=?", (rel,))
            c.execute("DELETE FROM review WHERE rel=? AND status='pending'", (rel,))

    def update_file(self, rel: str, **fields) -> None:
        fields["updated"] = time.time()
        for k in ("counts", "years", "verify", "detail"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k], sort_keys=True)
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE files SET {sets} WHERE rel=?", (*fields.values(), rel))

    def file(self, rel: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM files WHERE rel=?", (rel,)).fetchone()

    def files(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.conn.execute("SELECT * FROM files WHERE status=? ORDER BY rel", (status,)).fetchall()
        return self.conn.execute("SELECT * FROM files ORDER BY rel").fetchall()

    # ---- shards
    def ensure_shards(self, rel: str, bounds: list[tuple[int, int]]) -> None:
        with self.tx() as c:
            existing = c.execute("SELECT COUNT(*) FROM shards WHERE rel=?", (rel,)).fetchone()[0]
            if existing != len(bounds):
                c.execute("DELETE FROM shards WHERE rel=?", (rel,))
                c.executemany("INSERT INTO shards (rel, idx, start, stop, status) VALUES (?,?,?,?, 'pending')",
                              [(rel, i, a, b) for i, (a, b) in enumerate(bounds)])
            c.execute("UPDATE files SET shards=? WHERE rel=?", (len(bounds), rel))

    def shards(self, rel: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM shards WHERE rel=? ORDER BY idx", (rel,)).fetchall()

    def checkpoint(self, rel: str, idx: int, progress: int, out_offset: int, tally: dict) -> None:
        self.conn.execute("UPDATE shards SET progress=?, out_offset=?, counts=? WHERE rel=? AND idx=?",
                          (progress, out_offset, json.dumps(tally, sort_keys=True), rel, idx))

    def finish_shard(self, rel: str, idx: int, status: str, **fields) -> None:
        for k in ("counts",):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k], sort_keys=True)
        fields["status"] = status
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE shards SET {sets} WHERE rel=? AND idx=?", (*fields.values(), rel, idx))

    def reset_interrupted(self) -> None:
        """After a crash: shards that were mid-flight resume from their checkpoint."""
        self.conn.execute("UPDATE shards SET status='pending' WHERE status='processing'")
        self.conn.execute("UPDATE files SET status='pending' WHERE status='processing'")

    # ---- quarantine / review
    def quarantine(self, item: str, parent: str | None, reason: str, mime: str | None = None,
                   size: int | None = None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO quarantine VALUES (?,?,?,?,?,?)",
                          (item, parent, reason, mime, size, time.time()))

    def quarantined(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM quarantine ORDER BY item").fetchall()

    def add_review(self, rel: str, reasons: list[str], details: list[dict] | None = None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO review (rel, reasons, status, details) VALUES (?,?, 'pending', ?)",
                          (rel, json.dumps(reasons), json.dumps(details or [])))

    def decide_review(self, rel: str, status: str, by: str, note: str = "") -> None:
        now = time.time()
        with self.tx() as c:
            c.execute("UPDATE review SET status=?, decided_by=?, decided_at=?, note=? WHERE rel=?",
                      (status, by, now, note, rel))
            row = c.execute("SELECT r.reasons, f.out_rel FROM review r JOIN files f ON f.rel = r.rel WHERE r.rel=?",
                            (rel,)).fetchone()
            c.execute("INSERT INTO review_log (rel, out_rel, decision, decided_by, decided_at, note, reasons)"
                      " VALUES (?,?,?,?,?,?,?)", (rel, row["out_rel"] if row else None, status, by, now, note,
                                                   row["reasons"] if row else "[]"))

    def review_log(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM review_log ORDER BY decided_at").fetchall()

    # ---- samples / misses (local review screen only)
    def set_samples(self, rel: str, samples: list[dict]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM samples WHERE rel=?", (rel,))
            c.executemany("INSERT INTO samples VALUES (?,?,?,?)",
                          [(rel, i, s["original"], s["scrubbed"]) for i, s in enumerate(samples)])

    def samples(self, rel: str | None = None, limit: int = 20, random_order: bool = False) -> list[sqlite3.Row]:
        order = "RANDOM()" if random_order else "rel, n"
        if rel:
            return self.conn.execute(f"SELECT * FROM samples WHERE rel=? ORDER BY {order} LIMIT ?", (rel, limit)).fetchall()
        return self.conn.execute(f"SELECT s.* FROM samples s JOIN files f ON f.rel = s.rel "
                                 f"ORDER BY {order} LIMIT ?", (limit,)).fetchall()

    def add_miss(self, rel: str | None, term: str, etype: str, note: str, by: str) -> None:
        self.conn.execute("INSERT INTO misses (rel, term, etype, note, reported_by, reported_at) VALUES (?,?,?,?,?,?)",
                          (rel, term, etype, note, by, time.time()))

    def misses(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM misses ORDER BY reported_at").fetchall()

    def reviews(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.conn.execute("SELECT * FROM review WHERE status=? ORDER BY rel", (status,)).fetchall()
        return self.conn.execute("SELECT * FROM review ORDER BY rel").fetchall()
