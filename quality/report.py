from dataclasses import dataclass

from collector.batch import CaptureManifest

from .grouping import group_observations
from .normalize import NormalizedObservation, normalize_observations
from .statistics import GroupStatistics, summarize_group


@dataclass(frozen=True, slots=True)
class QualityResult:
    observations: tuple[NormalizedObservation, ...]
    groups: tuple[GroupStatistics, ...]


def build_quality_result(manifest: CaptureManifest) -> QualityResult:
    observations = normalize_observations(manifest)
    groups = group_observations(manifest.bindings, observations)
    return QualityResult(observations, tuple(summarize_group(group) for group in groups))
