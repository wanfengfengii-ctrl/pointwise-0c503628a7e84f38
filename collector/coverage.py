from portal.pages import FrozenPage
from portal.records import COLUMN_ORDER, Column


class PageCoverageError(ValueError):
    """The original pages cannot establish complete frozen coverage."""


class CoverageTracker:
    def __init__(self, export_id: str):
        self._export_id = export_id
        self._next_page = dict.fromkeys(COLUMN_ORDER, 1)
        self._finished: set[Column] = set()

    def next_request(self) -> tuple[Column, int] | None:
        for column in COLUMN_ORDER:
            if column not in self._finished:
                return column, self._next_page[column]
        return None

    def check(self, page: FrozenPage) -> None:
        if not isinstance(page, FrozenPage):
            raise PageCoverageError("the portal did not return a frozen page")
        if page.export_id != self._export_id:
            raise PageCoverageError(f"expected export {self._export_id!r}, received {page.export_id!r}")
        if type(page.column) is not Column or type(page.page_number) is not int or page.page_number < 1:
            raise PageCoverageError("invalid frozen page column or page number")
        if type(page.terminal) is not bool or type(page.rows) is not tuple:
            raise PageCoverageError("invalid frozen page rows or terminal marker")
        requested = self.next_request()
        if requested != (page.column, page.page_number):
            raise PageCoverageError(
                f"expected page request {requested!r}, received {page.column.value}/{page.page_number}"
            )

    def accept(self, page: FrozenPage) -> None:
        self.check(page)
        self._next_page[page.column] += 1
        if page.terminal:
            self._finished.add(page.column)

    def require_complete(self) -> None:
        missing = [column.value for column in COLUMN_ORDER if column not in self._finished]
        if missing:
            raise PageCoverageError(f"missing terminal page for {', '.join(missing)}")
