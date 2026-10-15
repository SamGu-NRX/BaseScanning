"""Synthetic capture-admission reference; upstream assessment is NOT verified here."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


class Conflict(ValueError):
    pass


def valid_id(value):
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class ReturnedStill:
    frame_id: str
    timestamp: float
    epoch: str
    tracking: str
    usable: bool
    # Pairs are (requirement ID, exact assessed revision), not inferred from request.
    supports: tuple[tuple[str, int], ...]

    def encoded(self) -> str:
        if not valid_id(self.frame_id) or not valid_id(self.epoch):
            raise ValueError("Returned-frame identity/epoch must be nonempty strings")
        if (
            type(self.timestamp) not in (int, float)
            or not math.isfinite(self.timestamp)
            or self.timestamp < 0
        ):
            raise ValueError("Returned-frame identity/epoch/finite timestamp required")
        if not isinstance(self.tracking, str) or self.tracking not in {
            "normal",
            "limited",
            "unavailable",
        }:
            raise ValueError("Unknown tracking state")
        if type(self.usable) is not bool:
            raise ValueError("Assessment must explicitly state usable")
        if not isinstance(self.supports, tuple) or any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not valid_id(pair[0])
            or type(pair[1]) is not int
            or pair[1] < 1
            for pair in self.supports
        ):
            raise ValueError("Invalid assessed requirement revision")
        d = asdict(self)
        d["timestamp"] = float(self.timestamp)
        d["supports"] = sorted(set(self.supports))
        return json.dumps(d, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Ledger:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS epochs(id TEXT PRIMARY KEY);
        INSERT OR IGNORE INTO epochs SELECT value FROM settings WHERE key='epoch';
        CREATE TABLE IF NOT EXISTS requirements(
          id TEXT PRIMARY KEY, revision INTEGER NOT NULL, kind TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(
          id TEXT PRIMARY KEY, target TEXT NOT NULL, revision INTEGER NOT NULL,
          preview_frame TEXT NOT NULL, epoch TEXT NOT NULL, status TEXT NOT NULL,
          still_metadata TEXT, asset_hash TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_capture
          ON requests(status) WHERE status='pending';
        CREATE TABLE IF NOT EXISTS assets(
          hash TEXT PRIMARY KEY, payload BLOB NOT NULL, server_accepted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS support(
          capture_id TEXT NOT NULL REFERENCES requests(id),
          requirement_id TEXT NOT NULL, revision INTEGER NOT NULL,
          PRIMARY KEY(capture_id, requirement_id, revision));
        """)

    def close(self):
        self.db.close()

    def _transaction(self, operation):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = operation()
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _read_view(self, operation):
        if self.db.in_transaction:
            raise Conflict("Progress is unavailable inside an uncommitted write")
        self.db.execute("BEGIN")
        try:
            result = operation()
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def set_epoch(self, epoch: str):
        """Start a fresh coordinate epoch. Re-registration into old maps is not modeled."""
        if not valid_id(epoch):
            raise ValueError("Epoch is required")

        def op():
            current = self.db.execute(
                "SELECT value FROM settings WHERE key='epoch'"
            ).fetchone()
            if current and current[0] == epoch:
                return
            if self.db.execute("SELECT 1 FROM epochs WHERE id=?", (epoch,)).fetchone():
                raise Conflict(
                    "Epoch ID cannot be reused without explicit registration"
                )
            self.db.execute("INSERT INTO epochs VALUES(?)", (epoch,))
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES('epoch',?)", (epoch,)
            )

        self._transaction(op)

    def require(self, key: str, revision: int, kind: str):
        if (
            not valid_id(key)
            or type(revision) is not int
            or revision < 1
            or not isinstance(kind, str)
            or kind not in {"detail", "spatial"}
        ):
            raise ValueError("Invalid requirement")

        def op():
            old = self.db.execute(
                "SELECT revision,kind FROM requirements WHERE id=?", (key,)
            ).fetchone()
            if old and (revision < old[0] or (revision == old[0] and kind != old[1])):
                raise Conflict("A changed requirement needs a newer revision")
            self.db.execute(
                "INSERT OR REPLACE INTO requirements VALUES(?,?,?)",
                (key, revision, kind),
            )

        self._transaction(op)

    def request(self, capture_id: str, target: str, preview_frame: str):
        if not all(valid_id(x) for x in (capture_id, target, preview_frame)):
            raise ValueError("Capture and preview IDs required")

        def op():
            task = self.db.execute(
                "SELECT revision FROM requirements WHERE id=?", (target,)
            ).fetchone()
            epoch = self.db.execute(
                "SELECT value FROM settings WHERE key='epoch'"
            ).fetchone()
            if not task or not epoch:
                raise ValueError("Current requirement and epoch required")
            identity = (target, task[0], preview_frame, epoch[0])
            old = self.db.execute(
                "SELECT target,revision,preview_frame,epoch FROM requests WHERE id=?",
                (capture_id,),
            ).fetchone()
            if old:
                if old != identity:
                    raise Conflict("Capture ID was reused for different intent")
                return "duplicate_request"
            self.db.execute(
                "INSERT INTO requests(id,target,revision,preview_frame,epoch,status) VALUES(?,?,?,?,?,'pending')",
                (capture_id, *identity),
            )
            return "requested"

        return self._transaction(op)

    def receive(
        self,
        capture_id: str,
        payload: bytes,
        still: ReturnedStill,
        before_commit: Callable[[], None] | None = None,
    ):
        """Fault hook is an experiment seam. Payload is opaque; no quality/OCR is inferred."""
        if not valid_id(capture_id) or not isinstance(still, ReturnedStill):
            raise ValueError("Capture ID and returned-still record required")
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("Nonempty returned bytes required")
        metadata = still.encoded()
        digest = hashlib.sha256(payload).hexdigest()

        def op():
            prior = self.db.execute(
                "SELECT status,still_metadata,asset_hash FROM requests WHERE id=?",
                (capture_id,),
            ).fetchone()
            if not prior:
                raise Conflict("Unknown capture callback")
            status, old_metadata, old_hash = prior
            if status in {"admitted", "rejected"}:
                if (old_metadata, old_hash) != (metadata, digest):
                    raise Conflict("Conflicting callback for completed capture")
                return "duplicate_callback"
            if status != "pending":
                raise Conflict(
                    "Native capture was interrupted; callback requires explicit recovery"
                )
            new_status = "admitted" if still.usable else "rejected"
            # Retain source bytes for either decision in this tiny audit prototype.
            self.db.execute(
                "INSERT OR IGNORE INTO assets(hash,payload) VALUES(?,?)",
                (digest, payload),
            )
            self.db.execute(
                "UPDATE requests SET status=?,still_metadata=?,asset_hash=? WHERE id=?",
                (new_status, metadata, digest, capture_id),
            )
            if still.usable:
                for key, revision in set(still.supports):
                    # An unrecognized assessment must not retroactively satisfy a
                    # requirement introduced later under the same identifier.
                    self.db.execute(
                        """INSERT INTO support
                      SELECT ?,id,revision FROM requirements WHERE id=? AND revision=?
                      AND (kind='detail' OR (kind='spatial' AND ?='normal'
                      AND ?=(SELECT value FROM settings WHERE key='epoch')))""",
                        (capture_id, key, revision, still.tracking, still.epoch),
                    )
            if before_commit:
                before_commit()
            return new_status

        return self._transaction(op)

    def recover_orphaned_requests(self):
        """Called only after the app establishes no corresponding native operations survive."""
        return self.db.execute(
            "UPDATE requests SET status='interrupted' WHERE status='pending'"
        ).rowcount

    def acknowledge(self, digest: str, byte_count: int):
        if not valid_id(digest):
            raise Conflict("Asset hash must be a string")

        def op():
            asset = self.db.execute(
                "SELECT length(payload) FROM assets WHERE hash=?", (digest,)
            ).fetchone()
            if type(byte_count) is not int or not asset or byte_count != asset[0]:
                raise Conflict("Server acceptance does not identify a committed asset")
            self.db.execute(
                "UPDATE assets SET server_accepted=1 WHERE hash=?", (digest,)
            )

        self._transaction(op)

    def progress(self):
        return self._read_view(self._progress)

    def _progress(self):
        epoch = self.db.execute(
            "SELECT value FROM settings WHERE key='epoch'"
        ).fetchone()
        result = {}
        for key, revision, kind in self.db.execute(
            "SELECT id,revision,kind FROM requirements ORDER BY id"
        ).fetchall():
            candidates = self.db.execute(
                """SELECT r.asset_hash,r.still_metadata FROM support s
              JOIN requests r ON r.id=s.capture_id JOIN assets a ON a.hash=r.asset_hash
              WHERE s.requirement_id=? AND s.revision=? AND r.status='admitted'""",
                (key, revision),
            ).fetchall()
            assets = set()
            for digest, encoded in candidates:
                frame = json.loads(encoded)
                if kind == "spatial" and (
                    not epoch
                    or frame["epoch"] != epoch[0]
                    or frame["tracking"] != "normal"
                ):
                    continue
                assets.add(digest)
            result[key] = {
                "revision": revision,
                "kind": kind,
                "captured": bool(assets),
                "unique_assets": len(assets),
                "spatial_epoch": epoch[0] if epoch and kind == "spatial" else None,
            }
        return result

    def snapshot(self):
        def read():
            progress = self._progress()
            epoch = self.db.execute(
                "SELECT value FROM settings WHERE key='epoch'"
            ).fetchone()
            return {
                "progress": progress,
                "epoch": epoch[0] if epoch else None,
                "requests": self.db.execute(
                    "SELECT id,status,preview_frame,still_metadata,asset_hash FROM requests ORDER BY id"
                ).fetchall(),
                "assets": self.db.execute(
                    "SELECT hash,length(payload),server_accepted FROM assets ORDER BY hash"
                ).fetchall(),
            }

        return self._read_view(read)
