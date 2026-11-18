from collector.batch import CaptureManifest
from collector.coverage import PageCoverageError
from quality.report import build_quality_result

from .codec import CaptureProjection, encode_evidence, encode_projection, evidence_digest
from .evidence import freeze_evidence, rebuild_manifest
from .site import build_site_receipts
from .store import ArchiveStore


class Publisher:
    def __init__(self, store: ArchiveStore):
        self._store = store

    def publish(self, manifest: CaptureManifest) -> str:
        return self._commit(manifest, with_site_receipts=False)

    def publish_with_receipts(self, manifest: CaptureManifest) -> str:
        return self._commit(manifest, with_site_receipts=True)

    def _commit(self, manifest: CaptureManifest, *, with_site_receipts: bool) -> str:
        evidence = freeze_evidence(manifest)
        canonical = rebuild_manifest(evidence)
        if canonical != manifest:
            raise PageCoverageError("capture manifest differs from its original pages and bindings")
        projection = CaptureProjection(canonical.capture_id, canonical.export_id, build_quality_result(canonical))
        projection_payload = encode_projection(projection)
        evidence_payload = encode_evidence(evidence)
        site_receipts = build_site_receipts(evidence) if with_site_receipts else ()
        self._store.commit_capture(
            projection.capture_id,
            projection_payload,
            evidence_payload,
            evidence_digest(evidence_payload),
            site_receipts,
        )
        return projection.capture_id
