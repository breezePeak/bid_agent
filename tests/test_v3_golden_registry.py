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


class GoldenRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = GoldenRegistry(ROOT / "tests" / "fixtures" / "v3_golden")

    def test_manifest_lists_eight_sample_slots(self) -> None:
        manifest = self.registry.load_manifest()
        self.assertEqual(manifest.registry_version, "v3-golden-1")
        self.assertEqual(len(manifest.samples), 8)
        self.assertTrue(manifest.policy.get("historical_92_198_not_threshold"))
        self.assertTrue(manifest.policy.get("scaffold_samples_not_gate_a_evidence"))

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

    def test_layer_loader_returns_a1_samples(self) -> None:
        samples = self.registry.load_layer(GoldenSuite.A, GoldenLayer.A1)
        ids = {sample.sample_id for sample in samples}
        self.assertIn("G-A1-SYN-001", ids)
        self.assertIn("G-A1-SCAN-PLACEHOLDER", ids)

    def test_compare_id_sets_blocking_miss(self) -> None:
        metrics = compare_id_sets(
            expected_ids=["a", "b", "c"],
            actual_ids=["a", "c"],
            blocking_ids=["b"],
        )
        self.assertEqual(metrics.blocking_miss_count, 1)
        self.assertLess(metrics.critical_recall or 0, 1.0)

    def test_evaluate_layer_without_predictions_is_infrastructure_safe(self) -> None:
        report = evaluate_layer(
            self.registry,
            suite=GoldenSuite.A,
            layer=GoldenLayer.A1,
            actual_ids_by_sample={},
        )
        self.assertEqual(report.suite, GoldenSuite.A)
        self.assertEqual(report.layer, GoldenLayer.A1)
        self.assertGreaterEqual(len(report.samples), 1)
        # Synthetic sample has blocking expectations and no actuals => blocking miss recorded.
        syn = next(item for item in report.samples if item.sample_id == "G-A1-SYN-001")
        self.assertGreaterEqual(syn.metrics.blocking_miss_count, 1)
        # No expert_accepted samples yet.
        self.assertFalse(any(item.status == SampleStatus.EXPERT_ACCEPTED for item in report.samples))

    def test_evaluate_script_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            # Provide perfect ids for synthetic sample only.
            actual = {
                "G-A1-SYN-001": ["EXP-R-credit", "EXP-R-duration", "EXP-R-acceptance"],
            }
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
            syn = next(item for item in report["samples"] if item["sample_id"] == "G-A1-SYN-001")
            self.assertEqual(syn["metrics"]["blocking_miss_count"], 0)


if __name__ == "__main__":
    unittest.main()
