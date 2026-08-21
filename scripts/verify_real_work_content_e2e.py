"""Real regression for project-bound 《工作内容》 writing."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api import v3_app  # noqa: E402
from document_pipeline.chapter_workspace import ChapterWorkspaceService  # noqa: E402


def main() -> int:
    workspace_id = next(
        path.name
        for path in sorted((ROOT / "runs").iterdir(), key=lambda item: item.name, reverse=True)
        if path.is_dir() and path.name.startswith("2026")
    )
    context = v3_app._context(workspace_id)
    chapters = ChapterWorkspaceService(context).list_chapters(include_archived=False)["items"]
    chapter = next(
        item
        for item in chapters
        if item.get("is_leaf") and str(item.get("title") or "").strip() == "工作内容"
    )
    chapter_id = str(chapter["chapter_id"])
    before = int(chapter.get("head_content_revision") or 0)
    runtime = v3_app._chapter_chat_runtime(workspace_id, chapter_id)
    events = list(
        runtime["service"].iter_answer_events(
            chapter_id,
            "开始编写本章正文",
            chapter=runtime["chapter"],
            global_project_context=runtime["global_project_context"],
            tender_requirements=runtime["requirements"],
            scoring_requirements=runtime["scoring"],
            sibling_context=runtime["sibling_context"],
            outline_context=runtime["outline_context"],
            writing_orientation=runtime["writing_orientation"],
            actor={
                "id": "real-work-content-e2e",
                "type": "system",
                "role": "chapter-batch-worker",
            },
        )
    )
    done = next(item for item in reversed(events) if item.get("type") == "done")
    after_chapter = ChapterWorkspaceService(context).get_chapter(chapter_id)
    after = int(after_chapter.get("head_content_revision") or 0)
    revision = after_chapter.get("content") if isinstance(after_chapter.get("content"), dict) else {}
    content = "\n\n".join(
        str(item.get("content") or "").strip()
        for item in revision.get("blocks") or []
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    )
    research = [
        {"status": item.get("status"), "message": item.get("message")}
        for item in events
        if item.get("type") == "research"
    ]
    required_terms = ["准备工作", "质量控制", "成果复核", "项目支撑"]
    failures = []
    if after <= before:
        failures.append("Draft revision was not created")
    if not done.get("document_write_completed"):
        failures.append("document_write_completed is false")
    if any(item.get("status") in {"required", "searching", "fallback"} for item in research):
        failures.append("project-bound work content entered public-search/fallback path")
    missing = [term for term in required_terms if term not in content]
    if missing:
        failures.append("missing work packages: " + ", ".join(missing))
    result = {
        "workspace": workspace_id,
        "chapter_id": chapter_id,
        "chapter_title": chapter.get("title"),
        "input": "开始编写本章正文",
        "draft_revision_before": before,
        "draft_revision_after": after,
        "research": research,
        "writing_phases": [
            item.get("write_phase") for item in events if item.get("type") == "writing_phase"
        ],
        "content": content,
        "failures": failures,
        "real_e2e": "PASS" if not failures else "FAIL",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
