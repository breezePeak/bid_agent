from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from artifact_manifest import (  # noqa: E402
    record_document_edit_artifacts,
    record_external_chapter_mutation,
    record_stage_artifacts,
    stage_artifacts_reusable,
)
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
            store = ControlStore(context)
            store.record_stage_run("pipeline-1", "split-docs", "succeeded", disposition="produced")
            self.assertTrue(stage_artifacts_reusable(context, "split-docs"))
            (root / "inputs" / "tender.md").write_text("changed", encoding="utf-8")
            self.assertFalse(stage_artifacts_reusable(context, "split-docs"))

    def test_reuse_rejects_manifest_without_successful_stage_run(self) -> None:
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

            record_stage_artifacts(context, "split-docs")
            self.assertFalse(stage_artifacts_reusable(context, "split-docs"))
            ControlStore(context).record_stage_run("pipeline-1", "split-docs", "failed", disposition="runner_failed")
            self.assertFalse(stage_artifacts_reusable(context, "split-docs"))
            ControlStore(context).record_stage_run("pipeline-2", "split-docs", "succeeded", disposition="produced")
            self.assertTrue(stage_artifacts_reusable(context, "split-docs"))

    def test_document_edit_refreshes_final_manifests_and_stales_quality_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "workspace" / "chapters").mkdir(parents=True)
            (root / "outputs").mkdir(parents=True)
            (root / "inputs").mkdir(parents=True)
            (root / "workspace" / "chapters" / "01.md").write_text("chapter", encoding="utf-8")
            (root / "workspace" / "outline.json").write_text("{}", encoding="utf-8")
            (root / "outputs" / "final.md").write_text("edited", encoding="utf-8")
            (root / "outputs" / "final.docx").write_bytes(b"docx")
            (root / "workspace" / "compliance_report.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "format_check_report.json").write_text("{}", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            for key in ("workspace/compliance_report.json", "workspace/format_check_report.json"):
                store.upsert_artifact_state(
                    {
                        "artifact_key": key,
                        "path": key,
                        "kind": "file",
                        "status": "ready",
                        "producer": "quality-test",
                        "sha256": "old",
                        "input_fingerprint": "old",
                    }
                )

            record_document_edit_artifacts(context)

            self.assertEqual(store.artifact_state("outputs/final.md")["disposition"], "manual_override")
            self.assertEqual(store.artifact_state("outputs/final.docx")["status"], "ready")
            self.assertEqual(store.artifact_state("workspace/compliance_report.json")["status"], "stale")
            self.assertEqual(store.artifact_state("workspace/format_check_report.json")["status"], "stale")

    def test_external_chapter_mutation_refreshes_chapters_and_stales_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            root = runs / "alpha"
            (root / "workspace" / "contexts").mkdir(parents=True)
            (root / "workspace" / "chapters").mkdir(parents=True)
            (root / "workspace" / "contexts" / "01_context.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "chapters" / "01.md").write_text("new chapter", encoding="utf-8")
            (root / "outputs").mkdir(parents=True)
            (root / "outputs" / "final.md").write_text("old final", encoding="utf-8")
            context = WorkspaceContext.resolve(runs, "alpha")
            store = ControlStore(context)
            store.upsert_artifact_state(
                {
                    "artifact_key": "outputs/final.md",
                    "path": "outputs/final.md",
                    "kind": "file",
                    "status": "ready",
                    "producer": "build-md",
                    "sha256": "old",
                    "input_fingerprint": "old",
                }
            )

            recorded = record_external_chapter_mutation(context, disposition="chapter_rewrite")

            self.assertEqual(recorded[0]["disposition"], "chapter_rewrite")
            self.assertEqual(store.artifact_state("workspace/chapters/*.md")["status"], "ready")
            self.assertEqual(store.artifact_state("outputs/final.md")["status"], "stale")


if __name__ == "__main__":
    unittest.main()
