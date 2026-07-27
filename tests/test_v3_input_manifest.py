from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.source_normalizer import SourceNormalizer  # noqa: E402


class V3InputManifestTests(unittest.TestCase):
    def _context(self, base: Path) -> WorkspaceContext:
        runs = base / "runs"
        (runs / "alpha").mkdir(parents=True)
        return WorkspaceContext.resolve(runs, "alpha")

    def test_reference_docx_role_never_activates_template_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "参考资料.md"
            source.write_text("行业背景", encoding="utf-8")
            service = InputManifestService(self._context(base))
            registration = service.register_local_file(source, InputRole.REFERENCE)
            active_templates = [item for item in registration.manifest.inputs if item.role is InputRole.TEMPLATE and item.active]
            self.assertEqual(active_templates, [])
            self.assertEqual(registration.item.role, InputRole.REFERENCE)

    def test_template_replacement_deactivates_old_version_and_marks_all_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "模板一.md"
            second = base / "模板二.md"
            first.write_text("第一版", encoding="utf-8")
            second.write_text("第二版", encoding="utf-8")
            service = InputManifestService(self._context(base))
            original = service.register_local_file(first, InputRole.TEMPLATE)
            replacement = service.register_local_file(second, InputRole.TEMPLATE, replaces_input_id=original.item.input_id)
            active_templates = [item for item in replacement.manifest.inputs if item.role is InputRole.TEMPLATE and item.active]
            self.assertEqual([item.input_id for item in active_templates], [replacement.item.input_id])
            self.assertEqual(replacement.change_set.affected_contract_nodes, ["*"])
            self.assertEqual(replacement.change_set.affected_content_units, ["*"])

    def test_normalization_keeps_company_and_reference_chunks_separate_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            company = base / "企业资料.md"
            reference = base / "标准资料.md"
            company.write_text("企业资质 A\n\n企业业绩 B", encoding="utf-8")
            reference.write_text("国家标准 C", encoding="utf-8")
            context = self._context(base)
            service = InputManifestService(context)
            service.register_local_file(company, InputRole.COMPANY)
            service.register_local_file(reference, InputRole.REFERENCE)
            normalizer = SourceNormalizer(context)
            first = normalizer.normalize_active_inputs()
            second = normalizer.normalize_active_inputs()
            roles = first["by_role"]
            self.assertEqual(len(roles["company"]), 2)
            self.assertEqual(len(roles["reference"]), 1)
            self.assertEqual(
                [item["chunk_id"] for item in first["by_role"]["company"]],
                [item["chunk_id"] for item in second["by_role"]["company"]],
            )


if __name__ == "__main__":
    unittest.main()
