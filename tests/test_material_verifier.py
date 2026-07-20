from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.material_verifier import (
    classify_material_type,
    extract_fields,
    human_confirm_verification,
    verify_material,
)
from utils import write_json


class MaterialVerifierTests(unittest.TestCase):
    def test_extract_fields_and_type(self) -> None:
        text = (
            "质量管理体系认证证书\n"
            "单位名称：某某科技有限公司\n"
            "证书编号：ISO-2024-001234\n"
            "签发单位：认证中心\n"
            "有效期至：2028年12月31日\n"
            "ISO9001\n"
        )
        mtype, conf = classify_material_type(text, "iso.pdf")
        self.assertEqual(mtype, "iso_cert")
        self.assertGreater(conf, 0.3)
        fields = extract_fields(text)
        self.assertIn("有限公司", fields["company_name"])
        self.assertTrue(fields["cert_no"])
        self.assertTrue(fields["valid_until"].startswith("2028"))

    def test_valid_cert_can_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            write_json(
                ws / "materials_checklist.json",
                {
                    "items": [
                        {
                            "item_id": "mat_iso",
                            "title": "ISO9001 质量管理体系认证",
                            "requirement": "ISO9001 质量管理体系认证",
                            "category": "qualification",
                        }
                    ]
                },
            )
            write_json(ws / "company_facts.json", {"company_name": "某某科技有限公司"})
            cert = ws / "upload_iso.txt"
            cert.write_text(
                "ISO9001 质量管理体系认证证书\n"
                "单位名称：某某科技有限公司\n"
                "证书编号：ABC123456789\n"
                "有效期至：2030年01月01日\n",
                encoding="utf-8",
            )
            result = verify_material(root, "mat_iso", uploaded_path=str(cert))
            self.assertTrue(result.get("ok"))
            self.assertIn(result.get("lifecycle_status"), {"verified", "uploaded"})
            self.assertTrue(result.get("evidence_path"))
            if result.get("lifecycle_status") != "verified":
                # low conf path still allows human confirm
                confirmed = human_confirm_verification(root, "mat_iso", accept=True, operator="tester")
                self.assertEqual(confirmed.get("lifecycle_status"), "verified")

    def test_expired_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            write_json(
                ws / "materials_checklist.json",
                {
                    "items": [
                        {
                            "item_id": "mat_iso",
                            "title": "ISO9001",
                            "requirement": "ISO9001",
                            "category": "qualification",
                        }
                    ]
                },
            )
            write_json(ws / "company_facts.json", {"company_name": "某某科技有限公司"})
            cert = ws / "expired.txt"
            cert.write_text(
                "ISO9001\n单位名称：某某科技有限公司\n有效期至：2020年01月01日\n",
                encoding="utf-8",
            )
            result = verify_material(root, "mat_iso", uploaded_path=str(cert))
            self.assertEqual(result.get("lifecycle_status"), "rejected")
            self.assertIn("expired", result.get("match", {}).get("issues") or [])

    def test_unrelated_file_not_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            write_json(
                ws / "materials_checklist.json",
                {
                    "items": [
                        {
                            "item_id": "mat_safety",
                            "title": "安全生产许可证",
                            "requirement": "安全生产许可证",
                            "category": "qualification",
                        }
                    ]
                },
            )
            junk = ws / "menu.txt"
            junk.write_text("今日菜单：红烧肉 青菜", encoding="utf-8")
            result = verify_material(root, "mat_safety", uploaded_path=str(junk))
            self.assertNotEqual(result.get("lifecycle_status"), "verified")


if __name__ == "__main__":
    unittest.main()
