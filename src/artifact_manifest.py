from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext
from pipeline_registry import RunArtifact, stage_spec_by_command


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
    return store.upsert_artifact_states(manifests)
