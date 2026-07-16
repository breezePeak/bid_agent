from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claim_validator import align_claim_to_chunks, validate_chapter_claims


class ClaimSourceAlignmentTests(unittest.TestCase):
    def test_align_amount_to_chunk(self) -> None:
        claim = {
            "type": "amount",
            "value": "500万元",
            "amount_key": "5000000",
            "text": "完成某某银行项目，合同金额 500 万元。",
            "certainty": True,
        }
        chunks = [
            {
                "chunk_id": "COMPANY_001",
                "source": "company",
                "content": "我司为某某银行实施系统项目，合同金额 500 万元，已验收。",
            }
        ]
        alignments = align_claim_to_chunks(claim, chunks)
        self.assertTrue(alignments)
        self.assertEqual(alignments[0]["chunk_id"], "COMPANY_001")
        self.assertGreaterEqual(alignments[0]["score"], 0.9)

    def test_validate_chapter_writes_alignment_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs").mkdir()
            (root / "workspace" / "chunks").mkdir(parents=True)
            (root / "workspace" / "contexts").mkdir(parents=True)
            (root / "workspace" / "source_traces").mkdir(parents=True)
            (root / "inputs" / "company.md").write_text(
                "示例科技有限公司已取得 ISO9001 认证。完成某某银行项目，合同金额 500 万元。",
                encoding="utf-8",
            )
            (root / "inputs" / "tender.md").write_text("招标说明", encoding="utf-8")
            (root / "workspace" / "company_facts.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "global_facts.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "chunks" / "company_chunks.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "COMPANY_001",
                            "content": "示例科技有限公司已取得 ISO9001 认证，完成某某银行项目，合同金额 500 万元。",
                            "source": "company.md",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "workspace" / "chunks" / "tender_chunks.json").write_text("[]", encoding="utf-8")
            (root / "workspace" / "contexts" / "01_context.json").write_text(
                json.dumps(
                    {
                        "chapter_id": "01",
                        "selected_company_chunks": [{"id": "COMPANY_001", "reason": "业绩"}],
                        "selected_tender_chunks": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = validate_chapter_claims(
                root,
                "01",
                "我司已取得 ISO9001 认证，并完成某某银行项目，合同金额 500 万元。",
            )
            self.assertGreaterEqual(result.get("aligned_count", 0), 1)
            self.assertTrue(result.get("claim_alignments"))
            self.assertTrue(result.get("ok"))


if __name__ == "__main__":
    unittest.main()
