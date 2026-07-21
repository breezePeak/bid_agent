<template>
  <div class="business-page">
    <WorkspaceSidebar
      :runs="runs"
      :active-run-id="activeRunId"
      :loading="runsLoading"
      :collapsed="sidebarCollapsed"
      @select="handleSelectRun"
      @delete="handleDeleteRun"
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
          <WorkspaceView
            :key="activeRunId"
            :run-id="activeRunId"
            :run="activeRun"
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
    <ConfirmDialog
      :visible="showDeleteConfirm"
      title="删除工作空间"
      :message="`确定要删除「${pendingDeleteName}」吗？此操作不可恢复。`"
      confirm-text="删除"
      @confirm="doDeleteRun"
      @cancel="showDeleteConfirm = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import WorkspaceSidebar from '../components/WorkspaceSidebar.vue'
import TopBar from '../components/TopBar.vue'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog.vue'
import SettingsDialog from '../components/SettingsDialog.vue'
import WorkspaceView from '../components/WorkspaceView.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { fetchRuns, deleteRun } from '../api'

const runs = ref([])
const activeRunId = ref('')
const runsLoading = ref(false)
const showCreateDialog = ref(false)
const showSettingsDialog = ref(false)
const sidebarCollapsed = ref(false)
const showDeleteConfirm = ref(false)
const pendingDeleteId = ref('')
const pendingDeleteName = ref('')

const activeRun = computed(() => {
  return runs.value.find(r => r.id === activeRunId.value) || null
})

async function loadRuns() {
  runsLoading.value = true
  try {
    const { data } = await fetchRuns()
    if (data.ok) {
      runs.value = data.runs || []
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
  if (runId === activeRunId.value) return
  activeRunId.value = runId
}

function handleDeleteRun(runId) {
  pendingDeleteId.value = runId
  const run = runs.value.find(r => r.id === runId)
  pendingDeleteName.value = run ? extractName(run.id) : runId
  showDeleteConfirm.value = true
}

async function doDeleteRun() {
  try {
    const { data } = await deleteRun(pendingDeleteId.value)
    if (data.ok) {
      if (pendingDeleteId.value === activeRunId.value) {
        activeRunId.value = ''
      }
      showDeleteConfirm.value = false
      await loadRuns()
    }
  } catch (e) {
    console.error('删除工作空间失败', e)
  }
}

function extractName(id) {
  const match = id.match(/^(.+?)_(\d{8}_\d{6})/)
  return match ? match[1].replace(/_/g, ' ') : id
}

function onWorkspaceCreated(runId) {
  showCreateDialog.value = false
  loadRuns()
  activeRunId.value = runId
}

function onSettingsSaved() {
  // 配置已写入 .env，下次调用大模型时生效，无需额外操作
}

onMounted(() => {
  loadRuns()
})
</script>
