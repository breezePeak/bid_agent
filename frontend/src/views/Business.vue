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
            :mode="viewMode"
            :chapter-hint="chapterNavHint"
          />
          <div class="workspace-body">
            <ChapterWorkspaceView
              v-if="viewMode === 'chapter'"
              :key="`${activeRunId}:${chapterId}`"
              :workspace-id="activeRunId"
              :chapter-id="chapterId"
            />
            <ProjectHomeView
              v-else-if="viewMode === 'home'"
              :key="`home-${activeRunId}`"
              :workspace-id="activeRunId"
            />
            <V3WorkspaceView
              v-else
              :key="`pipeline-${activeRunId}`"
              :run-id="activeRunId"
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
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkspaceSidebar from '../components/WorkspaceSidebar.vue'
import WorkspaceSubNav from '../components/WorkspaceSubNav.vue'
import TopBar from '../components/TopBar.vue'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog.vue'
import SettingsDialog from '../components/SettingsDialog.vue'
import V3WorkspaceView from '../components/V3WorkspaceView.vue'
import ProjectHomeView from '../components/ProjectHomeView.vue'
import ChapterWorkspaceView from '../components/ChapterWorkspaceView.vue'
import { fetchRuns } from '../api'

const route = useRoute()
const router = useRouter()
const runs = ref([])
const activeRunId = ref('')
const runsLoading = ref(false)
const showCreateDialog = ref(false)
const showSettingsDialog = ref(false)
const sidebarCollapsed = ref(false)

const chapterId = computed(() => String(route.params.chapterId || ''))
const viewMode = computed(() => {
  if (route.name === 'ChapterWorkspace' && chapterId.value) return 'chapter'
  if (route.name === 'WorkspacePipeline') return 'pipeline'
  // Project home is default for workspace deep links and bare /business selection.
  if (route.name === 'ProjectHome' || route.name === 'Business') return activeRunId.value ? 'home' : 'empty'
  return activeRunId.value ? 'home' : 'empty'
})

const chapterNavHint = computed(() => {
  if (viewMode.value !== 'chapter') return ''
  return chapterId.value ? `当前章节：${chapterId.value}` : ''
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
  // Bare /business keeps current selection if still present.
  if (activeRunId.value && runs.value.some(item => item.id === activeRunId.value)) {
    return
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
          router.replace(`/business/${activeRunId.value}`)
        }
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
  // Selecting a workspace opens the project home (chapters / formal status).
  if (route.params.workspaceId === runId && route.name === 'ProjectHome') return
  router.push(`/business/${runId}`)
}

function onWorkspaceCreated(runId) {
  showCreateDialog.value = false
  loadRuns()
  activeRunId.value = runId
  router.push(`/business/${runId}`)
}

function onSettingsSaved() {
  // 配置已写入 .env / 流程设置；章节 H2 开关对新 revision 生效
}

watch(() => route.fullPath, syncActiveFromRoute)

onMounted(loadRuns)
</script>

<style scoped>
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
}
</style>
