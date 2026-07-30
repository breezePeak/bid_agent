"""Opt-in smoke tests for the configured production inference Provider.

These tests are intentionally excluded from the normal deterministic unit suite.
Set BID_AGENT_RUN_REAL_MODEL_SMOKE=1 to exercise the complete semantic planning
chain.  Set BID_AGENT_REAL_BID_DOCX as well to run the same structural checks
against a local, access-controlled real tender document.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402
from document_pipeline.topic_graph import (  # noqa: E402
    load_promoted_topic_graph,
)


_RUN_REAL_MODEL = os.environ.get("BID_AGENT_RUN_REAL_MODEL_SMOKE") == "1"


def _run_semantic_planning(
    *,
    runs_root: Path,
    workspace_id: str,
    sources: list[tuple[Path, InputRole]],
) -> None:
    workspace = runs_root / workspace_id
    workspace.mkdir(parents=True)
    context = WorkspaceContext.resolve(runs_root, workspace_id)
    manifest = InputManifestService(context)
    for path, role in sources:
        manifest.register_local_file(path, role)

    runner = V3StageRunner(context)
    runner.run("normalize_sources")
    runner.run("build_requirement_ledger")
    score_model = runner.run("analyze_scores")
    project_model = runner.run("plan_response")
    graph = load_promoted_topic_graph(context)
    blueprint = runner.run("compile_chapter_blueprint")

    assert project_model.semantic_upstream_refs
    assert graph.duties
    assert blueprint.nodes
    expected_conditions = {
        condition.condition_id
        for point in score_model.points
        for condition in point.score_conditions
        if point.response_scope == "section"
    }
    planned_conditions = {
        condition_id
        for node in blueprint.nodes
        for condition_id in node.score_condition_ids
    }
    assert expected_conditions <= planned_conditions


@pytest.mark.skipif(
    not _RUN_REAL_MODEL,
    reason="set BID_AGENT_RUN_REAL_MODEL_SMOKE=1 for paid real-Provider smoke",
)
def test_configured_provider_runs_full_semantic_planning_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BID_AGENT_INFERENCE_MODE", "llm")
    tender = tmp_path / "tender.md"
    score = tmp_path / "score.md"
    tender.write_text(
        "项目背景：开展年度数据核实。\n"
        "项目目标：提高核查准确性与工作效率。\n"
        "工作范围：完成样本接收、分类检查和成果汇总。",
        encoding="utf-8",
    )
    score.write_text(
        "目标任务（4分）：项目任务背景描述清楚，工作必要性和可行性理由"
        "充分、逻辑清晰；工作目标明确、可行；工作内容具体、翔实，得4分。",
        encoding="utf-8",
    )

    _run_semantic_planning(
        runs_root=tmp_path / "runs",
        workspace_id="real-provider-smoke",
        sources=[
            (tender, InputRole.TENDER),
            (score, InputRole.SCORE),
        ],
    )


@pytest.mark.skipif(
    not (
        _RUN_REAL_MODEL
        and os.environ.get("BID_AGENT_REAL_BID_DOCX")
    ),
    reason="set BID_AGENT_REAL_BID_DOCX for an access-controlled real-bid smoke",
)
def test_configured_provider_runs_on_real_bid_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BID_AGENT_INFERENCE_MODE", "llm")
    source = Path(os.environ["BID_AGENT_REAL_BID_DOCX"]).resolve()
    if not source.is_file():
        pytest.fail(f"BID_AGENT_REAL_BID_DOCX does not exist: {source}")

    _run_semantic_planning(
        runs_root=tmp_path / "runs",
        workspace_id="real-bid-smoke",
        sources=[(source, InputRole.TENDER)],
    )
