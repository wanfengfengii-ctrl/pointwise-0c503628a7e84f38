from collector.batch import CaptureManifest
from collector.coverage import PageCoverageError
from quality.report import build_quality_result

from .codec import CaptureProjection, encode_evidence, encode_projection, evidence_digest
from .evidence import freeze_evidence, rebuild_manifest
from .store import ArchiveStore


class Publisher:
    def __init__(self, store: ArchiveStore):
        self._store = store

    def publish(self, manifest: CaptureManifest) -> str:
        evidence = freeze_evidence(manifest)
        canonical = rebuild_manifest(evidence)
        if canonical != manifest:
            raise PageCoverageError("capture manifest differs from its original pages and bindings")
        projection = CaptureProjection(canonical.capture_id, canonical.export_id, build_quality_result(canonical))
        projection_payload = encode_projection(projection)
        evidence_payload = encode_evidence(evidence)
        self._store.commit_capture(
            projection.capture_id, projection_payload, evidence_payload, evidence_digest(evidence_payload)
        )
        return projection.capture_id
