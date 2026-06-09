from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from graph.nodes import (
    build_docx_node,
    build_markdown_node,
    extract_facts_node,
    generate_outline_node,
    global_review_node,
    init_workspace,
    parse_score_node,
    plan_chapter_jobs_node,
    review_chapters_node,
    select_contexts_node,
    split_docs_node,
    write_chapters_node,
)
from graph.state import BidState
from utils import project_root


def build_bid_graph():
    graph = StateGraph(BidState)
    graph.add_node("init_workspace", init_workspace)
    graph.add_node("split_docs", split_docs_node)
    graph.add_node("parse_score", parse_score_node)
    graph.add_node("extract_facts", extract_facts_node)
    graph.add_node("generate_outline", generate_outline_node)
    graph.add_node("plan_chapter_jobs", plan_chapter_jobs_node)
    graph.add_node("select_contexts", select_contexts_node)
    graph.add_node("write_chapters", write_chapters_node)
    graph.add_node("review_chapters", review_chapters_node)
    graph.add_node("global_review", global_review_node)
    graph.add_node("build_markdown", build_markdown_node)
    graph.add_node("build_docx", build_docx_node)

    graph.add_edge(START, "init_workspace")
    graph.add_edge("init_workspace", "split_docs")
    graph.add_edge("split_docs", "parse_score")
    graph.add_edge("parse_score", "extract_facts")
    graph.add_edge("extract_facts", "generate_outline")
    graph.add_edge("generate_outline", "plan_chapter_jobs")
    graph.add_edge("plan_chapter_jobs", "select_contexts")
    graph.add_edge("select_contexts", "write_chapters")
    graph.add_edge("write_chapters", "review_chapters")
    graph.add_edge("review_chapters", "global_review")
    graph.add_edge("global_review", "build_markdown")
    graph.add_edge("build_markdown", "build_docx")
    graph.add_edge("build_docx", END)
    return graph.compile()


def run_bid_graph(root: Path | None = None, workers: int = 1) -> BidState:
    root = root or project_root()
    graph = build_bid_graph()
    return graph.invoke(
        {
            "root_dir": str(root),
            "workers": workers,
            "completed_chapters": [],
            "failed_chapters": [],
            "errors": [],
        }
    )
