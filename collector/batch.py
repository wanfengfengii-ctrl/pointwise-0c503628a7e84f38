from dataclasses import dataclass, replace

from portal.pages import FrozenPage
from portal.records import Column, validate_identifier, validate_observation

from .bindings import StationBindings
from .coverage import CoverageTracker
from .provenance import LocatedObservation, locate_rows


class DuplicateObservation(ValueError):
    """The same observation identity appears more than once in a frozen capture."""


@dataclass(frozen=True, slots=True)
class CaptureManifest:
    capture_id: str
    export_id: str
    bindings: StationBindings
    pages: tuple[FrozenPage, ...]
    observations: tuple[LocatedObservation, ...]


class CaptureBuilder:
    def __init__(self, capture_id: str, export_id: str, bindings: StationBindings):
        self._capture_id = validate_identifier(capture_id, "capture_id")
        self._export_id = validate_identifier(export_id, "export_id")
        self._bindings = bindings
        self._coverage = CoverageTracker(export_id)
        self._pages: list[FrozenPage] = []
        self._observations: list[LocatedObservation] = []
        self._observation_ids: set[str] = set()

    def next_request(self) -> tuple[Column, int] | None:
        return self._coverage.next_request()

    def accept_page(self, page: FrozenPage) -> None:
        self._coverage.check(page)
        fixed_page = replace(page, rows=tuple(validate_observation(row) for row in page.rows))
        observations = locate_rows(fixed_page)
        page_ids: set[str] = set()
        for located in observations:
            row = located.row
            if row.observation_id in self._observation_ids or row.observation_id in page_ids:
                raise DuplicateObservation(row.observation_id)
            self._bindings.for_station(row.station_id)
            page_ids.add(row.observation_id)

        # Advance coverage only when every original row on this page has been accepted.
        self._coverage.accept(fixed_page)
        self._pages.append(fixed_page)
        self._observations.extend(observations)
        self._observation_ids.update(page_ids)

    def finish(self) -> CaptureManifest:
        self._coverage.require_complete()
        return CaptureManifest(
            self._capture_id,
            self._export_id,
            self._bindings,
            tuple(self._pages),
            tuple(self._observations),
        )
