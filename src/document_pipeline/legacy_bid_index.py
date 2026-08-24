from __future__ import annotations

import hashlib
from pathlib import Path

from control_plane import ControlPlaneError, ControlStore, WorkspaceContext

from .contracts import (
    SOURCE_PARSER_VERSION,
    InputItem,
    InputRole,
    LegacyBidIndex,
    LegacyBidSection,
    LegacyBidSource,
)
from .source_artifacts import promote_source_artifact
from .source_normalizer import SourceNormalizer


class LegacyBidIndexService:
    """Build the isolated, previewable structure for one frozen old bid."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.store = ControlStore(context)

    def build(self, source: LegacyBidSource) -> LegacyBidIndex:
        manifest_artifact = self.store.v3_active_artifact("LegacyBidSourceManifest")
        if manifest_artifact is None:
            raise ControlPlaneError(
                "LEGACY_BID_SOURCE_MISSING",
                "旧投标书来源清单尚未晋级。",
                status_code=409,
            )
        path = self.context.root / Path(source.stored_path)
        if not path.is_file():
            raise ControlPlaneError(
                "LEGACY_BID_FILE_MISSING",
                "旧投标书原文件不存在。",
                status_code=409,
            )
        item = InputItem(
            input_id=source.legacy_bid_id,
            role=InputRole.LEGACY_BID,
            filename=source.filename,
            mime_type=source.mime_type,
            sha256=source.sha256,
            version=source.version,
        )
        blocks, coverage = SourceNormalizer(self.context).parse_frozen_file(item, path)
        sections, review = self._sections(blocks)
        gaps = [item for item in coverage if item.status == "structure_gap"]
        if gaps:
            review.append(f"{len(gaps)} 个结构缺口需要复核")
        active = self.store.v3_active_artifact("LegacyBidIndex")
        revision = int(active["revision"]) + 1 if active else 1
        index = LegacyBidIndex(
            revision=revision,
            source_hashes={source.legacy_bid_id: source.sha256},
            legacy_bid_id=source.legacy_bid_id,
            filename=source.filename,
            file_hash=source.sha256,
            parser_version=SOURCE_PARSER_VERSION,
            source_manifest_revision=int(manifest_artifact["revision"]),
            source_manifest_artifact_hash=str(manifest_artifact["artifact_hash"]),
            sections=sections,
            blocks=blocks,
            structure_gaps=gaps,
            needs_review=review,
        )
        promote_source_artifact(
            self.context,
            artifact_kind="LegacyBidIndex",
            payload=index.model_dump(mode="json"),
            operation_id=(
                f"legacy-bid-index:{source.legacy_bid_id}:"
                f"{manifest_artifact['revision']}:{SOURCE_PARSER_VERSION}:{revision}"
            ),
            gate_id="G0_LEGACY_BID_STRUCTURE",
        )
        promoted = self.store.v3_active_artifact("LegacyBidIndex")
        assert promoted is not None
        return LegacyBidIndex.model_validate(promoted["payload"])

    @staticmethod
    def _sections(blocks):
        sections: list[LegacyBidSection] = []
        stack: list[tuple[int, int]] = []
        current_index: int | None = None
        review: list[str] = []
        unsectioned = 0
        for block in blocks:
            if block.block_kind == "heading":
                level = max(1, len(block.heading_path))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                if stack and level > stack[-1][0] + 1:
                    review.append(f"标题跳级：{block.content}")
                parent_id = sections[stack[-1][1]].section_id if stack else None
                section_id = hashlib.sha256(
                    f"{block.block_id}:section:{level}".encode("utf-8")
                ).hexdigest()[:24]
                sections.append(
                    LegacyBidSection(
                        section_id=section_id,
                        parent_section_id=parent_id,
                        level=level,
                        order=len(sections),
                        title=block.content,
                        heading_block_id=block.block_id,
                        content_block_ids=[],
                        start_ordinal=block.ordinal,
                        end_ordinal=block.ordinal,
                        needs_review=bool(stack and level > stack[-1][0] + 1),
                    )
                )
                current_index = len(sections) - 1
                stack.append((level, current_index))
            elif current_index is None:
                unsectioned += 1
            else:
                section = sections[current_index]
                sections[current_index] = section.model_copy(
                    update={
                        "content_block_ids": [*section.content_block_ids, block.block_id],
                        "end_ordinal": block.ordinal,
                    }
                )
        if unsectioned:
            review.append(f"{unsectioned} 个段落位于首个标题之前")
        if blocks and not sections:
            review.append("未识别到标题层级")
        return sections, review
