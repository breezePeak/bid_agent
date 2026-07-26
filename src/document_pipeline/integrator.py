from __future__ import annotations

import hashlib
from pathlib import Path

from control_plane import ControlStore, WorkspaceContext
from utils import read_json, write_json

from .contracts import ContentBlock, IntegratedDocument
from .input_manifest import V3_ROOT


INTEGRATED_DOCUMENT_PATH = V3_ROOT / "integrated_document.json"
REWRITE_TRACE_PATH = V3_ROOT / "reports" / "rewrite_trace.json"


class DocumentIntegrator:
    """Perform deterministic in-unit, adjacent, and document-wide de-duplication."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root
        self.store = ControlStore(context)

    def integrate(self, *, contract_revision: int, plan_revision: int) -> IntegratedDocument:
        locked = {item["block_id"] for item in self.store.content_locks()}
        blocks = self._load_blocks()
        kept: list[ContentBlock] = []
        trace: list[dict[str, str]] = []
        seen_requirement: dict[str, ContentBlock] = {}
        seen_content: dict[str, ContentBlock] = {}
        for block in blocks:
            block = block.model_copy(update={"human_locked": block.human_locked or block.block_id in locked})
            duplicate_of = self._duplicate_of(block, seen_requirement, seen_content)
            if duplicate_of and not block.human_locked:
                trace.append({"action": "delete_duplicate", "block_id": block.block_id, "kept_block_id": duplicate_of.block_id})
                continue
            if duplicate_of and block.human_locked and not duplicate_of.human_locked:
                kept.remove(duplicate_of)
                trace.append({"action": "replace_with_locked", "block_id": duplicate_of.block_id, "kept_block_id": block.block_id})
            kept.append(block)
            for requirement_id in block.requirement_ids:
                seen_requirement[requirement_id] = block
            seen_content[self._content_key(block)] = block
        document = IntegratedDocument(
            revision=max(contract_revision, plan_revision),
            source_hashes={"content_blocks": hashlib.sha256("|".join(block.block_id for block in kept).encode("utf-8")).hexdigest()},
            contract_revision=contract_revision,
            plan_revision=plan_revision,
            blocks=kept,
        )
        write_json(self.root / INTEGRATED_DOCUMENT_PATH, document.model_dump(mode="json"))
        write_json(self.root / REWRITE_TRACE_PATH, {"schema_version": "v3", "revision": document.revision, "actions": trace})
        return document

    def _load_blocks(self) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for path in sorted((self.root / V3_ROOT / "content_units").glob("*.json")):
            if path.name == "index.json":
                continue
            data = read_json(path)
            rows = data.get("blocks") if isinstance(data, dict) else []
            blocks.extend(ContentBlock.model_validate(item) for item in rows if isinstance(item, dict))
        return blocks

    @staticmethod
    def _content_key(block: ContentBlock) -> str:
        return " ".join(block.content.split()).lower()

    def _duplicate_of(self, block: ContentBlock, by_requirement: dict[str, ContentBlock], by_content: dict[str, ContentBlock]) -> ContentBlock | None:
        for requirement_id in block.requirement_ids:
            if requirement_id in by_requirement:
                return by_requirement[requirement_id]
        return by_content.get(self._content_key(block))
