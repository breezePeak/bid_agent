<template>
  <div class="chapter-workspace">
    <header class="top">
      <div class="title-block">
        <router-link :to="`/business/${workspaceId}`" class="back">← 返回项目主页</router-link>
        <h2>{{ chapter?.title || chapterId }}</h2>
        <div class="sub">
          <span class="pill" :class="statusPill">{{ statusText }}</span>
          <span>chapter_rev {{ chapter?.chapter_revision || 0 }}</span>
          <span>head {{ chapter?.head_content_revision || 0 }}</span>
          <span>formal {{ chapter?.formal_content_revision || 0 }}</span>
          <span>context {{ chapter?.head_context_revision || 0 }}</span>
        </div>
      </div>
      <div class="actions">
        <button type="button" class="btn" :disabled="busy" @click="materialize">
          {{ chapter?.materialized ? '刷新物化' : '打开/物化' }}
        </button>
        <button type="button" class="btn" :disabled="busy || !chapter?.materialized" @click="generateDraft">
          生成草稿
        </button>
        <button type="button" class="btn" :disabled="busy || !chapter?.materialized" @click="loadRevisions">
          版本
        </button>
        <button type="button" class="btn btn-primary" :disabled="busy || !canApprove" @click="approveHead">
          H2 确认 head
        </button>
      </div>
    </header>

    <div v-if="error" class="error" role="alert">{{ error }}</div>
    <div v-if="message" class="message">{{ message }}</div>

    <div class="body">
      <div class="editor-pane">
        <div v-if="!chapter?.materialized" class="placeholder">
          <p>章节尚未物化。点击「打开/物化」从已晋级 Blueprint 创建章节 Workspace。</p>
        </div>
        <ContentBlockEditor
          v-else
          ref="editorRef"
          :blocks="editorBlocks"
          :busy="busy"
          :remote-hint="remoteHint"
          @save="onSaveBlocks"
        />
      </div>
      <ChapterContextPanel
        :items="contextItems"
        :head-revision="chapter?.head_context_revision || 0"
      />
    </div>

    <ChapterRevisionDrawer
      :open="showRevisions"
      :revisions="revisions"
      :head-revision="chapter?.head_content_revision || 0"
      :formal-revision="chapter?.formal_content_revision || 0"
      @close="showRevisions = false"
      @restore="onRestore"
      @approve="onApproveRevision"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  fetchChapter,
  fetchChapterRevisions,
  submitV3Command,
  fetchSnapshot,
} from '../api'
import ContentBlockEditor from './ContentBlockEditor.vue'
import ChapterContextPanel from './ChapterContextPanel.vue'
import ChapterRevisionDrawer from './ChapterRevisionDrawer.vue'

const props = defineProps({
  workspaceId: { type: String, required: true },
  chapterId: { type: String, required: true },
})

const chapter = ref(null)
const revisions = ref([])
const workspaceRevision = ref(0)
const busy = ref(false)
const error = ref('')
const message = ref('')
const showRevisions = ref(false)
const remoteHint = ref('')
const editorRef = ref(null)
let pollTimer = null

const editorBlocks = computed(() => chapter.value?.content?.blocks || [])
const contextItems = computed(() => chapter.value?.context?.items || [])
const canApprove = computed(() => {
  const head = Number(chapter.value?.head_content_revision || 0)
  const formal = Number(chapter.value?.formal_content_revision || 0)
  return Boolean(chapter.value?.materialized) && head > 0 && head !== formal
})

const statusText = computed(() => {
  if (!chapter.value) return '加载中'
  if (chapter.value.status === 'archived') return '已归档'
  if (chapter.value.approval_status === 'approved') return '已确认正式版'
  if (Number(chapter.value.head_content_revision || 0) > 0) return '草稿'
  if (chapter.value.materialized) return '已物化'
  return '未打开'
})

const statusPill = computed(() => {
  if (chapter.value?.approval_status === 'approved') return 'ok'
  if (Number(chapter.value?.head_content_revision || 0) > 0) return 'draft'
  if (chapter.value?.materialized) return 'ready'
  return ''
})

async function refresh(options = {}) {
  const { force = false } = options
  const dirty = editorRef.value?.dirty
  if (dirty && !force) {
    remoteHint.value = '远端章节已变化；本地有未保存编辑，未覆盖草稿'
  }
  const { data } = await fetchChapter(props.workspaceId, props.chapterId)
  if (!data.ok) throw new Error(data.message || '加载章节失败')
  if (!(dirty && !force)) {
    chapter.value = data.chapter
    remoteHint.value = ''
  } else {
    chapter.value = {
      ...data.chapter,
      content: chapter.value?.content,
    }
  }
  const snap = await fetchSnapshot(props.workspaceId)
  if (snap.data?.ok) {
    workspaceRevision.value = Number(snap.data.snapshot?.workspace_revision || 0)
  }
}

async function loadRevisions() {
  showRevisions.value = true
  const rev = await fetchChapterRevisions(props.workspaceId, props.chapterId)
  if (rev.data?.ok) revisions.value = rev.data.revisions || []
}

async function runCommand(kind, payload, successText = '') {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    // Refresh workspace revision right before write to reduce REVISION_CONFLICT.
    const snap = await fetchSnapshot(props.workspaceId)
    if (snap.data?.ok) {
      workspaceRevision.value = Number(snap.data.snapshot?.workspace_revision || 0)
    }
    // Also refresh chapter_revision for CAS when local metadata may be stale.
    if (chapter.value?.materialized) {
      const latest = await fetchChapter(props.workspaceId, props.chapterId)
      if (latest.data?.ok && !editorRef.value?.dirty) {
        chapter.value = latest.data.chapter
      } else if (latest.data?.ok) {
        payload = {
          ...payload,
          expected_chapter_revision: Number(latest.data.chapter?.chapter_revision || 0),
        }
      }
    }
    const { data } = await submitV3Command(props.workspaceId, {
      kind,
      payload,
      expected_revision: workspaceRevision.value,
      idempotency_key: `${kind}-${props.chapterId}-${Date.now()}`,
    })
    if (!data.ok) {
      const detail = data.receipt?.error?.message || data.message || data.receipt?.message
      throw new Error(detail || '命令失败')
    }
    if (data.receipt?.result?.chapter) {
      chapter.value = {
        ...(chapter.value || {}),
        ...data.receipt.result.chapter,
        content: data.receipt.result.content || chapter.value?.content,
        context: chapter.value?.context,
        materialized: true,
      }
    }
    await refresh({ force: true })
    if (showRevisions.value) await loadRevisions()
    message.value = successText || data.message || data.receipt?.message || '已完成'
  } catch (e) {
    error.value = e?.response?.data?.message
      || e?.response?.data?.error?.message
      || e.message
      || String(e)
  } finally {
    busy.value = false
  }
}

function materialize() {
  return runCommand('chapter.workspace.create', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
  }, '章节 Workspace 已就绪')
}

function generateDraft() {
  return runCommand('chapter.generate_draft', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
    overwrite_locked: false,
  }, '草稿 revision 已生成（未自动当作正式版，除非关闭 H2 开关）')
}

function onSaveBlocks(operations) {
  return runCommand('chapter.content.apply', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
    operations,
  }, '正文已保存为新 head revision')
}

function onRestore(item) {
  return runCommand('chapter.revision.restore', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
    from_content_revision: Number(item.content_revision),
  }, `已从 r${item.content_revision} 恢复为新 head`)
}

function onApproveRevision(item) {
  return runCommand('chapter.approval.confirm', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
    content_revision: Number(item.content_revision),
    content_hash: item.content_hash,
  }, `H2 已确认 r${item.content_revision} 为正式版`)
}

function approveHead() {
  const content = chapter.value?.content
  if (!content) {
    error.value = '当前没有 head 正文可确认'
    return
  }
  return onApproveRevision(content)
}

onMounted(async () => {
  try {
    await refresh({ force: true })
  } catch (e) {
    error.value = e.message || String(e)
  }
  pollTimer = setInterval(async () => {
    try {
      await refresh({ force: false })
    } catch (_) {
      /* ignore poll errors */
    }
  }, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.chapter-workspace {
  position: relative;
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #f8fafc;
}
.top {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  border-bottom: 1px solid #e2e8f0;
  background: #fff;
}
.back {
  color: #2563eb;
  text-decoration: none;
  font-size: 13px;
}
h2 {
  margin: 4px 0;
  font-size: 18px;
  color: #0f172a;
}
.sub {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: #64748b;
  align-items: center;
}
.pill {
  display: inline-flex;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
}
.pill.ok { background: #dcfce7; color: #166534; }
.pill.draft { background: #ffedd5; color: #9a3412; }
.pill.ready { background: #dbeafe; color: #1d4ed8; }
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: flex-start;
}
.body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  min-height: 0;
  flex: 1;
}
.editor-pane {
  padding: 12px 16px;
  overflow: auto;
}
.placeholder {
  background: #fff;
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  padding: 28px;
  color: #64748b;
  text-align: center;
}
.error {
  color: #b91c1c;
  padding: 8px 16px;
  background: #fef2f2;
  border-bottom: 1px solid #fecaca;
}
.message {
  color: #166534;
  padding: 8px 16px;
  background: #ecfdf5;
  border-bottom: 1px solid #a7f3d0;
  font-size: 13px;
}
@media (max-width: 960px) {
  .body { grid-template-columns: 1fr; }
}
</style>
