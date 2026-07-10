<template>
  <div class="file-explorer">
    <div class="file-explorer-header">
      <h3>工作区文件</h3>
      <button class="btn btn-sm btn-icon" @click="refresh" title="刷新">&#x21BB;</button>
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
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'

const props = defineProps({
  runId: { type: String, required: true },
})

const emit = defineEmits(['preview-file'])

const loading = ref(false)
const sections = reactive([
  { key: 'tender', label: '招标文件', open: true, items: [] },
  { key: 'company', label: '公司资料', open: true, items: [] },
  { key: 'template', label: '标书模板', open: true, items: [] },
  { key: 'inputs', label: '标准化输入', open: false, items: [] },
  { key: 'outputs', label: '输出产物', open: true, items: [] },
])

async function refresh() {
  loading.value = true
  try {
    const data = await fetch('/api/status').then(r => r.json())
    if (data && data.inputs) {
      sections.find(s => s.key === 'tender').items = (data.sources?.tender || []).map(f => ({ name: f.name, path: `sources/tender/${f.name}`, size: f.size }))
      sections.find(s => s.key === 'company').items = (data.sources?.company || []).map(f => ({ name: f.name, path: `sources/company/${f.name}`, size: f.size }))
      sections.find(s => s.key === 'template').items = (data.sources?.template || []).map(f => ({ name: f.name, path: `sources/template/${f.name}`, size: f.size }))
    }
    if (data && data.artifacts) {
      const inputs = []
      const outputs = []
      for (const [key, val] of Object.entries(data.artifacts)) {
        if (val && typeof val === 'boolean' && val) {
          outputs.push({ name: key, path: key, size: 0 })
        }
      }
      sections.find(s => s.key === 'outputs').items = outputs
    }
    if (data && data.files) {
      const inFiles = data.files.inputs || []
      sections.find(s => s.key === 'inputs').items = inFiles.map(f => ({ name: f.name || f, path: `inputs/${f.name || f}`, size: f.size || 0 }))
    }
  } catch (e) { /* ignore */ }
  loading.value = false
}

function iconFor(item) {
  const n = item.name.toLowerCase()
  if (n.endsWith('.pdf')) return '📕'
  if (n.endsWith('.docx') || n.endsWith('.doc')) return '📄'
  if (n.endsWith('.md')) return '📝'
  if (n.endsWith('.json')) return '📋'
  if (n.endsWith('.txt')) return '📃'
  return '📎'
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'K'
  return (bytes / (1024 * 1024)).toFixed(1) + 'M'
}

function preview(item) {
  emit('preview-file', item.path)
}

onMounted(refresh)
watch(() => props.runId, refresh)
</script>
