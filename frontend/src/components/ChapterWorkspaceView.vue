<template>
  <div class="chapter-workspace">
    <header class="top">
      <div>
        <router-link :to="`/business/${workspaceId}`" class="back">← 项目</router-link>
        <h2>{{ chapter?.title || chapterId }}</h2>
        <div class="sub">
          status {{ chapter?.status || '-' }} ·
          chapter_rev {{ chapter?.chapter_revision || 0 }} ·
          head {{ chapter?.head_content_revision || 0 }} ·
          formal {{ chapter?.formal_content_revision || 0 }} ·
          approval {{ chapter?.approval_status || 'not_started' }}
        </div>
      </div>
      <div class="actions">
        <button type="button" class="btn" :disabled="busy" @click="materialize">打开/物化</button>
        <button type="button" class="btn" :disabled="busy || !chapter?.materialized" @click="generateDraft">生成草稿</button>
        <button type="button" class="btn" :disabled="busy" @click="showRevisions = true">版本</button>
        <button type="button" class="btn" :disabled="busy || !canApprove" @click="approveHead">H2 确认 head</button>
      </div>
    </header>
    <div v-if="error" class="error">{{ error }}</div>
    <div class="body">
      <div class="editor-pane">
        <ContentBlockEditor
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
const showRevisions = ref(false)
const remoteHint = ref('')
const editorRef = ref(null)
let pollTimer = null

const editorBlocks = computed(() => chapter.value?.content?.blocks || [])
const contextItems = computed(() => chapter.value?.context?.items || [])
const canApprove = computed(() => {
  const head = Number(chapter.value?.head_content_revision || 0)
  const formal = Number(chapter.value?.formal_content_revision || 0)
  return head > 0 && head !== formal
})

async function refresh(options = {}) {
  const { force = false } = options
  const dirty = editorRef.value?.dirty
  if (dirty && !force) {
    remoteHint.value = '远端章节已变化，本地有未保存编辑，未覆盖草稿'
  }
  const { data } = await fetchChapter(props.workspaceId, props.chapterId)
  if (!data.ok) throw new Error(data.message || '加载章节失败')
  if (!(dirty && !force)) {
    chapter.value = data.chapter
    remoteHint.value = ''
  } else {
    // keep local editor; still refresh metadata pointers carefully
    chapter.value = {
      ...data.chapter,
      content: chapter.value?.content,
    }
  }
  const rev = await fetchChapterRevisions(props.workspaceId, props.chapterId)
  if (rev.data?.ok) revisions.value = rev.data.revisions || []
  const snap = await fetchSnapshot(props.workspaceId)
  if (snap.data?.ok) workspaceRevision.value = Number(snap.data.snapshot?.workspace_revision || 0)
}

async function runCommand(kind, payload) {
  busy.value = true
  error.value = ''
  try {
    const { data } = await submitV3Command(props.workspaceId, {
      kind,
      payload,
      expected_revision: workspaceRevision.value,
      idempotency_key: `${kind}-${props.chapterId}-${Date.now()}`,
    })
    if (!data.ok) {
      throw new Error(data.message || data.receipt?.message || '命令失败')
    }
    await refresh({ force: true })
  } catch (e) {
    error.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

function materialize() {
  return runCommand('chapter.workspace.create', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
  })
}

function generateDraft() {
  return runCommand('chapter.generate_draft', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
    overwrite_locked: false,
  })
}

function onSaveBlocks(operations) {
  return runCommand('chapter.content.apply', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
    operations,
  })
}

function onRestore(item) {
  return runCommand('chapter.revision.restore', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
    from_content_revision: Number(item.content_revision),
  })
}

function onApproveRevision(item) {
  return runCommand('chapter.approval.confirm', {
    chapter_id: props.chapterId,
    expected_chapter_revision: Number(chapter.value?.chapter_revision || 0),
    content_revision: Number(item.content_revision),
    content_hash: item.content_hash,
  })
}

function approveHead() {
  const content = chapter.value?.content
  if (!content) return
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
.chapter-workspace { position: relative; height: 100%; display: flex; flex-direction: column; min-height: 0; }
.top { display: flex; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid #e2e8f0; }
.back { color: #2563eb; text-decoration: none; font-size: 13px; }
h2 { margin: 4px 0; font-size: 18px; }
.sub { font-size: 12px; color: #64748b; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: flex-start; }
.btn { border: 1px solid #cbd5e1; background: #fff; border-radius: 6px; padding: 6px 10px; cursor: pointer; }
.body { display: grid; grid-template-columns: 1fr 280px; min-height: 0; flex: 1; }
.editor-pane { padding: 12px 16px; overflow: auto; }
.error { color: #b91c1c; padding: 8px 16px; background: #fef2f2; }
</style>
