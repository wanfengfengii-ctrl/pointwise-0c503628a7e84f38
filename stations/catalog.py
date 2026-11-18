from os import PathLike

from .calibration import StationRevision
from .ranges import ClosedRange
from .repository import StationRepository


class StationCatalog:
    def __init__(self, path: str | PathLike[str]):
        self._repository = StationRepository(path)

    def initialize(self) -> None:
        self._repository.initialize()

    def register_station(
        self, station_id: str, version: str, coefficient: int, offset: int, lower: int, upper: int
    ) -> StationRevision:
        revision = StationRevision(station_id, version, coefficient, offset, ClosedRange(lower, upper))
        return self._repository.put_revision(revision)

    def get_revision(self, station_id: str, version: str) -> StationRevision:
        return self._repository.get_revision(station_id, version)

    def close(self) -> None:
        self._repository.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
