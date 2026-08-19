"""Word export for the current chapter workbench state."""

from __future__ import annotations

from pathlib import Path

from control_plane import WorkspaceContext

from .chapter_editing import ChapterEditingService


def build_current_word(context: WorkspaceContext) -> Path:
    """Write a downloadable Word draft that keeps the on-page preview styles."""
    try:
        from .renderers.word_styles import write_composed_document
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 Word 导出依赖 python-docx。") from exc

    composed = ChapterEditingService(context).compose_current_document()
    output_dir = context.root / "workspace" / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)
    return write_composed_document(composed, output_dir / "current.docx")
