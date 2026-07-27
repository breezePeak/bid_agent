from __future__ import annotations
import json, sys, tempfile, unittest
from pathlib import Path
from docx import Document
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from control_plane import WorkspaceContext
from document_pipeline.renderers.standard_renderer import StandardRenderer
class V3StandardRendererTests(unittest.TestCase):
 def test_renders_only_outline_titles_and_integrated_blocks(self):
  with tempfile.TemporaryDirectory() as tmp:
   b=Path(tmp); runs=b/'runs'; w=runs/'a'; (w/'workspace/v3/contracts').mkdir(parents=True)
   contract={'schema_version':'v3','revision':1,'source_hashes':{},'mode':'auto_outline','nodes':[{'node_id':'n1','parent_node_id':None,'order':0,'writable_target':'node:n1','title':'招标来源标题','requirement_ids':['R1']}]} 
   (w/'workspace/v3/contracts/document_contract.json').write_text(json.dumps(contract),encoding='utf-8')
   integrated={'schema_version':'v3','revision':1,'source_hashes':{},'contract_revision':1,'plan_revision':1,'blocks':[{'block_id':'b1','target_node_id':'n1','type':'paragraph','content':'正文响应','topic_ids':[],'requirement_ids':['R1'],'score_point_ids':[],'evidence_ids':[],'fact_ids':[],'confidence':1,'human_locked':False,'critical_claims':[]}]}
   (w/'workspace/v3/integrated_document.json').write_text(json.dumps(integrated),encoding='utf-8')
   output,md=StandardRenderer(WorkspaceContext.resolve(runs,'a')).render()
   self.assertTrue(output.exists()); self.assertIn('招标来源标题',md.read_text(encoding='utf-8')); self.assertIn('正文响应',Document(str(output)).paragraphs[1].text)
if __name__=='__main__': unittest.main()
