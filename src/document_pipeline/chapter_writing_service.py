"""The single orchestration boundary for chapter writing.

All entry points which want chapter正文 are expected to call
``ChapterWritingService.write``.  This module deliberately contains no HTTP,
chat, batch scheduling, or chapter-specific prompt rules.  It validates the
write request, assembles and freezes one WriterInputBundle, runs the one
research coordinator, invokes ContentWriter's one model boundary, applies the
content gate, and finally asks ChapterEditingService to append a draft
revision.  Formal chapter pointers are never written here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from typing import Any, Callable, Protocol, Sequence

from control_plane import (
    CommandEnvelope,
    CommandGateway,
    ControlPlaneError,
    ControlStore,
    WorkspaceContext,
)
from utils import write_json

from .canonicalization import canonical_hash
from .chapter_editing import ChapterEditingService
from .chapter_workspace import ChapterWorkspaceService
from .content_gate import WriterBundleContentGate
from .contracts import ContentBlock, ContentProposal, WriterInputBundle
from .content_writer import ContentWriter
from .writer_bundle import BUNDLE_DIR, WriterInputBundleAssembler
from .writer_research import WriterResearchCoordinator


class BundleAssembler(Protocol):
    def assemble(self, unit_id: str, node_ids: list[str]) -> WriterInputBundle: ...


class BundleWriter(Protocol):
    def stream_bundle(
        self, bundle: WriterInputBundle, *, operation_id: str = ""
    ) -> Sequence[ContentBlock]: ...


class ResearchRunner(Protocol):
    def resolve_for_bundle(
        self, bundle: WriterInputBundle
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...


@dataclass(frozen=True)
class ChapterWritingRequest:
    """Transport-neutral request accepted by the one writing service."""

    unit_id: str
    node_ids: tuple[str, ...]
    operation_id: str = ""
    operation: str = "create"
    user_instruction: str = ""
    overwrite_locked: bool = False
    chapter_id: str = ""
    expected_workspace_revision: int | None = None
    expected_chapter_revision: int | None = None
    expected_chapter_revisions: dict[str, int] = field(default_factory=dict)
    actor: dict[str, Any] = field(default_factory=dict)
    run_research: bool = True
    commit_drafts: bool = True
    require_outline_review: bool = False

    def validate(self) -> None:
        if not str(self.unit_id).strip():
            raise ControlPlaneError("WRITING_UNIT_REQUIRED", "缺少写作单元。", status_code=400)
        nodes = [str(item).strip() for item in self.node_ids if str(item).strip()]
        if not nodes:
            raise ControlPlaneError("WRITING_NODES_REQUIRED", "缺少章节节点。", status_code=400)
        if len(nodes) != len(set(nodes)):
            raise ControlPlaneError("WRITING_NODES_DUPLICATE", "章节节点不能重复。", status_code=400)
        if self.chapter_id and self.chapter_id not in nodes:
            raise ControlPlaneError(
                "WRITING_CHAPTER_NOT_IN_UNIT",
                "chapter_id 必须属于 node_ids。",
                status_code=400,
            )
        if self.operation not in {"create", "rewrite", "repair"}:
            raise ControlPlaneError("WRITING_OPERATION_INVALID", "写作操作类型无效。", status_code=400)


@dataclass(frozen=True)
class WriterResult:
    """The service result; ``draft_revisions`` are the only workspace writes."""

    bundle: WriterInputBundle
    blocks: tuple[ContentBlock, ...]
    proposal: ContentProposal
    research_decision: dict[str, Any]
    research_evidence: tuple[dict[str, Any], ...]
    draft_revisions: dict[str, dict[str, Any]]


RepairWriter = Callable[
    [WriterInputBundle, list[ContentBlock], Exception],
    Sequence[ContentBlock] | tuple[WriterInputBundle, Sequence[ContentBlock]],
]
AuthorizeWrite = Callable[[ChapterWritingRequest], bool | None]
DraftCommitter = Callable[
    [str, int, str, dict[str, Any], tuple[str, int, str] | None, tuple[str, int, str] | None, list[str], bool],
    dict[str, Any],
]


class ChapterWritingService:
    """唯一公共写作入口和确定性编排服务。"""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        assembler: BundleAssembler | None = None,
        writer: BundleWriter | None = None,
        research: ResearchRunner | None = None,
        quality_gate: WriterBundleContentGate | None = None,
        repair_writer: RepairWriter | None = None,
        authorize: AuthorizeWrite | None = None,
        draft_committer: DraftCommitter | None = None,
        command_gateway: CommandGateway | None = None,
        deterministic_test: bool = False,
    ) -> None:
        self.context = context
        self.store = ControlStore(context)
        self.deterministic_test = bool(deterministic_test)
        self.assembler = assembler or WriterInputBundleAssembler(
            context, deterministic_test=self.deterministic_test
        )
        self.writer = writer or (
            ContentWriter.for_deterministic_tests(context)
            if self.deterministic_test
            else ContentWriter(context)
        )
        self.research = research or WriterResearchCoordinator(
            context, deterministic_test=self.deterministic_test
        )
        self.quality_gate = quality_gate or WriterBundleContentGate()
        self.repair_writer = repair_writer or self._repair_with_writer
        self.authorize = authorize
        self.command_gateway = command_gateway or CommandGateway(
            context,
            {"chapter.generate_draft": ChapterEditingService(context).handle_generate_draft},
        )
        self.draft_committer = draft_committer or self._commit_draft_command

    def write(self, request: ChapterWritingRequest) -> WriterResult:
        """Execute one complete chapter write transaction up to Draft Revision."""
        request.validate()
        self._authorize(request)
        self._validate_chapter_versions(request)

        node_ids = [str(item).strip() for item in request.node_ids if str(item).strip()]
        bundle = self.assembler.assemble(str(request.unit_id), node_ids)
        bundle = self._apply_request_metadata(bundle, request)
        self._validate_outline_authority(request)
        decision: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        if request.run_research:
            decision, evidence = self.research.resolve_for_bundle(bundle)
            bundle = self._freeze_research_bundle(
                bundle, evidence=evidence, decision=decision
            )

        raw_blocks = self.writer.stream_bundle(
            bundle, operation_id=str(request.operation_id or request.unit_id)
        )
        blocks = [item if isinstance(item, ContentBlock) else ContentBlock.model_validate(item) for item in raw_blocks]
        proposal, blocks, bundle = self._quality_gate(bundle, blocks)
        revisions = (
            self._commit_drafts(request, bundle, blocks, evidence)
            if request.commit_drafts
            else {}
        )
        return WriterResult(
            bundle=bundle,
            blocks=tuple(blocks),
            proposal=proposal,
            research_decision=decision,
            research_evidence=tuple(evidence),
            draft_revisions=revisions,
        )

    def stream(self, request: ChapterWritingRequest) -> WriterResult:
        """Synchronous compatibility name used by HTTP and batch adapters."""
        return self.write(request)

    def iter_events(self, request: ChapterWritingRequest):
        """Stream the real write checkpoints in the order the user reviews them."""
        yield {
            "type": "meta",
            "unit_id": request.unit_id,
            "operation_id": request.operation_id,
            "chapter_id": request.chapter_id,
        }
        request.validate()
        self._authorize(request)
        self._validate_chapter_versions(request)

        node_ids = [str(item).strip() for item in request.node_ids if str(item).strip()]
        bundle = self.assembler.assemble(str(request.unit_id), node_ids)
        bundle = self._apply_request_metadata(bundle, request)
        self._validate_outline_authority(request)

        decision: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        if request.run_research:
            yield {
                "type": "thinking_step",
                "step": "research_decision",
                "chapter_id": request.chapter_id,
                "message": "资料查询判断：正在确认是否需要补充公开资料。",
            }
            plan_research = getattr(self.research, "plan_for_bundle", None)
            execute_research = getattr(self.research, "execute_plan", None)
            if callable(plan_research) and callable(execute_research):
                research_plan = plan_research(bundle)
                planned = research_plan.model_dump(mode="json")
                needs_research = bool(planned.get("needs_research"))
                reason = str(planned.get("reason") or "").strip()
                yield {
                    "type": "research",
                    "unit_id": bundle.unit_id,
                    "chapter_id": request.chapter_id,
                    "status": "required" if needs_research else "not_required",
                    "message": (
                        f"需要查询公开资料：{reason or '现有资料存在写作缺口。'}"
                        if needs_research
                        else f"无需查询公开资料：{reason or '现有资料足以支撑本章写作。'}"
                    ),
                    "sources": [],
                }
                if needs_research:
                    queries = [
                        str(item.get("question") or "").strip()
                        for item in planned.get("queries") or []
                        if isinstance(item, dict) and str(item.get("question") or "").strip()
                    ]
                    yield {
                        "type": "research",
                        "unit_id": bundle.unit_id,
                        "chapter_id": request.chapter_id,
                        "status": "searching",
                        "message": "已开始搜索公开资料" + (f"：{queries[0]}" if queries else "。"),
                        "sources": [],
                    }
                decision, evidence = execute_research(bundle, research_plan)
            else:
                decision, evidence = self.research.resolve_for_bundle(bundle)
            bundle = self._freeze_research_bundle(
                bundle, evidence=evidence, decision=decision
            )
        else:
            yield {
                "type": "thinking_step",
                "step": "research_decision",
                "chapter_id": request.chapter_id,
                "message": "资料查询判断：已按用户确认跳过公开资料查询，使用现有资料继续。",
            }

        if decision:
            source_rows = [
                source
                for item in evidence
                if isinstance(item, dict)
                for source in item.get("sources") or []
                if isinstance(source, dict)
            ]
            decision_status = str(decision.get("decision_status") or "")
            if decision_status == "published":
                result_message = f"搜索完成：已获得 {len(source_rows)} 条可用公开来源，开始用于正文写作。"
            elif not decision.get("needs_research") or decision_status == "skipped":
                result_message = "查询结论：无需搜索公开资料，使用现有项目资料继续写作。"
            else:
                result_message = str(decision.get("reason") or "资料查询处理完成。")
            yield {
                "type": "research",
                "unit_id": bundle.unit_id,
                "chapter_id": request.chapter_id,
                "status": decision_status,
                "message": result_message,
                "sources": source_rows,
            }
        yield {
            "type": "thinking_step",
            "step": "drafting",
            "chapter_id": request.chapter_id,
            "message": "开始撰写：正在按已确认提纲生成正文，并实时写入中间文档。",
        }

        streamed_blocks: list[ContentBlock] = []
        for item in self.writer.stream_bundle(
            bundle, operation_id=str(request.operation_id or request.unit_id)
        ):
            block = item if isinstance(item, ContentBlock) else ContentBlock.model_validate(item)
            streamed_blocks.append(block)
            yield {
                "type": "content_delta",
                "unit_id": bundle.unit_id,
                "chapter_id": block.target_node_id,
                "delta": block.content,
                "block": block.model_dump(mode="json"),
            }

        _proposal, blocks, bundle = self._quality_gate(bundle, streamed_blocks)
        revisions = (
            self._commit_drafts(request, bundle, blocks, evidence)
            if request.commit_drafts
            else {}
        )
        committed = dict(revisions.get(request.chapter_id) or {})
        yield {
            "type": "done",
            "unit_id": bundle.unit_id,
            "chapter_id": request.chapter_id,
            "bundle_id": bundle.bundle_id,
            "bundle_hash": bundle.bundle_hash,
            "draft_revisions": revisions,
            "chapter": committed.get("chapter"),
            "content": committed.get("content"),
        }

    def _authorize(self, request: ChapterWritingRequest) -> None:
        actor = request.actor if isinstance(request.actor, dict) else {}
        principal_id = str(actor.get("id") or "").strip()
        if not self.deterministic_test and not principal_id:
            raise ControlPlaneError(
                "CHAPTER_WRITE_AUTH_REQUIRED",
                "章节写作必须携带已认证操作者。",
                status_code=403,
            )
        if (
            not self.deterministic_test
            and str(actor.get("type") or "user") == "user"
        ):
            self.store.require_workspace_access(principal_id, write=True)
        elif (
            not self.deterministic_test
            and str(actor.get("role") or "") != "chapter-batch-worker"
        ):
            raise ControlPlaneError(
                "CHAPTER_WRITE_FORBIDDEN",
                "未授权的系统身份不得执行章节写作。",
                status_code=403,
            )
        if self.authorize is not None and self.authorize(request) is False:
            raise ControlPlaneError(
                "CHAPTER_WRITE_FORBIDDEN", "当前操作者没有章节写作权限。", status_code=403
            )

    def _validate_chapter_versions(self, request: ChapterWritingRequest) -> None:
        if request.expected_workspace_revision is not None:
            actual_workspace_revision = int(self.store.revision())
            if int(request.expected_workspace_revision) != actual_workspace_revision:
                raise ControlPlaneError(
                    "WORKSPACE_REVISION_CONFLICT",
                    "工作空间已发生变化，请刷新后重试。",
                    status_code=409,
                    details={
                        "expected": int(request.expected_workspace_revision),
                        "actual": actual_workspace_revision,
                    },
                )
        chapter_ids = [request.chapter_id] if request.chapter_id else list(request.node_ids)
        for chapter_id in chapter_ids:
            chapter_id = str(chapter_id or "").strip()
            if not chapter_id:
                continue
            workspace = self.store.chapter_workspace(chapter_id)
            if workspace is None:
                continue
            ChapterWorkspaceService(self.context).require_leaf_chapter(chapter_id)
            expected = request.expected_chapter_revisions.get(chapter_id)
            if expected is None and chapter_id == request.chapter_id:
                expected = request.expected_chapter_revision
            if expected is None:
                raise ControlPlaneError(
                    "CHAPTER_REVISION_REQUIRED",
                    f"章节 {chapter_id} 缺少 expected_chapter_revision。",
                    status_code=409,
                )
            actual = int(workspace.get("chapter_revision") or 0)
            if int(expected) != actual:
                raise ControlPlaneError(
                    "CHAPTER_REVISION_CONFLICT",
                    f"章节 {chapter_id} 已发生变化，请刷新后重试。",
                    status_code=409,
                    details={"expected": int(expected), "actual": actual},
                )

    def _freeze_research_bundle(
        self,
        bundle: WriterInputBundle,
        *,
        evidence: list[dict[str, Any]],
        decision: dict[str, Any],
    ) -> WriterInputBundle:
        """Create the immutable post-research WriterBundle consumed by Writer."""
        body = bundle.model_dump(
            mode="json",
            exclude={"revision", "source_hashes", "bundle_id", "bundle_hash"},
        )
        body["evidence_snapshot"] = [*bundle.evidence_snapshot, *evidence]
        body["research_decisions"] = [*bundle.research_decisions, decision] if decision else list(bundle.research_decisions)
        source_hashes = dict(bundle.source_hashes)
        for item in evidence:
            if isinstance(item, dict) and item.get("batch_id"):
                source_hashes[f"evidence:{item['batch_id']}"] = canonical_hash(item)
        bundle_hash = canonical_hash(body)
        frozen = WriterInputBundle(
            revision=bundle.revision,
            source_hashes=source_hashes,
            bundle_id=f"{bundle.bundle_id}-r{bundle_hash[:8]}",
            bundle_hash=bundle_hash,
            **body,
        )
        write_json(
            self.context.root / BUNDLE_DIR / f"{frozen.bundle_id}.json",
            frozen.model_dump(mode="json"),
        )
        return frozen

    def _validate_outline_authority(
        self,
        request: ChapterWritingRequest,
    ) -> None:
        if not request.require_outline_review or not request.chapter_id:
            return
        from .chapter_chat import ChapterChatService
        from .chapter_semantics import (
            load_chapter_project_context,
            project_chapter_semantic_requirements,
        )

        chapter = ChapterWorkspaceService(self.context).get_chapter(
            request.chapter_id
        )
        requirements, scoring = project_chapter_semantic_requirements(
            self.context,
            chapter,
        )
        chat_service = ChapterChatService(self.context)
        chat_context = chat_service.build_chapter_chat_context(
            chapter,
            global_project_context=load_chapter_project_context(self.context),
            tender_requirements=requirements,
            scoring_requirements=scoring,
        )
        authority = chat_service.require_write_ready(
            request.chapter_id,
            outline=chat_context.get("writing_outline"),
        )
        if not authority.get("ready"):
            raise ControlPlaneError(
                "CHAPTER_OUTLINE_REVIEW_REQUIRED",
                str(authority.get("reason") or "请先确认本章写作提纲。"),
                status_code=409,
                details={"authority": authority},
            )

    def _quality_gate(
        self, bundle: WriterInputBundle, blocks: list[ContentBlock]
    ) -> tuple[ContentProposal, list[ContentBlock], WriterInputBundle]:
        try:
            return self.quality_gate.validate(bundle, blocks), blocks, bundle
        except Exception as exc:
            repair_result = self.repair_writer(bundle, blocks, exc)
            repair_bundle = bundle
            repaired_source: Sequence[ContentBlock]
            if (
                isinstance(repair_result, tuple)
                and len(repair_result) == 2
                and isinstance(repair_result[0], WriterInputBundle)
            ):
                repair_bundle = repair_result[0]
                repaired_source = repair_result[1]
            else:
                repaired_source = repair_result
            repaired = list(repaired_source)
            return (
                self.quality_gate.validate(repair_bundle, repaired),
                repaired,
                repair_bundle,
            )

    def _repair_with_writer(
        self,
        bundle: WriterInputBundle,
        blocks: list[ContentBlock],
        error: Exception,
    ) -> tuple[WriterInputBundle, Sequence[ContentBlock]]:
        """Run one repair pass through the same ContentWriter kernel."""
        body = bundle.model_dump(
            mode="json",
            exclude={"revision", "source_hashes", "bundle_id", "bundle_hash"},
        )
        body.update(
            {
                "operation": "repair",
                "existing_content": "\n\n".join(
                    str(block.content) for block in blocks
                ).strip(),
                "user_instruction": "\n".join(
                    item
                    for item in (
                        str(bundle.user_instruction or "").strip(),
                        f"修复质量校验问题：{str(error)}",
                    )
                    if item
                ),
            }
        )
        bundle_hash = canonical_hash(body)
        repair_bundle = bundle.__class__(
            revision=bundle.revision,
            source_hashes=dict(bundle.source_hashes),
            bundle_id=f"{bundle.unit_id}-repair-{bundle_hash[:12]}",
            bundle_hash=bundle_hash,
            **body,
        )
        write_json(
            self.context.root / BUNDLE_DIR / f"{repair_bundle.bundle_id}.json",
            repair_bundle.model_dump(mode="json"),
        )
        return (
            repair_bundle,
            self.writer.stream_bundle(
                repair_bundle,
                operation_id=f"repair:{bundle.unit_id}",
            ),
        )

    def _commit_drafts(
        self,
        request: ChapterWritingRequest,
        bundle: WriterInputBundle,
        blocks: list[ContentBlock],
        evidence: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        revisions: dict[str, dict[str, Any]] = {}
        targets = [
            item
            for item in bundle.document_target_constraints
            if isinstance(item, dict) and str(item.get("output_target") or "").strip()
        ]
        for target in targets:
            chapter_id = str(target.get("output_target") or target.get("node_id") or "").strip()
            if not chapter_id or self.store.chapter_workspace(chapter_id) is None:
                continue
            expected = request.expected_chapter_revisions.get(chapter_id)
            if expected is None and chapter_id == request.chapter_id:
                expected = request.expected_chapter_revision
            if expected is None:
                raise ControlPlaneError(
                    "CHAPTER_REVISION_REQUIRED",
                    f"章节 {chapter_id} 缺少 expected_chapter_revision。",
                    status_code=409,
                )
            chapter_context = dict(
                (bundle.chapter_grounding_contexts or {}).get(chapter_id)
                or bundle.chapter_grounding_context
                or {}
            )
            global_ref = self._context_ref(chapter_context, "global_context")
            chapter_ref = self._context_ref(chapter_context, "chapter_context")
            evidence_batches = [
                str(item.get("batch_id"))
                for item in [*bundle.evidence_snapshot, *evidence]
                if isinstance(item, dict) and str(item.get("batch_id") or "").strip()
            ]
            evidence_batches = list(dict.fromkeys(evidence_batches))
            text = "\n\n".join(
                str(block.content)
                for block in blocks
                if str(block.target_node_id) == chapter_id
            ).strip()
            if not text:
                continue
            revisions[chapter_id] = self.draft_committer(
                chapter_id,
                int(expected),
                text,
                request.actor,
                global_ref,
                chapter_ref,
                evidence_batches,
                bool(request.overwrite_locked),
            )
        return revisions

    def _commit_draft_command(
        self,
        chapter_id: str,
        expected_chapter_revision: int,
        text: str,
        actor: dict[str, Any],
        global_ref: tuple[str, int, str] | None,
        chapter_ref: tuple[str, int, str] | None,
        evidence_batch_ids: list[str],
        overwrite_locked: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chapter_id": chapter_id,
            "expected_chapter_revision": expected_chapter_revision,
            "text": text,
            "overwrite_locked": bool(overwrite_locked),
            "evidence_batch_ids": evidence_batch_ids,
        }
        if global_ref is not None:
            payload.update(
                {
                    "global_context_id": global_ref[0],
                    "global_context_revision": global_ref[1],
                    "global_context_hash": global_ref[2],
                }
            )
        if chapter_ref is not None:
            payload.update(
                {
                    "chapter_context_id": chapter_ref[0],
                    "chapter_context_revision": chapter_ref[1],
                    "chapter_context_hash": chapter_ref[2],
                }
            )
        command_id = str(uuid.uuid4())
        envelope = CommandEnvelope(
            command_id=command_id,
            workspace_id=self.context.workspace_id,
            kind="chapter.generate_draft",
            payload=payload,
            goal_id=None,
            actor=actor or {"type": "system", "id": "chapter-writing-service"},
            expected_revision=self.store.revision(),
            idempotency_key=f"chapter-draft:{chapter_id}:{expected_chapter_revision}:{canonical_hash(text)}",
        )
        receipt = self.command_gateway.submit(envelope)
        if receipt.status == "rejected":
            error = receipt.error if isinstance(receipt.error, dict) else {}
            raise ControlPlaneError(
                str(error.get("code") or "CHAPTER_DRAFT_COMMIT_REJECTED"),
                str(error.get("message") or receipt.message or "章节草稿提交失败。"),
                status_code=409,
                details=error,
            )
        return dict(receipt.result or {})

    def _apply_request_metadata(
        self, bundle: WriterInputBundle, request: ChapterWritingRequest
    ) -> WriterInputBundle:
        body = bundle.model_dump(
            mode="json",
            exclude={"revision", "source_hashes", "bundle_id", "bundle_hash"},
        )
        body.update(
            {
                "operation": request.operation,
                "user_instruction": request.user_instruction,
                "overwrite_locked": bool(request.overwrite_locked),
            }
        )
        bundle_hash = canonical_hash(body)
        frozen = bundle.__class__(
            revision=bundle.revision,
            source_hashes=dict(bundle.source_hashes),
            bundle_id=f"{bundle.unit_id}-{bundle_hash[:16]}",
            bundle_hash=bundle_hash,
            **body,
        )
        write_json(
            self.context.root / BUNDLE_DIR / f"{frozen.bundle_id}.json",
            frozen.model_dump(mode="json"),
        )
        return frozen

    @staticmethod
    def _context_ref(context: dict[str, Any], prefix: str) -> tuple[str, int, str] | None:
        ident = str(context.get(f"{prefix}_id") or "").strip()
        digest = str(context.get(f"{prefix}_hash") or "").strip()
        if not ident or not digest:
            return None
        try:
            revision = int(context.get(f"{prefix}_revision") or 0)
        except (TypeError, ValueError):
            return None
        return ident, revision, digest


__all__ = [
    "ChapterWritingRequest",
    "ChapterWritingService",
    "WriterResult",
]
