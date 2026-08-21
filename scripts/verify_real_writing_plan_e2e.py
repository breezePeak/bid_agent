"""Run the required real Chapter Agent -> ChapterWritingService acceptance flow."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api import v3_app  # noqa: E402
from control_plane import ControlPlaneError  # noqa: E402


def _run_turn(runtime: dict[str, Any], chapter_id: str, message: str) -> dict[str, Any]:
    try:
        events = list(
            runtime["service"].iter_answer_events(
            chapter_id,
            message,
            chapter=runtime["chapter"],
            global_project_context=runtime["global_project_context"],
            tender_requirements=runtime["requirements"],
            scoring_requirements=runtime["scoring"],
            sibling_context=runtime["sibling_context"],
            outline_context=runtime["outline_context"],
            writing_orientation=runtime["writing_orientation"],
            actor={
                "id": "real-writing-plan-e2e",
                "type": "system",
                "role": "chapter-batch-worker",
            },
            )
        )
    except ControlPlaneError as exc:
        print(
            json.dumps(
                {"code": exc.code, "message": exc.message, "details": exc.details},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            flush=True,
        )
        raise
    done = next((event for event in reversed(events) if event.get("type") == "done"), None)
    if not done:
        raise RuntimeError(f"turn did not complete: {message}")
    return {
        "input": message,
        "write_phases": [
            event.get("write_phase")
            for event in events
            if event.get("type") == "writing_phase"
        ],
        "reply": done.get("reply"),
        "write_completed": bool(done.get("document_write_completed")),
        "chapter": done.get("chapter"),
        "content": done.get("content"),
    }


def main() -> int:
    workspace_ids = sorted(
        path.name
        for path in (ROOT / "runs").iterdir()
        if path.is_dir() and path.name.startswith("2026年度")
    )
    if not workspace_ids:
        raise RuntimeError("real workspace not found")
    workspace_id = workspace_ids[-1]
    context = v3_app._context(workspace_id)
    from document_pipeline.chapter_workspace import ChapterWorkspaceService

    items = ChapterWorkspaceService(context).list_chapters(include_archived=False)["items"]
    leaves = [item for item in items if item.get("is_leaf") and item.get("materialized")]
    if not leaves:
        raise RuntimeError("no materialized leaf chapter found")
    previously_written = {
        "chapter-4be95857ff1a3032",
        "chapter-7a80904d90cbe818",
        "chapter-92a2e832db3f31ea",
    }
    eligible = [item for item in leaves if item.get("chapter_id") in previously_written] or leaves
    if "--list" in sys.argv:
        print(
            json.dumps(
                [
                    {
                        "chapter_id": item.get("chapter_id"),
                        "title": item.get("title"),
                        "purpose": (item.get("blueprint_node") or {}).get("purpose"),
                        "writing_objectives": (item.get("blueprint_node") or {}).get("writing_objectives"),
                    }
                    for item in leaves
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    chapter = next(
        (item for item in eligible if str(item.get("title") or "") == "工作目标"),
        None,
    ) or next(
        (
            item
            for item in eligible
            if any(marker in str(item.get("title") or "") for marker in ("工作目标", "目标", "任务"))
        ),
        eligible[0],
    )
    chapter_id = str(chapter["chapter_id"])
    if "--inspect" in sys.argv:
        runtime = v3_app._chapter_chat_runtime(workspace_id, chapter_id)
        chat_context = runtime["service"].build_chapter_chat_context(
            runtime["chapter"],
            global_project_context=runtime["global_project_context"],
            tender_requirements=runtime["requirements"],
            scoring_requirements=runtime["scoring"],
            sibling_context=runtime["sibling_context"],
            outline_context=runtime["outline_context"],
            writing_orientation=runtime["writing_orientation"],
        )
        print(
            json.dumps(
                {
                    "chapter": runtime["chapter"],
                    "writing_plan": chat_context.get("writing_outline"),
                    "scope": chat_context.get("chapter_scope"),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0
    print(
        json.dumps(
            {
                "workspace": workspace_id,
                "chapter_id": chapter_id,
                "chapter_title": chapter.get("title"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    final_only = "--final-only" in sys.argv
    if not final_only:
        initial_runtime = v3_app._chapter_chat_runtime(workspace_id, chapter_id)
        initial_runtime["service"].clear_history(chapter_id)
        initial_runtime["service"].reset_writing_plan(chapter_id)

    turns: list[dict[str, Any]] = []
    messages = (
        ("按这个写",)
        if final_only
        else (
            "开始编写本章正文",
            "写得太空了，把两项任务分别写清楚，再把实施边界写具体。",
            "先看看这一章怎么写",
            "第二点再具体一点",
            "按这个写",
        )
    )
    for message in messages:
        print(json.dumps({"running": message}, ensure_ascii=False), flush=True)
        runtime = v3_app._chapter_chat_runtime(workspace_id, chapter_id)
        turns.append(_run_turn(runtime, chapter_id, message))

    failures = []
    write_indexes = (0,) if final_only else (0, 1, 4)
    for index in write_indexes:
        if turns[index]["write_phases"] != ["write_body"]:
            failures.append(f"turn {index + 1} did not route directly to write_body")
        if not turns[index]["write_completed"]:
            failures.append(f"turn {index + 1} did not create a Draft revision")
    if not final_only:
        for index in (2, 3):
            if turns[index]["write_phases"] != ["show_writing_plan"]:
                failures.append(f"turn {index + 1} did not show WritingPlan")
            if "WritingPlan" not in str(turns[index]["reply"] or ""):
                failures.append(f"turn {index + 1} response omitted WritingPlan")

    final_content = turns[-1].get("content") or {}
    blocks = final_content.get("blocks") if isinstance(final_content, dict) else []
    body = "\n".join(
        str(block.get("content") or block.get("text") or "")
        for block in (blocks or [])
    )
    if len(body.strip()) < 200:
        failures.append("final Draft body is too short")
    for required_term in ("核查", "复核", "范围"):
        if required_term not in body:
            failures.append(f"final Draft omitted planned block: {required_term}")

    result = {
        "workspace": workspace_id,
        "chapter_id": chapter_id,
        "chapter_title": chapter.get("title"),
        "turns": [
            {
                "input": turn["input"],
                "write_phases": turn["write_phases"],
                "reply": turn["reply"],
                "write_completed": turn["write_completed"],
                "chapter_revision": (turn.get("chapter") or {}).get("chapter_revision"),
                "content_revision": (turn.get("content") or {}).get("content_revision"),
            }
            for turn in turns
        ],
        "final_body": body,
        "pass": not failures,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
