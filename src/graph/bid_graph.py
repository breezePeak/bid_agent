from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from graph.nodes import (
    build_score_coverage_matrix_node,
    estimate_final_score_node,
    build_source_trace_index_node,
    build_template_evidence_node,
    build_docx_node,
    build_markdown_node,
    check_format_node,
    compliance_check_node,
    extract_facts_node,
    generate_outline_node,
    global_review_node,
    init_workspace,
    parse_score_node,
    plan_chapter_jobs_node,
    prepare_inputs_node,
    review_fix_chapters_node,
    select_contexts_node,
    split_docs_node,
    summarize_chapters_node,
    write_chapters_node,
)
from graph.state import BidState
from graph.state_recorder import load_run_state, save_run_state
from pipeline_registry import workflow_stage_specs
from utils import read_json
from utils import project_root


def build_bid_graph():
    graph = StateGraph(BidState)
    node_map = {
        "init_workspace": init_workspace,
        "prepare_inputs": prepare_inputs_node,
        "split_docs": split_docs_node,
        "parse_score": parse_score_node,
        "extract_facts": extract_facts_node,
        "build_template_evidence": build_template_evidence_node,
        "generate_outline": generate_outline_node,
        "plan_chapter_jobs": plan_chapter_jobs_node,
        "select_contexts": select_contexts_node,
        "write_chapters": write_chapters_node,
        "review_fix_chapters": review_fix_chapters_node,
        "build_source_trace_index": build_source_trace_index_node,
        "build_score_coverage_matrix": build_score_coverage_matrix_node,
        "estimate_final_score": estimate_final_score_node,
        "summarize_chapters": summarize_chapters_node,
        "global_review": global_review_node,
        "compliance_check": compliance_check_node,
        "build_markdown": build_markdown_node,
        "build_docx": build_docx_node,
        "check_format": check_format_node,
    }
    ordered_specs = workflow_stage_specs()
    for spec in ordered_specs:
        graph.add_node(spec.id, node_map[spec.id])

    graph.add_edge(START, ordered_specs[0].id)
    for previous, current in zip(ordered_specs, ordered_specs[1:]):
        graph.add_edge(previous.id, current.id)
    graph.add_edge(ordered_specs[-1].id, END)
    return graph.compile()


def run_bid_graph(
    root: Path | None = None,
    workers: int = 1,
    resume: bool = False,
    max_retries: int = 0,
) -> BidState:
    root = root or project_root()
    graph = build_bid_graph()
    initial_state: BidState = {
        "root_dir": str(root),
        "workers": workers,
        "max_retries": max(0, int(max_retries)),
        "resume": resume,
        "completed_chapters": [],
        "failed_chapters": [],
        "errors": [],
    }
    if resume:
        previous = load_run_state(root)
        previous_state = previous.get("state", {}) if isinstance(previous.get("state"), dict) else {}
        initial_state.update(previous_state)
        initial_state["root_dir"] = str(root)
        initial_state["workers"] = workers
        initial_state["max_retries"] = max(0, int(max_retries))
        initial_state["resume"] = True

    final_state = graph.invoke(
        initial_state
    )
    final_status = "warn" if final_state.get("failed_chapters") or final_state.get("errors") else "ok"
    global_review_path = final_state.get("global_review_path")
    if isinstance(global_review_path, str) and global_review_path:
        try:
            review = read_json(Path(global_review_path))
            if isinstance(review, dict) and review.get("need_manual_review"):
                final_status = "warn"
        except Exception:
            pass
    compliance_report_path = final_state.get("compliance_report_path")
    if isinstance(compliance_report_path, str) and compliance_report_path:
        try:
            compliance = read_json(Path(compliance_report_path))
            if isinstance(compliance, dict):
                if compliance.get("blocking") or (
                    isinstance(compliance.get("summary"), dict) and compliance["summary"].get("blocking")
                ):
                    final_status = "error"
                elif compliance.get("need_manual_review") or (
                    isinstance(compliance.get("summary"), dict) and compliance["summary"].get("need_manual_review")
                ):
                    if final_status == "ok":
                        final_status = "warn"
        except Exception:
            pass
    final_message = ""
    if final_state.get("errors"):
        final_message = "; ".join(str(item) for item in final_state.get("errors", [])[:5])
    save_run_state(root, final_state, stage="finished", status=final_status, message=final_message)
    return final_state
