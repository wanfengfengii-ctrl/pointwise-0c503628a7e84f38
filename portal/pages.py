from dataclasses import dataclass

from .errors import InvalidRecord
from .records import Column, RawObservation


@dataclass(frozen=True, slots=True)
class FrozenPage:
    export_id: str
    column: Column
    page_number: int
    rows: tuple[RawObservation, ...]
    terminal: bool


def paginate_column(
    export_id: str,
    column: Column,
    rows: tuple[RawObservation, ...],
    page_size: int,
) -> tuple[FrozenPage, ...]:
    if type(page_size) is not int or page_size < 1:
        raise InvalidRecord("page_size must be a positive integer")
    if not rows:
        return (FrozenPage(export_id, column, 1, (), True),)

    pages = []
    for page_number, start in enumerate(range(0, len(rows), page_size), start=1):
        stop = start + page_size
        pages.append(FrozenPage(export_id, column, page_number, rows[start:stop], stop >= len(rows)))
    return tuple(pages)
