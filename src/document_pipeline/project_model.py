from __future__ import annotations

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import ProjectModel


def load_promoted_project_model(context: WorkspaceContext) -> ProjectModel:
    """Return the only runtime ProjectModel: the active promoted revision."""
    artifact = ControlStore(context).v3_active_artifact("ProjectModel")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "ProjectModel 尚未晋级。", status_code=409)
    model = ProjectModel.model_validate(artifact["payload"])
    if model.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "ProjectModel revision 与晋级记录不一致。", status_code=409)
    return model
