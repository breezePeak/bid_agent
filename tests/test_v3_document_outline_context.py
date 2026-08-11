"""Full outline awareness and read-only peer chapter views."""

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
    compact_outline_for_prompt,
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
        blueprint_id="bp-outline",
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


def _nodes() -> list[BlueprintNode]:
    return [
        BlueprintNode(
            chapter_id="parent-route",
            order=0,
            title="技术路线",
            purpose="组织技术路线",
            content_policy="structural_only",
        ),
        BlueprintNode(
            chapter_id="ch-overview",
            parent_chapter_id="parent-route",
            order=1,
            title="总体技术路线",
            purpose="阶段框架",
        ),
        BlueprintNode(
            chapter_id="ch-method",
            parent_chapter_id="parent-route",
            order=2,
            title="关键技术方法",
            purpose="方法细则",
        ),
        BlueprintNode(
            chapter_id="ch-diagram",
            parent_chapter_id="parent-route",
            order=3,
            title="技术路线图",
            purpose="以图呈现阶段",
        ),
        BlueprintNode(
            chapter_id="other-parent",
            order=10,
            title="其他大章",
            purpose="其他",
            content_policy="structural_only",
        ),
        BlueprintNode(
            chapter_id="ch-other",
            parent_chapter_id="other-parent",
            order=11,
            title="无关章节",
            purpose="无关",
        ),
    ]


class DocumentOutlineContextTests(unittest.TestCase):
    def test_outline_includes_full_tree_and_current_position(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
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
                        "content": "核查准备→数据接收→内业核查→成果复核",
                        "confidence": 0.9,
                    }
                ],
                source="ai_draft",
            )
            diagram = service.get_chapter("ch-diagram")
            payload = DocumentOutlineContextService(context).build_for_chapter(diagram)

            ids = [item["chapter_id"] for item in payload["outline"]]
            self.assertEqual(
                ids,
                [
                    "parent-route",
                    "ch-overview",
                    "ch-method",
                    "ch-diagram",
                    "other-parent",
                    "ch-other",
                ],
            )
            current = next(item for item in payload["outline"] if item["is_current"])
            self.assertEqual(current["chapter_id"], "ch-diagram")
            self.assertEqual(payload["position"]["path_label"], "技术路线 / 技术路线图")
            self.assertFalse(payload["access"]["can_edit_other_chapters"])
            related_ids = [item["chapter_id"] for item in payload["related_summaries"]]
            self.assertIn("ch-overview", related_ids)
            self.assertIn("parent-route", related_ids)
            overview_related = next(
                item for item in payload["related_summaries"] if item["chapter_id"] == "ch-overview"
            )
            self.assertIn("核查准备", overview_related["summary"])

            compact = compact_outline_for_prompt(payload)
            self.assertEqual(compact["current_chapter_id"], "ch-diagram")
            self.assertEqual(len(compact["outline"]), 6)

    def test_readonly_view_is_not_editable(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            context = _workspace(Path(tmp))
            _seed_blueprint(context, _nodes())
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
                        "content": "总体四阶段路线",
                        "confidence": 0.9,
                    }
                ],
                source="ai_draft",
            )
            view = DocumentOutlineContextService(context).readonly_chapter_view(
                "ch-overview",
                viewer_chapter_id="ch-diagram",
            )
            self.assertFalse(view["access"]["can_edit"])
            self.assertEqual(view["access"]["mode"], "read_only")
            self.assertEqual(view["chapter_id"], "ch-overview")
            self.assertIn("四阶段", view["summary"])
            self.assertEqual(view["access"]["viewer_chapter_id"], "ch-diagram")


if __name__ == "__main__":
    unittest.main()
