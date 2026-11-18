from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .errors import InvalidRecord, PageOutOfRange
from .pages import FrozenPage, paginate_column
from .records import COLUMN_ORDER, Column, ObservationInput, parse_column, validate_identifier, validate_observation


@dataclass(frozen=True, slots=True)
class FrozenExport:
    export_id: str
    columns: tuple[tuple[FrozenPage, ...], ...]

    def page(self, column: Column | str, page_number: int) -> FrozenPage:
        selected = parse_column(column)
        pages = self.columns[COLUMN_ORDER.index(selected)]
        if type(page_number) is not int or not 1 <= page_number <= len(pages):
            raise PageOutOfRange(f"{self.export_id}/{selected.value} has no page {page_number!r}")
        return pages[page_number - 1]


def build_export(
    export_id: str,
    columns: Mapping[Column | str, Iterable[ObservationInput]],
    page_size: int,
) -> FrozenExport:
    export_id = validate_identifier(export_id, "export_id")
    if not isinstance(columns, Mapping):
        raise InvalidRecord("columns must map each portal column to its observations")

    inputs: dict[Column, Iterable[ObservationInput]] = {}
    for label, records in columns.items():
        column = parse_column(label)
        if column in inputs:
            raise InvalidRecord(f"column {column.value} was supplied more than once")
        inputs[column] = records
    if inputs.keys() != set(COLUMN_ORDER):
        raise InvalidRecord("a frozen export requires both portal columns")

    frozen_columns = []
    for column in COLUMN_ORDER:
        try:
            records = iter(inputs[column])
        except TypeError as exc:
            raise InvalidRecord(f"column {column.value} must contain an iterable of observations") from exc
        rows = tuple(validate_observation(record) for record in records)
        frozen_columns.append(paginate_column(export_id, column, rows, page_size))
    return FrozenExport(export_id, tuple(frozen_columns))
