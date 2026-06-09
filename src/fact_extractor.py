from __future__ import annotations

from pathlib import Path
from typing import Any

from file_loader import read_required_input
from llm_client import chat
from utils import listify, load_prompt, parse_json_from_model, project_root, stringify, write_json


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


def extract_facts(root: Path | None = None) -> Path:
    root = root or project_root()
    tender_markdown = read_required_input(root, "tender.md", "招标文件 inputs/tender.md")
    company_markdown = read_required_input(root, "company.md", "公司资料 inputs/company.md")
    prompt = load_prompt(root, "extract_facts.md")

    raw = chat(
        [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "请仅基于以下资料提取全局事实。\n\n"
                    "## 招标文件\n\n"
                    f"{tender_markdown}\n\n"
                    "## 公司资料\n\n"
                    f"{company_markdown}"
                ),
            },
        ],
        temperature=0.1,
    )
    data = parse_json_from_model(raw, root / "workspace" / "debug_extract_facts_raw.txt")
    facts = normalize_facts(data)

    output_path = root / "workspace" / "global_facts.json"
    write_json(output_path, facts)
    print(f"[完成] 已提取全局事实: {output_path}")
    return output_path
