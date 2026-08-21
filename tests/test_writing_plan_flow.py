from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from control_plane import ControlPlaneError, WorkspaceContext
from document_pipeline.chapter_chat import ChapterChatService, _decide_chapter_action
from document_pipeline.chapter_research_planner import plan_chapter_research
from document_pipeline.chapter_writing_outline import compile_chapter_writing_plan
from document_pipeline.chapter_writing_service import ChapterWritingService
from document_pipeline.contracts import WriterInputBundle


def _service(root: Path) -> ChapterChatService:
    runs = root / "runs"
    (runs / "alpha").mkdir(parents=True)
    return ChapterChatService(WorkspaceContext.resolve(runs, "alpha"))


def test_body_write_never_waits_for_plan_approval() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        service = _service(Path(temporary))
        phase = service.resolve_write_phase(
            "chapter-a",
            outline={"blocks": []},
            agent_action="write_document",
        )
        assert phase["write_phase"] == "write_body"
        assert "review_status" not in phase
        assert "outline_hash" not in phase


def test_plan_is_internal_persisted_and_only_shown_on_request() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        service = _service(Path(temporary))
        plan = compile_chapter_writing_plan(
            {
                "chapter_id": "chapter-a",
                "title": "工作目标",
                "blueprint_node": {
                    "purpose": "写清两项任务目标和实施边界",
                    "writing_objectives": ["任务一目标", "任务二目标", "实施边界"],
                },
            }
        )
        saved = service.save_writing_plan("chapter-a", plan)
        assert saved["writing_plan"]["schema_version"] == "v3.chapter-writing-plan.v1"
        shown = service.resolve_write_phase(
            "chapter-a", outline=plan, agent_action="show_writing_plan"
        )
        assert shown["write_phase"] == "show_writing_plan"
        write = service.resolve_write_phase(
            "chapter-a", outline=plan, agent_action="write_document"
        )
        assert write["write_phase"] == "write_body"


def test_quality_gate_keeps_one_automatic_repair_attempt() -> None:
    source = (ROOT / "src/document_pipeline/chapter_writing_service.py").read_text(
        encoding="utf-8"
    )
    assert "repair_result = self.repair_writer(bundle, blocks, exc)" in source
    assert "self.quality_gate.validate(repair_bundle, repaired)," in source


def test_hard_bundle_gate_failure_is_not_sent_to_repair() -> None:
    service = ChapterWritingService.__new__(ChapterWritingService)
    service.quality_gate = mock_gate = type(
        "Gate",
        (),
        {"validate": lambda *_args: (_ for _ in ()).throw(ValueError("G4_CONTENT_TARGET_OUT_OF_BUNDLE"))},
    )()
    repair_calls = []
    service.repair_writer = lambda *_args: repair_calls.append(True) or []
    try:
        service._quality_gate(object(), [])
    except ValueError as exc:
        assert "G4_CONTENT_TARGET_OUT_OF_BUNDLE" in str(exc)
    else:
        raise AssertionError("hard Bundle violation must block")
    assert repair_calls == []
    assert mock_gate is service.quality_gate


def test_failed_supplemental_research_uses_sufficient_project_materials() -> None:
    bundle = WriterInputBundle.model_construct(
        global_project_context={
            "identity": {"project_name": "真实项目"},
            "scope": ["项目核查范围"],
        },
        project_context={},
        requirement_excerpts=[{"requirement_id": "R-1", "text": "开展成果核查"}],
        chapter_context_items=[],
        chapter_grounding_context={},
    )
    decision, evidence = ChapterWritingService._research_failure_fallback(
        bundle,
        ControlPlaneError(
            "WRITER_RESEARCH_ACTION_REQUIRED",
            "未取得可核验公开来源",
            details={
                "research": {
                    "needs_research": True,
                    "fallback_to_existing_materials": True,
                }
            },
        ),
    )
    assert decision["decision_status"] == "fallback_existing_materials"
    assert decision["supplemental_search_failed"] is True
    assert evidence == []


def test_required_research_failure_is_not_reclassified_from_generic_project_facts() -> None:
    bundle = WriterInputBundle.model_construct(
        global_project_context={"scope": ["项目范围"], "work_packages": ["任务一"]},
        project_context={},
        requirement_excerpts=[{"requirement_id": "R-1", "text": "工作内容"}],
        chapter_context_items=[],
        chapter_grounding_context={},
    )
    error = ControlPlaneError(
        "WRITER_RESEARCH_ACTION_REQUIRED",
        "未取得可核验公开来源",
        details={"research": {"needs_research": True}},
    )
    try:
        ChapterWritingService._research_failure_fallback(bundle, error)
    except ControlPlaneError as raised:
        assert raised is error
    else:
        raise AssertionError("required research failure must stop before drafting")


def test_writing_plan_required_research_rejects_stale_fallback_flag() -> None:
    bundle = WriterInputBundle.model_construct(
        chapter_writing_plan={
            "blocks": [
                {
                    "must_answer": "现行公开标准",
                    "needs_public_research": True,
                }
            ]
        },
        global_project_context={"scope": ["项目范围"]},
        project_context={},
        requirement_excerpts=[],
        chapter_context_items=[],
        chapter_grounding_context={},
    )
    error = ControlPlaneError(
        "WRITER_RESEARCH_ACTION_REQUIRED",
        "未取得可核验公开来源",
        details={
            "research": {
                "needs_research": True,
                "fallback_to_existing_materials": True,
            }
        },
    )
    try:
        ChapterWritingService._research_failure_fallback(bundle, error)
    except ControlPlaneError as raised:
        assert raised is error
    else:
        raise AssertionError("WritingPlan mandatory research must never be downgraded")


def test_work_content_plan_binds_project_tasks_and_skips_public_research() -> None:
    chapter = {
        "chapter_id": "chapter-work",
        "title": "工作内容",
        "blueprint_node": {
            "title": "工作内容",
            "purpose": "围绕工作目标展开具体任务说明，完整呈现项目实施内容。",
            "writing_objectives": ["具体、翔实地说明各项工作内容。"],
        },
    }
    project = {
        "identity": {"project_name": "真实项目"},
        "work_packages": [
            "准备工作：建立核查样本。",
            "成果复核：对再次提交成果开展内业复核。",
        ],
    }
    plan = compile_chapter_writing_plan(chapter, project_context=project)
    assert [item["project_fact_refs"] for item in plan["blocks"]] == [
        ["work_packages[0]"],
        ["work_packages[1]"],
    ]
    assert "成果复核" in plan["blocks"][1]["must_answer"]

    decision = plan_chapter_research(
        chapter,
        project_context=project,
        writing_orientation={"chapter_writing_plan": plan},
        decision_provider=lambda _brief: (_ for _ in ()).throw(
            AssertionError("project-bound work content must not ask the model to invent a public gap")
        ),
    )
    assert decision["need_research"] is False
    assert decision["decision_source"] == "writing_plan_project_facts_guard"


def test_all_entry_points_still_use_single_service() -> None:
    chat = (ROOT / "src/document_pipeline/chapter_chat.py").read_text(encoding="utf-8")
    api = (ROOT / "src/api/v3_app.py").read_text(encoding="utf-8")
    assert "ChapterWritingService(self.context).iter_events" in chat
    assert "ChapterWritingService(context).iter_events" in api
    for forbidden in ("ChatWriter", "DirectWriter", "OutlineWriter", "FastWriter"):
        assert forbidden not in chat
        assert forbidden not in api


def test_explicit_body_and_plan_intents_are_deterministic() -> None:
    context = {"chapter_scope": {}}
    for message in (
        "开始写正文",
        "写本章",
        "继续写",
        "按这个写",
        "把这段改具体",
        "重新写",
    ):
        assert _decide_chapter_action(context, [], message)["action"] == "write_document"
    shown_history = [
        {"role": "assistant", "content": "本章 WritingPlan\n1. 总体目标\n2. 两项任务"}
    ]
    assert (
        _decide_chapter_action(context, shown_history, "第二点再具体一点")["action"]
        == "revise_writing_plan"
    )
    assert (
        _decide_chapter_action(context, [], "先看看这一章怎么写")["action"]
        == "show_writing_plan"
    )
