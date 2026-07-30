from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.llm_telemetry import (  # noqa: E402
    llm_request_metadata,
    llm_stage_context,
    record_llm_request,
)
from document_pipeline.workspace_snapshot import (  # noqa: E402
    V3WorkspaceSnapshotBuilder,
)


def test_llm_request_is_visible_while_running_and_finishes_without_secrets():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        runs = Path(temp) / "runs"
        (runs / "alpha").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        store = ControlStore(context)
        messages = [
            {"role": "system", "content": "controlled prompt"},
            {"role": "user", "content": "frozen input"},
        ]

        with llm_stage_context(
            context,
            "operation-1",
            "score_semantic",
            capability_id="score.semantic_interpretation",
            prompt_version="prompt-v1",
            schema_version="schema-v1",
            model="openai:test-model",
            temperature=0.1,
        ):
            with record_llm_request(messages):
                running = store.llm_requests("operation-1")
                assert len(running) == 1
                assert running[0]["status"] == "running"
                assert running[0]["parameters"]["messages"] == messages
                assert "api_key" not in running[0]["parameters"]

        finished = store.llm_requests("operation-1")
        assert finished[0]["status"] == "succeeded"
        assert finished[0]["completed_at"]


def test_llm_request_records_logical_batch_and_attempt_kind():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        runs = Path(temp) / "runs"
        (runs / "alpha").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        store = ControlStore(context)

        with llm_stage_context(
            context,
            "operation-lineage",
            "project_understanding",
            capability_id="planning.project_understanding",
            prompt_version="prompt-v1",
            schema_version="schema-v1",
            model="openai:test-model",
            temperature=0.1,
        ):
            with llm_request_metadata(
                logical_batch_id="score-group:business",
                attempt_kind="controlled_repair",
                candidate_attempt=2,
                repair_of_attempt=1,
            ):
                with record_llm_request(
                    [{"role": "user", "content": "repair"}]
                ):
                    pass

        parameters = store.llm_requests("operation-lineage")[0]["parameters"]
        assert parameters["logical_batch_id"] == "score-group:business"
        assert parameters["attempt_kind"] == "controlled_repair"
        assert parameters["candidate_attempt"] == 2
        assert parameters["repair_of_attempt"] == 1


def test_llm_request_failure_and_stage_projection_preserve_each_attempt():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        runs = Path(temp) / "runs"
        (runs / "alpha").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        store = ControlStore(context)

        try:
            with llm_stage_context(
                context,
                "operation-2",
                "project_understanding",
                capability_id="planning.project_understanding",
                prompt_version="prompt-v1",
                schema_version="schema-v1",
                model="openai:test-model",
                temperature=0.1,
            ):
                with record_llm_request([{"role": "user", "content": "first"}]):
                    pass
                with record_llm_request([{"role": "user", "content": "repair"}]):
                    raise RuntimeError("provider unavailable")
        except RuntimeError:
            pass

        requests = store.llm_requests("operation-2")
        assert [item["request_index"] for item in requests] == [1, 2]
        assert [item["status"] for item in requests] == ["succeeded", "failed"]
        assert requests[1]["error"] == "provider unavailable"

        stage = V3WorkspaceSnapshotBuilder(context)._pipeline_stage(
            "project_understanding",
            "failed",
            None,
            {"project_understanding": requests},
        )
        assert stage["llm_request_count"] == 2
        assert stage["llm_requests"] == requests
