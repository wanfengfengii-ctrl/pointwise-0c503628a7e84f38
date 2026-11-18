"""Single-station handover receipts, packages and offline verification.

A receipt is an independent, station-scoped document.  It carries only the
identities, the fixed calibration and digest commitments over the station's
own original rows.  Original rows and readings of other stations never enter
a station receipt or a station package.
"""
import hashlib
import json

from collector.bindings import StationBindings
from collector.provenance import LocatedObservation, RowLocation
from portal.records import COLUMN_ORDER, Column
from quality.grouping import group_observations
from quality.normalize import NormalizedObservation
from quality.report import QualityResult
from quality.statistics import summarize_group
from stations.calibration import StationRevision
from stations.ranges import ClosedRange

from .codec import CaptureProjection, encode_evidence, evidence_digest
from .errors import AuditError
from .evidence import FrozenEvidence, rebuild_manifest

_RECEIPT_SCHEMA = 1


class SiteReceiptUnavailable(LookupError):
    """The capture was published through the original entry without station receipts."""


def _dump(document) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(document) -> str:
    return hashlib.sha256(_dump(document)).hexdigest()


def _hex(value) -> str:
    if type(value) is not int:
        raise AuditError("expected an exact integer")
    return hex(value)


def _identity(value, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise AuditError(f"{field} must be a nonblank string")
    return value


def _row_commitment(export_id: str, column: Column, located: LocatedObservation) -> dict:
    row = located.row
    location = located.location
    document = {
        "schema": _RECEIPT_SCHEMA,
        "export_id": _identity(export_id, "export_id"),
        "column": column.value,
        "page_number": location.page_number,
        "row_number": location.row_number,
        "observation_id": _identity(row.observation_id, "observation_id"),
        "raw_value": _hex(row.raw_value),
    }
    return {"location": {
        "column": column.value,
        "page_number": location.page_number,
        "row_number": location.row_number,
        "observation_id": _identity(row.observation_id, "observation_id"),
    }, "commitment": _digest(document)}


def _calibration_document(revision: StationRevision) -> dict:
    return {
        "version": _identity(revision.version, "version"),
        "coefficient": _hex(revision.coefficient),
        "offset": _hex(revision.offset),
        "lower": _hex(revision.allowed.lower),
        "upper": _hex(revision.allowed.upper),
    }


def build_site_receipt(evidence: FrozenEvidence, station_id: str) -> bytes:
    """Build the fixed, station-scoped receipt bytes for one bound station."""
    if not isinstance(evidence, FrozenEvidence):
        raise AuditError("expected frozen capture evidence")
    station_id = _identity(station_id, "station_id")
    revision = evidence.bindings.for_station(station_id)
    manifest = rebuild_manifest(evidence)
    columns = {column: [] for column in COLUMN_ORDER}
    for located in manifest.observations:
        if located.row.station_id == station_id:
            columns[located.location.column].append(located)
    body = {
        "schema": _RECEIPT_SCHEMA,
        "capture_id": _identity(evidence.capture_id, "capture_id"),
        "export_id": _identity(evidence.export_id, "export_id"),
        "station_id": station_id,
        "calibration": _calibration_document(revision),
        "evidence_digest": evidence_digest_binding(evidence),
        "columns": {
            column.value: [_row_commitment(evidence.export_id, column, located) for located in columns[column]]
            for column in COLUMN_ORDER
        },
    }
    return _dump(body)


def evidence_digest_binding(evidence: FrozenEvidence) -> str:
    return evidence_digest(encode_evidence(evidence))


def decode_site_receipt(payload: bytes) -> dict:
    if type(payload) is not bytes:
        raise AuditError("a station receipt must be document bytes")
    try:
        body = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, RecursionError, UnicodeDecodeError) as exc:
        raise AuditError("invalid station receipt document") from exc
    if type(body) is not dict or body.get("schema") != _RECEIPT_SCHEMA:
        raise AuditError("unsupported station receipt schema")
    required = {"schema", "capture_id", "export_id", "station_id", "calibration", "evidence_digest", "columns"}
    if set(body) != required:
        raise AuditError("station receipt has an unexpected field set")
    return body


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditError(f"duplicate receipt field {key!r}")
        result[key] = value
    return result


def build_site_receipts(evidence: FrozenEvidence) -> tuple[tuple[str, bytes, str], ...]:
    """Build one receipt per bound station, paired with its digest."""
    return tuple(
        (station_id, payload, hashlib.sha256(payload).hexdigest())
        for station_id in evidence.bindings.station_ids()
        for payload in (build_site_receipt(evidence, station_id),)
    )


def build_site_package(evidence: FrozenEvidence, station_id: str, receipt: bytes) -> dict:
    """Assemble the station package, exposing only this station's originals."""
    station_id = _identity(station_id, "station_id")
    revision = evidence.bindings.for_station(station_id)
    manifest = rebuild_manifest(evidence)
    buckets = {column: [] for column in COLUMN_ORDER}
    for located in manifest.observations:
        if located.row.station_id == station_id:
            buckets[located.location.column].append(located)

    columns = {}
    for column in COLUMN_ORDER:
        columns[column.value] = [
            {
                "observation_id": located.row.observation_id,
                "raw_value": located.row.raw_value,
                "page_number": located.location.page_number,
                "row_number": located.location.row_number,
            }
            for located in buckets[column]
        ]
    return {
        "capture_id": evidence.capture_id,
        "export_id": evidence.export_id,
        "station_id": station_id,
        "calibration": {
            "version": revision.version,
            "coefficient": revision.coefficient,
            "offset": revision.offset,
            "lower": revision.allowed.lower,
            "upper": revision.allowed.upper,
        },
        "columns": columns,
        "proof": {
            "receipt_digest": hashlib.sha256(receipt).hexdigest(),
            "evidence_digest": evidence_digest_binding(evidence),
        },
    }


def _require_object(value, field: str) -> dict:
    if type(value) is not dict:
        raise AuditError(f"{field} must be an object")
    return value


def _require_int(value, field: str) -> int:
    if type(value) is not int:
        raise AuditError(f"{field} must be an exact integer")
    return value


def _column_entries(value):
    data = _require_object(value, "columns")
    if set(data) != {"pending", "reviewed"}:
        raise AuditError("package columns must contain exactly pending and reviewed")
    entries = {}
    for label in ("pending", "reviewed"):
        rows = data[label]
        if type(rows) is not list:
            raise AuditError(f"column {label} must be a list of originals")
        entries[Column(label)] = rows
    return entries


def _verify_row(export_id: str, column: Column, position: int, value: object, receipt_columns: dict) -> dict:
    row = _require_object(value, f"{column.value} original")
    if set(row) != {"observation_id", "raw_value", "page_number", "row_number"}:
        raise AuditError("an original needs observation_id, raw_value, page_number and row_number")
    observation_id = _identity(row["observation_id"], "observation_id")
    raw_value = _require_int(row["raw_value"], "raw_value")
    page_number = _require_int(row["page_number"], "page_number")
    row_number = _require_int(row["row_number"], "row_number")
    if page_number < 1 or row_number < 1:
        raise AuditError("original page and row numbers start at 1")
    document = {
        "schema": _RECEIPT_SCHEMA,
        "export_id": export_id,
        "column": column.value,
        "page_number": page_number,
        "row_number": row_number,
        "observation_id": observation_id,
        "raw_value": hex(raw_value),
    }
    entry = {"location": {
        "column": column.value,
        "page_number": page_number,
        "row_number": row_number,
        "observation_id": observation_id,
    }, "commitment": _digest(document)}
    committed = receipt_columns.get(column.value)
    if type(committed) is not list or position >= len(committed) or committed[position] != entry:
        raise AuditError(
            f"package original at {column.value} position {position} is not fixed by the station receipt"
        )
    return entry

def _verify_calibration(package_value: object, receipt: dict) -> StationRevision:
    calibration = _require_object(package_value, "calibration")
    if set(calibration) != {"version", "coefficient", "offset", "lower", "upper"}:
        raise AuditError("calibration needs version, coefficient, offset, lower and upper")
    fixed = _require_object(receipt["calibration"], "receipt calibration")
    expected = {
        "version": fixed["version"],
        "coefficient": int(fixed["coefficient"], 16),
        "offset": int(fixed["offset"], 16),
        "lower": int(fixed["lower"], 16),
        "upper": int(fixed["upper"], 16),
    }
    actual = {key: _require_int(calibration[key], key) if key != "version"
              else _identity(calibration[key], "version") for key in expected}
    if actual != expected:
        raise AuditError("package calibration differs from the fixed station revision")
    return StationRevision(
        receipt["station_id"], expected["version"], expected["coefficient"], expected["offset"],
        ClosedRange(expected["lower"], expected["upper"]),
    )


def _projection_for_station(
    receipt: dict, revision: StationRevision, rows_by_column: dict[Column, list[dict]]
) -> CaptureProjection:
    normalized: list[NormalizedObservation] = []
    for column in COLUMN_ORDER:
        for row in rows_by_column[column]:
            value = revision.normalize(row["raw_value"])
            normalized.append(NormalizedObservation(
                row["observation_id"],
                receipt["station_id"],
                RowLocation(receipt["export_id"], column, row["page_number"], row["row_number"]),
                value,
                revision.in_range(value),
            ))
    bindings = StationBindings((revision,))
    groups = tuple(summarize_group(group) for group in group_observations(bindings, tuple(normalized)))
    return CaptureProjection(receipt["capture_id"], receipt["export_id"], QualityResult(tuple(normalized), groups))


def verify_site(receipt: bytes, capture_id: str, station_id: str, package: dict) -> CaptureProjection:
    """Verify a station package using only the independently retained receipt."""
    try:
        body = decode_site_receipt(receipt)
        package = _require_object(package, "package")
        if set(package) != {"capture_id", "export_id", "station_id", "calibration", "columns", "proof"}:
            raise AuditError("package has an unexpected field set")
        capture_id = _identity(capture_id, "capture_id")
        station_id = _identity(station_id, "station_id")
        if body["capture_id"] != capture_id or body["station_id"] != station_id:
            raise AuditError("receipt identity does not match the expected capture and station")
        if body["export_id"] != _identity(package.get("export_id", ""), "export_id"):
            raise AuditError("package export identity differs from the receipt")
        if package.get("capture_id") != capture_id or package.get("station_id") != station_id:
            raise AuditError("package identity does not match the expected capture and station")

        revision = _verify_calibration(package.get("calibration"), body)
        package_columns = _column_entries(package.get("columns"))
        receipt_columns = _require_object(body["columns"], "receipt columns")
        if set(receipt_columns) != {"pending", "reviewed"}:
            raise AuditError("receipt columns must contain exactly pending and reviewed")

        rows_by_column: dict[Column, list[dict]] = {}
        for column in COLUMN_ORDER:
            rows = package_columns[column]
            rows_by_column[column] = rows
            for position, row in enumerate(rows):
                _verify_row(body["export_id"], column, position, row, receipt_columns)
            committed = receipt_columns[column.value]
            if type(committed) is not list or len(committed) != len(rows):
                raise AuditError(f"package column {column.value} does not cover the fixed station originals")

        proof = _require_object(package.get("proof"), "proof")
        if set(proof) != {"receipt_digest", "evidence_digest"}:
            raise AuditError("package proof needs receipt_digest and evidence_digest")
        if proof["receipt_digest"] != hashlib.sha256(receipt).hexdigest():
            raise AuditError("package proof is not bound to this station receipt")
        if proof["evidence_digest"] != body["evidence_digest"]:
            raise AuditError("package proof is not bound to the fixed capture evidence")
        return _projection_for_station(body, revision, rows_by_column)
    except AuditError:
        raise
    except Exception as exc:
        raise AuditError(f"station verification failed: {exc}") from exc


def audit_site_receipts(store, evidence: FrozenEvidence, capture_id: str) -> None:
    """Rebuild every bound station receipt from fixed pages and reconcile stored bytes.

    Captures published through the original entry carry no receipts; they keep the
    original audit behavior.  A signed capture must expose exactly one intact receipt
    per bound station, including captures where every bound column is empty.
    """
    expected = {station_id: (payload, digest) for station_id, payload, digest in build_site_receipts(evidence)}
    stored = {station_id: (payload, digest) for station_id, payload, digest in store.list_site_receipts(capture_id)}
    if not store.is_signed(capture_id):
        # Captures published through the original entry carry no receipts.
        if stored:
            raise AuditError(f"station receipts exist for an unsigned capture {capture_id!r}")
        return
    if set(stored) != set(expected):
        raise AuditError(f"station receipt set differs for {capture_id!r}")
    for station_id, (payload, digest) in expected.items():
        actual_payload, actual_digest = stored[station_id]
        if actual_digest != digest or hashlib.sha256(actual_payload).hexdigest() != digest:
            raise AuditError(f"fixed station receipt digest differs for {capture_id!r}/{station_id!r}")
        if actual_payload != payload:
            raise AuditError(f"fixed station receipt differs for {capture_id!r}/{station_id!r}")
        decode_site_receipt(actual_payload)
