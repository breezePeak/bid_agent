<template>
  <header class="topbar">
    <div class="topbar-left">
      <button class="btn btn-icon sidebar-toggle" @click="$emit('toggleSidebar')" title="折叠侧边栏">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M3 12h18M3 6h18M3 18h18" />
        </svg>
      </button>
      <h1>标书 Agent 控制台</h1>
      <span class="mode-badge" :class="modeClass" :title="modeHint">{{ modeLabel }}</span>
      <span v-if="activeRun" class="topbar-run-name">
        / {{ extractName(activeRun.id) }}
      </span>
    </div>
    <div class="topbar-right">
      <button class="btn btn-icon sidebar-toggle settings-btn" @click="$emit('settings')" title="大模型设置">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
      </button>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { fetchAgentFlags } from '../api'

defineProps({
  activeRun: { type: Object, default: null },
})

defineEmits(['create', 'toggleSidebar', 'settings'])

const mode = ref('agent')
const modeLabel = ref('Agent 模式')
const modeHint = ref('Supervisor 默认入口；AGENT_SUPERVISOR_ENABLED=false 可紧急回退')

const modeClass = computed(() => (mode.value === 'agent' ? 'mode-agent' : 'mode-legacy'))

async function loadMode() {
  try {
    const resp = await fetchAgentFlags()
    const body = resp?.data || {}
    if (body.ok !== false) {
      mode.value = body.mode || (body.supervisor_enabled ? 'agent' : 'legacy')
      modeLabel.value = body.mode_label || (mode.value === 'agent' ? 'Agent 模式' : '流水线模式')
    }
  } catch {
    /* keep default */
  }
}

onMounted(loadMode)

function extractName(id) {
  if (!id) return ''
  const match = id.match(/^(.+?)_(\d{8}_\d{6})/)
  return match ? match[1].replace(/_/g, ' ') : id
}
</script>

<style scoped>
.mode-badge {
  display: inline-flex;
  align-items: center;
  margin-left: 10px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  border: 1px solid transparent;
}
.mode-agent {
  color: #0b6e4f;
  background: rgba(11, 110, 79, 0.12);
  border-color: rgba(11, 110, 79, 0.25);
}
.mode-legacy {
  color: #8a5a00;
  background: rgba(196, 140, 0, 0.12);
  border-color: rgba(196, 140, 0, 0.3);
}
</style>
