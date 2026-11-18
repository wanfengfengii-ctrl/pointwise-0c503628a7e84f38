from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from stations.calibration import StationRevision
from stations.catalog import StationCatalog


class UnboundStation(ValueError):
    """An original observation names a station without a capture binding."""


@dataclass(frozen=True, slots=True)
class StationBindings:
    revisions: tuple[StationRevision, ...]
    _indexed: Mapping[str, StationRevision] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_station: dict[str, StationRevision] = {}
        for revision in self.revisions:
            if not isinstance(revision, StationRevision):
                raise ValueError("each station binding requires a station revision")
            if revision.station_id in by_station:
                raise ValueError(f"duplicate station binding {revision.station_id!r}")
            by_station[revision.station_id] = revision
        ordered = dict(sorted(by_station.items()))
        object.__setattr__(self, "revisions", tuple(ordered.values()))
        object.__setattr__(self, "_indexed", MappingProxyType(ordered))

    def for_station(self, station_id: str) -> StationRevision:
        try:
            return self._indexed[station_id]
        except KeyError as exc:
            raise UnboundStation(f"no bound revision for station {station_id!r}") from exc

    def station_ids(self) -> tuple[str, ...]:
        return tuple(self._indexed)


def resolve_bindings(catalog: StationCatalog, versions: Mapping[str, str]) -> StationBindings:
    if not isinstance(versions, Mapping):
        raise TypeError("versions must map station IDs to explicit revision IDs")
    requested = tuple(versions.items())
    return StationBindings(tuple(catalog.get_revision(station_id, version) for station_id, version in requested))
