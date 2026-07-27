from __future__ import annotations
import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from control_plane import WorkspaceContext
from document_pipeline.contracts import InputRole
from document_pipeline.input_manifest import InputManifestService
from document_pipeline.stage_runner import V3StageRunner
class V3StageRunnerTests(unittest.TestCase):
 def test_runs_v3_content_chain_and_rejects_unknown_stage(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   b=Path(t);runs=b/'runs';(runs/'a').mkdir(parents=True);c=WorkspaceContext.resolve(runs,'a');(b/'t.md').write_text('项目目标。\n\n服务范围；交付成果；验收条件；工期30日。',encoding='utf-8');(b/'s.md').write_text('评分要求：实施方案。',encoding='utf-8');i=InputManifestService(c);i.register_local_file(b/'t.md',InputRole.TENDER);i.register_local_file(b/'s.md',InputRole.SCORE);r=V3StageRunner(c)
   for stage in ('ingest_inputs','normalize_sources','build_requirement_ledger','analyze_scores','build_project_model','compile_chapter_blueprint','confirm_planning','sync_material_requirements','compile_document_contract','plan_document','execute_content_plan','integrate_document'):r.run(stage)
   self.assertEqual(r.run('verify_document').verdict,'pass')
   self.assertTrue(r.run('render_document')[0].exists())
   self.assertEqual(r.run('verify_delivery')['status'],'ready')
   with self.assertRaisesRegex(ValueError,'V3_UNKNOWN_STAGE'):r.run('legacy')

 def test_render_is_refused_until_the_content_gate_passes(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   b=Path(t);runs=b/'runs';(runs/'a').mkdir(parents=True);c=WorkspaceContext.resolve(runs,'a');(b/'t.md').write_text('项目目标。\n\n服务范围；交付成果；验收条件；工期30日。',encoding='utf-8');i=InputManifestService(c);i.register_local_file(b/'t.md',InputRole.TENDER);r=V3StageRunner(c)
   for stage in ('normalize_sources','build_requirement_ledger','analyze_scores','plan_response','compile_chapter_blueprint','confirm_planning','compile_document_contract','plan_document','execute_content_plan','integrate_document'):r.run(stage)
   with self.assertRaisesRegex(ValueError,'RENDER_BLOCKED'):
    r.run('render_document')
if __name__=='__main__':unittest.main()
