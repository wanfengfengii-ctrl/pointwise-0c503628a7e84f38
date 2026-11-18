import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import ArchiveStore, CaptureAuditor, CaptureReader, Publisher
from collector import Collector
from portal import Column, FixturePortal, RawObservation
from stations import StationCatalog


def main() -> int:
    with TemporaryDirectory(prefix="river-handover-") as workspace:
        work = Path(workspace)
        catalog_path = work / "calibrations.sqlite3"
        archive_path = work / "observations.sqlite3"
        portal = FixturePortal(page_size=1)
        try:
            with StationCatalog(catalog_path) as catalog, ArchiveStore(archive_path) as store:
                catalog.register_station("bridge", "morning", 2, 1, 7, 8)
                export_id = portal.freeze_export("day-shift", {
                    Column.PENDING: [RawObservation("A", "bridge", 3), RawObservation("B", "bridge", 4)],
                    Column.REVIEWED: [],
                })
                capture_id = Collector(catalog, Publisher(store)).capture_frozen(
                    portal, export_id, "night-handover", {"bridge": "morning"}
                )
                daytime = CaptureReader(store).get(capture_id)
                catalog.register_station("bridge", "next-shift", 2, 13, 7, 8)
        finally:
            portal.close()

        with ArchiveStore(archive_path) as store:
            nighttime = CaptureReader(store).get(capture_id)
            audited = CaptureAuditor(store).audit(capture_id)
        values = [row.normalized_value for row in nighttime.observations]
        groups = [[g.station_id, g.column.value, g.count, g.minimum, g.maximum, g.out_of_range] for g in nighttime.groups]
        expected_groups = [["bridge", "pending", 2, 7, 9, 1], ["bridge", "reviewed", 0, None, None, 0]]
        if nighttime != daytime or values != [7, 9] or groups != expected_groups or not audited:
            raise RuntimeError("night shift did not recover the expected frozen capture")
        print(f"成果 {capture_id}，规范值（毫单位）{values}")
        for group in nighttime.groups:
            print(f"站点 {group.station_id} 栏目 {group.column.value}，观测 {group.count}，极值 {group.minimum}/{group.maximum}，越界 {group.out_of_range}")
        print(f"停源重开核对通过 {audited}")
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v8",
            "input": {"raw_pending": [3, 4], "fixed_offset": 1, "later_offset": 13, "coefficient": 2, "range": [7, 8], "source_closed": True},
            "expected": {"normalized_values": [7, 9], "groups": expected_groups},
            "actual": {"normalized_values": values, "groups": groups},
            "downstream_expected": {"same_projection": True, "audit_passed": True},
            "downstream_actual": {"same_projection": nighttime == daytime, "audit_passed": audited},
            "code_paths": ["collector/sweep.py", "archive/publish.py", "archive/read.py", "archive/audit.py"],
            "oracle": "The fixed calibration gives 7 and 9 by hand, with only 9 outside [7,8]. The later revision has no bearing on the archived batch.",
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
