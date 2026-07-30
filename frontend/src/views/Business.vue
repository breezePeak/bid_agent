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
        <template v-else-if="viewMode === 'chapter'">
          <ChapterWorkspaceView
            :key="`${activeRunId}:${chapterId}`"
            :workspace-id="activeRunId"
            :chapter-id="chapterId"
          />
        </template>
        <template v-else-if="viewMode === 'home'">
          <ProjectHomeView
            :key="`home-${activeRunId}`"
            :workspace-id="activeRunId"
          />
        </template>
        <template v-else>
          <div class="pipeline-nav">
            <router-link :to="`/business/${activeRunId}`">项目主页</router-link>
          </div>
          <V3WorkspaceView
            :key="activeRunId"
            :run-id="activeRunId"
          />
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
  if (route.name === 'ProjectHome') return 'home'
  if (route.name === 'WorkspacePipeline') return 'pipeline'
  // default list selection uses pipeline shell for backward compatibility
  return activeRunId.value ? 'pipeline' : 'empty'
})

const activeRun = computed(() => {
  return runs.value.find(r => r.id === activeRunId.value) || null
})

function syncActiveFromRoute() {
  const fromRoute = String(route.params.workspaceId || '').trim()
  if (fromRoute) activeRunId.value = fromRoute
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
      }
    }
  } catch (e) {
    console.error('加载工作空间列表失败', e)
  } finally {
    runsLoading.value = false
  }
}

function handleSelectRun(runId) {
  if (runId === activeRunId.value && route.name === 'WorkspacePipeline') return
  activeRunId.value = runId
  router.push(`/business/${runId}/pipeline`)
}

function onWorkspaceCreated(runId) {
  showCreateDialog.value = false
  loadRuns()
  activeRunId.value = runId
  router.push(`/business/${runId}`)
}

function onSettingsSaved() {
  // 配置已写入 .env，下次调用大模型时生效，无需额外操作
}

watch(() => route.fullPath, syncActiveFromRoute)

onMounted(loadRuns)
</script>

<style scoped>
.pipeline-nav {
  padding: 8px 12px;
  border-bottom: 1px solid #e2e8f0;
  font-size: 13px;
}
.pipeline-nav a { color: #2563eb; text-decoration: none; }
</style>
