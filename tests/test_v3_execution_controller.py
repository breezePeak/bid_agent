from __future__ import annotations

import asyncio,json,sys,tempfile,unittest
import io
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))

from control_plane import CommandEnvelope,CommandGateway,ControlStore,WorkspaceContext
from document_pipeline.contracts import InputRole
from document_pipeline.execution_controller import V3ExecutionController
from document_pipeline.input_manifest import InputManifestService
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder
import api.v3_app as v3_app
from fastapi import UploadFile
from fastapi.testclient import TestClient


class _Request:
 def __init__(self, body, principal=None): self.body=body;self.state=SimpleNamespace(principal=principal)
 async def json(self): return self.body


class V3ExecutionControllerTests(unittest.TestCase):
 def test_public_fastapi_routes_expose_no_v1_or_v2_workspace_contract(self):
  paths=[getattr(route,'path','') for route in v3_app.app.routes]
  self.assertIn('/api/v3/workspaces',paths)
  self.assertFalse(any(path.startswith('/api/v2/') for path in paths))

 def test_legacy_api_namespaces_return_410(self):
  with TestClient(v3_app.app) as client:
   for path in ('/api/v1/workspaces','/api/v2/workspaces/example/snapshot'):
    response=client.get(path)
    self.assertEqual(response.status_code,410)
    self.assertEqual(response.json()['error']['code'],'LEGACY_API_RETIRED')

 def test_command_gateway_is_the_only_execution_entry_point(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   base=Path(t);runs=base/'runs';(runs/'alpha').mkdir(parents=True);context=WorkspaceContext.resolve(runs,'alpha')
   tender=base/'tender.md';score=base/'score.md';tender.write_text('项目目标。\n\n服务范围；交付成果；验收条件；工期30日。',encoding='utf-8');score.write_text('评分要求：实施方案。',encoding='utf-8')
   inputs=InputManifestService(context);inputs.register_local_file(tender,InputRole.TENDER);inputs.register_local_file(score,InputRole.SCORE)
   controller=V3ExecutionController(context);gateway=CommandGateway(context,controller.handlers())
   envelope=CommandEnvelope.from_mapping({'kind':'document.run_pipeline','expected_revision':ControlStore(context).revision(),'idempotency_key':'v3-full-run'},workspace_id='alpha')
   receipt=gateway.submit(envelope)
   self.assertEqual(receipt.status,'accepted')
   self.assertTrue((context.root/'outputs/v3/final.docx').is_file())
   snapshot=V3WorkspaceSnapshotBuilder(context).build()
   # The legacy stage files are deliberately not runtime facts. Snapshot only
   # projects revisions that travelled through Proposal → Gate → Promotion.
   self.assertIsNone(snapshot['document']['mode'])
   self.assertIsNone(snapshot['document']['delivery'])
   self.assertEqual(snapshot['content_units'], [])
   stage_runs=ControlStore(context).stage_runs(str(receipt.operation_id))
   self.assertEqual({item['stage_command'] for item in stage_runs},{'ingest_inputs','normalize_sources','build_requirement_ledger','build_project_model','sync_material_requirements','compile_document_contract','plan_document','execute_content_plan','integrate_document','verify_document','render_document','verify_delivery'})

 def test_controller_rejects_unregistered_stage(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   runs=Path(t)/'runs';(runs/'alpha').mkdir(parents=True);context=WorkspaceContext.resolve(runs,'alpha');controller=V3ExecutionController(context);gateway=CommandGateway(context,controller.handlers())
   receipt=gateway.submit(CommandEnvelope.from_mapping({'kind':'document.run_stage','payload':{'stage':'legacy'},'expected_revision':0,'idempotency_key':'v3-bad-stage'},workspace_id='alpha'))
   self.assertEqual(receipt.status,'rejected')
   self.assertEqual(receipt.error['code'],'COMMAND_DISPATCH_FAILED')

 def test_v3_api_uses_the_v3_command_controller_and_snapshot(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   base=Path(t);runs=base/'runs';(runs/'alpha').mkdir(parents=True);context=WorkspaceContext.resolve(runs,'alpha');tender=base/'tender.md';score=base/'score.md';tender.write_text('项目目标。\n\n服务范围；交付成果；验收条件；工期30日。',encoding='utf-8');score.write_text('评分要求：实施方案。',encoding='utf-8');inputs=InputManifestService(context);inputs.register_local_file(tender,InputRole.TENDER);inputs.register_local_file(score,InputRole.SCORE)
   with mock.patch.object(v3_app,'RUNS_DIR',runs):
    response=asyncio.run(v3_app.command('alpha',_Request({'kind':'document.run_pipeline','expected_revision':0,'idempotency_key':'v3-api-run'})))
    self.assertTrue(json.loads(response.body)['ok'])
    snapshot=v3_app.snapshot('alpha')
   payload=json.loads(snapshot.body)
   self.assertIsNone(payload['snapshot']['document']['delivery'])
   self.assertEqual(payload['snapshot']['promoted_artifacts'], [])
   with mock.patch.object(v3_app,'RUNS_DIR',runs):
    self.assertTrue(json.loads(v3_app.latest_gate('alpha').body)['ok'])
    self.assertTrue(json.loads(v3_app.evidence('alpha').body)['ok'])
    self.assertTrue(json.loads(v3_app.events('alpha',0,200).body)['events'])

 def test_v3_upload_registers_an_explicit_input_role(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   runs=Path(t)/'runs';(runs/'alpha').mkdir(parents=True);upload=UploadFile(filename='tender.md',file=io.BytesIO('项目目标'.encode('utf-8')))
   with mock.patch.object(v3_app,'RUNS_DIR',runs):
    response=asyncio.run(v3_app.upload('alpha','tender',upload,''))
   payload=json.loads(response.body)
   self.assertTrue(payload['ok'])
   self.assertEqual(payload['input']['role'],'tender')

 def test_v3_workspace_api_excludes_legacy_workspace_layouts(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
   runs=Path(t)/'runs';(runs/'legacy'/'workspace').mkdir(parents=True);principal={'id':'owner','role':'user'}
   with mock.patch.object(v3_app,'RUNS_DIR',runs):
    created=asyncio.run(v3_app.create_workspace(_Request({'name':'V3 项目'},principal)))
    body=json.loads(created.body);self.assertTrue(body['ok'])
    listed=json.loads(v3_app.list_workspaces(_Request({},principal)).body)
   self.assertEqual([item['id'] for item in listed['workspaces']],[body['workspace']['id']])

if __name__=='__main__':unittest.main()
