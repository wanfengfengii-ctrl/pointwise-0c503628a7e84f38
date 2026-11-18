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
        return self._capture(source, export_id, capture_id, versions, with_site_receipts=False)

    def capture_frozen_with_receipts(
        self,
        source: FrozenSource,
        export_id: str,
        capture_id: str,
        versions: Mapping[str, str],
    ) -> str:
        """Collect the whole frozen order and sign a receipt for every bound station atomically."""
        return self._capture(source, export_id, capture_id, versions, with_site_receipts=True)

    def _capture(
        self,
        source: FrozenSource,
        export_id: str,
        capture_id: str,
        versions: Mapping[str, str],
        *,
        with_site_receipts: bool,
    ) -> str:
        bindings = resolve_bindings(self._catalog, versions)
        builder = CaptureBuilder(capture_id, export_id, bindings)
        while (request := builder.next_request()) is not None:
            column, page_number = request
            page = source.read_page(export_id, column, page_number)
            builder.accept_page(page)
        manifest = builder.finish()
        if with_site_receipts:
            return self._publisher.publish_with_receipts(manifest)
        return self._publisher.publish(manifest)
