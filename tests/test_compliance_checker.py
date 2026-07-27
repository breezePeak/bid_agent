from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from compliance_checker import (
    make_check_item,
    normalize_compliance_report,
    run_compliance_check,
    summarize_compliance_items,
)
from quality_gates import compliance_review_status, validate_compliance_blocking


class ComplianceCheckerTests(unittest.TestCase):
    def _write_json(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_make_check_item_schema(self) -> None:
        item = make_check_item(
            check_id="SIGN-001",
            check_type="signature",
            check_name="投标函签字盖章",
            status="fail",
            severity="fatal",
            requirement="投标函须签字盖章",
            suggestion="补充签章",
            need_manual_review=True,
        )
        self.assertEqual(item["check_id"], "SIGN-001")
        self.assertEqual(item["severity"], "fatal")
        self.assertTrue(item["need_manual_review"])
        self.assertIn("requirement_source", item)
        self.assertIn("bid_evidence", item)

    def test_summarize_blocking_on_fatal_fail(self) -> None:
        items = [
            make_check_item(
                check_id="A",
                check_type="x",
                check_name="a",
                status="fail",
                severity="fatal",
                need_manual_review=True,
            ),
            make_check_item(
                check_id="B",
                check_type="x",
                check_name="b",
                status="pass",
                severity="info",
            ),
        ]
        summary = summarize_compliance_items(items)
        self.assertFalse(summary["blocking"])
        self.assertTrue(summary["advisory_only"])
        self.assertTrue(summary["hard_findings"])
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["counts"]["fail"], 1)

    def test_compliance_review_status(self) -> None:
        self.assertEqual(compliance_review_status({"blocking": True}), "warn")
        self.assertEqual(compliance_review_status({"need_manual_review": True}), "warn")
        self.assertEqual(compliance_review_status({"ok": True, "blocking": False}), "ok")

    def test_run_compliance_check_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_text(
                root / "inputs" / "tender.md",
                "\n".join(
                    [
                        "项目名称：示范采购项目",
                        "项目编号：DEMO-2026-001",
                        "投标有效期不少于 90 天。",
                        "投标保证金人民币 10000 元。",
                        "投标函须由法定代表人签字并加盖公章。",
                        "★ 必须提供驻场服务。",
                        "有下列情形之一的，作废标处理：未按要求缴纳保证金。",
                        "投标文件应包含法定代表人身份证明、授权委托书、营业执照。",
                    ]
                ),
            )
            self._write_text(root / "inputs" / "score.md", "评分标准：技术分 70，商务分 30。")
            self._write_text(root / "inputs" / "company.md", "投标人：示例科技有限公司\n营业执照已附后。")
            self._write_json(
                root / "workspace" / "global_facts.json",
                {
                    "project_name": "示范采购项目",
                    "bidder_name": "示例科技有限公司",
                    "service_period": "一年",
                    "warranty_period": "一年",
                },
            )
            self._write_json(
                root / "workspace" / "tender_requirements.json",
                {
                    "qualification_requirements": [
                        "具备独立法人资格",
                        "提供营业执照",
                    ],
                    "evidence_notes": ["按要求缴纳投标保证金"],
                },
            )
            self._write_json(
                root / "workspace" / "outline.json",
                {"chapters": [{"id": "01", "title": "商务响应"}]},
            )
            self._write_text(
                root / "workspace" / "chapters" / "01.md",
                "\n".join(
                    [
                        "# 01 商务响应",
                        "项目名称：示范采购项目",
                        "投标人：示例科技有限公司",
                        "项目编号：DEMO-2026-001",
                        "投标有效期 90 天。",
                        "我方完全响应★驻场服务要求。",
                        "投标函已由法定代表人签字并加盖公章。",
                        "授权委托书附后。",
                        "投标保证金 10000 元已缴纳，凭证附后。",
                        "法定代表人身份证明、营业执照附后。",
                        "具备独立法人资格。",
                    ]
                ),
            )
            self._write_json(
                root / "workspace" / "global_review.json",
                {
                    "project_name_consistent": True,
                    "bidder_name_consistent": True,
                    "service_period_consistent": True,
                    "warranty_period_consistent": True,
                    "chapter_conflicts": [],
                    "need_manual_review": False,
                },
            )
            self._write_json(root / "workspace" / "score_points.json", [{"id": "S001", "title": "技术"}])

            report_path = run_compliance_check(root)
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            normalized = normalize_compliance_report(report)
            self.assertIn("items", normalized)
            self.assertIn("summary", normalized)
            self.assertTrue(len(normalized["items"]) >= 8)
            check_types = {item["check_type"] for item in normalized["items"]}
            for expected in {
                "qualification",
                "disqualification",
                "mandatory_param",
                "signature",
                "bid_bond",
                "bid_validity",
                "completeness",
                "consistency",
                "commercial",
            }:
                self.assertIn(expected, check_types)
            # 签章不得因正文出现“盖章”而自动 pass
            signature_items = [i for i in normalized["items"] if i["check_type"] == "signature"]
            self.assertTrue(signature_items)
            self.assertTrue(all(i["status"] != "pass" for i in signature_items))
            self.assertTrue(all(i.get("need_manual_review") for i in signature_items))

    def test_signature_never_auto_pass_on_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_text(root / "inputs" / "tender.md", "投标函须签字盖章。授权委托书须签字。")
            self._write_json(root / "workspace" / "global_facts.json", {"project_name": "P", "bidder_name": "C"})
            self._write_json(root / "workspace" / "tender_requirements.json", {"qualification_requirements": []})
            self._write_json(root / "workspace" / "outline.json", {"chapters": [{"id": "01", "title": "A"}]})
            self._write_text(
                root / "workspace" / "chapters" / "01.md",
                "投标函已签字盖章。授权委托书已签字。法定代表人签字。公章已盖。",
            )
            self._write_json(root / "workspace" / "score_points.json", [])
            report = json.loads(run_compliance_check(root).read_text(encoding="utf-8"))
            signature_items = [i for i in report["items"] if i["check_type"] == "signature"]
            self.assertTrue(signature_items)
            self.assertTrue(all(i["status"] in {"warn", "fail"} for i in signature_items))

    def test_commercial_ceiling_over_limit_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_text(
                root / "inputs" / "tender.md",
                "最高限价 100000 元。投标文件应包含分项报价表。",
            )
            self._write_json(root / "workspace" / "global_facts.json", {"project_name": "P", "bidder_name": "C"})
            self._write_json(root / "workspace" / "tender_requirements.json", {})
            self._write_json(root / "workspace" / "outline.json", {"chapters": [{"id": "01", "title": "报价"}]})
            self._write_text(
                root / "workspace" / "chapters" / "01.md",
                "分项报价表如下。投标总价 150000 元。",
            )
            self._write_json(root / "workspace" / "score_points.json", [])
            report = json.loads(run_compliance_check(root).read_text(encoding="utf-8"))
            price_items = [i for i in report["items"] if i.get("check_id") == "PRICE-010"]
            self.assertTrue(price_items)
            self.assertEqual(price_items[0]["status"], "fail")
            self.assertFalse(report.get("blocking"))
            self.assertTrue(report.get("need_manual_review"))

    def test_final_phase_requires_final_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_json(root / "workspace" / "global_facts.json", {})
            self._write_json(root / "workspace" / "tender_requirements.json", {})
            report = json.loads(
                run_compliance_check(root, raise_on_blocking=True, phase="final").read_text(encoding="utf-8")
            )
            self.assertFalse(report.get("blocking"))
            self.assertTrue(report.get("need_manual_review"))

    def test_validate_compliance_blocking_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "workspace" / "compliance_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps({"blocking": True, "summary": {"blocking": True}, "items": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            validate_compliance_blocking(root, required=True)

    def test_missing_bid_content_marks_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_text(
                root / "inputs" / "tender.md",
                "投标有效期不少于 90 天。★ 强制提供驻场。废标：未盖章。投标保证金 5000 元。",
            )
            self._write_json(
                root / "workspace" / "global_facts.json",
                {"project_name": "X项目", "bidder_name": "Y公司"},
            )
            self._write_json(
                root / "workspace" / "tender_requirements.json",
                {"qualification_requirements": ["具备相关资质"]},
            )
            self._write_json(root / "workspace" / "outline.json", {"chapters": [{"id": "01", "title": "A"}]})
            self._write_json(root / "workspace" / "score_points.json", [])
            report_path = run_compliance_check(root)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report.get("blocking") or report.get("need_manual_review"))


if __name__ == "__main__":
    unittest.main()
