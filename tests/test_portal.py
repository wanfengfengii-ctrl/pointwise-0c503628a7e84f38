import unittest

from portal import Column, FixturePortal, RawObservation
from portal.errors import DuplicateExport, InvalidColumn, InvalidRecord, PageOutOfRange, SourceUnavailable, UnknownExport


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.portal = FixturePortal(page_size=2)
        self.addCleanup(self.portal.close)

    def get_export_pages(self, export_id):
        return (
            self.portal.read_page(export_id, Column.PENDING, 1),
            self.portal.read_page(export_id, Column.PENDING, 2),
            self.portal.read_page(export_id, Column.REVIEWED, 1),
        )

    def test_v3_repeat_multipage_export_with_empty_column(self):
        rows = [RawObservation("B", "bridge", 3), RawObservation("A", "bridge", 4), RawObservation("C", "bridge", 5)]
        self.assertEqual(self.portal.freeze_export("day", {"pending": rows, "reviewed": []}), "day")
        first, second, empty = self.get_export_pages("day")
        self.assertEqual((first.rows, first.terminal), (tuple(rows[:2]), False))
        self.assertEqual((second.rows, second.terminal), ((rows[2],), True))
        self.assertEqual((empty.rows, empty.page_number, empty.terminal), ((), 1, True))
        self.assertEqual([page.page_number for page in (first, second, empty)], [1, 2, 1])
        self.assertEqual(self.get_export_pages("day"), (first, second, empty))

    def test_v3_input_snapshot_is_independent_of_original_records_and_lists(self):
        first = {"observation_id": "A", "station_id": "bridge", "raw_value": 3}
        pending = [first, RawObservation("B", "bridge", 4), RawObservation("C", "bridge", 5)]
        reviewed = []
        inputs = {Column.PENDING: pending, Column.REVIEWED: reviewed}
        self.portal.freeze_export("day", inputs)
        original = self.get_export_pages("day")
        first["raw_value"] = 90
        pending[-1] = RawObservation("other", "bridge", 12)
        pending.pop(1)
        reviewed.append(RawObservation("new", "bridge", 20))
        inputs[Column.PENDING] = []
        self.assertEqual(self.get_export_pages("day"), original)
        self.assertEqual(
            [[(row.observation_id, row.raw_value) for row in page.rows] for page in original],
            [[("A", 3), ("B", 4)], [("C", 5)], []],
        )
        self.assertEqual([page.terminal for page in original], [False, True, True])

    def test_v3_source_errors_are_distinct(self):
        self.portal.freeze_export("day", {"pending": [RawObservation("A", "bridge", 3)], "reviewed": []})
        original = self.portal.read_page("day", "pending", 1)
        with self.assertRaises(UnknownExport):
            self.portal.read_page("unknown", "pending", 1)
        with self.assertRaises(InvalidColumn):
            self.portal.read_page("day", "elsewhere", 1)
        for number in (0, 2, True):
            with self.subTest(number=number), self.assertRaises(PageOutOfRange):
                self.portal.read_page("day", "pending", number)
        self.portal.fail_page("day", "reviewed", 1)
        with self.assertRaises(SourceUnavailable):
            self.portal.read_page("day", "reviewed", 1)
        self.assertEqual(self.portal.read_page("day", "pending", 1), original)
        self.portal.close()
        with self.assertRaises(SourceUnavailable):
            self.portal.read_page("day", "pending", 1)

    def test_invalid_freeze_does_not_claim_export_identity(self):
        invalid = (
            {"observation_id": "B", "station_id": "bridge"},
            {"observation_id": "B", "station_id": "bridge", "raw_value": "3"},
            RawObservation("B", "bridge", True),
        )
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(InvalidRecord):
                self.portal.freeze_export("retry", {"pending": [RawObservation("A", "bridge", 3)], "reviewed": [record]})
            with self.assertRaises(UnknownExport):
                self.portal.read_page("retry", "pending", 1)
        with self.assertRaises(InvalidRecord):
            self.portal.freeze_export("retry", {"pending": []})
        self.portal.freeze_export("retry", {"pending": [], "reviewed": []})
        with self.assertRaises(DuplicateExport):
            self.portal.freeze_export("retry", {"pending": [RawObservation("C", "bridge", 7)], "reviewed": []})
        self.assertEqual(self.portal.read_page("retry", "pending", 1).rows, ())
