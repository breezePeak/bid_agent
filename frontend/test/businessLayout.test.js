import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const stylesheet = await readFile(
  new URL('../src/assets/styles/main.css', import.meta.url),
  'utf8',
)
const workspaceView = await readFile(
  new URL('../src/components/V3WorkspaceView.vue', import.meta.url),
  'utf8',
)

test('business content owns vertical scrolling inside the fixed application shell', () => {
  const rule = stylesheet.match(/\.main-content\s*\{([^}]*)\}/)

  assert.ok(rule, 'expected a .main-content layout rule')
  assert.match(rule[1], /overflow-y:\s*auto\s*;/)
  assert.match(rule[1], /overflow-x:\s*hidden\s*;/)
  assert.match(rule[1], /min-height:\s*0\s*;/)
  assert.doesNotMatch(rule[1], /overflow:\s*hidden\s*;/)
})

test('LLM request UI distinguishes transport return from candidate validation', () => {
  assert.match(workspaceView, /succeeded:\s*'接口已返回'/)
  assert.match(workspaceView, /controlled_repair:\s*'受控修复'/)
  assert.match(workspaceView, /parameters\.logical_batch_id/)
})

test('program audit warnings remain visible without presenting the product as failed', () => {
  assert.match(workspaceView, /程序审核提示（不阻塞后续流程）/)
  assert.match(workspaceView, /product\.status === 'warning'/)
  assert.match(workspaceView, /product\.warnings/)
  assert.match(workspaceView, /warning:\s*'需复核'/)
  assert.match(workspaceView, /warning_count:\s*'审核提示'/)
})

test('planning UI exposes condition traceability without relying on raw JSON', () => {
  assert.match(workspaceView, /condition\.normalized_condition \|\| condition\.text/)
  assert.match(workspaceView, /conditionRoleLabel\(condition\.condition_role\)/)
  assert.match(workspaceView, /condition\.source_excerpt/)
  assert.match(workspaceView, /condition\.source_location\.label/)
  assert.match(workspaceView, /condition\.response_units/)
  assert.match(workspaceView, /condition\.destinations/)
  assert.match(workspaceView, /chapter\.score_conditions/)
  assert.match(workspaceView, /chapter\.requirements/)
  assert.match(workspaceView, /planningView\.quality_gates/)
  assert.match(workspaceView, /document_quality_gate/)
})

test('full document generation stays observable and loads chapter bodies on demand', () => {
  assert.match(workspaceView, /完整标书生成任务已启动/)
  assert.match(workspaceView, /window\.setInterval\(\(\) => \{/)
  assert.match(workspaceView, /\}, 2000\)/)
  assert.match(workspaceView, /fetchV3ContentUnit/)
  assert.match(workspaceView, /generationContent\.units/)
  assert.match(workspaceView, /正在生成，不要重复提交/)
  assert.match(workspaceView, /章节写作外部资料检索记录/)
})

test('outline actions stay consistent with the currently displayed pipeline', () => {
  assert.match(workspaceView, /v-if="planningStatus !== 'confirmed' && hasTender"/)
  assert.match(workspaceView, /:disabled="outlineActionDisabled"/)
  assert.match(workspaceView, /latestOperationKind\.value === 'document\.prepare_outline'/)
  assert.match(workspaceView, /latestOperationKind\.value === 'document\.run_pipeline'/)
  assert.match(
    workspaceView,
    /const topPipelineStages = computed\(\(\) => \(\s*showGenerationPipeline\.value/,
  )
  assert.match(
    workspaceView,
    /const outlineBusy = computed\(\(\) => \(\s*\(running\.value && runningAction\.value === 'outline'\)\s*\|\| latestOutlineOperationBusy\.value/,
  )
  assert.match(workspaceView, /阶段 2 · 解析评分并生成目录/)
  assert.match(workspaceView, /阶段 3 · 完整标书生成/)
  assert.match(workspaceView, /generationExecutionStages\.value/)
  assert.match(workspaceView, /前置规划不会重复展示/)
  assert.match(workspaceView, /pipelineStageOperation\(stage\)/)
  assert.match(workspaceView, /compile_chapter_blueprint:\s*'操作：生成评分驱动章节目录'/)
  assert.match(workspaceView, /execute_content_plan:\s*'操作：按所选章节写作；缺公开依据时自动联网检索'/)
  assert.match(workspaceView, /只生成本章/)
  assert.match(workspaceView, /runV3Pipeline\(props\.runId, normalizedChapterIds\)/)
  assert.match(workspaceView, /证据缺口必须由人工提供真实的企业、人员、业绩、资质或项目材料/)
})

test('clicking a workflow node opens a live right-side audit trace', () => {
  assert.match(workspaceView, /@click="openStageDrawer\(stage\)"/)
  assert.match(workspaceView, /fetchV3GenerationStage\(props\.runId, normalized\)/)
  assert.match(workspaceView, /role="dialog"/)
  assert.match(workspaceView, /class="stage-drawer"/)
  assert.match(workspaceView, /stageDetail\.research_trace/)
  assert.match(workspaceView, /stageDetail\.trace_disclosure/)
  assert.match(workspaceView, /stageDetail\?\.current_writing/)
  assert.match(workspaceView, /writingPhaseLabel\(stageDetail\.current_writing\.phase/)
  assert.match(workspaceView, /trace\.decision_summary/)
  assert.match(workspaceView, /query\.question/)
  assert.match(workspaceView, /result\.answer_excerpt/)
  assert.match(workspaceView, /result\.source_url/)
  assert.match(workspaceView, /result\.used_in_bid/)
  assert.match(workspaceView, /result\.used_in_chapters/)
  assert.match(workspaceView, /contentUnitTraceLabel\(trace\.unit_status\)/)
  assert.match(workspaceView, /researchUsageLabel\(result\.usage_status\)/)
  assert.match(workspaceView, /stageDetailRequestToken/)
  assert.match(workspaceView, /closeStageDrawer\(\)/)
})
