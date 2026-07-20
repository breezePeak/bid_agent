<template>
  <div class="agent-goal-panel">
    <div class="agp-goal-block">
      <div class="agp-header">
        <span class="agp-title">Agent 目标</span>
        <span class="agp-poll" :class="{ on: polling }">{{ polling ? 'live' : 'idle' }}</span>
      </div>

      <div v-if="error" class="agp-empty">{{ error }}</div>
      <div v-else-if="!goal" class="agp-empty compact">
        暂无活动目标
        <span class="agp-hint"> · 对话下达目标后显示</span>
      </div>
      <div v-else class="agp-body">
        <div class="agp-row">
          <span class="agp-status" :class="'st-' + (goal.status || 'pending')">{{ goal.status || 'pending' }}</span>
          <span class="agp-id">#{{ goal.goal_id }}</span>
        </div>
        <div class="agp-goal-text">{{ goal.raw_user_goal || summary }}</div>
        <div class="agp-summary">{{ summary }}</div>

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
import { fetchAgentGoal, fetchAgentDecisions } from '../api'

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
const error = ref('')
const polling = ref(false)
let timer = null

async function refresh() {
  if (!props.enabled) return
  try {
    const [gResp, dResp] = await Promise.all([
      fetchAgentGoal(),
      fetchAgentDecisions(8),
    ])
    const gBody = gResp?.data || {}
    const dBody = dResp?.data || {}
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
    decisions.value = Array.isArray(dBody.decisions) ? dBody.decisions.slice().reverse() : []
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
