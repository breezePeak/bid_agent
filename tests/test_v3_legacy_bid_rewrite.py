from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import UploadFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import api.v3_app as v3_app  # noqa: E402
from control_plane import ControlPlaneError, ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.legacy_bid_index import LegacyBidIndexService  # noqa: E402
from document_pipeline.legacy_bid_source import LegacyBidSourceService  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


def payload(response):
    return json.loads(response.body)


class V3LegacyBidRewriteTests(unittest.TestCase):
    def context(self, base: Path, mode: str = "bid_rewrite") -> WorkspaceContext:
        runs = base / "runs"
        (runs / "alpha" / "workspace" / "v3").mkdir(parents=True)
        context = WorkspaceContext.resolve(runs, "alpha")
        ControlStore(context).initialize_workspace_profile(mode)
        return context

    def test_workspace_profile_defaults_and_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = self.context(Path(temporary), "full_write")
            store = ControlStore(context)
            self.assertEqual(store.workspace_profile()["project_mode"], "full_write")
            with self.assertRaises(ControlPlaneError) as raised:
                store.initialize_workspace_profile("bid_rewrite")
            self.assertEqual(raised.exception.code, "PROJECT_MODE_IMMUTABLE")

            (Path(temporary) / "runs" / "old" / "workspace" / "v3").mkdir(parents=True)
            old = WorkspaceContext.resolve(Path(temporary) / "runs", "old")
            self.assertEqual(ControlStore(old).workspace_profile()["project_mode"], "full_write")

    def test_old_bid_is_indexed_without_main_input_pollution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            context = self.context(base)
            old_bid = base / "old.md"
            old_bid.write_text(
                "# 技术方案\n总体说明\n## 实施计划\n第一阶段实施。\n",
                encoding="utf-8",
            )
            source = LegacyBidSourceService(context).register_local_file(old_bid, old_bid.name)
            store = ControlStore(context)
            index = LegacyBidSourceService(context).index(source.legacy_bid_id)

            self.assertIsNone(store.v3_active_artifact("InputManifest"))
            self.assertIsNone(store.v3_active_artifact("SourceIndex"))
            self.assertEqual([section.title for section in index.sections], ["技术方案", "实施计划"])
            self.assertEqual(index.sections[1].parent_section_id, index.sections[0].section_id)
            self.assertTrue(all(block.content_hash for block in index.blocks))
            snapshot = V3WorkspaceSnapshotBuilder(context).build()
            self.assertEqual(snapshot["profile"]["project_mode"], "bid_rewrite")
            self.assertEqual(snapshot["legacy_bid"]["status"], "ready")
            self.assertEqual(snapshot["legacy_bid"]["section_count"], 2)

            original_ids = [block.block_id for block in index.blocks]
            rebuilt = LegacyBidIndexService(context).build(source)
            self.assertEqual([block.block_id for block in rebuilt.blocks], original_ids)

    def test_replacement_advances_manifest_and_rebuilds_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            context = self.context(base)
            first = base / "first.md"
            second = base / "second.md"
            first.write_text("# 旧目录\n旧段落", encoding="utf-8")
            second.write_text("# 新目录\n新段落", encoding="utf-8")
            service = LegacyBidSourceService(context)
            old = service.register_local_file(first, first.name)
            old_index = service.index(old.legacy_bid_id)
            new = service.register_local_file(second, second.name)
            new_index = service.index(new.legacy_bid_id)

            manifest = service.manifest()
            self.assertEqual(sum(1 for item in manifest.sources if item.active), 1)
            self.assertFalse(next(item for item in manifest.sources if item.legacy_bid_id == old.legacy_bid_id).active)
            self.assertGreater(new_index.source_manifest_revision, old_index.source_manifest_revision)
            self.assertNotEqual(new_index.file_hash, old_index.file_hash)

            restored = service.register_local_file(first, first.name)
            self.assertEqual(restored.legacy_bid_id, old.legacy_bid_id)
            self.assertEqual(len({item.legacy_bid_id for item in service.manifest().sources}), 2)
            self.assertEqual(sum(1 for item in service.manifest().sources if item.active), 1)

    def test_full_write_and_generic_upload_reject_legacy_bid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            context = self.context(base, "full_write")
            path = base / "old.md"
            path.write_text("# 旧标书", encoding="utf-8")
            with self.assertRaises(ControlPlaneError) as raised:
                LegacyBidSourceService(context).register_local_file(path, path.name)
            self.assertEqual(raised.exception.code, "LEGACY_BID_MODE_REQUIRED")
            with self.assertRaisesRegex(ValueError, "LEGACY_BID_UPLOAD_ISOLATED"):
                InputManifestService(context).register_local_file(path, InputRole.LEGACY_BID)

            with mock.patch.object(v3_app, "RUNS_DIR", base / "runs"):
                response = asyncio.run(
                    v3_app.upload(
                        "alpha",
                        "legacy_bid",
                        UploadFile(filename="old.md", file=io.BytesIO(b"# old")),
                        "",
                    )
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(payload(response)["error"]["code"], "LEGACY_BID_UPLOAD_ISOLATED")

    def test_legacy_bid_http_functions_complete_upload_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            context = self.context(base)
            with mock.patch.object(v3_app, "RUNS_DIR", base / "runs"):
                response = asyncio.run(
                    v3_app.upload_legacy_bid(
                        "alpha",
                        UploadFile(
                            filename="old.md",
                            file=io.BytesIO("# 总体方案\n原文段落".encode("utf-8")),
                        ),
                    )
                )
                body = payload(response)
                listed = payload(v3_app.list_legacy_bids("alpha"))
                preview = payload(
                    v3_app.get_legacy_bid_index(
                        "alpha", body["legacy_bid"]["legacy_bid_id"]
                    )
                )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(len(listed["legacy_bids"]), 1)
            self.assertEqual(preview["index"]["sections"][0]["title"], "总体方案")
            self.assertIsNone(ControlStore(context).v3_active_artifact("InputManifest"))

    def test_parse_failure_is_persisted_for_snapshot_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            context = self.context(base)
            broken = base / "broken.docx"
            broken.write_bytes(b"not-a-docx")
            with self.assertRaises(Exception):
                LegacyBidSourceService(context).register_local_file(broken, broken.name)
            snapshot = V3WorkspaceSnapshotBuilder(context).build()
            self.assertEqual(snapshot["legacy_bid"]["status"], "failed")
            self.assertTrue(snapshot["legacy_bid"]["error"])


if __name__ == "__main__":
    unittest.main()
