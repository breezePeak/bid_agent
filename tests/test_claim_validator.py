from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claim_validator import (
    extract_claims,
    validate_claims_against_evidence,
    validate_chapter_claims,
)
from quality_gates import validate_chapter_claims_gate


class ClaimValidatorTests(unittest.TestCase):
    def test_extract_amount_and_cert_claims(self) -> None:
        text = "我司已取得 ISO9001 认证，并完成了某某银行核心系统项目，合同金额 500 万元。"
        claims = extract_claims(text)
        types = {c["type"] for c in claims}
        self.assertIn("certification", types)
        self.assertIn("amount", types)
        self.assertIn("case", types)

    def test_ungrounded_amount_is_blocker(self) -> None:
        evidence = {
            "company_text": "示例科技有限公司，具备软件开发能力。",
            "amount_text": "示例科技有限公司",
        }
        result = validate_claims_against_evidence(
            "我司已完成某某银行项目，合同金额 800 万元。",
            evidence,
        )
        self.assertFalse(result["ok"])
        self.assertGreaterEqual(result["blocker_count"], 1)
        self.assertTrue(any(f["claim_type"] == "amount" for f in result["findings"]))

    def test_grounded_cert_passes(self) -> None:
        evidence = {
            "company_text": "公司已取得 ISO9001 质量管理体系认证。",
            "amount_text": "公司已取得 ISO9001 质量管理体系认证。",
        }
        result = validate_claims_against_evidence(
            "我司已取得 ISO9001 认证。",
            evidence,
        )
        cert_findings = [f for f in result["findings"] if f.get("claim_type") == "certification"]
        self.assertEqual(cert_findings, [])

    def test_writer_gate_raises_on_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs").mkdir()
            (root / "workspace").mkdir()
            (root / "inputs" / "company.md").write_text("示例公司简介。", encoding="utf-8")
            (root / "inputs" / "tender.md").write_text("招标说明", encoding="utf-8")
            (root / "workspace" / "company_facts.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "global_facts.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_chapter_claims_gate(
                    root,
                    "01",
                    "我司已具备涉密信息系统集成甲级资质，合同金额 1200 万元。",
                    raise_on_blocker=True,
                )

    def test_disabling_chapter_review_disables_write_time_claim_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs").mkdir()
            (root / "workspace").mkdir()
            (root / "inputs" / "company.md").write_text("示例公司简介。", encoding="utf-8")
            (root / "inputs" / "tender.md").write_text("招标说明", encoding="utf-8")
            (root / "workspace" / "company_facts.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "global_facts.json").write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, {"BID_AGENT_CHAPTER_REVIEW_ENABLED": "0"}):
                result = validate_chapter_claims_gate(
                    root,
                    "01",
                    "我司已具备涉密信息系统集成甲级资质，合同金额 1200 万元。",
                    raise_on_blocker=True,
                )
            self.assertGreater(result["blocker_count"], 0)


if __name__ == "__main__":
    unittest.main()
