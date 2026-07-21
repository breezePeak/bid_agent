from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from artifact_manifest import record_stage_artifacts, stage_artifacts_reusable  # noqa: E402
from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402


class ArtifactManifestTests(unittest.TestCase):
    def test_records_ready_artifacts_and_input_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "inputs").mkdir(parents=True)
            (root / "workspace" / "chunks").mkdir(parents=True)
            (root / "inputs" / "tender.md").write_text("tender-v1", encoding="utf-8")
            (root / "inputs" / "company.md").write_text("company-v1", encoding="utf-8")
            (root / "workspace" / "chunks" / "tender_chunks.json").write_text("[]", encoding="utf-8")
            (root / "workspace" / "chunks" / "company_chunks.json").write_text("[]", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")

            first = record_stage_artifacts(context, "split-docs")
            first_fingerprint = first[0]["input_fingerprint"]
            (root / "inputs" / "tender.md").write_text("tender-v2", encoding="utf-8")
            second = record_stage_artifacts(context, "split-docs", disposition="reused")

            self.assertEqual(len(first), 2)
            self.assertTrue(all(item["status"] == "ready" for item in first))
            self.assertTrue(all(item["producer"] == "split-docs" for item in first))
            self.assertTrue(all(item["disposition"] == "reused" for item in second))
            self.assertNotEqual(first_fingerprint, second[0]["input_fingerprint"])
            snapshot = ControlStore(context).snapshot()
            self.assertEqual(len(snapshot["artifacts"]), 2)

    def test_missing_required_output_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "inputs").mkdir(parents=True)
            (root / "workspace" / "chunks").mkdir(parents=True)
            (root / "inputs" / "tender.md").write_text("tender", encoding="utf-8")
            (root / "workspace" / "chunks" / "tender_chunks.json").write_text("[]", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")

            with self.assertRaisesRegex(ControlPlaneError, "必需 Artifact"):
                record_stage_artifacts(context, "split-docs")

            states = ControlStore(context).artifact_states()
            self.assertEqual(states, [])

    def test_changed_upstream_marks_existing_downstream_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "inputs").mkdir(parents=True)
            (root / "workspace" / "chunks").mkdir(parents=True)
            (root / "workspace").mkdir(exist_ok=True)
            (root / "inputs" / "tender.md").write_text("tender-v1", encoding="utf-8")
            (root / "inputs" / "company.md").write_text("company", encoding="utf-8")
            (root / "workspace" / "chunks" / "tender_chunks.json").write_text("[]", encoding="utf-8")
            (root / "workspace" / "chunks" / "company_chunks.json").write_text("[]", encoding="utf-8")
            (root / "workspace" / "template_evidence_map.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "template_quality_report.json").write_text("{}", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)

            record_stage_artifacts(context, "split-docs")
            store.upsert_artifact_state(
                {
                    "artifact_key": "workspace/template_evidence_map.json",
                    "path": "workspace/template_evidence_map.json",
                    "kind": "file",
                    "status": "ready",
                    "sha256": "old",
                    "producer": "build-template-evidence",
                    "input_fingerprint": "old-input",
                }
            )
            (root / "inputs" / "tender.md").write_text("tender-v2", encoding="utf-8")
            record_stage_artifacts(context, "split-docs")

            stale = store.artifact_state("workspace/template_evidence_map.json")
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["stale_source_command"], "split-docs")

    def test_reuse_requires_ready_manifest_with_current_hash_and_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "inputs").mkdir(parents=True)
            (root / "workspace" / "chunks").mkdir(parents=True)
            (root / "inputs" / "tender.md").write_text("tender", encoding="utf-8")
            (root / "inputs" / "company.md").write_text("company", encoding="utf-8")
            (root / "workspace" / "chunks" / "tender_chunks.json").write_text("[]", encoding="utf-8")
            (root / "workspace" / "chunks" / "company_chunks.json").write_text("[]", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")

            self.assertTrue(stage_artifacts_reusable(context, "split-docs"))
            record_stage_artifacts(context, "split-docs")
            self.assertTrue(stage_artifacts_reusable(context, "split-docs"))
            (root / "inputs" / "tender.md").write_text("changed", encoding="utf-8")
            self.assertFalse(stage_artifacts_reusable(context, "split-docs"))


if __name__ == "__main__":
    unittest.main()
