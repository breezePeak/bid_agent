#!/usr/bin/env python3
"""Evaluate current pipeline outputs against V3 Golden fixtures (PR-14.1).

Example:
  python scripts/evaluate_v3_bid_pipeline.py --suite A --layer A1
  python scripts/evaluate_v3_bid_pipeline.py --suite A --layer A1 --actual-json path/to/pred.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.golden import (  # noqa: E402
    DEFAULT_GOLDEN_ROOT,
    GoldenEvalReport,
    GoldenLayer,
    GoldenRegistry,
    GoldenSuite,
    SampleEvalResult,
    SampleStatus,
    aggregate_metrics,
    compare_id_sets,
    write_report,
)


def _load_actual_ids(path: Path | None) -> dict[str, list[str]]:
    """actual-json maps sample_id -> list[requirement_id] for A1 smoke."""
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("actual-json must be an object mapping sample_id to id lists")
    return {str(k): [str(x) for x in (v or [])] for k, v in data.items()}


def evaluate_layer(
    registry: GoldenRegistry,
    *,
    suite: GoldenSuite,
    layer: GoldenLayer,
    actual_ids_by_sample: dict[str, list[str]],
) -> GoldenEvalReport:
    samples = registry.load_layer(suite, layer)
    results: list[SampleEvalResult] = []
    for sample in samples:
        expectation = sample.record.expectation_for(layer)
        if expectation is None:
            continue
        expected_ids = [str(obj.get("requirement_id") or obj.get("id") or "") for obj in expectation.objects]
        expected_ids = [item for item in expected_ids if item]
        actual_ids = actual_ids_by_sample.get(sample.sample_id, [])
        metrics = compare_id_sets(
            expected_ids=expected_ids,
            actual_ids=actual_ids,
            blocking_ids=expectation.blocking_ids,
        )
        # Scaffold/pending samples do not produce Gate A evidence even if scores look high.
        note = None
        if sample.record.status in {SampleStatus.SCAFFOLD, SampleStatus.ANNOTATION_PENDING}:
            note = "sample not expert-accepted; metrics are infrastructure-only"
        results.append(
            SampleEvalResult(
                sample_id=sample.sample_id,
                layer=layer,
                status=sample.record.status,
                metrics=metrics,
                paired_diff=[
                    {"type": "missing", "id": item}
                    for item in sorted(set(expected_ids) - set(actual_ids))
                ]
                + [
                    {"type": "extra", "id": item}
                    for item in sorted(set(actual_ids) - set(expected_ids))
                ],
                notes=note,
            )
        )
    aggregate = aggregate_metrics(results)
    blocked = any(item.metrics.blocking_miss_count > 0 for item in results)
    return GoldenEvalReport(
        report_id=uuid4().hex,
        registry_version=registry.load_manifest().registry_version,
        suite=suite,
        layer=layer,
        samples=results,
        aggregate=aggregate,
        blocked_by_blocking_miss=blocked,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate V3 bid pipeline against Golden fixtures")
    parser.add_argument("--fixtures", type=Path, default=ROOT / DEFAULT_GOLDEN_ROOT)
    parser.add_argument("--suite", choices=[s.value for s in GoldenSuite], default="A")
    parser.add_argument("--layer", choices=[layer.value for layer in GoldenLayer], default="A1")
    parser.add_argument("--actual-json", type=Path, default=None, help="Optional predictions map for offline compare")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "artifacts" / "golden_eval" / "latest_report.json",
        help="Where to write the evaluation report",
    )
    args = parser.parse_args(argv)

    registry = GoldenRegistry(args.fixtures)
    suite = GoldenSuite(args.suite)
    layer = GoldenLayer(args.layer)
    actual = _load_actual_ids(args.actual_json)
    report = evaluate_layer(registry, suite=suite, layer=layer, actual_ids_by_sample=actual)
    report_hash = write_report(args.out, report)

    print(f"suite={suite.value} layer={layer.value}")
    print(f"samples={len(report.samples)}")
    print(f"aggregate_f1={report.aggregate.f1}")
    print(f"blocking_miss_count={report.aggregate.blocking_miss_count}")
    print(f"blocked_by_blocking_miss={report.blocked_by_blocking_miss}")
    print(f"report={args.out}")
    print(f"report_hash={report_hash}")
    # Infrastructure success: loader/report works. Expert Gate A still requires expert_accepted samples.
    expert = [item for item in report.samples if item.status == SampleStatus.EXPERT_ACCEPTED]
    if not expert:
        print("NOTE: no expert_accepted samples; Gate A evidence incomplete by design.")
    return 0 if not report.blocked_by_blocking_miss or not expert else 2


if __name__ == "__main__":
    raise SystemExit(main())
