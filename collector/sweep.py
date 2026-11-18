from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

from portal.pages import FrozenPage
from portal.records import Column
from stations.catalog import StationCatalog

from .batch import CaptureBuilder
from .bindings import resolve_bindings

if TYPE_CHECKING:
    from archive.publish import Publisher


class FrozenSource(Protocol):
    def read_page(self, export_id: str, column: Column, page_number: int) -> FrozenPage:
        """Read the requested original page from a frozen export."""
        ...


class Collector:
    def __init__(self, catalog: StationCatalog, publisher: Publisher):
        self._catalog = catalog
        self._publisher = publisher

    def capture_frozen(
        self,
        source: FrozenSource,
        export_id: str,
        capture_id: str,
        versions: Mapping[str, str],
    ) -> str:
        bindings = resolve_bindings(self._catalog, versions)
        builder = CaptureBuilder(capture_id, export_id, bindings)
        while (request := builder.next_request()) is not None:
            column, page_number = request
            page = source.read_page(export_id, column, page_number)
            builder.accept_page(page)
        return self._publisher.publish(builder.finish())
