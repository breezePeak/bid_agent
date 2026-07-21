from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from pipeline_registry import RunArtifact, stage_spec_by_command, workflow_stage_specs


def _hash_files(root: Path, files: list[Path]) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    for path in sorted(files, key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
        total += len(content)
    return digest.hexdigest(), total


def describe_artifact(root: Path, artifact: RunArtifact) -> dict[str, Any]:
    key = str(artifact.path).replace("\\", "/")
    if artifact.kind == "virtual":
        return {
            "artifact_key": key,
            "path": key,
            "kind": artifact.kind,
            "status": "ready",
            "sha256": hashlib.sha256(f"virtual:{key}".encode("utf-8")).hexdigest(),
            "size_bytes": 0,
            "files": [],
        }

    target = root / artifact.path
    if artifact.kind == "glob":
        files = [path for path in root.glob(artifact.path) if path.is_file()]
    elif target.is_dir():
        files = [path for path in target.rglob("*") if path.is_file()]
    else:
        files = [target] if target.is_file() else []

    sha256, size_bytes = _hash_files(root, files) if files else ("", 0)
    ready = bool(files) and (not artifact.required_nonempty or size_bytes > 0)
    if not files and not artifact.required_nonempty:
        ready = True
        sha256 = hashlib.sha256(f"optional-missing:{key}".encode("utf-8")).hexdigest()
    return {
        "artifact_key": key,
        "path": key,
        "kind": artifact.kind,
        "status": "ready" if ready else "missing",
        "sha256": sha256,
        "size_bytes": size_bytes,
        "files": [path.relative_to(root).as_posix() for path in sorted(files)],
    }


def stage_input_fingerprint(root: Path, command: str) -> str:
    spec = stage_spec_by_command(command)
    inputs = [describe_artifact(root, artifact) for artifact in spec.requires]
    encoded = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record_stage_artifacts(
    context: WorkspaceContext,
    command: str,
    *,
    disposition: str = "produced",
) -> list[dict[str, Any]]:
    spec = stage_spec_by_command(command)
    input_fingerprint = stage_input_fingerprint(context.root, command)
    store = ControlStore(context)
    previous = {item["artifact_key"]: item for item in store.artifact_states()}
    manifests: list[dict[str, Any]] = []
    for artifact in spec.produces:
        manifest = describe_artifact(context.root, artifact)
        manifest.update(
            {
                "producer": command,
                "stage_id": spec.id,
                "input_fingerprint": input_fingerprint,
                "disposition": disposition,
            }
        )
        if manifest["status"] == "missing" and artifact.required_nonempty:
            raise ControlPlaneError(
                "ARTIFACT_NOT_READY",
                f"阶段 {command} 未生成必需 Artifact: {artifact.path}",
                status_code=409,
            )
        manifests.append(manifest)
    stored = store.upsert_artifact_states(manifests)
    if disposition != "reused" and any(
        not previous.get(item["artifact_key"])
        or previous[item["artifact_key"]].get("sha256") != item.get("sha256")
        or previous[item["artifact_key"]].get("input_fingerprint") != input_fingerprint
        for item in manifests
    ):
        store.mark_artifact_states_stale(
            downstream_artifact_keys(command),
            reason=f"上游阶段 {command} 的产物或输入已变化",
            source_command=command,
        )
    return stored


def downstream_artifact_keys(command: str) -> list[str]:
    stages = workflow_stage_specs()
    start = next((index for index, stage in enumerate(stages) if stage.command == command), -1)
    if start < 0:
        return []
    tainted = {artifact.path for artifact in stages[start].produces}
    downstream: list[str] = []
    for stage in stages[start + 1 :]:
        if not any(artifact.path in tainted for artifact in stage.requires):
            continue
        for artifact in stage.produces:
            tainted.add(artifact.path)
            downstream.append(str(artifact.path).replace("\\", "/"))
    return downstream


def stage_artifacts_reusable(context: WorkspaceContext, command: str) -> bool:
    spec = stage_spec_by_command(command)
    store = ControlStore(context)
    states = {item["artifact_key"]: item for item in store.artifact_states()}
    fingerprint = stage_input_fingerprint(context.root, command)
    for artifact in spec.produces:
        current = describe_artifact(context.root, artifact)
        if current["status"] != "ready":
            return False
        state = states.get(current["artifact_key"])
        # One compatibility release may bootstrap manifests for existing V1 output.
        if state is None:
            continue
        if state.get("status") != "ready":
            return False
        if state.get("sha256") != current.get("sha256"):
            return False
        if state.get("input_fingerprint") != fingerprint:
            return False
    return True


def record_document_edit_artifacts(context: WorkspaceContext) -> None:
    """Keep manual final.md edits authoritative while invalidating quality outputs."""
    store = ControlStore(context)
    store.mark_artifact_states_stale(
        [
            *downstream_artifact_keys("build-md"),
            "workspace/compliance_report.json",
            "workspace/format_check_report.json",
        ],
        reason="终稿 Markdown 已由 document.apply_edit 修改",
        source_command="document.apply_edit",
    )
    final_md = describe_artifact(context.root, RunArtifact("outputs/final.md"))
    if final_md["status"] != "ready":
        raise ControlPlaneError("ARTIFACT_NOT_READY", "文档编辑后的 final.md 无效。", status_code=409)
    final_md.update(
        {
            "producer": "build-md",
            "stage_id": "build_markdown",
            "input_fingerprint": stage_input_fingerprint(context.root, "build-md"),
            "disposition": "manual_override",
            "overridden_by": "document.apply_edit",
        }
    )
    store.upsert_artifact_state(final_md)
    record_stage_artifacts(context, "build-docx", disposition="produced")


def record_external_chapter_mutation(
    context: WorkspaceContext,
    *,
    disposition: str,
) -> list[dict[str, Any]]:
    """Bridge non-Pipeline chapter writers into the SQLite Artifact graph."""
    spec = stage_spec_by_command("write-all")
    if not all(describe_artifact(context.root, artifact)["status"] == "ready" for artifact in spec.produces):
        return []
    return record_stage_artifacts(context, "write-all", disposition=disposition)
