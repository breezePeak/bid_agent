<template>
  <div class="project-home">
    <header class="page-header">
      <div>
        <h2 class="page-title">{{ displayName }}</h2>
        <p class="page-desc">章节 Workspace · 正式版组装 · 不替代 H1 目录确认</p>
      </div>
      <div class="actions">
        <button type="button" class="btn" :disabled="busy" @click="load">刷新</button>
        <button type="button" class="btn btn-primary" :disabled="busy" @click="compose">检查正式组装</button>
      </div>
    </header>

    <div v-if="error" class="banner error" role="alert">{{ error }}</div>

    <section v-if="composeResult" class="compose-card" :class="{ ok: composeResult.export_allowed }">
      <div class="compose-title">
        {{ composeResult.export_allowed ? '可导出正式稿' : '仅草稿预览（存在待确认章节）' }}
      </div>
      <div class="compose-meta">document_hash: {{ composeResult.document_hash }}</div>
      <div class="compose-meta">
        正式章节 {{ composeResult.chapter_manifest?.length || 0 }} ·
        正文块 {{ composeResult.blocks?.length || 0 }}
      </div>
      <div v-if="composeResult.pending_chapters?.length" class="compose-pending">
        待确认：
        <router-link
          v-for="item in composeResult.pending_chapters"
          :key="item.chapter_id"
          class="pending-link"
          :to="`/business/${workspaceId}/chapters/${item.chapter_id}`"
        >{{ item.chapter_id }}</router-link>
      </div>
    </section>

    <section class="stats">
      <div class="stat"><span class="num">{{ items.length }}</span><span class="label">目录章节</span></div>
      <div class="stat"><span class="num">{{ materializedCount }}</span><span class="label">已物化</span></div>
      <div class="stat"><span class="num">{{ formalCount }}</span><span class="label">已有正式版</span></div>
      <div class="stat"><span class="num">{{ draftCount }}</span><span class="label">仅草稿</span></div>
    </section>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>章节</th>
            <th>状态</th>
            <th>chapter rev</th>
            <th>head / formal</th>
            <th>审批</th>
            <th>更新时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!items.length && !busy">
            <td colspan="7" class="empty-row">
              暂无章节。请先在「流水线」完成目录规划并晋级 ChapterBlueprint，然后在此打开章节。
            </td>
          </tr>
          <tr v-for="item in items" :key="item.chapter_id">
            <td>
              <div class="title">{{ item.title || item.chapter_id }}</div>
              <div class="id">{{ item.chapter_id }}</div>
            </td>
            <td>
              <span class="pill" :class="statusClass(item)">{{ chapterStatusLabel(item) }}</span>
            </td>
            <td>{{ item.chapter_revision || 0 }}</td>
            <td>{{ item.head_content_revision || 0 }} / {{ item.formal_content_revision || 0 }}</td>
            <td>{{ approvalLabel(item.approval_status) }}</td>
            <td class="time">{{ formatTime(item.updated_at) }}</td>
            <td class="ops">
              <router-link class="link" :to="`/business/${workspaceId}/chapters/${item.chapter_id}`">
                打开
              </router-link>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { fetchChapters, fetchDocumentCompose } from '../api'
import { chapterStatusLabel } from '../api/chapterContracts'

const props = defineProps({
  workspaceId: { type: String, required: true },
})

const items = ref([])
const busy = ref(false)
const error = ref('')
const composeResult = ref(null)

const displayName = computed(() => {
  const id = props.workspaceId || ''
  const match = id.match(/^(.+?)_(\d{8}_\d{6})/)
  return match ? match[1].replace(/_/g, ' ') : id
})

const materializedCount = computed(() => items.value.filter(item => item.materialized || item.status === 'active').length)
const formalCount = computed(() => items.value.filter(item => Number(item.formal_content_revision || 0) > 0).length)
const draftCount = computed(() => items.value.filter(item => {
  const head = Number(item.head_content_revision || 0)
  const formal = Number(item.formal_content_revision || 0)
  return head > 0 && head !== formal
}).length)

function approvalLabel(status) {
  const map = {
    not_started: '未开始',
    draft: '草稿',
    pending_approval: '待确认',
    approved: '已确认',
  }
  return map[String(status || 'not_started')] || status || '未开始'
}

function statusClass(item) {
  if (item.status === 'archived') return 'archived'
  if (item.approval_status === 'approved' || Number(item.formal_content_revision || 0) > 0) return 'ok'
  if (Number(item.head_content_revision || 0) > 0) return 'draft'
  if (item.materialized || item.status === 'active') return 'ready'
  return 'projected'
}

function formatTime(value) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch (_) {
    return value
  }
}

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
.project-home {
  padding: 16px 20px;
  overflow: auto;
  height: 100%;
  background: #f8fafc;
}
.page-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
  align-items: flex-start;
}
.page-title { margin: 0; font-size: 20px; color: #0f172a; }
.page-desc { margin: 6px 0 0; color: #64748b; font-size: 13px; }
.actions { display: flex; gap: 8px; }
.banner.error {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.compose-card {
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
  font-size: 13px;
}
.compose-card.ok {
  background: #ecfdf5;
  border-color: #a7f3d0;
}
.compose-title { font-weight: 700; margin-bottom: 4px; }
.compose-meta { color: #64748b; margin-top: 2px; word-break: break-all; }
.compose-pending { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.pending-link { color: #2563eb; text-decoration: none; }
.stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.stat {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 12px;
}
.stat .num { display: block; font-size: 22px; font-weight: 700; color: #0f172a; }
.stat .label { font-size: 12px; color: #64748b; }
.table-wrap {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  overflow: auto;
}
table { width: 100%; border-collapse: collapse; }
th, td {
  border-bottom: 1px solid #e2e8f0;
  text-align: left;
  padding: 12px 10px;
  font-size: 13px;
  vertical-align: top;
}
th { color: #64748b; font-weight: 600; background: #f8fafc; }
.title { font-weight: 600; color: #0f172a; }
.id { color: #94a3b8; font-size: 12px; margin-top: 2px; }
.time { color: #64748b; white-space: nowrap; }
.ops .link { color: #2563eb; text-decoration: none; font-weight: 600; }
.empty-row { text-align: center; color: #94a3b8; padding: 28px 12px !important; }
.pill {
  display: inline-flex;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: #f1f5f9;
  color: #475569;
}
.pill.ok { background: #dcfce7; color: #166534; }
.pill.draft { background: #ffedd5; color: #9a3412; }
.pill.ready { background: #dbeafe; color: #1d4ed8; }
.pill.projected { background: #f1f5f9; color: #64748b; }
.pill.archived { background: #e2e8f0; color: #475569; }
@media (max-width: 900px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
