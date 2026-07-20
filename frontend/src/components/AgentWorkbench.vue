<template>
  <div class="agent-workbench">
    <div class="aw-header">
      <div class="aw-title-wrap">
        <span class="aw-dot" :class="{ on: isLive }"></span>
        <strong>Agent 办公室</strong>
        <span class="aw-phase" v-if="phaseLabel">{{ phaseLabel }}</span>
        <span class="aw-pool">工位池 {{ poolSize }} 并发</span>
      </div>
      <div class="aw-summary">
        <span class="aw-chip run">在岗 {{ stats.running }}</span>
        <span class="aw-chip queue">待领 {{ stats.queued }}</span>
        <span class="aw-chip done">已交 {{ stats.done }}</span>
        <span class="aw-chip fail" v-if="stats.failed">失败 {{ stats.failed }}</span>
      </div>
    </div>

    <div class="office-room" aria-label="Agent 办公室平面图">
      <!-- 主控 -->
      <div class="room room-boss">
        <div class="room-label">
          <span class="room-tag">主办公室</span>
          <strong>主 Agent · 统筹调度</strong>
        </div>
        <div class="boss-bar">
          <div class="desk-unit c-indigo boss-desk" :title="coordinatorTitle">
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
              <span class="role">Coordinator</span>
              <span class="ch">主控</span>
            </div>
          </div>
          <div class="boss-status">
            <div class="boss-msg">{{ coordinatorMsg }}</div>
            <div class="boss-progress" v-if="stats.total > 0">
              <div class="bp-track">
                <div class="bp-fill done" :style="{ width: pct(stats.done) + '%' }"></div>
                <div class="bp-fill run" :style="{ width: pct(stats.running) + '%' }"></div>
                <div class="bp-fill fail" :style="{ width: pct(stats.failed) + '%' }"></div>
              </div>
              <div class="bp-text">
                章节任务 {{ stats.done + stats.failed }}/{{ stats.total }}
                <template v-if="stats.running"> · 在写 {{ stats.running }}</template>
                <template v-if="stats.queued"> · 排队 {{ stats.queued }}</template>
              </div>
            </div>
            <div v-if="materialsDeferred > 0" class="boss-materials-flag">待补材料 {{ materialsDeferred }} 条</div>
          </div>
        </div>
      </div>

      <!-- 角色工位池 -->
      <div class="room room-pools">
        <div class="room-label">
          <span class="room-tag work">开放办公区</span>
          <strong>角色工位池</strong>
          <em>固定 {{ poolSize }} 个工位 · 章节任务轮流领取</em>
        </div>
        <div class="pool-grid">
          <div
            v-for="team in roleTeams"
            :key="team.role"
            class="role-pool"
            :class="['c-' + team.color, { active: team.running.length || team.queued }]"
          >
            <div class="rp-head">
              <span class="rp-emoji">{{ team.emoji }}</span>
              <div class="rp-titles">
                <strong>{{ team.label }}</strong>
                <em>{{ team.running.length }}/{{ team.seats.length }} 在岗</em>
              </div>
              <div class="rp-stats">
                <span v-if="team.queued" class="mini queue">待 {{ team.queued }}</span>
                <span v-if="team.done" class="mini done">完 {{ team.done }}</span>
                <span v-if="team.failed" class="mini fail">败 {{ team.failed }}</span>
              </div>
            </div>
            <div class="seat-row">
              <div
                v-for="seat in team.seats"
                :key="seat.id"
                class="seat"
                :class="{ busy: seat.busy, idle: !seat.busy }"
                :title="seat.title"
              >
                <div class="desk-top compact">
                  <div class="monitor mini">
                    <div class="screen" :class="{ typing: seat.busy }">
                      <template v-if="seat.busy">
                        <span class="code-line"></span>
                        <span class="code-line short"></span>
                        <span class="cursor"></span>
                      </template>
                    </div>
                    <div class="stand"></div>
                  </div>
                  <div class="worker" :class="seat.busy ? 'working' : 'idle'">
                    <span class="head"></span>
                    <span class="body"></span>
                    <template v-if="seat.busy">
                      <span class="arm left"></span>
                      <span class="arm right"></span>
                    </template>
                  </div>
                </div>
                <div class="desk-board short"></div>
                <div class="seat-meta">
                  <span class="seat-no">#{{ seat.no }}</span>
                  <span class="seat-ch">{{ seat.busy ? ('章 ' + seat.chapter) : '空闲' }}</span>
                </div>
                <div v-if="seat.busy && seat.message" class="seat-msg">{{ seat.message }}</div>
              </div>
            </div>
            <div v-if="team.currentChapters.length" class="rp-current">
              正在处理：{{ team.currentChapters.join('、') }}
            </div>
          </div>
        </div>
      </div>

      <!-- 任务看板：排队 / 完成 / 失败 -->
      <div class="room room-board">
        <div class="board-col queue">
          <div class="board-h">
            <span class="room-tag queue">任务走廊</span>
            <strong>待领取</strong>
            <em>{{ stats.queued }} 章</em>
          </div>
          <div class="chip-list" v-if="queuedPreview.length">
            <span v-for="c in queuedPreview" :key="'q' + c" class="ch-chip queue">{{ c }}</span>
            <span v-if="stats.queued > queuedPreview.length" class="ch-more">+{{ stats.queued - queuedPreview.length }}</span>
          </div>
          <div v-else class="board-empty">暂无排队任务</div>
        </div>
        <div class="board-col done">
          <div class="board-h">
            <span class="room-tag lounge">交稿台</span>
            <strong>已完成</strong>
            <em>{{ stats.done }} 章</em>
          </div>
          <div class="chip-list" v-if="donePreview.length">
            <span v-for="c in donePreview" :key="'d' + c" class="ch-chip done">{{ c }}</span>
            <span v-if="stats.done > donePreview.length" class="ch-more">+{{ stats.done - donePreview.length }}</span>
          </div>
          <div v-else class="board-empty">还没有交稿</div>
        </div>
        <div class="board-col fail" v-if="stats.failed">
          <div class="board-h">
            <span class="room-tag fail">救火台</span>
            <strong>执行失败</strong>
            <em>{{ stats.failed }} 章</em>
          </div>
          <div class="chip-list">
            <span v-for="c in failedPreview" :key="'f' + c" class="ch-chip fail">{{ c }}</span>
            <span v-if="stats.failed > failedPreview.length" class="ch-more">+{{ stats.failed - failedPreview.length }}</span>
          </div>
          <div class="board-hint">
            {{ failHint }}
          </div>
          <div class="board-actions" v-if="canRetryWrites">
            <button class="btn btn-sm btn-primary" :disabled="retrying" @click="retryFailedWrites">
              {{ retrying ? '重试写作中…' : '重试失败写作' }}
            </button>
            <span v-if="retryMsg" class="board-retry-msg">{{ retryMsg }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { fetchAgentActivity, retryFailedWrites as apiRetryFailedWrites } from '../api'

const POOL_SIZE = 10
/** Board chips scroll inside columns; keep a moderate preview count */
const PREVIEW_N = 24

const ROLE_TEAMS = [
  { role: 'chapter_writer', label: '写作组', emoji: '✍️', color: 'blue' },
  { role: 'chapter_reviewer', label: '审核组', emoji: '🔍', color: 'purple' },
  { role: 'chapter_rewriter', label: '改稿组', emoji: '📝', color: 'orange' },
  { role: 'global_reviewer', label: '全文审核', emoji: '📋', color: 'teal', single: true },
]

const props = defineProps({
  runId: { type: String, required: true },
  active: { type: Boolean, default: false },
  intervalMs: { type: Number, default: 2000 },
  /** Prefer parent-injected activity from useWorkspaceRuntime (single status bus). */
  activity: { type: Object, default: null },
})

const local = ref({ status: 'idle', agents: [], summary: {}, phase_label: '' })
let timer = null

const data = computed(() => {
  // Single truth: parent activity from /api/status first; local poll only as fallback
  if (props.activity && (Array.isArray(props.activity.agents) || props.activity.summary)) {
    return props.activity
  }
  return local.value
})
const agents = computed(() => (Array.isArray(data.value.agents) ? data.value.agents : []))
const summary = computed(() => data.value.summary || {})
const phaseLabel = computed(() => data.value.phase_label || data.value.phase || '')
const isLive = computed(() => props.active || String(data.value.status || '') === 'running' || (summary.value.running || 0) > 0)

const isCoordinator = (a) => a && (a.is_coordinator || a.role === 'coordinator')
const coordinator = computed(() => agents.value.find(isCoordinator) || data.value.coordinator || null)
const materialsDeferred = computed(() => Number(data.value.materials_deferred || 0) || 0)
const coordinatorMsg = computed(() => {
  if (coordinator.value?.message) return coordinator.value.message
  if (materialsDeferred.value > 0) return `值班统筹 · 待补材料 ${materialsDeferred.value} 条`
  return '值班统筹 · 等待用户指令'
})
const coordinatorTitle = computed(() => `主 Agent · ${coordinatorMsg.value}`)

const tasks = computed(() => agents.value.filter((a) => !isCoordinator(a)))

const stats = computed(() => {
  const list = tasks.value
  const running = list.filter((a) => a.status === 'running').length
  const queued = list.filter((a) => a.status === 'queued').length
  const done = list.filter((a) => a.status === 'done').length
  const failed = list.filter((a) => a.status === 'failed').length
  return {
    total: list.length,
    running: running || Number(summary.value.running || 0),
    queued: queued || Number(summary.value.queued || 0),
    done: done || Number(summary.value.done || 0),
    failed: failed || Number(summary.value.failed || 0),
  }
})

const poolSize = computed(() => POOL_SIZE)

const activeRole = computed(() => {
  const running = tasks.value.find((a) => a.status === 'running')
  if (running?.role) return running.role
  const any = tasks.value[0]
  return any?.role || 'chapter_writer'
})

const roleTeams = computed(() => {
  const byRole = {}
  for (const a of tasks.value) {
    const r = a.role || 'chapter_writer'
    if (!byRole[r]) byRole[r] = []
    byRole[r].push(a)
  }
  // Only show teams that currently have work. Fully idle pools stay hidden.
  const rolesToShow = new Set()
  for (const r of Object.keys(byRole)) {
    if (byRole[r].some((a) => a.status === 'running' || a.status === 'queued' || a.status === 'failed')) {
      rolesToShow.add(r)
    }
  }
  // Fallback: if nothing active yet but phase has a role, show that pool once agents appear
  if (!rolesToShow.size && activeRole.value && (byRole[activeRole.value] || []).length) {
    rolesToShow.add(activeRole.value)
  }

  return ROLE_TEAMS
    .filter((t) => rolesToShow.has(t.role))
    .map((t) => {
      const list = byRole[t.role] || []
      const running = list.filter((a) => a.status === 'running')
      const queued = list.filter((a) => a.status === 'queued').length
      const done = list.filter((a) => a.status === 'done').length
      const failed = list.filter((a) => a.status === 'failed').length
      // Only render busy seats; empty idle desks are omitted
      const seats = running.map((job, i) => ({
        id: `${t.role}-seat-${i}`,
        no: i + 1,
        busy: true,
        chapter: job?.chapter_id || '',
        message: job?.message || '',
        title: `${t.label} #${i + 1} · 章 ${job.chapter_id} · ${job.message || '执行中'}`,
      }))
      return {
        ...t,
        running,
        queued,
        done,
        failed,
        seats,
        currentChapters: running.map((a) => a.chapter_id).filter(Boolean).slice(0, 8),
      }
    })
})

const queuedPreview = computed(() =>
  tasks.value.filter((a) => a.status === 'queued').map((a) => a.chapter_id).filter(Boolean).slice(0, PREVIEW_N)
)
const donePreview = computed(() =>
  tasks.value.filter((a) => a.status === 'done').map((a) => a.chapter_id).filter(Boolean).slice(-PREVIEW_N).reverse()
)
const failedPreview = computed(() =>
  tasks.value.filter((a) => a.status === 'failed').map((a) => a.chapter_id).filter(Boolean).slice(0, PREVIEW_N)
)

const failHint = computed(() => {
  const failed = tasks.value.filter((a) => a.status === 'failed')
  const roles = new Set(failed.map((a) => a.role || 'chapter_writer'))
  if (roles.has('chapter_writer') && roles.size === 1) {
    return '当前是写作执行失败，不是审核后的改稿。点「重试失败写作」由写作组再写；改稿组只在审核 need_rewrite 后出现。'
  }
  if (roles.has('chapter_reviewer')) {
    return '审核阶段失败。通过后若 need_rewrite，才会派发改稿组。'
  }
  if (roles.has('chapter_rewriter')) {
    return '改稿组执行失败，可重试改稿或检查审核意见。'
  }
  return '执行失败章节。写作失败≠改稿任务；改稿组只处理审核要求改写的章节。'
})

const canRetryWrites = computed(() =>
  tasks.value.some((a) => a.status === 'failed' && (a.role || 'chapter_writer') === 'chapter_writer')
)
const retrying = ref(false)
const retryMsg = ref('')

async function retryFailedWrites() {
  if (retrying.value) return
  retrying.value = true
  retryMsg.value = ''
  try {
    const ids = tasks.value
      .filter((a) => a.status === 'failed' && (a.role || 'chapter_writer') === 'chapter_writer')
      .map((a) => a.chapter_id)
      .filter(Boolean)
    const resp = await apiRetryFailedWrites(ids)
    const body = resp?.data || {}
    retryMsg.value = body.message || (body.ok ? '重试完成' : '重试失败')
    await refresh()
  } catch (e) {
    retryMsg.value = e?.message || '重试请求失败'
  } finally {
    retrying.value = false
  }
}

function pct(n) {
  const t = stats.value.total || 0
  if (!t) return 0
  return Math.max(0, Math.min(100, Math.round((Number(n || 0) / t) * 100)))
}

async function refresh() {
  // Fallback only when parent does not inject activity from the shared status bus
  if (props.activity && (Array.isArray(props.activity.agents) || props.activity.summary)) return
  try {
    const resp = await fetchAgentActivity()
    const body = resp && resp.data ? resp.data : {}
    if (body.ok && body.activity) local.value = body.activity
  } catch (e) { /* ignore */ }
}

function start() {
  stop()
  if (props.activity && (Array.isArray(props.activity.agents) || props.activity.summary)) {
    local.value = props.activity
    return
  }
  refresh()
  timer = setInterval(refresh, props.intervalMs)
}
function stop() {
  if (timer) { clearInterval(timer); timer = null }
}

watch(() => props.runId, () => start())
watch(() => props.active, (v) => { if (v) start() })
watch(() => props.activity, (v) => { if (v) { local.value = v; stop() } }, { deep: true })

onMounted(start)
onBeforeUnmount(stop)
</script>
