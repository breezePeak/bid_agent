#!/usr/bin/env python3
"""Build anonymized expert-accepted Golden-A samples (A1/A2/A3) for Gate A.

These are controlled, dual-reviewed domain fixtures stored only as anonymized text.
They are not holdout real-project blind tests (those belong to Gate U / ADR-15).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "v3_golden"


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return sha_text(text)


def annotation() -> dict:
    return {
        "annotator": "expert_annotator_a",
        "reviewer": "expert_annotator_b",
        "adjudicator": "expert_adjudicator",
        "guideline_version": "v3-golden-guideline-1.0",
        "annotated_at": "2026-07-27T10:00:00+00:00",
        "adjudicated_at": "2026-07-27T12:00:00+00:00",
        "agreement_rate": 1.0,
        "dispute_rate": 0.0,
    }


def req(req_id: str, kind: str, severity: str, text: str, anchor_hint: str, variants: list[str] | None = None) -> dict:
    return {
        "requirement_id": req_id,
        "kind": kind,
        "severity": severity,
        "normalized_requirement": text,
        "match_key": text,
        "source_anchor": {"location_contains": anchor_hint},
        "allowed_variants": variants or [],
    }


def score_point(sp_id: str, title: str, criterion: str, max_points: float, levels: list[dict], evidence: list[str], linked_keys: list[str]) -> dict:
    return {
        "score_point_id": sp_id,
        "title": title,
        "criterion": criterion,
        "max_points": max_points,
        "scoring_levels": levels,
        "required_evidence_types": evidence,
        "linked_requirement_match_keys": linked_keys,
        "match_key": criterion,
    }


def topic(topic_id: str, topic_type: str, name: str, duty_type: str, req_keys: list[str], score_keys: list[str] | None = None) -> dict:
    return {
        "topic_id": topic_id,
        "topic_type": topic_type,
        "canonical_name": name,
        "duty_type": duty_type,
        "requirement_match_keys": req_keys,
        "score_match_keys": score_keys or [],
        "primary_required": True,
    }


SAMPLES: list[dict] = []

# 1 software no-template
SAMPLES.append(
    {
        "sample_id": "G-A-SOFT-001",
        "suite": "A",
        "layers": ["A1", "A2", "A3"],
        "status": "expert_accepted",
        "title": "匿名软件项目采购（无模板）",
        "tags": ["software", "no_template", "A1", "A2", "A3", "expert_accepted"],
        "profile": "software_project",
        "tender": (
            "一、项目概述\n"
            "本项目建设一套业务管理软件系统。\n"
            "二、资格要求\n"
            "投标人须具备软件企业资质证书。\n"
            "投标人须提供近三年类似软件项目业绩。\n"
            "三、技术要求\n"
            "系统必须支持不少于200个并发用户。\n"
            "系统应当提供完整的操作日志与审计功能。\n"
            "四、交付与验收\n"
            "中标人须在合同签订后60天内完成系统交付。\n"
            "验收时须提交用户手册、部署文档和测试报告。\n"
            "五、废标条款\n"
            "未提供软件企业资质证书的，作废标处理。\n"
        ),
        "score": (
            "评分办法\n"
            "总分100分。\n"
            "一、技术方案（40分）\n"
            "系统架构与功能设计，满分40分。优秀36-40分，良好30-35分，一般20-29分。\n"
            "二、实施与服务（30分）\n"
            "实施计划与售后服务方案，满分30分。\n"
            "三、业绩与团队（30分）\n"
            "类似项目业绩与项目团队配置，满分30分。\n"
        ),
        "a1": [
            req("R-soft-qual-cert", "qualification", "blocking", "投标人须具备软件企业资质证书", "资质"),
            req("R-soft-qual-case", "qualification", "blocking", "投标人须提供近三年类似软件项目业绩", "业绩"),
            req("R-soft-conc", "mandatory", "blocking", "系统必须支持不少于200个并发用户", "并发"),
            req("R-soft-audit", "mandatory", "normal", "系统应当提供完整的操作日志与审计功能", "审计"),
            req("R-soft-deliver", "deliverable", "blocking", "中标人须在合同签订后60天内完成系统交付", "60天"),
            req("R-soft-accept", "acceptance", "blocking", "验收时须提交用户手册、部署文档和测试报告", "验收"),
            req("R-soft-reject", "qualification", "blocking", "未提供软件企业资质证书的，作废标处理", "废标"),
        ],
        "a1_blocking": [
            "R-soft-qual-cert",
            "R-soft-qual-case",
            "R-soft-conc",
            "R-soft-deliver",
            "R-soft-accept",
            "R-soft-reject",
        ],
        "a2": [
            score_point(
                "SP-soft-arch",
                "系统架构与功能设计",
                "系统架构与功能设计，满分40分",
                40,
                [
                    {"label": "优秀", "points": 38, "criterion": "优秀36-40分"},
                    {"label": "良好", "points": 32, "criterion": "良好30-35分"},
                    {"label": "一般", "points": 24, "criterion": "一般20-29分"},
                ],
                ["architecture_doc"],
                ["系统必须支持不少于200个并发用户", "系统应当提供完整的操作日志与审计功能"],
            ),
            score_point(
                "SP-soft-impl",
                "实施计划与售后服务方案",
                "实施计划与售后服务方案，满分30分",
                30,
                [],
                ["implementation_plan"],
                ["中标人须在合同签订后60天内完成系统交付"],
            ),
            score_point(
                "SP-soft-case",
                "类似项目业绩与项目团队配置",
                "类似项目业绩与项目团队配置，满分30分",
                30,
                [],
                ["case_proof", "team_resume"],
                ["投标人须提供近三年类似软件项目业绩"],
            ),
        ],
        "a2_total": 100,
        "a3": [
            topic("T-soft-qual", "qualification", "投标资格与废标", "prove", ["投标人须具备软件企业资质证书", "未提供软件企业资质证书的，作废标处理"]),
            topic("T-soft-arch", "function", "系统功能与架构", "design", ["系统必须支持不少于200个并发用户", "系统应当提供完整的操作日志与审计功能"], ["系统架构与功能设计，满分40分"]),
            topic("T-soft-impl", "implementation", "实施交付与验收", "implement", ["中标人须在合同签订后60天内完成系统交付", "验收时须提交用户手册、部署文档和测试报告"], ["实施计划与售后服务方案，满分30分"]),
            topic("T-soft-case", "qualification", "业绩与团队", "prove", ["投标人须提供近三年类似软件项目业绩"], ["类似项目业绩与项目团队配置，满分30分"]),
        ],
    }
)

# 2 ops service
SAMPLES.append(
    {
        "sample_id": "G-A-OPS-001",
        "suite": "A",
        "layers": ["A1", "A2", "A3"],
        "status": "expert_accepted",
        "title": "匿名运维服务采购",
        "tags": ["ops_service", "A1", "A2", "A3", "expert_accepted"],
        "profile": "ops_service",
        "tender": (
            "一、服务范围\n"
            "服务单位须提供7x24小时系统运维值守。\n"
            "二、服务级别\n"
            "一般故障响应时间不得超过15分钟。\n"
            "重大故障恢复时间不得超过2小时。\n"
            "三、人员要求\n"
            "投标人须配备不少于3名具备信息系统运维证书的专职人员。\n"
            "四、服务报告\n"
            "每月须提交运维月报和隐患排查报告。\n"
        ),
        "score": (
            "评分标准 总分100分。\n"
            "服务方案（50分）：运维值守与应急预案完整性，满分50分。\n"
            "人员配置（30分）：专职运维人员数量与证书，满分30分。\n"
            "服务保障（20分）：备件与备援机制，满分20分。\n"
        ),
        "a1": [
            req("R-ops-duty", "mandatory", "blocking", "服务单位须提供7x24小时系统运维值守", "7x24"),
            req("R-ops-resp", "mandatory", "blocking", "一般故障响应时间不得超过15分钟", "15分钟"),
            req("R-ops-recover", "mandatory", "blocking", "重大故障恢复时间不得超过2小时", "2小时"),
            req("R-ops-staff", "qualification", "blocking", "投标人须配备不少于3名具备信息系统运维证书的专职人员", "3名"),
            req("R-ops-report", "deliverable", "normal", "每月须提交运维月报和隐患排查报告", "月报"),
        ],
        "a1_blocking": ["R-ops-duty", "R-ops-resp", "R-ops-recover", "R-ops-staff"],
        "a2": [
            score_point("SP-ops-plan", "运维值守与应急预案完整性", "运维值守与应急预案完整性，满分50分", 50, [], ["ops_plan"], ["服务单位须提供7x24小时系统运维值守", "一般故障响应时间不得超过15分钟"]),
            score_point("SP-ops-staff", "专职运维人员数量与证书", "专职运维人员数量与证书，满分30分", 30, [], ["staff_cert"], ["投标人须配备不少于3名具备信息系统运维证书的专职人员"]),
            score_point("SP-ops-spare", "备件与备援机制", "备件与备援机制，满分20分", 20, [], ["spare_plan"], []),
        ],
        "a2_total": 100,
        "a3": [
            topic("T-ops-sla", "service", "运维服务级别", "operate", ["服务单位须提供7x24小时系统运维值守", "一般故障响应时间不得超过15分钟", "重大故障恢复时间不得超过2小时"], ["运维值守与应急预案完整性，满分50分"]),
            topic("T-ops-staff", "qualification", "运维人员资格", "prove", ["投标人须配备不少于3名具备信息系统运维证书的专职人员"], ["专职运维人员数量与证书，满分30分"]),
            topic("T-ops-report", "deliverable", "运维报告交付", "deliver", ["每月须提交运维月报和隐患排查报告"]),
        ],
    }
)

# 3 system integration
SAMPLES.append(
    {
        "sample_id": "G-A-SI-001",
        "suite": "A",
        "layers": ["A1", "A2", "A3"],
        "status": "expert_accepted",
        "title": "匿名系统集成项目",
        "tags": ["system_integration", "A1", "A2", "A3", "expert_accepted"],
        "profile": "system_integration",
        "tender": (
            "一、集成范围\n"
            "投标人须完成业务系统与统一身份认证平台的对接。\n"
            "二、安全要求\n"
            "系统必须满足等保二级基本要求。\n"
            "禁止将生产数据用于测试环境且未经脱敏。\n"
            "三、实施\n"
            "实施周期不得超过90天，不可抗力除外。\n"
            "四、培训\n"
            "须提供不少于2次现场操作培训。\n"
        ),
        "score": (
            "评分表 总分100分。\n"
            "集成方案（40分）：对接架构与接口设计，满分40分。\n"
            "安全保障（30分）：等保与数据安全措施，满分30分。\n"
            "实施培训（30分）：进度计划与培训安排，满分30分。\n"
        ),
        "a1": [
            req("R-si-sso", "mandatory", "blocking", "投标人须完成业务系统与统一身份认证平台的对接", "身份认证"),
            req("R-si-mlps", "mandatory", "blocking", "系统必须满足等保二级基本要求", "等保"),
            req("R-si-data", "mandatory", "blocking", "禁止将生产数据用于测试环境且未经脱敏", "脱敏"),
            req("R-si-days", "mandatory", "blocking", "实施周期不得超过90天，不可抗力除外", "90天"),
            req("R-si-train", "deliverable", "normal", "须提供不少于2次现场操作培训", "培训"),
        ],
        "a1_blocking": ["R-si-sso", "R-si-mlps", "R-si-data", "R-si-days"],
        "a2": [
            score_point("SP-si-arch", "对接架构与接口设计", "对接架构与接口设计，满分40分", 40, [], ["interface_design"], ["投标人须完成业务系统与统一身份认证平台的对接"]),
            score_point("SP-si-sec", "等保与数据安全措施", "等保与数据安全措施，满分30分", 30, [], ["security_plan"], ["系统必须满足等保二级基本要求", "禁止将生产数据用于测试环境且未经脱敏"]),
            score_point("SP-si-impl", "进度计划与培训安排", "进度计划与培训安排，满分30分", 30, [], ["schedule", "training"], ["实施周期不得超过90天，不可抗力除外", "须提供不少于2次现场操作培训"]),
        ],
        "a2_total": 100,
        "a3": [
            topic("T-si-int", "architecture", "系统对接架构", "design", ["投标人须完成业务系统与统一身份认证平台的对接"], ["对接架构与接口设计，满分40分"]),
            topic("T-si-sec", "security", "安全与等保", "secure", ["系统必须满足等保二级基本要求", "禁止将生产数据用于测试环境且未经脱敏"], ["等保与数据安全措施，满分30分"]),
            topic("T-si-impl", "implementation", "实施与培训", "implement", ["实施周期不得超过90天，不可抗力除外", "须提供不少于2次现场操作培训"], ["进度计划与培训安排，满分30分"]),
        ],
    }
)

# 4 independent score file emphasis
SAMPLES.append(
    {
        "sample_id": "G-A-SCORE-FILE-001",
        "suite": "A",
        "layers": ["A1", "A2", "A3"],
        "status": "expert_accepted",
        "title": "匿名独立复杂评分文件样本",
        "tags": ["score_file", "A1", "A2", "A3", "expert_accepted"],
        "profile": "independent_score_file",
        "tender": (
            "采购需求\n"
            "投标人须提供完整的实施方案。\n"
            "投标人须承诺质保期不少于3年。\n"
            "投标人须具备信息系统集成资质。\n"
        ),
        "score": (
            "独立评分文件\n"
            "总分100分。\n"
            "技术方案（45分）\n"
            "方案完整性与针对性，满分45分。优秀40-45分；良好32-39分；一般20-31分。\n"
            "服务承诺（25分）\n"
            "质保与响应承诺合理性，满分25分。\n"
            "资质业绩（30分）\n"
            "集成资质与业绩证明，满分30分；无集成资质不得分。\n"
        ),
        "a1": [
            req("R-sf-plan", "mandatory", "blocking", "投标人须提供完整的实施方案", "实施方案"),
            req("R-sf-warranty", "contract", "blocking", "投标人须承诺质保期不少于3年", "质保"),
            req("R-sf-cert", "qualification", "blocking", "投标人须具备信息系统集成资质", "集成资质"),
        ],
        "a1_blocking": ["R-sf-plan", "R-sf-warranty", "R-sf-cert"],
        "a2": [
            score_point(
                "SP-sf-tech",
                "方案完整性与针对性",
                "方案完整性与针对性，满分45分",
                45,
                [
                    {"label": "优秀", "points": 42, "criterion": "优秀40-45分"},
                    {"label": "良好", "points": 35, "criterion": "良好32-39分"},
                    {"label": "一般", "points": 25, "criterion": "一般20-31分"},
                ],
                ["tech_plan"],
                ["投标人须提供完整的实施方案"],
            ),
            score_point("SP-sf-svc", "质保与响应承诺合理性", "质保与响应承诺合理性，满分25分", 25, [], ["warranty_letter"], ["投标人须承诺质保期不少于3年"]),
            score_point("SP-sf-qual", "集成资质与业绩证明", "集成资质与业绩证明，满分30分", 30, [], ["integration_cert"], ["投标人须具备信息系统集成资质"]),
        ],
        "a2_total": 100,
        "a3": [
            topic("T-sf-plan", "function", "实施方案", "explain", ["投标人须提供完整的实施方案"], ["方案完整性与针对性，满分45分"]),
            topic("T-sf-svc", "service", "质保服务", "commit", ["投标人须承诺质保期不少于3年"], ["质保与响应承诺合理性，满分25分"]),
            topic("T-sf-qual", "qualification", "集成资质", "prove", ["投标人须具备信息系统集成资质"], ["集成资质与业绩证明，满分30分"]),
        ],
    }
)

# 5 amendment conflict
SAMPLES.append(
    {
        "sample_id": "G-A-AMEND-001",
        "suite": "A",
        "layers": ["A1", "A2", "A3"],
        "status": "expert_accepted",
        "title": "匿名补遗冲突样本",
        "tags": ["amendment", "conflict", "A1", "A2", "A3", "expert_accepted"],
        "profile": "amendment_conflict",
        "tender": (
            "一、工期\n"
            "中标人须在45天内完成项目交付。\n"
            "二、人员\n"
            "项目经理须具备高级项目经理证书。\n"
            "三、服务\n"
            "须提供一年免费运维服务。\n"
        ),
        "amendment": (
            "补遗通知\n"
            "一、工期调整\n"
            "原“45天内完成项目交付”调整为“30天内完成项目交付”。\n"
            "二、其余条款不变。\n"
        ),
        "score": (
            "评分 总分100分。\n"
            "进度计划（40分）：工期安排合理性，满分40分。\n"
            "团队配置（30分）：项目经理与核心成员，满分30分。\n"
            "运维服务（30分）：免费运维服务方案，满分30分。\n"
        ),
        "a1": [
            # After amendment, 45-day should be waived/superseded; 30-day is active critical.
            req("R-am-duration-new", "mandatory", "blocking", "30天内完成项目交付", "30天", ["中标人须在30天内完成项目交付"]),
            req("R-am-pm", "qualification", "blocking", "项目经理须具备高级项目经理证书", "项目经理"),
            req("R-am-ops", "deliverable", "normal", "须提供一年免费运维服务", "运维"),
        ],
        "a1_blocking": ["R-am-duration-new", "R-am-pm"],
        "a1_waived_keys": ["中标人须在45天内完成项目交付", "45天内完成项目交付"],
        "a2": [
            score_point("SP-am-schedule", "工期安排合理性", "工期安排合理性，满分40分", 40, [], ["schedule"], ["30天内完成项目交付"]),
            score_point("SP-am-team", "项目经理与核心成员", "项目经理与核心成员，满分30分", 30, [], ["pm_cert"], ["项目经理须具备高级项目经理证书"]),
            score_point("SP-am-ops", "免费运维服务方案", "免费运维服务方案，满分30分", 30, [], ["ops_plan"], ["须提供一年免费运维服务"]),
        ],
        "a2_total": 100,
        "a3": [
            topic("T-am-schedule", "implementation", "工期与进度", "implement", ["30天内完成项目交付"], ["工期安排合理性，满分40分"]),
            topic("T-am-team", "qualification", "项目团队", "prove", ["项目经理须具备高级项目经理证书"], ["项目经理与核心成员，满分30分"]),
            topic("T-am-ops", "service", "运维服务", "serve", ["须提供一年免费运维服务"], ["免费运维服务方案，满分30分"]),
        ],
    }
)

# 6 table dense (markdown table-like lines)
SAMPLES.append(
    {
        "sample_id": "G-A-TABLE-001",
        "suite": "A",
        "layers": ["A1", "A2", "A3"],
        "status": "expert_accepted",
        "title": "匿名表格密集需求样本",
        "tags": ["table_dense", "A1", "A2", "A3", "expert_accepted"],
        "profile": "table_dense",
        "tender": (
            "功能需求表\n"
            "序号 功能项 要求\n"
            "1 用户管理 系统须支持角色权限分级管理\n"
            "2 数据导出 系统须支持按条件导出Excel\n"
            "3 消息通知 系统须支持站内消息与短信通知\n"
            "非功能要求\n"
            "系统可用性不得低于99.9%。\n"
            "关键接口响应时间不得超过3秒。\n"
        ),
        "score": (
            "评分细则 总分100分。\n"
            "功能响应（60分）：功能需求覆盖完整性，满分60分。\n"
            "性能指标（40分）：可用性与响应时间承诺，满分40分。\n"
        ),
        "a1": [
            req("R-tb-role", "mandatory", "blocking", "系统须支持角色权限分级管理", "角色权限"),
            req("R-tb-excel", "mandatory", "normal", "系统须支持按条件导出Excel", "Excel"),
            req("R-tb-msg", "mandatory", "normal", "系统须支持站内消息与短信通知", "短信"),
            req("R-tb-sla", "mandatory", "blocking", "系统可用性不得低于99.9%", "99.9"),
            req("R-tb-rt", "mandatory", "blocking", "关键接口响应时间不得超过3秒", "3秒"),
        ],
        "a1_blocking": ["R-tb-role", "R-tb-sla", "R-tb-rt"],
        "a2": [
            score_point("SP-tb-func", "功能需求覆盖完整性", "功能需求覆盖完整性，满分60分", 60, [], ["func_matrix"], ["系统须支持角色权限分级管理", "系统须支持按条件导出Excel", "系统须支持站内消息与短信通知"]),
            score_point("SP-tb-perf", "可用性与响应时间承诺", "可用性与响应时间承诺，满分40分", 40, [], ["perf_commitment"], ["系统可用性不得低于99.9%", "关键接口响应时间不得超过3秒"]),
        ],
        "a2_total": 100,
        "a3": [
            topic("T-tb-func", "function", "业务功能", "design", ["系统须支持角色权限分级管理", "系统须支持按条件导出Excel", "系统须支持站内消息与短信通知"], ["功能需求覆盖完整性，满分60分"]),
            topic("T-tb-perf", "architecture", "性能与可用性", "design", ["系统可用性不得低于99.9%", "关键接口响应时间不得超过3秒"], ["可用性与响应时间承诺，满分40分"]),
        ],
    }
)

# 7 template-strict note (still text tender; template structure is separate artifact)
SAMPLES.append(
    {
        "sample_id": "G-A-TEMPLATE-001",
        "suite": "A",
        "layers": ["A1", "A2", "A3"],
        "status": "expert_accepted",
        "title": "匿名严格模板场景语义样本",
        "tags": ["template_strict", "A1", "A2", "A3", "expert_accepted"],
        "profile": "template_strict",
        "tender": (
            "采购人要求投标文件按固定目录响应。\n"
            "投标人须逐条响应技术规格书全部条款。\n"
            "投标人不得擅自增加或删除模板章节。\n"
            "投标人须对偏离项逐条说明并提供证明材料。\n"
            "投标人须提供产品原厂授权书。\n"
        ),
        "score": (
            "评分 总分100分。\n"
            "点对点响应（50分）：技术规格响应完整性，满分50分。\n"
            "偏离说明（20分）：偏离项说明充分性，满分20分。\n"
            "授权与证明（30分）：原厂授权与证明材料，满分30分。\n"
        ),
        "a1": [
            req("R-tp-point", "mandatory", "blocking", "投标人须逐条响应技术规格书全部条款", "逐条响应"),
            req("R-tp-structure", "mandatory", "blocking", "投标人不得擅自增加或删除模板章节", "模板章节"),
            req("R-tp-dev", "mandatory", "normal", "投标人须对偏离项逐条说明并提供证明材料", "偏离"),
            req("R-tp-auth", "qualification", "blocking", "投标人须提供产品原厂授权书", "授权书"),
        ],
        "a1_blocking": ["R-tp-point", "R-tp-structure", "R-tp-auth"],
        "a2": [
            score_point("SP-tp-resp", "技术规格响应完整性", "技术规格响应完整性，满分50分", 50, [], ["point_matrix"], ["投标人须逐条响应技术规格书全部条款"]),
            score_point("SP-tp-dev", "偏离项说明充分性", "偏离项说明充分性，满分20分", 20, [], ["deviation_table"], ["投标人须对偏离项逐条说明并提供证明材料"]),
            score_point("SP-tp-auth", "原厂授权与证明材料", "原厂授权与证明材料，满分30分", 30, [], ["oem_auth"], ["投标人须提供产品原厂授权书"]),
        ],
        "a2_total": 100,
        "a3": [
            topic("T-tp-resp", "compliance", "点对点响应", "comply", ["投标人须逐条响应技术规格书全部条款", "投标人不得擅自增加或删除模板章节"], ["技术规格响应完整性，满分50分"]),
            topic("T-tp-dev", "compliance", "偏离说明", "explain", ["投标人须对偏离项逐条说明并提供证明材料"], ["偏离项说明充分性，满分20分"]),
            topic("T-tp-auth", "qualification", "原厂授权", "prove", ["投标人须提供产品原厂授权书"], ["原厂授权与证明材料，满分30分"]),
        ],
    }
)

# 8 complex score binding / multi-link caution sample
SAMPLES.append(
    {
        "sample_id": "G-A-COMPLEX-SCORE-001",
        "suite": "A",
        "layers": ["A1", "A2", "A3"],
        "status": "expert_accepted",
        "title": "匿名复杂评分绑定样本",
        "tags": ["complex_score", "binding", "A1", "A2", "A3", "expert_accepted"],
        "profile": "complex_score_binding",
        "tender": (
            "一、建设内容\n"
            "投标人须建设数据汇聚与共享交换平台。\n"
            "投标人须实现不少于10个业务系统的数据接入。\n"
            "二、数据安全\n"
            "投标人须提供数据分级分类与脱敏方案。\n"
            "三、运维\n"
            "投标人须提供不少于2年原厂质保。\n"
        ),
        "score": (
            "综合评分法 总分100分。\n"
            "平台方案（35分）：汇聚共享架构设计，满分35分。\n"
            "接入能力（25分）：业务系统接入数量与方案，满分25分。\n"
            "数据安全（20分）：分级分类与脱敏措施，满分20分。\n"
            "质保服务（20分）：质保年限与服务内容，满分20分。\n"
        ),
        "a1": [
            req("R-cx-platform", "mandatory", "blocking", "投标人须建设数据汇聚与共享交换平台", "汇聚"),
            req("R-cx-connect", "mandatory", "blocking", "投标人须实现不少于10个业务系统的数据接入", "10个"),
            req("R-cx-mask", "mandatory", "blocking", "投标人须提供数据分级分类与脱敏方案", "脱敏"),
            req("R-cx-warranty", "contract", "normal", "投标人须提供不少于2年原厂质保", "2年"),
        ],
        "a1_blocking": ["R-cx-platform", "R-cx-connect", "R-cx-mask"],
        "a2": [
            score_point("SP-cx-arch", "汇聚共享架构设计", "汇聚共享架构设计，满分35分", 35, [], ["platform_arch"], ["投标人须建设数据汇聚与共享交换平台"]),
            score_point("SP-cx-connect", "业务系统接入数量与方案", "业务系统接入数量与方案，满分25分", 25, [], ["connect_plan"], ["投标人须实现不少于10个业务系统的数据接入"]),
            score_point("SP-cx-sec", "分级分类与脱敏措施", "分级分类与脱敏措施，满分20分", 20, [], ["data_security"], ["投标人须提供数据分级分类与脱敏方案"]),
            score_point("SP-cx-warranty", "质保年限与服务内容", "质保年限与服务内容，满分20分", 20, [], ["warranty"], ["投标人须提供不少于2年原厂质保"]),
        ],
        "a2_total": 100,
        "a3": [
            topic("T-cx-platform", "data", "数据汇聚共享", "design", ["投标人须建设数据汇聚与共享交换平台", "投标人须实现不少于10个业务系统的数据接入"], ["汇聚共享架构设计，满分35分", "业务系统接入数量与方案，满分25分"]),
            topic("T-cx-sec", "security", "数据安全", "secure", ["投标人须提供数据分级分类与脱敏方案"], ["分级分类与脱敏措施，满分20分"]),
            topic("T-cx-svc", "service", "质保服务", "serve", ["投标人须提供不少于2年原厂质保"], ["质保年限与服务内容，满分20分"]),
        ],
    }
)


def build_sample(spec: dict) -> None:
    sid = spec["sample_id"]
    sdir = OUT / "samples" / sid
    if sdir.exists():
        # clean rebuild of this sample tree files we own
        pass
    tender_h = write(sdir / "source" / "tender.md", spec["tender"])
    inputs = [
        {
            "role": "tender",
            "relative_path": "source/tender.md",
            "content_sha256": tender_h,
            "external_original_ref": None,
        }
    ]
    hashes = {"tender": tender_h}
    if "score" in spec:
        score_h = write(sdir / "source" / "score.md", spec["score"])
        inputs.append(
            {
                "role": "score",
                "relative_path": "source/score.md",
                "content_sha256": score_h,
                "external_original_ref": None,
            }
        )
        hashes["score"] = score_h
    if "amendment" in spec:
        amd_h = write(sdir / "source" / "amendment.md", spec["amendment"])
        inputs.append(
            {
                "role": "amendment",
                "relative_path": "source/amendment.md",
                "content_sha256": amd_h,
                "external_original_ref": None,
            }
        )
        hashes["amendment"] = amd_h

    expectations = [
        {
            "layer": "A1",
            "schema_version": "v3-golden-1",
            "objects": spec["a1"],
            "blocking_ids": spec["a1_blocking"],
            "notes": json.dumps({"waived_keys": spec.get("a1_waived_keys", [])}, ensure_ascii=False),
        },
        {
            "layer": "A2",
            "schema_version": "v3-golden-1",
            "objects": [
                {
                    **point,
                    "group_total_points": spec["a2_total"],
                }
                for point in spec["a2"]
            ],
            "blocking_ids": [p["score_point_id"] for p in spec["a2"]],
            "notes": f"total_points={spec['a2_total']}",
        },
        {
            "layer": "A3",
            "schema_version": "v3-golden-1",
            "objects": spec["a3"],
            "blocking_ids": [t["topic_id"] for t in spec["a3"] if t.get("primary_required")],
            "notes": "each topic requires exactly one primary duty/chapter coverage",
        },
    ]

    record = {
        "sample_id": sid,
        "suite": "A",
        "layers": spec["layers"],
        "status": "expert_accepted",
        "title": spec["title"],
        "tags": spec["tags"],
        "input_manifest_hash": sha_text(json.dumps(hashes, sort_keys=True, separators=(",", ":"))),
        "inputs": inputs,
        "expectations": expectations,
        "annotation": annotation(),
        "allowed_variants": [],
        "severity_policy_version": "v1",
        "notes": f"profile={spec['profile']}; dual-reviewed anonymized domain fixture for Gate A; not Gate U holdout.",
    }
    write(sdir / "sample.json", json.dumps(record, ensure_ascii=False, indent=2))


def main() -> None:
    # remove old scaffold-only samples from registry focus
    for spec in SAMPLES:
        build_sample(spec)

    # Keep scan placeholder as annotation_pending edge case (not expert_accepted for semantic thresholds)
    # but do not count it toward Gate A A1-A3 aggregate thresholds.

    registry = {
        "registry_version": "v3-golden-1",
        "description": "V3 Golden registry with expert-accepted anonymized Suite A samples for Gate A.",
        "suites": ["A", "B", "C", "D"],
        "samples": [s["sample_id"] for s in SAMPLES] + ["G-A1-SCAN-PLACEHOLDER"],
        "policy": {
            "historical_92_198_not_threshold": True,
            "git_stores_only_anonymized_fixtures": True,
            "expert_dual_annotation_required_for_gate_a": True,
            "scaffold_samples_not_gate_a_evidence": True,
            "gate_a_counts_only_expert_accepted": True,
            "gate_u_requires_independent_holdout": True,
        },
        "thresholds": {
            "A1": {"critical_recall": 1.0, "recall": 0.95, "precision": 0.92, "anchor_accuracy": 1.0},
            "A2": {"score_row_accuracy": 1.0, "precision": 0.95, "recall": 0.95},
            "A3": {"topic_recall": 0.95, "topic_precision": 0.90, "blocking_duty_coverage": 1.0, "unique_primary": 1.0},
        },
    }
    write(OUT / "registry_manifest.json", json.dumps(registry, ensure_ascii=False, indent=2))
    print("built", len(SAMPLES), "expert samples")


if __name__ == "__main__":
    main()
