import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import ArchiveStore, CaptureAuditor, CaptureNotFound, CaptureReader, Publisher
from archive.codec import decode_evidence
from collector import Collector, DuplicateObservation, PageCoverageError, UnboundStation
from collector.batch import CaptureBuilder
from collector.bindings import resolve_bindings
from portal import Column, FixturePortal, RawObservation
from portal.errors import SourceUnavailable
from stations import StationCatalog, UnknownRevision


class ObservedPortal(FixturePortal):
    def __init__(self, page_size):
        super().__init__(page_size)
        self.requests = []

    def read_page(self, export_id, column, page_number):
        self.requests.append((export_id, column, page_number))
        return super().read_page(export_id, column, page_number)


class SubstitutingSource:
    def __init__(self, portal, requested_page, replacement):
        self.portal = portal
        self.requested_page = requested_page
        self.replacement = replacement

    def read_page(self, export_id, column, page_number):
        if (column, page_number) == self.requested_page:
            return self.replacement
        return self.portal.read_page(export_id, column, page_number)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        work = Path(self.enterContext(TemporaryDirectory()))
        self.catalog = self.enterContext(StationCatalog(work / "stations.sqlite3"))
        self.store = self.enterContext(ArchiveStore(work / "archive.sqlite3"))
        self.catalog.register_station("bridge", "v1", 2, 1, 7, 8)
        self.publisher = Publisher(self.store)
        self.collector = Collector(self.catalog, self.publisher)
        self.reader = CaptureReader(self.store)
        self.portal = ObservedPortal(page_size=2)
        self.addCleanup(self.portal.close)
        self.versions = {"bridge": "v1"}

    def freeze(self, export_id, pending, reviewed):
        return self.portal.freeze_export(export_id, {Column.PENDING: pending, Column.REVIEWED: reviewed})

    def publish_old_capture(self):
        self.freeze("old-export", [RawObservation("old-observation", "bridge", 3)], [])
        self.collector.capture_frozen(self.portal, "old-export", "old", self.versions)
        return self.reader.get("old")

    def assert_unpublished(self, capture_id):
        with self.assertRaises(CaptureNotFound):
            self.reader.get(capture_id)
        with self.assertRaises(CaptureNotFound):
            self.store.read_capture(capture_id)

    def test_v4_normalization_and_empty_reviewed_group(self):
        self.freeze("day", [RawObservation("A", "bridge", 3), RawObservation("B", "bridge", 4)], [])
        capture_id = self.collector.capture_frozen(self.portal, "day", "capture", self.versions)
        self.assertEqual(capture_id, "capture")
        published = self.reader.get(capture_id)
        values = [row.normalized_value for row in published.observations]
        flags = [row.in_range for row in published.observations]
        groups = [[group.column.value, group.count, group.minimum, group.maximum, group.out_of_range] for group in published.groups]
        expected_groups = [["pending", 2, 7, 9, 1], ["reviewed", 0, None, None, 0]]
        self.assertEqual(values, [7, 9])
        self.assertEqual(flags, [True, False])
        self.assertEqual(groups, expected_groups)
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v4",
            "input": {"pending": [3, 4], "reviewed": [], "coefficient": 2, "offset": 1, "range": [7, 8]},
            "expected": {"values": [7, 9], "in_range": [True, False]},
            "actual": {"values": values, "in_range": flags},
            "downstream_expected": expected_groups,
            "downstream_actual": groups,
            "code_paths": ["collector/sweep.py", "stations/calibration.py", "quality/report.py", "archive/read.py"],
            "oracle": "By hand, 2 times 3 plus 1 is 7 and 2 times 4 plus 1 is 9. Only 7 lies in [7,8]. The reviewed group is empty.",
        }, separators=(",", ":")))

    def test_v4_group_matrix_has_independent_station_and_column_statistics(self):
        self.catalog.register_station("weir", "plain", 1, 0, 0, 10)
        self.freeze("day", [
            RawObservation("W0", "weir", 0),
            RawObservation("B3", "bridge", 3),
            RawObservation("W10", "weir", 10),
            RawObservation("B4", "bridge", 4),
        ], [RawObservation("W11", "weir", 11), RawObservation("B2", "bridge", 2)])
        self.collector.capture_frozen(self.portal, "day", "matrix", {"weir": "plain", "bridge": "v1"})
        published = self.reader.get("matrix")
        self.assertEqual(
            [(row.observation_id, row.normalized_value, row.in_range) for row in published.observations],
            [("W0", 0, True), ("B3", 7, True), ("W10", 10, True), ("B4", 9, False), ("W11", 11, False), ("B2", 5, False)],
        )
        self.assertEqual(
            [(g.station_id, g.column, g.count, g.minimum, g.maximum, g.out_of_range) for g in published.groups],
            [
                ("bridge", Column.PENDING, 2, 7, 9, 1),
                ("bridge", Column.REVIEWED, 1, 5, 5, 1),
                ("weir", Column.PENDING, 2, 0, 10, 0),
                ("weir", Column.REVIEWED, 1, 11, 11, 1),
            ],
        )

    def test_v4_empty_batch_has_two_empty_terminal_pages_and_groups(self):
        self.freeze("empty", [], [])
        self.collector.capture_frozen(self.portal, "empty", "empty-capture", self.versions)
        self.assertEqual(self.portal.requests, [("empty", Column.PENDING, 1), ("empty", Column.REVIEWED, 1)])
        result = self.reader.get("empty-capture")
        self.assertEqual(result.observations, ())
        self.assertEqual([(g.column, g.count, g.minimum, g.maximum, g.out_of_range) for g in result.groups], [
            (Column.PENDING, 0, None, None, 0), (Column.REVIEWED, 0, None, None, 0),
        ])
        evidence = decode_evidence(self.store.read_capture("empty-capture").evidence)
        self.assertEqual([(p.column, p.page_number, p.rows, p.terminal) for p in evidence.pages], [
            (Column.PENDING, 1, (), True), (Column.REVIEWED, 1, (), True),
        ])
        self.assertTrue(CaptureAuditor(self.store).audit("empty-capture"))

    def test_v4_provenance_points_to_each_original_observation(self):
        pending = [RawObservation(f"P{number}", "bridge", number) for number in range(1, 6)]
        reviewed = [RawObservation("R1", "bridge", 7), RawObservation("R2", "bridge", 8)]
        self.freeze("multicolumn", pending, reviewed)
        self.collector.capture_frozen(self.portal, "multicolumn", "located", self.versions)
        result = self.reader.get("located")
        self.assertEqual(
            [(r.location.column, r.location.page_number, r.location.row_number) for r in result.observations],
            [(Column.PENDING, 1, 1), (Column.PENDING, 1, 2), (Column.PENDING, 2, 1), (Column.PENDING, 2, 2),
             (Column.PENDING, 3, 1), (Column.REVIEWED, 1, 1), (Column.REVIEWED, 1, 2)],
        )
        readback = decode_evidence(self.store.read_capture("located").evidence)
        self.portal.close()
        archive_pages = {(page.column, page.page_number): page for page in readback.pages}
        originals = []
        for row in result.observations:
            location = row.location
            self.assertEqual(location.export_id, "multicolumn")
            page = archive_pages[(location.column, location.page_number)]
            original = page.rows[location.row_number - 1]
            self.assertEqual(original.observation_id, row.observation_id)
            originals.append(original)
        self.assertEqual(originals, pending + reviewed)

    def test_v4_unknown_revision_fails_before_first_page(self):
        self.freeze("day", [RawObservation("A", "bridge", 3)], [])
        with self.assertRaises(UnknownRevision):
            self.collector.capture_frozen(self.portal, "day", "bad", {"bridge": "missing"})
        self.assertEqual(self.portal.requests, [])
        self.assert_unpublished("bad")

    def test_unbound_station_rejects_an_otherwise_valid_page(self):
        self.freeze("day", [RawObservation("A", "other-station", 3)], [])
        with self.assertRaises(UnboundStation):
            self.collector.capture_frozen(self.portal, "day", "bad", self.versions)
        self.assert_unpublished("bad")

    def test_exact_large_integer_calibration_and_equal_endpoint(self):
        large = 1 << 20000
        expected = -(1 << 40000) + 1
        self.catalog.register_station("bridge", "wide", -large, 1, expected, expected)
        self.freeze("day", [RawObservation("exact", "bridge", large)], [])
        self.collector.capture_frozen(self.portal, "day", "wide-result", {"bridge": "wide"})
        published = self.reader.get("wide-result")
        self.assertTrue(published.observations[0].normalized_value == expected)
        self.assertTrue(published.observations[0].in_range)
        self.assertTrue(published.groups[0].minimum == expected == published.groups[0].maximum)
        self.assertEqual(published.groups[0].out_of_range, 0)
        self.assertTrue(CaptureAuditor(self.store).audit("wide-result"))

    def test_v5_second_column_final_read_failure_retains_history(self):
        old = self.publish_old_capture()
        self.freeze("new-export", [RawObservation("P", "bridge", 3)], [
            RawObservation("R1", "bridge", 3), RawObservation("R2", "bridge", 4), RawObservation("R3", "bridge", 5),
        ])
        self.portal.fail_page("new-export", Column.REVIEWED, 2)
        with self.assertRaises(SourceUnavailable):
            self.collector.capture_frozen(self.portal, "new-export", "new", self.versions)
        self.assertEqual(self.portal.requests[-3:], [
            ("new-export", Column.PENDING, 1), ("new-export", Column.REVIEWED, 1), ("new-export", Column.REVIEWED, 2),
        ])
        self.assert_unpublished("new")
        self.assertEqual(self.reader.get("old"), old)

    def test_v5_foreign_export_rejected_even_when_column_and_page_match(self):
        old = self.publish_old_capture()
        self.freeze("right", [RawObservation("P", "bridge", 3)], [RawObservation("R", "bridge", 4)])
        self.freeze("wrong", [], [RawObservation("X", "bridge", 7)])
        foreign = self.portal.read_page("wrong", Column.REVIEWED, 1)
        port = SubstitutingSource(self.portal, (Column.REVIEWED, 1), foreign)
        with self.assertRaisesRegex(PageCoverageError, "expected export"):
            self.collector.capture_frozen(port, "right", "new", self.versions)
        self.assert_unpublished("new")
        self.assertEqual(self.reader.get("old"), old)

    def test_v5_page_order_rejects_wrong_column_skip_and_replay(self):
        old = self.publish_old_capture()
        self.freeze("day", [RawObservation(f"P{n}", "bridge", n) for n in range(3)], [RawObservation("R", "bridge", 3)])
        first = self.portal.read_page("day", Column.PENDING, 1)
        second = self.portal.read_page("day", Column.PENDING, 2)
        reviewed = self.portal.read_page("day", Column.REVIEWED, 1)
        for label, requested, replacement in (
            ("wrong_column", (Column.PENDING, 1), reviewed),
            ("skipped", (Column.PENDING, 1), second),
            ("replayed", (Column.PENDING, 2), first),
        ):
            with self.subTest(label=label):
                port = SubstitutingSource(self.portal, requested, replacement)
                with self.assertRaises(PageCoverageError):
                    self.collector.capture_frozen(port, "day", label, self.versions)
                self.assert_unpublished(label)
                self.assertEqual(self.reader.get("old"), old)

    def test_publisher_rejects_a_manifest_missing_one_terminal_page(self):
        self.freeze("day", [RawObservation("A", "bridge", 3)], [])
        builder = CaptureBuilder("bad", "day", resolve_bindings(self.catalog, self.versions))
        first = self.portal.read_page("day", Column.PENDING, 1)
        builder.accept_page(first)
        with self.assertRaises(PageCoverageError):
            builder.finish()
        builder.accept_page(self.portal.read_page("day", Column.REVIEWED, 1))
        incomplete = replace(builder.finish(), pages=(first,))
        with self.assertRaisesRegex(PageCoverageError, "missing terminal page"):
            self.publisher.publish(incomplete)
        self.assert_unpublished("bad")

    def test_v6_observation_id_is_unique_across_columns_and_stations(self):
        self.catalog.register_station("weir", "v1", 1, 0, 0, 10)
        self.freeze("dupe", [RawObservation("E", "bridge", 3)], [RawObservation("E", "weir", 4)])
        with self.assertRaises(DuplicateObservation):
            self.collector.capture_frozen(self.portal, "dupe", "duplicate", {"bridge": "v1", "weir": "v1"})
        self.assert_unpublished("duplicate")

    def test_v6_same_column_repetition_across_pages_is_rejected(self):
        self.freeze("dupe", [RawObservation("E", "bridge", 3), RawObservation("F", "bridge", 4), RawObservation("E", "bridge", 5)], [])
        with self.assertRaises(DuplicateObservation):
            self.collector.capture_frozen(self.portal, "dupe", "duplicate", self.versions)
        self.assertEqual(self.portal.requests, [("dupe", Column.PENDING, 1), ("dupe", Column.PENDING, 2)])
        self.assert_unpublished("duplicate")
