from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


RiskLevel = Literal["low", "medium", "high", "critical"]
ToolKind = Literal["core", "utility", "analysis", "mutation", "export", "human_gate", "meta"]


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    kind: str = "file"
    required_nonempty: bool = True


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    label: str
    description: str
    kind: ToolKind
    command: str = ""
    stage_id: str = ""
    requires: tuple[ArtifactRef, ...] = ()
    produces: tuple[ArtifactRef, ...] = ()
    runner: str = ""
    params_schema: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = "medium"
    idempotent: bool = False
    side_effects: tuple[str, ...] = ()
    human_confirm_required: bool = False
    tags: tuple[str, ...] = ()
    prompt_agents: tuple[str, ...] = ()

    def to_manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "kind": self.kind,
            "command": self.command,
            "stage_id": self.stage_id,
            "risk_level": self.risk_level,
            "idempotent": self.idempotent,
            "side_effects": list(self.side_effects),
            "human_confirm_required": self.human_confirm_required,
            "tags": list(self.tags),
            "params_schema": self.params_schema,
            "requires": [asdict(item) for item in self.requires],
            "produces": [asdict(item) for item in self.produces],
        }


@dataclass
class ToolError:
    code: str
    message: str
    retryable: bool = False
    suggested_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "suggested_tools": list(self.suggested_tools),
        }


@dataclass
class ToolResult:
    ok: bool
    tool: str
    args: dict[str, Any]
    started_at: str
    ended_at: str
    error: ToolError | None = None
    artifacts_written: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    summary_for_llm: str = ""
    raw_refs: list[str] = field(default_factory=list)
    gate_results: list[dict[str, Any]] = field(default_factory=list)
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "args": self.args,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error.to_dict() if self.error else None,
            "artifacts_written": list(self.artifacts_written),
            "metrics": dict(self.metrics),
            "summary_for_llm": self.summary_for_llm,
            "raw_refs": list(self.raw_refs),
            "gate_results": list(self.gate_results),
            "skipped": self.skipped,
        }
