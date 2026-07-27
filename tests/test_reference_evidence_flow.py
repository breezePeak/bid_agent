from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chapter_writer import _load_selected_chunks
from context_selector import _load_shared_inputs
from document_splitter import split_docs
from reference_extractor import run_reference_import


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_reference_and_writing_brief_are_imported_separately(tmp_path: Path) -> None:
    _write(
        tmp_path / "sources" / "reference" / "standard.md",
        "# 技术标准\n\n用于说明专业方法，不代表投标人业绩。",
    )
    _write(
        tmp_path / "sources" / "guidance" / "brief.md",
        "# 编写要求\n\n项目理解按背景、范围、任务组织。",
    )

    reference_path, brief_path = run_reference_import(tmp_path)

    assert reference_path == tmp_path / "inputs" / "reference.md"
    assert brief_path == tmp_path / "inputs" / "writing_brief.md"
    assert "资料类型: 外部参考资料" in reference_path.read_text(encoding="utf-8")
    assert "项目理解按背景、范围、任务组织" in brief_path.read_text(encoding="utf-8")


def test_split_docs_creates_optional_reference_chunks(tmp_path: Path) -> None:
    _write(tmp_path / "inputs" / "tender.md", "# 采购需求\n\n完成遥感解译。")
    _write(tmp_path / "inputs" / "company.md", "# 公司资料\n\n具备已证明的项目经验。")
    _write(tmp_path / "inputs" / "reference.md", "# 行业方法\n\n先建立解译标志，再开展内业判读。")

    split_docs(tmp_path)

    chunks = json.loads(
        (tmp_path / "workspace" / "chunks" / "reference_chunks.json").read_text(
            encoding="utf-8"
        )
    )
    assert chunks
    assert chunks[0]["id"].startswith("REFERENCE_")
    assert chunks[0]["source"] == "reference.md"


def test_writer_loads_reference_without_mixing_it_into_company_evidence(
    tmp_path: Path,
) -> None:
    chunks_dir = tmp_path / "workspace" / "chunks"
    _write(
        chunks_dir / "tender_chunks.json",
        json.dumps([{"id": "TENDER_001", "content": "采购要求"}], ensure_ascii=False),
    )
    _write(
        chunks_dir / "company_chunks.json",
        json.dumps([{"id": "COMPANY_001", "content": "公司事实"}], ensure_ascii=False),
    )
    _write(
        chunks_dir / "reference_chunks.json",
        json.dumps([{"id": "REFERENCE_001", "content": "行业方法"}], ensure_ascii=False),
    )

    tender, company, reference = _load_selected_chunks(
        tmp_path,
        {
            "selected_tender_chunks": [{"id": "TENDER_001"}],
            "selected_company_chunks": [{"id": "COMPANY_001"}],
            "selected_reference_chunks": [{"id": "REFERENCE_001"}],
        },
    )

    assert [item["id"] for item in tender] == ["TENDER_001"]
    assert [item["id"] for item in company] == ["COMPANY_001"]
    assert [item["id"] for item in reference] == ["REFERENCE_001"]


def test_shared_context_fingerprint_includes_reference_and_writing_brief(
    tmp_path: Path,
    monkeypatch,
) -> None:
    chunks_dir = tmp_path / "workspace" / "chunks"
    _write(chunks_dir / "tender_chunks.json", "[]")
    _write(chunks_dir / "company_chunks.json", "[]")
    _write(
        chunks_dir / "reference_chunks.json",
        '[{"id":"REFERENCE_001","content":"方法"}]',
    )
    _write(tmp_path / "workspace" / "score_points.json", "[]")
    _write(tmp_path / "workspace" / "global_facts.json", "{}")
    _write(tmp_path / "workspace" / "template_evidence_map.json", "{}")
    _write(tmp_path / "inputs" / "writing_brief.md", "按背景、范围、任务组织")
    monkeypatch.setattr(
        "context_selector.load_agent_prompt",
        lambda root, role: "context prompt",
    )

    shared = _load_shared_inputs(tmp_path)

    assert shared["reference_chunks"][0]["id"] == "REFERENCE_001"
    assert shared["writing_brief"] == "按背景、范围、任务组织"
    assert shared["shared_input_fingerprint"]
