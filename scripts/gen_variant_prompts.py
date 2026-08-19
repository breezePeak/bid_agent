from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"

AGENTS = {
    "tender_requirement_extractor.md": "tender_requirement_extractor",
    "generate_outline.md": "outline_generator",
    "select_context.md": "chapter_context_selector",
    "global_review.md": "global_reviewer",
}

GUIDANCE = {
    "government_procurement": {
        "tender_requirement_extractor": "优先识别资格审查、政策符合性、投标文件格式、响应与偏离相关约束，避免把通用宣传语言写成确定事实。",
        "outline_generator": "优先体现资格响应、方案符合性、实施组织、服务保障、风险控制和验收条款响应，结构保持严谨保守。",
        "chapter_context_selector": "优先选择能支撑资格审查、合规承诺、服务响应和评分细则的证据片段。",
        "global_reviewer": "重点核查项目名称、供应商主体、资格响应、服务期限、偏离风险和合规措辞。",
    },
    "software_project": {
        "tender_requirement_extractor": "优先识别功能需求、性能要求、接口要求、实施里程碑、测试验收和交付物约束。",
        "outline_generator": "优先覆盖需求理解、总体方案、技术架构、实施计划、测试验收、运维与培训。",
        "chapter_context_selector": "优先选择架构、功能、实施、测试、交付和案例相关片段。",
        "global_reviewer": "重点核查功能闭环、架构一致性、实施周期、验收标准和案例适配性。",
    },
    "ops_service": {
        "tender_requirement_extractor": "优先识别服务范围、SLA、响应时间、值守机制、巡检、应急和考核要求。",
        "outline_generator": "优先覆盖服务理解、服务体系、SLA、值守与响应、应急预案、质量考核和持续改进。",
        "chapter_context_selector": "优先选择服务保障、团队、响应、应急和运维案例相关片段。",
        "global_reviewer": "重点核查服务承诺一致性、响应时效、值守安排、交付周期和运维能力证据。",
    },
    "system_integration": {
        "tender_requirement_extractor": "优先识别集成范围、接口边界、软硬件协同、部署调试、联调联试和交付约束。",
        "outline_generator": "优先覆盖集成理解、总体架构、接口方案、实施部署、联调测试、交付验收和风险控制。",
        "chapter_context_selector": "优先选择接口、设备、平台、实施、部署和集成案例相关片段。",
        "global_reviewer": "重点核查系统边界、接口关系、实施依赖、交付路径和案例匹配度。",
    },
}

LABELS = {
    "government_procurement": "政务采购",
    "software_project": "软件项目",
    "ops_service": "运维服务",
    "system_integration": "系统集成",
}


def main() -> None:
    count = 0
    for base_file, agent in AGENTS.items():
        base = (PROMPTS / base_file).read_text(encoding="utf-8").rstrip()
        for ptype, gmap in GUIDANCE.items():
            out = PROMPTS / f"{base_file[:-3]}.{ptype}.md"
            text = (
                f"{base}\n\n"
                f"## 项目类型变体：{LABELS[ptype]}（{ptype}）\n\n"
                f"在遵守上文全部硬性要求的前提下，额外遵循：\n"
                f"1. {gmap[agent]}\n"
                f"2. 表述风格与证据标准服从该项目类型常见评审口径。\n"
                f"3. 无证据时仍禁止编造；优先拟响应/按要求提交/附后说明。\n"
            )
            out.write_text(text + "\n", encoding="utf-8")
            count += 1
    print(f"wrote {count} variant prompts")


if __name__ == "__main__":
    main()
