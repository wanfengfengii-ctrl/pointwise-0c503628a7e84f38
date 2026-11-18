from collector.batch import DuplicateObservation
from collector.bindings import UnboundStation
from collector.coverage import PageCoverageError
from portal.errors import InvalidRecord
from quality.report import build_quality_result

from .codec import ArchiveEncodingError, CaptureProjection, decode_evidence, decode_projection, evidence_digest
from .errors import AuditError
from .evidence import rebuild_manifest
from .site import audit_site_receipts
from .store import ArchiveStore


class CaptureAuditor:
    def __init__(self, store: ArchiveStore):
        self._store = store

    def audit(self, capture_id: str) -> bool:
        stored = self._store.read_capture(capture_id)
        if stored.evidence is None or stored.digest is None:
            raise AuditError(f"missing fixed evidence for {capture_id!r}")
        try:
            if evidence_digest(stored.evidence) != stored.digest:
                raise AuditError(f"fixed evidence digest differs for {capture_id!r}")
            evidence = decode_evidence(stored.evidence)
            if evidence.capture_id != capture_id:
                raise AuditError(f"fixed evidence has a different capture identity for {capture_id!r}")
            manifest = rebuild_manifest(evidence)
            recomputed = CaptureProjection(manifest.capture_id, manifest.export_id, build_quality_result(manifest))
            published = decode_projection(stored.projection)
        except (ArchiveEncodingError, PageCoverageError, DuplicateObservation, UnboundStation, InvalidRecord) as exc:
            raise AuditError(f"invalid audit material for {capture_id!r}: {exc}") from exc
        if recomputed != published:
            raise AuditError(f"recomputed result differs from the published projection for {capture_id!r}")
        audit_site_receipts(self._store, evidence, capture_id)
        return True
