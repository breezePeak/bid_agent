<template>
  <div class="agent-workbench">
    <div class="aw-header">
      <div class="aw-title-wrap">
        <span class="aw-dot" :class="{ on: isLive }"></span>
        <strong>Agent 办公室</strong>
        <span class="aw-phase" v-if="phaseLabel">{{ phaseLabel }}</span>
      </div>
      <div class="aw-summary" v-if="summary">
        <span class="aw-chip run">工位 {{ summary.running || 0 }}</span>
        <span class="aw-chip queue">排队 {{ summary.queued || 0 }}</span>
        <span class="aw-chip done">休息 {{ summary.done || 0 }}</span>
        <span class="aw-chip fail" v-if="summary.failed">加班救火 {{ summary.failed }}</span>
      </div>
    </div>

    <div v-if="!agents.length" class="aw-empty">
      <div class="aw-empty-scene" aria-hidden="true">
        <div class="mini-person idle">
          <span class="head"></span>
          <span class="body"></span>
        </div>
        <div class="empty-desk">
          <span class="screen"></span>
        </div>
      </div>
      <div class="aw-empty-title">{{ isLive ? '小人们正在入场…' : '办公室空着，还没人开工' }}</div>
      <div>进入「生成章节 / 审核改稿」后：排队的站队等，开工的敲键盘，干完的去隔壁休息室摸鱼。</div>
    </div>

    <div v-else class="aw-office">
      <!-- 工位区 -->
      <section class="zone zone-work">
        <div class="zone-head">
          <span class="zone-icon">💻</span>
          <div>
            <strong>工位区</strong>
            <em>正在干活 · {{ workingAgents.length }}</em>
          </div>
        </div>
        <div class="zone-body desks" v-if="workingAgents.length">
          <div
            v-for="a in workingAgents"
            :key="a.id"
            class="desk-unit"
            :class="'c-' + (a.color || 'slate')"
            :title="cardTitle(a)"
          >
            <div class="desk-top">
              <div class="monitor">
                <div class="screen typing">
                  <span class="code-line"></span>
                  <span class="code-line short"></span>
                  <span class="code-line"></span>
                  <span class="cursor"></span>
                </div>
                <div class="stand"></div>
              </div>
              <div class="worker working">
                <span class="head"></span>
                <span class="body"></span>
                <span class="arm left"></span>
                <span class="arm right"></span>
              </div>
            </div>
            <div class="desk-board"></div>
            <div class="desk-label">
              <span class="role">{{ shortRole(a) }}</span>
              <span class="ch">章 {{ a.chapter_id || '—' }}</span>
            </div>
            <div class="desk-msg">{{ a.message || '敲键盘改稿中…' }}</div>
          </div>
        </div>
        <div v-else class="zone-empty">工位空闲，等待调度</div>
      </section>

      <!-- 排队区 -->
      <section class="zone zone-queue">
        <div class="zone-head">
          <span class="zone-icon">🧍</span>
          <div>
            <strong>排队廊</strong>
            <em>等待上场 · {{ queuedAgents.length }}</em>
          </div>
        </div>
        <div class="zone-body queue-line" v-if="queuedAgents.length">
          <div
            v-for="(a, idx) in queuedAgents"
            :key="a.id"
            class="queue-person"
            :class="'c-' + (a.color || 'slate')"
            :style="{ animationDelay: `${idx * 0.12}s` }"
            :title="cardTitle(a)"
          >
            <div class="person standing">
              <span class="head"></span>
              <span class="body"></span>
              <span class="leg left"></span>
              <span class="leg right"></span>
            </div>
            <div class="q-badge">#{{ idx + 1 }}</div>
            <div class="q-label">
              <span>{{ shortRole(a) }}</span>
              <em>章 {{ a.chapter_id || '—' }}</em>
            </div>
          </div>
        </div>
        <div v-else class="zone-empty">没人排队，真好</div>
      </section>

      <!-- 休息室 -->
      <section class="zone zone-lounge">
        <div class="zone-head">
          <span class="zone-icon">☕</span>
          <div>
            <strong>休息室</strong>
            <em>干完摸鱼 · {{ doneAgents.length }}</em>
          </div>
        </div>
        <div class="zone-body lounge" v-if="doneAgents.length || failedAgents.length || skippedAgents.length">
          <div
            v-for="a in doneAgents"
            :key="a.id"
            class="lounge-seat"
            :class="'c-' + (a.color || 'slate')"
            :title="cardTitle(a)"
          >
            <div class="sofa">
              <div class="person lounging">
                <span class="head"></span>
                <span class="body"></span>
              </div>
              <div class="phone"></div>
            </div>
            <div class="l-label">
              <span>{{ shortRole(a) }} · 章 {{ a.chapter_id || '—' }}</span>
              <em>摸鱼中</em>
            </div>
          </div>
          <div
            v-for="a in failedAgents"
            :key="a.id"
            class="lounge-seat fail"
            :title="cardTitle(a)"
          >
            <div class="sofa panic">
              <div class="person stressed">
                <span class="head"></span>
                <span class="body"></span>
              </div>
              <div class="fire">🔥</div>
            </div>
            <div class="l-label">
              <span>{{ shortRole(a) }} · 章 {{ a.chapter_id || '—' }}</span>
              <em class="bad">救火中</em>
            </div>
          </div>
          <div
            v-for="a in skippedAgents"
            :key="a.id"
            class="lounge-seat skip"
            :title="cardTitle(a)"
          >
            <div class="sofa">
              <div class="person idle-skip">
                <span class="head"></span>
                <span class="body"></span>
              </div>
            </div>
            <div class="l-label">
              <span>{{ shortRole(a) }} · 章 {{ a.chapter_id || '—' }}</span>
              <em>未上场</em>
            </div>
          </div>
        </div>
        <div v-else class="zone-empty">休息室还空着</div>
      </section>
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

const workingAgents = computed(() => agents.value.filter((a) => a.status === 'running'))
const queuedAgents = computed(() => agents.value.filter((a) => a.status === 'queued'))
const doneAgents = computed(() => agents.value.filter((a) => a.status === 'done'))
const failedAgents = computed(() => agents.value.filter((a) => a.status === 'failed'))
const skippedAgents = computed(() => agents.value.filter((a) => a.status === 'skipped'))

function shortRole(a) {
  const label = String(a?.label || a?.role || 'Agent')
  return label.replace(/子\s*Agent|Agent/gi, '').trim() || 'Agent'
}
function cardTitle(a) {
  const st = { running: '工作中', queued: '排队', done: '完成', failed: '失败', skipped: '跳过' }[a.status] || a.status
  return `${a.label || a.role} · 章节 ${a.chapter_id || '—'} · ${st}${a.message ? ' · ' + a.message : ''}`
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
