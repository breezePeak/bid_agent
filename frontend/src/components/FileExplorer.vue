<template>
  <div class="file-explorer">
    <div class="file-explorer-header">
      <h3>工作区文件</h3>
      <div class="fe-header-actions">
        <span class="fe-total" v-if="total > 0">{{ total }}</span>
        <button class="btn btn-sm" @click="refresh" :disabled="loading" title="重新读取工作区文件">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </div>
    <div class="file-explorer-body">
      <div v-if="loading" class="fe-loading">加载中...</div>
      <template v-else>
        <div v-for="section in sections" :key="section.key" class="fe-section">
          <div class="fe-section-title" @click="section.open = !section.open">
            <span class="fe-arrow">{{ section.open ? '▾' : '▸' }}</span>
            <span>{{ section.label }}</span>
            <span class="fe-count">{{ section.items.length }}</span>
          </div>
          <div v-if="section.open" class="fe-items">
            <div
              v-for="item in section.items"
              :key="item.path"
              class="fe-item"
              :class="{ active: previewPath === item.path }"
              :title="item.path"
              @click="preview(item)"
            >
              <span class="fe-item-icon">{{ iconFor(item) }}</span>
              <span class="fe-item-name">{{ item.name }}</span>
              <span class="fe-item-size" v-if="item.size">{{ formatSize(item.size) }}</span>
            </div>
            <div v-if="section.items.length === 0" class="fe-empty">暂无文件</div>
          </div>
        </div>
        <div v-if="!sections.length" class="fe-empty">暂无工作区文件</div>
      </template>
    </div>

    <div v-if="previewPath" class="fe-preview">
      <div class="fe-preview-head">
        <span class="fe-preview-title" :title="previewPath">{{ previewPath }}</span>
        <button class="btn btn-sm btn-icon" @click="closePreview" title="关闭">×</button>
      </div>
      <div v-if="previewLoading" class="fe-loading">加载预览...</div>
      <div v-else-if="previewKind === 'list'" class="fe-preview-list">
        <div
          v-for="item in previewItems"
          :key="item.path"
          class="fe-item"
          @click="preview(item)"
        >
          <span class="fe-item-icon">{{ iconFor(item) }}</span>
          <span class="fe-item-name">{{ item.name }}</span>
          <span class="fe-item-size" v-if="item.size">{{ formatSize(item.size) }}</span>
        </div>
        <div v-if="!previewItems.length" class="fe-empty">目录为空</div>
      </div>
      <pre v-else class="fe-preview-content">{{ previewContent }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({
  runId: { type: String, required: true },
})

const emit = defineEmits(['preview-file'])

const loading = ref(false)
const sections = ref([])
const total = ref(0)
const previewPath = ref('')
const previewLoading = ref(false)
const previewContent = ref('')
const previewKind = ref('text')
const previewItems = ref([])

const defaultSections = () => ([
  { key: 'tender', label: '招标文件', open: false, items: [] },
  { key: 'company', label: '公司资料', open: false, items: [] },
  { key: 'template', label: '标书模板', open: false, items: [] },
  { key: 'outputs', label: '最终输出', open: false, items: [] },
])

async function refresh() {
  loading.value = true
  try {
    const data = await fetch('/api/workspace-files').then(r => r.json())
    if (data?.ok && Array.isArray(data.sections)) {
      const prevOpen = Object.fromEntries((sections.value || []).map(s => [s.key, s.open]))
      sections.value = data.sections.map(section => ({
        ...section,
        // 默认全部折叠；用户手动展开后刷新时保留状态
        open: prevOpen[section.key] !== undefined ? prevOpen[section.key] : false,
        items: section.items || [],
      }))
      total.value = data.total || sections.value.reduce((n, s) => n + s.items.length, 0)
    } else {
      sections.value = defaultSections()
      total.value = 0
    }
  } catch (e) {
    sections.value = defaultSections()
    total.value = 0
  }
  loading.value = false
}

function iconFor(item) {
  const n = String(item.name || '').toLowerCase()
  if (n.endsWith('.pdf')) return '📕'
  if (n.endsWith('.docx') || n.endsWith('.doc')) return '📄'
  if (n.endsWith('.md')) return '📝'
  if (n.endsWith('.json') || n.endsWith('.jsonl')) return '📋'
  if (n.endsWith('.txt') || n.endsWith('.log')) return '📃'
  return '📎'
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'K'
  return (bytes / (1024 * 1024)).toFixed(1) + 'M'
}

async function preview(item) {
  const path = item.path
  if (!path) return
  emit('preview-file', path)
  previewPath.value = path
  previewLoading.value = true
  previewContent.value = ''
  previewItems.value = []
  previewKind.value = 'text'
  try {
    const data = await fetch(`/api/file-preview?path=${encodeURIComponent(path)}`).then(r => r.json())
    if (!data?.ok) {
      previewContent.value = data?.message || '预览失败'
      return
    }
    if (data.kind === 'list') {
      previewKind.value = 'list'
      previewItems.value = data.items || []
      return
    }
    if (data.kind === 'docx') {
      const blocks = (data.blocks || [])
        .map(b => b.type === 'table'
          ? (b.rows || []).map(row => row.join(' | ')).join('\n')
          : (b.text || ''))
        .filter(Boolean)
      previewContent.value = blocks.join('\n\n') || 'Word 文档没有可抽取文本。'
      if (data.truncated) previewContent.value += '\n\n…（已截取）'
      return
    }
    if (data.kind === 'binary') {
      previewContent.value = data.message || '该文件类型不支持内嵌预览。'
      return
    }
    previewContent.value = data.content || ''
    if (data.truncated) previewContent.value += '\n\n…（已截取前 30000 字符）'
  } catch (e) {
    previewContent.value = `预览失败：${e}`
  } finally {
    previewLoading.value = false
  }
}

function closePreview() {
  previewPath.value = ''
  previewContent.value = ''
  previewItems.value = []
}

onMounted(() => {
  refresh()
})
watch(() => props.runId, () => {
  closePreview()
  refresh()
})
</script>
