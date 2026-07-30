"""PR-14.1 Golden registry / loader / evaluation infrastructure tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.golden import (  # noqa: E402
    GoldenLayer,
    GoldenRegistry,
    GoldenSuite,
    SampleStatus,
    compare_id_sets,
)

# scripts/ is not a package; load evaluate module by path.
import importlib.util  # noqa: E402

_EVAL_PATH = ROOT / "scripts" / "evaluate_v3_bid_pipeline.py"
_SPEC = importlib.util.spec_from_file_location("evaluate_v3_bid_pipeline", _EVAL_PATH)
assert _SPEC and _SPEC.loader
_EVAL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EVAL)
evaluate_layer = _EVAL.evaluate_layer
evaluate_main = _EVAL.main

EXPERT_SAMPLE_IDS = {
    "G-A-SOFT-001",
    "G-A-OPS-001",
    "G-A-SI-001",
    "G-A-SCORE-FILE-001",
    "G-A-AMEND-001",
    "G-A-TABLE-001",
    "G-A-TEMPLATE-001",
    "G-A-COMPLEX-SCORE-001",
}


class GoldenRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = GoldenRegistry(ROOT / "tests" / "fixtures" / "v3_golden")

    def test_manifest_lists_expert_and_edge_case_samples(self) -> None:
        manifest = self.registry.load_manifest()
        self.assertEqual(manifest.registry_version, "v3-golden-1")
        self.assertEqual(len(manifest.samples), 9)
        self.assertTrue(EXPERT_SAMPLE_IDS.issubset(set(manifest.samples)))
        self.assertIn("G-A1-SCAN-PLACEHOLDER", manifest.samples)
        self.assertTrue(manifest.policy.get("historical_92_198_not_threshold"))
        self.assertTrue(manifest.policy.get("scaffold_samples_not_gate_a_evidence"))
        self.assertTrue(manifest.policy.get("gate_a_counts_only_expert_accepted"))

    def test_load_synthetic_a1_sample_and_verify_input_hash(self) -> None:
        sample = self.registry.load_sample("G-A1-SYN-001")
        self.assertEqual(sample.record.suite, GoldenSuite.A)
        self.assertEqual(sample.record.status, SampleStatus.SYNTHETIC)
        self.assertIn(GoldenLayer.A1, sample.record.layers)
        exp = sample.record.expectation_for(GoldenLayer.A1)
        self.assertIsNotNone(exp)
        assert exp is not None
        self.assertGreaterEqual(len(exp.objects), 2)
        self.assertTrue(exp.blocking_ids)

    def test_expert_accepted_samples_load_with_dual_annotation(self) -> None:
        for sample_id in sorted(EXPERT_SAMPLE_IDS):
            sample = self.registry.load_sample(sample_id)
            self.assertEqual(sample.record.status, SampleStatus.EXPERT_ACCEPTED)
            self.assertIn(GoldenLayer.A1, sample.record.layers)
            self.assertIn(GoldenLayer.A2, sample.record.layers)
            self.assertIn(GoldenLayer.A3, sample.record.layers)
            self.assertIsNotNone(sample.record.annotation.annotator)
            self.assertIsNotNone(sample.record.annotation.reviewer)
            a1 = sample.record.expectation_for(GoldenLayer.A1)
            assert a1 is not None
            self.assertGreaterEqual(len(a1.objects), 2)
            self.assertTrue(a1.blocking_ids)

    def test_layer_loader_returns_a1_samples(self) -> None:
        samples = self.registry.load_layer(GoldenSuite.A, GoldenLayer.A1)
        ids = {sample.sample_id for sample in samples}
        self.assertTrue(EXPERT_SAMPLE_IDS.issubset(ids))
        self.assertNotIn("G-A1-SYN-001", ids)
        self.assertIn("G-A1-SCAN-PLACEHOLDER", ids)

    def test_compare_id_sets_blocking_miss(self) -> None:
        metrics = compare_id_sets(
            expected_ids=["a", "b", "c"],
            actual_ids=["a", "c"],
            blocking_ids=["b"],
        )
        self.assertEqual(metrics.blocking_miss_count, 1)
        self.assertLess(metrics.critical_recall or 0, 1.0)

    def test_evaluate_layer_without_predictions_marks_expert_blocking_miss(self) -> None:
        report = evaluate_layer(
            self.registry,
            suite=GoldenSuite.A,
            layer=GoldenLayer.A1,
            actual_ids_by_sample={},
        )
        self.assertEqual(report.suite, GoldenSuite.A)
        self.assertEqual(report.layer, GoldenLayer.A1)
        self.assertGreaterEqual(len(report.samples), len(EXPERT_SAMPLE_IDS))
        expert_rows = [item for item in report.samples if item.status == SampleStatus.EXPERT_ACCEPTED]
        self.assertGreaterEqual(len(expert_rows), len(EXPERT_SAMPLE_IDS))
        self.assertTrue(any(item.metrics.blocking_miss_count >= 1 for item in expert_rows))
        self.assertTrue(report.blocked_by_blocking_miss)

    def test_evaluate_script_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            actual = {}
            for sample in self.registry.load_layer(
                GoldenSuite.A,
                GoldenLayer.A1,
            ):
                expectation = sample.record.expectation_for(GoldenLayer.A1)
                assert expectation is not None
                actual[sample.sample_id] = [
                    str(
                        item.get("requirement_id")
                        or item.get("id")
                        or ""
                    )
                    for item in expectation.objects
                    if item.get("requirement_id") or item.get("id")
                ]
            actual_path = Path(tmp) / "actual.json"
            actual_path.write_text(json.dumps(actual), encoding="utf-8")
            code = evaluate_main(
                [
                    "--fixtures",
                    str(ROOT / "tests" / "fixtures" / "v3_golden"),
                    "--suite",
                    "A",
                    "--layer",
                    "A1",
                    "--actual-json",
                    str(actual_path),
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("report_hash", report)
            expert = next(
                item
                for item in report["samples"]
                if item["sample_id"] in EXPERT_SAMPLE_IDS
            )
            self.assertEqual(
                expert["metrics"]["blocking_miss_count"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
