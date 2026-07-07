from __future__ import annotations

from pathlib import Path

from utils import read_json, read_nonempty_text, read_text


def input_path(root: Path, filename: str) -> Path:
    return root / "inputs" / filename


def workspace_path(root: Path, filename: str) -> Path:
    return root / "workspace" / filename


def read_input(root: Path, filename: str) -> str:
    return read_text(input_path(root, filename))


def read_required_input(root: Path, filename: str, purpose: str) -> str:
    return read_nonempty_text(input_path(root, filename), purpose)


def load_score_points(root: Path) -> list[dict]:
    data = read_json(root / "workspace" / "score_points.json")
    if not isinstance(data, list):
        raise ValueError("workspace/score_points.json 必须是 JSON 数组。")
    return data


def load_global_facts(root: Path) -> dict:
    data = read_json(root / "workspace" / "global_facts.json")
    if not isinstance(data, dict):
        raise ValueError("workspace/global_facts.json 必须是 JSON 对象。")
    return data


def load_tender_requirements(root: Path) -> dict:
    data = read_json(root / "workspace" / "tender_requirements.json")
    if not isinstance(data, dict):
        raise ValueError("workspace/tender_requirements.json 必须是 JSON 对象。")
    return data


def load_outline(root: Path) -> dict:
    data = read_json(root / "workspace" / "outline.json")
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise ValueError("workspace/outline.json 必须包含 chapters 数组。")
    return data


def load_template_outline(root: Path) -> dict:
    schema_path = root / "workspace" / "template_schema.json"
    if schema_path.exists():
        try:
            schema = read_json(schema_path)
            headings = schema.get("headings") if isinstance(schema, dict) else []
            if isinstance(headings, list) and headings:
                return {
                    "headings": [
                        {
                            "id": str(item.get("id", "")),
                            "title": str(item.get("title", "")),
                            "level": int(item.get("level", 1)),
                            "parent_id": str(item.get("parent_id", "")),
                        }
                        for item in headings
                        if isinstance(item, dict) and item.get("id") and item.get("title")
                    ],
                    "writing_tasks": schema.get("writing_tasks", []),
                    "fill_slots": schema.get("fill_slots", []),
                }
        except Exception:
            pass

    template_path = root / "inputs" / "template.docx"
    if not template_path.exists() or template_path.stat().st_size == 0:
        return {"headings": []}

    try:
        from docx import Document
    except ImportError as exc:
        raise ImportError("缺少依赖 python-docx，请先执行: pip install -r requirements.txt") from exc

    document = Document(str(template_path))
    counters = [0] * 9
    headings: list[dict[str, str | int]] = []

    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        style_name = para.style.name if para.style else ""
        if not style_name.startswith("Heading "):
            continue

        try:
            level = int(style_name.split()[-1])
        except Exception:
            continue

        if level < 1 or level > len(counters):
            continue

        counters[level - 1] += 1
        for idx in range(level, len(counters)):
            counters[idx] = 0

        number = ".".join(str(value) for value in counters[:level] if value > 0)
        parent_id = ".".join(number.split(".")[:-1])
        headings.append(
            {
                "id": number,
                "title": text,
                "level": level,
                "parent_id": parent_id,
            }
        )

    return {"headings": headings}


def load_template_evidence_map(root: Path) -> dict:
    evidence_path = root / "workspace" / "template_evidence_map.json"
    if not evidence_path.exists() or evidence_path.stat().st_size == 0:
        return {"summary": {}, "items": []}
    data = read_json(evidence_path)
    if not isinstance(data, dict):
        return {"summary": {}, "items": []}
    items = data.get("items")
    if not isinstance(items, list):
        data["items"] = []
    return data
