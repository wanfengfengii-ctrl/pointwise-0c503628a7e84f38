from dataclasses import dataclass

from portal.pages import FrozenPage
from portal.records import Column, RawObservation


@dataclass(frozen=True, slots=True)
class RowLocation:
    export_id: str
    column: Column
    page_number: int
    row_number: int


@dataclass(frozen=True, slots=True)
class LocatedObservation:
    row: RawObservation
    location: RowLocation


def locate_rows(page: FrozenPage) -> tuple[LocatedObservation, ...]:
    return tuple(
        LocatedObservation(row, RowLocation(page.export_id, page.column, page.page_number, position))
        for position, row in enumerate(page.rows, start=1)
    )
