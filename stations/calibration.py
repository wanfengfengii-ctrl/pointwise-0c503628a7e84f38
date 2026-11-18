from dataclasses import dataclass

from .ranges import ClosedRange


def validate_revision_key(station_id: str, version: str) -> None:
    for label, value in (("station_id", station_id), ("version", version)):
        if type(value) is not str or not value.strip():
            raise ValueError(f"{label} must be a nonblank string")
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"{label} must contain valid Unicode") from exc


@dataclass(frozen=True, slots=True)
class StationRevision:
    station_id: str
    version: str
    coefficient: int
    offset: int
    allowed: ClosedRange

    def __post_init__(self) -> None:
        validate_revision_key(self.station_id, self.version)
        if type(self.coefficient) is not int or type(self.offset) is not int:
            raise ValueError("coefficient and offset must be integers")
        if not isinstance(self.allowed, ClosedRange):
            raise ValueError("allowed must be a closed interval")

    def normalize(self, raw_value: int) -> int:
        if type(raw_value) is not int:
            raise TypeError("an original value must be an integer")
        return raw_value * self.coefficient + self.offset

    def in_range(self, normalized_value: int) -> bool:
        return self.allowed.contains(normalized_value)
