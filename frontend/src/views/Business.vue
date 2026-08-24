<template>
  <div class="business-page">
    <div class="main-area">
      <TopBar
        :active-run="activeRun"
        :runs="runs"
        @select="handleSelectRun"
        @create="showCreateDialog = true"
        @settings="showSettingsDialog = true"
      />
      <div class="main-content">
        <template v-if="!activeRunId">
          <WorkspaceSelector
            :runs="runs"
            :loading="runsLoading"
            @select="handleSelectRun"
            @create="showCreateDialog = true"
            @delete="handleDeleteRun"
          />
        </template>
        <template v-else>
          <div class="workspace-body">
            <V3WorkspaceView
              v-if="shellMode === 'pipeline'"
              :key="`pipeline-${activeRunId}`"
              :run-id="activeRunId"
            />
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
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkspaceSelector from '../components/WorkspaceSelector.vue'
import TopBar from '../components/TopBar.vue'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog.vue'
import SettingsDialog from '../components/SettingsDialog.vue'
import V3WorkspaceView from '../components/V3WorkspaceView.vue'
import ChapterWorkbenchView from '../components/ChapterWorkbenchView.vue'
import { fetchRuns, fetchV3WorkspaceSnapshot, deleteRun, subscribeV3Workspace } from '../api'
import { alertDialog, confirmDialog } from '../composables/appDialog.js'

const route = useRoute()
const router = useRouter()
const runs = ref([])
const activeRunId = ref('')
const runsLoading = ref(false)
const showCreateDialog = ref(false)
const showSettingsDialog = ref(false)
const hasOutline = ref(false)
const workflowPhase = ref('materials')
const workflowStatus = ref('not_started')
const outlineProbing = ref(false)
let closeWorkspaceStream = null

const chapterId = computed(() => String(route.params.chapterId || ''))
const shellMode = computed(() => {
  if (route.name === 'WorkspacePipeline') return 'pipeline'
  if (route.name === 'ChapterWorkspace') return 'workbench'
  // A historical ChapterBlueprint is not a routing signal.  The workbench is
  // reachable only after the current H1 receipt has confirmed that exact
  // directory version.
  // H1 confirmation is the routing boundary.  A later writing failure still
  // belongs to the chapter workbench, where its retry and review actions live.
  return workflowPhase.value === 'writing'
    ? 'workbench'
    : 'pipeline'
})

const activeRun = computed(() => {
  return runs.value.find(r => r.id === activeRunId.value) || null
})

function extractName(id) {
  if (!id) return ''
  const match = id.match(/^(.+?)_(\d{8}_\d{6})/)
  return match ? match[1].replace(/_/g, ' ') : id
}

function syncActiveFromRoute() {
  const fromRoute = String(route.params.workspaceId || '').trim()
  if (fromRoute) {
    activeRunId.value = fromRoute
    return
  }
  // When on bare /business without workspaceId, remain empty to show WorkspaceSelector
  activeRunId.value = ''
}

function blueprintNodesFromSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return []
  const analysisNodes = snapshot.analysis?.chapter_blueprint?.nodes
  if (Array.isArray(analysisNodes) && analysisNodes.length) return analysisNodes
  const planNodes = snapshot.document?.plan?.nodes
  if (Array.isArray(planNodes) && planNodes.length) return planNodes
  const promoted = Array.isArray(snapshot.promoted_artifacts) ? snapshot.promoted_artifacts : []
  return promoted.find(item => item?.artifact_kind === 'ChapterBlueprint')?.payload?.nodes || []
}

function applyWorkflowRouteState(snapshot) {
  const current = snapshot && typeof snapshot === 'object' ? snapshot : {}
  const workflow = current.workflow && typeof current.workflow === 'object'
    ? current.workflow
    : {}
  workflowPhase.value = String(
    workflow.phase || (current.planning?.status === 'confirmed' ? 'writing' : 'planning'),
  )
  workflowStatus.value = String(
    workflow.status || (current.planning?.status === 'confirmed' ? 'ready' : 'not_started'),
  )
  // Retain this value for the list display, but never use it to open the
  // workbench before the current planning receipt exists.
  hasOutline.value = blueprintNodesFromSnapshot(current).length > 0
}

async function probeOutline(workspaceId) {
  if (!workspaceId) {
    hasOutline.value = false
    return false
  }
  outlineProbing.value = true
  try {
    let ready = false
    try {
      const { data } = await fetchV3WorkspaceSnapshot(workspaceId)
      const snapshot = data?.snapshot || data
      applyWorkflowRouteState(snapshot)
      ready = workflowPhase.value === 'writing'
    } catch (_) {
      /* A new workspace may not have a snapshot yet. */
    }
    // Do not fall back to the chapter list here: old chapter rows are exactly
    // what previously routed a failed or unreviewed outline into the workbench.
    return ready
  } finally {
    outlineProbing.value = false
  }
}

function connectWorkspaceStream(workspaceId) {
  closeWorkspaceStream?.()
  closeWorkspaceStream = null
  if (!workspaceId) return
  closeWorkspaceStream = subscribeV3Workspace(workspaceId, {
    onSnapshot: payload => {
      const current = payload?.snapshot || payload || {}
      applyWorkflowRouteState(current)
    },
    onClosed: () => {
      if (activeRunId.value === workspaceId) {
        hasOutline.value = false
        workflowPhase.value = 'materials'
        workflowStatus.value = 'not_started'
      }
    },
  })
}

function disconnectWorkspaceStream() {
  closeWorkspaceStream?.()
  closeWorkspaceStream = null
}

async function loadRuns() {
  runsLoading.value = true
  try {
    const { data } = await fetchRuns()
    if (data.ok) {
      runs.value = data.workspaces || []
      syncActiveFromRoute()
      if (activeRunId.value) await probeOutline(activeRunId.value)
    }
  } catch (e) {
    console.error('加载工作空间列表失败', e)
  } finally {
    runsLoading.value = false
  }
}

function handleSelectRun(runId) {
  activeRunId.value = runId
  router.push(`/business/${runId}`)
}

async function handleDeleteRun(runId) {
  const confirmed = await confirmDialog({
    title: '删除工作空间',
    message: `确定删除工作空间「${extractName(runId)}」吗？此操作会移除该空间的材料、规划和正文，且无法恢复。`,
    confirmText: '删除工作空间',
    cancelText: '取消',
    tone: 'danger',
  })
  if (!confirmed) return
  try {
    // Stop every workspace poll before the destructive request. Otherwise a
    // just-started snapshot request can keep SQLite/WAL files open on Windows
    // and make recursive deletion intermittently fail.
    if (activeRunId.value === runId) {
      disconnectWorkspaceStream()
      activeRunId.value = ''
      hasOutline.value = false
      await router.replace('/business')
      await nextTick()
    }
    await deleteRun(runId)
    await loadRuns()
  } catch (e) {
    await alertDialog({
      title: '删除失败',
      message: e?.response?.data?.message || e?.message || '未知错误',
      tone: 'danger',
      confirmText: '知道了',
    })
  }
}

function onWorkspaceCreated(runId) {
  showCreateDialog.value = false
  loadRuns()
  activeRunId.value = runId
  router.push(`/business/${runId}`)
}

function onSettingsSaved() {}

watch(
  () => route.fullPath,
  async () => {
    syncActiveFromRoute()
    if (activeRunId.value) await probeOutline(activeRunId.value)
  },
)

watch(activeRunId, async id => {
  hasOutline.value = false
  workflowPhase.value = 'materials'
  workflowStatus.value = 'not_started'
  connectWorkspaceStream(id)
  if (id) await probeOutline(id)
})

onMounted(() => {
  loadRuns()
})

onUnmounted(() => {
  disconnectWorkspaceStream()
})
</script>

<style scoped>
.business-page {
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.main-area {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.workspace-body {
  flex: 1;
  min-height: 0;
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
  background: #f8fafc;
}
</style>
