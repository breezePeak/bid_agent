from __future__ import annotations

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import ResponseTopicGraph


def load_promoted_topic_graph(context: WorkspaceContext) -> ResponseTopicGraph:
    artifact = ControlStore(context).v3_active_artifact("ResponseTopicGraph")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "ResponseTopicGraph 尚未晋级。", status_code=409)
    graph = ResponseTopicGraph.model_validate(artifact["payload"])
    if graph.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "ResponseTopicGraph revision 与晋级记录不一致。", status_code=409)
    return graph
