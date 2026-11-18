from collector.bindings import UnboundStation

from .codec import ArchiveEncodingError, CaptureProjection, decode_evidence, decode_projection, evidence_digest
from .errors import AuditError
from .site import (
    SiteReceiptUnavailable,
    build_site_package,
    decode_site_receipt,
    evidence_digest_binding,
)
from .store import ArchiveStore


class CaptureReader:
    def __init__(self, store: ArchiveStore):
        self._store = store

    def get(self, capture_id: str) -> CaptureProjection:
        projection = decode_projection(self._store.read_projection(capture_id))
        if projection.capture_id != capture_id:
            raise ArchiveEncodingError("stored projection has a different capture identity")
        return projection

    def get_site_receipt(self, capture_id: str, station_id: str) -> bytes:
        return self._load_signed(capture_id, station_id)[1]

    def get_site_package(self, capture_id: str, station_id: str) -> dict:
        evidence, receipt = self._load_signed(capture_id, station_id)
        return build_site_package(evidence, station_id, receipt)

    def _load_signed(self, capture_id: str, station_id: str):
        # An unknown capture keeps its dedicated lookup error before any station check.
        stored = self._store.read_capture(capture_id)
        if not self._store.is_signed(capture_id):
            raise SiteReceiptUnavailable(
                f"capture {capture_id!r} was published without single-station receipts"
            )
        if stored.evidence is None or stored.digest is None:
            raise AuditError(f"missing fixed evidence for {capture_id!r}")
        if evidence_digest(stored.evidence) != stored.digest:
            raise AuditError(f"fixed evidence digest differs for {capture_id!r}")
        try:
            evidence = decode_evidence(stored.evidence)
        except ArchiveEncodingError as exc:
            raise AuditError(f"invalid fixed evidence for {capture_id!r}: {exc}") from exc
        if evidence.capture_id != capture_id:
            raise AuditError(f"fixed evidence has a different capture identity for {capture_id!r}")
        try:
            evidence.bindings.for_station(station_id)
        except UnboundStation:
            raise
        record = self._store.read_site_receipt(capture_id, station_id)
        if record is None:
            raise AuditError(f"missing fixed station receipt for {capture_id!r}/{station_id!r}")
        payload, digest = record
        if evidence_digest(payload) != digest:
            raise AuditError(f"fixed station receipt digest differs for {capture_id!r}/{station_id!r}")
        try:
            receipt = decode_site_receipt(payload)
        except AuditError:
            raise
        if receipt["capture_id"] != capture_id or receipt["station_id"] != station_id:
            raise AuditError(f"station receipt identity differs for {capture_id!r}/{station_id!r}")
        if receipt["export_id"] != evidence.export_id:
            raise AuditError(f"station receipt export differs for {capture_id!r}/{station_id!r}")
        if receipt["evidence_digest"] != evidence_digest_binding(evidence):
            raise AuditError(f"station receipt is not bound to the fixed evidence for {capture_id!r}/{station_id!r}")
        return evidence, payload
