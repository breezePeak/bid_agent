<template>
  <div class="business-page">
    <WorkspaceSidebar
      :runs="runs"
      :active-run-id="activeRunId"
      :loading="runsLoading"
      :collapsed="sidebarCollapsed"
      :deletable="false"
      @select="handleSelectRun"
      @toggle="sidebarCollapsed = !sidebarCollapsed"
      @create="showCreateDialog = true"
    />
    <div class="main-area">
      <TopBar
        :active-run="activeRun"
        @create="showCreateDialog = true"
        @toggle-sidebar="sidebarCollapsed = !sidebarCollapsed"
        @settings="showSettingsDialog = true"
      />
      <div class="main-content">
        <template v-if="!activeRunId">
          <div class="empty-state">
            <div class="empty-icon">&#x1F4C1;</div>
            <h2>欢迎使用标书 Agent</h2>
            <p>请从左侧选择一个工作空间，或创建一个新的工作空间开始</p>
          </div>
        </template>
        <template v-else>
          <WorkspaceSubNav
            :workspace-id="activeRunId"
            :mode="shellMode"
            :has-outline="hasOutline"
            :outline-probing="outlineProbing"
            :chapter-hint="chapterNavHint"
          />
          <div class="workspace-body">
            <!-- 阶段 1：尚无目录 → 流水线；阶段 2：目录已生成 → 三栏工作台 -->
            <div v-if="shellMode === 'pipeline'" class="pipeline-stage">
              <div v-if="!hasOutline" class="stage-banner">
                <strong>阶段一 · 流水线</strong>
                <span>先完成输入、解析与目录规划。目录生成后将自动进入写作工作台。</span>
              </div>
              <div v-else class="stage-banner ready">
                <strong>目录已就绪</strong>
                <span>可返回工作台按章生成与编辑正文。</span>
                <router-link class="btn btn-sm btn-primary" :to="`/business/${activeRunId}`">
                  进入工作台
                </router-link>
              </div>
              <div class="pipeline-frame">
                <V3WorkspaceView
                  :key="`pipeline-${activeRunId}`"
                  :run-id="activeRunId"
                />
              </div>
            </div>
            <ChapterWorkbenchView
              v-else
              :key="`workbench-${activeRunId}`"
              :workspace-id="activeRunId"
              :initial-chapter-id="chapterId"
            />
          </div>
        </template>
      </div>
    </div>
    <CreateWorkspaceDialog
      :visible="showCreateDialog"
      @close="showCreateDialog = false"
      @created="onWorkspaceCreated"
    />
    <SettingsDialog
      :visible="showSettingsDialog"
      @close="showSettingsDialog = false"
      @saved="onSettingsSaved"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkspaceSidebar from '../components/WorkspaceSidebar.vue'
import WorkspaceSubNav from '../components/WorkspaceSubNav.vue'
import TopBar from '../components/TopBar.vue'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog.vue'
import SettingsDialog from '../components/SettingsDialog.vue'
import V3WorkspaceView from '../components/V3WorkspaceView.vue'
import ChapterWorkbenchView from '../components/ChapterWorkbenchView.vue'
import { fetchChapters, fetchRuns, fetchV3WorkspaceSnapshot } from '../api'

const route = useRoute()
const router = useRouter()
const runs = ref([])
const activeRunId = ref('')
const runsLoading = ref(false)
const showCreateDialog = ref(false)
const showSettingsDialog = ref(false)
const sidebarCollapsed = ref(false)

const hasOutline = ref(false)
const outlineProbing = ref(false)
const outlineChapterCount = ref(0)
let outlineTimer = null
let lastAutoAdvancedKey = ''

const chapterId = computed(() => String(route.params.chapterId || ''))

/**
 * Route intent vs effective shell:
 * - Explicit /pipeline always shows pipeline (user may re-run planning).
 * - Default home / chapter routes: pipeline until outline exists, then workbench.
 */
const shellMode = computed(() => {
  if (!activeRunId.value) return 'empty'
  if (route.name === 'WorkspacePipeline') return 'pipeline'
  // Prefer workbench once directory structure is available.
  if (hasOutline.value) return 'workbench'
  return 'pipeline'
})

const chapterNavHint = computed(() => {
  if (outlineProbing.value && !hasOutline.value) return '正在检测目录…'
  if (!hasOutline.value) return '完成流水线目录规划后进入工作台'
  if (chapterId.value) return `当前章节：${chapterId.value}`
  return `目录 ${outlineChapterCount.value} 章 · 左目录 · 中正文 · 右对话`
})

const activeRun = computed(() => {
  return runs.value.find(r => r.id === activeRunId.value) || null
})

function syncActiveFromRoute() {
  const fromRoute = String(route.params.workspaceId || '').trim()
  if (fromRoute) {
    activeRunId.value = fromRoute
    return
  }
  if (activeRunId.value && runs.value.some(item => item.id === activeRunId.value)) {
    return
  }
}

function blueprintNodesFromSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return []
  const analysisBp = snapshot.analysis?.chapter_blueprint
  if (Array.isArray(analysisBp?.nodes) && analysisBp.nodes.length) return analysisBp.nodes
  const plan = snapshot.document?.plan
  if (Array.isArray(plan?.nodes) && plan.nodes.length) return plan.nodes
  const promoted = Array.isArray(snapshot.promoted_artifacts) ? snapshot.promoted_artifacts : []
  const active = promoted.find(item => item?.artifact_kind === 'ChapterBlueprint')
  const payload = active?.payload
  if (Array.isArray(payload?.nodes) && payload.nodes.length) return payload.nodes
  return []
}

async function probeOutline(workspaceId) {
  if (!workspaceId) {
    hasOutline.value = false
    outlineChapterCount.value = 0
    return false
  }
  outlineProbing.value = true
  try {
    let count = 0
    let ready = false
    try {
      const { data } = await fetchV3WorkspaceSnapshot(workspaceId)
      const snapshot = data?.snapshot || data
      const nodes = blueprintNodesFromSnapshot(snapshot)
      if (nodes.length) {
        ready = true
        count = nodes.length
      }
    } catch (_) {
      /* snapshot may fail on brand-new workspace */
    }
    try {
      const { data } = await fetchChapters(workspaceId, true)
      const items = data?.chapters?.items || []
      if (items.length) {
        ready = true
        count = Math.max(count, items.length)
      }
    } catch (_) {
      /* chapters endpoint needs blueprint; ignore */
    }
    hasOutline.value = ready
    outlineChapterCount.value = count
    return ready
  } finally {
    outlineProbing.value = false
  }
}

function maybeAutoAdvanceToWorkbench() {
  // Only auto-advance from the default workspace entry (not when user explicitly opened pipeline).
  if (!activeRunId.value || !hasOutline.value) return
  if (route.name !== 'ProjectHome' && route.name !== 'Business' && route.name !== 'ChapterWorkspace') {
    return
  }
  // Already showing workbench via shellMode; if URL is bare business without workspace, fix it.
  if (route.name === 'Business' && !route.params.workspaceId) {
    const key = `${activeRunId.value}:home`
    if (lastAutoAdvancedKey === key) return
    lastAutoAdvancedKey = key
    router.replace(`/business/${activeRunId.value}`)
  }
}

function startOutlinePolling() {
  stopOutlinePolling()
  outlineTimer = setInterval(async () => {
    if (!activeRunId.value) return
    // Keep probing while on pipeline without outline, or lightly while on workbench to detect stale wipe.
    const prev = hasOutline.value
    const ready = await probeOutline(activeRunId.value)
    if (!prev && ready) {
      maybeAutoAdvanceToWorkbench()
    }
  }, hasOutline.value ? 8000 : 2500)
}

function stopOutlinePolling() {
  if (outlineTimer) {
    clearInterval(outlineTimer)
    outlineTimer = null
  }
}

async function loadRuns() {
  runsLoading.value = true
  try {
    const { data } = await fetchRuns()
    if (data.ok) {
      runs.value = data.workspaces || []
      syncActiveFromRoute()
      if (!activeRunId.value && runs.value.length) {
        activeRunId.value = runs.value[0].id
        if (route.name === 'Business' && !route.params.workspaceId) {
          // New workspaces start on pipeline stage until outline exists.
          router.replace(`/business/${activeRunId.value}`)
        }
      }
      if (activeRunId.value) {
        await probeOutline(activeRunId.value)
        maybeAutoAdvanceToWorkbench()
      }
    }
  } catch (e) {
    console.error('加载工作空间列表失败', e)
  } finally {
    runsLoading.value = false
  }
}

function handleSelectRun(runId) {
  activeRunId.value = runId
  lastAutoAdvancedKey = ''
  // Always land on workspace root; shell picks pipeline vs workbench by outline readiness.
  if (route.params.workspaceId === runId && route.name === 'ProjectHome') {
    probeOutline(runId)
    return
  }
  router.push(`/business/${runId}`)
}

function onWorkspaceCreated(runId) {
  showCreateDialog.value = false
  hasOutline.value = false
  outlineChapterCount.value = 0
  lastAutoAdvancedKey = ''
  loadRuns()
  activeRunId.value = runId
  router.push(`/business/${runId}`)
}

function onSettingsSaved() {}

watch(
  () => route.fullPath,
  async () => {
    syncActiveFromRoute()
    if (activeRunId.value) {
      await probeOutline(activeRunId.value)
      maybeAutoAdvanceToWorkbench()
    }
  },
)

watch(activeRunId, async (id) => {
  hasOutline.value = false
  outlineChapterCount.value = 0
  if (id) {
    await probeOutline(id)
    maybeAutoAdvanceToWorkbench()
  }
})

onMounted(() => {
  loadRuns()
  startOutlinePolling()
})

onUnmounted(() => {
  stopOutlinePolling()
})
</script>

<style scoped>
.workspace-body {
  flex: 1;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.main-content {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  overflow: hidden !important;
}
.pipeline-stage {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.stage-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 14px;
  padding: 10px 14px;
  background: #eff6ff;
  border-bottom: 1px solid #bfdbfe;
  color: #1e3a8a;
  font-size: 13px;
  flex-shrink: 0;
}
.stage-banner.ready {
  background: #ecfdf5;
  border-bottom-color: #a7f3d0;
  color: #14532d;
}
.stage-banner strong {
  font-weight: 700;
}
.stage-banner span {
  flex: 1;
  min-width: 200px;
}
.pipeline-frame {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>
