from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from file_loader import read_required_input
from llm_client import chat
from prompt_registry import load_agent_prompt
from runtime_context import agent_run
from utils import compact_json, listify, parse_json_from_model, project_root, stringify, write_json


FACT_KEYS = {
    "project_name": "",
    "bidder_name": "",
    "service_period": "",
    "warranty_period": "",
    "project_location": "",
    "core_products": [],
    "company_advantages": [],
    "similar_cases": [],
    "team_roles": [],
}

TENDER_REQUIREMENT_KEYS = {
    "project_name": "",
    "project_location": "",
    "service_period": "",
    "warranty_period": "",
    "procurement_scope": [],
    "functional_requirements": [],
    "service_requirements": [],
    "delivery_requirements": [],
    "implementation_requirements": [],
    "acceptance_requirements": [],
    "qualification_requirements": [],
    "evidence_notes": [],
}

COMPANY_FACT_KEYS = {
    "bidder_name": "",
    "core_products": [],
    "company_advantages": [],
    "similar_cases": [],
    "team_roles": [],
}


def normalize_facts(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("全局事实提取结果必须是 JSON 对象。")

    normalized: dict[str, Any] = {}
    for key, default in FACT_KEYS.items():
        value = data.get(key, default)
        if isinstance(default, list):
            normalized[key] = [item for item in listify(value) if stringify(item)]
        else:
            normalized[key] = stringify(value)
    return normalized


def normalize_tender_requirements(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("招标需求抽取结果必须是 JSON 对象。")

    normalized: dict[str, Any] = {}
    for key, default in TENDER_REQUIREMENT_KEYS.items():
        value = data.get(key, default)
        if isinstance(default, list):
            normalized[key] = [item for item in listify(value) if stringify(item)]
        else:
            normalized[key] = stringify(value)
    return normalized


def normalize_company_facts(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("公司事实抽取结果必须是 JSON 对象。")

    normalized: dict[str, Any] = {}
    for key, default in COMPANY_FACT_KEYS.items():
        value = data.get(key, default)
        if isinstance(default, list):
            normalized[key] = [item for item in listify(value) if stringify(item)]
        else:
            normalized[key] = stringify(value)
    return normalized


def _fallback_bidder_name(company_markdown: str) -> str:
    patterns = [
        r"(?:公司名称|企业名称|供应商名称|投标人名称)[：:\s]*([^\n|]{4,80}?公司)",
        r"(?:投标人|供应商)[：:\s]*([^\n|]{4,80}?公司)",
    ]
    for pattern in patterns:
        match = re.search(pattern, company_markdown)
        if match:
            return stringify(match.group(1)).strip(" ：:|")
    return ""


def extract_facts(root: Path | None = None) -> Path:
    root = root or project_root()
    tender_markdown = read_required_input(root, "tender.md", "招标文件 inputs/tender.md")
    company_markdown = read_required_input(root, "company.md", "公司资料 inputs/company.md")
    tender_prompt = load_agent_prompt(root, "tender_requirement_extractor")
    with agent_run(
        root,
        "extract_facts",
        "tender_requirement_extractor",
        input_summary={"tender_chars": len(tender_markdown)},
        temperature=0.1,
    ):
        raw_tender = chat(
            [
                {"role": "system", "content": tender_prompt},
                {
                    "role": "user",
                    "content": "请仅基于以下招标文件抽取项目需求与约束，输出 JSON。\n\n## 招标文件\n\n" + tender_markdown,
                },
            ],
            temperature=0.1,
        )
    tender_data = parse_json_from_model(raw_tender, root / "workspace" / "debug_tender_requirements_raw.txt")
    tender_requirements = normalize_tender_requirements(tender_data)
    write_json(root / "workspace" / "tender_requirements.json", tender_requirements)

    company_prompt = load_agent_prompt(root, "company_facts_extractor")
    with agent_run(
        root,
        "extract_facts",
        "company_facts_extractor",
        input_summary={"company_chars": len(company_markdown), "tender_requirement_keys": len(tender_requirements)},
        temperature=0.1,
    ):
        raw_company = chat(
            [
                {"role": "system", "content": company_prompt},
                {
                    "role": "user",
                    "content": (
                        "请仅基于以下公司资料提取可复用事实，输出 JSON。"
                        "不要引用招标要求，不要臆造。\n\n"
                        "## 公司资料\n\n"
                        f"{company_markdown}\n\n"
                        "## 已抽取的招标需求摘要\n\n"
                        f"{compact_json(tender_requirements)}"
                    ),
                },
            ],
            temperature=0.1,
        )
    company_data = parse_json_from_model(raw_company, root / "workspace" / "debug_company_facts_raw.txt")
    company_facts = normalize_company_facts(company_data)
    if not company_facts.get("bidder_name"):
        company_facts["bidder_name"] = _fallback_bidder_name(company_markdown)
    write_json(root / "workspace" / "company_facts.json", company_facts)

    facts = normalize_facts(
        {
            "project_name": tender_requirements.get("project_name", ""),
            "bidder_name": company_facts.get("bidder_name", ""),
            "service_period": tender_requirements.get("service_period", ""),
            "warranty_period": tender_requirements.get("warranty_period", ""),
            "project_location": tender_requirements.get("project_location", ""),
            "core_products": company_facts.get("core_products", []),
            "company_advantages": company_facts.get("company_advantages", []),
            "similar_cases": company_facts.get("similar_cases", []),
            "team_roles": company_facts.get("team_roles", []),
        }
    )

    output_path = root / "workspace" / "global_facts.json"
    write_json(output_path, facts)
    print(f"[完成] 已提取全局事实: {output_path}")
    return output_path
