import hashlib
import json
from dataclasses import dataclass

from collector.bindings import StationBindings
from collector.provenance import RowLocation
from portal.errors import InvalidColumn, InvalidRecord
from portal.pages import FrozenPage
from portal.records import Column, RawObservation, parse_column, validate_identifier
from quality.normalize import NormalizedObservation
from quality.report import QualityResult
from quality.statistics import GroupStatistics
from stations.calibration import StationRevision
from stations.ranges import ClosedRange

from .evidence import FrozenEvidence


class ArchiveEncodingError(ValueError):
    """The persisted projection or evidence has an unsupported shape or value."""


@dataclass(frozen=True, slots=True)
class CaptureProjection:
    capture_id: str
    export_id: str
    quality: QualityResult

    @property
    def observations(self) -> tuple[NormalizedObservation, ...]:
        return self.quality.observations

    @property
    def groups(self) -> tuple[GroupStatistics, ...]:
        return self.quality.groups


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArchiveEncodingError(f"duplicate document field {key!r}")
        result[key] = value
    return result


def _fields(value, *keys):
    if type(value) is not dict or value.keys() != set(keys):
        raise ArchiveEncodingError(f"expected an object with fields {', '.join(keys)}")
    return value


def _document(payload: bytes, *fields):
    if type(payload) is not bytes:
        raise ArchiveEncodingError("expected document bytes")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except ArchiveEncodingError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ArchiveEncodingError("invalid UTF-8 JSON document") from exc
    value = _fields(value, "schema", "capture_id", "export_id", *fields)
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ArchiveEncodingError("unsupported archive document schema")
    return value


def _dump(document) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _array(value):
    if type(value) is not list:
        raise ArchiveEncodingError("expected a document array")
    return value


def _identity(value, field: str) -> str:
    try:
        return validate_identifier(value, field)
    except InvalidRecord as exc:
        raise ArchiveEncodingError(str(exc)) from exc


def _column(value) -> Column:
    try:
        return parse_column(value)
    except InvalidColumn as exc:
        raise ArchiveEncodingError(str(exc)) from exc


def _column_value(value) -> str:
    if type(value) is not Column:
        raise ArchiveEncodingError("expected a portal column")
    return value.value


def _boolean(value, field: str) -> bool:
    if type(value) is not bool:
        raise ArchiveEncodingError(f"{field} must be a boolean")
    return value


def _count(value, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ArchiveEncodingError(f"{field} must be an integer at least {minimum}")
    return value


def _integer_text(value) -> str:
    if type(value) is not int:
        raise ArchiveEncodingError("expected an exact integer")
    # A power-of-two representation bypasses decimal conversion limits for large integers.
    return hex(value)


def _read_integer(value) -> int:
    if type(value) is not str:
        raise ArchiveEncodingError("expected an integer encoded as hexadecimal text")
    try:
        integer = int(value, 16)
    except ValueError as exc:
        raise ArchiveEncodingError("invalid hexadecimal integer") from exc
    if hex(integer) != value:
        raise ArchiveEncodingError("noncanonical hexadecimal integer")
    return integer


def _pack_location(location: RowLocation):
    if not isinstance(location, RowLocation):
        raise ArchiveEncodingError("expected an original row location")
    return {
        "export_id": _identity(location.export_id, "export_id"),
        "column": _column_value(location.column),
        "page_number": _count(location.page_number, "page_number", 1),
        "row_number": _count(location.row_number, "row_number", 1),
    }


def _unpack_location(value) -> RowLocation:
    data = _fields(value, "export_id", "column", "page_number", "row_number")
    return RowLocation(
        _identity(data["export_id"], "export_id"),
        _column(data["column"]),
        _count(data["page_number"], "page_number", 1),
        _count(data["row_number"], "row_number", 1),
    )


def _pack_normalized(row: NormalizedObservation):
    if not isinstance(row, NormalizedObservation):
        raise ArchiveEncodingError("expected a normalized observation")
    return {
        "observation_id": _identity(row.observation_id, "observation_id"),
        "station_id": _identity(row.station_id, "station_id"),
        "location": _pack_location(row.location),
        "normalized_value": _integer_text(row.normalized_value),
        "in_range": _boolean(row.in_range, "in_range"),
    }


def _unpack_normalized(value) -> NormalizedObservation:
    data = _fields(value, "observation_id", "station_id", "location", "normalized_value", "in_range")
    return NormalizedObservation(
        _identity(data["observation_id"], "observation_id"),
        _identity(data["station_id"], "station_id"),
        _unpack_location(data["location"]),
        _read_integer(data["normalized_value"]),
        _boolean(data["in_range"], "in_range"),
    )


def _check_group(group: GroupStatistics) -> None:
    _count(group.count, "count")
    _count(group.out_of_range, "out_of_range")
    if group.out_of_range > group.count:
        raise ArchiveEncodingError("out_of_range exceeds the group count")
    if group.count == 0:
        if group.minimum is not None or group.maximum is not None:
            raise ArchiveEncodingError("an empty group requires null extrema")
    elif type(group.minimum) is not int or type(group.maximum) is not int or group.minimum > group.maximum:
        raise ArchiveEncodingError("a populated group requires ordered integer extrema")


def _pack_group(group: GroupStatistics):
    if not isinstance(group, GroupStatistics):
        raise ArchiveEncodingError("expected statistics for a station column")
    _check_group(group)
    return {
        "station_id": _identity(group.station_id, "station_id"),
        "column": _column_value(group.column),
        "count": group.count,
        "minimum": _integer_text(group.minimum) if group.minimum is not None else None,
        "maximum": _integer_text(group.maximum) if group.maximum is not None else None,
        "out_of_range": group.out_of_range,
    }


def _unpack_group(value) -> GroupStatistics:
    data = _fields(value, "station_id", "column", "count", "minimum", "maximum", "out_of_range")
    group = GroupStatistics(
        _identity(data["station_id"], "station_id"),
        _column(data["column"]),
        _count(data["count"], "count"),
        _read_integer(data["minimum"]) if data["minimum"] is not None else None,
        _read_integer(data["maximum"]) if data["maximum"] is not None else None,
        _count(data["out_of_range"], "out_of_range"),
    )
    _check_group(group)
    return group


def encode_projection(projection: CaptureProjection) -> bytes:
    if not isinstance(projection, CaptureProjection) or not isinstance(projection.quality, QualityResult):
        raise ArchiveEncodingError("expected a capture projection and its quality result")
    return _dump({
        "schema": 1,
        "capture_id": _identity(projection.capture_id, "capture_id"),
        "export_id": _identity(projection.export_id, "export_id"),
        "observations": [_pack_normalized(row) for row in projection.observations],
        "groups": [_pack_group(group) for group in projection.groups],
    })


def decode_projection(payload: bytes) -> CaptureProjection:
    data = _document(payload, "observations", "groups")
    quality = QualityResult(
        tuple(_unpack_normalized(row) for row in _array(data["observations"])),
        tuple(_unpack_group(group) for group in _array(data["groups"])),
    )
    return CaptureProjection(_identity(data["capture_id"], "capture_id"), _identity(data["export_id"], "export_id"), quality)


def _pack_original(row: RawObservation):
    if not isinstance(row, RawObservation):
        raise ArchiveEncodingError("expected a frozen original observation")
    return {
        "observation_id": _identity(row.observation_id, "observation_id"),
        "station_id": _identity(row.station_id, "station_id"),
        "raw_value": _integer_text(row.raw_value),
    }


def _unpack_original(value) -> RawObservation:
    data = _fields(value, "observation_id", "station_id", "raw_value")
    return RawObservation(
        _identity(data["observation_id"], "observation_id"),
        _identity(data["station_id"], "station_id"),
        _read_integer(data["raw_value"]),
    )


def _pack_page(page: FrozenPage):
    if not isinstance(page, FrozenPage) or type(page.rows) is not tuple:
        raise ArchiveEncodingError("expected a frozen source page")
    return {
        "export_id": _identity(page.export_id, "export_id"),
        "column": _column_value(page.column),
        "page_number": _count(page.page_number, "page_number", 1),
        "rows": [_pack_original(row) for row in page.rows],
        "terminal": _boolean(page.terminal, "terminal"),
    }


def _unpack_page(value) -> FrozenPage:
    data = _fields(value, "export_id", "column", "page_number", "rows", "terminal")
    return FrozenPage(
        _identity(data["export_id"], "export_id"),
        _column(data["column"]),
        _count(data["page_number"], "page_number", 1),
        tuple(_unpack_original(row) for row in _array(data["rows"])),
        _boolean(data["terminal"], "terminal"),
    )


def _pack_revision(revision: StationRevision):
    return {
        "station_id": revision.station_id,
        "version": revision.version,
        "coefficient": _integer_text(revision.coefficient),
        "offset": _integer_text(revision.offset),
        "lower": _integer_text(revision.allowed.lower),
        "upper": _integer_text(revision.allowed.upper),
    }


def _unpack_revision(value) -> StationRevision:
    data = _fields(value, "station_id", "version", "coefficient", "offset", "lower", "upper")
    return StationRevision(
        _identity(data["station_id"], "station_id"),
        _identity(data["version"], "version"),
        _read_integer(data["coefficient"]),
        _read_integer(data["offset"]),
        ClosedRange(_read_integer(data["lower"]), _read_integer(data["upper"])),
    )


def encode_evidence(evidence: FrozenEvidence) -> bytes:
    if not isinstance(evidence, FrozenEvidence) or not isinstance(evidence.bindings, StationBindings):
        raise ArchiveEncodingError("expected frozen original pages and station bindings")
    if type(evidence.pages) is not tuple:
        raise ArchiveEncodingError("expected an immutable sequence of original pages")
    return _dump({
        "schema": 1,
        "capture_id": _identity(evidence.capture_id, "capture_id"),
        "export_id": _identity(evidence.export_id, "export_id"),
        "pages": [_pack_page(page) for page in evidence.pages],
        "bindings": [_pack_revision(revision) for revision in evidence.bindings.revisions],
    })


def decode_evidence(payload: bytes) -> FrozenEvidence:
    data = _document(payload, "pages", "bindings")
    try:
        bindings = StationBindings(tuple(_unpack_revision(item) for item in _array(data["bindings"])))
    except ArchiveEncodingError:
        raise
    except ValueError as exc:
        raise ArchiveEncodingError("invalid fixed station bindings") from exc
    return FrozenEvidence(
        _identity(data["capture_id"], "capture_id"),
        _identity(data["export_id"], "export_id"),
        tuple(_unpack_page(page) for page in _array(data["pages"])),
        bindings,
    )


def evidence_digest(payload: bytes) -> str:
    if type(payload) is not bytes:
        raise ArchiveEncodingError("expected original evidence bytes")
    return hashlib.sha256(payload).hexdigest()
