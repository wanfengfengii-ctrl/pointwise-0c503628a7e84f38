"""Mixed-page input for the station handover and a check of the current capture."""
import json

from archive import ArchiveStore, CaptureAuditor, CaptureReader, Publisher
from archive.codec import decode_evidence
from collector import Collector
from portal import Column, FixturePortal, RawObservation
from stations import StationCatalog


def freeze_example(catalog, source, export_id="shift", *, bridge_raw=3, weir_raw=70):
    catalog.register_station("bridge", "zero", 0, 7, 7, 7)
    catalog.register_station("weir", "zero", 0, 11, 11, 11)
    frozen = source.freeze_export(export_id, {
        Column.PENDING: [
            RawObservation("A", "bridge", bridge_raw),
            RawObservation("B", "weir", weir_raw),
            RawObservation("C", "bridge", 4),
        ],
        Column.REVIEWED: [],
    })
    return frozen, {"bridge": "zero", "weir": "zero"}


def main():
    portal = FixturePortal(page_size=3)
    with ArchiveStore(":memory:") as store:
        try:
            with StationCatalog(":memory:") as catalog:
                export_id, versions = freeze_example(catalog, portal)
                capture_id = Collector(catalog, Publisher(store)).capture_frozen(
                    portal, export_id, "night-shift", versions
                )
        finally:
            portal.close()

        evidence = decode_evidence(store.read_capture(capture_id).evidence)
        pages = [
            [page.column.value, page.page_number, page.terminal,
             [[row.observation_id, row.station_id, row.raw_value] for row in page.rows]]
            for page in evidence.pages
        ]
        expected_pages = [
            ["pending", 1, True, [["A", "bridge", 3], ["B", "weir", 70], ["C", "bridge", 4]]],
            ["reviewed", 1, True, []],
        ]
        projection = CaptureReader(store).get(capture_id)
        rows = [[row.observation_id, row.location.row_number, row.normalized_value]
                for row in projection.observations]
        groups = [[g.station_id, g.column.value, g.count] for g in projection.groups]
        if pages != expected_pages or rows != [["A", 1, 7], ["B", 2, 11], ["C", 3, 7]]:
            raise RuntimeError("mixed-page originals or positions differ from the fixture")
        if groups != [["bridge", "pending", 2], ["bridge", "reviewed", 0],
                      ["weir", "pending", 1], ["weir", "reviewed", 0]]:
            raise RuntimeError("frozen group counts differ from the fixture")
        if not CaptureAuditor(store).audit(capture_id):
            raise RuntimeError("full capture audit failed after source shutdown")
        print(json.dumps({"capture": capture_id, "original_pages": pages,
                          "normalized_rows": rows, "group_counts": groups}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
