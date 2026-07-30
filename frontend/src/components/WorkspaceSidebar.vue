<template>
  <aside class="sidebar" :class="{ collapsed: collapsed }">
    <div class="sidebar-header">
      <h2 v-if="!collapsed">工作空间</h2>
      <button class="btn btn-sm btn-icon sidebar-collapse-btn" @click="$emit('toggle')" :title="collapsed ? '展开' : '折叠'">
        <svg v-if="collapsed" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M9 18l6-6-6-6"/>
        </svg>
        <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M15 18l-6-6 6-6"/>
        </svg>
      </button>
    </div>
    <div class="sidebar-list">
      <div v-if="loading" class="sidebar-loading">
        <span v-if="!collapsed">加载中...</span>
      </div>
      <div
        v-for="run in runs"
        :key="run.id"
        class="sidebar-item"
        :class="{ active: run.id === activeRunId }"
        :title="!collapsed ? '' : extractName(run.id)"
        @click="$emit('select', run.id)"
      >
        <div class="sidebar-item-thumb" :style="{ backgroundColor: thumbColor(extractName(run.id)) }">
          {{ firstChar(extractName(run.id)) }}
        </div>
        <div v-if="!collapsed" class="sidebar-item-info">
          <div class="sidebar-item-name">{{ extractName(run.id) }}</div>
          <div class="sidebar-item-meta">
            <span class="sidebar-item-date">{{ formatDate(run.id) }}</span>
            <span v-if="run.progress" class="sidebar-item-progress">
              {{ run.progress.done }}/{{ run.progress.total }}
            </span>
            <span v-if="chapterSummary(run)" class="sidebar-item-chapters" :title="chapterSummary(run)">
              {{ chapterSummary(run) }}
            </span>
          </div>
          <div v-if="run.delivery_status" class="sidebar-item-status">
            {{ deliveryLabel(run.delivery_status) }}
          </div>
        </div>
        <button
          v-if="!collapsed && deletable"
          class="sidebar-item-delete"
          title="删除工作空间"
          @click.stop="$emit('delete', run.id)"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            <line x1="10" y1="11" x2="10" y2="17"/>
            <line x1="14" y1="11" x2="14" y2="17"/>
          </svg>
        </button>
      </div>
      <div v-if="!loading && runs.length === 0 && !collapsed" class="sidebar-empty">
        暂无工作空间
      </div>
    </div>
    <div v-if="!collapsed" class="sidebar-footer">
      <button class="btn btn-primary btn-block" @click="$emit('create')">
        + 新建工作空间
      </button>
    </div>
    <div v-else class="sidebar-footer-mini">
      <button class="btn btn-primary btn-icon" @click="$emit('create')" title="新建工作空间">+</button>
    </div>
  </aside>
</template>

<script setup>
defineProps({
  runs: { type: Array, default: () => [] },
  activeRunId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  deletable: { type: Boolean, default: true },
})

defineEmits(['select', 'toggle', 'create', 'delete'])

const THUMB_COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed', '#db2777', '#2563eb']

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
  const match = id.match(/^(.+?)_(\d{8}_\d{6})/)
  return match ? match[1].replace(/_/g, ' ') : id
}

function formatDate(id) {
  const match = id.match(/(\d{8})_\d{6}/)
  if (!match) return ''
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
  if (value === 'ready' || value === 'ready_with_warnings') return '可交付'
  if (value === 'draft_with_gaps') return '草稿'
  return value
}
</script>

<style scoped>
.sidebar-item-chapters {
  margin-left: 6px;
  color: #2563eb;
  font-size: 11px;
}
.sidebar-item-status {
  margin-top: 2px;
  font-size: 11px;
  color: #64748b;
}
</style>
