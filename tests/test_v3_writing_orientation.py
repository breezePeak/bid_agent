"""Writing orientation: purpose, document position, chapter relations."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.canonicalization import canonical_payload_hash  # noqa: E402
from document_pipeline.chapter_workspace import ChapterWorkspaceService  # noqa: E402
from document_pipeline.contracts import (  # noqa: E402
    BlueprintNode,
    ChapterBlueprint,
    DocumentMode,
)
from document_pipeline.document_outline_context import (  # noqa: E402
    DocumentOutlineContextService,
)
from document_pipeline.sibling_chapter_context import (  # noqa: E402
    SiblingChapterContextService,
)
from document_pipeline.writing_orientation import (  # noqa: E402
    WritingOrientationService,
    compact_orientation_for_prompt,
    public_orientation_view,
)


def _workspace(base: Path, workspace_id: str = "alpha") -> WorkspaceContext:
    runs = base / "runs"
    (runs / workspace_id).mkdir(parents=True)
    return WorkspaceContext.resolve(runs, workspace_id)


def hashlib_sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_blueprint(context: WorkspaceContext, nodes: list[BlueprintNode]) -> None:
    blueprint = ChapterBlueprint(
        schema_version="v3",
        revision=1,
        source_hashes={},
        blueprint_id="bp-orient",
        mode=DocumentMode.AUTO_OUTLINE,
        planning_model="score_direct",
        requirement_ledger_revision=1,
        score_model_revision=1,
        nodes=nodes,
        assignments=[],
    )
    payload = blueprint.model_dump(mode="json")
    artifact_hash = canonical_payload_hash(payload)
    proposal_id = f"prop-bp-{uuid.uuid4()}"
    proposal_hash = hashlib_sha(proposal_id + artifact_hash)
    now = "2026-08-13T00:00:00.000+00:00"
    store = ControlStore(context)
    with store._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO v3_proposals(
                    proposal_id, workspace_id, artifact_kind, producer_role, operation_id,
                    base_revision, dependency_fingerprint, declared_dependencies_json,
                    proposal_hash, canonical_payload_hash, payload_json, cited_source_ids_json,
                    prompt_version, model_fingerprint, status, created_at
                ) VALUES (?, ?, 'ChapterBlueprint', 'planning_agent', ?, 0, 'fp-test', '[]',
                          ?, ?, ?, '[]', 'test', 'test', 'promoted', ?)
                """,
                (
                    proposal_id,
                    context.workspace_id,
                    f"op-bp-{uuid.uuid4()}",
                    proposal_hash,
                    artifact_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO v3_artifact_revisions(
                    artifact_kind, revision, artifact_id, artifact_hash, payload_json,
                    producer_role, dependency_fingerprint, proposal_id, proposal_hash, created_at
                ) VALUES ('ChapterBlueprint', 1, ?, ?, ?, 'planning_agent', 'fp-test', ?, ?, ?)
                """,
                (
                    "ChapterBlueprint@1",
                    artifact_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    proposal_id,
                    proposal_hash,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO v3_active_artifacts(artifact_kind, artifact_id, revision, updated_at)
                VALUES ('ChapterBlueprint', ?, 1, ?)
                ON CONFLICT(artifact_kind) DO UPDATE SET
                    artifact_id = excluded.artifact_id,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at
                """,
                ("ChapterBlueprint@1", now),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _route_nodes() -> list[BlueprintNode]:
    return [
        BlueprintNode(
            chapter_id="parent-route",
            order=0,
            title="技术路线",
            purpose="组织技术路线相关响应",
            content_policy="structural_only",
        ),
        BlueprintNode(
            chapter_id="ch-overview",
            parent_chapter_id="parent-route",
            order=1,
            title="总体技术路线",
            purpose="给出核查质量控制的完整阶段框架",
            writing_objectives=["给出阶段骨架"],
            score_point_ids=["SP-1"],
            primary_response_unit_ids=["RU-1"],
        ),
        BlueprintNode(
            chapter_id="ch-method",
            parent_chapter_id="parent-route",
            order=2,
            title="关键技术方法",
            purpose="展开各阶段核心技术方法",
            score_point_ids=["SP-1"],
            supporting_response_unit_ids=["RU-1"],
        ),
        BlueprintNode(
            chapter_id="ch-diagram",
            parent_chapter_id="parent-route",
            order=3,
            title="技术路线图",
            purpose="以图呈现总体技术路线阶段与节点",
            writing_objectives=["只画阶段节点，不写方法细则"],
        ),
    ]


class WritingOrientationTests(unittest.TestCase):
    def test_diagram_orientation_has_purpose_position_and_relations(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _route_nodes())
            ChapterWorkspaceService(context).create(
                chapter_id="ch-diagram",
                expected_chapter_revision=0,
            )
            chapter = ChapterWorkspaceService(context).get_chapter("ch-diagram")
            outline = DocumentOutlineContextService(context).build_for_chapter(chapter)
            sibling = SiblingChapterContextService(context).build_for_chapter(chapter)
            payload = WritingOrientationService(context).build_for_chapter(
                chapter,
                outline_context=outline,
                sibling_context=sibling,
                tender_requirements=[{"requirement_id": "R-1", "text": "应给出技术路线"}],
                scoring_requirements=[{"score_point_id": "SP-1", "title": "技术路线"}],
            )

            purpose = payload["writing_purpose"]
            self.assertEqual(purpose["title"], "技术路线图")
            self.assertIn("以图呈现", purpose["purpose"])
            self.assertEqual(purpose["role"], "visual")
            self.assertTrue(purpose["is_leaf"])
            self.assertIn("只画阶段节点", purpose["writing_objectives"][0])

            position = payload["document_position"]
            self.assertIn("技术路线", position["path_label"])
            self.assertIn("技术路线图", position["path_label"])
            self.assertEqual(position["parent_chapter_id"], "parent-route")

            relations = payload["chapter_relations"]["items"]
            titles = {item["title"]: item for item in relations}
            self.assertIn("技术路线", titles)
            self.assertEqual(titles["技术路线"]["relation"], "parent")
            self.assertIn("总体技术路线", titles)
            self.assertEqual(titles["总体技术路线"]["relation"], "upstream")
            self.assertIn("关键技术方法", titles)
            self.assertEqual(titles["关键技术方法"]["relation"], "upstream")

            materials = payload["existing_materials"]
            self.assertGreaterEqual(materials["chapter_context_item_count"], 1)
            self.assertEqual(materials["tender_requirement_count"], 1)
            self.assertTrue(materials["has_local_materials"])
            self.assertIn("写作目的", payload["summary_text"])
            self.assertIn("全书位置", payload["summary_text"])
            self.assertIn("与其他章节关系", payload["summary_text"])

            compact = compact_orientation_for_prompt(payload)
            self.assertNotIn("summary", json.dumps(compact["chapter_relations"], ensure_ascii=False))
            view = public_orientation_view(payload)
            self.assertEqual(view["role_label"], "图示/路线图")
            self.assertTrue(view["related"])

    def test_shared_score_marks_related_primary_chapter(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _route_nodes())
            chapter = ChapterWorkspaceService(context).get_chapter("ch-method")
            payload = WritingOrientationService(context).build_for_chapter(chapter)
            shared = [
                item
                for item in payload["chapter_relations"]["items"]
                if item["chapter_id"] == "ch-overview"
            ]
            self.assertTrue(shared)
            self.assertIn(shared[0]["relation"], {"upstream", "peer", "shared_score"})
            self.assertIn("共享评分点", str(shared[0].get("note") or ""))


if __name__ == "__main__":
    unittest.main()
