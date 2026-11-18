from collections.abc import Iterable, Mapping

from .errors import DuplicateExport, InvalidRecord, SourceUnavailable, UnknownExport
from .exports import FrozenExport, build_export
from .pages import FrozenPage
from .records import Column, ObservationInput


class FixturePortal:
    def __init__(self, page_size: int = 2):
        if type(page_size) is not int or page_size < 1:
            raise InvalidRecord("page_size must be a positive integer")
        self._page_size = page_size
        self._exports: dict[str, FrozenExport] = {}
        self._failed_pages: set[tuple[str, Column, int]] = set()
        self._closed = False

    def freeze_export(
        self,
        export_id: str,
        columns: Mapping[Column | str, Iterable[ObservationInput]],
    ) -> str:
        self._require_open()
        if type(export_id) is str and export_id in self._exports:
            raise DuplicateExport(export_id)
        frozen = build_export(export_id, columns, self._page_size)
        self._exports[frozen.export_id] = frozen
        return frozen.export_id

    def read_page(self, export_id: str, column: Column | str, page_number: int) -> FrozenPage:
        page = self._find_page(export_id, column, page_number)
        if (page.export_id, page.column, page.page_number) in self._failed_pages:
            raise SourceUnavailable(f"cannot read {export_id}/{page.column.value}/{page_number}")
        return page

    def fail_page(self, export_id: str, column: Column | str, page_number: int) -> None:
        page = self._find_page(export_id, column, page_number)
        self._failed_pages.add((page.export_id, page.column, page.page_number))

    def close(self) -> None:
        self._closed = True
        self._exports.clear()
        self._failed_pages.clear()

    def _require_open(self) -> None:
        if self._closed:
            raise SourceUnavailable("the portal is closed")

    def _find_page(self, export_id: str, column: Column | str, page_number: int) -> FrozenPage:
        self._require_open()
        if type(export_id) is not str or export_id not in self._exports:
            raise UnknownExport(export_id)
        return self._exports[export_id].page(column, page_number)
