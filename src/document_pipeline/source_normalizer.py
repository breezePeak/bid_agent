from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from control_plane import WorkspaceContext
from document_converter import convert_to_markdown
from utils import write_json

from .contracts import InputRole, NormalizedChunk, SourceAnchor
from .input_manifest import InputManifestService, V3_ROOT


SOURCE_INDEX_PATH = V3_ROOT / "source_index.json"


class SourceNormalizer:
    """Normalize frozen V3 inputs without mixing company and reference evidence."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root
        self.inputs = InputManifestService(context)

    def normalize_active_inputs(self) -> dict[str, object]:
        manifest = self.inputs.load()
        by_role: dict[str, list[dict[str, object]]] = defaultdict(list)
        for item in manifest.inputs:
            if not item.active:
                continue
            source = self.root / V3_ROOT / "sources" / item.input_id / item.filename
            for chunk in self._chunks_for(item.input_id, item.role, source):
                by_role[item.role.value].append(chunk.model_dump(mode="json"))
        index: dict[str, object] = {
            "schema_version": "v3",
            "revision": manifest.revision,
            "source_hashes": manifest.source_hashes,
            "by_role": dict(by_role),
        }
        write_json(self.root / SOURCE_INDEX_PATH, index)
        return index

    @staticmethod
    def _markdown(source: Path) -> str:
        if source.suffix.lower() in {".md", ".txt"}:
            return source.read_text(encoding="utf-8")
        return convert_to_markdown(source)

    def _chunks_for(self, input_id: str, role: InputRole, source: Path) -> list[NormalizedChunk]:
        markdown = self._markdown(source).replace("\r\n", "\n")
        paragraphs = [part.strip() for part in markdown.split("\n\n") if part.strip()]
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return [
            NormalizedChunk(
                chunk_id=hashlib.sha256(f"{input_id}:{ordinal}:{digest}".encode("utf-8")).hexdigest()[:24],
                input_id=input_id,
                role=role,
                ordinal=ordinal,
                content=paragraph,
                source_anchor=SourceAnchor(
                    source_input_id=input_id,
                    chunk_id=hashlib.sha256(f"{input_id}:{ordinal}:{digest}".encode("utf-8")).hexdigest()[:24],
                    location=f"paragraph:{ordinal + 1}",
                ),
            )
            for ordinal, paragraph in enumerate(paragraphs)
        ]
