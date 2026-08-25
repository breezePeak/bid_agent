from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.canonicalization import canonical_json  # noqa: E402
from document_pipeline.inference_checkpoints import (  # noqa: E402
    InferenceCheckpointService,
)


class _Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


@dataclass
class _Result:
    candidate: _Candidate
    raw_output: str
    normalized_output: str
    reasoning: str
    input_snapshot: str
    attempt_count: int
    capability_id: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    provider_fingerprint: str
    model_fingerprint: str
    temperature: float


def test_validated_checkpoint_reuses_only_the_exact_runtime_and_input(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runs" / "checkpoint"
    workspace.mkdir(parents=True)
    context = WorkspaceContext.resolve(tmp_path / "runs", "checkpoint")
    service = InferenceCheckpointService(context)
    input_snapshot = {"source": ["B-1"]}
    runtime = {
        "capability_id": "planning.project_understanding",
        "capability_version": "1",
        "prompt_version": "prompt-1",
        "prompt_hash": "prompt-hash",
        "schema_version": "schema-1",
        "provider_fingerprint": "provider-1",
        "model_fingerprint": "model-1",
        "temperature": 0.1,
        "input_snapshot": input_snapshot,
    }
    cache_key = service.cache_key(**runtime)
    result = _Result(
        candidate=_Candidate(value="已校验"),
        raw_output='{"value":"已校验"}',
        normalized_output='{"value":"已校验"}',
        reasoning="",
        input_snapshot=canonical_json(input_snapshot),
        attempt_count=1,
        capability_id=runtime["capability_id"],
        prompt_version=runtime["prompt_version"],
        prompt_hash=runtime["prompt_hash"],
        schema_version=runtime["schema_version"],
        provider_fingerprint=runtime["provider_fingerprint"],
        model_fingerprint=runtime["model_fingerprint"],
        temperature=runtime["temperature"],
    )

    service.save(
        cache_key=cache_key,
        operation_id="op-1",
        result=result,
        capability_version="1",
    )
    hit = service.load(
        cache_key=cache_key,
        candidate_model=_Candidate,
        expected_input_snapshot=input_snapshot,
    )

    assert hit is not None
    assert hit.record["candidate"].value == "已校验"
    assert service.cache_key(**{**runtime, "temperature": 0.2}) != cache_key
    assert service.cache_key(
        **{**runtime, "input_snapshot": {"source": ["B-2"]}}
    ) != cache_key
