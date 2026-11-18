"""Single-station handover demo on a mixed-station frozen page.

The day shift freezes one export whose first page mixes the bridge and weir
stations, then publishes a signed capture that fixes one receipt per bound
station together with the whole-order originals and projection.  The night
shift first saves the bridge receipt through an independent channel, then the
portal and the station catalog are shut down and moved away.  The archive is
reopened to claim the bridge package, and finally the archive is closed too:
the package is verified using only the independently retained receipt.
"""
import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import (
    ArchiveCommitError,
    ArchiveStore,
    AuditError,
    CaptureAuditor,
    CaptureNotFound,
    CaptureReader,
    Publisher,
    verify_site,
)
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


def _assert_reject(label, receipt, capture_id, station_id, package):
    try:
        verify_site(receipt, capture_id, station_id, package)
    except AuditError as exc:
        print(f"  拒绝 {label}: {exc}")
        return
    raise RuntimeError(f"tampered package was accepted: {label}")


def main() -> int:
    with TemporaryDirectory(prefix="river-site-handover-") as workspace:
        work = Path(workspace)
        catalog_path = work / "calibrations.sqlite3"
        archive_path = work / "observations.sqlite3"
        portal = FixturePortal(page_size=3)
        try:
            with StationCatalog(catalog_path) as catalog, ArchiveStore(archive_path) as store:
                export_id, versions = freeze_example(catalog, portal)
                capture_id = Collector(catalog, Publisher(store)).capture_frozen_with_receipts(
                    portal, export_id, "night-shift", versions
                )
                reader = CaptureReader(store)
                # The day shift hands each station its own receipt through an independent channel.
                saved_bridge_receipt = reader.get_site_receipt(capture_id, "bridge")
                saved_weir_receipt = reader.get_site_receipt(capture_id, "weir")
                bridge_package = reader.get_site_package(capture_id, "bridge")
                weir_package = reader.get_site_package(capture_id, "weir")
                if not CaptureAuditor(store).audit(capture_id):
                    raise RuntimeError("whole-archive audit failed before the sources shut down")
        finally:
            # Portal and station library go offline; the fixed archive remains the only source.
            portal.close()
            catalog_path.rename(work / "calibrations-offline.sqlite3")

        with ArchiveStore(archive_path) as store:
            reader = CaptureReader(store)
            reopened_receipt = reader.get_site_receipt(capture_id, "bridge")
            if reopened_receipt != saved_bridge_receipt:
                raise RuntimeError("reopened receipt differs from the independently saved bytes")
            claimed_package = reader.get_site_package(capture_id, "bridge")
            if claimed_package != bridge_package:
                raise RuntimeError("reopened package differs from the first claim")
            print(f"停源重开领取成果 {capture_id!r} 的桥站单站包")
            print(json.dumps(claimed_package, ensure_ascii=False, sort_keys=True))

        # Close the archive as well: only the saved receipt and the package bytes remain.
        tamper_cases = {
            "去掉 C": lambda p: p["columns"]["pending"].pop(),
            "去掉空栏": lambda p: p["columns"].pop("reviewed"),
            "C 原行改为 2": lambda p: p["columns"]["pending"][1].__setitem__("row_number", 2),
            "A 原值改为 30": lambda p: p["columns"]["pending"][0].__setitem__("raw_value", 30),
            "换口径": lambda p: p["calibration"].__setitem__("coefficient", 1),
        }
        print("离线核验（仅凭另存回执、预期成果号与站点号）：")
        projection = verify_site(saved_bridge_receipt, capture_id, "bridge", claimed_package)
        normalized = [
            [row.observation_id, row.location.page_number, row.location.row_number, row.normalized_value]
            for row in projection.observations
        ]
        if normalized != [["A", 1, 1, 7], ["C", 1, 3, 7]]:
            raise RuntimeError("offline normalized rows differ from the fixed calibration")
        ledgers = [
            [group.column.value, group.count, group.minimum, group.maximum, group.out_of_range]
            for group in projection.groups
        ]
        if ledgers != [["pending", 2, 7, 7, 0], ["reviewed", 0, None, None, 0]]:
            raise RuntimeError("offline two-column ledger differs from the closed-interval rule")
        print(f"  原包通过：规范行 {normalized}")
        print(f"  两栏账 {ledgers}")
        for label, mutate in tamper_cases.items():
            altered = copy.deepcopy(claimed_package)
            mutate(altered)
            _assert_reject(label, saved_bridge_receipt, capture_id, "bridge", altered)
        _assert_reject("混用堰站回执", saved_weir_receipt, capture_id, "bridge", claimed_package)
        _assert_reject("混用别的成果号", saved_bridge_receipt, "other-capture", "bridge", claimed_package)
        _assert_reject("混用别的站点号", saved_bridge_receipt, capture_id, "weir", claimed_package)

        # 断库交班：复用原回归的延迟外键故障让最终提交失败，重开后新成果与回执都不可见。
        failing_portal = FixturePortal(page_size=3)
        try:
            with StationCatalog(work / "calibrations-offline.sqlite3") as catalog, ArchiveStore(archive_path) as store:
                failing_export, failing_versions = freeze_example(catalog, failing_portal, export_id="broken-shift")
                store._connection.executescript("""
                    CREATE TABLE demo_commit_gate (id INTEGER PRIMARY KEY);
                    CREATE TABLE demo_deferred_failure (
                        gate_id INTEGER REFERENCES demo_commit_gate(id) DEFERRABLE INITIALLY DEFERRED
                    );
                    CREATE TEMP TRIGGER fail_broken_evidence_commit
                    AFTER INSERT ON capture_evidence WHEN NEW.capture_id = 'broken'
                    BEGIN
                        INSERT INTO demo_deferred_failure(gate_id) VALUES (42);
                    END;
                """)
                try:
                    Collector(catalog, Publisher(store)).capture_frozen_with_receipts(
                        failing_portal, failing_export, "broken", failing_versions
                    )
                    raise RuntimeError("broken capture was unexpectedly committed")
                except ArchiveCommitError as exc:
                    print(f"断库提交按预期回滚：{type(exc).__name__} <- {type(exc.__cause__).__name__}")
        finally:
            failing_portal.close()
        with ArchiveStore(archive_path) as reopened:
            for claim in (
                lambda: CaptureReader(reopened).get("broken"),
                lambda: CaptureReader(reopened).get_site_receipt("broken", "bridge"),
                lambda: CaptureReader(reopened).get_site_package("broken", "bridge"),
            ):
                try:
                    claim()
                    raise RuntimeError("broken capture remained visible after rollback")
                except CaptureNotFound:
                    pass
            if not CaptureAuditor(reopened).audit(capture_id):
                raise RuntimeError("earlier signed capture stopped auditing after the broken commit")
            print("断库重开：成果 broken 与回执均不可领取，既有成果仍可整档复核")

        # In an isolated archive copy, missing or altered receipts fail the whole-archive review.
        for label, mutate in (
            ("整组回执丢失", lambda con: con.execute(
                "DELETE FROM site_receipts WHERE capture_id = ?", (capture_id,))),
            ("桥站回执改动", lambda con: _alter_bridge_receipt(con, capture_id)),
        ):
            copy_path = work / f"{label}.sqlite3"
            with closing(sqlite3.connect(archive_path)) as source, closing(sqlite3.connect(copy_path)) as target:
                source.backup(target)
            with closing(sqlite3.connect(copy_path)) as connection, connection:
                mutate(connection)
            with ArchiveStore(copy_path) as isolated:
                try:
                    CaptureAuditor(isolated).audit(capture_id)
                except AuditError as exc:
                    print(f"  隔离副本 {label} 整档复核失败：{exc}")
                else:
                    raise RuntimeError(f"whole-archive audit accepted {label}")

        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "site-handover",
            "input": {
                "page_size": 3,
                "pending_page": [["A", "bridge", 3], ["B", "weir", 70], ["C", "bridge", 4]],
                "reviewed": [],
                "bridge_calibration": {"coefficient": 0, "offset": 7, "range": [7, 7]},
            },
            "expected": {
                "bridge_rows": [["A", 1, 1, 7], ["C", 1, 3, 7]],
                "bridge_ledgers": [["pending", 2, 7, 7, 0], ["reviewed", 0, None, None, 0]],
            },
            "actual": {"bridge_rows": normalized, "bridge_ledgers": ledgers},
            "offline": {"portal_closed": True, "station_library_moved": True, "archive_closed": True},
            "code_paths": ["collector/sweep.py", "archive/publish.py", "archive/site.py",
                           "archive/read.py", "archive/audit.py"],
            "oracle": "The receipt fixes each bridge original by page/row and raw value, so any "
                      "missing row, moved row, changed raw value or changed calibration is rejected "
                      "even when the normalized projection would stay at 7.",
        }, ensure_ascii=False, separators=(",", ":")))
        print("交接范围：回执按绑定站点逐站签发，只含本站两栏原件的页行/原值摘要承诺与固定口径；")
        print("交接原址：包内每条原件保留混站原页的绝对页号行号，空栏以空列表交付；")
        print("固定口径：版本、系数、偏置与闭区间随回执冻结，离线按 README 闭区间口径复算两栏账。")
    return 0


def _alter_bridge_receipt(connection, capture_id):
    payload = connection.execute(
        "SELECT payload FROM site_receipts WHERE capture_id = ? AND station_id = 'bridge'",
        (capture_id,),
    ).fetchone()[0]
    data = json.loads(payload)
    data["calibration"]["offset"] = hex(8)
    connection.execute(
        "UPDATE site_receipts SET payload = ? WHERE capture_id = ? AND station_id = 'bridge'",
        (json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"), capture_id),
    )


if __name__ == "__main__":
    raise SystemExit(main())
