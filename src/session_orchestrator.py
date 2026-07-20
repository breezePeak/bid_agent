from __future__ import annotations

import json
import re
from typing import Any

from subagent_registry import (
    CHAPTER_WRITER,
    pipeline_manifest,
    subagent_manifest,
)


_ORCHESTRATOR_SYSTEM_PROMPT = """你是标书 Agent 的主 Agent（全局会话编排器 / Coordinator）。

## 1. 你的角色
你是 coordinator，统筹整个标书流水线。你不亲自写章节正文，只做：编排、综合、与用户对话、派发子 agent。
- 能直接答的就答，不要把你能处理的事派出去
- 子 agent 结果是内部信号，不是对话对象——不要感谢/确认它们，而是把新信息总结给用户

## 2. 子 agent（blueprint，运行时按 instantiation 实例化）
{subagents}

## 3. 可调用的流水线阶段（直接执行，非 subagent）
{pipeline}

## 4. 四阶段分工（Claude Code 模型）
| 阶段 | 谁做 |
|---|---|
| Research | 子 agent（并行，只读）|
| Synthesis | 你（读结果→写决策）|
| Implementation | 子 agent（写作/改稿）|
| Verification | 子 agent（fresh，不能是写作那个）|

关键：综合是你的核心职责。读审核结果后，你要决定改哪些章、聚焦哪些问题，不能偷懒转交（禁止"基于审核结果去改"这种说法）。改稿派给写作子 agent（continue 模式，复用文件上下文）；审核必须是独立于写作的审核子 agent（fresh eyes）。

## 5. 动作空间（action 字段，只能取其一）
- query: 只读查询，不执行命令。给 query_type（status/manual_review/score_coverage/quality_risk/inputs/outputs）。
- run_command: 执行某流水线阶段，给 command（从 pipeline 里选）。
- dispatch_chapters: 派发多个章节写作子 agent 并发初写。对应 write-all。
- dispatch_review: 派发审核子 agent 并发审核 + 需要时由写作子 agent 改稿（自动循环）。对应 review-fix-all。
- dispatch_rewrite: 你已读了各章 review 结果，定向派发写作子 agent 对指定章节改稿。给 rewrite_targets。
- global_review: 触发全文审核子 agent（单实例，自带上下文）。对应 global-review。
- auto_run: 从当前进度自动连续执行剩余所有阶段。
- chat: 通用回答/引导。
说明：专项合规检查命令为 compliance-check，通过 run_command 触发，位于 global-review 之后、build-md 之前。

## 6. 输出规则
1. 只输出一个 JSON 对象，不要任何额外文字、不要 markdown 代码块。
2. 字段：intent, action, query_type(仅 query), command(仅 run_command), rewrite_targets(仅 dispatch_rewrite，数组 [{chapter_id, focus_problems, priority}]), reply(基于状态快照真实数字，不编造), actions(按钮数组), auto_execute(布尔，仅用户明确要"执行/继续/开始/跑"时 true)。
3. actions 元素：{"type":"run_command","command":"<cmd>","label":"<lbl>"}；{"type":"dispatch_chapters","label":"派发章节写作"}；{"type":"dispatch_review","label":"派发审核改稿"}；{"type":"dispatch_rewrite","label":"定向改稿"}；{"type":"global_review","label":"全文审核"}；{"type":"auto_run","label":"一键跑完剩余"}；{"type":"show_step","command":"<cmd>","label":"<lbl>"}。
4. 用户说"继续/下一步/开始/执行/跑/重试/派发"→action=run_command, command=next_step.command, auto_execute=true。
5. 用户说"全部跑完/一键生成"→action=auto_run, auto_execute=true。
6. 用户说"审核/检查质量"但没有明确要求执行时，先 action=query, query_type=quality_risk；只有明确说"执行审核/开始审核/派发审核"才 action=dispatch_review 或 global_review；用户说"合规检查/废标检查/专项合规"→action=run_command, command=compliance-check。
7. 用户说"改某章/定向改稿"→action=dispatch_rewrite，给 rewrite_targets。
8. query 和 chat 永远 auto_execute=false。
9. 普通问答规则：
   当用户问"是什么/为什么/怎么做/依据是什么/当前还有什么问题"且没有明确执行意图时，action=chat 或 query，直接用状态快照和常识回答，不要强行触发流程。
10. 自动恢复规则：
   当快照 run_state_status 是 recovering 或 retrying 时，reply 必须说明系统正在自主修复，写出 recovery.reason/action/attempt，不要要求用户手动重试。
 11. 诊断规则（Claude Code 模式——coordinator 必须读错误、写具体 spec，禁止懒转交）：
    当用户消息含"诊断/失败/错误/修复"且快照中有 failed_stage_error.lines 时：
    - 必须从 lines 中提取具体信息：哪个文件、哪一行、什么错误。
    - 若可以定位到具体文件+行号（如 NameError/ImportError/FileNotFoundError），写明「修改 src/xxx.py 第 N 行」。
    - 不可写"请检查错误日志"或"根据错误信息修复"——这是把理解工作推给用户，绝对禁止。
    - 诊断结果写入 reply；actions 给 [retry_stage, skip_stage]，不要给泛泛的"继续下一步"。
 12. 材料待补规则：
    当 materials_summary.exists 且 materials_summary.deferred > 0 时：
    - 你只知道「待补条数」等数量，不知道每条明细（明细在右侧材料清单）。
    - 用户问状态/进度/缺什么/材料时，必须点明「还有 N 条材料待补充」，并引导打开材料清单或上传公司资料。
    - 不要编造具体证书/附件名称；需要明细时 action=show_step command=build-materials-checklist。
"""


def _compact_status_snapshot(status: dict[str, Any]) -> dict[str, Any]:
    run_state = status.get("run_state", {}) if isinstance(status.get("run_state"), dict) else {}
    next_step = status.get("next_step") if isinstance(status.get("next_step"), dict) else None
    blocked_step = status.get("blocked_step") if isinstance(status.get("blocked_step"), dict) else None
    sources = status.get("sources", {}) if isinstance(status.get("sources"), dict) else {}
    inputs = status.get("inputs", {}) if isinstance(status.get("inputs"), dict) else {}
    outputs = status.get("outputs", {}) if isinstance(status.get("outputs"), dict) else {}
    manual_summary = status.get("manual_review_summary", {}) if isinstance(status.get("manual_review_summary"), dict) else {}
    run_state_status = run_state.get("status") or "未知"
    run_state_message = run_state.get("message") or ""
    workflow = []
    for item in status.get("workflow", []) or []:
        if not isinstance(item, dict):
            continue
        workflow.append(
            {
                "command": item.get("command"),
                "label": item.get("label"),
                "state": item.get("state"),
                "done": bool(item.get("done")),
            }
        )
    snapshot = {
        "active_run": status.get("active_run", {}),
        "running": bool(status.get("running")),
        "run_state_status": run_state_status,
        "run_state_message": run_state_message,
        "next_step": next_step,
        "blocked_step": blocked_step,
        "sources_count": {
            "tender": len(sources.get("tender", [])) if isinstance(sources.get("tender"), list) else 0,
            "company": len(sources.get("company", [])) if isinstance(sources.get("company"), list) else 0,
            "template": len(sources.get("template", [])) if isinstance(sources.get("template"), list) else 0,
        },
        "inputs": inputs,
        "outputs": {
            "final_md": bool(outputs.get("final_md")),
            "final_docx": bool(outputs.get("final_docx")),
        },
        "manual_review_summary": manual_summary,
        "workflow": workflow,
        "failed_stage_error": status.get("failed_stage_error"),
        "recovery": status.get("recovery"),
    }
    issues_summary = status.get("issues_summary")
    if isinstance(issues_summary, dict):
        snapshot["issues_summary"] = issues_summary

    # 仅数量级材料摘要（无明细），供主 Agent 提醒用户补料
    materials_summary = status.get("materials_summary")
    if isinstance(materials_summary, dict):
        snapshot["materials_summary"] = {
            "exists": bool(materials_summary.get("exists")),
            "total": int(materials_summary.get("total") or 0),
            "deferred": int(materials_summary.get("deferred") or 0),
            "ready": int(materials_summary.get("ready") or 0),
            "waived": int(materials_summary.get("waived") or 0),
        }

    pending_confirmation = status.get("pending_confirmation")
    if isinstance(pending_confirmation, dict):
        snapshot["pending_confirmation"] = {
            key: pending_confirmation.get(key)
            for key in ("confirmation_id", "type", "count")
            if key in pending_confirmation
        }

    repair_job = status.get("repair_job")
    if not isinstance(repair_job, dict):
        repair_job = status.get("current_repair_job")
    if isinstance(repair_job, dict):
        snapshot["repair_job"] = {
            key: repair_job.get(key)
            for key in (
                "job_id",
                "status",
                "phase",
                "counts",
                "total_count",
                "auto_count",
                "manual_count",
                "resolved_count",
                "remaining_count",
                "failed_count",
                "progress_percent",
                "message",
                "resume_command",
                "resume_attempted",
            )
            if key in repair_job
        }
    return snapshot


def _recent_assistant_actions(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep a small, model-useful record of actions offered by the assistant."""
    recent_actions: list[dict[str, Any]] = []
    for msg in history[-6:]:
        if msg.get("role") != "assistant" or not isinstance(msg.get("actions"), list):
            continue
        for action in msg["actions"]:
            if not isinstance(action, dict):
                continue
            compact_action = {
                key: value
                for key, value in action.items()
                if key in {
                    "type",
                    "command",
                    "label",
                    "category",
                    "issue_id",
                    "issue_ids",
                    "confirmation_id",
                }
            }
            if compact_action:
                recent_actions.append(compact_action)
    return recent_actions[-8:]


def _build_user_prompt(
    message: str,
    history: list[dict[str, Any]],
    snapshot: dict[str, Any],
    review_context: list[dict[str, Any]] | None = None,
) -> str:
    recent = history[-6:] if history else []
    history_text = ""
    for msg in recent:
        role = msg.get("role", "user")
        content = str(msg.get("content", ""))[:300]
        if content:
            history_text += f"{role}: {content}\n"
    parts = [
        f"当前状态快照（JSON）：\n{json.dumps(snapshot, ensure_ascii=False, default=str)}",
    ]
    if review_context:
        parts.append(
            "各章审核结果摘要（用于 dispatch_rewrite 综合改稿目标）：\n"
            + json.dumps(review_context, ensure_ascii=False, default=str)
        )
    recent_actions = _recent_assistant_actions(history)
    if recent_actions:
        parts.append(
            "最近助手动作（JSON）：\n"
            + json.dumps(recent_actions, ensure_ascii=False, default=str)
        )
    parts.append(f"最近对话：\n{history_text}")
    parts.append(f"用户最新消息：\n{message}")
    parts.append("输出决策 JSON。")
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = cleaned[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    cleaned_candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(cleaned_candidate)
    except json.JSONDecodeError:
        return None


_VALID_ACTIONS = {
    "query", "run_command", "dispatch_chapters", "dispatch_review",
    "dispatch_rewrite", "global_review", "auto_run", "chat",
}


def _normalize_plan(plan: dict[str, Any], message: str) -> dict[str, Any]:
    action = str(plan.get("action", "chat")).strip()
    if action not in _VALID_ACTIONS:
        action = "chat"
    auto_execute = bool(plan.get("auto_execute", False))
    reply = str(plan.get("reply", "")).strip()
    actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
    intent = str(plan.get("intent", "")).strip()
    query_type = str(plan.get("query_type", "status")).strip()
    command = str(plan.get("command", "")).strip()
    rewrite_targets = plan.get("rewrite_targets") if isinstance(plan.get("rewrite_targets"), list) else []

    lower = message.lower()
    wants_run = any(k in message or k in lower for k in ("继续", "下一步", "执行下一步", "继续执行", "next", "开始", "执行", "跑", "派发", "重试", "写章节", "生成章节", "开始审核", "执行审核", "派发审核", "改稿"))
    if wants_run:
        auto_execute = True

    return {
        "intent": intent,
        "action": action,
        "query_type": query_type,
        "command": command,
        "rewrite_targets": rewrite_targets,
        "auto_execute": auto_execute,
        "reply": reply,
        "actions": actions,
    }


def _fallback_plan(message: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    next_step = snapshot.get("next_step") if isinstance(snapshot.get("next_step"), dict) else None
    actions: list[dict[str, Any]] = []
    reply = "我暂时没完全理解你的意图。我可以：查看状态、执行下一步、派发章节子 Agent 写作、一键生成 Word。"
    if next_step:
        actions.append({"type": "run_command", "command": str(next_step.get("command", "")), "label": f"执行 {next_step.get('label', '下一步')}"})
        actions.append({"type": "auto_run", "label": "一键跑完剩余"})
    return {
        "intent": "fallback",
        "action": "chat",
        "query_type": "status",
        "command": "",
        "auto_execute": False,
        "reply": reply,
        "actions": actions,
    }


def plan(
    message: str,
    history: list[dict[str, Any]],
    status: dict[str, Any],
    llm_chat=None,
    review_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # PR-3: optional Supervisor short-loop (flag default off)
    try:
        from agent.flags import agent_supervisor_enabled
        from agent.supervisor import plan_with_supervisor

        if agent_supervisor_enabled():
            supervised = plan_with_supervisor(
                message,
                history,
                status,
                llm_chat=llm_chat,
                review_context=review_context,
            )
            if supervised:
                return supervised
    except Exception:
        # fall through to legacy orchestrator
        pass

    snapshot = _compact_status_snapshot(status)
    system_prompt = (
        _ORCHESTRATOR_SYSTEM_PROMPT
        .replace("{subagents}", json.dumps(subagent_manifest(), ensure_ascii=False))
        .replace("{pipeline}", json.dumps(pipeline_manifest(), ensure_ascii=False))
    )
    user_prompt = _build_user_prompt(message, history, snapshot, review_context=review_context)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    reasoning = ""
    try:
        if llm_chat is None:
            from llm_client import chat_with_meta

            meta = chat_with_meta(messages, temperature=0.1)
            raw = meta.get("content") or ""
            reasoning = str(meta.get("reasoning") or "").strip()
        else:
            raw = llm_chat(messages, temperature=0.1)
            if isinstance(raw, dict):
                reasoning = str(raw.get("reasoning") or "").strip()
                raw = raw.get("content") or ""
    except Exception as exc:
        return _fallback_plan(message, snapshot) | {"error": f"LLM 编排失败: {exc}"}
    plan_json = _extract_json(raw)
    if not plan_json:
        return _fallback_plan(message, snapshot) | {
            "error": "LLM 未返回合法 JSON",
            "raw": raw,
            "thinking": reasoning,
        }
    plan = _normalize_plan(plan_json, message)
    if reasoning:
        plan["thinking"] = reasoning
    return plan


def build_query_reply(query_type: str, status: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    snapshot = _compact_status_snapshot(status)
    next_step = snapshot.get("next_step") if isinstance(snapshot.get("next_step"), dict) else None
    manual = snapshot.get("manual_review_summary", {}) if isinstance(snapshot.get("manual_review_summary"), dict) else {}
    inputs = snapshot.get("inputs", {}) if isinstance(snapshot.get("inputs"), dict) else {}
    sources_count = snapshot.get("sources_count", {}) if isinstance(snapshot.get("sources_count"), dict) else {}
    outputs = snapshot.get("outputs", {}) if isinstance(snapshot.get("outputs"), dict) else {}
    recovery = snapshot.get("recovery", {}) if isinstance(snapshot.get("recovery"), dict) else {}
    materials = snapshot.get("materials_summary", {}) if isinstance(snapshot.get("materials_summary"), dict) else {}
    materials_deferred = int(materials.get("deferred") or 0)
    materials_line = (
        f"待补材料 {materials_deferred} 条。"
        if materials.get("exists") and materials_deferred > 0
        else ("材料清单已就绪，无待补项。" if materials.get("exists") else "")
    )

    if query_type == "manual_review":
        reply = (
            f"人工复核待处理 {manual.get('total_pending', 0)} 项："
            f"弱证据/缺口 {manual.get('template_evidence_pending', 0)}，"
            f"评分点覆盖 {manual.get('score_coverage_pending', 0)}，"
            f"章节问题 {manual.get('chapter_review_pending', 0)}，"
            f"全文风险 {manual.get('global_review_pending', 0)}。"
        )
        return reply, [
            {"type": "show_manual_review", "category": "score_coverage", "label": "看未覆盖评分点"},
            {"type": "show_manual_review", "category": "template_evidence", "label": "看弱证据项"},
            {"type": "show_manual_review", "category": "global_review", "label": "看全文风险"},
        ]

    if query_type == "score_coverage":
        score_done = any(w.get("command") == "parse-score" and w.get("done") for w in snapshot.get("workflow", []))
        matrix_done = any(w.get("command") == "build-score-coverage" and w.get("done") for w in snapshot.get("workflow", []))
        pending = manual.get("score_coverage_pending", 0)
        reply = (
            f"评分点解析：{'已完成' if score_done else '未完成'}；"
            f"评分覆盖矩阵：{'已生成' if matrix_done else '未生成'}；"
            f"未覆盖评分点待处理 {pending} 项。"
        )
        actions = [
            {"type": "show_step", "command": "parse-score", "label": "查看评分解析"},
            {"type": "show_step", "command": "build-score-coverage", "label": "查看覆盖矩阵"},
            {"type": "show_manual_review", "category": "score_coverage", "label": "看未覆盖项"},
        ]
        if next_step:
            actions.append({"type": "run_command", "command": str(next_step.get("command", "")), "label": "执行下一步"})
        return reply, actions

    if query_type == "quality_risk":
        compliance_done = any(w.get("command") == "compliance-check" and w.get("done") for w in snapshot.get("workflow", []))
        reply = (
            f"质量风险概览：人工复核待处理 {manual.get('total_pending', 0)} 项，"
            f"章节问题 {manual.get('chapter_review_pending', 0)}，"
            f"全文风险 {manual.get('global_review_pending', 0)}，"
            f"弱证据/模板缺口 {manual.get('template_evidence_pending', 0)}；"
            f"专项合规检查：{'已完成' if compliance_done else '未完成'}。"
            + (f" {materials_line}" if materials_line else "")
        )
        actions = [
            {"type": "show_manual_review", "category": "global_review", "label": "看全文风险"},
            {"type": "show_manual_review", "category": "chapter_review", "label": "看章节问题"},
            {"type": "show_step", "command": "global-review", "label": "查看全文审核"},
            {"type": "show_step", "command": "compliance-check", "label": "查看专项合规"},
        ]
        if materials_deferred > 0:
            actions.insert(0, {"type": "show_step", "command": "build-materials-checklist", "label": "打开材料清单"})
        return reply, actions

    if query_type == "inputs":
        reply = (
            f"输入资料：招标文件 {sources_count.get('tender', 0)} 个，"
            f"公司资料 {sources_count.get('company', 0)} 个，"
            f"Word 模板 {sources_count.get('template', 0)} 个。"
            f" 已导入：招标 {'是' if inputs.get('tender_md') else '否'}，"
            f"公司 {'是' if inputs.get('company_md') else '否'}，"
            f"模板 {'是' if inputs.get('template_docx') else '否'}。"
        )
        return reply, [
            {"type": "show_step", "command": "prepare-inputs", "label": "查看导入资料"},
            {"type": "run_command", "command": "prepare-inputs", "label": "重新导入资料"},
        ]

    if query_type == "outputs":
        reply = (
            f"最终输出：Markdown {'已生成' if outputs.get('final_md') else '未生成'}，"
            f" Word {'已生成' if outputs.get('final_docx') else '未生成'}。"
        )
        actions = [
            {"type": "show_step", "command": "build-md", "label": "查看 Markdown 节点"},
            {"type": "show_step", "command": "build-docx", "label": "查看 Word 节点"},
        ]
        if next_step:
            actions.append({"type": "run_command", "command": str(next_step.get("command", "")), "label": "继续生成"})
        return reply, actions

    # default: status
    run_state_status = snapshot.get("run_state_status", "未知")
    reply = f"当前运行状态：{run_state_status}。"
    if run_state_status in {"recovering", "retrying"} and recovery:
        reply += (
            f" 正在尝试自主修复：{recovery.get('reason', '')}；"
            f"{recovery.get('action', '')}（{recovery.get('attempt', 0)}/{recovery.get('max_attempts', 2)}）。"
        )
    if next_step:
        reply += f" 下一步是「{next_step.get('label', '')}」。"
    if snapshot.get("blocked_step"):
        reply += f" 阻塞点：{snapshot.get('blocked_step', {}).get('label', '')}。"
    if snapshot.get("run_state_message"):
        reply += f" 说明：{snapshot.get('run_state_message')}。"
    if materials_line:
        reply += f" {materials_line}"
    actions: list[dict[str, Any]] = []
    if materials_deferred > 0:
        actions.append({"type": "show_step", "command": "build-materials-checklist", "label": "打开材料清单"})
    if next_step:
        actions.append({"type": "run_command", "command": str(next_step.get("command", "")), "label": f"执行 {next_step.get('label', '')}"})
        actions.append({"type": "auto_run", "label": "一键跑完剩余"})
    return reply, actions


def resolve_execution(plan_result: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    action = plan_result.get("action", "chat")
    auto_execute = bool(plan_result.get("auto_execute", False))
    next_step = status.get("next_step") if isinstance(status.get("next_step"), dict) else None

    result: dict[str, Any] = {
        "action": action,
        "intent": plan_result.get("intent", ""),
        "auto_execute": auto_execute,
        "reply": plan_result.get("reply", ""),
        "actions": plan_result.get("actions", []),
        "trigger_command": "",
        "trigger_auto_run": False,
        "trigger_rewrite_targets": [],
    }

    if action == "query":
        # 保留模型原话；规则模板仅在无有效回复时兜底，按钮可叠加
        llm_reply = str(plan_result.get("reply") or "").strip()
        reply, actions = build_query_reply(plan_result.get("query_type", "status"), status)
        if llm_reply:
            result["reply"] = llm_reply
        elif reply:
            result["reply"] = reply
        if actions:
            existing = result.get("actions") if isinstance(result.get("actions"), list) else []
            # 去重叠加：规则动作补在后
            labels = {str(a.get("label") or a.get("type")) for a in existing if isinstance(a, dict)}
            merged = list(existing)
            for act in actions:
                if not isinstance(act, dict):
                    continue
                key = str(act.get("label") or act.get("type"))
                if key not in labels:
                    merged.append(act)
                    labels.add(key)
            result["actions"] = merged
        result["auto_execute"] = False
        return result

    if action == "run_command":
        command = plan_result.get("command", "")
        if not command and next_step:
            command = str(next_step.get("command", ""))
        result["trigger_command"] = command if auto_execute else ""
        if not result["actions"]:
            result["actions"] = [{"type": "run_command", "command": command, "label": f"执行 {command}"}] if command else []
        if not result["reply"] and command:
            result["reply"] = f"准备执行 `{command}`。"
        return result

    if action == "dispatch_chapters":
        result["trigger_command"] = CHAPTER_WRITER.command if auto_execute else ""
        if not result["reply"]:
            result["reply"] = "把章节写作派发给多个章节子 Agent 并发执行。"
        if not result["actions"]:
            result["actions"] = [{"type": "dispatch_chapters", "label": "派发章节写作"}]
        return result

    if action == "dispatch_review":
        result["trigger_command"] = "review-fix-all" if auto_execute else ""
        if not result["reply"]:
            result["reply"] = "派发审核子 Agent 并发审核，需要时由写作子 Agent 改稿。"
        if not result["actions"]:
            result["actions"] = [{"type": "dispatch_review", "label": "派发审核改稿"}]
        return result

    if action == "dispatch_rewrite":
        targets = plan_result.get("rewrite_targets", []) or []
        result["trigger_rewrite_targets"] = targets if auto_execute else []
        if not result["reply"]:
            if targets:
                ids = ", ".join(str(t.get("chapter_id", "")) for t in targets)
                result["reply"] = f"已综合审核结果，定向派发写作子 Agent 改稿：{ids}。"
            else:
                result["reply"] = "请先审核（dispatch_review）产出 review 结果，再做定向改稿。"
        if not result["actions"]:
            result["actions"] = [{"type": "dispatch_rewrite", "label": "定向改稿"}]
        return result

    if action == "global_review":
        result["trigger_command"] = "global-review" if auto_execute else ""
        if not result["reply"]:
            result["reply"] = "触发全文审核子 Agent（单实例，自带上下文装配）。"
        if not result["actions"]:
            result["actions"] = [{"type": "global_review", "label": "全文审核"}]
        return result

    if action == "auto_run":
        result["trigger_auto_run"] = bool(auto_execute)
        if not result["reply"]:
            result["reply"] = "从当前进度开始自动执行剩余全流程（含章节子 Agent 派发）。"
        if not result["actions"]:
            result["actions"] = [{"type": "auto_run", "label": "一键跑完剩余"}]
        return result

    # chat
    return result
