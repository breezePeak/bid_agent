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
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkspaceSelector from '../components/WorkspaceSelector.vue'
import TopBar from '../components/TopBar.vue'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog.vue'
import SettingsDialog from '../components/SettingsDialog.vue'
import V3WorkspaceView from '../components/V3WorkspaceView.vue'
import ChapterWorkbenchView from '../components/ChapterWorkbenchView.vue'
import { fetchChapters, fetchRuns, fetchV3WorkspaceSnapshot, deleteRun } from '../api'

const route = useRoute()
const router = useRouter()
const runs = ref([])
const activeRunId = ref('')
const runsLoading = ref(false)
const showCreateDialog = ref(false)
const showSettingsDialog = ref(false)
const hasOutline = ref(false)
const outlineProbing = ref(false)
let outlineTimer = null

const chapterId = computed(() => String(route.params.chapterId || ''))
const shellMode = computed(() => {
  if (route.name === 'WorkspacePipeline') return 'pipeline'
  return hasOutline.value ? 'workbench' : 'pipeline'
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
      ready = blueprintNodesFromSnapshot(snapshot).length > 0
    } catch (_) {
      /* A new workspace may not have a snapshot yet. */
    }
    if (!ready) {
      try {
        const { data } = await fetchChapters(workspaceId, true)
        ready = Boolean(data?.chapters?.items?.length)
      } catch (_) {
        /* Chapters are unavailable before the outline is created. */
      }
    }
    hasOutline.value = ready
    return ready
  } finally {
    outlineProbing.value = false
  }
}

function startOutlinePolling() {
  stopOutlinePolling()
  outlineTimer = setInterval(() => {
    if (activeRunId.value) probeOutline(activeRunId.value)
  }, hasOutline.value ? 8000 : 2500)
}

function stopOutlinePolling() {
  if (!outlineTimer) return
  clearInterval(outlineTimer)
  outlineTimer = null
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
  if (!confirm(`确定要删除工作空间 "${extractName(runId)}" 吗？`)) return
  try {
    await deleteRun(runId)
    if (activeRunId.value === runId) {
      activeRunId.value = ''
      router.push('/business')
    }
    await loadRuns()
  } catch (e) {
    alert('删除工作空间失败: ' + (e?.response?.data?.message || e?.message || '未知错误'))
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
  if (id) await probeOutline(id)
  startOutlinePolling()
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
