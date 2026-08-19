"""Same-parent sibling context for dependent leaf chapters (e.g. 技术路线图)."""

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
from document_pipeline.sibling_chapter_context import (  # noqa: E402
    SiblingChapterContextService,
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
        blueprint_id="bp-sib",
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
    now = "2026-08-11T00:00:00.000+00:00"
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
        ),
        BlueprintNode(
            chapter_id="ch-method",
            parent_chapter_id="parent-route",
            order=2,
            title="关键技术方法",
            purpose="展开各阶段核心技术方法",
        ),
        BlueprintNode(
            chapter_id="ch-diagram",
            parent_chapter_id="parent-route",
            order=3,
            title="技术路线图",
            purpose="以图呈现总体技术路线阶段与节点",
        ),
        BlueprintNode(
            chapter_id="other-parent",
            order=10,
            title="其他大章",
            purpose="不相关父节点",
            content_policy="structural_only",
        ),
        BlueprintNode(
            chapter_id="ch-other",
            parent_chapter_id="other-parent",
            order=11,
            title="无关章节",
            purpose="不应进入兄弟上下文",
        ),
    ]


class SiblingChapterContextTests(unittest.TestCase):
    def test_diagram_chapter_sees_same_parent_siblings_only(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _route_nodes())
            service = ChapterWorkspaceService(context)
            overview = service.create(chapter_id="ch-overview", expected_chapter_revision=0)
            service.store.append_chapter_content_revision(
                chapter_id="ch-overview",
                expected_chapter_revision=overview["chapter_revision"],
                blocks=[
                    {
                        "block_id": "b1",
                        "target_node_id": "ch-overview",
                        "type": "paragraph",
                        "content": (
                            "总体技术路线分为核查准备、数据接收、内业核查、成果复核四个阶段，"
                            "并明确阶段输入输出与质控节点。"
                        ),
                        "confidence": 0.9,
                    }
                ],
                source="ai_draft",
            )
            diagram = service.get_chapter("ch-diagram")
            payload = SiblingChapterContextService(context).build_for_chapter(
                diagram,
                include_bodies=True,
            )

            self.assertEqual(payload["chapter_role"], "general")
            self.assertEqual(payload["parent_chapter_id"], "parent-route")
            sibling_ids = [item["chapter_id"] for item in payload["siblings"]]
            self.assertEqual(sibling_ids, ["ch-overview", "ch-method"])
            self.assertNotIn("ch-other", sibling_ids)
            self.assertNotIn("parent-route", sibling_ids)

            overview_row = next(
                item for item in payload["siblings"] if item["chapter_id"] == "ch-overview"
            )
            method_row = next(
                item for item in payload["siblings"] if item["chapter_id"] == "ch-method"
            )
            self.assertTrue(overview_row["has_content"])
            self.assertIn("四个阶段", overview_row["summary"])
            self.assertEqual(overview_row["relation"], "upstream")
            self.assertEqual(overview_row["role"], "general")
            self.assertFalse(method_row["has_content"])
            self.assertEqual(method_row["role"], "general")
            self.assertTrue(payload["ready_for_dependent_writing"])
            self.assertEqual(payload["missing_upstream"], [])

    def test_diagram_marks_empty_overview_sibling(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _route_nodes())
            diagram = ChapterWorkspaceService(context).get_chapter("ch-diagram")
            payload = SiblingChapterContextService(context).build_for_chapter(diagram)
            self.assertEqual(payload["chapter_role"], "general")
            self.assertTrue(payload["ready_for_dependent_writing"])
            self.assertEqual(payload["missing_upstream"], [])


if __name__ == "__main__":
    unittest.main()
