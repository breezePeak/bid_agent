from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import load_global_facts, load_score_points, load_tender_requirements
from llm_client import chat
from prompt_registry import load_agent_prompt
from runtime_context import agent_run
from utils import compact_json, listify, parse_json_from_model, project_root, stringify, write_json


LIST_FIELDS = {
    "project_goals",
    "project_scope",
    "work_packages",
    "deliverables",
    "acceptance_focus",
    "constraints",
    "key_technologies",
    "known_standards",
    "ambiguities",
    "research_topics",
    "research_queries",
}


def normalize_project_understanding(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("项目整体理解必须是 JSON 对象。")
    normalized: dict[str, Any] = {
        "project_name": stringify(data.get("project_name")),
        "project_summary": stringify(data.get("project_summary")),
        "business_background": stringify(data.get("business_background")),
        "technical_route_hypothesis": stringify(data.get("technical_route_hypothesis")),
    }
    for key in LIST_FIELDS:
        normalized[key] = [stringify(item) for item in listify(data.get(key)) if stringify(item)]
    normalized["research_queries"] = normalized["research_queries"][:12]
    if not normalized["research_queries"]:
        raise ValueError("项目整体理解未形成 research_queries，无法进入资料搜集阶段。")
    return normalized


def analyze_project_understanding(root: Path | None = None) -> Path:
    """Build one project-level mental model before any proposal drafting."""
    root = root or project_root()
    tender_requirements = load_tender_requirements(root)
    global_facts = load_global_facts(root)
    score_points = load_score_points(root)
    prompt = load_agent_prompt(root, "project_understanding")
    with agent_run(
        root,
        "analyze_project_understanding",
        "project_understanding",
        input_summary={
            "requirement_keys": len(tender_requirements),
            "score_point_count": len(score_points),
        },
        temperature=0.1,
    ):
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        "请先对整个项目形成统一理解，并给出后续联网资料搜集问题。"
                        "不要开始编写投标章节。\n\n"
                        "## 招标需求摘要\n\n"
                        f"{compact_json(tender_requirements)}\n\n"
                        "## 评分点\n\n"
                        f"{compact_json(score_points)}\n\n"
                        "## 全局事实\n\n"
                        f"{compact_json(global_facts)}"
                    ),
                },
            ],
            temperature=0.1,
        )
    data = parse_json_from_model(
        raw,
        root / "workspace" / "debug_project_understanding_raw.txt",
    )
    understanding = normalize_project_understanding(data)
    output = root / "workspace" / "project_understanding.json"
    write_json(output, understanding)
    print(
        f"[完成] 已形成项目整体理解和 {len(understanding['research_queries'])} 条检索问题: {output}"
    )
    return output
