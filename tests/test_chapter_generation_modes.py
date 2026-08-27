from __future__ import annotations

from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from control_plane import WorkspaceContext
from document_pipeline.chapter_writing_service import (
    ChapterWritingRequest,
    ChapterWritingService,
)
from document_pipeline.contracts import ContentBlock, WriterInputBundle


MODES = ("copy", "light_edit", "restructure", "new_write")


def _bundle(mode: str) -> WriterInputBundle:
    legacy_sources = (
        []
        if mode == "new_write"
        else [{"block_id": "OLD-1", "content_hash": "old", "content": "旧稿正文"}]
    )
    required_changes = (
        ["替换项目名称"] if mode in {"light_edit", "restructure"} else []
    )
    return WriterInputBundle(
        revision=1,
        source_hashes={},
        bundle_id=f"bundle-{mode}",
        bundle_hash=f"hash-{mode}",
        unit_id="chapter-CH-1",
        source_blueprint_artifact_id="BP-1",
        source_blueprint_revision=1,
        source_blueprint_hash="bp-hash",
        h1_receipt_id="H1-1",
        effective_generation_mode=mode,
        blueprint_slice=[
            {
                "chapter_id": "CH-1",
                "title": "实施方案",
                "purpose": "说明实施方案",
                "writing_objectives": ["形成可执行方案"],
                "rewrite_mode": None if mode == "new_write" else mode,
                "legacy_sources": legacy_sources,
                "required_changes": required_changes,
            }
        ],
        document_target_constraints=[
            {"node_id": "CH-1", "output_target": "CH-1", "title": "实施方案"}
        ],
        prompt_version="test",
        model_config_hash="test",
    )


class _Assembler:
    def __init__(self, bundle: WriterInputBundle) -> None:
        self.bundle = bundle

    def assemble(self, unit_id: str, node_ids: list[str]) -> WriterInputBundle:
        del unit_id, node_ids
        return self.bundle


class _Research:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_for_bundle(self, bundle: WriterInputBundle):
        self.calls += 1
        return {"needs_research": False, "decision_status": "skipped"}, []


class _Writer:
    def __init__(self) -> None:
        self.bundles: list[WriterInputBundle] = []

    def stream_bundle(self, bundle: WriterInputBundle, *, operation_id: str = ""):
        del operation_id
        self.bundles.append(bundle)
        return [
            ContentBlock(
                block_id=f"B-{bundle.effective_generation_mode}",
                target_node_id="CH-1",
                type="paragraph",
                content="统一 ContentWriter 输出的正文。",
                confidence=1,
            )
        ]


class _Gate:
    def validate(self, bundle, blocks):
        del bundle, blocks
        return object()


@pytest.mark.parametrize("mode", MODES)
def test_generation_modes_share_writer_and_only_new_write_runs_research(mode: str) -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        runs = Path(temporary) / "runs"
        (runs / "alpha").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        research = _Research()
        writer = _Writer()
        result = ChapterWritingService(
            context,
            assembler=_Assembler(_bundle(mode)),
            research=research,
            writer=writer,
            quality_gate=_Gate(),
            deterministic_test=True,
        ).write(
            ChapterWritingRequest(
                unit_id="chapter-CH-1",
                node_ids=("CH-1",),
                chapter_id="CH-1",
                run_research=True,
                commit_drafts=False,
            )
        )

    assert result.bundle.effective_generation_mode == mode
    assert len(writer.bundles) == 1
    assert research.calls == (1 if mode == "new_write" else 0)


def test_generation_mode_vocabulary_is_exact() -> None:
    field = WriterInputBundle.model_fields["effective_generation_mode"]
    assert set(field.annotation.__args__) == set(MODES)
