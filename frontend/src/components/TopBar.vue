<template>
  <header class="topbar">
    <div class="topbar-left">
      <router-link to="/business" class="topbar-brand" title="返回工作空间大厅">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
          <polyline points="10 9 9 9 8 9"/>
        </svg>
        <span class="brand-title">标书 Agent</span>
      </router-link>
      <span class="mode-badge" :class="modeClass" :title="modeHint">{{ modeLabel }}</span>

      <!-- 工作空间切换器 -->
      <div v-if="activeRun" ref="dropdownRef" class="workspace-dropdown-wrapper">
        <button class="workspace-selector-btn" @click="dropdownOpen = !dropdownOpen">
          <span class="ws-icon-thumb" :style="{ backgroundColor: thumbColor(extractName(activeRun.id)) }">
            {{ firstChar(extractName(activeRun.id)) }}
          </span>
          <span class="ws-current-name">{{ extractName(activeRun.id) }}</span>
          <svg class="chevron-icon" :class="{ open: dropdownOpen }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </button>

        <!-- 下拉框面板 -->
        <div v-if="dropdownOpen" class="workspace-dropdown-panel">
          <div class="panel-header">
            <span>切换工作空间</span>
            <router-link to="/business" class="link-all" @click="dropdownOpen = false">所有大厅 ›</router-link>
          </div>
          <div class="panel-list">
            <div
              v-for="run in runs"
              :key="run.id"
              class="panel-item"
              :class="{ active: run.id === activeRun.id }"
              @click="handleSelect(run.id)"
            >
              <span class="item-thumb" :style="{ backgroundColor: thumbColor(extractName(run.id)) }">
                {{ firstChar(extractName(run.id)) }}
              </span>
              <div class="item-info">
                <div class="item-name">{{ extractName(run.id) }}</div>
                <div class="item-date">{{ formatDate(run.id) }}</div>
              </div>
              <svg v-if="run.id === activeRun.id" class="check-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            </div>
          </div>
          <div class="panel-footer">
            <button class="btn btn-sm btn-block btn-create-mini" @click="handleCreate">
              + 新建工作空间
            </button>
          </div>
        </div>
      </div>

      <template v-if="activeRun">
        <span class="workspace-mode-hint">统一工作台</span>
      </template>
    </div>

    <div class="topbar-right">
      <router-link v-if="activeRun" to="/business" class="btn btn-sm btn-outline-lobby" title="返回工作空间大厅">
        所有工作空间
      </router-link>
      <button class="btn btn-icon settings-btn" @click="$emit('settings')" title="模型与流程设置" aria-label="模型与流程设置">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
      </button>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  activeRun: { type: Object, default: null },
  runs: { type: Array, default: () => [] },
})

const emit = defineEmits(['create', 'select', 'settings'])

const dropdownOpen = ref(false)
const dropdownRef = ref(null)

const mode = 'v3'
const modeLabel = 'V3 模式'
const modeHint = 'V3 工作区使用唯一 StageRunner 与 V3 CommandGateway。'

const modeClass = computed(() => (mode === 'v3' ? 'mode-agent' : 'mode-legacy'))

const THUMB_COLORS = ['#3b82f6', '#10b981', '#6366f1', '#f59e0b', '#ec4899', '#8b5cf6', '#06b6d4', '#14b8a6']

function thumbColor(name) {
  if (!name) return THUMB_COLORS[0]
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  return THUMB_COLORS[Math.abs(hash) % THUMB_COLORS.length]
}

function firstChar(name) {
  if (!name) return '?'
  return name.charAt(0).toUpperCase()
}

function extractName(id) {
  if (!id) return ''
  const match = id.match(/^(.+?)_(\d{8}_\d{6})/)
  return match ? match[1].replace(/_/g, ' ') : id
}

function formatDate(id) {
  const match = id.match(/(\d{8})_\d{6}/)
  if (!match) return ''
  const d = match[1]
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`
}

function handleSelect(runId) {
  dropdownOpen.value = false
  emit('select', runId)
}

function handleCreate() {
  dropdownOpen.value = false
  emit('create')
}

function handleClickOutside(event) {
  if (dropdownRef.value && !dropdownRef.value.contains(event.target)) {
    dropdownOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.topbar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  text-decoration: none;
  color: #1e293b;
  font-weight: 700;
  font-size: 16px;
  margin-right: 4px;
}

.topbar-brand:hover {
  color: #2563eb;
}

.brand-title {
  letter-spacing: -0.01em;
}

.mode-badge {
  display: inline-flex;
  align-items: center;
  margin-left: 6px;
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

/* 下拉菜单拉开样式 */
.workspace-dropdown-wrapper {
  position: relative;
  margin-left: 12px;
}

.workspace-selector-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  padding: 4px 10px 4px 6px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  color: #1e293b;
  font-weight: 500;
  transition: all 0.2s;
}

.workspace-selector-btn:hover {
  background: #e2e8f0;
  border-color: #cbd5e1;
}

.ws-icon-thumb {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ws-current-name {
  max-width: 180px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chevron-icon {
  color: #64748b;
  transition: transform 0.2s;
}

.chevron-icon.open {
  transform: rotate(180deg);
}

.workspace-dropdown-panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: 260px;
  background: #ffffff;
  border-radius: 10px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.05);
  border: 1px solid #e2e8f0;
  z-index: 100;
  overflow: hidden;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid #f1f5f9;
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
}

.link-all {
  color: #2563eb;
  text-decoration: none;
}

.link-all:hover {
  text-decoration: underline;
}

.panel-list {
  max-height: 240px;
  overflow-y: auto;
  padding: 6px 0;
}

.panel-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  cursor: pointer;
  transition: background 0.15s;
}

.panel-item:hover {
  background: #f8fafc;
}

.panel-item.active {
  background: #eff6ff;
}

.item-thumb {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.item-info {
  flex: 1;
  min-width: 0;
}

.item-name {
  font-size: 13px;
  font-weight: 500;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.item-date {
  font-size: 11px;
  color: #94a3b8;
}

.check-icon {
  color: #2563eb;
  flex-shrink: 0;
}

.panel-footer {
  padding: 8px;
  border-top: 1px solid #f1f5f9;
  background: #fafafa;
}

.btn-create-mini {
  background: #ffffff;
  border: 1px dashed #cbd5e1;
  color: #2563eb;
  font-weight: 500;
}

.btn-create-mini:hover {
  background: #eff6ff;
  border-color: #93c5fd;
}

.workspace-mode-hint {
  margin-left: 12px;
  font-size: 12px;
  color: #64748b;
}

.btn-outline-lobby {
  background: transparent;
  border: 1px solid #cbd5e1;
  color: #475569;
  font-size: 12px;
  margin-right: 8px;
  padding: 4px 10px;
}

.btn-outline-lobby:hover {
  background: #f1f5f9;
  color: #1e293b;
}
</style>
