import copy
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import (
    ArchiveCommitError,
    ArchiveStore,
    AuditError,
    CaptureAlreadyExists,
    CaptureAuditor,
    CaptureNotFound,
    CaptureReader,
    Publisher,
    SiteReceiptUnavailable,
    verify_site,
)
from collector import Collector, UnboundStation
from portal import Column, FixturePortal, RawObservation
from stations import StationCatalog

from examples.site_handover import freeze_example


class SiteHandoverTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(TemporaryDirectory()))
        self.catalog_path = self.work / "stations.sqlite3"
        self.archive_path = self.work / "archive.sqlite3"
        self.portal = FixturePortal(page_size=3)
        self.addCleanup(self.portal.close)
        with StationCatalog(self.catalog_path) as catalog:
            self.export_id, self.versions = freeze_example(catalog, self.portal)

    def publish_signed(self, store, capture_id="signed", catalog_path=None):
        with StationCatalog(catalog_path or self.catalog_path) as catalog:
            return Collector(catalog, Publisher(store)).capture_frozen_with_receipts(
                self.portal, self.export_id, capture_id, self.versions
            )

    def publish_legacy(self, store, capture_id="legacy"):
        with StationCatalog(self.catalog_path) as catalog:
            return Collector(catalog, Publisher(store)).capture_frozen(
                self.portal, self.export_id, capture_id, self.versions
            )

    def copy_archive(self, name="isolated-copy.sqlite3"):
        copy_path = self.work / name
        with closing(sqlite3.connect(self.archive_path)) as source, closing(sqlite3.connect(copy_path)) as copy:
            source.backup(copy)
        return copy_path

    # --- publishing -----------------------------------------------------

    def test_signed_capture_issues_one_receipt_per_bound_station_in_one_transaction(self):
        with ArchiveStore(self.archive_path) as store:
            capture_id = self.publish_signed(store)
            self.assertEqual(capture_id, "signed")
            rows = store._connection.execute(
                "SELECT station_id FROM site_receipts WHERE capture_id = ? ORDER BY station_id", (capture_id,)
            ).fetchall()
            self.assertEqual(rows, [("bridge",), ("weir",)])
            self.assertTrue(store.is_signed(capture_id))
            reader = CaptureReader(store)
            for station_id in ("bridge", "weir"):
                payload, digest = store.read_site_receipt(capture_id, station_id)
                self.assertEqual(reader.get_site_receipt(capture_id, station_id), payload)
                import hashlib

                self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def test_receipt_reopen_returns_identical_bytes(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
            first = CaptureReader(store).get_site_receipt("signed", "bridge")
        with ArchiveStore(self.archive_path) as store:
            second = CaptureReader(store).get_site_receipt("signed", "bridge")
        self.assertEqual(first, second)
        self.assertIsInstance(second, bytes)

    def test_new_and_old_entries_share_one_capture_identity_space(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store, capture_id="shared")
            with self.assertRaises(CaptureAlreadyExists):
                self.publish_legacy(store, capture_id="shared")
            with self.assertRaises(CaptureAlreadyExists):
                self.publish_signed(store, capture_id="shared")

    def test_original_entry_still_publishes_without_receipts(self):
        with ArchiveStore(self.archive_path) as store:
            capture_id = self.publish_legacy(store)
            self.assertFalse(store.is_signed(capture_id))
            self.assertEqual(store.list_site_receipts(capture_id), ())
            # Whole-capture read and audit keep working for legacy captures.
            self.assertTrue(CaptureReader(store).get(capture_id).observations)
            self.assertTrue(CaptureAuditor(store).audit(capture_id))

    def test_commit_failure_leaves_no_capture_and_no_receipts(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store, capture_id="earlier")
            store._connection.executescript("""
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
            with self.assertRaises(ArchiveCommitError) as caught:
                self.publish_signed(store, capture_id="rejected")
            self.assertIsInstance(caught.exception.__cause__, sqlite3.IntegrityError)
        with ArchiveStore(self.archive_path) as reopened:
            for reader_call in (
                lambda: CaptureReader(reopened).get("rejected"),
                lambda: CaptureReader(reopened).get_site_receipt("rejected", "bridge"),
                lambda: CaptureReader(reopened).get_site_package("rejected", "bridge"),
            ):
                with self.assertRaises(CaptureNotFound):
                    reader_call()
            self.assertEqual(
                reopened._connection.execute("SELECT capture_id FROM captures").fetchall(), [("earlier",)]
            )
            self.assertEqual(
                reopened._connection.execute("SELECT DISTINCT capture_id FROM site_receipts").fetchall(),
                [("earlier",)],
            )
            self.assertTrue(CaptureAuditor(reopened).audit("earlier"))

    def test_source_failure_during_signed_capture_publishes_nothing(self):
        self.portal.fail_page(self.export_id, Column.REVIEWED, 1)
        with ArchiveStore(self.archive_path) as store:
            from portal.errors import SourceUnavailable

            with self.assertRaises(SourceUnavailable):
                self.publish_signed(store, capture_id="aborted")
            with self.assertRaises(CaptureNotFound):
                CaptureReader(store).get("aborted")
            self.assertEqual(store.list_site_receipts("aborted"), ())

    # --- claim error semantics ------------------------------------------

    def test_claim_entries_report_unknown_unsigned_and_unbound_in_order(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
            self.publish_legacy(store, "old")
            reader = CaptureReader(store)
            with self.assertRaises(CaptureNotFound):
                reader.get_site_receipt("missing", "bridge")
            with self.assertRaises(CaptureNotFound):
                reader.get_site_package("missing", "bridge")
            with self.assertRaises(SiteReceiptUnavailable):
                reader.get_site_receipt("old", "bridge")
            with self.assertRaises(SiteReceiptUnavailable):
                reader.get_site_package("old", "bridge")
            with self.assertRaises(UnboundStation):
                reader.get_site_receipt("signed", "dam")
            with self.assertRaises(UnboundStation):
                reader.get_site_package("signed", "dam")

    # --- package shape and disclosure boundary --------------------------

    def test_bridge_package_exposes_only_bridge_rows_with_absolute_page_positions(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
            package = CaptureReader(store).get_site_package("signed", "bridge")
        self.assertEqual(set(package), {"capture_id", "export_id", "station_id", "calibration", "columns", "proof"})
        self.assertEqual((package["capture_id"], package["export_id"], package["station_id"]),
                         ("signed", self.export_id, "bridge"))
        self.assertEqual(package["calibration"],
                         {"version": "zero", "coefficient": 0, "offset": 7, "lower": 7, "upper": 7})
        self.assertEqual(set(package["columns"]), {"pending", "reviewed"})
        self.assertEqual(package["columns"]["pending"], [
            {"observation_id": "A", "raw_value": 3, "page_number": 1, "row_number": 1},
            {"observation_id": "C", "raw_value": 4, "page_number": 1, "row_number": 3},
        ])
        self.assertEqual(package["columns"]["reviewed"], [])
        encoded = json.dumps(package)
        self.assertNotIn("weir", encoded)
        self.assertNotIn('"B"', encoded)

    def test_package_and_receipt_never_carry_other_station_originals_or_readings(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
            reader = CaptureReader(store)
            for station_id, own, foreign in (("bridge", {"A", "C"}, {"B"}), ("weir", {"B"}, {"A", "C"})):
                package = reader.get_site_package("signed", station_id)
                receipt = reader.get_site_receipt("signed", station_id)
                blob = json.dumps(package) + receipt.decode("utf-8")
                for observation_id in foreign:
                    self.assertNotIn(observation_id, blob)
                for observation_id in own:
                    self.assertIn(observation_id, blob)

    # --- offline verification -------------------------------------------

    def _offline_materials(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
            reader = CaptureReader(store)
            receipt = reader.get_site_receipt("signed", "bridge")
            package = reader.get_site_package("signed", "bridge")
        return receipt, package

    def test_offline_verify_returns_station_projection_and_two_column_ledger(self):
        receipt, package = self._offline_materials()
        projection = verify_site(receipt, "signed", "bridge", package)
        self.assertEqual(
            [(row.observation_id, row.location.page_number, row.location.row_number, row.normalized_value)
             for row in projection.observations],
            [("A", 1, 1, 7), ("C", 1, 3, 7)],
        )
        self.assertEqual(
            [(g.column.value, g.count, g.minimum, g.maximum, g.out_of_range) for g in projection.groups],
            [("pending", 2, 7, 7, 0), ("reviewed", 0, None, None, 0)],
        )

    def test_offline_verify_rejects_every_fixture_tamper_case(self):
        receipt, package = self._offline_materials()

        def reject(label, altered):
            with self.subTest(label=label):
                with self.assertRaises(AuditError):
                    verify_site(receipt, "signed", "bridge", altered)

        without_c = copy.deepcopy(package)
        without_c["columns"]["pending"].pop()
        reject("remove C", without_c)

        without_empty = copy.deepcopy(package)
        del without_empty["columns"]["reviewed"]
        reject("drop empty column", without_empty)

        moved_row = copy.deepcopy(package)
        moved_row["columns"]["pending"][1]["row_number"] = 2
        reject("change C row to 2", moved_row)

        changed_raw = copy.deepcopy(package)
        changed_raw["columns"]["pending"][0]["raw_value"] = 30
        reject("change A raw to 30", changed_raw)

        changed_calibration = copy.deepcopy(package)
        changed_calibration["calibration"]["coefficient"] = 1
        reject("swap calibration", changed_calibration)

        bad_proof = copy.deepcopy(package)
        bad_proof["proof"]["evidence_digest"] = "0" * 64
        reject("tampered proof", bad_proof)

        extra_column = copy.deepcopy(package)
        extra_column["columns"]["archived"] = []
        reject("extra column", extra_column)

        with self.assertRaises(AuditError):
            verify_site(receipt, "other-capture", "bridge", package)
        with self.assertRaises(AuditError):
            verify_site(receipt, "signed", "weir", package)

    def test_same_specification_value_with_changed_raw_is_rejected(self):
        receipt, package = self._offline_materials()
        altered = copy.deepcopy(package)
        # Zero coefficient keeps every normalized value at 7, so the projection would still match.
        altered["columns"]["pending"][0]["raw_value"] = 99
        with self.assertRaises(AuditError):
            verify_site(receipt, "signed", "bridge", altered)

    def test_cross_capture_and_cross_station_receipts_are_rejected(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
            reader = CaptureReader(store)
            bridge_receipt = reader.get_site_receipt("signed", "bridge")
            weir_receipt = reader.get_site_receipt("signed", "weir")
            bridge_package = reader.get_site_package("signed", "bridge")
        with self.assertRaises(AuditError):
            verify_site(weir_receipt, "signed", "bridge", bridge_package)
        # A second capture gets distinct receipts even with identical originals.
        second_portal = FixturePortal(page_size=3)
        self.addCleanup(second_portal.close)
        second_export = second_portal.freeze_export("shift-2", {
            Column.PENDING: [
                RawObservation("A", "bridge", 3),
                RawObservation("B", "weir", 70),
                RawObservation("C", "bridge", 4),
            ],
            Column.REVIEWED: [],
        })
        second_path = self.work / "second.sqlite3"
        with StationCatalog(self.catalog_path) as catalog, ArchiveStore(second_path) as second_store:
            Collector(catalog, Publisher(second_store)).capture_frozen_with_receipts(
                second_portal, second_export, "signed-2", self.versions
            )
            other_receipt = CaptureReader(second_store).get_site_receipt("signed-2", "bridge")
        self.assertNotEqual(other_receipt, bridge_receipt)
        with self.assertRaises(AuditError):
            verify_site(other_receipt, "signed", "bridge", bridge_package)

    def test_corrupt_receipt_bytes_are_rejected_without_partial_results(self):
        receipt, package = self._offline_materials()
        corrupted = bytearray(receipt)
        corrupted[10] = (corrupted[10] + 1) % 256
        with self.assertRaises(AuditError):
            verify_site(bytes(corrupted), "signed", "bridge", package)

    def test_reorder_duplicate_and_malformed_packages_are_rejected(self):
        receipt, package = self._offline_materials()

        def reject(label, altered):
            with self.subTest(label=label):
                with self.assertRaises(AuditError):
                    verify_site(receipt, "signed", "bridge", altered)

        reordered = copy.deepcopy(package)
        reordered["columns"]["pending"].reverse()
        reject("reorder rows", reordered)

        duplicated = copy.deepcopy(package)
        duplicated["columns"]["pending"][1] = copy.deepcopy(duplicated["columns"]["pending"][0])
        reject("duplicate row", duplicated)

        reject("package not an object", [])
        reject("extra top-level field", {**package, "mystery": 1})
        reject("null proof", {**package, "proof": None})
        reject("extra proof field", {**package, "proof": {**package["proof"], "other": "x"}})

        bad_raw = copy.deepcopy(package)
        bad_raw["columns"]["pending"][0]["raw_value"] = 3.0
        reject("non-integer raw value", bad_raw)

        missing_field = copy.deepcopy(package)
        del missing_field["columns"]["pending"][0]["row_number"]
        reject("missing original field", missing_field)

        extra_field = copy.deepcopy(package)
        extra_field["columns"]["pending"][0]["extra"] = 1
        reject("extra original field", extra_field)

        zero_page = copy.deepcopy(package)
        zero_page["columns"]["pending"][0]["page_number"] = 0
        reject("page number below one", zero_page)

        with self.assertRaises(AuditError):
            verify_site(receipt.decode("utf-8"), "signed", "bridge", package)

    def test_bound_station_with_empty_columns_still_gets_a_verifiable_receipt(self):
        silent_portal = FixturePortal(page_size=3)
        self.addCleanup(silent_portal.close)
        with StationCatalog(self.catalog_path) as catalog:
            catalog.register_station("silent", "zero", 1, 0, 0, 10)
        silent_export = silent_portal.freeze_export("silent-shift", {
            Column.PENDING: [
                RawObservation("A", "bridge", 3),
                RawObservation("B", "weir", 70),
                RawObservation("C", "bridge", 4),
            ],
            Column.REVIEWED: [],
        })
        with StationCatalog(self.catalog_path) as catalog, ArchiveStore(self.archive_path) as store:
            capture_id = Collector(catalog, Publisher(store)).capture_frozen_with_receipts(
                silent_portal, silent_export, "with-silent",
                {**self.versions, "silent": "zero"},
            )
            reader = CaptureReader(store)
            receipt = reader.get_site_receipt(capture_id, "silent")
            package = reader.get_site_package(capture_id, "silent")
            self.assertEqual(package["columns"], {"pending": [], "reviewed": []})
            projection = verify_site(receipt, capture_id, "silent", package)
            self.assertEqual(projection.observations, ())
            self.assertEqual(
                [(group.column.value, group.count, group.minimum, group.maximum, group.out_of_range)
                 for group in projection.groups],
                [("pending", 0, None, None, 0), ("reviewed", 0, None, None, 0)],
            )
            self.assertTrue(CaptureAuditor(store).audit(capture_id))

    # --- whole-archive audit --------------------------------------------

    def test_whole_audit_rebuilds_and_reconciles_all_receipts(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
            self.assertTrue(CaptureAuditor(store).audit("signed"))

    def test_whole_audit_fails_when_any_receipt_is_missing_or_altered(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
        for label, mutate in (
            ("delete-one", lambda con: con.execute(
                "DELETE FROM site_receipts WHERE capture_id='signed' AND station_id='weir'")),
            ("delete-group", lambda con: con.execute("DELETE FROM site_receipts WHERE capture_id='signed'")),
            ("alter-payload", lambda con: self._alter_bridge_receipt(con)),
        ):
            copy_path = self.copy_archive(f"{label}.sqlite3")
            with closing(sqlite3.connect(copy_path)) as connection, connection:
                mutate(connection)
            with self.subTest(label=label), ArchiveStore(copy_path) as copy:
                with self.assertRaises(AuditError):
                    CaptureAuditor(copy).audit("signed")

    @staticmethod
    def _alter_bridge_receipt(connection):
        payload = connection.execute(
            "SELECT payload FROM site_receipts WHERE capture_id='signed' AND station_id='bridge'"
        ).fetchone()[0]
        data = json.loads(payload)
        data["calibration"]["offset"] = hex(8)
        connection.execute(
            "UPDATE site_receipts SET payload = ? WHERE capture_id='signed' AND station_id='bridge'",
            (json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"),),
        )

    def test_damaged_stored_receipt_blocks_claim_with_audit_error(self):
        with ArchiveStore(self.archive_path) as store:
            self.publish_signed(store)
        copy_path = self.copy_archive()
        with closing(sqlite3.connect(copy_path)) as connection, connection:
            self._alter_bridge_receipt(connection)
        with ArchiveStore(copy_path) as copy:
            with self.assertRaises(AuditError):
                CaptureReader(copy).get_site_receipt("signed", "bridge")
            with self.assertRaises(AuditError):
                CaptureReader(copy).get_site_package("signed", "bridge")


if __name__ == "__main__":
    unittest.main()
