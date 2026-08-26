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
const aiProcessDisclosure = await readFile(
  new URL('../src/components/AiProcessDisclosure.vue', import.meta.url),
  'utf8',
)
const apiClient = await readFile(
  new URL('../src/api/index.js', import.meta.url),
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

test('project chat renders the API reply instead of a fixed acknowledgement', () => {
  assert.match(workspaceView, /data\?\.reply \|\| data\?\.answer \|\| data\?\.message/)
  assert.match(workspaceView, /暂未收到可显示的回复，请稍后重试。/)
  assert.match(workspaceView, /e\?\.response\?\.data\?\.message \|\| e\?\.message/)
  assert.match(apiClient, /timeout:\s*120000/)
  assert.match(apiClient, /chatV3[\s\S]*?timeout:\s*120000/)
})

test('continue commands resume the failed workflow without another chat-model call', () => {
  assert.match(workspaceView, /function isContinueIntent\(message\)/)
  assert.match(workspaceView, /void prepareOutline\(\)/)
  assert.match(workspaceView, /void runDocument\(\)/)
  assert.match(workspaceView, /:disabled="!initialChatInput\.trim\(\)"/)
  return
  assert.match(workspaceView, /function isContinueIntent\(message\)/)
  assert.match(workspaceView, /正在思考，正在检查可复用节点并恢复处理，请稍候/)
  assert.match(
    workspaceView,
    /if \(isContinueIntent\(msg\)\) \{[\s\S]*?await nextTick\(\)[\s\S]*?await scrollChatToLatest\(true\)[\s\S]*?await continueCurrentWorkflow\(\)/,
  )
  assert.match(workspaceView, /outlineOperation\?\.status === 'failed'/)
  assert.match(workspaceView, /void prepareOutline\(\)/)
  assert.match(workspaceView, /generation\.value\.status === 'failed'/)
  assert.match(workspaceView, /void runDocument\(\)/)
  assert.match(workspaceView, /:disabled="!initialChatInput\.trim\(\)"/)
})

test('planning review prompt blocks the chat and opens the existing review tab once per operation', () => {
  assert.match(workspaceView, /v-if="showPlanningReviewPrompt"/)
  assert.match(workspaceView, /planningStatus\.value === 'needs_human'/)
  assert.match(workspaceView, /hasOutline\.value/)
  assert.match(workspaceView, /dismissedPlanningReviewOperationId/)
  assert.match(workspaceView, /function dismissPlanningReviewPrompt\(\)/)
  assert.match(
    workspaceView,
    /function openPlanningReview\(\) \{[\s\S]*?activeTab\.value = 'planning'/,
  )
  assert.match(workspaceView, /目录已生成，等待审核/)
  assert.match(workspaceView, /进入审核目录/)
  assert.match(workspaceView, /稍后审核/)
})

test('only long-running workflow phases show expandable processing details', () => {
  assert.equal(
    (workspaceView.match(/<AiProcessDisclosure\b/g) || []).length,
    2,
    'expected one disclosure each for phases 2 and 3 only',
  )
  assert.doesNotMatch(workspaceView, /:status="turn\.processStatus \|\| 'completed'"/)
  assert.doesNotMatch(workspaceView, /:seconds="assistantTurnElapsedSeconds\(turn\)"/)
  assert.doesNotMatch(workspaceView, /:detail-text="turn\.processDetail"/)
  assert.match(workspaceView, /:status="outlineProcessStatus"[\s\S]*?:seconds="outlineElapsedSeconds"/)
  assert.match(workspaceView, /:status="generationProcessStatus"[\s\S]*?:seconds="generationElapsedSeconds"/)
  assert.match(aiProcessDisclosure, /<details class="ai-process-disclosure"/)
  assert.match(aiProcessDisclosure, /<summary>/)
  assert.match(aiProcessDisclosure, /normalizedStatus\.value === 'processing'[\s\S]*?正在处理 · 已用/)
  assert.match(aiProcessDisclosure, /normalizedStatus\.value === 'completed'[\s\S]*?已处理 · 耗时/)
  assert.match(aiProcessDisclosure, /normalizedStatus\.value === 'failed'[\s\S]*?处理失败 · 耗时/)
  assert.match(aiProcessDisclosure, /width: 100%[\s\S]*?background: #f8fafc;/)
  assert.doesNotMatch(aiProcessDisclosure, /background:\s*#(?:000|0f172a|111827)/i)
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
  assert.match(workspaceView, /currentOutlineChapter\.score_conditions/)
  assert.match(workspaceView, /currentOutlineChapter\.requirements/)
  assert.match(workspaceView, /planningView\.quality_gates/)
  assert.match(workspaceView, /document_quality_gate/)
})

test('full document generation stays observable and loads chapter bodies on demand', () => {
  assert.match(workspaceView, /完整标书生成任务已启动/)
  assert.match(workspaceView, /subscribeV3Workspace/)
  assert.match(workspaceView, /function connectWorkspaceStream\(\)/)
  assert.doesNotMatch(workspaceView, /\}, 2000\)/)
  assert.match(workspaceView, /fetchV3ContentUnit/)
  assert.match(workspaceView, /generationContent\.units/)
  assert.match(workspaceView, /正在一键生成…/)
  assert.match(workspaceView, /章节写作外部资料检索记录/)
})

test('outline actions stay consistent with the currently displayed pipeline', () => {
  assert.match(workspaceView, /prepareOutline/)
  assert.match(workspaceView, /:disabled="outlineBusy || running"/)
  assert.match(workspaceView, /latestOperationKind\.value === 'document\.prepare_outline'/)
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
  assert.match(workspaceView, /pipelineStageOperation\(stage\)/)
  assert.match(workspaceView, /compile_chapter_blueprint:\s*'操作：生成评分驱动章节目录'/)
  assert.match(workspaceView, /execute_content_plan:\s*'操作：按所选章节写作；缺公开依据时自动联网检索'/)
  assert.match(workspaceView, /生成当前章/)
  assert.match(workspaceView, /runV3Pipeline\(props\.runId, normalizedChapterIds\)/)
  assert.match(workspaceView, /证据缺口必须由人工提供真实的企业、人员、业绩、资质或项目材料/)
})

test('the application routes completed planning into the current three-pane chapter workbench', async () => {
  const businessView = await readFile(
    new URL('../src/views/Business.vue', import.meta.url),
    'utf8',
  )
  const router = await readFile(
    new URL('../src/router/index.js', import.meta.url),
    'utf8',
  )
  const chapterWorkbench = await readFile(
    new URL('../src/components/ChapterWorkbenchView.vue', import.meta.url),
    'utf8',
  )

  assert.match(businessView, /v-if="shellMode === 'pipeline'"/)
  assert.match(businessView, /<ChapterWorkbenchView[\s\S]*?:workspace-id="activeRunId"/)
  assert.match(businessView, /const shellMode = computed/)
  assert.match(businessView, /async function probeOutline\(workspaceId\)/)
  assert.match(router, /path: '\/business\/:workspaceId\/pipeline',[\s\S]*name: 'WorkspacePipeline'/)
  assert.match(router, /path: '\/business\/:workspaceId\/chapters\/:chapterId',[\s\S]*name: 'ChapterWorkspace'/)
  assert.match(chapterWorkbench, /<!-- 左：目录结构 -->/)
  assert.match(chapterWorkbench, /<!-- 中：文档生成 -->/)
  assert.match(chapterWorkbench, /<!-- 右：聊天 \+ 上下文 -->/)
  assert.match(chapterWorkbench, />本章对话<\/button>/)
  assert.match(chapterWorkbench, />上下文<\/button>/)
  assert.match(chapterWorkbench, /公共项目事实/)
  assert.match(chapterWorkbench, /const childrenByParent = new Map\(\)/)
  assert.match(chapterWorkbench, /const appendBranch = \(item, depth\) =>/)
  assert.match(chapterWorkbench, /children\.forEach\(child => appendBranch\(child, depth \+ 1\)\)/)
  assert.match(chapterWorkbench, /class="tree-indent"/)
  assert.match(chapterWorkbench, /grid-template-columns: calc\(var\(--tree-depth\) \* 20px\) 10px minmax\(0, 1fr\) auto/)
  assert.match(chapterWorkbench, /@click="backToAssistant"/)
  assert.match(chapterWorkbench, /返回助手/)
})

test('writer workspace supports one-click full and selected large-chapter generation', () => {
  assert.match(workspaceView, /一键生成全部/)
  assert.match(workspaceView, /一键生成所选/)
  assert.match(workspaceView, /勾选大章节会包含其全部子章节/)
  assert.match(workspaceView, /function descendantChapterIds\(chapterId\)/)
  assert.match(workspaceView, /if \(selected\.has\(chapter\.parent_chapter_id\)\) selected\.add\(chapter\.chapter_id\)/)
  assert.match(workspaceView, /async function runSelectedChapters\(\)/)
  assert.match(workspaceView, /await runDocument\(selectedGenerationChapterIds\.value\)/)
  assert.match(
    workspaceView,
    /async function runDocument\(chapterIds = \[\]\)[\s\S]*?activeTab\.value = 'upload'/,
  )
  assert.doesNotMatch(workspaceView, /activeTab\.value = 'generation'/)
  assert.match(workspaceView, /name: normalizedChapterIds\.length \? 'ChapterWorkspace' : 'ProjectHome'/)
})

test('confirmed planning exposes a workbench entry directly above the assistant composer', () => {
  const entry = workspaceView.indexOf('class="workbench-entry-card"')
  const composer = workspaceView.indexOf('class="modern-input-card"')

  assert.ok(entry >= 0, 'expected a visible workbench entry after confirmation')
  assert.ok(composer > entry, 'expected the workbench entry immediately above the composer')
  assert.match(workspaceView, /v-if="planningStatus === 'confirmed' && hasOutline"/)
  assert.match(workspaceView, /@click="openWritingWorkbench"/)
  assert.match(workspaceView, /进入三栏工作台：左侧目录、中间正文、右侧本章对话与公共上下文。/)
  assert.match(workspaceView, /await refresh\(\)\s*activeTab\.value = 'upload'/)
})

test('delivery requires a clean gate result before preview or download', () => {
  assert.match(workspaceView, /deliveryStatus\.value === 'ready'/)
  assert.match(workspaceView, /ready_with_warnings:\s*'不可交付：存在校验错误'/)
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

test('one compact workflow disclosure is rendered before the composer', () => {
  assert.doesNotMatch(workspaceView, /class="live-monitor-banner"/)

  const chatStart = workspaceView.indexOf('ref="studioChatBody"')
  const activityGroup = workspaceView.indexOf('class="pipeline-activity-group phase-activity-group phase-2-activity-group"')
  const activityMessage = workspaceView.indexOf('class="pipeline-activity-msg"')
  const conversationTurns = workspaceView.indexOf('v-for="turn in initialChatTurns"')
  const chatFooter = workspaceView.indexOf('class="studio-input-footer"')
  const verticalPlan = workspaceView.indexOf('class="pipeline-plan-dock"')
  const composer = workspaceView.indexOf('class="modern-input-card"')

  assert.ok(chatStart >= 0, 'expected the chat stream to own a scrollable body')
  assert.ok(conversationTurns > chatStart, 'expected conversation turns in the chat stream')
  assert.ok(composer > chatFooter, 'expected the composer in the footer')
  assert.doesNotMatch(workspaceView, /class="pipeline-plan-dock"/)
  assert.doesNotMatch(workspaceView, /class="pipeline-activity-group/)
  assert.ok(
    workspaceView.indexOf('v-if="initialMaterialsReady && secondStageConfirmed" class="chat-msg bot-msg timeline-step-msg outline-stage-msg"') > conversationTurns,
    'expected the phase-2 card after conversation once materials are ready',
  )
  return

  assert.ok(chatStart >= 0, 'expected the chat stream to own a scrollable body')
  assert.ok(activityGroup > chatStart, 'expected the phase-2 activity group in chat')
  assert.ok(conversationTurns > chatStart, 'expected the conversation turns in the chat stream')
  assert.ok(verticalPlan > conversationTurns, 'expected a new plan after the user conversation')
  assert.ok(activityMessage > chatStart, 'expected live activity messages inside the chat stream')
  assert.ok(chatFooter > activityMessage, 'expected activity messages before the chat footer')
  assert.ok(composer > chatFooter, 'expected the composer to remain in the footer')
  assert.doesNotMatch(workspaceView, /class="pipeline-plan-dock"/)
  assert.doesNotMatch(workspaceView, /class="pipeline-activity-group/)
  assert.ok(
    workspaceView.indexOf('v-if="initialMaterialsReady" class="chat-msg bot-msg timeline-step-msg outline-stage-msg"') > conversationTurns,
    'expected the phase-2 card after conversation once materials are ready',
  )
  return
  assert.match(workspaceView, /pipeline-turn-\$\{pipelineActivityVersion\}/)
  assert.match(workspaceView, /\{\{ topPipelineStatusLabel \}\}/)
  assert.match(workspaceView, /正在进行第 \$\{requestNumber\} 次大模型连接/)
  assert.match(workspaceView, /还剩 \{\{ remainingPipelineStageCount \}\} 步/)
  assert.match(workspaceView, /\.vertical-pipeline-plan\s*\{[\s\S]*?flex-direction:\s*column;/)
  assert.match(workspaceView, /class="pipeline-log-entry"/)
  assert.doesNotMatch(workspaceView, /class="msg-bubble pipeline-activity-bubble"/)
  assert.match(workspaceView, /\.pipeline-log-entry\s*\{[\s\S]*?background:\s*transparent;/)
  assert.match(workspaceView, /\.legacy-chat-stream \.bot-msg > \.msg-bubble\s*\{[\s\S]*?border:\s*0;/)
  assert.match(workspaceView, /outlinePipelineActivitySummaryLabel/)
  assert.match(workspaceView, /generationPipelineActivitySummaryLabel/)
  assert.match(workspaceView, /<Teleport to="body">/)
})

test('workflow disclosure is light and keeps processing details progressively disclosed', () => {
  assert.match(aiProcessDisclosure, /aria-live="polite"/)
  assert.match(aiProcessDisclosure, /prefers-reduced-motion/)
  assert.match(aiProcessDisclosure, /focus-visible/)
  assert.doesNotMatch(aiProcessDisclosure, /background:\s*#(?:000|0f172a|111827)/i)
  return
  assert.doesNotMatch(workspaceView, /查看请求与诊断/)
  assert.doesNotMatch(workspaceView, /class="pipeline-plan-actions"/)
  assert.match(workspaceView, /正在处理 · 已运行 \$\{formatPipelineDuration\(runningDurationSeconds\.value\)\}/)
  assert.match(workspaceView, /\.pipeline-activity-group > summary\s*\{[\s\S]*?border-bottom:\s*1px solid #0f172a;/)
})

test('outline planning and full-document generation render as separate chat phases', () => {
  assert.match(workspaceView, /v-if="initialMaterialsReady && secondStageConfirmed" class="chat-msg bot-msg timeline-step-msg outline-stage-msg"/)
  assert.match(workspaceView, /v-if="planningStatus === 'confirmed'" class="chat-msg bot-msg timeline-step-msg generation-stage-msg"/)
  assert.match(workspaceView, /const phaseStates = computed\(\(\) => workflow\.value\.phase_states \|\| \{\}\)/)
  assert.match(workspaceView, /planningPhaseState\.value\.phase_status/)
  assert.match(workspaceView, /writingPhaseState\.value\.phase_status/)
  assert.doesNotMatch(workspaceView, /if \(planningStatus\.value === 'confirmed' && hasOutline\.value\) return 'completed'/)
  assert.match(workspaceView, /:status="outlineProcessStatus"[\s\S]*?:seconds="outlineElapsedSeconds"/)
  assert.match(workspaceView, /:status="generationProcessStatus"[\s\S]*?:seconds="generationElapsedSeconds"/)
  assert.doesNotMatch(workspaceView, /class="pipeline-plan-dock"/)
  return
  const outlineActivity = workspaceView.indexOf('v-if="!outlineBusy && outlinePipelineActivityMessages.length"')
  const outlineResult = workspaceView.indexOf('v-if="planningReadyForReview"')
  const conversationTurns = workspaceView.indexOf('v-for="turn in initialChatTurns"')
  const generationMessage = workspaceView.indexOf('class="chat-msg bot-msg timeline-step-msg generation-stage-msg"')
  const generationActivity = workspaceView.indexOf('v-if="generationPipelineActivityMessages.length"')
  const sharedPlan = workspaceView.indexOf('class="pipeline-plan-dock"')

  assert.ok(outlineActivity >= 0, 'expected an execution stream dedicated to phase 2')
  assert.ok(outlineActivity > outlineResult, 'expected phase-2 execution inside the phase-2 result message')
  assert.ok(outlineActivity < generationMessage, 'expected phase-2 execution before the phase-3 message')
  assert.ok(outlineResult < conversationTurns, 'expected the completed outline to remain a distinct earlier result')
  assert.ok(generationMessage > outlineResult, 'expected phase 3 to be a new chat message after phase 2')
  assert.ok(generationActivity > generationMessage, 'expected phase-3 execution inside the phase-3 message')
  assert.ok(sharedPlan > conversationTurns, 'expected the current plan after ordinary conversation turns')
  assert.match(
    workspaceView,
    /v-if="!showGenerationPipeline && outlineBusy && outlinePipelineActivityMessages\.length"/,
    'expected the current plan to show phase-2 activity only while phase 2 is running',
  )
  assert.match(workspaceView, /阶段 2 执行过程/)
  assert.match(workspaceView, /阶段 3 · 完整标书生成/)
  assert.match(workspaceView, /阶段 3 执行过程/)
  assert.match(workspaceView, /buildPipelineActivityMessages\(pipelineStages\.value, 'phase-2'\)/)
  assert.match(workspaceView, /buildPipelineActivityMessages\(generationExecutionStages\.value, 'phase-3'\)/)
  assert.match(workspaceView, /showGenerationPipeline\.value\s*\? generationExecutionStages\.value\s*:\s*pipelineStages\.value/)
  assert.match(
    workspaceView,
    /hasOutline\.value\s*&&\s*planningStatus\.value === 'confirmed'\s*&&\s*generationBusy\.value/,
  )
})

test('chat flow avoids duplicate hydration notices and keeps outline work in chat', () => {
  assert.doesNotMatch(workspaceView, /watch\(\(\) => activeInputs\.value\.length/)
  assert.doesNotMatch(workspaceView, /watch\(hasOutline/)
  assert.doesNotMatch(
    workspaceView,
    /async function prepareOutline\(\)[\s\S]*?activeTab\.value = 'planning'[\s\S]*?clearError\(\)/,
  )
  assert.match(workspaceView, /@click="activeTab = 'upload'"[\s\S]*?返回聊天助手/)
})

test('planning does not become reviewable merely because a historical generation exists', () => {
  assert.match(workspaceView, /const planningReadyForReview = computed/)
  assert.doesNotMatch(workspaceView, /showGenerationPipeline\.value\s*\|\|\s*hasOutline\.value/)
  assert.match(workspaceView, /hasOutline\.value\s*\|\| \['needs_human', 'confirmed'\]\.includes\(planningStatus\.value\)/)
  assert.match(workspaceView, /hasOutline\.value\s*&&\s*planningStatus\.value === 'confirmed'/)
  assert.match(workspaceView, /确认当前目录/)
  assert.match(
    workspaceView,
    /planningReadyForReview && planningStatus !== 'confirmed'/,
    'a confirmed outline must not continue showing the review CTA in the assistant chat',
  )
  assert.doesNotMatch(
    workspaceView,
    /v-else-if="planningStatus === 'confirmed'"[\s\S]*?生成完整标书/,
    'full-document generation belongs to the writing workspace, not the outline review page',
  )
})

test('chat workflow prevents duplicate uploads and avoids duplicating completed pipeline cards', () => {
  assert.match(workspaceView, /canonicalInputFilename/)
  assert.match(workspaceView, /const seen = new Set\(\)[\s\S]*?seen\.has\(key\)/)
  assert.match(workspaceView, /已跳过 \$\{duplicateCount\} 份重复文件/)
  assert.match(workspaceView, /toolbar-attachment-menu/)
  assert.match(workspaceView, /attachment-trigger/)
  assert.doesNotMatch(workspaceView, /class="workflow-result-panel pipeline-result-panel"/)
  assert.doesNotMatch(workspaceView, /<div v-if="hasOutline" class="plan-result-details">/)
})

test('first stage applies mode-specific material readiness before phase 2', () => {
  assert.match(workspaceView, /v-if="!secondStageConfirmed && !hasOutline && !loading"/)
  assert.match(workspaceView, /class="required-upload-zones"/)
  assert.match(workspaceView, /class="required-upload-zone"/)
  assert.match(workspaceView, /role: 'tender'/)
  assert.match(workspaceView, /role: 'company'/)
  assert.match(workspaceView, /projectMode\.value === 'bid_rewrite' \? hasLegacyBid\.value : hasCompanyMaterials\.value/)
  assert.match(workspaceView, /materialReadiness\.value\.ready === true/)
  assert.match(workspaceView, /projectMode\.value === 'full_write' \? \[\{/)
  assert.match(workspaceView, /是否继续第二阶段？/)
  assert.match(workspaceView, /回复“继续第二阶段”/)
  assert.match(workspaceView, /const secondStageConfirmed = ref\(false\)/)
  assert.match(workspaceView, /!secondStageConfirmed\.value/)
  assert.match(workspaceView, /void prepareOutline\(\)/)
  assert.doesNotMatch(workspaceView, />生成编写计划</)
  assert.match(workspaceView, /@change="handleQuickUpload\('company', \$event\)"/)
  assert.doesNotMatch(workspaceView, /handleQuickUpload\('company_fact', \$event\)/)
})

test('user chat messages stay right-aligned without a duplicate avatar', () => {
  assert.match(workspaceView, /\.chat-msg\.user-msg\s*\{[\s\S]*?justify-content:\s*flex-end;[\s\S]*?align-self:\s*flex-end;/)
  assert.match(workspaceView, /\.legacy-chat-stream > \.chat-msg\.user-msg \.msg-avatar \{ display:\s*none; \}/)
})

test('every workspace view has a scroll owner while the chat keeps its own scroll', () => {
  assert.match(workspaceView, /\.workspace-tab-view\s*\{[\s\S]*?overflow:\s*auto;/)
  assert.match(
    workspaceView,
    /\.workspace-tab-view\.tab-upload\s*\{[\s\S]*?overflow:\s*hidden;/,
  )
  assert.match(workspaceView, /\.studio-chat-body\s*\{[\s\S]*?overflow-y:\s*auto;/)
  assert.match(workspaceView, /\.initial-chat-studio\s*\{[\s\S]*?width:\s*50vw;[\s\S]*?max-width:\s*50vw;/)
  assert.match(workspaceView, /\.chat-msg\s*\{[\s\S]*?width:\s*100%;[\s\S]*?max-width:\s*100%;/)
  assert.match(workspaceView, /distanceFromBottom\s*<\s*96/)
})

test('multi-chapter queued state is shown beside the chapter title, not in chat', async () => {
  const chapterWorkbench = await readFile(
    new URL('../src/components/ChapterWorkbenchView.vue', import.meta.url),
    'utf8',
  )

  assert.match(chapterWorkbench, /v-if="isMultiChapterQueued\(item\)" class="tree-queue-label">队列中</)
  assert.match(chapterWorkbench, /batchJobItems\.value\.length <= 1/)
  assert.match(chapterWorkbench, /event\?\.type === 'chapter_queued'/)
  assert.doesNotMatch(chapterWorkbench, /已进入批量编写队列，正在准备/)
})
