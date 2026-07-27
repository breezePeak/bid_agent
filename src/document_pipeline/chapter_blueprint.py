from __future__ import annotations

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import ChapterBlueprint


def load_promoted_chapter_blueprint(context: WorkspaceContext) -> ChapterBlueprint:
    artifact = ControlStore(context).v3_active_artifact("ChapterBlueprint")
    if artifact is None:
        raise ControlPlaneError("V3_ARTIFACT_NOT_PROMOTED", "ChapterBlueprint 尚未晋级。", status_code=409)
    blueprint = ChapterBlueprint.model_validate(artifact["payload"])
    if blueprint.revision != int(artifact["revision"]):
        raise ControlPlaneError("V3_ARTIFACT_INVALID", "ChapterBlueprint revision 与晋级记录不一致。", status_code=409)
    return blueprint
