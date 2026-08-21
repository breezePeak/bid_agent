"""Verify the frozen PR-00 contracts without external services."""

from __future__ import annotations

import os
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def workspace(base: Path, workspace_id: str = "baseline"):
    from control_plane import WorkspaceContext

    runs = base / "runs"
    (runs / workspace_id).mkdir(parents=True)
    return WorkspaceContext.resolve(runs, workspace_id)


def check_imports() -> None:
    from api.v3_app import app
    from control_plane import ControlStore
    from document_pipeline.chapter_writing_service import ChapterWritingService
    from document_pipeline.current_word_export import build_current_word
    from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder

    require(all((app, ControlStore, ChapterWritingService, build_current_word, V3WorkspaceSnapshotBuilder)), "关键模块导入失败")


def check_control_store_upgrade() -> None:
    from control_plane import ControlStore

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = workspace(Path(tmp), "legacy")
        db = context.root / "workspace" / "control.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as connection:
            connection.execute(
                "CREATE TABLE control_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO control_meta(key, value) VALUES ('schema_version', '1')"
            )
        store = ControlStore(context)
        with sqlite3.connect(store.path) as connection:
            version = connection.execute(
                "SELECT value FROM control_meta WHERE key = 'schema_version'"
            ).fetchone()
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(operations)")
            }
        require(version == (str(ControlStore.SCHEMA_VERSION),), "旧 ControlStore Schema 未升级")
        require({"workflow_phase", "phase_status", "phase_revision"} <= columns, "ControlStore 新列缺失")


def check_writer_entry() -> None:
    api = (SRC / "api" / "v3_app.py").read_text(encoding="utf-8")
    batch = (SRC / "document_pipeline" / "chapter_batch.py").read_text(encoding="utf-8")
    chat = (SRC / "document_pipeline" / "chapter_chat.py").read_text(encoding="utf-8")
    runner = (SRC / "document_pipeline" / "stage_runner.py").read_text(encoding="utf-8")
    require("ChapterWritingService" in api, "API 未使用统一章节写作入口")
    require("ChapterWritingService" in batch, "批量写作未使用统一章节写作入口")
    require("ChapterWritingService" in chat, "章节对话未使用统一章节写作入口")
    require("if stage == \"execute_content_plan\"" not in runner, "旧写作执行器仍可启动新运行")


def check_deterministic_research_is_offline() -> None:
    from document_pipeline.contracts import (
        EvidenceNeed,
        EvidenceRelevanceTier,
        EvidenceSourceType,
    )
    from document_pipeline.research_service import ResearchCandidate, ResearchService

    class Provider:
        provider_id = "pr00-offline"
        cache_fingerprint = "pr00-offline-v1"

        @staticmethod
        def search(question: str, *, limit: int):
            del question, limit
            return [
                ResearchCandidate(
                    title="公开标准",
                    publisher="国家标准公开平台",
                    content="公开标准规定实施过程应保留质量检查与验收记录。",
                    source_url="https://example.gov.cn/standard",
                    source_type=EvidenceSourceType.STANDARD,
                    relevance_tier=EvidenceRelevanceTier.INDUSTRY_STANDARD,
                    claim_types=("standard", "method"),
                )
            ]

    def review(_need, candidate):
        return {
            "verdict": "relevant",
            "confidence": 1.0,
            "reason": "deterministic fixture",
            "supporting_excerpts": [candidate.content],
            "extracted_points": [candidate.content],
            "usage_category": "industry_standard",
        }

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = workspace(Path(tmp), "offline")
        need = EvidenceNeed(
            need_id="EN-PR00-OFFLINE",
            question="查询公开质量验收标准",
            topic_id="chapter:offline",
            deadline_stage="chapter_writing",
            query_budget=1,
        )
        with mock.patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("deterministic baseline attempted network"),
        ):
            batch = ResearchService(
                context,
                Provider(),
                semantic_reviewer=review,
            ).resolve(need)
        require(batch.status == "published", "确定性研究未离线发布证据")


def check_stage_snapshot_contract() -> None:
    from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder

    require(
        V3WorkspaceSnapshotBuilder._canonical_generation_stage(
            "execute_content_plan"
        )
        == "chapter_writing",
        "历史写作阶段未映射到 chapter_writing",
    )
    require(
        "chapter_writing" in V3WorkspaceSnapshotBuilder._GENERATION_STAGE_LABELS,
        "当前写作阶段未注册",
    )


def check_confirmation_switch() -> None:
    from document_pipeline.chapter_editing import ChapterEditingService

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("BID_AGENT_CHAPTER_CONFIRMATION_REQUIRED", None)
        require(not ChapterEditingService._confirmation_required(), "章节确认默认值错误")
    with mock.patch.dict(
        os.environ,
        {"BID_AGENT_CHAPTER_CONFIRMATION_REQUIRED": "1"},
        clear=False,
    ):
        require(ChapterEditingService._confirmation_required(), "章节确认开关开启失败")


def check_api_contract() -> None:
    from api.v3_app import app

    paths = {route.path for route in app.routes}
    required = {
        "/api/v3/workspaces/{workspace_id}/exports/word",
        "/api/v3/workspaces/{workspace_id}/chapters",
    }
    require(required <= paths, f"关键 API contract 缺失: {sorted(required - paths)}")


def check_word_export() -> None:
    from document_pipeline.current_word_export import build_current_word
    from document_pipeline.renderers.word_styles import write_composed_document

    require(callable(build_current_word), "当前 Word 导出入口不可用")
    require(callable(write_composed_document), "Word 渲染模块不可用")


def main() -> int:
    checks = (
        ("imports", check_imports),
        ("control_store_upgrade", check_control_store_upgrade),
        ("writer_entry", check_writer_entry),
        ("deterministic_research_offline", check_deterministic_research_is_offline),
        ("stage_snapshot", check_stage_snapshot_contract),
        ("confirmation_switch", check_confirmation_switch),
        ("api_contract", check_api_contract),
        ("word_export", check_word_export),
    )
    for name, check in checks:
        check()
        print(f"PASS {name}")
    print(f"PR-00 baseline verified: {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
