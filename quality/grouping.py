from dataclasses import dataclass

from collector.bindings import StationBindings
from portal.records import COLUMN_ORDER, Column

from .normalize import NormalizedObservation


@dataclass(frozen=True, slots=True)
class ObservationGroup:
    station_id: str
    column: Column
    observations: tuple[NormalizedObservation, ...]


def group_observations(
    bindings: StationBindings, observations: tuple[NormalizedObservation, ...]
) -> tuple[ObservationGroup, ...]:
    buckets: dict[tuple[str, Column], list[NormalizedObservation]] = {
        (station_id, column): []
        for station_id in bindings.station_ids()
        for column in COLUMN_ORDER
    }
    for observation in observations:
        buckets[(observation.station_id, observation.column)].append(observation)
    return tuple(ObservationGroup(station, column, tuple(rows)) for (station, column), rows in buckets.items())
