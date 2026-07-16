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
              <button v-for="act in msg.actions" :key="act.label" class="btn btn-sm" @click="handleAction(act)">{{ act.label }}</button>
            </div>
            <div v-if="(msg.goal && (msg.goal.status || msg.goal.all_criteria_ok !== undefined)) || msg.goal_id" class="chat-goal-badge">
              <span class="chat-goal-label">目标</span>
              <span class="chat-goal-status" :class="'goal-' + (msg.goal.status || 'pending')">{{ msg.goal.status || 'pending' }}</span>
              <span v-if="msg.goal_id || (msg.goal && msg.goal.goal_id)" class="chat-goal-id">#{{ msg.goal_id || msg.goal.goal_id }}</span>
              <span v-if="msg.goal.all_criteria_ok === true" class="chat-goal-ok">criteria OK</span>
              <span v-else-if="msg.goal.all_criteria_ok === false" class="chat-goal-bad">criteria pending</span>
            </div>
            <div v-if="msg.supervisor_steps && msg.supervisor_steps.length" class="chat-supervisor-steps" :class="{ collapsed: !msg.stepsExpanded }">
              <div class="chat-supervisor-header" @click="msg.stepsExpanded = !msg.stepsExpanded">
                <span class="chat-supervisor-arrow">{{ msg.stepsExpanded ? '▼' : '▶' }}</span>
                <span>Agent 决策轨迹</span>
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

    <!-- quick actions -->
    <div class="chat-quick-row" v-if="quickBtns.length">
      <button
        v-for="btn in quickBtns"
        :key="btn.label"
        class="btn btn-sm"
        @click="handleQuick(btn)"
      >{{ btn.label }}</button>
    </div>

    <!-- plan list above input -->
    <div v-if="showPlan || running || autoExecuting || (agentActivity && agentActivity.agents && agentActivity.agents.length)" class="chat-plan-area">
      <AgentWorkbench
        :run-id="runId"
        :active="running || autoExecuting"
        :activity="agentActivity"
      />
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
        <button class="btn btn-sm btn-icon" @click="openChatFile" title="上传文件">&#x1F4CE;</button>
        <textarea
          v-model="input"
          class="chat-input"
          placeholder="输入问题或指令，Enter 发送..."
          rows="3"
          @keydown.enter.exact.prevent="submit"
        ></textarea>
        <input type="file" ref="chatFileInput" hidden multiple @change="onChatFileSelected" />
        <button class="btn btn-sm btn-primary" @click="submit" :disabled="(!input.trim() && !tags.length) || sending">发送</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import PlanList from './PlanList.vue'
import AgentWorkbench from './AgentWorkbench.vue'
import UploadTile from './UploadTile.vue'
import { fetchChatMessages, saveChatMessage, orchestrateChat } from '../api'

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

const emit = defineEmits(['preview', 'open-doc-editor', 'rewrite-done'])

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

const prevStatusMap = reactive({})
let messagesLoaded = false

const running = ref(false)
const autoExecuting = ref(false)
const autoStarted = ref(false)
const showPlan = ref(false)
let sseSource = null; let statusTimer = null
let tagSeq = 0

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
const quickBtns = computed(() => {
  if (!uploadedAll.value) return []
  if (planDone.value || docxReady.value) {
    const btns = [
      { label: '下载 Word', action: 'download-docx' },
      { label: '预览 Word', action: 'doc-editor' },
      { label: '查看全文审核', type: 'show_step', command: 'global-review' },
    ]
    if (complianceSummary.value) {
      btns.splice(2, 0, { label: complianceSummary.value.blocking ? '合规阻断详情' : '查看专项合规', type: 'show_step', command: 'compliance-check' })
    }
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
  if (e.target.files.length) {
    addMessage('assistant', '请选择文件类别：', [
      { type: 'upload_batch', category: 'tender', label: '招标文件' },
      { type: 'upload_batch', category: 'company', label: '公司资料' },
      { type: 'upload_batch', category: 'template', label: '标书模板' },
    ])
  }
  e.target.value = ''
}

// ---- status ----
async function loadStatus() {
  try {
    const data = await fetch('/api/status').then(r => r.json())
    updateFromStatus(data)
  } catch (e) { /* */ }
}
function updateFromStatus(data) {
  if (!data || !data.workflow) return
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
    }
    prevStatusMap[s.command] = s.status
  })
  running.value = data.running || false
  if (data.agent_activity) agentActivity.value = data.agent_activity
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
}

// ---- auto run ----
function maybeAutoStart() {
  if (!uploadedAll.value) return
  if (autoStarted.value || autoExecuting.value || running.value) return
  if (planDone.value) return
  autoStarted.value = true
  showPlan.value = true
  addMessage('assistant', '资料已齐全，自动开始执行流程。每完成一个阶段会自动推进到下一个，无需手动点击。可随时输入「暂停」或提问。')
  nextTick(() => startAutoRun())
}
async function startAutoRun(fromCommand = null) {
  if (autoExecuting.value) return
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
// 把有价值的实时日志按阶段聚合成一个可折叠块，写入聊天并入库；不再单独显示日志面板。
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
  // 整块日志作为一条消息入库
  saveChatMessage('system', m.content, { kind: 'stage_log', actions: [] }).catch(() => {})
  activeStageLog.value = null
  activeLogBodyEl = null
}

function pushValuableLog(line, kind = 'log') {
  const text = String(line || '').trim()
  if (!text || text === lastLogLine) return
  lastLogLine = text
  const label = currentStageLabel()
  let m = activeStageLog.value
  if (!m || m.stageLabel !== label) {
    // 进入新阶段 → 先折叠上一块
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
  if (btn && btn.text) { send(btn.text); return }

  if (btn.type) {
    handleAction({ type: btn.type, label: btn.label, command: btn.command })
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
function send(msg) { addMessage('user', msg); doChat(msg) }
function submit() {
  const t = input.value.trim()
  if ((!t && !tags.value.length) || sending.value) return
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
  addMessage('user', msg)
  doChat(msg)
}
async function doChat(text) {
  sending.value = true
  const match = text.match(/@L(\d+)\s/)
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
    '正在理解你的意图，并查询当前工作区状态…',
    '正在调用编排器 / 可选 Supervisor 决策…',
    '正在汇总结果，请稍候…',
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
    const resp = await orchestrateChat(text)
    const body = resp && resp.data ? resp.data : {}
    clearInterval(thinkTimer)
    streamingIdx.value = -1
    isStreamingEmpty.value = false

    if (body.ok === false) {
      if (messages.value[msgIndex]) {
        messages.value[msgIndex].content = body.message || '无法获取响应'
        messages.value[msgIndex].thinking = (messages.value[msgIndex].thinking || '') + '\n请求结束：编排器返回失败。'
        messages.value[msgIndex].thinkingExpanded = false
      }
      sending.value = false
      return
    }

    const reply = body.reply || ''
    const actions = Array.isArray(body.actions) ? body.actions : []
    const supervisor_steps = Array.isArray(body.supervisor_steps) ? body.supervisor_steps : []
    const goal = body.goal && typeof body.goal === 'object' ? body.goal : null
    const goal_id = body.goal_id || ''

    // 把决策轨迹整理进 thinking，结果出来后默认折叠
    let thinkingDone = '已完成分析。'
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
      thinkingDone = 'Supervisor 已处理，但本轮无逐步 tool 轨迹。'
    } else {
      thinkingDone = '编排器已返回结果（经典模式）。'
    }
    if (body.orchestrator_note) {
      thinkingDone += `\n备注：${body.orchestrator_note}`
    }

    if (messages.value[msgIndex]) {
      const msg = messages.value[msgIndex]
      msg.content = reply
      msg.actions = actions
      msg.thinking = thinkingDone
      msg.thinkingExpanded = false
      msg.supervisor_steps = supervisor_steps
      msg.stepsExpanded = supervisor_steps.length > 0
      msg.goal = goal
      msg.goal_id = goal_id
      // 结果落盘
      saveChatMessage('assistant', reply, { actions, kind: 'message' }).catch(e => console.error('保存消息失败', e))
    } else {
      addMessage('assistant', reply, actions, { supervisor_steps, goal, goal_id, stepsExpanded: true })
    }

    if (body.triggered_auto_run) {
      nextTick(() => { if (!autoExecuting.value) startAutoRun() })
    } else if (body.triggered_command && workflowCommands().includes(body.triggered_command)) {
      nextTick(() => { if (!autoExecuting.value) startAutoRun(body.triggered_command) })
    } else if (body.triggered_command || body.triggered_rewrite) {
      watchLiveRun()
    }
    if (body.orchestrator_note) {
      addMessage('system', `[编排器] ${body.orchestrator_note}`)
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
  addMessage('system', `${label || ''}：${stepLabel(cmd)}`)
  runCommand(cmd)
  if (!autoExecuting.value) startAutoRun(cmd)
}
function handleAction(act) {
  if (act.type === 'chat_prompt') send(act.label)
  else if (act.type === 'run_command') triggerAndAutoAdvance(act.command, '执行')
  else if (act.type === 'retry_stage') { addMessage('system', `重试: ${stepLabel(act.command)}`); triggerAndAutoAdvance(act.command, '重试') }
  else if (act.type === 'skip_stage') { skipFailedStage(act.command) }
  else if (act.type === 'dispatch_chapters') triggerAndAutoAdvance('write-all', '派发章节写作子 Agent')
  else if (act.type === 'dispatch_review') triggerAndAutoAdvance('review-fix-all', '派发审核改稿子 Agent')
  else if (act.type === 'dispatch_rewrite') { send('对需要改稿的章节定向改稿') }
  else if (act.type === 'global_review') triggerAndAutoAdvance('global-review', '触发全文审核子 Agent')
  else if (act.type === 'auto_run') { if (!autoExecuting.value) startAutoRun() }
  else if (act.type === 'show_step') emit('preview', act.command)
  else if (act.type === 'show_doc_editor') emit('open-doc-editor')
  else if (act.type === 'show_manual_review') emit('preview', 'manual-review')
  else if (act.type === 'upload_batch') { /* handled by file input */ }
  else if (act.type === 'accept_rewrite') acceptRewrite(act)
  else if (act.type === 'discard_rewrite') discardRewrite()
  else if (act.type === 'undo_rewrite') undoRewrite()
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

defineExpose({ addInputText, notifyRewriteApplied, notifyRewriteDiscarded })

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
  if (running.value) {
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
  closeSSE()
})
</script>
