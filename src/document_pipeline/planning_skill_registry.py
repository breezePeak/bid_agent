"""Static registry for controlled internal BidAgent planning skills."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .planning_inference import (
    OUTLINE_CAPABILITY_VERSION,
    OUTLINE_PROMPT_FILE,
    OUTLINE_PROMPT_VERSION,
    OUTLINE_SCHEMA_VERSION,
    OUTLINE_SKILL_ID,
    REWRITE_OUTLINE_CAPABILITY_VERSION,
    REWRITE_OUTLINE_PROMPT_FILE,
    REWRITE_OUTLINE_SCHEMA_VERSION,
    REWRITE_OUTLINE_SKILL_ID,
    planning_prompt_hash,
)


class PlanningSkillRegistryError(RuntimeError):
    """Raised when an unknown skill or unauthorized role is requested."""


@dataclass(frozen=True, slots=True)
class PlanningSkillRegistration:
    skill_id: str
    version: str
    prompt_file: str
    prompt_hash: str
    schema_version: str
    allowed_roles: frozenset[str]
    execution_mode: Literal["structured_llm"]
    allows_dynamic_scripts: Literal[False]
    allows_filesystem_access: Literal[False]
    allows_database_writes: Literal[False]


CHAPTER_OUTLINE_SPLIT_SKILL = PlanningSkillRegistration(
    skill_id=OUTLINE_SKILL_ID,
    version=OUTLINE_CAPABILITY_VERSION,
    prompt_file=OUTLINE_PROMPT_FILE,
    prompt_hash=planning_prompt_hash(OUTLINE_PROMPT_FILE),
    schema_version=OUTLINE_SCHEMA_VERSION,
    allowed_roles=frozenset({"planning_agent"}),
    execution_mode="structured_llm",
    allows_dynamic_scripts=False,
    allows_filesystem_access=False,
    allows_database_writes=False,
)

REWRITE_OUTLINE_MERGE_SKILL = PlanningSkillRegistration(
    skill_id=REWRITE_OUTLINE_SKILL_ID,
    version=REWRITE_OUTLINE_CAPABILITY_VERSION,
    prompt_file=REWRITE_OUTLINE_PROMPT_FILE,
    prompt_hash=planning_prompt_hash(REWRITE_OUTLINE_PROMPT_FILE),
    schema_version=REWRITE_OUTLINE_SCHEMA_VERSION,
    allowed_roles=frozenset({"planning_agent"}),
    execution_mode="structured_llm",
    allows_dynamic_scripts=False,
    allows_filesystem_access=False,
    allows_database_writes=False,
)

PLANNING_SKILL_REGISTRY = MappingProxyType(
    {
        CHAPTER_OUTLINE_SPLIT_SKILL.skill_id: CHAPTER_OUTLINE_SPLIT_SKILL,
        REWRITE_OUTLINE_MERGE_SKILL.skill_id: REWRITE_OUTLINE_MERGE_SKILL,
    }
)


def get_planning_skill(
    skill_id: str,
    *,
    caller_role: str,
) -> PlanningSkillRegistration:
    """Return an immutable registration after enforcing the role allow-list."""

    registration = PLANNING_SKILL_REGISTRY.get(skill_id)
    if registration is None:
        raise PlanningSkillRegistryError(f"未注册的 BidAgent 内部规划 Skill: {skill_id}")
    if caller_role not in registration.allowed_roles:
        raise PlanningSkillRegistryError(
            f"角色 {caller_role!r} 无权调用内部规划 Skill {skill_id}"
        )
    return registration
