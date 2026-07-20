from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.tool_registry import get_tool
from agent.types import ToolSpec


@dataclass
class PolicyDecision:
    allow: bool
    reason: str = ""
    ask_human: bool = False
    rewrite_tool: str | None = None


_READONLY_TOOLS = {"query_status", "query_artifacts", "diagnose_failure", "analyze_coverage", "analyze_compliance", "list_issues", "export_preflight", "explain_issue"}


def evaluate_tool_call(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    auto_execute: bool = False,
    user_confirmed: bool = False,
) -> PolicyDecision:
    """Rule-first policy. Readonly always allowed; mutations need intent/confirm."""
    args = args or {}
    name = str(tool_name or "").strip()
    if not name:
        return PolicyDecision(False, "tool 名为空")

    spec = get_tool(name)
    if spec is None:
        return PolicyDecision(False, f"未知 tool: {name}")

    if name in _READONLY_TOOLS or "readonly" in (spec.tags or ()):
        return PolicyDecision(True, "只读 tool 允许")

    # dry_run always ok
    if args.get("dry_run") is True:
        return PolicyDecision(True, "dry_run 允许")

    risk = spec.risk_level
    if risk == "critical" and not user_confirmed:
        return PolicyDecision(False, "critical tool 需要人工确认", ask_human=True)

    if spec.human_confirm_required and not user_confirmed:
        return PolicyDecision(False, f"{name} 需要人工确认", ask_human=True)

    # Mutations: allow selection, but auto_execute false unless caller opts in
    if not auto_execute and name in {"run_stage", "build_docx", "build-docx"}:
        # still allow invoke only if dry_run; otherwise deny silent mutate from supervisor auto loop
        return PolicyDecision(
            False,
            "变更类 tool 默认不自动执行，请用户确认后执行",
            ask_human=True,
        )

    if name == "run_stage":
        command = str(args.get("command") or "")
        if command in {"build-docx", "build-md"} and not user_confirmed and not auto_execute:
            return PolicyDecision(False, "导出类阶段需要确认", ask_human=True)

    if name == "repair_issue" and not user_confirmed:
        if bool((args or {}).get("confirm_execute")):
            return PolicyDecision(False, "修复问题需要确认", ask_human=True)

    if name == "fix_compliance" and not user_confirmed:
        if bool((args or {}).get("confirm_execute")):
            return PolicyDecision(False, "合规改稿执行需要确认", ask_human=True)

    if name == "fix_coverage" and not user_confirmed:
        # allow planning (confirm_execute false) via invoke path; supervisor still asks human for execute
        if bool((args or {}).get("confirm_execute")):
            return PolicyDecision(False, "覆盖率改稿执行需要确认", ask_human=True)

    if name == "build_export" and not user_confirmed:
        return PolicyDecision(False, "导出终稿需要确认", ask_human=True)

    if name == "run_pipeline_remaining" and not user_confirmed and not auto_execute:
        return PolicyDecision(False, "续跑剩余流水线需要确认", ask_human=True)

    if name in {"write_chapters", "review_chapters", "rewrite_chapters"} and not user_confirmed and not auto_execute:
        return PolicyDecision(False, "章节变更需要确认", ask_human=True)

    # PR-A4: forbid price chapter mutations when constraint flag present on args
    if args.get("forbid_price_chapters") or args.get("forbid_price_changes"):
        chapter_ids = args.get("chapter_ids") or []
        exclude = [str(x) for x in (args.get("exclude_sections") or [])]
        price_tokens = ("报价", "价格", "price", "商务报价")
        for cid in chapter_ids:
            blob = str(cid)
            if any(t in blob for t in price_tokens) or any(t in blob for t in exclude):
                return PolicyDecision(False, "约束禁止修改报价相关章节", ask_human=False)

    return PolicyDecision(True, "策略通过")


def is_readonly_tool(tool_name: str) -> bool:
    name = str(tool_name or "").strip()
    if name in _READONLY_TOOLS:
        return True
    spec = get_tool(name)
    return bool(spec and "readonly" in (spec.tags or ()))
