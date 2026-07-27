"""Golden registry, loaders and report contracts for V3 evaluation (PR-14.1).

Git stores only anonymized fixtures, manifests, hashes and evaluation outputs.
Sensitive originals stay outside the repository behind access control.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonicalization import canonical_hash, canonical_json

GOLDEN_SCHEMA_VERSION = "v3-golden-1"
DEFAULT_GOLDEN_ROOT = Path("tests/fixtures/v3_golden")


class GoldenSuite(str, Enum):
    A = "A"  # Requirement / Score / Topic / Blueprint
    B = "B"  # Evidence
    C = "C"  # ContentBlock
    D = "D"  # Integration / Audit


class GoldenLayer(str, Enum):
    """Golden-A internal independent layers."""

    A1 = "A1"  # Source → Requirement
    A2 = "A2"  # Requirement → Score
    A3 = "A3"  # Requirement/Score → Topic/Duty
    A4 = "A4"  # Duty → ChapterBlueprint
    B = "B"
    C = "C"
    D = "D"


class SampleStatus(str, Enum):
    SCAFFOLD = "scaffold"  # infrastructure only; not expert-annotated
    SYNTHETIC = "synthetic"  # synthetic anonymized fixture
    ANNOTATION_PENDING = "annotation_pending"
    BASELINE_FROZEN = "baseline_frozen"
    EXPERT_ACCEPTED = "expert_accepted"


class ErrorTaxonomy(str, Enum):
    MISS = "漏项"
    FALSE_POSITIVE = "误抽"
    SPLIT_ERROR = "拆分错误"
    NEGATION_EXCEPTION = "否定/例外错误"
    AMENDMENT_OVERRIDE = "补遗覆盖错误"
    SCORE_VALUE = "分值错误"
    BAD_BINDING = "错误绑定"
    FABRICATED_ANCHOR = "虚构anchor"
    TOPIC_OVER_SPLIT = "Topic过度拆分"
    TOPIC_OVER_MERGE = "Topic过度合并"
    DUTY_CONTEXT = "Duty上下文错误"
    PRIMARY_CHAPTER = "primary章节错误"
    OTHER = "其他"


class GoldenInputRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    content_sha256: str = Field(min_length=1)
    # External sensitive original is never stored in git.
    external_original_ref: str | None = None


class GoldenExpectation(BaseModel):
    """Layer-specific expected objects. Shape is intentionally open within one layer schema version."""

    model_config = ConfigDict(extra="forbid")

    layer: GoldenLayer
    schema_version: str = Field(default=GOLDEN_SCHEMA_VERSION, min_length=1)
    objects: list[dict[str, Any]] = Field(default_factory=list)
    blocking_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class GoldenAnnotationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    annotator: str | None = None
    reviewer: str | None = None
    adjudicator: str | None = None
    guideline_version: str | None = None
    annotated_at: str | None = None
    adjudicated_at: str | None = None
    agreement_rate: float | None = Field(default=None, ge=0, le=1)
    dispute_rate: float | None = Field(default=None, ge=0, le=1)


class GoldenSampleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sample_id: str = Field(min_length=1)
    suite: GoldenSuite
    layers: list[GoldenLayer] = Field(min_length=1)
    status: SampleStatus
    title: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    input_manifest_hash: str = Field(min_length=1)
    inputs: list[GoldenInputRef] = Field(default_factory=list)
    expectations: list[GoldenExpectation] = Field(default_factory=list)
    annotation: GoldenAnnotationMeta = Field(default_factory=GoldenAnnotationMeta)
    allowed_variants: list[str] = Field(default_factory=list)
    severity_policy_version: str = Field(default="v1", min_length=1)
    notes: str | None = None

    @field_validator("layers")
    @classmethod
    def layers_unique(cls, value: list[GoldenLayer]) -> list[GoldenLayer]:
        if len(value) != len(set(value)):
            raise ValueError("layers 不允许重复")
        return value

    def expectation_for(self, layer: GoldenLayer) -> GoldenExpectation | None:
        for item in self.expectations:
            if item.layer is layer:
                return item
        return None


class GoldenRegistryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    registry_version: str = Field(default=GOLDEN_SCHEMA_VERSION, min_length=1)
    description: str = Field(min_length=1)
    suites: list[GoldenSuite] = Field(min_length=1)
    samples: list[str] = Field(min_length=1)  # sample_id list
    policy: dict[str, Any] = Field(default_factory=dict)


class MetricScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    critical_recall: float | None = None
    anchor_accuracy: float | None = None
    mapping_accuracy: float | None = None
    abstain_rate: float | None = None
    needs_human_rate: float | None = None
    blocking_miss_count: int = 0
    support: int = 0


class SampleEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    layer: GoldenLayer
    status: SampleStatus
    metrics: MetricScores
    error_taxonomy_counts: dict[str, int] = Field(default_factory=dict)
    paired_diff: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


class GoldenEvalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    eval_version: str = Field(default=GOLDEN_SCHEMA_VERSION, min_length=1)
    registry_version: str = Field(min_length=1)
    suite: GoldenSuite
    layer: GoldenLayer | None = None
    samples: list[SampleEvalResult] = Field(default_factory=list)
    aggregate: MetricScores = Field(default_factory=MetricScores)
    blocked_by_blocking_miss: bool = False
    created_at: str

    def report_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json", exclude={"report_id"}))


@dataclass(frozen=True)
class GoldenSample:
    root: Path
    record: GoldenSampleRecord

    @property
    def sample_id(self) -> str:
        return self.record.sample_id


class GoldenRegistry:
    """Load versioned Golden fixtures from an anonymized fixture root."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_GOLDEN_ROOT
        self.manifest_path = self.root / "registry_manifest.json"

    def load_manifest(self) -> GoldenRegistryManifest:
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"Golden registry manifest missing: {self.manifest_path}")
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return GoldenRegistryManifest.model_validate(data)

    def list_sample_ids(self, *, suite: GoldenSuite | None = None) -> list[str]:
        manifest = self.load_manifest()
        if suite is None:
            return list(manifest.samples)
        result: list[str] = []
        for sample_id in manifest.samples:
            sample = self.load_sample(sample_id)
            if sample.record.suite is suite:
                result.append(sample_id)
        return result

    def load_sample(self, sample_id: str) -> GoldenSample:
        sample_dir = self.root / "samples" / sample_id
        record_path = sample_dir / "sample.json"
        if not record_path.is_file():
            raise FileNotFoundError(f"Golden sample missing: {record_path}")
        record = GoldenSampleRecord.model_validate(json.loads(record_path.read_text(encoding="utf-8")))
        if record.sample_id != sample_id:
            raise ValueError(f"sample_id mismatch: dir={sample_id} record={record.sample_id}")
        # Verify declared input hashes when files exist in git.
        for item in record.inputs:
            path = sample_dir / item.relative_path
            if path.is_file():
                digest = _file_sha256(path)
                if digest != item.content_sha256:
                    raise ValueError(
                        f"input hash mismatch for {sample_id}/{item.relative_path}: "
                        f"expected {item.content_sha256}, got {digest}"
                    )
        return GoldenSample(root=sample_dir, record=record)

    def load_suite(self, suite: GoldenSuite) -> list[GoldenSample]:
        return [self.load_sample(sample_id) for sample_id in self.list_sample_ids(suite=suite)]

    def load_layer(self, suite: GoldenSuite, layer: GoldenLayer) -> list[GoldenSample]:
        return [
            sample
            for sample in self.load_suite(suite)
            if layer in sample.record.layers and sample.record.expectation_for(layer) is not None
        ]


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_id_sets(
    *,
    expected_ids: Iterable[str],
    actual_ids: Iterable[str],
    blocking_ids: Iterable[str] | None = None,
) -> MetricScores:
    """Minimal set-based metrics used until expert multi-field matchers land."""
    expected = set(expected_ids)
    actual = set(actual_ids)
    blocking = set(blocking_ids or [])
    true_positive = expected & actual
    false_positive = actual - expected
    false_negative = expected - actual
    precision = (len(true_positive) / len(actual)) if actual else (1.0 if not expected else 0.0)
    recall = (len(true_positive) / len(expected)) if expected else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    blocking_miss = len(blocking & false_negative)
    critical_recall = 1.0 if not blocking else (1.0 - blocking_miss / len(blocking))
    return MetricScores(
        precision=precision,
        recall=recall,
        f1=f1,
        critical_recall=critical_recall,
        blocking_miss_count=blocking_miss,
        support=len(expected),
    )


def aggregate_metrics(results: list[SampleEvalResult]) -> MetricScores:
    if not results:
        return MetricScores()
    # Micro-average over supports for precision/recall/f1 is deferred; report mean of sample metrics.
    def _mean(attr: str) -> float | None:
        values = [getattr(item.metrics, attr) for item in results if getattr(item.metrics, attr) is not None]
        if not values:
            return None
        return sum(values) / len(values)

    return MetricScores(
        precision=_mean("precision"),
        recall=_mean("recall"),
        f1=_mean("f1"),
        critical_recall=_mean("critical_recall"),
        anchor_accuracy=_mean("anchor_accuracy"),
        mapping_accuracy=_mean("mapping_accuracy"),
        abstain_rate=_mean("abstain_rate"),
        needs_human_rate=_mean("needs_human_rate"),
        blocking_miss_count=sum(item.metrics.blocking_miss_count for item in results),
        support=sum(item.metrics.support for item in results),
    )


def write_report(path: Path, report: GoldenEvalReport) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    payload["report_hash"] = report.report_hash()
    text = canonical_json(payload)
    # Pretty file for humans; hash is of canonical dump above via report_hash().
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return payload["report_hash"]
