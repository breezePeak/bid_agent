<template>
  <div class="agent-workbench">
    <div class="aw-header">
      <div class="aw-title-wrap">
        <span class="aw-dot" :class="{ on: isLive }"></span>
        <strong>Agent 工作台</strong>
        <span class="aw-phase" v-if="phaseLabel">{{ phaseLabel }}</span>
      </div>
      <div class="aw-summary" v-if="summary">
        <span class="aw-chip run">进行中 {{ summary.running || 0 }}</span>
        <span class="aw-chip queue">排队 {{ summary.queued || 0 }}</span>
        <span class="aw-chip done">完成 {{ summary.done || 0 }}</span>
        <span class="aw-chip fail" v-if="summary.failed">失败 {{ summary.failed }}</span>
      </div>
    </div>

    <div v-if="!agents.length" class="aw-empty">
      <div class="aw-empty-title">{{ isLive ? '正在准备子 Agent…' : '当前没有子 Agent 在工作' }}</div>
      <div>进入「生成章节 / 审核改稿」等并发阶段后，这里会显示每个写作/审核 Agent 的卡片状态（排队 / 工作中 / 完成 / 失败）。</div>
    </div>

    <div v-else class="aw-grid">
      <div
        v-for="a in agents"
        :key="a.id"
        class="aw-card"
        :class="['st-' + (a.status || 'queued'), 'c-' + (a.color || 'slate')]"
      >
        <div class="aw-card-top">
          <span class="aw-emoji">{{ a.emoji || '🤖' }}</span>
          <div class="aw-card-meta">
            <div class="aw-role">{{ a.label || a.role }}</div>
            <div class="aw-chapter">章节 {{ a.chapter_id || '—' }}</div>
          </div>
          <span class="aw-status">{{ statusText(a.status) }}</span>
        </div>
        <div class="aw-msg">{{ a.message || defaultMsg(a.status) }}</div>
        <div class="aw-foot" v-if="a.attempt">尝试 #{{ a.attempt }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { fetchAgentActivity } from '../api'

const props = defineProps({
  runId: { type: String, required: true },
  active: { type: Boolean, default: false },
  intervalMs: { type: Number, default: 1500 },
  activity: { type: Object, default: null },
})

const local = ref({ status: 'idle', agents: [], summary: {}, phase_label: '' })
let timer = null

const data = computed(() => (props.activity && Array.isArray(props.activity.agents) ? props.activity : local.value))
const agents = computed(() => (Array.isArray(data.value.agents) ? data.value.agents : []))
const summary = computed(() => data.value.summary || {})
const phaseLabel = computed(() => data.value.phase_label || data.value.phase || '')
const isLive = computed(() => props.active || String(data.value.status || '') === 'running' || (summary.value.running || 0) > 0)
const visible = computed(() => props.active || agents.value.length > 0 || isLive.value)

function statusText(st) {
  const m = { running: '工作中', queued: '排队', done: '完成', failed: '失败', skipped: '跳过' }
  return m[st] || st || '—'
}
function defaultMsg(st) {
  const m = { running: '处理中…', queued: '等待调度', done: '已完成', failed: '执行失败', skipped: '未执行' }
  return m[st] || ''
}

async function refresh() {
  try {
    const resp = await fetchAgentActivity()
    const body = resp && resp.data ? resp.data : {}
    if (body.ok && body.activity) local.value = body.activity
  } catch (e) { /* ignore */ }
}

function start() {
  stop()
  refresh()
  timer = setInterval(refresh, props.intervalMs)
}
function stop() {
  if (timer) { clearInterval(timer); timer = null }
}

watch(() => props.runId, () => start())
watch(() => props.active, (v) => { if (v) start() })
watch(() => props.activity, (v) => { if (v) local.value = v }, { deep: true })

onMounted(start)
onBeforeUnmount(stop)
</script>
