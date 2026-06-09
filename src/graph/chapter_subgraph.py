from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.chapter_writer_agent import run as write_chapter_agent
from chapter_reviewer import review_chapter_markdown
from file_loader import load_global_facts, load_score_points
from graph.state import ChapterState
from utils import project_root, read_json, select_score_points, stringify, write_json, write_text


def load_chapter_job(state: ChapterState) -> ChapterState:
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job")
    if not job:
        chapter_id = stringify(state.get("chapter_id"))
        if not chapter_id:
            raise ValueError("章节子图缺少 job 或 chapter_id。")
        job = read_json(root / "workspace" / "jobs" / f"{chapter_id}.json")
    chapter_id = stringify(job.get("chapter_id"))
    return {"job": job, "chapter_id": chapter_id}


def load_chapter_context(state: ChapterState) -> ChapterState:
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job") or {}
    chapter_id = stringify(state.get("chapter_id") or job.get("chapter_id"))
    context_path = Path(stringify(job.get("context_path")) or root / "workspace" / "contexts" / f"{chapter_id}_context.json")
    if not context_path.is_absolute():
        context_path = root / context_path
    context = read_json(context_path)
    return {"context": context}


def write_chapter(state: ChapterState) -> ChapterState:
    root = Path(state.get("root_dir") or project_root())
    job = state.get("job") or {}
    context = state.get("context") or {}
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
        return {"self_check": self_check}
    except Exception as exc:
        return {
            "self_check": {
                "chapter_id": chapter_id,
                "chapter_title": stringify(job.get("chapter_title")),
                "score_coverage": [],
                "problems": [
                    {
                        "type": "self_check_failed",
                        "description": str(exc),
                        "suggestion": "请人工检查本章节内容。",
                    }
                ],
                "need_rewrite": True,
            },
            "error": str(exc),
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

    self_check_path = root / "workspace" / "reviews" / f"{chapter_id}_self_check.json"
    write_json(self_check_path, state.get("self_check") or {})
    print(f"[完成] 章节 SubAgent 已生成 {chapter_id}: {output_path}")
    return {"output_path": str(output_path), "self_check_path": str(self_check_path)}


def build_chapter_subgraph():
    graph = StateGraph(ChapterState)
    graph.add_node("load_chapter_job", load_chapter_job)
    graph.add_node("load_chapter_context", load_chapter_context)
    graph.add_node("write_chapter", write_chapter)
    graph.add_node("self_check_chapter", self_check_chapter)
    graph.add_node("save_chapter", save_chapter)

    graph.add_edge(START, "load_chapter_job")
    graph.add_edge("load_chapter_job", "load_chapter_context")
    graph.add_edge("load_chapter_context", "write_chapter")
    graph.add_edge("write_chapter", "self_check_chapter")
    graph.add_edge("self_check_chapter", "save_chapter")
    graph.add_edge("save_chapter", END)
    return graph.compile()
