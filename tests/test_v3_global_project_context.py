from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.canonicalization import canonical_payload_hash  # noqa: E402
from document_pipeline.contracts import ProjectFact, ProjectModel  # noqa: E402
from document_pipeline.global_project_context import (  # noqa: E402
    GlobalProjectContextService,
)
from document_pipeline.writer_policy import writer_base_fingerprint  # noqa: E402


def _workspace(base: Path) -> WorkspaceContext:
    runs = base / "runs"
    (runs / "alpha").mkdir(parents=True)
    return WorkspaceContext.resolve(runs, "alpha")


def _seed_project(
    context: WorkspaceContext,
    *,
    revision: int,
    province_count: int,
) -> dict:
    project = ProjectModel(
        schema_version="v3",
        revision=revision,
        source_hashes={"tender": "t" * 64},
        project_id="national-land-change-2026",
        identity={
            "project_name": "2026年度全国国土变更调查监测数据核实处理项目",
            "purchaser": "中国国土勘测规划院",
            "project_no": "GTKC-2026-02",
        },
        background=["面向年度全国国土变更调查监测数据开展国家级核实处理。"],
        goals=["形成可复核、可验收的国家级核查成果。"],
        scope=[f"覆盖全国{province_count}个省级区域。"],
        work_packages=[
            "完成数据接收、任务分发、国家级内外业核查、质量控制及成果复核。"
        ],
        inputs=["省级调查监测成果数据。"],
        processing=["开展国家级内业核查和外业核查。"],
        outputs=["形成质量控制记录和成果复核结果。"],
        deliverables=["提交国家级核查成果。"],
        acceptance_conditions=["成果通过采购人组织的复核验收。"],
        confirmed_facts=[
            ProjectFact(
                fact_id="PF-SCOPE",
                statement=f"项目核查范围覆盖全国{province_count}个省级区域。",
            ),
            ProjectFact(
                fact_id="PF-TASK",
                statement="项目包括数据接收、任务分发、国家级内外业核查、质量控制及成果复核。",
            ),
        ],
    )
    payload = project.model_dump(mode="json")
    artifact_hash = canonical_payload_hash(payload)
    proposal_id = f"prop-project-{revision}-{uuid.uuid4()}"
    proposal_hash = canonical_payload_hash(
        {"proposal_id": proposal_id, "artifact_hash": artifact_hash}
    )
    now = f"2026-08-02T00:00:0{revision}.000+00:00"
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    store = ControlStore(context)
    with store._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO v3_proposals(
                proposal_id, workspace_id, artifact_kind, producer_role,
                operation_id, base_revision, dependency_fingerprint,
                declared_dependencies_json, proposal_hash,
                canonical_payload_hash, payload_json, cited_source_ids_json,
                prompt_version, model_fingerprint, status, created_at
            ) VALUES (?, ?, 'ProjectModel', 'planning_agent', ?, ?, 'fp-test',
                      '[]', ?, ?, ?, '[]', 'test', 'test', 'promoted', ?)
            """,
            (
                proposal_id,
                context.workspace_id,
                f"op-project-{revision}-{uuid.uuid4()}",
                revision - 1,
                proposal_hash,
                artifact_hash,
                encoded,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO v3_artifact_revisions(
                artifact_kind, revision, artifact_id, artifact_hash,
                payload_json, producer_role, dependency_fingerprint,
                proposal_id, proposal_hash, created_at
            ) VALUES ('ProjectModel', ?, ?, ?, ?, 'planning_agent',
                      'fp-test', ?, ?, ?)
            """,
            (
                revision,
                f"ProjectModel@{revision}",
                artifact_hash,
                encoded,
                proposal_id,
                proposal_hash,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO v3_active_artifacts(
                artifact_kind, artifact_id, revision, updated_at
            ) VALUES ('ProjectModel', ?, ?, ?)
            ON CONFLICT(artifact_kind) DO UPDATE SET
                artifact_id = excluded.artifact_id,
                revision = excluded.revision,
                updated_at = excluded.updated_at
            """,
            (f"ProjectModel@{revision}", revision, now),
        )
        connection.commit()
    return payload


class GlobalProjectContextTests(unittest.TestCase):
    def test_prompt_projection_uses_exact_goal_and_excludes_zero_relevance_facts(self) -> None:
        goal = "交代项目任务所处背景、现实情境及任务由来，帮助评审理解项目实施基础。"
        projected = GlobalProjectContextService.prompt_projection(
            {
                "global_context_id": "PM-1",
                "global_context_revision": 1,
                "global_context_hash": "a" * 64,
                "project_id": "P-1",
                "identity": {"project_name": "测试项目", "purchaser": "某采购人"},
                "scope": ["依据采购人安排接收资料"],
                "work_packages": ["完成任务分发和人员考试"],
                "constraints": ["项目组人员必须驻场"],
                "confirmed_facts": [
                    {"fact_id": "PF-PURCHASER", "statement": "依据采购人安排接收资料并完成任务分发。"},
                    {"fact_id": "PF-STAFF", "statement": "项目组人员通过考试后驻场。"},
                ],
            },
            {"highlighted_fact_ids": ["PF-PURCHASER", "PF-STAFF"]},
            purpose=goal,
            writing_objectives=["清楚说明项目任务背景及任务由来。"],
            scoring_requirements=[],
        )
        self.assertEqual(projected["confirmed_facts"], [])
        self.assertEqual(projected["selected_fact_ids"], [])
        self.assertNotIn("scope", projected)
        self.assertNotIn("work_packages", projected)
        self.assertNotIn("constraints", projected)
        self.assertNotIn("purchaser", projected["identity"])

    def test_prompt_projection_keeps_canonical_goal_facts_for_goal_writing(self) -> None:
        projected = GlobalProjectContextService.prompt_projection(
            {
                "identity": {"project_name": "测试项目"},
                "goals": ["完成成果核查与复核，保障成果符合统一技术标准。"],
                "scope": ["覆盖全国县级调查区域。"],
                "work_packages": ["开展内外业核查质量控制。"],
                "confirmed_facts": [],
            },
            {},
            purpose="明确项目工作目标及实施边界。",
            writing_objectives=["提出可实施、可检验的工作目标。"],
            scoring_requirements=[],
        )

        self.assertEqual(
            projected["goals"],
            ["完成成果核查与复核，保障成果符合统一技术标准。"],
        )
        self.assertEqual(projected["scope"], ["覆盖全国县级调查区域。"])
        self.assertNotIn("work_packages", projected)

    def test_all_chapters_share_one_global_version_without_fact_copies(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_project(context, revision=1, province_count=31)
            service = GlobalProjectContextService(context)
            background = service.build_chapter_context(
                "background",
                chapter_context_items=[
                    {"body": "重点说明国家级核查任务。"}
                ],
            )
            quality = service.build_chapter_context(
                "quality",
                chapter_context_items=[
                    {"body": "重点说明质量控制及成果复核。"}
                ],
            )
            self.assertEqual(
                (
                    background["global_context_id"],
                    background["global_context_revision"],
                    background["global_context_hash"],
                ),
                (
                    quality["global_context_id"],
                    quality["global_context_revision"],
                    quality["global_context_hash"],
                ),
            )
            self.assertNotIn("scope", background)
            self.assertNotIn("confirmed_facts", quality)

    def test_new_global_revision_is_seen_by_every_new_chapter_context(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_project(context, revision=1, province_count=31)
            first = GlobalProjectContextService(context).build_chapter_context(
                "technical"
            )
            _seed_project(context, revision=2, province_count=32)
            service = GlobalProjectContextService(context)
            second = service.build_chapter_context("technical")
            third = service.build_chapter_context("quality")
            self.assertEqual(second["global_context_revision"], 2)
            self.assertEqual(
                second["global_context_hash"], third["global_context_hash"]
            )
            self.assertNotEqual(
                first["global_context_hash"], second["global_context_hash"]
            )
            self.assertIn("全国32个省级区域", service.load()["scope"][0])

    def test_chapter_cannot_override_identity_or_shared_scope(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_project(context, revision=1, province_count=31)
            service = GlobalProjectContextService(context)
            for body in (
                "项目名称：另一个采购项目。",
                "采购人：某省自然资源厅。",
                "任务范围：覆盖全国30个省级区域。",
            ):
                with self.subTest(body=body):
                    with self.assertRaises(ControlPlaneError) as caught:
                        service.build_chapter_context(
                            "background",
                            chapter_context_items=[{"body": body}],
                        )
                    self.assertEqual(caught.exception.code, "CHAPTER_CONTEXT_CONFLICT")

    def test_missing_promoted_project_model_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            with self.assertRaises(ControlPlaneError) as caught:
                GlobalProjectContextService(context).load()
            self.assertEqual(
                caught.exception.code,
                "GLOBAL_PROJECT_CONTEXT_REQUIRED",
            )

    def test_project_model_hash_participates_in_writer_freshness(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            store = mock.Mock()
            hashes = {"ProjectModel": "project-v1"}
            store.v3_active_artifact.side_effect = lambda kind: (
                {"artifact_hash": hashes[kind]} if kind in hashes else None
            )
            with mock.patch(
                "document_pipeline.writer_policy.ControlStore",
                return_value=store,
            ):
                first = writer_base_fingerprint(
                    context,
                    unit_id="CU-1",
                    contract_revision=1,
                    node_ids=["chapter-a"],
                    deterministic_test=True,
                )
                hashes["ProjectModel"] = "project-v2"
                second = writer_base_fingerprint(
                    context,
                    unit_id="CU-1",
                    contract_revision=1,
                    node_ids=["chapter-a"],
                    deterministic_test=True,
                )
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
