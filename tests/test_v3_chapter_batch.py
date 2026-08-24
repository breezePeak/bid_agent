from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import CommandEnvelope, CommandGateway, ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.chapter_batch import ChapterBatchService  # noqa: E402


def _workspace(base: Path) -> WorkspaceContext:
    runs = base / "runs"
    (runs / "alpha").mkdir(parents=True)
    return WorkspaceContext.resolve(runs, "alpha")


def test_batch_store_persists_items_events_and_recovery() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = ControlStore(_workspace(Path(tmp)))
        job = store.create_batch_job(
            [
                {"chapter_id": "leaf-a", "chapter_title": "章节 A", "context_ref": {"chapter_context_revision": 1}},
                {"chapter_id": "leaf-b", "chapter_title": "章节 B", "context_ref": {"chapter_context_revision": 2}},
            ],
            job_id="batch-1",
        )

        assert job["status"] == "queued"
        assert [item["chapter_id"] for item in job["items"]] == ["leaf-a", "leaf-b"]
        # Background job records must not occupy the workspace write-operation slot.
        assert store.snapshot()["operation"]["status"] == "background"

        first = job["items"][0]
        event = store.append_batch_event(
            "batch-1",
            event_type="analysis_started",
            status="running",
            stage="analyzing",
            item_id=first["item_id"],
            chapter_id=first["chapter_id"],
            chapter_title=first["chapter_title"],
            message="开始分析",
        )
        store.save_batch_checkpoint(
            "batch-1",
            first["item_id"],
            stage="analyzing",
            input_hash="ctx-1",
            artifact_refs={"draft": "draft-1"},
            event_sequence=event["sequence"],
        )
        store.update_batch_item(first["item_id"], status="failed", stage="committing", error={"code": "COMMIT_FAILED"})
        store.update_batch_job("batch-1", status="paused", current_chapter_id="leaf-a", error={"code": "COMMIT_FAILED"})

        restored = store.latest_batch_job()
        assert restored is not None
        assert restored["status"] == "paused"
        assert restored["items"][0]["error"]["code"] == "COMMIT_FAILED"
        assert store.batch_events("batch-1", after_sequence=0)[0]["chapter_title"] == "章节 A"
        assert store.recover_batch_jobs()[0]["job_id"] == "batch-1"


def test_background_batch_operation_does_not_block_chapter_worker_command() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _workspace(Path(tmp))
        store = ControlStore(context)
        store.create_batch_job(
            [{"chapter_id": "leaf-a", "chapter_title": "章节 A"}],
            job_id="batch-1",
        )
        gateway = CommandGateway(
            context,
            {
                "document.run_pipeline": lambda _ctx, _envelope, _operation_id: {
                    "accepted": True,
                    "operation_status": "succeeded",
                    "message": "chapter committed",
                }
            },
        )
        receipt = gateway.submit(
            CommandEnvelope.from_mapping(
                {
                    "kind": "document.run_pipeline",
                    "payload": {"chapter_ids": ["leaf-a"]},
                    "expected_revision": store.revision(),
                    "idempotency_key": "batch-1:leaf-a:1",
                },
                workspace_id="alpha",
            )
        )

        assert receipt.status == "accepted"


def test_new_batch_claim_invalidates_old_worker_fencing_token() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = ControlStore(_workspace(Path(tmp)))
        store.create_batch_job([{"chapter_id": "leaf-a"}], job_id="batch-1")
        first = store.claim_batch_job("batch-1")
        second = store.claim_batch_job("batch-1")

        assert second["fencing_token"] > first["fencing_token"]
        try:
            store.assert_batch_fence("batch-1", first["fencing_token"])
        except ControlPlaneError as exc:
            assert exc.code == "CHAPTER_BATCH_LEASE_LOST"
        else:
            raise AssertionError("old batch worker token was not rejected")


def test_parent_selection_expands_only_ordered_leaf_descendants() -> None:
    service = ChapterBatchService.__new__(ChapterBatchService)

    class Chapters:
        @staticmethod
        def list_chapters(*, include_archived: bool):
            assert include_archived is False
            return {
                "items": [
                    {"chapter_id": "parent", "title": "父目录", "parent_chapter_id": None, "order": 1, "is_leaf": False},
                    {"chapter_id": "leaf-b", "title": "叶子 B", "parent_chapter_id": "parent", "order": 3, "is_leaf": True},
                    {"chapter_id": "middle", "title": "中间目录", "parent_chapter_id": "parent", "order": 2, "is_leaf": False},
                    {"chapter_id": "leaf-a", "title": "叶子 A", "parent_chapter_id": "middle", "order": 1, "is_leaf": True},
                ]
            }

    service.chapters = Chapters()
    resolved = service._resolve_leaf_chapters(["parent", "leaf-b"])

    assert [item["chapter_id"] for item in resolved] == ["leaf-a", "leaf-b"]
    assert all(item["is_leaf"] for item in resolved)


def test_worker_only_completes_after_content_revision_increases() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _workspace(Path(tmp))
        service = ChapterBatchService(context)
        job = service.store.create_batch_job(
            [{"chapter_id": "leaf-a", "chapter_title": "章节 A"}],
            job_id="batch-commit",
        )

        class Chapters:
            calls = 0

            def get_chapter(self, chapter_id: str):
                self.calls += 1
                return {
                    "chapter_id": chapter_id,
                    "title": "章节 A",
                    "head_content_revision": 0,
                    "context": {"context_revision": 1, "content_hash": "stored", "items": [{"body": "目标"}]},
                }

        service.chapters = Chapters()
        first = job["items"][0]
        service.store.update_batch_item(
            first["item_id"],
            context_ref=service._context_ref(service.chapters.get_chapter("leaf-a")),
        )

        class WritingService:
            @staticmethod
            def iter_events(request):
                assert request.chapter_id == "leaf-a"
                yield {"type": "done"}

        service._writing_service_factory = lambda _context: WritingService()
        service._run("batch-commit")

        failed = service.store.batch_job("batch-commit")
        assert failed["status"] == "paused"
        assert failed["completed_count"] == 0
        assert failed["items"][0]["error"]["code"] == "CHAPTER_DRAFT_COMMIT_REJECTED"


def test_worker_starts_next_chapter_only_after_previous_revision_increases() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        context = _workspace(Path(tmp))
        service = ChapterBatchService(context)
        job = service.store.create_batch_job(
            [
                {"chapter_id": "leaf-a", "chapter_title": "章节 A"},
                {"chapter_id": "leaf-b", "chapter_title": "章节 B"},
            ],
            job_id="batch-sequential",
        )

        class Chapters:
            revisions = {"leaf-a": 0, "leaf-b": 0}

            def get_chapter(self, chapter_id: str):
                return {
                    "chapter_id": chapter_id,
                    "title": chapter_id,
                    "head_content_revision": self.revisions[chapter_id],
                    "context": {"context_revision": 1, "content_hash": f"stored-{chapter_id}", "items": [{"body": chapter_id}]},
                    "content": {"blocks": [{"content": f"正文 {chapter_id}"}]},
                }

        service.chapters = Chapters()
        for item in job["items"]:
            chapter = service.chapters.get_chapter(item["chapter_id"])
            service.store.update_batch_item(item["item_id"], context_ref=service._context_ref(chapter))

        started = []

        class WritingService:
            @staticmethod
            def iter_events(request):
                chapter_id = request.chapter_id
                assert chapter_id is not None
                if chapter_id == "leaf-b":
                    assert service.chapters.revisions["leaf-a"] == 1
                started.append(chapter_id)
                service.chapters.revisions[chapter_id] += 1
                yield {"type": "done"}

        service._writing_service_factory = lambda _context: WritingService()
        service._run("batch-sequential")

        completed = service.store.batch_job("batch-sequential")
        assert started == ["leaf-a", "leaf-b"]
        assert completed["status"] == "succeeded"
        assert completed["completed_count"] == 2
        assert [item["content_revision"] for item in completed["items"]] == [1, 1]
