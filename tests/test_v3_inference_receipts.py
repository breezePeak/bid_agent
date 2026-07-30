from __future__ import annotations

import json
import sqlite3
import tempfile
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext
from document_pipeline.artifact_promotion import (
    AgentProposalSandbox,
    validate_and_record,
)
from document_pipeline.canonicalization import canonical_hash
from document_pipeline.contracts import InputRole
from document_pipeline.input_manifest import InputManifestService
from document_pipeline.inference_runtime import INFERENCE_RUNTIME_REGISTRY
from document_pipeline.proposals import InferenceReceipt, InferenceReceiptRef
from document_pipeline.score_agent import ScoreAgent
from document_pipeline.score_model import load_promoted_score_model
from document_pipeline.stage_runner import V3StageRunner


def _scored_workspace(tmp_path: Path) -> WorkspaceContext:
    runs = tmp_path / "runs"
    (runs / "alpha").mkdir(parents=True)
    context = WorkspaceContext.resolve(runs, "alpha")
    tender = tmp_path / "tender.md"
    tender.write_text(
        "# 项目要求\n投标人须提供完整实施方案。\n\n"
        "# 评分办法\n实施方案完整、可行，满分10分。",
        encoding="utf-8",
    )
    InputManifestService(context).register_local_file(
        tender,
        InputRole.TENDER,
    )
    runner = V3StageRunner.for_deterministic_tests(context)
    runner.run("normalize_sources")
    runner.run("build_requirement_ledger")
    runner.run("analyze_scores")
    return context


def _candidate_proposal(
    context: WorkspaceContext,
    *,
    operation_id: str,
    refs: list[InferenceReceiptRef],
):
    active = ControlStore(context).v3_active_artifact("ScoreModel")
    assert active is not None
    model = load_promoted_score_model(context).model_copy(
        update={"revision": int(active["revision"]) + 1}
    )
    return ScoreAgent(context).create_score_model_proposal(
        model,
        base_revision=int(active["revision"]),
        operation_id=operation_id,
        requirement_revision=1,
        prompt_version="receipt-negative-test",
        model_fingerprint="receipt-negative-model",
        inference_receipt_refs=refs,
    )


def _rewrite_receipt_with_valid_content_hash(
    context: WorkspaceContext,
    receipt_id: str,
    *,
    updates: dict[str, object],
) -> InferenceReceiptRef:
    store = ControlStore(context)
    current = store.v3_inference_receipt(receipt_id)
    assert current is not None
    body = {
        key: value
        for key, value in current.items()
        if key not in {"receipt_hash", "created_at"}
    }
    body.update(updates)
    receipt = InferenceReceipt.model_validate(body)
    storage_record = receipt.storage_record()
    with sqlite3.connect(context.root / "workspace" / "control.db") as db:
        db.execute(
            """
            UPDATE v3_inference_receipts
               SET receipt_hash = ?, receipt_json = ?
             WHERE receipt_id = ?
            """,
            (
                storage_record["receipt_hash"],
                json.dumps(
                    storage_record,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                receipt_id,
            ),
        )
        db.commit()
    return InferenceReceiptRef(
        receipt_id=receipt_id,
        receipt_hash=storage_record["receipt_hash"],
    )


def test_semantic_proposal_without_inference_receipt_fails_closed() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _scored_workspace(Path(tmp))
        proposal = _candidate_proposal(
            context,
            operation_id="missing-receipt",
            refs=[],
        )
        AgentProposalSandbox(context, "score_agent").submit(proposal)

        report = validate_and_record(context, proposal.proposal_id)

        assert not report.passed
        assert {
            finding.code for finding in report.findings
        } >= {"INFERENCE_RECEIPT_REQUIRED"}


def test_nonexistent_or_hash_tampered_receipt_cannot_validate() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _scored_workspace(Path(tmp))
        store = ControlStore(context)
        active = store.v3_active_artifact("ScoreModel")
        assert active is not None
        active_proposal = store.v3_proposal(str(active["proposal_id"]))
        assert active_proposal is not None
        real_ref = InferenceReceiptRef.model_validate(
            active_proposal["inference_receipt_refs"][0]
        )
        cases = (
            InferenceReceiptRef(
                receipt_id="does-not-exist",
                receipt_hash="fake-hash",
            ),
            InferenceReceiptRef(
                receipt_id=real_ref.receipt_id,
                receipt_hash="tampered-hash",
            ),
        )
        expected_codes = (
            "INFERENCE_RECEIPT_NOT_FOUND",
            "INFERENCE_RECEIPT_HASH_MISMATCH",
        )
        for index, (ref, expected_code) in enumerate(
            zip(cases, expected_codes, strict=True),
            start=1,
        ):
            proposal = _candidate_proposal(
                context,
                operation_id=f"forged-receipt-{index}",
                refs=[ref],
            )
            AgentProposalSandbox(context, "score_agent").submit(proposal)
            report = validate_and_record(context, proposal.proposal_id)
            assert not report.passed
            assert expected_code in {
                finding.code for finding in report.findings
            }


def test_inference_proposal_without_trusted_runtime_metadata_fails_closed() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _scored_workspace(Path(tmp))
        store = ControlStore(context)
        active = store.v3_active_artifact("ScoreModel")
        assert active is not None
        active_proposal = store.v3_proposal(str(active["proposal_id"]))
        assert active_proposal is not None
        ref = InferenceReceiptRef.model_validate(
            active_proposal["inference_receipt_refs"][0]
        )
        proposal = _candidate_proposal(
            context,
            operation_id="missing-runtime-provider-metadata",
            refs=[ref],
        )
        AgentProposalSandbox(context, "score_agent").submit(proposal)

        with mock.patch.object(
            INFERENCE_RUNTIME_REGISTRY,
            "metadata",
            return_value=None,
        ):
            report = validate_and_record(context, proposal.proposal_id)

        assert not report.passed
        assert "INFERENCE_RUNTIME_METADATA_MISSING" in {
            finding.code for finding in report.findings
        }


def test_receipt_hashes_the_exact_provider_input_snapshot() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _scored_workspace(Path(tmp))
        store = ControlStore(context)
        active = store.v3_active_artifact("ScoreModel")
        assert active is not None
        proposal = store.v3_proposal(str(active["proposal_id"]))
        assert proposal is not None
        ref = InferenceReceiptRef.model_validate(
            proposal["inference_receipt_refs"][0]
        )
        receipt = store.v3_inference_receipt(ref.receipt_id)
        assert receipt is not None

        assert str(receipt["input_snapshot"])
        assert receipt["input_snapshot_hash"] == canonical_hash(
            receipt["input_snapshot"]
        )
        assert len(str(receipt["input_snapshot_hash"])) == 64
        assert receipt["input_snapshot_hash"] != canonical_hash(
            receipt["input_artifact_refs"]
        )
        assert str(receipt["provider_fingerprint"])


def test_coherently_rehashed_fake_input_still_fails_exact_reconstruction() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _scored_workspace(Path(tmp))
        store = ControlStore(context)
        active = store.v3_active_artifact("ScoreModel")
        assert active is not None
        active_proposal = store.v3_proposal(str(active["proposal_id"]))
        assert active_proposal is not None
        original_ref = InferenceReceiptRef.model_validate(
            active_proposal["inference_receipt_refs"][0]
        )
        receipt = store.v3_inference_receipt(original_ref.receipt_id)
        assert receipt is not None
        fake_snapshot = f"{receipt['input_snapshot']}#forged-input"
        forged_ref = _rewrite_receipt_with_valid_content_hash(
            context,
            original_ref.receipt_id,
            updates={
                "input_snapshot": fake_snapshot,
                "input_snapshot_hash": canonical_hash(fake_snapshot),
            },
        )
        proposal = _candidate_proposal(
            context,
            operation_id="coherently-rehashed-fake-input",
            refs=[forged_ref],
        )
        AgentProposalSandbox(context, "score_agent").submit(proposal)

        report = validate_and_record(context, proposal.proposal_id)
        codes = {finding.code for finding in report.findings}

        assert not report.passed
        assert "INFERENCE_EXACT_INPUT_MISMATCH" in codes
        assert "INFERENCE_INPUT_CONTENT_HASH_MISMATCH" not in codes


def test_coherently_rehashed_provider_fingerprint_fails_runtime_policy() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _scored_workspace(Path(tmp))
        store = ControlStore(context)
        active = store.v3_active_artifact("ScoreModel")
        assert active is not None
        active_proposal = store.v3_proposal(str(active["proposal_id"]))
        assert active_proposal is not None
        original_ref = InferenceReceiptRef.model_validate(
            active_proposal["inference_receipt_refs"][0]
        )
        forged_ref = _rewrite_receipt_with_valid_content_hash(
            context,
            original_ref.receipt_id,
            updates={"provider_fingerprint": "forged-provider"},
        )
        proposal = _candidate_proposal(
            context,
            operation_id="coherently-rehashed-fake-provider",
            refs=[forged_ref],
        )
        AgentProposalSandbox(context, "score_agent").submit(proposal)

        report = validate_and_record(context, proposal.proposal_id)

        assert not report.passed
        assert "INFERENCE_RUNTIME_METADATA_MISMATCH" in {
            finding.code for finding in report.findings
        }
