from __future__ import annotations

"""Chapter subgraph with limited self-check → rewrite loop (PR-11)."""

from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from agents.chapter_writer_agent import run as write_chapter_agent
from chapter_reviewer import (
    review_chapter_markdown,
    rewrite_fix_signatures,
    should_auto_rewrite,
)
from file_loader import load_global_facts, load_score_points
from graph.state import ChapterState
from utils import project_root, read_json, select_score_points, stringify, write_json, write_text

DEFAULT_MAX_REWRITE_ROUNDS = 2


def _max_rewrite_rounds() -> int:
    try:
        from agent.budgets import max_repair_rounds

        return max(1, int(max_repair_rounds()))
    except Exception:
        return DEFAULT_MAX_REWRITE_ROUNDS


def load_chapter_job(state: ChapterState) -> ChapterState:
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job")
    if not job:
        chapter_id = stringify(state.get("chapter_id"))
        if not chapter_id:
            raise ValueError("章节子图缺少 job 或 chapter_id。")
        job = read_json(root / "workspace" / "jobs" / f"{chapter_id}.json")
    chapter_id = stringify(job.get("chapter_id"))
    return {
        "job": job,
        "chapter_id": chapter_id,
        "rewrite_round": int(state.get("rewrite_round") or 0),
        "max_rewrite_rounds": int(state.get("max_rewrite_rounds") or _max_rewrite_rounds()),
        "chapter_status": str(state.get("chapter_status") or "pending"),
        "problem_fingerprints": list(state.get("problem_fingerprints") or []),
    }


def load_chapter_context(state: ChapterState) -> ChapterState:
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job") or {}
    chapter_id = stringify(state.get("chapter_id") or job.get("chapter_id"))
    from context_selector import valid_context_ids

    if chapter_id not in valid_context_ids(root, [job]):
        raise ValueError(f"章节 {chapter_id} 上下文缺失、无效或输入已变化，请先续跑上下文选择")
    context_path = Path(stringify(job.get("context_path")) or root / "workspace" / "contexts" / f"{chapter_id}_context.json")
    if not context_path.is_absolute():
        context_path = root / context_path
    context = read_json(context_path)
    return {"context": context}


def write_chapter(state: ChapterState) -> ChapterState:
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job") or {}
    context = state.get("context") or {}
    # If already have markdown and this is a rewrite path, keep existing unless rewrite node set it
    if state.get("chapter_markdown") and int(state.get("rewrite_round") or 0) > 0:
        return {}
    markdown = write_chapter_agent(job, context, root)
    return {"chapter_markdown": markdown}


def self_check_chapter(state: ChapterState) -> ChapterState:
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job") or {}
    chapter_id = stringify(job.get("chapter_id") or state.get("chapter_id"))
    chapter = {
        "id": chapter_id,
        "title": stringify(job.get("chapter_title")),
        "score_point_ids": job.get("score_point_ids", []),
        "description": stringify(job.get("description")),
        "sections": job.get("sections", []),
    }
    try:
        score_points = load_score_points(root)
        global_facts = load_global_facts(root)
        related_score_points = select_score_points(score_points, job.get("score_point_ids", []))
        self_check = review_chapter_markdown(
            chapter,
            related_score_points,
            global_facts,
            stringify(state.get("chapter_markdown")),
            root,
            debug_name=f"debug_self_check_{chapter_id}_raw.txt",
        )
    except Exception as exc:
        self_check = {
            "chapter_id": chapter_id,
            "chapter_title": stringify(job.get("chapter_title")),
            "score_coverage": [],
            "problems": [
                {
                    "type": "self_check_failed",
                    "severity": "blocker",
                    "description": str(exc),
                    "suggestion": "请人工检查本章节内容。",
                }
            ],
            "priority_fixes": [
                {
                    "id": "self_check_failed",
                    "severity": "blocker",
                    "source": "problem",
                    "score_point_id": "",
                    "problem_type": "self_check_failed",
                    "target": str(exc),
                    "action": "请人工检查本章节内容。",
                    "acceptance": "自检通过且无阻断错误。",
                }
            ],
            "max_severity": "blocker",
            "need_rewrite": True,
            "need_evidence": False,
            "has_writing_fixes": True,
            "rewrite_status": "need_rewrite",
        }
        return {
            "self_check": self_check,
            "error": str(exc),
            "chapter_status": "failed",
        }

    fingerprints = list(state.get("problem_fingerprints") or [])
    current_sigs = rewrite_fix_signatures(self_check) if isinstance(self_check, dict) else []
    sig_key = "|".join(current_sigs)
    stuck = False
    if sig_key and fingerprints and fingerprints[-1] == sig_key:
        # same fingerprint as previous round → stuck if two consecutive
        if len(fingerprints) >= 1:
            stuck = True
    if sig_key:
        fingerprints = (fingerprints + [sig_key])[-4:]

    status = "passed"
    rewrite_status = stringify(self_check.get("rewrite_status")) if isinstance(self_check, dict) else "ok"
    need_evidence = bool(isinstance(self_check, dict) and self_check.get("need_evidence"))
    need_rewrite = bool(isinstance(self_check, dict) and should_auto_rewrite(self_check))

    if stuck and need_rewrite:
        status = "stuck"
        if isinstance(self_check, dict):
            self_check = dict(self_check)
            self_check["stuck"] = True
            self_check["rewrite_status"] = "stuck"
    elif need_evidence and not need_rewrite:
        status = "deferred_material"
        if isinstance(self_check, dict):
            self_check = dict(self_check)
            self_check["rewrite_status"] = "need_evidence"
    elif need_rewrite:
        status = "need_rewrite"
    elif rewrite_status in {"ok", ""} and not (isinstance(self_check, dict) and self_check.get("need_rewrite")):
        status = "passed"
    elif isinstance(self_check, dict) and self_check.get("need_rewrite"):
        # has writing issues but should_auto_rewrite false (e.g. evidence only mixed)
        if need_evidence:
            status = "deferred_material"
        else:
            status = "need_rewrite"
    else:
        status = "passed"

    return {
        "self_check": self_check,
        "problem_fingerprints": fingerprints,
        "chapter_status": status,
        "last_problem_signature": sig_key,
    }


def rewrite_chapter_node(state: ChapterState) -> ChapterState:
    """In-subgraph rewrite using chapter_rewriter when possible; fallback keeps markdown."""
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job") or {}
    chapter_id = stringify(job.get("chapter_id") or state.get("chapter_id"))
    round_n = int(state.get("rewrite_round") or 0) + 1
    max_rounds = int(state.get("max_rewrite_rounds") or _max_rewrite_rounds())

    # Persist current draft so rewrite_chapter can load it
    markdown = stringify(state.get("chapter_markdown"))
    output_path = Path(stringify(job.get("output_path")) or root / "workspace" / "chapters" / f"{chapter_id}.md")
    if not output_path.is_absolute():
        output_path = root / output_path
    if markdown:
        write_text(output_path, markdown)

    # Persist self_check as review so rewrite_chapter / should_auto_rewrite can use it
    self_check = state.get("self_check") or {}
    review_path = root / "workspace" / "reviews" / f"{chapter_id}_review.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_payload = dict(self_check) if isinstance(self_check, dict) else {}
    review_payload.setdefault("chapter_id", chapter_id)
    write_json(review_path, review_payload)

    # Also save self_check snapshot per round
    sc_path = root / "workspace" / "reviews" / f"{chapter_id}_self_check_r{round_n}.json"
    write_json(sc_path, review_payload)

    new_md = markdown
    rewrite_error = ""
    try:
        from chapter_rewriter import rewrite_chapter

        rewrite_chapter(chapter_id, root)
        if output_path.exists():
            new_md = output_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        rewrite_error = str(exc)
        # keep previous markdown

    # activity log
    try:
        from agent.activity import mark_agent

        mark_agent(
            root,
            role="chapter_rewriter",
            chapter_id=chapter_id,
            status="done" if not rewrite_error else "failed",
            message=f"round={round_n}/{max_rounds}" + (f" err={rewrite_error[:120]}" if rewrite_error else ""),
            attempt=round_n,
        )
    except Exception:
        pass

    return {
        "chapter_markdown": new_md,
        "rewrite_round": round_n,
        "error": rewrite_error or state.get("error") or "",
        "chapter_status": "rewritten",
    }


def save_chapter(state: ChapterState) -> ChapterState:
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job") or {}
    chapter_id = stringify(job.get("chapter_id") or state.get("chapter_id"))
    markdown = stringify(state.get("chapter_markdown"))
    if not markdown:
        raise ValueError(f"章节 {chapter_id} 未生成正文。")

    output_path = Path(stringify(job.get("output_path")) or root / "workspace" / "chapters" / f"{chapter_id}.md")
    if not output_path.is_absolute():
        output_path = root / output_path
    write_text(output_path, markdown)

    self_check = dict(state.get("self_check") or {})
    final_status = str(state.get("chapter_status") or "passed")
    if final_status == "need_rewrite":
        # exhausted or routed to save without rewrite success
        if int(state.get("rewrite_round") or 0) >= int(state.get("max_rewrite_rounds") or _max_rewrite_rounds()):
            final_status = "stuck"
            self_check["stuck"] = True
            self_check["rewrite_status"] = "stuck"
        elif self_check.get("need_evidence"):
            final_status = "deferred_material"
    if final_status == "rewritten":
        final_status = "passed" if not self_check.get("need_rewrite") else final_status

    self_check["chapter_final_status"] = final_status
    self_check["rewrite_rounds"] = int(state.get("rewrite_round") or 0)
    self_check_path = root / "workspace" / "reviews" / f"{chapter_id}_self_check.json"
    write_json(self_check_path, self_check)

    # sync issues for supervisor
    try:
        from agent.root_cause import sync_issues_from_review_fix

        if final_status == "deferred_material":
            sync_issues_from_review_fix(
                root,
                need_rewrite_ids=[],
                need_evidence_ids=[chapter_id],
                stuck_ids=[],
            )
        elif final_status == "stuck":
            sync_issues_from_review_fix(
                root,
                need_rewrite_ids=[],
                need_evidence_ids=[],
                stuck_ids=[chapter_id],
            )
        elif final_status == "failed":
            sync_issues_from_review_fix(
                root,
                need_rewrite_ids=[chapter_id],
                need_evidence_ids=[],
                stuck_ids=[],
            )
    except Exception:
        pass

    print(f"[完成] 章节 SubAgent 已生成 {chapter_id}: {output_path} status={final_status}")
    return {
        "output_path": str(output_path),
        "self_check_path": str(self_check_path),
        "chapter_status": final_status,
        "self_check": self_check,
    }


def route_after_self_check(state: ChapterState) -> Literal["save_chapter", "rewrite_chapter"]:
    status = str(state.get("chapter_status") or "passed")
    round_n = int(state.get("rewrite_round") or 0)
    max_rounds = int(state.get("max_rewrite_rounds") or _max_rewrite_rounds())

    if status == "passed":
        return "save_chapter"
    if status == "deferred_material":
        return "save_chapter"
    if status == "stuck":
        return "save_chapter"
    if status == "failed" and not should_auto_rewrite(state.get("self_check") or {}):
        return "save_chapter"
    if status == "need_rewrite" and round_n < max_rounds:
        self_check = state.get("self_check") or {}
        if should_auto_rewrite(self_check):
            return "rewrite_chapter"
        return "save_chapter"
    return "save_chapter"


def build_chapter_subgraph():
    graph = StateGraph(ChapterState)
    graph.add_node("load_chapter_job", load_chapter_job)
    graph.add_node("load_chapter_context", load_chapter_context)
    graph.add_node("write_chapter", write_chapter)
    graph.add_node("self_check_chapter", self_check_chapter)
    graph.add_node("rewrite_chapter", rewrite_chapter_node)
    graph.add_node("save_chapter", save_chapter)

    graph.add_edge(START, "load_chapter_job")
    graph.add_edge("load_chapter_job", "load_chapter_context")
    graph.add_edge("load_chapter_context", "write_chapter")
    graph.add_edge("write_chapter", "self_check_chapter")
    graph.add_conditional_edges(
        "self_check_chapter",
        route_after_self_check,
        {
            "rewrite_chapter": "rewrite_chapter",
            "save_chapter": "save_chapter",
        },
    )
    graph.add_edge("rewrite_chapter", "self_check_chapter")
    graph.add_edge("save_chapter", END)
    return graph.compile()
