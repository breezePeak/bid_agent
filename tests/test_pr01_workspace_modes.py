from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import UploadFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import api.v3_app as v3_app  # noqa: E402
from control_plane import ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.contracts import (  # noqa: E402
    ChapterPlanFlowVersion,
    InputRole,
    ProjectWritingMode,
)
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.source_normalizer import SourceNormalizer  # noqa: E402
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


class _Request:
    def __init__(self, body: object, principal: dict[str, str] | None = None) -> None:
        self.body = body
        self.state = SimpleNamespace(
            principal=principal or {"id": "owner", "role": "user"}
        )

    async def json(self) -> object:
        return self.body


def _payload(response) -> dict[str, object]:
    return json.loads(response.body)


class PR01WorkspaceModeTests(unittest.TestCase):
    def test_old_document_state_is_upgraded_without_rebuilding_or_overwriting(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            runs = Path(temporary) / "runs"
            root = runs / "old"
            (root / "workspace").mkdir(parents=True)
            path = root / "workspace" / "control.db"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE control_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT INTO control_meta(key, value) VALUES ('schema_version', '29');
                    CREATE TABLE document_state(
                        workspace_id TEXT PRIMARY KEY,
                        document_mode TEXT NOT NULL DEFAULT '',
                        project_model_revision INTEGER,
                        document_contract_revision INTEGER,
                        document_plan_revision INTEGER,
                        integration_revision INTEGER,
                        delivery_status TEXT NOT NULL DEFAULT 'draft_with_gaps',
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO document_state(
                        workspace_id, document_mode, delivery_status, updated_at
                    ) VALUES ('old', 'template_strict', 'ready', '2026-01-01T00:00:00Z');
                    """
                )

            store = ControlStore(WorkspaceContext.resolve(runs, "old"))
            state = store.document_state()
            self.assertEqual(state["document_mode"], "template_strict")
            self.assertEqual(state["delivery_status"], "ready")
            self.assertEqual(state["writing_mode"], "full_write")
            self.assertEqual(state["chapter_plan_flow"], "legacy_inline")
            repeated = ControlStore(WorkspaceContext.resolve(runs, "old")).document_state()
            self.assertEqual(repeated, state)
            with sqlite3.connect(path) as connection:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(document_state)")
                }
                version = connection.execute(
                    "SELECT value FROM control_meta WHERE key='schema_version'"
                ).fetchone()[0]
            self.assertIn("writing_mode", columns)
            self.assertIn("chapter_plan_flow", columns)
            self.assertEqual(version, str(ControlStore.SCHEMA_VERSION))

    def test_new_workspace_defaults_are_persisted_and_projected(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            runs = Path(temporary) / "runs"
            with mock.patch.object(v3_app, "RUNS_DIR", runs):
                response = asyncio.run(
                    v3_app.create_workspace(_Request({"name": "默认全量编写"}))
                )
                body = _payload(response)
                workspace_id = str(body["workspace"]["id"])
                context = WorkspaceContext.resolve(runs, workspace_id)
                state = ControlStore(context).document_state()
                snapshot = V3WorkspaceSnapshotBuilder(context).build()
                listed = _payload(v3_app.list_workspaces(_Request({})))

            self.assertEqual(response.status_code, 201)
            self.assertEqual(state["writing_mode"], "full_write")
            self.assertEqual(state["chapter_plan_flow"], "legacy_inline")
            self.assertEqual(snapshot["writing_mode"], "full_write")
            self.assertEqual(snapshot["chapter_plan_flow"], "legacy_inline")
            self.assertFalse(snapshot["capabilities"]["bid_rewrite"]["released"])
            self.assertFalse(
                snapshot["capabilities"]["chapter_plan_v2"]["workbench_enabled"]
            )
            self.assertEqual(listed["workspaces"][0]["writing_mode"], "full_write")

    def test_chapter_plan_workbench_capability_is_independently_reversible(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            runs = Path(temporary) / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            ControlStore(context)
            with mock.patch.dict(
                os.environ,
                {"BID_AGENT_CHAPTER_PLAN_WORKBENCH_ENABLED": "1"},
            ):
                snapshot = V3WorkspaceSnapshotBuilder(context).build()
            self.assertTrue(
                snapshot["capabilities"]["chapter_plan_v2"]["workbench_enabled"]
            )

    def test_invalid_and_unreleased_rewrite_modes_are_rejected_without_workspace(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            runs = Path(temporary) / "runs"
            with mock.patch.object(v3_app, "RUNS_DIR", runs):
                invalid = asyncio.run(
                    v3_app.create_workspace(
                        _Request({"name": "invalid", "writing_mode": "other"})
                    )
                )
                with mock.patch.dict(
                    os.environ, {"BID_AGENT_BID_REWRITE_ENABLED": "0"}
                ):
                    disabled = asyncio.run(
                        v3_app.create_workspace(
                            _Request(
                                {"name": "disabled", "writing_mode": "bid_rewrite"}
                            )
                        )
                    )
                with mock.patch.dict(
                    os.environ, {"BID_AGENT_BID_REWRITE_ENABLED": "1"}
                ):
                    unreleased = asyncio.run(
                        v3_app.create_workspace(
                            _Request(
                                {"name": "unreleased", "writing_mode": "bid_rewrite"}
                            )
                        )
                    )

            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(_payload(invalid)["error"]["code"], "WRITING_MODE_INVALID")
            self.assertEqual(disabled.status_code, 403)
            self.assertEqual(_payload(disabled)["error"]["code"], "CAPABILITY_DISABLED")
            self.assertEqual(unreleased.status_code, 409)
            self.assertEqual(_payload(unreleased)["error"]["code"], "FEATURE_NOT_RELEASED")
            self.assertFalse(runs.exists())

    def test_legacy_bid_schema_is_recognized_but_upload_is_disabled(self) -> None:
        self.assertIs(InputRole("legacy_bid"), InputRole.LEGACY_BID)
        self.assertIs(ProjectWritingMode("full_write"), ProjectWritingMode.FULL_WRITE)
        self.assertIs(
            ChapterPlanFlowVersion("legacy_inline"),
            ChapterPlanFlowVersion.LEGACY_INLINE,
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            runs = Path(temporary) / "runs"
            (runs / "alpha" / "workspace" / "v3").mkdir(parents=True)
            upload = UploadFile(
                filename="old-bid.md", file=io.BytesIO("旧项目正文".encode("utf-8"))
            )
            with (
                mock.patch.object(v3_app, "RUNS_DIR", runs),
                mock.patch.dict(os.environ, {"BID_AGENT_BID_REWRITE_ENABLED": "0"}),
            ):
                response = asyncio.run(v3_app.upload("alpha", "legacy_bid", upload, ""))
            self.assertEqual(response.status_code, 403)
            self.assertEqual(_payload(response)["error"]["code"], "CAPABILITY_DISABLED")

    def test_legacy_bid_is_excluded_from_source_index_and_semantic_downstream(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            runs = root / "runs"
            (runs / "alpha").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            legacy = root / "legacy.md"
            legacy.write_text("# 旧项目\n旧客户名称与旧项目方案。", encoding="utf-8")
            registration = InputManifestService(context).register_local_file(
                legacy, InputRole.LEGACY_BID
            )

            source_index = SourceNormalizer(context).normalize_active_inputs()

            self.assertNotIn(registration.item.input_id, source_index["source_hashes"])
            self.assertFalse(source_index["blocks"])
            status = source_index["input_status"][0]
            self.assertEqual(status["input_id"], registration.item.input_id)
            self.assertEqual(status["status"], "excluded")


if __name__ == "__main__":
    unittest.main()
