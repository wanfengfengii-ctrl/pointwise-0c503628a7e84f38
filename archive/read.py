from .codec import ArchiveEncodingError, CaptureProjection, decode_projection
from .store import ArchiveStore


class CaptureReader:
    def __init__(self, store: ArchiveStore):
        self._store = store

    def get(self, capture_id: str) -> CaptureProjection:
        projection = decode_projection(self._store.read_projection(capture_id))
        if projection.capture_id != capture_id:
            raise ArchiveEncodingError("stored projection has a different capture identity")
        return projection
