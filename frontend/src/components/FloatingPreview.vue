<template>
  <div v-if="visible" class="floating-preview">
    <div class="floating-preview-header">
      <h3>{{ title }}</h3>
      <button class="btn btn-icon" @click="$emit('close')">&times;</button>
    </div>
    <div class="floating-preview-body">
      <!-- loading -->
      <div v-if="loading" class="preview-loading">加载中...</div>

      <!-- step detail view -->
      <template v-else-if="mode === 'step-detail' && stepDetail">
        <!-- summary -->
        <div v-if="stepDetail.summary && Object.keys(stepDetail.summary).length" class="preview-section">
          <h4>概要</h4>
          <div class="preview-kv">
            <div v-for="(val, key) in stepDetail.summary" :key="key" class="preview-kv-row">
              <span class="preview-kv-key">{{ key }}</span>
              <span class="preview-kv-value">{{ formatValue(val) }}</span>
            </div>
          </div>
        </div>

        <!-- timing -->
        <div v-if="stepDetail.timing && stepDetail.timing.duration_label" class="preview-section">
          <h4>耗时</h4>
          <span>{{ stepDetail.timing.duration_label }}</span>
        </div>

        <!-- score points -->
        <div v-if="stepDetail.details && stepDetail.details.score_point_rows && stepDetail.details.score_point_rows.length" class="preview-section">
          <h4>评分点 ({{ stepDetail.details.score_point_rows.length }})</h4>
          <div class="score-list">
            <div v-for="row in stepDetail.details.score_point_rows.slice(0, 20)" :key="row.id || row.index" class="score-item">
              <div class="score-item-head">
                <span class="score-item-id">{{ row.id || row.index }}</span>
                <span class="score-item-score" v-if="row.score">{{ row.score }}分</span>
              </div>
              <div class="score-item-title">{{ row.title || row.name }}</div>
              <div class="score-item-category" v-if="row.category">{{ row.category }}</div>
            </div>
            <div v-if="stepDetail.details.score_point_rows.length > 20" class="preview-more">
              还有 {{ stepDetail.details.score_point_rows.length - 20 }} 条...
            </div>
          </div>
        </div>

        <!-- review rows -->
        <div v-if="stepDetail.details && stepDetail.details.review_rows && stepDetail.details.review_rows.length" class="preview-section">
          <h4>审核问题 ({{ stepDetail.details.review_rows.length }})</h4>
          <div class="review-list">
            <div v-for="row in stepDetail.details.review_rows.slice(0, 15)" :key="row.chapter_id || row.index" class="review-item">
              <div class="review-item-head">
                <span>{{ row.chapter_id || row.name || '' }}</span>
                <span class="review-problem-count" v-if="row.problem_count != null">{{ row.problem_count }} 个问题</span>
              </div>
            </div>
          </div>
        </div>

        <!-- artifacts: requires & produces -->
        <div v-if="stepDetail.requires && stepDetail.requires.length" class="preview-section">
          <h4>依赖文件</h4>
          <div class="artifact-list">
            <div v-for="art in stepDetail.requires" :key="art.path" class="artifact-item">
              <span class="artifact-path">{{ art.path }}</span>
              <span class="artifact-status" :class="{ exists: art.exists }">{{ art.exists ? '✓' : '✗' }}</span>
              <button v-if="art.previewable && art.exists" class="btn btn-sm" @click="previewFile(art.path)">预览</button>
            </div>
          </div>
        </div>

        <div v-if="stepDetail.produces && stepDetail.produces.length" class="preview-section">
          <h4>产物文件</h4>
          <div class="artifact-list">
            <div v-for="art in stepDetail.produces" :key="art.path" class="artifact-item">
              <span class="artifact-path">{{ art.path }}</span>
              <span class="artifact-status" :class="{ exists: art.exists }">{{ art.exists ? '✓' : '✗' }}</span>
              <span v-if="art.size" class="artifact-size">{{ formatSize(art.size) }}</span>
              <button v-if="art.previewable && art.exists" class="btn btn-sm" @click="previewFile(art.path)">预览</button>
            </div>
          </div>
        </div>

        <!-- agent runs -->
        <div v-if="stepDetail.agent_runs && stepDetail.agent_runs.length" class="preview-section">
          <h4>Agent 运行记录</h4>
          <div class="agent-runs-list">
            <div v-for="run in stepDetail.agent_runs.slice(0, 10)" :key="run.agent_name + run.chapter_id" class="agent-run-item">
              <div class="agent-run-head">
                <span class="agent-run-name">{{ run.agent_name }}</span>
                <span class="agent-run-chapter" v-if="run.chapter_id">{{ run.chapter_id }}</span>
              </div>
              <div class="agent-run-meta">
                <span v-if="run.prompt_version">v{{ run.prompt_version }}</span>
                <span v-if="run.duration_ms">{{ (run.duration_ms / 1000).toFixed(1) }}s</span>
              </div>
            </div>
          </div>
        </div>

        <!-- history -->
        <div v-if="stepDetail.history && stepDetail.history.length" class="preview-section">
          <h4>执行历史</h4>
          <div class="history-list">
            <div v-for="h in stepDetail.history.slice(0, 10)" :key="h.updated_at" class="history-item">
              <span class="history-time">{{ h.updated_at }}</span>
              <span class="history-status">{{ h.status }}</span>
              <span class="history-msg">{{ h.message }}</span>
            </div>
          </div>
        </div>
      </template>

      <!-- doc editor mode -->
      <template v-else-if="mode === 'doc-editor'">
        <DocEditor :run-id="runId" @reload="$emit('reload')" />
      </template>

      <!-- file preview mode -->
      <template v-else-if="mode === 'file-preview'">
        <div class="file-preview-content">
          <pre v-if="fileContent" class="file-preview-text">{{ fileContent }}</pre>
          <div v-else class="preview-loading">加载文件内容...</div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import DocEditor from './DocEditor.vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  mode: { type: String, default: 'step-detail' },
  command: { type: String, default: '' },
  runId: { type: String, required: true },
})

const emit = defineEmits(['close'])

const title = ref('详情')
const loading = ref(false)
const stepDetail = ref(null)
const fileContent = ref('')
const previewFilePath = ref('')

watch(() => [props.visible, props.command, props.mode], async () => {
  if (!props.visible) return

  if (props.mode === 'step-detail' && props.command) {
    loading.value = true
    try {
      const workspace = encodeURIComponent(props.runId)
      const res = await fetch(`/api/v2/workspaces/${workspace}/workflow-step-detail?command=${encodeURIComponent(props.command)}`).then(r => r.json())
      if (res.ok) {
        stepDetail.value = res
        title.value = res.step?.label || props.command
      }
    } catch (e) {
      console.error('加载步骤详情失败', e)
    } finally {
      loading.value = false
    }
  } else if (props.mode === 'doc-editor') {
    title.value = '文档编辑'
    loading.value = false
  }
}, { immediate: true })

async function previewFile(path) {
  loading.value = true
  title.value = path
  previewFilePath.value = path
  try {
    const workspace = encodeURIComponent(props.runId)
    const res = await fetch(`/api/v2/workspaces/${workspace}/files/preview?path=${encodeURIComponent(path)}`).then(r => r.json())
    if (res.ok && res.content) {
      fileContent.value = typeof res.content === 'string' ? res.content : JSON.stringify(res.content, null, 2)
    } else if (res.ok && res.message) {
      fileContent.value = res.message
    }
  } catch (e) {
    fileContent.value = '加载失败'
  } finally {
    loading.value = false
  }
}

function formatValue(val) {
  if (Array.isArray(val)) return val.join(', ')
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}
</script>
