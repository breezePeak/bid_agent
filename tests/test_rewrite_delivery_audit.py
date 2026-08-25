import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.rewrite_delivery_audit import RewriteDeliveryAuditService  # noqa: E402


def test_delivery_audit_blocks_legacy_values_and_internal_markers() -> None:
    service = RewriteDeliveryAuditService.__new__(RewriteDeliveryAuditService)
    service.context = SimpleNamespace()
    service.store = SimpleNamespace(
        v3_active_artifact=lambda _kind: {
            "payload": {"blocks": [{"content": "项目名称：旧城平台项目。工期30天"}]}
        }
    )
    service._strategies = lambda: []
    with mock.patch("document_pipeline.rewrite_delivery_audit.GlobalProjectContextService") as context_service:
        context_service.return_value.load.return_value = {
            "confirmed_facts": [{"statement": "新城云平台项目"}]
        }
        report = service.audit(
            {"blocks": [{"content": "项目名称：旧城平台项目。rewrite_context"}]},
            delivery_kind="final",
    )
    assert report["status"] == "blocked"
    assert "改写内部标记" in {item["type"] for item in report["findings"]}
    assert any("旧城平台项目" in item["source_text"] for item in report["findings"])
