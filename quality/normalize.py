from dataclasses import dataclass

from collector.batch import CaptureManifest
from collector.provenance import RowLocation
from portal.records import Column


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    observation_id: str
    station_id: str
    location: RowLocation
    normalized_value: int
    in_range: bool

    @property
    def column(self) -> Column:
        return self.location.column


def normalize_observations(manifest: CaptureManifest) -> tuple[NormalizedObservation, ...]:
    observations = []
    for located in manifest.observations:
        original = located.row
        calibration = manifest.bindings.for_station(original.station_id)
        value = calibration.normalize(original.raw_value)
        observations.append(
            NormalizedObservation(
                original.observation_id, original.station_id, located.location, value, calibration.in_range(value)
            )
        )
    return tuple(observations)
