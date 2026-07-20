<template>
  <div class="chat-panel" :class="{ narrow: narrow }">
    <div class="chat-body" ref="msgContainer">
      <div v-if="messages.length === 0 && !uploadedAll" class="chat-welcome">
        <div class="chat-welcome-icon">&#x1F4CB;</div>
        <h3>欢迎使用标书 Agent</h3>
        <p>请先上传招标文件、公司资料和标书模板，然后我会自动生成执行计划并开始投标文件生成流程。</p>
      </div>

      <!-- upload zone -->
      <div class="chat-upload-zone" v-if="!uploadedAll">
        <UploadTile label="招标文件" category="tender" :files="files.tender" @upload="onFileSelected" />
        <UploadTile label="公司资料" category="company" :files="files.company" @upload="onFileSelected" />
        <UploadTile label="标书模板" category="template" :files="files.template" @upload="onFileSelected" />
      </div>

      <!-- messages -->
      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        class="chat-msg"
        :class="{ user: msg.role === 'user', assistant: msg.role === 'assistant', system: msg.role === 'system', thinking: idx === streamingIdx, 'stage-log-msg': msg.role === 'system' && msg.kind === 'stage_log' }"
      >
        <div class="chat-msg-content">
          <template v-if="msg.role === 'system'">
            <div v-if="msg.kind === 'stage_log'" class="stage-log-block" :class="{ collapsed: msg.collapsed }">
              <div class="stage-log-header" @click="msg.collapsed = !msg.collapsed">
                <span class="stage-log-arrow">{{ msg.collapsed ? '▸' : '▾' }}</span>
                <span class="stage-log-title">{{ msg.stageLabel }} 实时日志</span>
                <span class="stage-log-count">（{{ msg.logCount }} 行）</span>
              </div>
              <pre v-if="!msg.collapsed" class="stage-log-body" :ref="el => { if (msg === activeStageLog) activeLogBodyEl = el }" v-html="formatLog(msg.content)"></pre>
            </div>
            <span v-else class="system-text" :class="{ 'system-stage': msg.kind === 'stage_duration', 'system-log': msg.kind === 'log' || msg.kind === 'run_event' }">{{ msg.content }}</span>
          </template>
          <template v-else>
            <div class="chat-msg-text">
              <div
                v-if="msg.thinking"
                class="chat-thinking"
                :class="{
                  collapsed: !msg.thinkingExpanded,
                  'is-running': !msg.content && msg.thinkingExpanded,
                  'is-done': !!msg.content
                }"
              >
                <div class="chat-thinking-header" @click="msg.thinkingExpanded = !msg.thinkingExpanded">
                  <span class="chat-thinking-arrow">{{ msg.thinkingExpanded ? '▼' : '▶' }}</span>
                  <span class="chat-thinking-spinner" v-if="!msg.content && msg.thinkingExpanded"></span>
                  <span>{{ msg.content ? '思考过程' : '正在思考' }}</span>
                  <span class="chat-thinking-badge" :class="msg.content ? 'done' : 'running'">{{ msg.content ? '已完成' : '进行中' }}</span>
                </div>
                <div v-if="msg.thinkingExpanded" class="chat-thinking-body" :ref="el => { if (el && idx === streamingIdx) el.scrollTop = el.scrollHeight }">{{ msg.thinking }}</div>
              </div>
              <div v-if="idx === streamingIdx && isStreamingEmpty" class="thinking-live">
                <span class="thinking-dots">AI 正在思考<span class="dot1">.</span><span class="dot2">.</span><span class="dot3">.</span></span>
              </div>
              <span v-else-if="msg.content" style="white-space:pre-wrap">{{ msg.content }}</span>
            </div>
            <div v-if="msg.actions && msg.actions.length" class="chat-msg-actions">
              <button
                v-for="act in msg.actions"
                :key="`${act.type || 'action'}:${act.confirmation_id || act.label}`"
                class="btn btn-sm"
                :disabled="isActionDisabled(act)"
                @click="handleAction(act, msg)"
              >{{ act.label }}</button>
            </div>
            <div
              v-if="msg.goal_id || (msg.goal && (msg.goal.goal_id || msg.goal.status))"
              class="chat-goal-badge chat-goal-link"
              title="在右侧目标面板查看详情"
              @click="emit('focus-rail', 'goal')"
            >
              <span class="chat-goal-label">目标</span>
              <span class="chat-goal-status" :class="'goal-' + ((msg.goal && msg.goal.status) || 'pending')">{{ (msg.goal && msg.goal.status) || 'pending' }}</span>
              <span v-if="msg.goal_id || (msg.goal && msg.goal.goal_id)" class="chat-goal-id">#{{ msg.goal_id || msg.goal.goal_id }}</span>
            </div>
            <div v-if="msg.supervisor_steps && msg.supervisor_steps.length" class="chat-supervisor-steps" :class="{ collapsed: !msg.stepsExpanded }">
              <div class="chat-supervisor-header" @click="msg.stepsExpanded = !msg.stepsExpanded">
                <span class="chat-supervisor-arrow">{{ msg.stepsExpanded ? '▼' : '▶' }}</span>
                <span>决策过程</span>
                <span class="chat-supervisor-count">{{ msg.supervisor_steps.length }} 步</span>
              </div>
              <div v-if="msg.stepsExpanded" class="chat-supervisor-body">
                <div v-for="(st, si) in msg.supervisor_steps" :key="si" class="chat-supervisor-step">
                  <div class="chat-supervisor-step-title">
                    <span class="step-idx">#{{ st.step || (si + 1) }}</span>
                    <span class="step-tool">{{ st.tool || 'chat' }}</span>
                    <span class="step-flag" :class="st.executed ? 'exec' : 'plan'">{{ st.executed ? '已执行' : '未执行' }}</span>
                  </div>
                  <div v-if="st.thought_summary" class="chat-supervisor-thought">{{ st.thought_summary }}</div>
                  <div v-if="st.observation" class="chat-supervisor-obs">{{ st.observation }}</div>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- valuable logs are written into chat as system messages (persisted) -->
    </div>

    <div v-if="productMode || !statusConsistent" class="chat-runtime-bar" :class="{ bad: !statusConsistent }">
      <span class="chat-runtime-mode">{{ productModeLabel || productMode || '状态' }}</span>
      <span v-if="!statusConsistent" class="chat-runtime-warn">live 状态不一致 · 以右侧办公室为准</span>
      <span v-else-if="goalLiveStatus" class="chat-runtime-goal">Goal: {{ goalLiveStatus }}</span>
    </div>

    <section
      v-if="repairJob || repairExecuting"
      class="chat-repair-card"
      :class="`repair-${repairStatus}`"
      aria-live="polite"
    >
      <div class="chat-repair-header">
        <div>
          <div class="chat-repair-title">最小修复</div>
          <div class="chat-repair-phase">{{ repairPhaseText }}</div>
        </div>
        <span class="chat-repair-status">{{ repairStatusText }}</span>
      </div>
      <div class="chat-repair-progress" role="progressbar" :aria-valuenow="repairProgress" aria-valuemin="0" aria-valuemax="100">
        <span :style="{ width: `${repairProgress}%` }"></span>
      </div>
      <div class="chat-repair-counts">
        <span>共 {{ repairCount('total_count') }} 项</span>
        <span>自动 {{ repairCount('auto_count') }}</span>
        <span>需人工 {{ repairCount('manual_count') }}</span>
        <span class="ok">已解决 {{ repairCount('resolved_count') }}</span>
        <span>剩余 {{ repairCount('remaining_count') }}</span>
        <span v-if="repairCount('failed_count')" class="bad">失败 {{ repairCount('failed_count') }}</span>
      </div>
      <div v-if="repairJob && repairJob.message" class="chat-repair-message">{{ repairJob.message }}</div>
      <div v-if="repairJob && repairJob.resume_command" class="chat-repair-resume">
        后续节点：{{ stepLabel(repairJob.resume_command) }} · {{ repairJob.resume_attempted ? '已尝试恢复' : '等待恢复' }}
      </div>
      <div v-if="repairInterrupted" class="chat-repair-hint">
        与上方聊天历史中的「目标已完成」可能不是同一时刻状态：修复任务在服务重启后被标记中断，请重新发起修复或继续流水线。
      </div>
      <div v-if="repairResultText || repairResultItems.length" class="chat-repair-result">
        <strong>修复结果</strong>
        <div v-if="repairResultText">{{ repairResultText }}</div>
        <ul v-if="repairResultItems.length">
          <li v-for="(item, ri) in repairResultItems" :key="item.issue_id || item.id || ri">{{ repairResultItemText(item, ri) }}</li>
        </ul>
      </div>
      <div v-if="repairInterrupted || repairStatus === 'failed'" class="chat-repair-actions">
        <button class="btn btn-sm btn-primary" :disabled="interactionBusy" @click="retryMinimalRepair">重新发起最小修复</button>
        <button class="btn btn-sm" :disabled="interactionBusy" @click="dismissRepairCard">收起</button>
      </div>
    </section>

    <!-- quick actions -->
    <div class="chat-quick-row" v-if="quickBtns.length">
      <button
        v-for="btn in quickBtns"
        :key="btn.label"
        class="btn btn-sm"
        :disabled="interactionBusy"
        @click="handleQuick(btn)"
      >{{ btn.label }}</button>
    </div>

    <!-- plan list above input -->
    <div v-if="showPlan || running || autoExecuting || (agentActivity && agentActivity.agents && agentActivity.agents.length)" class="chat-plan-area">
      <PlanList
        :steps="planSteps"
        :running="running"
        :executing="autoExecuting"
        :force-expand="running || autoExecuting"
        :recovery="recoveryState"
        :compliance="complianceSummary"
        @pause="pauseAutoRun"
        @preview-compliance="emit('preview', 'compliance-check')"
        @preview="emit('preview', $event)"
      />
    </div>

    <!-- input -->
    <div class="chat-input-area">
      <div class="chat-tags" v-if="tags.length">
        <span v-for="tag in tags" :key="tag.id" class="chat-tag">
          <span class="chat-tag-line">@L{{ tag.line }}</span>
          <span class="chat-tag-text">{{ tag.preview }}</span>
          <button class="chat-tag-remove" @click="removeTag(tag.id)">&times;</button>
        </span>
      </div>
      <div class="chat-input-row">
        <button class="btn btn-sm btn-icon" :disabled="interactionBusy" @click="openChatFile" title="上传文件">&#x1F4CE;</button>
        <textarea
          v-model="input"
          class="chat-input"
          placeholder="输入问题或指令，Enter 发送..."
          rows="3"
          :disabled="interactionBusy"
          @keydown.enter.exact.prevent="submit"
        ></textarea>
        <input type="file" ref="chatFileInput" hidden multiple @change="onChatFileSelected" />
        <button class="btn btn-sm btn-primary" @click="submit" :disabled="(!input.trim() && !tags.length) || interactionBusy">发送</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import PlanList from './PlanList.vue'
import UploadTile from './UploadTile.vue'
import { fetchChatMessages, saveChatMessage, orchestrateChat, fetchExportPreflight, fetchCurrentRepairJob, fetchMaterialsChecklist } from '../api'
import { pushStatusSnapshot, forceRuntimeRefresh } from '../composables/useWorkspaceRuntime'

const props = defineProps({
  runId: { type: String, required: true },
  narrow: { type: Boolean, default: false },
})

function formatLog(content) {
  if (!content) return ''
  let html = content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  html = html.replace(/\[系统\]/g, '<span class="log-tag log-tag-system">[系统]</span>')
  html = html.replace(/\[完成\]/g, '<span class="log-tag log-tag-success">[完成]</span>')
  html = html.replace(/\[LLM\]/g, '<span class="log-tag log-tag-llm">[LLM]</span>')
  html = html.replace(/\[错误\]/g, '<span class="log-tag log-tag-error">[错误]</span>')
  html = html.replace(/\[重试\]/g, '<span class="log-tag log-tag-warn">[重试]</span>')
  html = html.replace(/\[警告\]/g, '<span class="log-tag log-tag-warn">[警告]</span>')
  html = html.replace(/\[跳过\]/g, '<span class="log-tag log-tag-info">[跳过]</span>')
  html = html.replace(/\[启动\]/g, '<span class="log-tag log-tag-info">[启动]</span>')
  return html
}

const emit = defineEmits(['preview', 'open-doc-editor', 'rewrite-done', 'pipeline-log', 'focus-rail', 'materials-alert'])

function coreWorkflowSteps(workflow = []) {
  return (Array.isArray(workflow) ? workflow : []).filter(step => step && step.kind !== 'utility' && step.command)
}

function stepLabel(command) {
  const hit = planSteps.value.find(s => s.command === command)
  return hit?.label || command
}

function workflowCommands() {
  return planSteps.value.map(s => s.command)
}

function syncPlanStepsFromWorkflow(workflow, timings = {}) {
  const core = coreWorkflowSteps(workflow)
  if (!core.length) return
  const prevByCommand = Object.fromEntries(planSteps.value.map(s => [s.command, s]))
  planSteps.value = core.map(step => {
    const command = step.command
    const prev = prevByCommand[command] || {}
    let status = prev.status || 'pending'
    if (step.done) status = 'done'
    else if (step.state === 'running') status = 'running'
    else if (step.state === 'recovering' || step.state === 'retrying') status = step.state
    else if (step.state === 'error') status = 'error'
    else if (!prev.status) status = 'pending'
    const timing = timings[command]
    const durationLabel = (timing && typeof timing === 'object' && timing.duration_label)
      ? timing.duration_label
      : (prev.durationLabel || '')
    return {
      command,
      label: step.label || prev.label || command,
      status,
      message: step.message || prev.message || '',
      durationLabel,
    }
  })
}

const messages = ref([])
const input = ref('')
const sending = ref(false)
const chatFileInput = ref(null)
const msgContainer = ref(null)

const files = reactive({ tender: [], company: [], template: [] })
const planSteps = ref([])
/** Files chosen via paperclip before user picks category (upload_batch). */
const pendingChatFiles = ref([])

const prevStatusMap = reactive({})
let messagesLoaded = false

const running = ref(false)
const autoExecuting = ref(false)
const autoStarted = ref(false)
const showPlan = ref(false)
let sseSource = null; let statusTimer = null
let tagSeq = 0

const repairExecuting = ref(false)
const repairJob = ref(null)
let repairTimer = null
let repairPollInFlight = false
let repairPollStarting = null
let activeRepairJobId = ''
let terminalRepairHandledId = ''

const tags = ref([])
const streamingIdx = ref(-1)
const isStreamingEmpty = ref(true)
const activeStageLog = ref(null)
let activeLogBodyEl = null

const uploadedAll = computed(() => files.tender.length > 0 && files.company.length > 0 && files.template.length > 0)
const planDone = computed(() => planSteps.value.length > 0 && planSteps.value.every(s => s.status === 'done'))
const docxReady = ref(false)
const recoveryState = ref(null)
const agentActivity = ref(null)
const complianceSummary = ref(null)
const issuesSummary = ref(null)
const materialsDeferred = ref(0)
const productMode = ref('')
const productModeLabel = ref('')
const statusConsistent = ref(true)
const goalLiveStatus = ref('')
let lastMaterialsNotifyKey = ''
const interactionBusy = computed(() => sending.value || repairExecuting.value)
const ACTIVE_REPAIR_STATUSES = new Set(['running', 'revalidating'])
const TERMINAL_REPAIR_STATUSES = new Set(['completed', 'partial', 'failed'])

const repairStatus = computed(() => {
  const status = String(repairJob.value?.status || '').trim()
  return status || (repairExecuting.value ? 'running' : 'awaiting_confirmation')
})
const repairStatusText = computed(() => ({
  awaiting_confirmation: '等待确认',
  running: '修复中',
  revalidating: '重验中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
}[repairStatus.value] || repairStatus.value || '准备中'))
const repairInterrupted = computed(() => {
  const phase = String(repairJob.value?.phase || '').trim()
  const msg = String(repairJob.value?.message || '')
  return phase === 'interrupted' || msg.includes('服务重启中断')
})
const repairPhaseText = computed(() => {
  const phase = String(repairJob.value?.phase || '').trim()
  const labels = {
    awaiting_confirmation: '等待用户确认',
    analyzing: '正在分析并合并根因',
    analysis: '正在分析并合并根因',
    edit: '正在执行根因修复',
    executing: '正在执行最小修复',
    repairing: '正在执行最小修复',
    revalidate: '正在重验修复结果',
    revalidating: '正在重验修复结果',
    resuming: '正在恢复后续节点',
    completed: '修复与重验已完成',
    partial: '部分问题仍需处理',
    declined: '已选择暂不修复',
    failed: '修复任务失败',
    interrupted: '修复任务已中断',
  }
  if (phase) return labels[phase] || phase
  return repairStatus.value === 'revalidating' ? '正在重验修复结果' : '正在准备修复任务'
})
const repairProgress = computed(() => {
  const persisted = Number(repairJob.value?.progress_percent)
  if (Number.isFinite(persisted)) return Math.max(0, Math.min(100, Math.round(persisted)))
  const total = repairNumber(repairJob.value?.total_count)
  if (!total) return TERMINAL_REPAIR_STATUSES.has(repairStatus.value) ? 100 : 0
  const remainingValue = repairJob.value?.remaining_count
  const processed = remainingValue !== undefined && remainingValue !== null
    ? total - repairNumber(remainingValue)
    : repairNumber(repairJob.value?.resolved_count) + repairNumber(repairJob.value?.failed_count)
  return Math.max(0, Math.min(100, Math.round((processed / total) * 100)))
})
const repairResultText = computed(() => {
  const result = repairJob.value?.result
  if (!result) return ''
  if (typeof result === 'string') return result
  if (typeof result !== 'object') return String(result)
  return String(result.message || result.summary || '').trim()
})
const repairResultItems = computed(() => {
  const items = repairJob.value?.result?.results
  return Array.isArray(items) ? items.slice(0, 8) : []
})

function repairNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) && n >= 0 ? n : 0
}

function repairCount(field) {
  return repairNumber(repairJob.value?.[field])
}

function repairResultItemText(item, index) {
  if (!item || typeof item !== 'object') return String(item || `第 ${index + 1} 项`)
  const id = item.issue_id || item.id || item.plan?.issue_id || `第 ${index + 1} 项`
  const detail = item.message || item.summary || item.final_status || (item.ok === false ? '修复失败' : '已处理')
  return `${id}：${detail}`
}

function actionConfirmationId(action) {
  return String(action?.confirmation_id || action?.params?.confirmation_id || '').trim()
}

function isActionDisabled(action) {
  if (interactionBusy.value || action?.consumed) return true
  const confirmationId = actionConfirmationId(action)
  if (!confirmationId || !repairJob.value) return false
  return confirmationId === String(repairJob.value.confirmation_id || '')
    && repairStatus.value !== 'awaiting_confirmation'
}
const quickBtns = computed(() => {
  if (!uploadedAll.value) return []
  if (planDone.value || docxReady.value) {
    const btns = [
      { label: '出稿前检查', type: 'export_preflight' },
      { label: '下载 Word', action: 'download-docx' },
      { label: '预览 Word', action: 'doc-editor' },
      { label: '查看全文审核', type: 'show_step', command: 'global-review' },
    ]
    if (complianceSummary.value) {
      btns.splice(2, 0, { label: complianceSummary.value.blocking ? '合规阻断详情' : '查看专项合规', type: 'show_step', command: 'compliance-check' })
    }
    btns.push({ label: '材料清单', type: 'show_step', command: 'build-materials-checklist' })
    btns.push({ label: '流水线日志', type: 'show_step', command: 'logs' })
    return btns
  }
  const stepStatus = (cmd) => planSteps.value.find(s => s.command === cmd)?.status
  const failedStep = planSteps.value.find(s => s.status === 'error')
  if (failedStep) return [
    { label: '当前状态', action: 'chat' },
    { label: `重试 "${failedStep.label}"`, type: 'retry_stage', command: failedStep.command },
    { label: `跳过 "${failedStep.label}"`, type: 'skip_stage', command: failedStep.command },
    { label: '诊断错误', action: 'chat' },
  ]
  const btns = [
    { label: '当前状态', action: 'chat' },
    { label: '评分覆盖', action: 'chat', text: '当前评分覆盖率如何' },
    { label: '材料清单', type: 'show_step', command: 'build-materials-checklist' },
  ]
  const nextPending = planSteps.value.find(s => s.status !== 'done')
  if (nextPending) {
    if (nextPending.command === 'review-fix-all') {
      btns.push({ label: '派发审核改稿', type: 'dispatch_review' })
    } else if (nextPending.command === 'global-review') {
      btns.push({ label: '定向改稿', type: 'dispatch_rewrite' })
      btns.push({ label: '全文审核', type: 'global_review' })
    } else {
      btns.push({ label: `执行 ${nextPending.label}`, type: 'run_command', command: nextPending.command })
    }
  }
  btns.push({ label: '评分覆盖', action: 'chat' })
  return btns
})

function addMessage(role, content, actions = [], opts = {}) {
  const persist = opts.persist !== false
  const kind = opts.kind || (role === 'system' ? 'system' : 'message')
  messages.value.push({
    role,
    content,
    actions,
    thinking: opts.thinking || '',
    thinkingExpanded: opts.thinkingExpanded === true || (!!opts.thinking && opts.thinkingExpanded !== false) || (!content && !!opts.thinking),
    created_at: opts.created_at || '',
    kind,
    supervisor_steps: Array.isArray(opts.supervisor_steps) ? opts.supervisor_steps : [],
    stepsExpanded: opts.stepsExpanded !== false && Array.isArray(opts.supervisor_steps) && opts.supervisor_steps.length > 0,
    goal: opts.goal && typeof opts.goal === 'object' ? opts.goal : null,
    goal_id: opts.goal_id || (opts.goal && opts.goal.goal_id) || '',
  })
  if (persist) {
    saveChatMessage(role, content, { actions, kind }).catch(e => console.error('保存消息失败', e))
  }
  nextTick(scrollBottom)
}
function scrollBottom() { if (msgContainer.value) msgContainer.value.scrollTop = msgContainer.value.scrollHeight }

// ---- upload ----
function onFileSelected(category, fileList) { uploadFiles(category, fileList) }
async function uploadFiles(category, fileList) {
  const fd = new FormData(); fd.append('category', category)
  for (const f of fileList) fd.append('files', f)
  try {
    const r = await fetch(`/api/upload?category=${encodeURIComponent(category)}`, { method: 'POST', body: fd }).then(r => r.json())
    if (r.ok) {
      files[category] = r.saved || []
      addMessage('system', `已上传: ${files[category].join(', ')}`)
      if (uploadedAll.value) { showPlan.value = true; loadStatus().then(maybeAutoStart) }
    }
  } catch (e) { addMessage('system', `上传失败: ${e.message}`) }
}
function openChatFile() { if (chatFileInput.value) chatFileInput.value.click() }
function onChatFileSelected(e) {
  const list = e.target.files ? Array.from(e.target.files) : []
  e.target.value = ''
  if (!list.length) return
  pendingChatFiles.value = list
  addMessage('assistant', `已选择 ${list.length} 个文件，请选择类别：`, [
    { type: 'upload_batch', category: 'tender', label: '招标文件' },
    { type: 'upload_batch', category: 'company', label: '公司资料' },
    { type: 'upload_batch', category: 'template', label: '标书模板' },
  ], { persist: false })
}

// ---- status ----
function isRepairTaskName(value) {
  return /(minimal[-_ ]?repair|repair[-_ ]?issues?|最小修复|修复问题)/i.test(String(value || ''))
}

function stopRepairPolling() {
  if (repairTimer) clearInterval(repairTimer)
  repairTimer = null
}

function dismissRepairCard() {
  stopRepairPolling()
  repairJob.value = null
  repairExecuting.value = false
  activeRepairJobId = ''
}

function retryMinimalRepair() {
  // Must use a phrase mapped to repair intent "start" (not Supervisor goal loop)
  dismissRepairCard()
  send('重新发起最小修复', {
    action: { type: 'restart_minimal_repair' },
  })
}

function applyRepairJob(job) {
  if (!job || typeof job !== 'object' || Array.isArray(job)) return false
  const incomingId = String(job.job_id || '').trim()
  const previous = repairJob.value
  const previousId = String(previous?.job_id || '').trim()
  repairJob.value = previous && (!incomingId || !previousId || incomingId === previousId)
    ? { ...previous, ...job }
    : { ...job }
  activeRepairJobId = incomingId || previousId || activeRepairJobId
  autoStarted.value = true

  const status = String(repairJob.value?.status || '').trim()
  repairExecuting.value = ACTIVE_REPAIR_STATUSES.has(status)
  if (TERMINAL_REPAIR_STATUSES.has(status)) stopRepairPolling()
  nextTick(scrollBottom)
  return true
}

async function refreshCurrentRepairJob(expectedJobId = '') {
  if (repairPollInFlight) return
  repairPollInFlight = true
  try {
    const resp = await fetchCurrentRepairJob()
    const body = resp && resp.data ? resp.data : {}
    const job = body.repair_job || body.job || null
    const returnedId = String(job?.job_id || '').trim()
    if (job && (!expectedJobId || !returnedId || returnedId === expectedJobId)) {
      applyRepairJob(job)
      if (TERMINAL_REPAIR_STATUSES.has(String(job.status || '')) && terminalRepairHandledId !== returnedId) {
        terminalRepairHandledId = returnedId
        await loadChatHistory()
        await loadStatus()
        if (running.value && !autoExecuting.value && !repairExecuting.value) {
          autoExecuting.value = true
          autoStarted.value = true
          watchLiveRun()
        }
      }
    }
  } catch (_) {
    // Keep the last known job visible; the next poll or /api/status can recover.
  } finally {
    repairPollInFlight = false
  }
}

async function beginRepairTracking(jobId = '', initialJob = null) {
  if (initialJob) applyRepairJob(initialJob)
  activeRepairJobId = String(jobId || initialJob?.job_id || activeRepairJobId || '').trim()
  if (TERMINAL_REPAIR_STATUSES.has(repairStatus.value)) return
  repairExecuting.value = true
  autoExecuting.value = false
  autoStarted.value = true
  if (statusTimer) clearInterval(statusTimer)
  statusTimer = null
  closeSSE()

  if (repairPollStarting) return repairPollStarting
  repairPollStarting = (async () => {
    stopRepairPolling()
    await refreshCurrentRepairJob(activeRepairJobId)
    if (repairExecuting.value) {
      repairTimer = setInterval(() => refreshCurrentRepairJob(activeRepairJobId), 1500)
    }
  })().finally(() => { repairPollStarting = null })
  return repairPollStarting
}

async function loadStatus() {
  try {
    // Prefer shared runtime bus so office / goal / repair stay in lockstep
    const data = await forceRuntimeRefresh()
    if (data) updateFromStatus(data)
    else {
      const raw = await fetch('/api/status').then(r => r.json())
      updateFromStatus(raw)
    }
  } catch (e) { /* */ }
}
function updateFromStatus(data) {
  if (!data || typeof data !== 'object') return
  // Publish to shared bus for GoalPanel / Workbench
  try { pushStatusSnapshot(data) } catch (_) { /* */ }
  if (data.repair_job) applyRepairJob(data.repair_job)
  const hasActiveRepairJob = ACTIVE_REPAIR_STATUSES.has(String(data.repair_job?.status || repairJob.value?.status || ''))
  const repairTaskRunning = hasActiveRepairJob || (!!data.running && isRepairTaskName(data.current_task))
  if (repairTaskRunning) {
    repairExecuting.value = true
    autoExecuting.value = false
    if (statusTimer) clearInterval(statusTimer)
    statusTimer = null
    closeSSE()
    if (!repairTimer && !repairPollStarting) {
      void beginRepairTracking(data.repair_job?.job_id || repairJob.value?.job_id || '', data.repair_job || null)
    }
  }
  running.value = !!data.running && !repairTaskRunning
  if (!data.workflow) return
  const wf = data.workflow || []
  const timings = (data.timings && typeof data.timings === 'object') ? data.timings : {}
  const before = Object.fromEntries(planSteps.value.map(s => [s.command, s.status]))
  syncPlanStepsFromWorkflow(wf, timings)
  planSteps.value.forEach(s => {
    const prev = before[s.command] || prevStatusMap[s.command]
    if (prev === 'running' && s.status === 'done' && messagesLoaded) {
      if (activeStageLog.value && activeStageLog.value.stageLabel === s.label) collapseActiveStageLog()
      const dur = s.durationLabel || ''
      addMessage('system', `✓ ${s.label} 完成${dur ? '（用时 ' + dur + '）' : ''}`, [], { kind: 'stage_duration' })
      // 材料清单阶段完成后立刻检查待补项
      if (s.command === 'build-materials-checklist') {
        nextTick(() => maybeNotifyMaterialsGap({ force: true }))
      }
    }
    prevStatusMap[s.command] = s.status
  })
  if (data.agent_activity) agentActivity.value = data.agent_activity
  productMode.value = data.product_mode || data.runtime?.product_mode || ''
  productModeLabel.value = data.product_mode_label || data.runtime?.product_mode_label || ''
  statusConsistent.value = data.consistent !== false && data.runtime?.consistent !== false
  goalLiveStatus.value = String(data.goal?.status || data.goal_full?.status || data.runtime?.stores?.goal?.status || '')
  if (data.sources) {
    if (data.sources.tender?.length) files.tender = data.sources.tender.map(f => f.name || f)
    if (data.sources.company?.length) files.company = data.sources.company.map(f => f.name || f)
    if (data.sources.template?.length) files.template = data.sources.template.map(f => f.name || f)
    if (uploadedAll.value) { showPlan.value = true; maybeAutoStart() }
  }
  const outputs = data.outputs || {}
  docxReady.value = !!(outputs.final_docx || outputs.final_md)
  recoveryState.value = data.recovery || null
  complianceSummary.value = (data.compliance_summary && typeof data.compliance_summary === 'object')
    ? data.compliance_summary
    : null
  issuesSummary.value = (data.issues_summary && typeof data.issues_summary === 'object')
    ? data.issues_summary
    : null
  if (data.recovery_resolved) {
    const note = '✓ LLM 已恢复，当前阶段正在正常继续执行。'
    const recoveryMessage = [...messages.value].reverse().find(m =>
      typeof m.content === 'string' &&
      (m.content.includes('系统正在自动重试') || m.content.includes('正在尝试自主修复'))
    )
    if (recoveryMessage && !recoveryMessage.content.includes(note)) {
      recoveryMessage.content += `\n\n${note}`
    }
  }
  const pipelineStatus = data.pipeline?.status || ''
  if (pipelineStatus === 'complete' && autoExecuting.value) {
    autoExecuting.value = false
    clearInterval(statusTimer); closeSSE()
    addMessage('assistant', '全部流程已完成！可以编辑文档或下载。', [{ type: 'show_doc_editor', label: '文档编辑' }])
  } else if (['failed', 'paused'].includes(pipelineStatus) && autoExecuting.value) {
    autoExecuting.value = false
    clearInterval(statusTimer); closeSSE()
    const detail = data.pipeline?.error || data.pipeline?.message || ''
    addMessage('assistant', `后端流水线已${pipelineStatus === 'paused' ? '暂停' : '停止'}${detail ? '：' + detail : ''}`)
  }
  // 状态轮询时同步材料待补提醒（去重）
  void maybeNotifyMaterialsGap()
}

function applyMaterialsStatus(payload = {}) {
  const n = Number(payload.deferred || 0) || 0
  materialsDeferred.value = n
  emit('materials-alert', { deferred: n, total: Number(payload.total || 0) || 0, exists: !!payload.exists })
}

async function maybeNotifyMaterialsGap({ force = false } = {}) {
  try {
    const { data } = await fetchMaterialsChecklist()
    if (!data?.ok || !data.exists) return
    const summary = data.summary || data.checklist?.summary || {}
    const deferred = Number(summary.deferred || 0) || 0
    const items = Array.isArray(data.items) ? data.items : []
    applyMaterialsStatus({ deferred, total: summary.total || 0, exists: true })
    if (deferred <= 0) {
      lastMaterialsNotifyKey = ''
      return
    }
    const key = `${props.runId}:${deferred}`
    if (!force && key === lastMaterialsNotifyKey) return
    lastMaterialsNotifyKey = key
    const samples = items
      .filter(i => (i.response_status || 'deferred') === 'deferred')
      .slice(0, 5)
      .map(i => `· ${i.item_id || ''} ${String(i.requirement || '').slice(0, 48)}`.trim())
    const more = deferred > samples.length ? `\n…另有 ${deferred - samples.length} 条` : ''
    addMessage(
      'assistant',
      `检测到 ${deferred} 条材料待补充。请在右侧「材料」页处理，或上传公司资料后重建清单。\n\n${samples.join('\n')}${more}`,
      [
        { type: 'show_step', command: 'build-materials-checklist', label: '打开材料清单' },
        { type: 'show_step', command: 'logs', label: '查看日志' },
      ],
      { persist: false },
    )
    emit('focus-rail', 'materials')
  } catch (e) { /* ignore */ }
}

/** 供父组件推送材料角标状态；聊天提醒只在 maybeNotifyMaterialsGap 里发，避免双通道刷屏 */
function notifyMaterialsStatus(payload) {
  applyMaterialsStatus(payload || {})
  const deferred = Number(payload?.deferred || 0) || 0
  if (deferred <= 0) lastMaterialsNotifyKey = ''
}

// ---- auto run ----
function maybeAutoStart() {
  if (!uploadedAll.value) return
  if (autoStarted.value || autoExecuting.value || running.value || repairExecuting.value) return
  if (planDone.value) return
  autoStarted.value = true
  showPlan.value = true
  addMessage('assistant', '资料已齐全，自动开始执行流程。每完成一个阶段会自动推进到下一个，无需手动点击。可随时输入「暂停」或提问。')
  nextTick(() => startAutoRun())
}
async function startAutoRun(fromCommand = null) {
  if (autoExecuting.value || sending.value || repairExecuting.value) return
  autoExecuting.value = true
  autoStarted.value = true
  addMessage('system', fromCommand ? `从 ${stepLabel(fromCommand)} 继续后端流水线...` : '启动后端自动流水线...')
  connectSSE()
  if (statusTimer) clearInterval(statusTimer)
  statusTimer = setInterval(loadStatus, 2000)
  try {
    const resp = await fetch('/api/start-pipeline', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: props.runId, start_command: fromCommand || '' }),
    })
    const body = await resp.json()
    // 409 通常表示聊天编排器已经在后端启动了同一条流水线，此时继续观察即可。
    if (!body.ok) {
      // 409 + gate: quality block; 409 without gate: already running
      if (resp.status === 409 && body.gate && body.gate.can_proceed === false) {
        autoExecuting.value = false; clearInterval(statusTimer); closeSSE()
        addMessage('system', body.message || '质量门禁阻断，请先处理问题再继续')
        return
      }
      if (resp.status !== 409) {
        autoExecuting.value = false; clearInterval(statusTimer); closeSSE()
        addMessage('system', `流水线启动失败: ${body.message || ''}`)
      }
    }
  } catch (e) {
    autoExecuting.value = false; clearInterval(statusTimer); closeSSE()
    addMessage('system', `流水线启动请求失败: ${e.message}`)
  }
}
function pauseAutoRun() {
  autoExecuting.value = false; clearInterval(statusTimer); closeSSE()
  fetch('/api/pause-run', { method: 'POST' }); addMessage('system', '流程已暂停')
}
function skipFailedStage(failedCmd) {
  const commands = workflowCommands()
  const idx = commands.indexOf(failedCmd)
  if (idx < 0) return
  addMessage('system', `已跳过 "${stepLabel(failedCmd)}"，继续下一步...`)
  const nextCommand = commands[idx + 1]
  if (nextCommand) startAutoRun(nextCommand)
}
async function runCommand(cmd) {
  try {
    const r = await fetch('/api/run-command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: cmd, run_id: props.runId }) }).then(r => r.json())
    if (!r.ok) { addMessage('system', `执行失败: ${r.message || cmd}`); autoExecuting.value = false }
  } catch (e) { addMessage('system', `执行请求失败: ${e.message}`); autoExecuting.value = false }
}

// ---- SSE ----
// 详细日志分流到右侧「日志」；聊天仅保留失败/门禁等强信号。
const CHAT_LOG_RE = /(失败|错误|质量门禁|阻断|✗|Exception|Traceback|启动失败)/i
const VALUABLE_LOG_RE = /(失败|错误|重试|质量门禁|SubAgent|warn|启动|完成|执行|章节|并发|生成|写作|审核|改稿|进度|LLM|开始|成功|跳过|警告|✗)/i
let lastLogLine = ''

function currentStageLabel() {
  const runningStep = planSteps.value.find(s => s.status === 'running')
  if (runningStep) return runningStep.label
  return '执行'
}

function collapseActiveStageLog() {
  const m = activeStageLog.value
  if (!m) return
  m.collapsed = true
  activeStageLog.value = null
  activeLogBodyEl = null
}

function pushValuableLog(line, kind = 'log') {
  const text = String(line || '').trim()
  if (!text || text === lastLogLine) return
  lastLogLine = text
  const label = currentStageLabel()
  emit('pipeline-log', { line: text, stage: label, kind })
  if (!CHAT_LOG_RE.test(text)) return
  let m = activeStageLog.value
  if (!m || m.stageLabel !== label) {
    if (m) collapseActiveStageLog()
    m = { role: 'system', kind: 'stage_log', content: text, stageLabel: label, collapsed: true, logCount: 1, thinking: '', thinkingExpanded: false, created_at: '', actions: [] }
    messages.value.push(m)
    activeStageLog.value = m
  } else {
    m.content = m.content ? m.content + '\n' + text : text
    m.logCount = (m.logCount || 0) + 1
  }
  nextTick(() => {
    scrollBottom()
    if (activeLogBodyEl) activeLogBodyEl.scrollTop = activeLogBodyEl.scrollHeight
  })
}

function connectSSE() {

  closeSSE()
  sseSource = new EventSource('/api/logs/stream')
  sseSource.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data)
      if (!d || !d.line) return
      const line = String(d.line)
      if (d.type === 'run_event') {
        const et = (d.event && d.event.event_type) || ''
        // success 由 status 轮询给出带用时的更漂亮消息，这里避免重复
        if (et === 'success') return
        pushValuableLog(line, 'run_event')
      } else {
        if (VALUABLE_LOG_RE.test(line)) pushValuableLog(line, 'log')
      }
    } catch (_) { /* */ }
  }
  sseSource.onerror = () => {
    closeSSE()
    setTimeout(() => { if (autoExecuting.value || running.value) connectSSE() }, 2000)
  }
}
function closeSSE() { if (sseSource) { sseSource.close(); sseSource = null } }

// ---- chat ----
function handleQuick(btn) {
  if (interactionBusy.value) return
  if (btn && btn.text) { send(btn.text); return }

  if (btn.type) {
    handleAction({
      type: btn.type,
      label: btn.label,
      command: btn.command,
      category: btn.category,
      ...btn,
    })
    return
  }
  if (btn.action === 'download-docx') {
    window.open('/api/download/final-docx', '_blank')
    addMessage('system', '正在下载 Word 文档...')
    return
  }
  if (btn.action === 'doc-editor') {
    emit('open-doc-editor')
    return
  }
  send(btn.label)
}
function isRewriteRequest(text) {
  return /@L\d+\s/.test(String(text || ''))
}

function send(msg, { action = null } = {}) {
  if (interactionBusy.value) return false
  const text = String(msg || '').trim()
  if (!text && !action) return false
  // The orchestrator persists both sides atomically. Direct block rewrites still use the legacy chat store.
  addMessage('user', text, [], { persist: !action && isRewriteRequest(text) })
  void doChat(text, { action })
  return true
}
function submit() {
  const t = input.value.trim()
  if ((!t && !tags.value.length) || interactionBusy.value) return
  let msg = ''
  if (tags.value.length) {
    const tagRefs = tags.value.map(tag => `@L${tag.line} ${tag.preview}`).join('\n')
    msg = tagRefs + (t ? '\n\n' + t : '')
    tags.value = []
  } else {
    msg = t
  }
  if (!msg) return
  input.value = ''
  send(msg)
}
async function doChat(text, { action = null } = {}) {
  if (sending.value || repairExecuting.value) return
  sending.value = true
  const match = !action && text.match(/@L(\d+)\s/)
  if (match) {
    const lineNumber = parseInt(match[1])
    const instruction = text.replace(/@L\d+\s*/g, '').trim()
    if (instruction) {
      await doRewriteBlock(lineNumber, instruction)
      sending.value = false
      return
    }
  }

  // 先插入“思考中”气泡，避免页面长时间无反馈
  addMessage('assistant', '', [], {
    persist: false,
    thinking: '正在理解你的意图，并查询当前工作区状态…',
    thinkingExpanded: true,
  })
  const msgIndex = messages.value.length - 1
  streamingIdx.value = msgIndex
  isStreamingEmpty.value = true
  if (messages.value[msgIndex]) {
    messages.value[msgIndex].thinkingExpanded = true
    messages.value[msgIndex].thinking = '正在理解你的意图，并查询当前工作区状态…'
  }
  await nextTick()
  scrollBottom()

  const thinkLines = [
    '正在理解你的意图，并查看当前工作区…',
    '正在分析并决定下一步…',
    '正在整理回复，请稍候…',
  ]
  let thinkStep = 0
  const thinkTimer = setInterval(() => {
    thinkStep = Math.min(thinkStep + 1, thinkLines.length - 1)
    if (messages.value[msgIndex]) {
      messages.value[msgIndex].thinking = thinkLines[thinkStep]
      if (thinkStep === thinkLines.length - 1) {
        messages.value[msgIndex].thinking += `\n（已等待处理，若较慢通常是 LLM 或长任务）`
      }
    }
    nextTick(scrollBottom)
  }, 1800)

  try {
    const resp = await orchestrateChat(text, { action })
    const body = resp && resp.data ? resp.data : {}
    clearInterval(thinkTimer)
    streamingIdx.value = -1
    isStreamingEmpty.value = false

    if (body.ok === false) {
      if (messages.value[msgIndex]) {
        messages.value[msgIndex].content = body.message || '无法获取响应'
        messages.value[msgIndex].thinking = (messages.value[msgIndex].thinking || '') + '\n请求结束：未能完成回复。'
        messages.value[msgIndex].thinkingExpanded = false
      }
      sending.value = false
      return
    }

    const assistantPayload = body.assistant && typeof body.assistant === 'object' ? body.assistant : null
    const reply = typeof body.assistant === 'string'
      ? body.assistant
      : (assistantPayload?.content || assistantPayload?.reply || assistantPayload?.message || assistantPayload?.text || body.reply || body.message || '')
    const actions = Array.isArray(body.actions)
      ? body.actions
      : (Array.isArray(assistantPayload?.actions) ? assistantPayload.actions : [])
    const supervisor_steps = Array.isArray(body.supervisor_steps)
      ? body.supervisor_steps
      : (Array.isArray(assistantPayload?.supervisor_steps) ? assistantPayload.supervisor_steps : [])
    const goalPayload = body.goal || assistantPayload?.goal
    const goal = goalPayload && typeof goalPayload === 'object' ? goalPayload : null
    const goal_id = body.goal_id || assistantPayload?.goal_id || ''

    // 优先展示模型真实思考过程；否则回退到决策轨迹摘要
    const modelThinking = String(body.thinking || body.reasoning || assistantPayload?.thinking || '').trim()
    let thinkingDone = modelThinking
    if (!thinkingDone) {
      if (supervisor_steps.length) {
        thinkingDone = supervisor_steps.map((st, i) => {
          const n = st.step || (i + 1)
          const tool = st.tool || 'chat'
          const thought = st.thought_summary || ''
          const obs = st.observation || ''
          const flag = st.executed ? '已执行' : '未执行'
          return `#${n} ${tool}（${flag}）\n${thought}${obs ? '\n→ ' + obs : ''}`
        }).join('\n\n')
      } else if (body.supervisor) {
        thinkingDone = '本轮已完成决策（无逐步工具轨迹）。'
      } else {
        thinkingDone = '本轮已完成决策。'
      }
    }
    const execNote = String(body.execution_note || body.orchestrator_note || '').trim()
    if (execNote) {
      thinkingDone = (thinkingDone ? thinkingDone + '\n' : '') + execNote
    }

    // 执行结果并入同一条助手回复，避免“另一个系统”另起消息
    let displayReply = String(reply || '').trim()
    if (execNote && !displayReply.includes(execNote) && /失败|错误|无法|未开启/.test(execNote)) {
      displayReply = displayReply ? `${displayReply}\n\n${execNote}` : execNote
    }

    if (messages.value[msgIndex]) {
      const msg = messages.value[msgIndex]
      msg.content = displayReply
      msg.actions = actions
      msg.thinking = thinkingDone
      msg.thinkingExpanded = false
      msg.supervisor_steps = supervisor_steps
      // 轨迹已进思考过程时默认折叠，减少“双脑”感
      msg.stepsExpanded = false
      msg.goal = goal
      msg.goal_id = goal_id
    } else {
      addMessage('assistant', displayReply, actions, { persist: false, thinking: thinkingDone, supervisor_steps, goal, goal_id, stepsExpanded: false })
    }

    if (body.repair_job) applyRepairJob(body.repair_job)
    if (body.triggered_repair && (body.job_id || body.repair_job)) {
      await beginRepairTracking(body.job_id || body.repair_job?.job_id || '', body.repair_job || null)
    } else if (body.triggered_auto_run) {
      nextTick(() => { if (!autoExecuting.value) startAutoRun() })
    } else if (body.triggered_command && workflowCommands().includes(body.triggered_command)) {
      nextTick(() => { if (!autoExecuting.value) startAutoRun(body.triggered_command) })
    } else if (body.triggered_command || body.triggered_rewrite) {
      watchLiveRun()
    }
  } catch (e) {
    clearInterval(thinkTimer)
    streamingIdx.value = -1
    isStreamingEmpty.value = false
    const errText = '请求失败：' + (e && e.message ? e.message : '')
    if (messages.value[msgIndex]) {
      messages.value[msgIndex].content = errText
      messages.value[msgIndex].thinking = (messages.value[msgIndex].thinking || '请求过程') + '\n发生错误，已停止等待。'
      messages.value[msgIndex].thinkingExpanded = false
    } else {
      addMessage('assistant', errText)
    }
  }
  sending.value = false
  nextTick(scrollBottom)
}

function watchLiveRun() {
  connectSSE()
  if (statusTimer) clearInterval(statusTimer)
  statusTimer = setInterval(loadStatus, 2000)
}

async function doRewriteBlock(lineNumber, instruction) {
  addMessage('assistant', '', [], { persist: false })
  const msgIndex = messages.value.length - 1
  messages.value[msgIndex].thinking = ''
  messages.value[msgIndex].thinkingExpanded = true
  streamingIdx.value = msgIndex
  isStreamingEmpty.value = true
  let streamedText = ''
  let reasoningText = ''
  let finalNewText = ''
  try {
    const resp = await fetch('/api/final-doc/rewrite-block/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ line_number: lineNumber, instruction }),
    })
    console.log('[rewrite-stream] status', resp.status, resp.headers.get('content-type'))
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEvent = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) { console.log('[rewrite-stream] done'); break }
      const raw = decoder.decode(value, { stream: true })
      console.log('[rewrite-stream] raw chunk:', JSON.stringify(raw.slice(0, 200)))
      buffer += raw
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('event: ')) { currentEvent = line.slice(7).trim(); console.log('[rewrite-stream] event:', currentEvent); continue }
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            console.log('[rewrite-stream] data event:', currentEvent, 'keys:', Object.keys(data))
            if (currentEvent === 'chunk' && data.text) {
              streamedText += data.text
              isStreamingEmpty.value = false
              messages.value[msgIndex].content = streamedText
              nextTick(() => scrollBottom())
            } else if (currentEvent === 'reasoning' && data.text) {
              reasoningText += data.text
              isStreamingEmpty.value = false
              messages.value[msgIndex].thinking = reasoningText
              nextTick(() => scrollBottom())
            } else if (currentEvent === 'done') {
              finalNewText = data.new_text
              streamingIdx.value = -1
              isStreamingEmpty.value = false
              messages.value[msgIndex].thinkingExpanded = false
              messages.value[msgIndex].content = streamedText
              messages.value[msgIndex].actions = [
                { type: 'accept_rewrite', label: '确认改写', lineNumber, newText: finalNewText },
                { type: 'discard_rewrite', label: '放弃' },
              ]
              saveChatMessage('assistant', streamedText, { thinking: reasoningText, actions: messages.value[msgIndex].actions }).catch(() => {})
              emit('rewrite-done')
            } else if (currentEvent === 'error') {
              streamingIdx.value = -1
              isStreamingEmpty.value = false
              messages.value[msgIndex].content = '改写失败: ' + (data.message || '')
              saveChatMessage('assistant', messages.value[msgIndex].content, { kind: 'message' }).catch(() => {})
            }
          } catch (e) { /* skip */ }
          currentEvent = ''
        }
      }
    }
  } catch (e) {
    streamingIdx.value = -1
    isStreamingEmpty.value = false
    messages.value[msgIndex].content = '请求失败'
  }
  sending.value = false
}
function triggerAndAutoAdvance(cmd, label) {
  const command = String(cmd || '').trim()
  if (!command) {
    addMessage('system', '缺少可执行命令')
    return
  }
  addMessage('system', `${label || ''}：${stepLabel(command)}`)
  runCommand(command)
  if (!autoExecuting.value) startAutoRun(command)
}

/** Supervisor confirm buttons carry tool/args; re-enter orchestrator with user_confirmed. */
function confirmSupervisorAction(act) {
  const tool = String(act.tool || '').trim()
  const command = String(act.command || act.args?.command || '').trim()
  const text = act.label || (command ? `确认执行 ${command}` : '确认执行')
  send(text, {
    action: {
      type: 'confirm_tool',
      tool,
      command,
      args: act.args && typeof act.args === 'object' ? act.args : {},
      user_confirmed: true,
    },
  })
}

function handleAction(act, sourceMessage = null) {
  if (!act || interactionBusy.value || act.consumed) return
  act.consumed = true
  if (sourceMessage && ['confirm_minimal_repair', 'decline_minimal_repair', 'confirm_tool'].includes(act.type)) {
    sourceMessage.actions.forEach(action => { action.consumed = true })
  }
  if (act && act.type === 'export_preflight') {
    ;(async () => {
      try {
        const { data } = await fetchExportPreflight()
        const lines = (data.checks || []).map(c => `${c.ok ? '✓' : '✗'} ${c.label}: ${c.detail}`).join('\n')
        addMessage('system', (data.message || '出稿前检查') + '\n' + lines)
        if (!data.can_export && data.block_issues && data.block_issues.length) {
          const top = data.block_issues[0]
          const cmd = top.stage_id === 'compliance_check' ? 'compliance-check' : 'global-review'
          emit('preview', cmd)
        }
      } catch (e) {
        addMessage('system', '出稿前检查失败: ' + (e.message || ''))
      }
    })()
    return
  }

  if (act.type === 'chat_prompt') send(act.prompt || act.label)
  else if (act.type === 'confirm_minimal_repair') {
    const confirmationId = actionConfirmationId(act)
    const text = act.label || '是，执行最小修复'
    if (confirmationId) {
      send(text, { action: { type: 'confirm_minimal_repair', confirmation_id: confirmationId } })
    } else {
      send('是，执行最小修复')
    }
  }
  else if (act.type === 'decline_minimal_repair') {
    const confirmationId = actionConfirmationId(act)
    send(act.label || '否，暂不修复', {
      action: confirmationId
        ? { type: 'decline_minimal_repair', confirmation_id: confirmationId }
        : null,
    })
  }
  else if (act.type === 'confirm_tool') confirmSupervisorAction(act)
  else if (act.type === 'run_command') {
    // Confirm buttons carry tool / user_confirmed; pipeline stage buttons only have command
    const tool = String(act.tool || '').trim()
    const cmd = String(act.command || '').trim()
    const needsConfirm = act.user_confirmed === true || Boolean(tool)
      || String(act.label || '').includes('确认执行')
    if (needsConfirm) {
      confirmSupervisorAction(act)
    } else {
      triggerAndAutoAdvance(cmd, '执行')
    }
  }
  else if (act.type === 'retry_stage' || act.type === 'rerun_stage') {
    const cmd = act.command || act.params?.command || ''
    addMessage('system', `重试: ${stepLabel(cmd)}`)
    triggerAndAutoAdvance(cmd, '重试')
  }
  else if (act.type === 'skip_stage') { skipFailedStage(act.command) }
  else if (act.type === 'dispatch_chapters') triggerAndAutoAdvance('write-all', '派发章节写作子 Agent')
  else if (act.type === 'dispatch_review') triggerAndAutoAdvance('review-fix-all', '派发审核改稿子 Agent')
  else if (act.type === 'dispatch_rewrite' || act.type === 'rewrite_chapters') {
    send(act.label || '对需要改稿的章节定向改稿', {
      action: { type: 'confirm_tool', tool: 'rewrite_chapters', user_confirmed: true, args: act.params || act.args || {} },
    })
  }
  else if (act.type === 'global_review' || act.type === 'revalidate_gate') {
    const cmd = act.command || act.params?.command || 'global-review'
    triggerAndAutoAdvance(cmd, act.type === 'revalidate_gate' ? '重验' : '触发全文审核子 Agent')
  }
  else if (act.type === 'fix_coverage') {
    send(act.label || '按覆盖缺口改稿', {
      action: { type: 'confirm_tool', tool: 'fix_coverage', user_confirmed: true, args: act.params || act.args || {} },
    })
  }
  else if (act.type === 'fix_compliance') {
    send(act.label || '合规定向改稿', {
      action: { type: 'confirm_tool', tool: 'fix_compliance', user_confirmed: true, args: act.params || act.args || {} },
    })
  }
  else if (act.type === 'auto_run') { if (!autoExecuting.value) startAutoRun() }
  else if (act.type === 'show_step') emit('preview', act.command || act.params?.command)
  else if (act.type === 'open_detail') {
    const cmd = act.command || act.params?.command || act.params?.stage_command || ''
    if (cmd) emit('preview', cmd)
    else emit('focus-rail', 'issues')
  }
  else if (act.type === 'show_doc_editor') emit('open-doc-editor')
  else if (act.type === 'show_manual_review') {
    const cat = String(act.category || act.params?.category || '').trim()
    emit('preview', cat ? `manual-review:${cat}` : 'manual-review')
  }
  else if (act.type === 'upload_batch') {
    const category = String(act.category || '').trim()
    const list = pendingChatFiles.value
    if (!category) {
      addMessage('system', '未指定上传类别')
      return
    }
    if (!list || !list.length) {
      addMessage('system', '没有待上传文件，请重新点击附件选择')
      openChatFile()
      return
    }
    pendingChatFiles.value = []
    uploadFiles(category, list)
  }
  else if (act.type === 'upload_materials' || act.type === 'upload_evidence') {
    emit('focus-rail', 'materials')
    addMessage('system', '请在右侧「材料」面板上传缺失资料，完成后可发送“继续”。')
    openChatFile()
  }
  else if (act.type === 'accept_rewrite') acceptRewrite(act)
  else if (act.type === 'discard_rewrite') discardRewrite()
  else if (act.type === 'undo_rewrite') undoRewrite()
  else if (act.label) {
    // Unknown action types: treat label as chat so the button is never a no-op
    send(act.prompt || act.label)
  } else {
    addMessage('system', `未实现的操作类型: ${act.type || 'unknown'}`)
  }
}

async function acceptRewrite(act) {
  try {
    await fetch('/api/final-doc/selection-apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_text: act.newText }),
    })
    addMessage('system', `第 ${act.lineNumber} 行改写已确认，Word 正在重建。`, [
      { type: 'undo_rewrite', label: '撤销' },
    ])
    emit('rewrite-done')
  } catch (e) { addMessage('system', '确认失败') }
}

async function undoRewrite() {
  try {
    const r = await fetch('/api/final-doc/undo-rewrite', { method: 'POST' }).then(r => r.json())
    addMessage('system', r.message || '已撤销')
    emit('rewrite-done')
  } catch (e) { addMessage('system', '撤销失败') }
}

async function discardRewrite() {
  try {
    await fetch('/api/final-doc/selection-discard', { method: 'POST' })
    addMessage('system', '已放弃改写。')
  } catch (e) { /* */ }
}
function onPlanPreview(cmd) { emit('preview', cmd) }

function addInputText(text, opts) {
  if (opts && opts.line && opts.fullText) {
    tagSeq++
    tags.value.push({ id: tagSeq, line: opts.line, preview: text, fullText: opts.fullText })
  } else {
    input.value = text
  }
}

function removeTag(id) { tags.value = tags.value.filter(t => t.id !== id) }

function notifyRewriteApplied() {
  const last = messages.value[messages.value.length - 1]
  if (last && last.actions) last.actions = []
  addMessage('system', '改写已确认（来自文档预览），Word 正在重建。', [{ type: 'undo_rewrite', label: '撤销' }])
}

function notifyRewriteDiscarded() {
  const last = messages.value[messages.value.length - 1]
  if (last && last.actions) last.actions = []
  addMessage('system', '改写已放弃（来自文档预览）。')
}

defineExpose({ addInputText, notifyRewriteApplied, notifyRewriteDiscarded, startAutoRun, notifyMaterialsStatus, maybeNotifyMaterialsGap })

async function loadChatHistory() {
  try {
    const resp = await fetchChatMessages()
    const body = resp && resp.data
    if (body && body.ok && Array.isArray(body.messages) && body.messages.length) {
      messages.value = body.messages.map(m => {
        const kind = m.kind || (m.role === 'system' ? 'system' : 'message')
        if (kind === 'stage_log') {
          const content = m.content || ''
          const firstLine = content.split('\n')[0] || ''
          const mm = firstLine.match(/^\[([^\]]+)\]/)
          return {
            role: 'system',
            kind: 'stage_log',
            content,
            stageLabel: mm ? mm[1] : '阶段日志',
            collapsed: true,
            logCount: content ? content.split('\n').length : 0,
            thinking: '', thinkingExpanded: false, created_at: m.created_at || '', actions: [],
          }
        }
        return {
          role: m.role,
          content: m.content,
          actions: Array.isArray(m.actions) ? m.actions : [],
          thinking: m.thinking || '',
          thinkingExpanded: false,
          created_at: m.created_at || '',
          kind,
        }
      })
      nextTick(scrollBottom)
    }
  } catch (e) { /* ignore */ }
  messagesLoaded = true
}

onMounted(async () => {
  await loadChatHistory()
  if (messages.value.length === 0) {
    addMessage('assistant', '你好！请上传招标文件、公司资料和标书模板，我会自动生成执行计划并帮你生成投标文件。')
  }
  await loadStatus()
  if (repairExecuting.value) {
    await beginRepairTracking(repairJob.value?.job_id || '', repairJob.value)
  } else if (running.value) {
    autoExecuting.value = true
    autoStarted.value = true
    connectSSE()
    statusTimer = setInterval(loadStatus, 2000)
  } else {
    maybeAutoStart()
  }
})
onBeforeUnmount(() => {
  clearInterval(statusTimer)
  stopRepairPolling()
  closeSSE()
})
</script>
