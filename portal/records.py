from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from .errors import InvalidColumn, InvalidRecord


class Column(str, Enum):
    PENDING = "pending"
    REVIEWED = "reviewed"


COLUMN_ORDER = (Column.PENDING, Column.REVIEWED)


@dataclass(frozen=True, slots=True)
class RawObservation:
    observation_id: str
    station_id: str
    raw_value: int


ObservationInput = RawObservation | Mapping[str, object]


def parse_column(value: object) -> Column:
    if isinstance(value, Column):
        return value
    if type(value) is str:
        try:
            return Column(value)
        except ValueError as exc:
            raise InvalidColumn(f"unknown portal column {value!r}") from exc
    raise InvalidColumn(f"unknown portal column {value!r}")


def validate_identifier(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise InvalidRecord(f"{field} must be a nonblank string")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidRecord(f"{field} must contain valid Unicode") from exc
    return value


def validate_observation(record: ObservationInput) -> RawObservation:
    if isinstance(record, RawObservation):
        observation_id = record.observation_id
        station_id = record.station_id
        raw_value = record.raw_value
    elif isinstance(record, Mapping):
        if record.keys() != {"observation_id", "station_id", "raw_value"}:
            raise InvalidRecord("each observation needs observation_id, station_id and raw_value")
        observation_id = record["observation_id"]
        station_id = record["station_id"]
        raw_value = record["raw_value"]
    else:
        raise InvalidRecord("expected an observation or an observation mapping")
    if type(raw_value) is not int:
        raise InvalidRecord("raw_value must be an integer")
    return RawObservation(
        validate_identifier(observation_id, "observation_id"),
        validate_identifier(station_id, "station_id"),
        raw_value,
    )
