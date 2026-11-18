from dataclasses import dataclass

from portal.records import Column

from .grouping import ObservationGroup


@dataclass(frozen=True, slots=True)
class GroupStatistics:
    station_id: str
    column: Column
    count: int
    minimum: int | None
    maximum: int | None
    out_of_range: int


def summarize_group(group: ObservationGroup) -> GroupStatistics:
    values = tuple(observation.normalized_value for observation in group.observations)
    return GroupStatistics(
        group.station_id,
        group.column,
        len(values),
        min(values, default=None),
        max(values, default=None),
        sum(1 for observation in group.observations if not observation.in_range),
    )
