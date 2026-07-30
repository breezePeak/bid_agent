from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.artifact_promotion import HumanGateService  # noqa: E402
from document_pipeline.contracts import ChapterBlueprint, InputRole  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.inference_runtime import (  # noqa: E402
    INFERENCE_RUNTIME_REGISTRY,
    InferenceRuntimeMetadata,
)
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402


class V3InferenceStalenessTests(unittest.TestCase):
    def _prepare_to_g2(
        self,
        base: Path,
    ) -> tuple[WorkspaceContext, V3StageRunner]:
        runs = base / "runs"
        (runs / "alpha").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        tender = base / "tender.md"
        tender.write_text(
            "# 项目需求\n"
            "投标人须提供项目实施方案，并在30日内完成交付和验收。\n\n"
            "# 评标办法\n"
            "## 技术评分\n"
            "项目实施方案完整、措施可行，满分10分。\n",
            encoding="utf-8",
        )
        InputManifestService(context).register_local_file(
            tender,
            InputRole.TENDER,
        )
        runner = V3StageRunner.for_deterministic_tests(context)
        for stage in (
            "normalize_sources",
            "build_requirement_ledger",
            "analyze_scores",
            "plan_response",
            "compile_chapter_blueprint",
        ):
            runner.run(stage)
        return context, runner

    def test_inference_metadata_change_invalidates_reuse_and_old_h1(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context, runner = self._prepare_to_g2(Path(tmp))
            store = ControlStore(context)
            active = store.v3_active_artifact("ChapterBlueprint")
            assert active is not None
            proposal = store.v3_proposal(str(active["proposal_id"]))
            assert proposal is not None
            receipt_ref = proposal["inference_receipt_refs"][0]
            receipt = store.v3_inference_receipt(receipt_ref["receipt_id"])
            assert receipt is not None

            current_metadata = {
                "capability_version": str(receipt["capability_version"]),
                "prompt_version": str(receipt["prompt_version"]),
                "prompt_hash": str(receipt["prompt_hash"]),
                "output_schema_version": str(
                    receipt["output_schema_version"]
                ),
                "provider_fingerprint": str(
                    receipt["provider_fingerprint"]
                ),
                "model_fingerprint": str(receipt["model_fingerprint"]),
                "temperature": float(receipt["temperature"]),
            }
            self.assertTrue(
                runner._active_inference_artifact_is_current(
                    "ChapterBlueprint",
                    **current_metadata,
                )
            )
            changed_fields = {
                "prompt_version": "prompt-version-next",
                "prompt_hash": "prompt-checksum-next",
                "provider_fingerprint": "provider-fingerprint-next",
                "model_fingerprint": "model-fingerprint-next",
                "capability_version": "skill-version-next",
                "output_schema_version": "schema-version-next",
                "temperature": 0.6,
            }
            for field_name, changed_value in changed_fields.items():
                with self.subTest(field_name=field_name):
                    metadata = {**current_metadata, field_name: changed_value}
                    self.assertFalse(
                        runner._active_inference_artifact_is_current(
                            "ChapterBlueprint",
                            **metadata,
                        )
                    )
            self.assertTrue(
                runner._active_inference_artifact_is_current(
                    "ChapterBlueprint",
                    **current_metadata,
                )
            )

            store.grant_workspace_access("owner")
            human_gate = HumanGateService(context)
            old_snapshot = human_gate.planning_snapshot()
            old_h1 = human_gate.confirm_planning(
                principal_id="owner",
                submitted_snapshot=old_snapshot,
                nonce="confirm-before-inference-version-change",
            )
            self.assertEqual(
                human_gate.require_current_confirmation().receipt_id,
                old_h1.receipt_id,
            )

            old_blueprint = ChapterBlueprint.model_validate(active["payload"])
            next_blueprint = old_blueprint.model_copy(
                update={"revision": int(active["revision"]) + 1}
            )
            changed_input = json.loads(str(receipt["input_snapshot"]))
            changed_result = runner._deterministic_result(
                capability_id=str(receipt["capability_id"]),
                capability_version=str(receipt["capability_version"]),
                schema_version=str(receipt["output_schema_version"]),
                candidate={"reason": "prompt-and-model-version-change"},
                input_value=changed_input,
                prompt_version="outline-prompt-version-next",
                model_fingerprint="outline-model-fingerprint-next",
            )
            changed_proposal = runner._proposal_from_inference(
                artifact_kind="ChapterBlueprint",
                producer_role="planning_agent",
                payload=next_blueprint,
                base_revision=int(active["revision"]),
                operation_id="blueprint-after-inference-version-change",
                result=changed_result,
                input_snapshot=changed_input,
                capability_version=str(receipt["capability_version"]),
            )
            runner._validate_gate_promote(
                changed_proposal,
                producer_role="planning_agent",
                gate_id="G2_BLUEPRINT_INTEGRITY",
            )

            promoted = store.v3_active_artifact("ChapterBlueprint")
            assert promoted is not None
            self.assertNotEqual(
                promoted["artifact_hash"],
                active["artifact_hash"],
            )
            with self.assertRaises(ControlPlaneError) as no_longer_current:
                human_gate.require_current_confirmation()
            self.assertEqual(
                no_longer_current.exception.code,
                "PLANNING_CONFIRM_REQUIRED",
            )
            with self.assertRaises(ControlPlaneError) as stale_snapshot:
                human_gate.confirm_planning(
                    principal_id="owner",
                    submitted_snapshot=old_snapshot,
                    nonce="reject-old-planning-snapshot",
                )
            self.assertEqual(
                stale_snapshot.exception.code,
                "PLANNING_CONFIRM_STALE",
            )

    def test_g2_without_h1_blocks_writer_execution(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context, runner = self._prepare_to_g2(Path(tmp))
            store = ControlStore(context)
            blueprint = store.v3_active_artifact("ChapterBlueprint")
            assert blueprint is not None
            g2 = store.latest_v3_gate_receipt(
                str(blueprint["proposal_id"]),
                "G2_BLUEPRINT_INTEGRITY",
            )
            assert g2 is not None
            self.assertEqual(g2["verdict"], "pass")

            with self.assertRaises(ControlPlaneError) as blocked:
                runner.run("execute_content_plan")
            self.assertEqual(
                blocked.exception.code,
                "PLANNING_CONFIRM_REQUIRED",
            )
            self.assertFalse(
                any(
                    (
                        context.root
                        / "workspace"
                        / "v3"
                        / "writer_bundles"
                    ).glob("bundle-*.json")
                )
            )

    def test_deployment_inference_policy_change_stales_h1_without_rerun(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context, _ = self._prepare_to_g2(Path(tmp))
            store = ControlStore(context)
            store.grant_workspace_access("owner")
            human_gate = HumanGateService(context)
            snapshot = human_gate.planning_snapshot()
            human_gate.confirm_planning(
                principal_id="owner",
                submitted_snapshot=snapshot,
                nonce="confirm-before-deployment-policy-change",
            )

            registered = INFERENCE_RUNTIME_REGISTRY.metadata(
                context,
                "ChapterBlueprint",
            )
            assert registered is not None
            changed = dict(registered)
            changed["provider_fingerprint"] = (
                "deployment-provider-fingerprint-next"
            )
            INFERENCE_RUNTIME_REGISTRY.publish(
                context,
                "ChapterBlueprint",
                InferenceRuntimeMetadata(**changed),
            )
            with self.assertRaises(ControlPlaneError) as stale:
                human_gate.require_current_confirmation()
            self.assertEqual(
                stale.exception.code,
                "PLANNING_CONFIRM_STALE",
            )


if __name__ == "__main__":
    unittest.main()
