from __future__ import annotations

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import (
    ProjectModel,
    RequirementLedger,
    ResponseTopicGraph,
    ScoreModel,
    SourceIndex,
)
from .scoring_outline_policy import audit_response_topic_graph as _audit_response_topic_graph


def audit_response_topic_graph(
    score_model: ScoreModel,
    graph: ResponseTopicGraph,
    requirement_ledger: RequirementLedger | None = None,
    project_model: ProjectModel | None = None,
    source_index: SourceIndex | None = None,
) -> dict[str, object]:
    """Run BidAgent's deterministic score-to-Duty integrity policy."""

    return _audit_response_topic_graph(
        score_model,
        graph,
        requirement_ledger=requirement_ledger,
        project_model=project_model,
        source_index=source_index,
    )


def load_promoted_topic_graph(context: WorkspaceContext) -> ResponseTopicGraph:
    artifact = ControlStore(context).v3_active_artifact("ResponseTopicGraph")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "ResponseTopicGraph 尚未晋级。", status_code=409)
    graph = ResponseTopicGraph.model_validate(artifact["payload"])
    if graph.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "ResponseTopicGraph revision 与晋级记录不一致。", status_code=409)
    return graph
