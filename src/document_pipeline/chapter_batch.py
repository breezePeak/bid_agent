"""Durable, resumable orchestration for sequential chapter generation."""

from __future__ import annotations

import threading
import uuid
from typing import Any

from control_plane import (
    CommandEnvelope,
    CommandGateway,
    ControlPlaneError,
    ControlStore,
    WorkspaceContext,
)

from .canonicalization import chapter_context_hash
from .chapter_workspace import ChapterWorkspaceService
from .execution_controller import V3ExecutionController


_RUNNING: set[tuple[str, str]] = set()
_RUNNING_LOCK = threading.Lock()


def _error_payload(exc: Exception, *, stage: str, chapter_id: str = "") -> dict[str, Any]:
    if isinstance(exc, ControlPlaneError):
        result = exc.as_dict()
    else:
        transient = isinstance(exc, (TimeoutError, ConnectionError)) or any(
            token in type(exc).__name__.lower()
            for token in ("timeout", "connection", "temporar")
        )
        result = {
            "code": type(exc).__name__ or "CHAPTER_BATCH_FAILED",
            "message": str(exc) or "章节批量编写失败。",
            "retryable": transient,
            "details": {},
        }
    result.setdefault("retryable", False)
    details = result.get("details")
    details = dict(details) if isinstance(details, dict) else {}
    details.update({"stage": stage, "chapter_id": chapter_id})
    result["details"] = details
    result.setdefault("action_required", "retry_or_cancel")
    return result


class ChapterBatchService:
    """Create persistent jobs and execute them outside the HTTP request."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.chapters = ChapterWorkspaceService(context)

    def _resolve_leaf_chapters(self, requested_ids: list[str]) -> list[dict[str, Any]]:
        listing = self.chapters.list_chapters(include_archived=False)
        rows = [item for item in listing.get("items") or [] if isinstance(item, dict)]
        by_id = {str(item.get("chapter_id") or ""): item for item in rows}
        children: dict[str, list[dict[str, Any]]] = {}
        for item in rows:
            parent = str(item.get("parent_chapter_id") or "")
            children.setdefault(parent, []).append(item)
        for values in children.values():
            values.sort(key=lambda item: (int(item.get("order") or 0), str(item.get("chapter_id") or "")))

        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()

        def visit(chapter_id: str) -> None:
            item = by_id.get(chapter_id)
            if item is None:
                raise ControlPlaneError(
                    "CHAPTER_NOT_IN_BLUEPRINT",
                    f"目录中不存在章节: {chapter_id}",
                    status_code=404,
                    details={"chapter_id": chapter_id},
                )
            descendants = children.get(chapter_id) or []
            if descendants:
                for child in descendants:
                    visit(str(child.get("chapter_id") or ""))
                return
            if item.get("is_leaf") is False or str(item.get("status") or "") == "archived":
                return
            if chapter_id not in seen:
                seen.add(chapter_id)
                resolved.append(item)

        for chapter_id in requested_ids:
            normalized = str(chapter_id or "").strip()
            if normalized:
                visit(normalized)
        if not resolved:
            raise ControlPlaneError("CHAPTER_BATCH_EMPTY", "所选目录下没有可编写的叶子章节。", status_code=400)
        return resolved

    @staticmethod
    def _context_ref(chapter: dict[str, Any]) -> dict[str, Any]:
        context = chapter.get("context") if isinstance(chapter.get("context"), dict) else {}
        revision = int(context.get("context_revision") or 0)
        items = context.get("items") or []
        return {
            "chapter_context_id": f"chapter-context:{chapter['chapter_id']}",
            "chapter_context_revision": revision,
            "chapter_context_hash": chapter_context_hash(str(chapter["chapter_id"]), revision, items),
            "content_hash": str(context.get("content_hash") or ""),
        }

    def create(
        self,
        chapter_ids: list[str],
        *,
        actor: dict[str, Any] | None = None,
        idempotency_key: str = "",
        schedule: bool = True,
    ) -> dict[str, Any]:
        from .artifact_promotion import HumanGateService
        from .global_project_context import GlobalProjectContextService

        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.context.workspace_id}:{idempotency_key}")) if idempotency_key else str(uuid.uuid4())
        leaves = self._resolve_leaf_chapters(chapter_ids)
        resolved_ids = [str(item.get("chapter_id") or "") for item in leaves]
        existing = self.store.batch_job(job_id)
        if existing:
            if list(existing.get("chapter_ids") or []) != resolved_ids:
                raise ControlPlaneError(
                    "CHAPTER_BATCH_IDEMPOTENCY_CONFLICT",
                    "相同幂等键对应的章节选择不同，请使用新的请求标识。",
                    status_code=409,
                    details={
                        "job_id": job_id,
                        "existing_chapter_ids": list(existing.get("chapter_ids") or []),
                        "requested_chapter_ids": resolved_ids,
                    },
                )
            if schedule:
                self.schedule(job_id)
            return existing
        GlobalProjectContextService(self.context).load_model()
        HumanGateService(self.context).require_current_confirmation()
        active = self.store.latest_batch_job()
        if active and str(active.get("status") or "") in {"queued", "running", "paused"}:
            raise ControlPlaneError(
                "CHAPTER_BATCH_ALREADY_ACTIVE",
                "已有批量编写任务正在执行或等待处理，请先继续或取消该任务。",
                status_code=409,
                details={"job_id": active.get("job_id"), "status": active.get("status")},
            )
        prepared: list[dict[str, Any]] = []
        for item in leaves:
            chapter_id = str(item.get("chapter_id") or "")
            chapter = self.chapters.get_chapter(chapter_id)
            if not chapter.get("materialized"):
                self.chapters.create(chapter_id=chapter_id, actor=actor or {})
                chapter = self.chapters.get_chapter(chapter_id)
            context = chapter.get("context") if isinstance(chapter.get("context"), dict) else {}
            if not context or not str(context.get("content_hash") or ""):
                raise ControlPlaneError(
                    "CHAPTER_CONTEXT_REQUIRED",
                    f"章节上下文未就绪: 《{chapter.get('title') or chapter_id}》（{chapter_id}）",
                    status_code=409,
                    details={"chapter_id": chapter_id, "chapter_title": chapter.get("title") or chapter_id},
                )
            prepared.append(
                {
                    "chapter_id": chapter_id,
                    "chapter_title": str(chapter.get("title") or chapter_id),
                    "context_ref": self._context_ref(chapter),
                }
            )
        # Stable caller keys return the existing durable job instead of duplicating work.
        job = self.store.create_batch_job(
            prepared,
            job_id=job_id,
            retry_policy={"max_attempts": 3, "pause_on_failure": True},
            actor=str((actor or {}).get("id") or "user"),
        )
        self.store.append_batch_event(
            job_id,
            event_type="job_created",
            status="queued",
            message=f"已提交 {len(prepared)} 个叶子章节。",
            data={"chapter_ids": [item["chapter_id"] for item in prepared]},
        )
        for batch_item in job.get("items") or []:
            self.store.append_batch_event(
                job_id,
                event_type="chapter_queued",
                status="queued",
                stage="queued",
                item_id=str(batch_item.get("item_id") or ""),
                chapter_id=str(batch_item.get("chapter_id") or ""),
                chapter_title=str(batch_item.get("chapter_title") or ""),
                message="章节已进入编写队列。",
            )
        if schedule:
            self.schedule(job_id)
        return self.store.batch_job(job_id) or job

    def schedule(self, job_id: str) -> bool:
        key = (self.context.workspace_id, str(job_id))
        with _RUNNING_LOCK:
            if key in _RUNNING:
                return False
            _RUNNING.add(key)
        thread = threading.Thread(
            target=self._run_guarded,
            args=(str(job_id), key),
            name=f"chapter-batch-{str(job_id)[:8]}",
            daemon=True,
        )
        thread.start()
        return True

    def _run_guarded(self, job_id: str, key: tuple[str, str]) -> None:
        try:
            self._run(job_id)
        finally:
            with _RUNNING_LOCK:
                _RUNNING.discard(key)

    def _event(self, job_id: str, item: dict[str, Any], event_type: str, *, stage: str, status: str, message: str, data: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
        item_id = str(item.get("item_id") or "")
        current = self.store.batch_item(item_id) if item_id else None
        event_data = dict(data or {})
        event_data.setdefault("attempt", int((current or item).get("attempt") or 0))
        return self.store.append_batch_event(
            job_id,
            event_type=event_type,
            status=status,
            stage=stage,
            item_id=item_id,
            chapter_id=str(item.get("chapter_id") or ""),
            chapter_title=str(item.get("chapter_title") or ""),
            message=message,
            data=event_data,
            error=error,
        )

    def _run(self, job_id: str) -> None:
        job = self.store.claim_batch_job(job_id)
        if not job:
            return
        fencing_token = int(job.get("fencing_token") or 0)
        self.store.update_batch_job(job_id, status="running", current_chapter_id="", error={})
        self.store.append_batch_event(job_id, event_type="job_started", status="running", message="批量编写已开始。")
        completed = int(job.get("completed_count") or 0)
        failed = int(job.get("failed_count") or 0)
        for item in job.get("items") or []:
            self.store.assert_batch_fence(job_id, fencing_token)
            latest_job = self.store.batch_job(job_id) or {}
            if str(latest_job.get("status") or "") == "cancelled":
                return
            if str(item.get("status") or "") in {"succeeded", "skipped"}:
                continue
            chapter_id = str(item.get("chapter_id") or "")
            title = str(item.get("chapter_title") or chapter_id)
            current = self.store.batch_item(str(item.get("item_id") or "")) or item
            recovery = self.store.batch_checkpoint(str(item.get("item_id") or ""), stage="preflight")
            recovery_refs = recovery.get("artifact_refs") if isinstance(recovery, dict) else {}
            recovery_refs = recovery_refs if isinstance(recovery_refs, dict) else {}
            resuming_attempt = bool(recovery) and str(current.get("status") or "") == "running"
            attempt = max(1, int(current.get("attempt") or 0)) if resuming_attempt else int(current.get("attempt") or 0) + 1
            self.store.update_batch_job(job_id, status="running", current_chapter_id=chapter_id)
            self.store.update_batch_item(str(item["item_id"]), status="running", stage="preflight", attempt=attempt, error={})
            self._event(job_id, item, "chapter_started", stage="preflight", status="running", message=f"开始编写《{title}》。")
            try:
                chapter = self.chapters.get_chapter(chapter_id)
                requested_ref = dict(current.get("context_ref") or {})
                actual_ref = self._context_ref(chapter)
                if requested_ref and any(
                    requested_ref.get(key) != actual_ref.get(key)
                    for key in ("chapter_context_id", "chapter_context_revision", "chapter_context_hash")
                ):
                    raise ControlPlaneError(
                        "CHAPTER_CONTEXT_CONFLICT",
                        f"《{title}》上下文已更新，请确认后重试本章。",
                        status_code=409,
                        details={"chapter_id": chapter_id, "requested": requested_ref, "current": actual_ref},
                    )
                before_revision = (
                    int(recovery_refs.get("before_content_revision") or 0)
                    if resuming_attempt and "before_content_revision" in recovery_refs
                    else int(chapter.get("head_content_revision") or 0)
                )
                self.store.save_batch_checkpoint(
                    job_id,
                    str(item["item_id"]),
                    stage="preflight",
                    input_hash=str(actual_ref.get("chapter_context_hash") or ""),
                    artifact_refs={
                        "chapter_id": chapter_id,
                        "before_content_revision": before_revision,
                    },
                )
                self.store.update_batch_item(str(item["item_id"]), stage="analyzing")
                self._event(job_id, item, "analysis_started", stage="analyzing", status="running", message="正在分析章节目标、上下文及上下游关系。")
                self.store.update_batch_item(str(item["item_id"]), stage="researching")
                self._event(job_id, item, "research_started", stage="researching", status="running", message="正在检查资料与公开检索需求。")
                self.store.update_batch_item(str(item["item_id"]), stage="drafting")
                self._event(job_id, item, "draft_started", stage="drafting", status="running", message="章节 Agent 正在生成正文。")

                snapshot = self.store.snapshot()
                envelope = CommandEnvelope.from_mapping(
                    {
                        "kind": "document.run_pipeline",
                        "payload": {"chapter_ids": [chapter_id]},
                        "actor": {"type": "system", "id": "chapter-batch-worker"},
                        "expected_revision": int(snapshot.get("revision") or 0),
                        "idempotency_key": f"chapter-batch:{job_id}:{item['item_id']}:{attempt}",
                    },
                    workspace_id=self.context.workspace_id,
                )
                controller = V3ExecutionController(self.context)
                receipt = CommandGateway(self.context, controller.handlers()).submit(envelope)
                self.store.assert_batch_fence(job_id, fencing_token)
                receipt_data = receipt.as_dict()
                result = receipt_data.get("result") if isinstance(receipt_data.get("result"), dict) else {}
                operation_status = str(result.get("operation_status") or receipt_data.get("status") or "")
                if operation_status not in {"succeeded", "completed"}:
                    raw_error = receipt_data.get("error") if isinstance(receipt_data.get("error"), dict) else {}
                    raise ControlPlaneError(
                        str(raw_error.get("code") or "CHAPTER_GENERATION_NOT_COMMITTED"),
                        str(raw_error.get("message") or receipt_data.get("message") or "章节生成未完成。"),
                        status_code=409,
                        retryable=bool(raw_error.get("retryable")),
                        details={"chapter_id": chapter_id, "receipt": receipt_data},
                    )

                updated = self.chapters.get_chapter(chapter_id)
                generated_text = "\n\n".join(
                    str(block.get("content") or "")
                    for block in ((updated.get("content") or {}).get("blocks") or [])
                    if isinstance(block, dict) and str(block.get("content") or "").strip()
                )
                self._event(
                    job_id,
                    item,
                    "research_result",
                    stage="researching",
                    status="running",
                    message="资料检查与必要检索已完成。",
                )
                if generated_text:
                    self._event(
                        job_id,
                        item,
                        "draft_delta",
                        stage="drafting",
                        status="running",
                        message="",
                        data={"text": generated_text[:12000]},
                    )
                self.store.update_batch_item(str(item["item_id"]), stage="validating")
                self._event(job_id, item, "validation_started", stage="validating", status="running", message="正在校验章节正文与事实约束。")
                self.store.update_batch_item(str(item["item_id"]), stage="committing")
                self._event(job_id, item, "commit_started", stage="committing", status="running", message="正在确认正文写入中间文档。")
                revision = int(updated.get("head_content_revision") or 0)
                if revision <= before_revision:
                    raise ControlPlaneError(
                        "CHAPTER_DRAFT_COMMIT_REJECTED",
                        f"《{title}》正文未产生新的中间文档版本。",
                        status_code=409,
                        details={"chapter_id": chapter_id, "before_revision": before_revision, "current_revision": revision},
                    )
                self.store.assert_batch_fence(job_id, fencing_token)
                completed += 1
                self.store.update_batch_item(str(item["item_id"]), status="succeeded", stage="committed", content_revision=revision, error={})
                self.store.update_batch_job(job_id, completed_count=completed, failed_count=failed)
                committed = self._event(
                    job_id,
                    item,
                    "chapter_committed",
                    stage="committed",
                    status="succeeded",
                    message=f"《{title}》正文已写入中间文档，版本 {revision}。",
                    data={"head_content_revision": revision},
                )
                self.store.save_batch_checkpoint(
                    job_id,
                    str(item["item_id"]),
                    stage="committed",
                    input_hash=str(actual_ref.get("chapter_context_hash") or ""),
                    artifact_refs={"chapter_id": chapter_id, "head_content_revision": revision},
                    event_sequence=int(committed.get("sequence") or 0),
                )
            except Exception as exc:
                if isinstance(exc, ControlPlaneError) and exc.code in {
                    "CHAPTER_BATCH_LEASE_LOST",
                    "CHAPTER_BATCH_CANCELLED",
                }:
                    return
                try:
                    self.store.assert_batch_fence(job_id, fencing_token)
                except ControlPlaneError:
                    return
                stage = str((self.store.batch_item(str(item["item_id"])) or {}).get("stage") or "failed")
                error = _error_payload(exc, stage=stage, chapter_id=chapter_id)
                max_attempts = int((job.get("retry_policy") or {}).get("max_attempts") or 3)
                if bool(error.get("retryable")) and attempt < max_attempts:
                    self.store.update_batch_item(
                        str(item["item_id"]),
                        status="queued",
                        stage="retrying",
                        error=error,
                    )
                    self.store.update_batch_job(
                        job_id,
                        status="queued",
                        current_chapter_id=chapter_id,
                        error=error,
                    )
                    self._event(
                        job_id,
                        item,
                        "chapter_retry_scheduled",
                        stage=stage,
                        status="queued",
                        message=f"《{title}》将在短暂退避后重试（{attempt}/{max_attempts}）。",
                        error=error,
                    )
                    timer = threading.Timer(
                        min(2 ** attempt, 30),
                        lambda: self.schedule(job_id),
                    )
                    timer.daemon = True
                    timer.start()
                    return
                failed += 1
                self.store.update_batch_item(str(item["item_id"]), status="failed", stage=stage, error=error)
                self.store.update_batch_job(job_id, status="paused", current_chapter_id=chapter_id, completed_count=completed, failed_count=failed, error=error)
                self._event(job_id, item, "chapter_failed", stage=stage, status="failed", message=error["message"], error=error)
                self.store.append_batch_event(job_id, event_type="job_paused", status="paused", stage=stage, chapter_id=chapter_id, chapter_title=title, message=f"队列已暂停在《{title}》。", error=error)
                return
        self.store.update_batch_job(job_id, status="succeeded", current_chapter_id="", completed_count=completed, failed_count=failed, error={})
        self.store.append_batch_event(job_id, event_type="job_completed", status="succeeded", message=f"批量编写完成，共 {completed} 章。", data={"completed_count": completed, "failed_count": failed})

    def events(self, job_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        if not self.store.batch_job(job_id):
            raise ControlPlaneError("CHAPTER_BATCH_NOT_FOUND", "批量编写任务不存在。", status_code=404)
        return self.store.batch_events(job_id, after_sequence=after_sequence)

    def action(self, job_id: str, action: str) -> dict[str, Any]:
        job = self.store.batch_job(job_id)
        if not job:
            raise ControlPlaneError("CHAPTER_BATCH_NOT_FOUND", "批量编写任务不存在。", status_code=404)
        action = str(action or "").strip().lower()
        current_id = str(job.get("current_chapter_id") or "")
        current = next((item for item in job.get("items") or [] if str(item.get("chapter_id") or "") == current_id), None)
        if action == "cancel":
            self.store.update_batch_job(job_id, status="cancelled", current_chapter_id="")
            self.store.append_batch_event(job_id, event_type="job_cancelled", status="cancelled", message="批量编写已取消。")
        elif action in {"retry", "skip"}:
            if not current:
                raise ControlPlaneError("CHAPTER_BATCH_ACTION_INVALID", "当前没有可处理的失败章节。", status_code=409)
            if action == "skip":
                self.store.update_batch_item(str(current["item_id"]), status="skipped", stage="skipped")
                self.store.append_batch_event(job_id, event_type="chapter_skipped", status="skipped", stage="skipped", item_id=str(current["item_id"]), chapter_id=current_id, chapter_title=str(current.get("chapter_title") or ""), message="已跳过当前章节。")
            else:
                self.store.update_batch_item(str(current["item_id"]), status="queued", stage="queued", error={})
            failed_count = int(job.get("failed_count") or 0)
            if action == "retry":
                failed_count = max(0, failed_count - 1)
            self.store.update_batch_job(
                job_id,
                status="queued",
                current_chapter_id="",
                failed_count=failed_count,
                error={},
            )
            self.schedule(job_id)
        else:
            raise ControlPlaneError("CHAPTER_BATCH_ACTION_INVALID", f"不支持的任务操作: {action}", status_code=400)
        return self.store.batch_job(job_id) or job

    @classmethod
    def recover(cls, context: WorkspaceContext) -> list[str]:
        service = cls(context)
        scheduled: list[str] = []
        for job in service.store.recover_batch_jobs():
            if str(job.get("status") or "") not in {"queued", "running"}:
                continue
            job_id = str(job.get("job_id") or "")
            if job_id and service.schedule(job_id):
                scheduled.append(job_id)
        return scheduled
