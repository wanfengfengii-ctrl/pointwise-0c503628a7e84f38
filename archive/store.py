import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from os import PathLike


class CaptureNotFound(LookupError):
    """The requested capture is absent from the archive."""


class CaptureAlreadyExists(ValueError):
    """A published capture already owns the requested identity."""


class ArchiveCommitError(RuntimeError):
    """The projection and evidence could not be committed together."""


@dataclass(frozen=True, slots=True)
class StoredCapture:
    projection: bytes
    evidence: bytes | None
    digest: str | None


class ArchiveStore:
    def __init__(self, path: str | PathLike[str]):
        self._connection = sqlite3.connect(path, isolation_level=None)
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self.initialize()
        except BaseException:
            self.close()
            raise

    def initialize(self) -> None:
        with self._connection:
            self._connection.execute("BEGIN")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS captures (
                    capture_id TEXT PRIMARY KEY NOT NULL,
                    projection BLOB NOT NULL,
                    signed INTEGER NOT NULL DEFAULT 0
                ) WITHOUT ROWID
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS capture_evidence (
                    capture_id TEXT PRIMARY KEY NOT NULL REFERENCES captures(capture_id),
                    payload BLOB NOT NULL,
                    digest TEXT NOT NULL
                ) WITHOUT ROWID
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS site_receipts (
                    capture_id TEXT NOT NULL,
                    station_id TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    digest TEXT NOT NULL,
                    PRIMARY KEY (capture_id, station_id),
                    FOREIGN KEY (capture_id) REFERENCES captures(capture_id)
                ) WITHOUT ROWID
                """
            )
            # Reopen archives created before single-station handover: add the signing marker.
            columns = {
                row[1]
                for row in self._connection.execute("PRAGMA table_info(captures)").fetchall()
            }
            if "signed" not in columns:
                self._connection.execute("ALTER TABLE captures ADD COLUMN signed INTEGER NOT NULL DEFAULT 0")

    def commit_capture(
        self,
        capture_id: str,
        projection: bytes,
        evidence: bytes,
        digest: str,
        site_receipts: Iterable[tuple[str, bytes, str]] = (),
    ) -> None:
        receipts = tuple(site_receipts)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            result = self._connection.execute(
                "INSERT INTO captures (capture_id, projection, signed) VALUES (?, ?, ?) "
                "ON CONFLICT (capture_id) DO NOTHING",
                (capture_id, projection, 1 if receipts else 0),
            )
            if result.rowcount != 1:
                raise CaptureAlreadyExists(capture_id)
            self._connection.execute(
                "INSERT INTO capture_evidence (capture_id, payload, digest) VALUES (?, ?, ?)",
                (capture_id, evidence, digest),
            )
            for station_id, payload, receipt_digest in receipts:
                self._connection.execute(
                    "INSERT INTO site_receipts (capture_id, station_id, payload, digest) VALUES (?, ?, ?, ?)",
                    (capture_id, station_id, payload, receipt_digest),
                )
            self._connection.commit()
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise ArchiveCommitError(f"publication failed for capture {capture_id!r}") from exc
        except BaseException:
            self._connection.rollback()
            raise

    def read_projection(self, capture_id: str) -> bytes:
        row = self._connection.execute(
            "SELECT projection FROM captures WHERE capture_id = ?", (capture_id,)
        ).fetchone()
        if row is None:
            raise CaptureNotFound(capture_id)
        return row[0]

    def read_capture(self, capture_id: str) -> StoredCapture:
        row = self._connection.execute(
            """
            SELECT captures.projection, capture_evidence.payload, capture_evidence.digest
            FROM captures LEFT JOIN capture_evidence USING (capture_id)
            WHERE captures.capture_id = ?
            """,
            (capture_id,),
        ).fetchone()
        if row is None:
            raise CaptureNotFound(capture_id)
        return StoredCapture(*row)

    def is_signed(self, capture_id: str) -> bool:
        row = self._connection.execute(
            "SELECT signed FROM captures WHERE capture_id = ?", (capture_id,)
        ).fetchone()
        if row is None:
            raise CaptureNotFound(capture_id)
        return bool(row[0])

    def list_site_receipts(self, capture_id: str) -> tuple[tuple[str, bytes, str], ...]:
        rows = self._connection.execute(
            """
            SELECT station_id, payload, digest FROM site_receipts
            WHERE capture_id = ? ORDER BY station_id
            """,
            (capture_id,),
        ).fetchall()
        return tuple(rows)

    def read_site_receipt(self, capture_id: str, station_id: str) -> tuple[bytes, str] | None:
        row = self._connection.execute(
            "SELECT payload, digest FROM site_receipts WHERE capture_id = ? AND station_id = ?",
            (capture_id, station_id),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
