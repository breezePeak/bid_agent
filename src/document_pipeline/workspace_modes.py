from __future__ import annotations

import os
from typing import Any

from .contracts import ChapterPlanFlowVersion, ProjectWritingMode


CHAPTER_PLAN_V2_ENABLED_ENV = "BID_AGENT_CHAPTER_PLAN_V2_ENABLED"
CHAPTER_PLAN_V2_DEFAULT_ENV = "BID_AGENT_CHAPTER_PLAN_V2_DEFAULT"
CHAPTER_PLAN_SHADOW_ENABLED_ENV = "BID_AGENT_CHAPTER_PLAN_SHADOW_ENABLED"
BID_REWRITE_ENABLED_ENV = "BID_AGENT_BID_REWRITE_ENABLED"
LEGACY_OUTLINE_FUSION_ENABLED_ENV = "BID_AGENT_LEGACY_OUTLINE_FUSION_ENABLED"


def env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def workspace_capabilities(
    writing_mode: ProjectWritingMode | str,
    chapter_plan_flow: ChapterPlanFlowVersion | str,
) -> dict[str, Any]:
    mode = ProjectWritingMode(str(getattr(writing_mode, "value", writing_mode)))
    flow = ChapterPlanFlowVersion(
        str(getattr(chapter_plan_flow, "value", chapter_plan_flow))
    )
    plan_enabled = env_flag(CHAPTER_PLAN_V2_ENABLED_ENV)
    shadow_enabled = env_flag(CHAPTER_PLAN_SHADOW_ENABLED_ENV)
    rewrite_enabled = env_flag(BID_REWRITE_ENABLED_ENV)
    return {
        "chapter_plan_v2": {
            "enabled": plan_enabled,
            "default": env_flag(CHAPTER_PLAN_V2_DEFAULT_ENV),
            "active": (
                plan_enabled
                and flow is ChapterPlanFlowVersion.CONFIRMED_PLAN_V2
            ),
            "shadow_enabled": shadow_enabled,
            "shadow_active": (
                plan_enabled
                and shadow_enabled
                and flow is ChapterPlanFlowVersion.LEGACY_INLINE
            ),
        },
        "bid_rewrite": {
            "enabled": rewrite_enabled,
            "released": False,
            "active": False,
        },
        "legacy_bid_upload": {
            "enabled": rewrite_enabled
            and mode is ProjectWritingMode.BID_REWRITE,
            "released": False,
        },
        "legacy_outline_fusion": {
            "enabled": env_flag(LEGACY_OUTLINE_FUSION_ENABLED_ENV),
            "active": False,
        },
    }
