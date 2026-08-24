from __future__ import annotations
import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from control_plane import ControlPlaneError, WorkspaceContext
from document_pipeline.contracts import InputRole
from document_pipeline.input_manifest import InputManifestService
from document_pipeline.stage_runner import V3StageRunner
from document_pipeline.artifact_promotion import HumanGateService
from document_pipeline.chapter_writing_service import ChapterWritingRequest, ChapterWritingService
from control_plane import ControlStore


def write_planned_units(context, units):
 service=ChapterWritingService(context,deterministic_test=True)
 results=[]
 for unit in units:
  results.append(service.write(ChapterWritingRequest(unit_id=unit.unit_id,node_ids=tuple(unit.node_ids),run_research=False,commit_drafts=False)))
 return results


class V3StageRunnerTests(unittest.TestCase):
 def test_runs_v3_content_chain_and_rejects_unknown_stage(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   b=Path(t);runs=b/'runs';(runs/'a').mkdir(parents=True);c=WorkspaceContext.resolve(runs,'a');(b/'t.md').write_text('项目目标。\n\n服务范围；交付成果；验收条件；工期30日。',encoding='utf-8');(b/'s.md').write_text('评分要求：实施方案。',encoding='utf-8');i=InputManifestService(c);i.register_local_file(b/'t.md',InputRole.TENDER);i.register_local_file(b/'s.md',InputRole.SCORE);r=V3StageRunner.for_deterministic_tests(c)
   for stage in ('ingest_inputs','normalize_sources','build_requirement_ledger','analyze_scores','build_project_model','compile_chapter_blueprint','confirm_planning'):r.run(stage)
   ControlStore(c).grant_workspace_access('owner')
   HumanGateService(c).confirm_planning(principal_id='owner',submitted_snapshot=HumanGateService(c).planning_snapshot(),nonce='stage-runner-h1')
   for stage in ('sync_material_requirements','compile_document_contract'):r.run(stage)
   _,units=r.run('plan_document');results=write_planned_units(c,units)
   self.assertTrue(results)
   self.assertTrue(all(result.blocks for result in results))
   with self.assertRaisesRegex(ValueError,'V3_UNKNOWN_STAGE'):r.run('legacy')

 def test_render_is_refused_until_the_content_gate_passes(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   b=Path(t);runs=b/'runs';(runs/'a').mkdir(parents=True);c=WorkspaceContext.resolve(runs,'a');(b/'t.md').write_text('项目目标。\n\n服务范围；交付成果；验收条件；工期30日。',encoding='utf-8');i=InputManifestService(c);i.register_local_file(b/'t.md',InputRole.TENDER);r=V3StageRunner.for_deterministic_tests(c)
   for stage in ('normalize_sources','build_requirement_ledger','analyze_scores','plan_response','compile_chapter_blueprint','confirm_planning'):r.run(stage)
   ControlStore(c).grant_workspace_access('owner')
   HumanGateService(c).confirm_planning(principal_id='owner',submitted_snapshot=HumanGateService(c).planning_snapshot(),nonce='render-h1')
   r.run('compile_document_contract');r.run('plan_document')
   with self.assertRaises(ControlPlaneError) as blocked:
    r.run('render_document')
   self.assertEqual(blocked.exception.code,'RENDER_BLOCKED_STALE_CONTENT')
if __name__=='__main__':unittest.main()
