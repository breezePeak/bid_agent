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
    <AgentWorkbench class="agp-workbench" :run-id="runId" :activity="activity" />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import AgentWorkbench from './AgentWorkbench.vue'
import { fetchAgentDecisions } from '../api'
import { useWorkspaceRuntime } from '../composables/useWorkspaceRuntime'

const props = defineProps({
  runId: { type: String, required: true },
  enabled: { type: Boolean, default: true },
  intervalMs: { type: Number, default: 2000 },
})

const {
  goal: sharedGoal,
  activity,
  productMode,
  productModeLabel,
  consistent,
  consistencyWarnings,
  refresh: refreshRuntime,
} = useWorkspaceRuntime({ runId: computed(() => props.runId), intervalMs: props.intervalMs })

const decisions = ref([])
const error = ref('')
const polling = ref(false)
let decisionTimer = null

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'budget_exceeded', 'blocked_policy'])

const goal = computed(() => {
  const g = sharedGoal.value
  if (!g) return null
  // prefer goal_full shape when present on status
  return g
})
const summary = computed(() => goal.value?.summary || '')
const criteria = computed(() =>
  Array.isArray(goal.value?.criteria_results) ? goal.value.criteria_results : []
)
const planSteps = computed(() => (Array.isArray(goal.value?.plan) ? goal.value.plan : []))
const blockedReason = computed(() => goal.value?.blocked_reason || '')
const runtimeBlock = computed(() => goal.value?.progress?.runtime_block || '')
const isTerminal = computed(() => TERMINAL.has(String(goal.value?.status || '')))

async function refreshDecisions() {
  if (!props.enabled) return
  try {
    const dResp = await fetchAgentDecisions(8)
    const dBody = dResp?.data || {}
    decisions.value = Array.isArray(dBody.decisions) ? dBody.decisions.slice().reverse() : []
    error.value = ''
  } catch (e) {
    error.value = e?.message || '决策加载失败'
  }
}

async function refresh() {
  if (!props.enabled) return
  polling.value = true
  await Promise.all([refreshRuntime(), refreshDecisions()])
}

function start() {
  stop()
  if (!props.enabled) return
  polling.value = true
  refresh()
  // Decisions are append-only diagnostics, not control truth; poll separately.
  decisionTimer = setInterval(refreshDecisions, Math.max(props.intervalMs, 3000))
}

function stop() {
  polling.value = false
  if (decisionTimer) {
    clearInterval(decisionTimer)
    decisionTimer = null
  }
}

watch(() => props.runId, () => { start() })
watch(() => props.enabled, (v) => { v ? start() : stop() })

onMounted(start)
onBeforeUnmount(stop)

defineExpose({ refresh })
</script>
