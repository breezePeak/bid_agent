from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils import read_json, write_json


@dataclass(frozen=True)
class ProjectProfile:
    project_type: str
    label: str
    description: str


PROJECT_PROFILES: dict[str, ProjectProfile] = {
    "general": ProjectProfile("general", "通用项目", "默认项目类型，不附加行业化 prompt 指令。"),
    "government_procurement": ProjectProfile("government_procurement", "政务采购", "强调合规响应、政策语言、审查口径和保守表述。"),
    "software_project": ProjectProfile("software_project", "软件项目", "强调需求分析、架构设计、功能边界、实施与验收。"),
    "ops_service": ProjectProfile("ops_service", "运维服务", "强调服务目录、SLA、响应时效、故障处置和持续保障。"),
    "system_integration": ProjectProfile("system_integration", "系统集成", "强调集成边界、接口协同、设备联动、部署与交付。"),
}

DEFAULT_PROJECT_TYPE = "general"
PROFILE_AGENT_COVERAGE = {
    "tender_requirement_extractor",
    "outline_generator",
    "chapter_context_selector",
    "chapter_writer",
    "global_reviewer",
}

PROFILE_AGENT_GUIDANCE: dict[str, dict[str, str]] = {
    "government_procurement": {
        "tender_requirement_extractor": "补充要求：优先识别资格审查、政策符合性、投标文件格式、响应与偏离相关约束，避免把通用宣传语言写成确定事实。",
        "outline_generator": "补充要求：优先体现资格响应、方案符合性、实施组织、服务保障、风险控制和验收条款响应，结构保持严谨保守。",
        "chapter_context_selector": "补充要求：优先选择能支撑资格审查、合规承诺、服务响应和评分细则的证据片段。",
        "chapter_writer": "补充要求：采用正式、审慎、政务采购口径，避免商业化夸张表达，对证据不足内容使用拟响应/按要求提交等保守表述。",
        "global_reviewer": "补充要求：重点核查项目名称、供应商主体、资格响应、服务期限、偏离风险和合规措辞。",
    },
    "software_project": {
        "tender_requirement_extractor": "补充要求：优先识别功能需求、性能要求、接口要求、实施里程碑、测试验收和交付物约束。",
        "outline_generator": "补充要求：优先覆盖需求理解、总体方案、技术架构、实施计划、测试验收、运维与培训。",
        "chapter_context_selector": "补充要求：优先选择架构、功能、实施、测试、交付和案例相关片段。",
        "chapter_writer": "补充要求：更强调功能拆解、架构设计、实施步骤、测试验收和技术可落地性，避免空泛服务话术。",
        "global_reviewer": "补充要求：重点核查功能闭环、架构一致性、实施周期、验收标准和案例适配性。",
    },
    "ops_service": {
        "tender_requirement_extractor": "补充要求：优先识别服务范围、SLA、响应时间、值守机制、巡检、应急和考核要求。",
        "outline_generator": "补充要求：优先覆盖服务理解、服务体系、SLA、值守与响应、应急预案、质量考核和持续改进。",
        "chapter_context_selector": "补充要求：优先选择服务保障、团队、响应、应急和运维案例相关片段。",
        "chapter_writer": "补充要求：更强调服务流程、响应机制、SLA 指标、值守安排、故障闭环和持续保障。",
        "global_reviewer": "补充要求：重点核查服务承诺一致性、响应时效、值守安排、交付周期和运维能力证据。",
    },
    "system_integration": {
        "tender_requirement_extractor": "补充要求：优先识别集成范围、接口边界、软硬件协同、部署调试、联调联试和交付约束。",
        "outline_generator": "补充要求：优先覆盖集成理解、总体架构、接口方案、实施部署、联调测试、交付验收和风险控制。",
        "chapter_context_selector": "补充要求：优先选择接口、设备、平台、实施、部署和集成案例相关片段。",
        "chapter_writer": "补充要求：更强调系统边界、接口协同、软硬件集成、部署联调、风险控制和交付责任分工。",
        "global_reviewer": "补充要求：重点核查系统边界、接口关系、实施依赖、交付路径和案例匹配度。",
    },
}


def normalize_project_type(project_type: str | None) -> str:
    text = (project_type or "").strip().lower()
    return text if text in PROJECT_PROFILES else DEFAULT_PROJECT_TYPE


def project_profile_choices() -> list[dict[str, str]]:
    return [
        {
            "project_type": profile.project_type,
            "label": profile.label,
            "description": profile.description,
        }
        for profile in PROJECT_PROFILES.values()
    ]


def default_project_profile_payload(project_type: str | None = None) -> dict[str, Any]:
    normalized = normalize_project_type(project_type)
    profile = PROJECT_PROFILES[normalized]
    return {
        "project_type": normalized,
        "label": profile.label,
        "description": profile.description,
        "updated_at": "",
    }


def project_profile_path(root: Path) -> Path:
    return root / "workspace" / "project_profile.json"


def load_project_profile(root: Path) -> dict[str, Any]:
    path = project_profile_path(root)
    if not path.exists():
        return default_project_profile_payload()
    try:
        data = read_json(path)
    except Exception:
        return default_project_profile_payload()
    if not isinstance(data, dict):
        return default_project_profile_payload()
    normalized = normalize_project_type(str(data.get("project_type", "")))
    profile = PROJECT_PROFILES[normalized]
    result = {
        "project_type": normalized,
        "label": profile.label,
        "description": profile.description,
        "updated_at": str(data.get("updated_at", "")),
    }
    expected_pages = data.get("expected_pages", 0)
    if isinstance(expected_pages, (int, float)) and int(expected_pages) > 0:
        result["expected_pages"] = int(expected_pages)
    return result


def save_project_profile(root: Path, project_type: str | None, expected_pages: int = 0) -> Path:
    normalized = normalize_project_type(project_type)
    profile = PROJECT_PROFILES[normalized]
    path = project_profile_path(root)
    payload: dict[str, Any] = {
        "project_type": normalized,
        "label": profile.label,
        "description": profile.description,
        "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if expected_pages > 0:
        payload["expected_pages"] = expected_pages
    write_json(path, payload)
    return path


def variant_prompt_filename(prompt_file: str, project_type: str) -> str:
    if project_type == DEFAULT_PROJECT_TYPE:
        return prompt_file
    suffix = f".{project_type}.md"
    if prompt_file.endswith(".md"):
        return prompt_file[:-3] + suffix
    return prompt_file + suffix


def prompt_profile_guidance(project_type: str, agent_name: str) -> str:
    normalized = normalize_project_type(project_type)
    return PROFILE_AGENT_GUIDANCE.get(normalized, {}).get(agent_name, "")

