from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from project_profile_registry import (
    PROFILE_AGENT_COVERAGE,
    load_project_profile,
    prompt_profile_guidance,
    variant_prompt_filename,
)
from utils import read_nonempty_text


@dataclass(frozen=True)
class AgentSpec:
    name: str
    prompt_file: str
    version: str
    input_contract: dict[str, Any] = field(default_factory=dict)
    output_contract: dict[str, Any] = field(default_factory=dict)
    context_budget: dict[str, int] = field(default_factory=dict)


AGENT_SPECS: dict[str, AgentSpec] = {
    "score_requirement_extractor": AgentSpec(
        name="score_requirement_extractor",
        prompt_file="score_requirement_extractor.md",
        version="1.0.0",
        input_contract={"documents": ["inputs/score.md"], "mode": "score_chunk_extract"},
        output_contract={"type": "json_array", "item_keys": ["category", "title", "score", "requirement", "scoring_criteria", "keywords", "source_excerpt"]},
    ),
    "score_point_parser": AgentSpec(
        name="score_point_parser",
        prompt_file="score_point_parser.md",
        version="1.0.0",
        input_contract={"documents": ["workspace/score_requirements.json"], "mode": "score_point_normalize"},
        output_contract={"type": "json_array", "item_keys": ["id", "category", "title", "score", "requirement", "keywords", "response_strategy"]},
    ),
    "tender_requirement_extractor": AgentSpec(
        name="tender_requirement_extractor",
        prompt_file="tender_requirement_extractor.md",
        version="1.0.0",
        input_contract={"documents": ["inputs/tender.md"], "mode": "tender_requirement_extract"},
        output_contract={"type": "json_object", "required_keys": ["project_name", "procurement_scope", "qualification_requirements"]},
    ),
    "company_facts_extractor": AgentSpec(
        name="company_facts_extractor",
        prompt_file="company_facts_extractor.md",
        version="1.0.0",
        input_contract={"documents": ["inputs/company.md", "workspace/tender_requirements.json"], "mode": "company_fact_extract"},
        output_contract={"type": "json_object", "required_keys": ["bidder_name", "core_products", "company_advantages"]},
    ),
    "project_understanding": AgentSpec(
        name="project_understanding",
        prompt_file="project_understanding.md",
        version="1.0.0",
        input_contract={
            "documents": [
                "workspace/tender_requirements.json",
                "workspace/score_points.json",
                "workspace/global_facts.json",
            ],
            "mode": "whole_project_understanding",
        },
        output_contract={
            "type": "json_object",
            "required_keys": [
                "project_summary",
                "project_scope",
                "work_packages",
                "deliverables",
                "research_queries",
            ],
        },
    ),
    "outline_generator": AgentSpec(
        name="outline_generator",
        prompt_file="generate_outline.md",
        version="1.0.0",
        input_contract={"documents": ["inputs/tender.md", "inputs/reference.md", "inputs/writing_brief.md", "workspace/score_points.json", "workspace/global_facts.json", "workspace/template_evidence_map.json"], "mode": "outline_generate"},
        output_contract={"type": "json_object", "required_keys": ["chapters"]},
        context_budget={"max_context_chars": 20000},
    ),
    "chapter_context_selector": AgentSpec(
        name="chapter_context_selector",
        prompt_file="select_context.md",
        version="1.0.0",
        input_contract={"documents": ["workspace/jobs/*.json", "workspace/chunks/tender_chunks.json", "workspace/chunks/company_chunks.json", "workspace/chunks/reference_chunks.json", "inputs/writing_brief.md"], "mode": "context_select"},
        output_contract={"type": "json_object", "required_keys": ["selected_tender_chunks", "selected_company_chunks"]},
        context_budget={"max_context_chars": 18000, "max_chunks": 30},
    ),
    "chapter_writer": AgentSpec(
        name="chapter_writer",
        prompt_file="write_chapter.md",
        version="1.0.0",
        input_contract={"documents": ["workspace/jobs/*.json", "workspace/contexts/*_context.json", "workspace/global_facts.json", "workspace/tender_requirements.json", "inputs/writing_brief.md"], "mode": "chapter_write"},
        output_contract={"type": "markdown", "required_features": ["chapter_heading"]},
        context_budget={"max_context_chars": 16000, "max_chunks": 16},
    ),
    "chapter_reviewer": AgentSpec(
        name="chapter_reviewer",
        prompt_file="review_chapter.md",
        version="1.1.0",
        input_contract={"documents": ["workspace/chapters/*.md", "workspace/jobs/*.json", "workspace/global_facts.json"], "mode": "chapter_review"},
        output_contract={"type": "json_object", "required_keys": ["score_coverage", "problems", "priority_fixes", "need_rewrite"]},
        context_budget={"max_context_chars": 14000},
    ),
    "chapter_rewriter": AgentSpec(
        name="chapter_rewriter",
        prompt_file="rewrite_chapter.md",
        version="1.1.0",
        input_contract={"documents": ["workspace/chapters/*.md", "workspace/reviews/*_review.json", "workspace/contexts/*_context.json"], "mode": "chapter_rewrite"},
        output_contract={"type": "markdown", "required_features": ["chapter_heading"]},
        context_budget={"max_context_chars": 17000, "max_chunks": 16},
    ),
    "chapter_summarizer": AgentSpec(
        name="chapter_summarizer",
        prompt_file="summarize_chapter.md",
        version="1.0.0",
        input_contract={"documents": ["workspace/chapters/*.md", "workspace/jobs/*.json", "workspace/reviews/*_review.json"], "mode": "chapter_summarize"},
        output_contract={"type": "json_object", "required_keys": ["chapter_id", "main_claims", "fabrication_risks"]},
    ),
    "global_reviewer": AgentSpec(
        name="global_reviewer",
        prompt_file="global_review.md",
        version="1.0.0",
        input_contract={"documents": ["workspace/global_facts.json", "workspace/outline.json", "workspace/reviews/*_review.json", "workspace/summaries/*_summary.json"], "mode": "global_review"},
        output_contract={"type": "json_object", "required_keys": ["need_manual_review", "uncovered_score_points", "fabrication_risks"]},
        context_budget={"max_context_chars": 20000},
    ),
    "tender_block_classifier": AgentSpec(
        name="tender_block_classifier",
        prompt_file="classify_tender_blocks.md",
        version="1.0.0",
        input_contract={"documents": ["workspace/imported/tender_blocks.json"], "mode": "tender_block_classify"},
        output_contract={"type": "json_array", "item_keys": ["block_id", "category", "target_file", "confidence"]},
    ),
}


def agent_spec_for(agent_name: str) -> AgentSpec:
    try:
        return AGENT_SPECS[agent_name]
    except KeyError as exc:
        raise KeyError(f"未配置 agent 提示词: {agent_name}") from exc


def prompt_file_for_agent(agent_name: str) -> str:
    return agent_spec_for(agent_name).prompt_file


def prompt_checksum(prompt_text: str) -> str:
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


def resolve_prompt_spec(root: Path, agent_name: str) -> dict[str, str]:
    spec = agent_spec_for(agent_name)
    base_prompt = read_nonempty_text(root / "prompts" / spec.prompt_file, f"Agent 提示词 {agent_name}")
    project_profile = load_project_profile(root)
    project_type = str(project_profile.get("project_type", "general"))
    if project_type == "general" or agent_name not in PROFILE_AGENT_COVERAGE:
        return {
            "prompt_file": spec.prompt_file,
            "prompt_text": base_prompt,
            "version": spec.version,
            "project_type": project_type,
        }

    variant_file = variant_prompt_filename(spec.prompt_file, project_type)
    variant_path = root / "prompts" / variant_file
    if variant_path.exists():
        prompt_text = read_nonempty_text(variant_path, f"项目类型提示词 {agent_name}")
    else:
        guidance = prompt_profile_guidance(project_type, agent_name)
        prompt_text = (
            f"{base_prompt}\n\n"
            "## 项目类型补充指令\n\n"
            f"当前项目类型：{project_type}\n"
            f"{guidance}\n"
        )
    return {
        "prompt_file": variant_file,
        "prompt_text": prompt_text,
        "version": f"{spec.version}+{project_type}",
        "project_type": project_type,
    }


def load_agent_prompt(root: Path, agent_name: str) -> str:
    resolved = resolve_prompt_spec(root, agent_name)
    prompt = resolved["prompt_text"]
    checksum = prompt_checksum(prompt)
    try:
        from runtime_context import register_prompt_metadata

        register_prompt_metadata(
            agent_name,
            resolved["prompt_file"],
            resolved["version"],
            checksum,
            project_type=resolved["project_type"],
        )
    except Exception:
        pass
    return prompt


def required_prompt_files() -> list[str]:
    return sorted({spec.prompt_file for spec in AGENT_SPECS.values()})
