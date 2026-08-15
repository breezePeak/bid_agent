<template>
  <div class="workspace-selector-container">
    <div class="selector-header">
      <div class="header-left">
        <h1 class="header-title">工作空间大厅</h1>
        <p class="header-desc">选择一个已有工作空间开始，或新建工作空间开展新项目</p>
      </div>
      <div class="header-right">
        <div class="search-box">
          <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"/>
            <line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input
            v-model="searchQuery"
            type="text"
            placeholder="搜索工作空间..."
            class="search-input"
          />
        </div>
        <button class="btn btn-primary create-btn" @click="$emit('create')">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
          新建工作空间
        </button>
      </div>
    </div>

    <!-- 加载中 -->
    <div v-if="loading" class="selector-loading">
      <div class="spinner"></div>
      <p>正在加载工作空间...</p>
    </div>

    <!-- 内容网格 -->
    <div v-else class="selector-grid">
      <!-- 新建工作空间快捷卡片 -->
      <div class="grid-card create-card" @click="$emit('create')">
        <div class="create-icon-wrapper">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
        </div>
        <span class="create-card-text">创建新工作空间</span>
      </div>

      <!-- 工作空间列表卡片 -->
      <div
        v-for="run in filteredRuns"
        :key="run.id"
        class="grid-card workspace-card"
        @click="$emit('select', run.id)"
      >
        <div class="card-top">
          <div class="card-avatar" :style="{ backgroundColor: thumbColor(extractName(run.id)) }">
            {{ firstChar(extractName(run.id)) }}
          </div>
          <div class="card-badge-group">
            <span v-if="run.delivery_status" class="status-badge" :class="deliveryClass(run.delivery_status)">
              {{ deliveryLabel(run.delivery_status) }}
            </span>
          </div>
        </div>

        <div class="card-body">
          <h3 class="card-title" :title="extractName(run.id)">{{ extractName(run.id) }}</h3>
          <div class="card-meta">
            <span class="meta-item">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
                <line x1="16" y1="2" x2="16" y2="6"/>
                <line x1="8" y1="2" x2="8" y2="6"/>
                <line x1="3" y1="10" x2="21" y2="10"/>
              </svg>
              {{ formatDate(run.id) }}
            </span>
            <span v-if="chapterSummary(run)" class="meta-item chapter-info">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
              </svg>
              {{ chapterSummary(run) }}
            </span>
          </div>
        </div>

        <div class="card-footer">
          <button class="btn btn-sm btn-enter">
            进入工作空间
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="9 18 15 12 9 6"/>
            </svg>
          </button>
          <button
            class="btn-delete"
            title="删除工作空间"
            @click.stop="$emit('delete', run.id)"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 无结果状态 -->
    <div v-if="!loading && filteredRuns.length === 0 && searchQuery" class="selector-empty">
      <p>未找到匹配 “{{ searchQuery }}” 的工作空间</p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  runs: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

defineEmits(['select', 'create', 'delete'])

const searchQuery = ref('')

const THUMB_COLORS = ['#3b82f6', '#10b981', '#6366f1', '#f59e0b', '#ec4899', '#8b5cf6', '#06b6d4', '#14b8a6']

const filteredRuns = computed(() => {
  if (!searchQuery.value.trim()) return props.runs
  const q = searchQuery.value.toLowerCase().trim()
  return props.runs.filter(r => {
    const name = extractName(r.id).toLowerCase()
    return name.includes(q) || r.id.toLowerCase().includes(q)
  })
})

function extractName(id) {
  if (!id) return ''
  const match = id.match(/^(.+?)_(\d{8}_\d{6})/)
  return match ? match[1].replace(/_/g, ' ') : id
}

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

function formatDate(id) {
  const match = id.match(/(\d{8})_\d{6}/)
  if (!match) return '内置空间'
  const d = match[1]
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`
}

function chapterSummary(run) {
  const chapters = run?.chapters
  if (!chapters || typeof chapters !== 'object') return ''
  const total = Number(chapters.total || 0)
  const materialized = Number(chapters.materialized || 0)
  const active = Number(chapters.active || 0)
  if (!total && !materialized) return ''
  return `章 ${materialized || active}/${total || materialized || 0}`
}

function deliveryLabel(status) {
  const value = String(status || '')
  if (!value || value === 'new') return ''
  if (value === 'ready') return '已完成'
  if (value === 'ready_with_warnings') return '不可交付：存在校验错误'
  if (value === 'draft_with_gaps') return '进行中'
  return value
}

function deliveryClass(status) {
  const value = String(status || '')
  if (value === 'ready') return 'badge-success'
  if (value === 'ready_with_warnings') return 'badge-danger'
  if (value === 'draft_with_gaps') return 'badge-warning'
  return 'badge-info'
}
</script>

<style scoped>
.workspace-selector-container {
  padding: 32px 40px;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

.selector-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 28px;
  flex-wrap: wrap;
  gap: 16px;
}

.header-title {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 6px 0;
}

.header-desc {
  font-size: 14px;
  color: #64748b;
  margin: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.search-box {
  position: relative;
  display: flex;
  align-items: center;
}

.search-icon {
  position: absolute;
  left: 12px;
  color: #94a3b8;
  pointer-events: none;
}

.search-input {
  padding: 8px 12px 8px 36px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: #ffffff;
  font-size: 14px;
  width: 220px;
  transition: all 0.2s ease;
}

.search-input:focus {
  outline: none;
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.create-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  font-weight: 600;
  border-radius: 8px;
}

.selector-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  color: #64748b;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #e2e8f0;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-bottom: 12px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.selector-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 20px;
}

.grid-card {
  background: #ffffff;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  padding: 20px;
  display: flex;
  flex-direction: column;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
}

.create-card {
  border: 2px dashed #cbd5e1;
  background: rgba(248, 250, 252, 0.6);
  justify-content: center;
  align-items: center;
  min-height: 180px;
  cursor: pointer;
}

.create-card:hover {
  border-color: #2563eb;
  background: #eff6ff;
  transform: translateY(-2px);
}

.create-icon-wrapper {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: #dbeafe;
  color: #2563eb;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
  transition: transform 0.2s;
}

.create-card:hover .create-icon-wrapper {
  transform: scale(1.1);
}

.create-card-text {
  font-size: 15px;
  font-weight: 600;
  color: #1e40af;
}

.workspace-card {
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

.workspace-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 10px 20px -5px rgba(0, 0, 0, 0.08), 0 4px 6px -2px rgba(0, 0, 0, 0.03);
  border-color: #bfdbfe;
}

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.card-avatar {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  color: #ffffff;
  font-weight: 700;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-badge {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 999px;
  font-weight: 600;
}

.badge-success {
  background: #dcfce7;
  color: #15803d;
}

.badge-warning {
  background: #fef3c7;
  color: #b45309;
}

.badge-info {
  background: #f1f5f9;
  color: #475569;
}

.card-body {
  flex: 1;
  margin-bottom: 16px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
  margin: 0 0 10px 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #64748b;
}

.chapter-info {
  color: #2563eb;
  font-weight: 500;
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 14px;
  border-top: 1px solid #f1f5f9;
  gap: 8px;
}

.btn-enter {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  padding: 6px 12px;
  background: #f0f9ff;
  color: #0284c7;
  border: 1px solid #bae6fd;
  border-radius: 6px;
  font-weight: 500;
  transition: all 0.2s;
}

.workspace-card:hover .btn-enter {
  background: #0284c7;
  color: #ffffff;
  border-color: #0284c7;
}

.btn-delete {
  background: transparent;
  border: none;
  color: #94a3b8;
  padding: 6px;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.btn-delete:hover {
  background: #fef2f2;
  color: #ef4444;
}

.selector-empty {
  text-align: center;
  padding: 48px 0;
  color: #94a3b8;
  font-size: 15px;
}
</style>
