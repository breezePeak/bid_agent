<template>
  <div class="project-home">
    <header>
      <div>
        <h2>{{ workspaceId }}</h2>
        <p>章节 Workspace · 正式版组装</p>
      </div>
      <div class="actions">
        <router-link class="btn" :to="`/business/${workspaceId}/pipeline`">流水线</router-link>
        <button type="button" class="btn" :disabled="busy" @click="load">刷新</button>
        <button type="button" class="btn" :disabled="busy" @click="compose">检查正式组装</button>
      </div>
    </header>
    <div v-if="error" class="error">{{ error }}</div>
    <div v-if="composeResult" class="compose">
      <strong>{{ composeResult.export_allowed ? '可导出正式稿' : '仅草稿预览' }}</strong>
      <div>document_hash: {{ composeResult.document_hash }}</div>
      <div v-if="composeResult.pending_chapters?.length">
        待确认：{{ composeResult.pending_chapters.map(item => item.chapter_id).join(', ') }}
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>章节</th>
          <th>状态</th>
          <th>chapter rev</th>
          <th>head / formal</th>
          <th>approval</th>
          <th>更新时间</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.chapter_id">
          <td>
            <div class="title">{{ item.title || item.chapter_id }}</div>
            <div class="id">{{ item.chapter_id }}</div>
          </td>
          <td>{{ chapterStatusLabel(item) }}</td>
          <td>{{ item.chapter_revision || 0 }}</td>
          <td>{{ item.head_content_revision || 0 }} / {{ item.formal_content_revision || 0 }}</td>
          <td>{{ item.approval_status || 'not_started' }}</td>
          <td>{{ item.updated_at || '-' }}</td>
          <td>
            <router-link :to="`/business/${workspaceId}/chapters/${item.chapter_id}`">打开</router-link>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { fetchChapters, fetchDocumentCompose } from '../api'
import { chapterStatusLabel } from '../api/chapterContracts'

const props = defineProps({
  workspaceId: { type: String, required: true },
})

const items = ref([])
const busy = ref(false)
const error = ref('')
const composeResult = ref(null)

async function load() {
  busy.value = true
  error.value = ''
  try {
    const { data } = await fetchChapters(props.workspaceId)
    if (!data.ok) throw new Error(data.message || '加载章节失败')
    items.value = data.chapters?.items || []
  } catch (e) {
    error.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

async function compose() {
  busy.value = true
  error.value = ''
  try {
    const { data } = await fetchDocumentCompose(props.workspaceId)
    if (!data.ok) throw new Error(data.message || '组装失败')
    composeResult.value = data.document
  } catch (e) {
    error.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.project-home { padding: 16px; overflow: auto; height: 100%; }
header { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
h2 { margin: 0; }
p { margin: 4px 0 0; color: #64748b; font-size: 13px; }
.actions { display: flex; gap: 8px; align-items: flex-start; }
.btn { border: 1px solid #cbd5e1; background: #fff; border-radius: 6px; padding: 6px 10px; text-decoration: none; color: inherit; cursor: pointer; }
table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { border-bottom: 1px solid #e2e8f0; text-align: left; padding: 10px 8px; font-size: 13px; vertical-align: top; }
.title { font-weight: 600; }
.id { color: #94a3b8; font-size: 12px; }
.error { color: #b91c1c; margin-bottom: 12px; }
.compose { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; padding: 10px; margin-bottom: 12px; font-size: 13px; }
</style>
