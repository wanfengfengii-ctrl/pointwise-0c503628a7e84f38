import sqlite3
from os import PathLike

from .calibration import StationRevision, validate_revision_key
from .ranges import ClosedRange


class RevisionConflict(ValueError):
    """An existing revision key is bound to different calibration parameters."""


class UnknownRevision(LookupError):
    """The requested station revision has not been registered."""


class StationRepository:
    def __init__(self, path: str | PathLike[str]):
        self._connection = sqlite3.connect(path)
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise

    def initialize(self) -> None:
        # Hexadecimal text keeps integers exact beyond SQLite's signed 64-bit range.
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS station_revisions (
                station_id TEXT NOT NULL,
                version TEXT NOT NULL,
                coefficient TEXT NOT NULL,
                offset TEXT NOT NULL,
                lower_bound TEXT NOT NULL,
                upper_bound TEXT NOT NULL,
                PRIMARY KEY (station_id, version)
            ) WITHOUT ROWID
            """
        )

    def get_revision(self, station_id: str, version: str) -> StationRevision:
        validate_revision_key(station_id, version)
        row = self._connection.execute(
            """
            SELECT coefficient, offset, lower_bound, upper_bound
            FROM station_revisions WHERE station_id = ? AND version = ?
            """,
            (station_id, version),
        ).fetchone()
        if row is None:
            raise UnknownRevision(f"no station revision {station_id!r}/{version!r}")
        coefficient, offset, lower, upper = (int(value, 16) for value in row)
        return StationRevision(station_id, version, coefficient, offset, ClosedRange(lower, upper))

    def put_revision(self, revision: StationRevision) -> StationRevision:
        if not isinstance(revision, StationRevision):
            raise TypeError("expected a validated station revision")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO station_revisions
                    (station_id, version, coefficient, offset, lower_bound, upper_bound)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (station_id, version) DO NOTHING
                """,
                (
                    revision.station_id,
                    revision.version,
                    hex(revision.coefficient),
                    hex(revision.offset),
                    hex(revision.allowed.lower),
                    hex(revision.allowed.upper),
                ),
            )
            persisted = self.get_revision(revision.station_id, revision.version)
            if persisted != revision:
                raise RevisionConflict(f"{revision.station_id!r}/{revision.version!r} already has a calibration")
        return persisted

    def close(self) -> None:
        self._connection.close()
