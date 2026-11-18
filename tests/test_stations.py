import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from stations import InvalidRange, RevisionConflict, StationCatalog, UnknownRevision


class StationTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(TemporaryDirectory())) / "stations.sqlite3"

    def count_revisions(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT COUNT(*) FROM station_revisions").fetchone()[0]

    def test_v1_identical_registration_reuses_revision(self):
        with StationCatalog(self.path) as catalog:
            first = catalog.register_station("bridge", "v1", 2, 1, 7, 8)
            second = catalog.register_station("bridge", "v1", 2, 1, 7, 8)
            self.assertEqual(first, second)
            self.assertEqual(first, catalog.get_revision("bridge", "v1"))
        self.assertEqual(self.count_revisions(), 1)

    def test_v1_reopen_init_preserves_registered_revision(self):
        with StationCatalog(self.path) as catalog:
            original = catalog.register_station("bridge", "v1", 2, 1, 7, 8)
            catalog.initialize()
            catalog.initialize()
        with StationCatalog(self.path) as catalog:
            catalog.initialize()
            self.assertEqual(catalog.get_revision("bridge", "v1"), original)
            self.assertEqual(catalog.register_station("bridge", "v1", 2, 1, 7, 8), original)
        self.assertEqual(self.count_revisions(), 1)

    def test_v2_conflicting_offset_cannot_replace_revision(self):
        with StationCatalog(self.path) as catalog:
            catalog.register_station("bridge", "v1", 2, 1, 7, 8)
            with self.assertRaises(RevisionConflict):
                catalog.register_station("bridge", "v1", 2, 9, 7, 8)
        with StationCatalog(self.path) as catalog:
            original = catalog.get_revision("bridge", "v1")
            self.assertEqual((original.coefficient, original.offset, original.allowed.lower, original.allowed.upper), (2, 1, 7, 8))
        self.assertEqual(self.count_revisions(), 1)

    def test_v2_invalid_range_creates_no_revision(self):
        with StationCatalog(self.path) as catalog:
            with self.assertRaises(InvalidRange):
                catalog.register_station("bridge", "bad", 2, 1, 8, 7)
            with self.assertRaises(InvalidRange):
                catalog.register_station("bridge", "bool", 2, 1, True, 7)
            with self.assertRaises(UnknownRevision):
                catalog.get_revision("bridge", "bad")
        self.assertEqual(self.count_revisions(), 0)
