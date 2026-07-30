from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402


def test_environment_cannot_enable_deterministic_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "runs" / "runtime-mode"
    workspace.mkdir(parents=True)
    context = WorkspaceContext.resolve(tmp_path / "runs", "runtime-mode")
    monkeypatch.setenv("BID_AGENT_INFERENCE_MODE", "deterministic_test")

    with pytest.raises(ValueError, match="不能启用 deterministic_test"):
        V3StageRunner(context)

    runner = V3StageRunner.for_deterministic_tests(context)
    assert runner.inference_mode == "deterministic_test"

