<template>
  <div class="workbench">
    <!-- 左：目录结构 -->
    <aside class="pane pane-tree">
      <header class="pane-header">
        <div>
          <p class="kicker">目录结构</p>
          <h3>测试 / 章节目录</h3>
        </div>
        <button type="button" class="btn btn-sm" :disabled="busy" @click="reloadAll">刷新</button>
      </header>

      <div class="tree-toolbar">
        <button type="button" class="btn btn-sm btn-primary" :disabled="busy || !selectedId" @click="materializeSelected">
          打开/物化
        </button>
        <button type="button" class="btn btn-sm" :disabled="busy" @click="composeCheck">检查组装</button>
      </div>

      <div v-if="listError" class="banner error">{{ listError }}</div>

      <div class="tree-stats">
        <span>{{ items.length }} 章</span>
        <span>{{ materializedCount }} 已物化</span>
        <span>{{ formalCount }} 正式</span>
      </div>

      <nav class="tree-list" aria-label="章节目录">
        <button
          v-for="item in treeItems"
          :key="item.chapter_id"
          type="button"
          class="tree-item"
          :class="{
            active: item.chapter_id === selectedId,
            archived: item.status === 'archived',
          }"
          :style="{ paddingLeft: `${12 + (item.depth || 0) * 14}px` }"
          @click="selectChapter(item.chapter_id)"
        >
          <span class="tree-dot" :class="statusClass(item)" />
          <span class="tree-title">{{ item.title || item.chapter_id }}</span>
          <span class="tree-meta">{{ shortStatus(item) }}</span>
        </button>
        <p v-if="!items.length && !busy" class="empty-hint">
          暂无目录。请先在「流水线」完成规划并晋级 Blueprint。
        </p>
      </nav>

      <div v-if="composeResult" class="compose-box" :class="{ ok: composeResult.export_allowed }">
        <strong>{{ composeResult.export_allowed ? '可导出正式稿' : '仅草稿预览' }}</strong>
        <small>{{ composeResult.document_hash?.slice(0, 16) }}…</small>
        <small v-if="composeResult.pending_chapters?.length">
          待确认 {{ composeResult.pending_chapters.length }} 章
        </small>
      </div>
    </aside>

    <!-- 中：文档生成 -->
    <main class="pane pane-doc">
      <header class="pane-header doc-header">
        <div>
          <p class="kicker">文档生成</p>
          <h3>{{ selectedChapter?.title || selectedId || '选择左侧章节' }}</h3>
          <div v-if="selectedChapter" class="doc-sub">
            <span class="pill" :class="statusClass(selectedChapter)">{{ shortStatus(selectedChapter) }}</span>
            <span>rev {{ chapterDetail?.chapter_revision || selectedChapter.chapter_revision || 0 }}</span>
            <span>head {{ chapterDetail?.head_content_revision || selectedChapter.head_content_revision || 0 }}</span>
            <span>formal {{ chapterDetail?.formal_content_revision || selectedChapter.formal_content_revision || 0 }}</span>
          </div>
        </div>
        <div class="doc-actions">
          <button
            type="button"
            class="btn btn-primary"
            :disabled="busy || !selectedId || !chapterDetail?.materialized"
            @click="generateDraft"
          >
            {{ busyAction === 'draft' ? '生成中…' : '生成草稿' }}
          </button>
          <button
            type="button"
            class="btn"
            :disabled="busy || !canApprove"
            @click="approveHead"
          >
            H2 确认
          </button>
          <button type="button" class="btn btn-sm" :disabled="busy || !selectedId" @click="showRevisions = true">
            版本
          </button>
        </div>
      </header>

      <div v-if="actionError" class="banner error">{{ actionError }}</div>
      <div v-if="actionMessage" class="banner ok">{{ actionMessage }}</div>

      <div class="doc-body">
        <div v-if="!selectedId" class="placeholder">
          <h4>从左侧选择章节</h4>
          <p>中间区域用于生成与编辑正文；右侧可查看上下文并与 Agent 对话。</p>
        </div>
        <div v-else-if="detailLoading" class="placeholder">加载章节…</div>
        <div v-else-if="!chapterDetail?.materialized" class="placeholder">
          <h4>章节尚未物化</h4>
          <p>点击左上角「打开/物化」，从 Blueprint 创建章节 Workspace。</p>
          <button type="button" class="btn btn-primary" :disabled="busy" @click="materializeSelected">打开/物化</button>
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
    </main>

    <!-- 右：聊天 + 上下文 -->
    <aside class="pane pane-chat">
      <header class="pane-header">
        <div>
          <p class="kicker">Agent</p>
          <h3>聊天与上下文</h3>
        </div>
      </header>

      <div class="chat-tabs">
        <button type="button" class="tab" :class="{ active: rightTab === 'chat' }" @click="rightTab = 'chat'">对话</button>
        <button type="button" class="tab" :class="{ active: rightTab === 'context' }" @click="rightTab = 'context'">上下文</button>
      </div>

      <div v-show="rightTab === 'context'" class="context-panel">
        <div v-if="!contextItems.length" class="empty-hint">当前章节暂无 Context。物化后会从 Blueprint 种子生成。</div>
        <article v-for="item in contextItems" :key="item.item_id" class="context-card">
          <div class="context-kind">{{ item.kind }}</div>
          <div class="context-title">{{ item.title }}</div>
          <div class="context-body">{{ item.body }}</div>
          <div class="context-src">{{ item.source }}</div>
        </article>
      </div>

      <div v-show="rightTab === 'chat'" class="chat-panel">
        <div class="chat-history" ref="chatHistoryEl">
          <div v-if="!chatTurns.length" class="empty-hint">
            可询问当前章节如何写、还缺什么材料。Agent 会结合右侧上下文与工作区状态回答。
          </div>
          <article
            v-for="turn in chatTurns"
            :key="turn.id"
            class="chat-bubble"
            :class="turn.role"
          >
            <strong>{{ turn.role === 'user' ? '你' : 'Agent' }}</strong>
            <p>{{ turn.content }}</p>
          </article>
        </div>
        <div class="chat-compose">
          <textarea
            v-model="chatInput"
            rows="3"
            placeholder="例如：结合评分要求，这一章应强调哪些交付物？"
            @keydown.ctrl.enter.prevent="sendChat"
          />
          <button
            type="button"
            class="btn btn-primary"
            :disabled="asking || !chatInput.trim()"
            @click="sendChat"
          >
            {{ asking ? '思考中…' : '发送' }}
          </button>
        </div>
      </div>
    </aside>

    <ChapterRevisionDrawer
      :open="showRevisions"
      :revisions="revisions"
      :head-revision="chapterDetail?.head_content_revision || 0"
      :formal-revision="chapterDetail?.formal_content_revision || 0"
      @close="showRevisions = false"
      @restore="onRestore"
      @approve="onApproveRevision"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  chatV3,
  fetchChapter,
  fetchChapterRevisions,
  fetchChapters,
  fetchDocumentCompose,
  fetchSnapshot,
  submitV3Command,
} from '../api'
import ContentBlockEditor from './ContentBlockEditor.vue'
import ChapterRevisionDrawer from './ChapterRevisionDrawer.vue'

const props = defineProps({
  workspaceId: { type: String, required: true },
  initialChapterId: { type: String, default: '' },
})

const router = useRouter()

const items = ref([])
const selectedId = ref('')
const chapterDetail = ref(null)
const revisions = ref([])
const composeResult = ref(null)
const workspaceRevision = ref(0)

const busy = ref(false)
const busyAction = ref('')
const detailLoading = ref(false)
const listError = ref('')
const actionError = ref('')
const actionMessage = ref('')
const remoteHint = ref('')
const showRevisions = ref(false)

const rightTab = ref('chat')
const chatInput = ref('')
const chatTurns = ref([])
const asking = ref(false)
const chatHistoryEl = ref(null)
const editorRef = ref(null)

let pollTimer = null

const selectedChapter = computed(() =>
  items.value.find(item => item.chapter_id === selectedId.value) || null,
)
const editorBlocks = computed(() => chapterDetail.value?.content?.blocks || [])
const contextItems = computed(() => chapterDetail.value?.context?.items || [])
const materializedCount = computed(() =>
  items.value.filter(item => item.materialized || item.status === 'active').length,
)
const formalCount = computed(() =>
  items.value.filter(item => Number(item.formal_content_revision || 0) > 0).length,
)
const canApprove = computed(() => {
  const head = Number(chapterDetail.value?.head_content_revision || 0)
  const formal = Number(chapterDetail.value?.formal_content_revision || 0)
  return Boolean(chapterDetail.value?.materialized) && head > 0 && head !== formal
})

const treeItems = computed(() => {
  const byId = new Map(items.value.map(item => [item.chapter_id, item]))
  return items.value.map(item => {
    let depth = 0
    let parent = item.parent_chapter_id
    const seen = new Set()
    while (parent && byId.has(parent) && !seen.has(parent)) {
      seen.add(parent)
      depth += 1
      parent = byId.get(parent)?.parent_chapter_id
    }
    return { ...item, depth: Math.min(depth, 6) }
  })
})

function shortStatus(item) {
  if (!item) return ''
  if (item.status === 'archived') return '归档'
  if (item.approval_status === 'approved' || Number(item.formal_content_revision || 0) > 0) return '正式'
  if (Number(item.head_content_revision || 0) > 0) return '草稿'
  if (item.materialized || item.status === 'active') return '已开'
  return '未开'
}

function statusClass(item) {
  if (!item) return ''
  if (item.status === 'archived') return 'archived'
  if (item.approval_status === 'approved' || Number(item.formal_content_revision || 0) > 0) return 'ok'
  if (Number(item.head_content_revision || 0) > 0) return 'draft'
  if (item.materialized || item.status === 'active') return 'ready'
  return 'projected'
}

async function refreshSnapshotRevision() {
  const snap = await fetchSnapshot(props.workspaceId)
  if (snap.data?.ok) {
    workspaceRevision.value = Number(snap.data.snapshot?.workspace_revision || 0)
  }
}

async function loadChapterList() {
  listError.value = ''
  try {
    const { data } = await fetchChapters(props.workspaceId)
    if (!data.ok) throw new Error(data.message || '加载目录失败')
    items.value = data.chapters?.items || []
    if (!selectedId.value) {
      const prefer = props.initialChapterId
        || items.value.find(item => item.materialized)?.chapter_id
        || items.value[0]?.chapter_id
        || ''
      if (prefer) selectedId.value = prefer
    }
  } catch (e) {
    listError.value = e?.response?.data?.message || e.message || String(e)
  }
}

async function loadChapterDetail(options = {}) {
  const { force = true } = options
  if (!selectedId.value) {
    chapterDetail.value = null
    return
  }
  const dirty = editorRef.value?.dirty
  if (dirty && !force) {
    remoteHint.value = '远端已更新；本地有未保存编辑，未覆盖草稿'
    return
  }
  detailLoading.value = true
  actionError.value = ''
  try {
    const { data } = await fetchChapter(props.workspaceId, selectedId.value)
    if (!data.ok) throw new Error(data.message || '加载章节失败')
    chapterDetail.value = data.chapter
    remoteHint.value = ''
    await refreshSnapshotRevision()
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    detailLoading.value = false
  }
}

async function reloadAll() {
  busy.value = true
  try {
    await loadChapterList()
    await loadChapterDetail({ force: true })
  } finally {
    busy.value = false
  }
}

function selectChapter(chapterId) {
  if (selectedId.value === chapterId) return
  selectedId.value = chapterId
  router.replace(`/business/${props.workspaceId}/chapters/${encodeURIComponent(chapterId)}`).catch(() => {})
}

async function runCommand(kind, payload, successText = '', action = '') {
  busy.value = true
  busyAction.value = action
  actionError.value = ''
  actionMessage.value = ''
  try {
    await refreshSnapshotRevision()
    if (selectedId.value) {
      const latest = await fetchChapter(props.workspaceId, selectedId.value)
      if (latest.data?.ok) {
        const rev = Number(latest.data.chapter?.chapter_revision || 0)
        payload = { ...payload, expected_chapter_revision: rev }
        if (!editorRef.value?.dirty) chapterDetail.value = latest.data.chapter
      }
    }
    const { data } = await submitV3Command(props.workspaceId, {
      kind,
      payload,
      expected_revision: workspaceRevision.value,
      idempotency_key: `${kind}-${selectedId.value || 'ws'}-${Date.now()}`,
    })
    if (!data.ok) {
      throw new Error(
        data.receipt?.error?.message || data.message || data.receipt?.message || '命令失败',
      )
    }
    await loadChapterList()
    await loadChapterDetail({ force: true })
    if (showRevisions.value) {
      const rev = await fetchChapterRevisions(props.workspaceId, selectedId.value)
      if (rev.data?.ok) revisions.value = rev.data.revisions || []
    }
    actionMessage.value = successText || data.message || data.receipt?.message || '已完成'
  } catch (e) {
    actionError.value = e?.response?.data?.message
      || e?.response?.data?.error?.message
      || e.message
      || String(e)
  } finally {
    busy.value = false
    busyAction.value = ''
  }
}

function materializeSelected() {
  if (!selectedId.value) return
  return runCommand('chapter.workspace.create', {
    chapter_id: selectedId.value,
    expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
  }, '章节已物化')
}

function generateDraft() {
  if (!selectedId.value) return
  return runCommand('chapter.generate_draft', {
    chapter_id: selectedId.value,
    expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
    overwrite_locked: false,
  }, '草稿已生成', 'draft')
}

function onSaveBlocks(operations) {
  return runCommand('chapter.content.apply', {
    chapter_id: selectedId.value,
    expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
    operations,
  }, '正文已保存')
}

function onRestore(item) {
  return runCommand('chapter.revision.restore', {
    chapter_id: selectedId.value,
    expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
    from_content_revision: Number(item.content_revision),
  }, `已恢复 r${item.content_revision}`)
}

function onApproveRevision(item) {
  return runCommand('chapter.approval.confirm', {
    chapter_id: selectedId.value,
    expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
    content_revision: Number(item.content_revision),
    content_hash: item.content_hash,
  }, `H2 已确认 r${item.content_revision}`)
}

function approveHead() {
  const content = chapterDetail.value?.content
  if (!content) {
    actionError.value = '没有 head 正文可确认'
    return
  }
  return onApproveRevision(content)
}

async function composeCheck() {
  busy.value = true
  actionError.value = ''
  try {
    const { data } = await fetchDocumentCompose(props.workspaceId)
    if (!data.ok) throw new Error(data.message || '组装失败')
    composeResult.value = data.document
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

async function openRevisions() {
  showRevisions.value = true
  if (!selectedId.value) return
  const rev = await fetchChapterRevisions(props.workspaceId, selectedId.value)
  if (rev.data?.ok) revisions.value = rev.data.revisions || []
}

watch(showRevisions, (open) => {
  if (open) openRevisions()
})

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text || asking.value) return
  asking.value = true
  actionError.value = ''
  const userTurn = { id: `u-${Date.now()}`, role: 'user', content: text }
  chatTurns.value = [...chatTurns.value, userTurn]
  chatInput.value = ''
  await nextTick()
  if (chatHistoryEl.value) chatHistoryEl.value.scrollTop = chatHistoryEl.value.scrollHeight
  try {
    // Prefixed chapter context so the agent sees the active chapter overlay.
    const contextHint = selectedId.value
      ? `[当前章节 ${selectedId.value} / ${selectedChapter.value?.title || ''}]\n上下文条目 ${contextItems.value.length} 条。\n\n${text}`
      : text
    const { data } = await chatV3(props.workspaceId, contextHint)
    if (!data.ok) throw new Error(data.message || '对话失败')
    chatTurns.value = [
      ...chatTurns.value,
      { id: `a-${Date.now()}`, role: 'assistant', content: data.reply || '（无回复）' },
    ]
    if (data.workspace_revision != null) {
      workspaceRevision.value = Number(data.workspace_revision)
    }
    await nextTick()
    if (chatHistoryEl.value) chatHistoryEl.value.scrollTop = chatHistoryEl.value.scrollHeight
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
    chatTurns.value = [
      ...chatTurns.value,
      { id: `e-${Date.now()}`, role: 'assistant', content: `请求失败：${actionError.value}` },
    ]
  } finally {
    asking.value = false
  }
}

watch(
  () => selectedId.value,
  async (id, prev) => {
    if (!id || id === prev) return
    await loadChapterDetail({ force: true })
    rightTab.value = contextItems.value.length ? 'context' : 'chat'
  },
)

watch(
  () => props.initialChapterId,
  (id) => {
    if (id && id !== selectedId.value) selectedId.value = id
  },
)

onMounted(async () => {
  if (props.initialChapterId) selectedId.value = props.initialChapterId
  await reloadAll()
  pollTimer = setInterval(async () => {
    try {
      await loadChapterList()
      await loadChapterDetail({ force: false })
    } catch (_) {
      /* ignore */
    }
  }, 6000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.workbench {
  display: grid;
  grid-template-columns: minmax(220px, 280px) minmax(0, 1fr) minmax(280px, 340px);
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #f1f5f9;
}
.pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  background: #fff;
  border-right: 1px solid #e2e8f0;
}
.pane-chat {
  border-right: none;
  border-left: 1px solid #e2e8f0;
}
.pane-header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: flex-start;
  padding: 12px 14px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}
.kicker {
  margin: 0;
  font-size: 11px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #64748b;
}
.pane-header h3 {
  margin: 2px 0 0;
  font-size: 15px;
  color: #0f172a;
}
.tree-toolbar {
  display: flex;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}
.tree-stats {
  display: flex;
  gap: 10px;
  padding: 6px 12px;
  font-size: 11px;
  color: #64748b;
  border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}
.tree-list {
  flex: 1;
  overflow: auto;
  padding: 8px;
}
.tree-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 8px;
  padding: 8px 10px;
  text-align: left;
  cursor: pointer;
  margin-bottom: 2px;
}
.tree-item:hover { background: #f8fafc; }
.tree-item.active {
  background: rgba(37, 99, 235, 0.08);
  border-color: rgba(37, 99, 235, 0.2);
}
.tree-item.archived { opacity: 0.55; }
.tree-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #cbd5e1;
  flex-shrink: 0;
}
.tree-dot.ok { background: #22c55e; }
.tree-dot.draft { background: #f59e0b; }
.tree-dot.ready { background: #3b82f6; }
.tree-dot.projected { background: #cbd5e1; }
.tree-dot.archived { background: #94a3b8; }
.tree-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-meta {
  font-size: 11px;
  color: #94a3b8;
  flex-shrink: 0;
}
.compose-box {
  margin: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex-shrink: 0;
}
.compose-box.ok {
  background: #ecfdf5;
  border-color: #a7f3d0;
}
.doc-header { align-items: center; }
.doc-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
  align-items: center;
}
.doc-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.doc-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 12px 14px;
  background: #f8fafc;
}
.placeholder {
  height: 100%;
  min-height: 240px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  text-align: center;
  color: #64748b;
  background: #fff;
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  padding: 24px;
}
.placeholder h4 { margin: 0; color: #0f172a; }
.placeholder p { margin: 0; max-width: 360px; font-size: 13px; }
.pill {
  display: inline-flex;
  padding: 1px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-size: 11px;
}
.pill.ok { background: #dcfce7; color: #166534; }
.pill.draft { background: #ffedd5; color: #9a3412; }
.pill.ready { background: #dbeafe; color: #1d4ed8; }
.banner {
  padding: 8px 14px;
  font-size: 13px;
  flex-shrink: 0;
}
.banner.error { background: #fef2f2; color: #b91c1c; border-bottom: 1px solid #fecaca; }
.banner.ok { background: #ecfdf5; color: #166534; border-bottom: 1px solid #a7f3d0; }
.chat-tabs {
  display: flex;
  gap: 4px;
  padding: 8px 10px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}
.tab {
  flex: 1;
  border: 1px solid transparent;
  background: #f8fafc;
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 13px;
  cursor: pointer;
  color: #475569;
}
.tab.active {
  background: rgba(37, 99, 235, 0.1);
  color: #1d4ed8;
  border-color: rgba(37, 99, 235, 0.2);
  font-weight: 600;
}
.context-panel,
.chat-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.context-panel {
  overflow: auto;
  padding: 10px;
  gap: 8px;
}
.context-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  background: #f8fafc;
}
.context-kind {
  font-size: 11px;
  color: #2563eb;
  font-weight: 700;
}
.context-title {
  font-size: 13px;
  font-weight: 600;
  margin: 2px 0;
  color: #0f172a;
}
.context-body {
  font-size: 12px;
  color: #334155;
  white-space: pre-wrap;
}
.context-src {
  margin-top: 4px;
  font-size: 11px;
  color: #94a3b8;
}
.chat-history {
  flex: 1;
  overflow: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chat-bubble {
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.45;
}
.chat-bubble.user {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  align-self: flex-end;
  max-width: 92%;
}
.chat-bubble.assistant {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  align-self: flex-start;
  max-width: 96%;
}
.chat-bubble strong {
  display: block;
  font-size: 11px;
  color: #64748b;
  margin-bottom: 2px;
}
.chat-bubble p {
  margin: 0;
  white-space: pre-wrap;
  color: #0f172a;
}
.chat-compose {
  border-top: 1px solid #e2e8f0;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
  background: #fff;
}
.chat-compose textarea {
  width: 100%;
  resize: vertical;
  min-height: 72px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 8px;
  font: inherit;
}
.empty-hint {
  color: #94a3b8;
  font-size: 12px;
  padding: 12px 4px;
  line-height: 1.5;
}
@media (max-width: 1100px) {
  .workbench {
    grid-template-columns: 220px minmax(0, 1fr) 260px;
  }
}
</style>
