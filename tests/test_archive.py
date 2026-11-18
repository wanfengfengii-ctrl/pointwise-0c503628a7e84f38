import hashlib
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import ArchiveCommitError, ArchiveStore, AuditError, CaptureAlreadyExists, CaptureAuditor, CaptureNotFound, CaptureReader, Publisher
from archive.codec import decode_evidence
from collector import Collector
from portal import Column, FixturePortal, RawObservation
from portal.errors import SourceUnavailable
from stations import StationCatalog


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(TemporaryDirectory()))
        self.archive_path = self.work / "archive.sqlite3"
        self.station_path = self.work / "stations.sqlite3"
        self.portal = FixturePortal(page_size=1)
        self.addCleanup(self.portal.close)
        with StationCatalog(self.station_path) as catalog:
            catalog.register_station("bridge", "v1", 2, 1, 7, 8)

    def publish(self, catalog, store, export_id, capture_id, value, version="v1"):
        self.portal.freeze_export(export_id, {
            Column.PENDING: [RawObservation(export_id + "-observation", "bridge", value)],
            Column.REVIEWED: [],
        })
        return Collector(catalog, Publisher(store)).capture_frozen(self.portal, export_id, capture_id, {"bridge": version})

    def prepare_original(self, version="v1"):
        with StationCatalog(self.station_path) as catalog, ArchiveStore(self.archive_path) as store:
            if version == "zero":
                catalog.register_station("bridge", "zero", 0, 7, 7, 7)
            self.assertEqual(self.publish(catalog, store, "original-export", "original", 3, version), "original")
            return CaptureReader(store).get("original")

    def copy_archive(self):
        copy_path = self.work / "isolated-copy.sqlite3"
        with closing(sqlite3.connect(self.archive_path)) as source, closing(sqlite3.connect(copy_path)) as copy:
            source.backup(copy)
        return copy_path

    def read_and_audit_original(self, expected):
        with ArchiveStore(self.archive_path) as store:
            self.assertEqual(CaptureReader(store).get("original"), expected)
            self.assertTrue(CaptureAuditor(store).audit("original"))

    def test_v7_real_commit_failure_rolls_back_projection_and_evidence(self):
        original = self.prepare_original()
        with StationCatalog(self.station_path) as catalog, ArchiveStore(self.archive_path) as writer:
            # This trigger stages a deferred foreign-key violation after new evidence is written.
            writer._connection.executescript("""
                CREATE TABLE test_commit_gate (id INTEGER PRIMARY KEY);
                CREATE TABLE test_deferred_failure (
                    gate_id INTEGER REFERENCES test_commit_gate(id) DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TEMP TRIGGER fail_new_evidence_commit
                AFTER INSERT ON capture_evidence WHEN NEW.capture_id = 'rejected'
                BEGIN
                    INSERT INTO test_deferred_failure(gate_id) VALUES (42);
                END;
            """)
            trace = []
            writer._connection.set_trace_callback(trace.append)
            with self.assertRaises(ArchiveCommitError) as caught:
                self.publish(catalog, writer, "rejected-export", "rejected", 4)
            self.assertIsInstance(caught.exception.__cause__, sqlite3.IntegrityError)
            self.assertEqual(caught.exception.__cause__.sqlite_errorname, "SQLITE_CONSTRAINT_FOREIGNKEY")
            self.assertIn("COMMIT", trace)
            self.assertIn("ROLLBACK", trace)
            self.assertFalse(writer._connection.in_transaction)
        with ArchiveStore(self.archive_path) as reopened:
            with self.assertRaises(CaptureNotFound):
                CaptureReader(reopened).get("rejected")
            with self.assertRaises(CaptureNotFound):
                CaptureAuditor(reopened).audit("rejected")
            self.assertEqual(reopened._connection.execute("SELECT capture_id FROM captures").fetchall(), [("original",)])
            self.assertEqual(reopened._connection.execute("SELECT capture_id FROM capture_evidence").fetchall(), [("original",)])
        self.read_and_audit_original(original)

    def test_v7_committed_view_hides_staged_publication_from_independent_connection(self):
        original = self.prepare_original()
        with StationCatalog(self.station_path) as catalog, ArchiveStore(self.archive_path) as writer, ArchiveStore(self.archive_path) as observer:
            staged_observations = []
            independent_reader = CaptureReader(observer)

            def inspect_staged_view():
                try:
                    visible_new = independent_reader.get("next")
                except CaptureNotFound:
                    visible_new = None
                visible_original = independent_reader.get("original")
                visible_evidence = observer._connection.execute(
                    "SELECT capture_id FROM capture_evidence WHERE capture_id = ?", ("next",)
                ).fetchall()
                staged_observations.append((visible_new, visible_original, visible_evidence))

            writer._connection.create_function("inspect_staged_view", 0, inspect_staged_view)
            writer._connection.executescript("""
                CREATE TEMP TRIGGER inspect_next_evidence
                AFTER INSERT ON capture_evidence WHEN NEW.capture_id = 'next'
                BEGIN
                    SELECT inspect_staged_view();
                END;
            """)
            self.assertEqual(self.publish(catalog, writer, "next-export", "next", 4), "next")
            self.assertEqual(staged_observations, [(None, original, [])])
        with ArchiveStore(self.archive_path) as reopened:
            published = CaptureReader(reopened).get("next")
            self.assertEqual([row.normalized_value for row in published.observations], [9])
            self.assertTrue(CaptureAuditor(reopened).audit("next"))

    def test_v7_duplicate_capture_retains_first_projection_and_fixed_evidence(self):
        original = self.prepare_original()
        with StationCatalog(self.station_path) as catalog, ArchiveStore(self.archive_path) as store:
            first_bytes = store.read_capture("original")
            with self.assertRaises(CaptureAlreadyExists):
                self.publish(catalog, store, "duplicate-export", "original", 4)
            self.assertEqual(store.read_capture("original"), first_bytes)
        self.read_and_audit_original(original)

    def test_v8_no_catalog_offline_reopen_keeps_nonzero_offset(self):
        original = self.prepare_original()
        with StationCatalog(self.station_path) as catalog:
            catalog.register_station("bridge", "v2", 2, 13, 7, 8)
            with ArchiveStore(self.archive_path) as store:
                self.publish(catalog, store, "second-export", "second", 3, "v2")
                self.assertEqual([row.normalized_value for row in CaptureReader(store).get("second").observations], [19])
        self.portal.close()
        with self.assertRaises(SourceUnavailable):
            self.portal.read_page("original-export", Column.PENDING, 1)
        # Move the closed catalog aside so a fallback catalog at its original path cannot supply old revisions.
        self.station_path.rename(self.work / "offline-stations.sqlite3")
        with ArchiveStore(self.archive_path) as store:
            store.initialize()
            store.initialize()
            historical = CaptureReader(store).get("original")
            self.assertEqual(historical, original)
            self.assertEqual([row.normalized_value for row in historical.observations], [7])
            fixed = decode_evidence(store.read_capture("original").evidence).bindings.for_station("bridge")
            self.assertEqual((fixed.version, fixed.coefficient, fixed.offset), ("v1", 2, 1))
            self.assertTrue(CaptureAuditor(store).audit("original"))
            self.assertTrue(CaptureAuditor(store).audit("second"))
        self.assertFalse(self.station_path.exists())

    def test_v8_unknown_capture_has_distinct_lookup_error(self):
        self.prepare_original()
        with ArchiveStore(self.archive_path) as store:
            with self.assertRaises(CaptureNotFound):
                CaptureReader(store).get("not-registered")
            with self.assertRaises(CaptureNotFound):
                CaptureAuditor(store).audit("not-registered")

    def change_first_raw_value(self, copy_path, value):
        with closing(sqlite3.connect(copy_path)) as connection, connection:
            payload = connection.execute("SELECT payload FROM capture_evidence WHERE capture_id = 'original'").fetchone()[0]
            data = json.loads(payload)
            data["pages"][0]["rows"][0]["raw_value"] = hex(value)
            connection.execute(
                "UPDATE capture_evidence SET payload = ? WHERE capture_id = 'original'",
                (json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"),),
            )

    def test_v9_changed_original_value_is_detected_on_reopen(self):
        original = self.prepare_original()
        self.portal.close()
        copy_path = self.copy_archive()
        self.change_first_raw_value(copy_path, 4)
        with ArchiveStore(copy_path) as copy:
            self.assertEqual(CaptureReader(copy).get("original"), original)
            with self.assertRaisesRegex(AuditError, "digest differs"):
                CaptureAuditor(copy).audit("original")
        self.read_and_audit_original(original)

    def test_v9_same_projection_still_detects_changed_original(self):
        original = self.prepare_original("zero")
        self.assertEqual([row.normalized_value for row in original.observations], [7])
        self.assertEqual([(g.count, g.minimum, g.maximum, g.out_of_range) for g in original.groups], [(1, 7, 7, 0), (0, None, None, 0)])
        self.portal.close()
        copy_path = self.copy_archive()
        self.change_first_raw_value(copy_path, 40)
        with ArchiveStore(copy_path) as copy:
            stored = copy.read_capture("original")
            self.assertEqual(decode_evidence(stored.evidence).pages[0].rows[0].raw_value, 40)
            self.assertEqual(CaptureReader(copy).get("original"), original)
            with self.assertRaisesRegex(AuditError, "digest differs"):
                CaptureAuditor(copy).audit("original")
        self.read_and_audit_original(original)

    def test_v9_missing_evidence_cannot_be_audited(self):
        original = self.prepare_original()
        copy_path = self.copy_archive()
        with closing(sqlite3.connect(copy_path)) as connection, connection:
            connection.execute("DELETE FROM capture_evidence WHERE capture_id = 'original'")
        with ArchiveStore(copy_path) as copy:
            self.assertEqual(CaptureReader(copy).get("original"), original)
            with self.assertRaisesRegex(AuditError, "missing fixed evidence"):
                CaptureAuditor(copy).audit("original")
        self.read_and_audit_original(original)

    def test_v9_projection_mismatch_is_detected_with_intact_evidence(self):
        original = self.prepare_original()
        copy_path = self.copy_archive()
        with closing(sqlite3.connect(copy_path)) as connection, connection:
            payload = connection.execute("SELECT projection FROM captures WHERE capture_id = 'original'").fetchone()[0]
            data = json.loads(payload)
            data["groups"][0]["out_of_range"] = 1
            connection.execute(
                "UPDATE captures SET projection = ? WHERE capture_id = 'original'",
                (json.dumps(data).encode("utf-8"),),
            )
        with ArchiveStore(copy_path) as copy:
            self.assertEqual(CaptureReader(copy).get("original").groups[0].out_of_range, 1)
            with self.assertRaisesRegex(AuditError, "recomputed result differs"):
                CaptureAuditor(copy).audit("original")
        self.read_and_audit_original(original)

    def test_decodable_evidence_cannot_omit_terminal_page_even_with_matching_digest(self):
        original = self.prepare_original()
        copy_path = self.copy_archive()
        with closing(sqlite3.connect(copy_path)) as connection, connection:
            payload = connection.execute("SELECT payload FROM capture_evidence WHERE capture_id = 'original'").fetchone()[0]
            data = json.loads(payload)
            data["pages"].pop()
            shortened = json.dumps(data).encode("utf-8")
            connection.execute(
                "UPDATE capture_evidence SET payload = ?, digest = ? WHERE capture_id = 'original'",
                (shortened, hashlib.sha256(shortened).hexdigest()),
            )
        with ArchiveStore(copy_path) as copy:
            with self.assertRaisesRegex(AuditError, "missing terminal page for reviewed"):
                CaptureAuditor(copy).audit("original")
        self.read_and_audit_original(original)
