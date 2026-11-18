from dataclasses import dataclass

from collector.batch import CaptureBuilder, CaptureManifest
from collector.bindings import StationBindings
from portal.pages import FrozenPage


@dataclass(frozen=True, slots=True)
class FrozenEvidence:
    capture_id: str
    export_id: str
    pages: tuple[FrozenPage, ...]
    bindings: StationBindings


def freeze_evidence(manifest: CaptureManifest) -> FrozenEvidence:
    return FrozenEvidence(manifest.capture_id, manifest.export_id, manifest.pages, manifest.bindings)


def rebuild_manifest(evidence: FrozenEvidence) -> CaptureManifest:
    builder = CaptureBuilder(evidence.capture_id, evidence.export_id, evidence.bindings)
    for page in evidence.pages:
        builder.accept_page(page)
    return builder.finish()
