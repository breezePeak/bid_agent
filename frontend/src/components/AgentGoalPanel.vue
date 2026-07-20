<template>
  <div class="agent-goal-panel">
    <div class="agp-goal-block">
      <div class="agp-header">
        <span class="agp-title">Agent 目标</span>
        <span class="agp-poll" :class="{ on: polling }">{{ polling ? 'live' : 'idle' }}</span>
      </div>

      <div v-if="error" class="agp-empty">{{ error }}</div>
      <div v-else-if="!goal" class="agp-empty compact">
        暂无目标记录
        <span class="agp-hint"> · 对话下达目标后显示</span>
      </div>
      <div v-else class="agp-body">
        <div class="agp-row">
          <span class="agp-status" :class="'st-' + (goal.status || 'pending')">{{ goal.status || 'pending' }}</span>
          <span class="agp-id">#{{ goal.goal_id }}</span>
          <span v-if="isTerminal" class="agp-terminal-hint">最近目标</span>
        </div>
        <div class="agp-goal-text">{{ goal.raw_user_goal || summary }}</div>
        <div class="agp-summary">{{ summary }}</div>
        <div v-if="runtimeBlock" class="agp-runtime-block">运行中：{{ runtimeBlock }}</div>

        <div v-if="planSteps.length" class="agp-plan">
          <div class="agp-section-label">计划步骤</div>
          <div v-for="(s, i) in planSteps" :key="i" class="agp-plan-step" :class="'ps-' + (s.status || 'pending')">
            <span class="ps-status">{{ s.status || 'pending' }}</span>
            <span class="ps-label">{{ s.label || s.tool || s.step_id }}</span>
          </div>
        </div>

        <div v-if="criteria.length" class="agp-criteria">
          <div class="agp-section-label">成功准则</div>
          <div v-for="(c, i) in criteria" :key="i" class="agp-criterion" :class="{ ok: c.ok, bad: !c.ok }">
            <span class="dot"></span>
            <span class="check">{{ c.check }}</span>
            <span class="detail">{{ c.detail }}</span>
          </div>
        </div>

        <div v-if="blockedReason" class="agp-block">
          <div class="agp-section-label">阻断原因</div>
          <div class="agp-block-text">{{ blockedReason }}</div>
        </div>

        <div v-if="productMode" class="agp-mode-row">
          <span class="agp-mode" :class="'mode-' + productMode">{{ productModeLabel || productMode }}</span>
          <span v-if="!consistent" class="agp-inconsistent">状态不一致</span>
        </div>
        <div v-if="consistencyWarnings.length" class="agp-warnings">
          <div class="agp-section-label">一致性告警</div>
          <div v-for="(w, i) in consistencyWarnings" :key="i" class="agp-warn" :class="w.severity">
            {{ w.message }}
          </div>
        </div>

        <div v-if="decisions.length" class="agp-decisions">
          <div class="agp-section-label">最近决策 / 选择原因</div>
          <div v-for="(d, i) in decisions" :key="i" class="agp-decision">
            <div class="agp-d-top">
              <span class="tool">{{ d.selected_tool || d.tool || '-' }}</span>
              <span class="flag" :class="d.executed ? 'exec' : 'plan'">{{ d.executed ? 'exec' : 'plan' }}</span>
            </div>
            <div v-if="d.thought_summary" class="agp-d-thought">{{ d.thought_summary }}</div>
          </div>
        </div>
      </div>
    </div>
    <AgentWorkbench class="agp-workbench" :run-id="runId" />
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import AgentWorkbench from './AgentWorkbench.vue'
import { fetchAgentGoal, fetchAgentDecisions, fetchRuntimeStatus } from '../api'

const props = defineProps({
  runId: { type: String, required: true },
  enabled: { type: Boolean, default: true },
  intervalMs: { type: Number, default: 3000 },
})

const goal = ref(null)
const summary = ref('')
const decisions = ref([])
const criteria = ref([])
const planSteps = ref([])
const blockedReason = ref('')
const runtimeBlock = ref('')
const isTerminal = ref(false)
const productMode = ref('')
const productModeLabel = ref('')
const consistent = ref(true)
const consistencyWarnings = ref([])
const error = ref('')
const polling = ref(false)
let timer = null

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'budget_exceeded', 'blocked_policy'])

async function refresh() {
  if (!props.enabled) return
  try {
    const [gResp, dResp, rResp] = await Promise.all([
      fetchAgentGoal(),
      fetchAgentDecisions(8),
      fetchRuntimeStatus(false).catch(() => null),
    ])
    const gBody = gResp?.data || {}
    const dBody = dResp?.data || {}
    const rBody = rResp?.data || {}
    if (gBody.ok === false) {
      error.value = gBody.message || 'goal api error'
      return
    }
    error.value = ''
    goal.value = gBody.goal || null
    summary.value = gBody.summary || ''
    criteria.value = Array.isArray(goal.value?.criteria_results) ? goal.value.criteria_results : []
    planSteps.value = Array.isArray(goal.value?.plan) ? goal.value.plan : []
    blockedReason.value = goal.value?.blocked_reason || ''
    runtimeBlock.value = goal.value?.progress?.runtime_block || ''
    isTerminal.value = TERMINAL.has(String(goal.value?.status || ''))
    decisions.value = Array.isArray(dBody.decisions) ? dBody.decisions.slice().reverse() : []
    // Prefer unified runtime aggregator for mode + warnings
    productMode.value = rBody.product_mode || gBody.product_mode || ''
    productModeLabel.value = rBody.product_mode_label || gBody.product_mode_label || ''
    consistent.value = rBody.consistent !== false && gBody.consistent !== false
    const warns = rBody.warnings || gBody.consistency_warnings || []
    consistencyWarnings.value = Array.isArray(warns) ? warns.slice(0, 5) : []
  } catch (e) {
    error.value = e?.message || '轮询失败'
  }
}

function start() {
  stop()
  if (!props.enabled) return
  polling.value = true
  refresh()
  timer = setInterval(refresh, props.intervalMs)
}

function stop() {
  polling.value = false
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(() => props.runId, () => { start() })
watch(() => props.enabled, (v) => { v ? start() : stop() })

onMounted(start)
onBeforeUnmount(stop)

defineExpose({ refresh })
</script>
