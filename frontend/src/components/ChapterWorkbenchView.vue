<template>
  <div class="workbench" :style="workbenchStyle" :class="{ 'is-dragging': isDragging }">
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
        <button type="button" class="btn btn-sm btn-primary" :disabled="busy" @click="openCreateModal">
          + 新建章节
        </button>
        <button type="button" class="btn btn-sm" :disabled="busy || !selectedId" @click="materializeSelected">
          打开/物化
        </button>
        <button type="button" class="btn btn-sm" :disabled="busy" @click="composeCheck">检查组装</button>
        <button type="button" class="btn btn-sm" :disabled="busy || !treeItems.length" @click="exportMarkdownOutline">
          导出 MD
        </button>
      </div>

      <div v-if="listError" class="banner error">{{ listError }}</div>

      <div class="tree-stats">
        <span>{{ items.length }} 章</span>
        <span>{{ materializedCount }} 已物化</span>
        <span>{{ formalCount }} 正式</span>
      </div>

      <nav class="tree-list" aria-label="章节目录">
        <div
          v-for="(item, idx) in treeItems"
          :key="item.chapter_id"
          class="tree-item"
          :class="{
            active: item.chapter_id === selectedId,
            archived: item.status === 'archived',
            editing: editingChapterId === item.chapter_id
          }"
          :style="{ paddingLeft: `${12 + (item.depth || 0) * 14}px` }"
          @click="selectChapter(item.chapter_id)"
        >
          <span class="tree-dot" :class="statusClass(item)" />

          <template v-if="editingChapterId === item.chapter_id">
            <input
              ref="editInputRef"
              v-model="editingTitle"
              class="tree-item-input"
              @keydown.enter.prevent="saveRenameChapter(item)"
              @keydown.esc.prevent="cancelRename"
              @click.stop
              @blur="saveRenameChapter(item)"
            />
          </template>
          <template v-else>
            <span class="tree-title" :title="item.title || item.chapter_id" @dblclick="startRenameChapter(item, $event)">
              {{ item.title || item.chapter_id }}
            </span>
            <span class="tree-meta">{{ shortStatus(item) }}</span>
            <div class="tree-item-actions" @click.stop>
              <button
                type="button"
                class="icon-action-btn"
                title="修改标题"
                @click="startRenameChapter(item, $event)"
              >
                ✏️
              </button>
              <button
                type="button"
                class="icon-action-btn"
                title="向上移动"
                :disabled="idx === 0"
                @click="handleMoveChapter(item, 'up', $event)"
              >
                ⬆️
              </button>
              <button
                type="button"
                class="icon-action-btn"
                title="向下移动"
                :disabled="idx === treeItems.length - 1"
                @click="handleMoveChapter(item, 'down', $event)"
              >
                ⬇️
              </button>
              <button
                type="button"
                class="icon-action-btn danger"
                title="归档/删除章节"
                @click="handleArchiveChapter(item, $event)"
              >
                🗑️
              </button>
            </div>
          </template>
        </div>
        <p v-if="!items.length && !busy" class="empty-hint">
          暂无目录。请先在「流水线」完成规划并晋级 Blueprint 或使用上方「+ 新建章节」。
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

    <!-- 左拖拽分割线 -->
    <div class="resizer resizer-left" title="拖动调整目录栏宽度" @mousedown="startDragLeft">
      <div class="resizer-handle"></div>
    </div>

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
            :disabled="busy || !selectedId || !chapterDetail?.materialized || !selectedIsLeaf"
            :title="selectedIsLeaf ? '生成当前叶子章节正文' : '目录父节点只保留标题，不生成正文'"
            @click="generateDraft"
          >
            {{ busyAction === 'draft' ? '生成中…' : (selectedIsLeaf ? '生成草稿' : '目录节点') }}
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

      <div ref="docBodyEl" class="chapter-doc-body">
        <div class="document-stage">
          <article
            class="document-paper"
            :aria-busy="detailLoading"
            aria-label="A4 正文编辑页"
          >
            <header v-if="selectedId" class="paper-heading">
              <h1>{{ selectedChapter?.title || selectedId || '未命名章节' }}</h1>
            </header>

            <div v-if="!selectedId" class="document-state">
              <h4>从左侧选择章节</h4>
              <p>中间区域用于生成与编辑正文；右侧是本章专属对话与上下文（各章历史互不混用）。</p>
            </div>
            <div
              v-else-if="detailLoading"
              class="document-state document-loading"
              role="status"
              aria-live="polite"
            >
              <span class="document-loading-mark" aria-hidden="true" />
              <h4>正在加载章节</h4>
              <p>正在把本章内容放入当前 Word 页面…</p>
            </div>
            <div v-else-if="detailError" class="document-state document-error" role="alert">
              <h4>章节加载失败</h4>
              <p>{{ detailError }}</p>
              <button type="button" class="btn" :disabled="busy" @click="loadChapterDetail({ force: true })">重新加载</button>
            </div>
            <div v-else-if="!selectedIsLeaf" class="document-state">
              <h4>该章节是目录父节点</h4>
              <p>父节点只保留标题和层级，不写正文。请从左侧选择其下级叶子章节生成内容。</p>
            </div>
            <div v-else-if="!chapterDetail?.materialized" class="document-state">
              <h4>章节尚未物化</h4>
              <p>点击左上角「打开/物化」，从 Blueprint 创建章节 Workspace。</p>
              <button type="button" class="btn btn-primary" :disabled="busy" @click="materializeSelected">打开/物化</button>
            </div>
            <template v-else>
              <ContentBlockEditor
                ref="editorRef"
                :blocks="editorBlocks"
                :busy="busy"
                :streaming="streamingDraft"
                :stream-text="streamText"
                :remote-hint="remoteHint"
                @save="onSaveBlocks"
              />
            </template>
          </article>
        </div>
      </div>
    </main>

    <!-- 右拖拽分割线 -->
    <div class="resizer resizer-right" title="拖动调整对话栏宽度" @mousedown="startDragRight">
      <div class="resizer-handle"></div>
    </div>

    <!-- 右：聊天 + 上下文 -->
    <aside class="pane pane-chat">
      <header class="pane-header">
        <div>
          <p class="kicker">Agent · 本章专属</p>
          <h3>聊天与上下文</h3>
          <p v-if="selectedChapter" class="chat-chapter-label">
            {{ selectedChapter.title || selectedId }}
          </p>
          <p v-else class="chat-chapter-label muted">请先选择左侧章节</p>
        </div>
      </header>

      <div class="chat-tabs">
        <button type="button" class="tab" :class="{ active: rightTab === 'chat' }" @click="rightTab = 'chat'">本章对话</button>
        <button type="button" class="tab" :class="{ active: rightTab === 'context' }" @click="rightTab = 'context'">上下文</button>
      </div>

      <div v-show="rightTab === 'context'" class="context-panel">
        <section class="context-section shared-context">
          <header class="context-section-header">
            <div>
              <strong>公共项目事实</strong>
              <small>所有章节继承 · 只读</small>
            </div>
            <span v-if="globalProjectContext.global_context_revision" class="context-version">
              r{{ globalProjectContext.global_context_revision }}
            </span>
          </header>
          <div v-if="!globalContextReady" class="context-warning">
            公共项目事实尚未就绪，系统会阻止生成，不能以空上下文继续写作。
          </div>
          <template v-else>
            <dl class="identity-grid">
              <template v-for="item in globalIdentityRows" :key="item.label">
                <dt>{{ item.label }}</dt>
                <dd>{{ item.value }}</dd>
              </template>
            </dl>
            <details v-for="group in globalFactGroups" :key="group.key" class="fact-group" :open="group.key === 'scope' || group.key === 'work_packages'">
              <summary>{{ group.label }} <span>{{ group.items.length }}</span></summary>
              <ul>
                <li v-for="(fact, index) in group.items" :key="`${group.key}-${index}`">{{ fact }}</li>
              </ul>
            </details>
            <p class="context-note">
              已确认详细事实 {{ globalProjectContext.confirmed_facts?.length || 0 }} 条。共同事实只能在项目级统一更新，本章不能覆盖。
            </p>
            <button type="button" class="context-project-link" @click="openProjectContextSource">
              修改公共事实：返回项目流水线
            </button>
          </template>
        </section>

        <section v-if="writingOrientation" class="context-section writing-orientation">
          <header class="context-section-header">
            <div>
              <strong>本章写作处境</strong>
              <small>目的 · 全书位置 · 章节关系</small>
            </div>
            <span class="context-version">{{ writingOrientation.writing_purpose?.role_label || outlineRoleLabel }}</span>
          </header>
          <p v-if="writingOrientation.writing_purpose?.purpose" class="context-note">
            写作目的：{{ writingOrientation.writing_purpose.purpose }}
          </p>
          <p
            v-if="writingOrientation.writing_purpose?.writing_objectives?.length"
            class="context-note"
          >
            写作目标：{{ writingOrientation.writing_purpose.writing_objectives.join('；') }}
          </p>
          <p v-if="orientationPathLabel" class="context-note outline-path">
            全书位置：{{ orientationPathLabel }}
          </p>
          <ul v-if="orientationRelations.length" class="orientation-relations">
            <li v-for="item in orientationRelations" :key="item.chapter_id || item.title">
              <em>{{ item.relation_label || item.relation }}</em>
              {{ item.title }}
              <span v-if="item.content_status && item.content_status !== 'unknown'">
                · {{ outlineContentLabel(item) }}
              </span>
            </li>
          </ul>
          <p v-if="orientationMaterials" class="context-note">
            已有资料：{{ orientationMaterials }}
          </p>
        </section>

        <section class="context-section outline-context">
          <header class="context-section-header">
            <div>
              <strong>整份目录</strong>
              <small>默认标题 · 点开才看详情</small>
            </div>
            <span class="context-version">{{ outlineRoleLabel }}</span>
          </header>
          <p v-if="outlinePathLabel" class="context-note outline-path">
            当前位置：{{ outlinePathLabel }}
          </p>
          <p v-if="outlineGuidance" class="context-note">{{ outlineGuidance }}</p>
          <div v-if="!outlineFlat.length" class="empty-hint">
            目录尚未就绪。
          </div>
          <div v-else class="outline-readonly-list" role="tree">
            <button
              v-for="item in outlineFlat"
              :key="item.chapter_id"
              type="button"
              class="outline-readonly-item"
              :class="{
                current: item.is_current,
                empty: !item.has_content,
                leaf: item.is_leaf,
              }"
              :style="{ paddingLeft: `${10 + (item.depth || 0) * 14}px` }"
              :title="item.is_current ? '当前正在编辑的章节' : '点击只读查看（不可修改）'"
              @click="inspectOutlineChapter(item)"
            >
              <span class="outline-readonly-title">{{ item.title || item.chapter_id }}</span>
              <span class="outline-readonly-meta">
                <em v-if="item.is_current">本章</em>
                <em v-else>{{ outlineContentLabel(item) }}</em>
              </span>
            </button>
          </div>

          <div v-if="readonlyView" class="readonly-chapter-panel">
            <header class="context-section-header">
              <div>
                <strong>他章只读</strong>
                <small>不可修改</small>
              </div>
              <button type="button" class="btn btn-ghost btn-tiny" @click="closeReadonlyView">关闭</button>
            </header>
            <div v-if="readonlyLoading" class="empty-hint">加载只读信息…</div>
            <template v-else>
              <div class="context-kind">
                {{ readonlyView.title || readonlyView.chapter_id }}
                · {{ outlineContentLabel(readonlyView) }}
                · 只读
              </div>
              <div v-if="readonlyView.purpose" class="context-body">目的：{{ readonlyView.purpose }}</div>
              <div
                v-if="readonlyView.writing_objectives?.length"
                class="context-body"
              >
                写作目标：{{ readonlyView.writing_objectives.join('；') }}
              </div>
              <div v-if="readonlyView.summary" class="context-body sibling-summary">
                {{ readonlyView.summary }}
              </div>
              <div v-else class="context-src">该章尚无正文摘要</div>
              <article
                v-for="(item, index) in (readonlyView.context_items || [])"
                :key="`${item.kind}-${index}`"
                class="context-card"
              >
                <div class="context-kind">{{ item.kind || '上下文' }}</div>
                <div class="context-title">{{ item.title }}</div>
                <div class="context-body">{{ item.body }}</div>
              </article>
              <p class="context-note">查看不会切换当前编辑章节，也不能在这里修改他章内容。</p>
            </template>
          </div>
        </section>

        <section class="context-section chapter-only-context">
          <header class="context-section-header">
            <div>
              <strong>本章专属要求</strong>
              <small>追加到公共事实之上</small>
            </div>
            <span class="context-version">r{{ chapterContextRef.chapter_context_revision || 0 }}</span>
          </header>
          <div v-if="!chapterRequirements.length && !chapterScoringRequirements.length && !contextItems.length" class="empty-hint">
            当前章节暂无专属要求。物化后会从 Blueprint 生成。
          </div>
          <article v-for="item in chapterRequirements" :key="item.requirement_id" class="context-card requirement-card">
            <div class="context-kind">招标要求 · {{ item.requirement_id }}</div>
            <div class="context-body">{{ item.text }}</div>
          </article>
          <article v-for="item in chapterScoringRequirements" :key="item.score_point_id" class="context-card scoring-card">
            <div class="context-kind">评分要求 · {{ item.score_point_id }}</div>
            <div class="context-title">{{ item.title }}</div>
            <div class="context-body">{{ item.response_expectation }}</div>
          </article>
          <article v-for="item in contextItems" :key="item.item_id" class="context-card">
            <div class="context-kind">{{ item.kind }}</div>
            <div class="context-title">{{ item.title }}</div>
            <div class="context-body">{{ item.body }}</div>
            <div class="context-src">{{ item.source }}</div>
          </article>
        </section>
      </div>

      <div v-show="rightTab === 'chat'" class="chat-panel">
        <div class="chat-history" ref="chatHistoryEl">
          <div v-if="!selectedId" class="empty-hint">
            选择左侧章节后，将打开该章独立对话；历史不会与其他章节混用。
          </div>
          <div v-else-if="chatLoading" class="empty-hint">正在加载本章对话…</div>
          <div v-else-if="!chatTurns.length" class="empty-hint">
            这是「{{ selectedChapter?.title || selectedId }}」的专属对话。
            Agent 默认只看目录标题；需要时再按需打开他章只读详情。
          </div>
          <article
            v-for="turn in chatTurns"
            :key="turn.id"
            class="chat-bubble"
            :class="[turn.role, { streaming: turn.streaming, editing: turn.editing }]"
          >
            <strong>{{ turn.role === 'user' ? '你' : 'Agent' }}</strong>
            <div
              v-if="turn.role === 'assistant' || turn.thinking"
              class="chat-thinking"
            >
              <div class="thinking-label">
                {{ turn.streaming && !turn.content ? '正在思考…' : '思考过程' }}
                <span v-if="turn.streaming && turn.thinking" class="thinking-live">实时</span>
              </div>
              <div
                class="thinking-body"
                :contenteditable="canEditChatTurn(turn)"
                :data-field="`thinking:${turn.id}`"
                spellcheck="false"
                @focus="onChatTurnFocus(turn)"
                @blur="onChatTurnBlur(turn, 'thinking', $event)"
              >{{ turn.thinking || (turn.streaming ? '（等待模型思考输出…）' : '') }}</div>
            </div>
            <div
              v-if="turn.content || !turn.streaming"
              class="chat-content"
              :contenteditable="canEditChatTurn(turn)"
              :data-field="`content:${turn.id}`"
              spellcheck="false"
              @focus="onChatTurnFocus(turn)"
              @blur="onChatTurnBlur(turn, 'content', $event)"
            >{{ turn.content }}</div>
            <p v-else class="chat-streaming-hint">正在生成回复…</p>
            <small v-if="canEditChatTurn(turn)" class="chat-edit-hint">点击可编辑，失焦后保存</small>
          </article>
        </div>
        <div class="chat-compose">
          <textarea
            v-model="chatInput"
            rows="3"
            :disabled="!selectedId || asking"
            :placeholder="selectedId
              ? '回车发送，Shift+回车换行。例如：结合评分要求，这一章应强调哪些交付物？'
              : '请先选择章节'"
            @keydown="onChatComposeKeydown"
          />
          <button
            type="button"
            class="btn btn-primary"
            :disabled="!selectedId || asking || !chatInput.trim()"
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

    <!-- 新建章节 Modal -->
    <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
      <div class="modal-card">
        <header class="modal-header">
          <h3>新建章节</h3>
          <button type="button" class="close-btn" @click="showCreateModal = false">&times;</button>
        </header>
        <div class="modal-body">
          <div class="form-group">
            <label>章节 ID</label>
            <input v-model="newChapterId" type="text" class="form-control" placeholder="如 chapter_03" />
            <small class="form-hint">英文字母、数字或下划线</small>
          </div>
          <div class="form-group">
            <label>章节标题</label>
            <input v-model="newChapterTitle" type="text" class="form-control" placeholder="如 3.1 项目管理方案" />
          </div>
        </div>
        <footer class="modal-footer">
          <button type="button" class="btn" @click="showCreateModal = false">取消</button>
          <button type="button" class="btn btn-primary" :disabled="busy || !newChapterId.trim()" @click="handleCreateChapter">
            确认新建
          </button>
        </footer>
      </div>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'ChapterWorkbenchView' })
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchChapterChatHistory,
  fetchChapterReadonlyView,
  fetchChapter,
  fetchChapterRevisions,
  fetchChapters,
  fetchDocumentCompose,
  fetchSnapshot,
  submitV3Command,
  createChapter,
  saveChapterMetadata,
  archiveChapter,
  streamChapterDraft,
  streamChapterChat,
  saveChapterChatTurn,
} from '../api'
import ContentBlockEditor from './ContentBlockEditor.vue'
import ChapterRevisionDrawer from './ChapterRevisionDrawer.vue'

const props = defineProps({
  workspaceId: { type: String, required: true },
  initialChapterId: { type: String, default: '' },
})

const router = useRouter()

const leftWidth = ref(480)
const rightWidth = ref(520)
const isDragging = ref(false)

const workbenchStyle = computed(() => ({
  gridTemplateColumns: `${leftWidth.value}px 6px minmax(0, 1fr) 6px ${rightWidth.value}px`,
}))

function startDragLeft(e) {
  e.preventDefault()
  isDragging.value = true
  const startX = e.clientX
  const startWidth = leftWidth.value

  const onMouseMove = (moveEvent) => {
    const deltaX = moveEvent.clientX - startX
    const newWidth = Math.min(Math.max(startWidth + deltaX, 160), 480)
    leftWidth.value = newWidth
  }

  const onMouseUp = () => {
    isDragging.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

function startDragRight(e) {
  e.preventDefault()
  isDragging.value = true
  const startX = e.clientX
  const startWidth = rightWidth.value

  const onMouseMove = (moveEvent) => {
    const deltaX = startX - moveEvent.clientX
    const newWidth = Math.min(Math.max(startWidth + deltaX, 220), 520)
    rightWidth.value = newWidth
  }

  const onMouseUp = () => {
    isDragging.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

const items = ref([])
const selectedId = ref('')
const chapterDetail = ref(null)
const revisions = ref([])
const composeResult = ref(null)
const workspaceRevision = ref(0)
const globalProjectContext = ref({})

const busy = ref(false)
const busyAction = ref('')
const detailLoading = ref(false)
const detailError = ref('')
const listError = ref('')
const actionError = ref('')
const actionMessage = ref('')
const remoteHint = ref('')
const showRevisions = ref(false)
const streamingDraft = ref(false)
const streamText = ref('')
const streamOperationId = ref('')
const researchStatus = ref('')
const researchSources = ref([])
const docBodyEl = ref(null)
let draftAbortController = null

const showCreateModal = ref(false)
const newChapterId = ref('')
const newChapterTitle = ref('')

const editingChapterId = ref('')
const editingTitle = ref('')
const editInputRef = ref(null)

function openCreateModal() {
  const nextNum = (items.value.length + 1).toString().padStart(2, '0')
  newChapterId.value = `chapter_${nextNum}`
  newChapterTitle.value = `${items.value.length + 1}.1 新增章节`
  showCreateModal.value = true
}

async function handleCreateChapter() {
  const cid = newChapterId.value.trim()
  const title = newChapterTitle.value.trim()
  if (!cid) {
    actionError.value = '章节ID不能为空'
    return
  }
  busy.value = true
  actionError.value = ''
  try {
    const { data } = await createChapter(props.workspaceId, cid, title)
    if (!data.ok) throw new Error(data.message || '创建章节失败')
    showCreateModal.value = false
    await loadChapterList()
    selectChapter(cid)
    actionMessage.value = `章节 ${cid} 创建成功`
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

function startRenameChapter(item, e) {
  if (e) e.stopPropagation()
  editingChapterId.value = item.chapter_id
  editingTitle.value = item.title || item.chapter_id
  nextTick(() => {
    if (editInputRef.value) {
      if (Array.isArray(editInputRef.value)) {
        editInputRef.value[0]?.focus()
      } else {
        editInputRef.value?.focus()
      }
    }
  })
}

async function saveRenameChapter(item) {
  if (!editingChapterId.value) return
  const title = editingTitle.value.trim()
  if (!title) {
    cancelRename()
    return
  }
  const cid = item.chapter_id
  editingChapterId.value = ''
  if (title === (item.title || item.chapter_id)) return

  busy.value = true
  try {
    const { data } = await saveChapterMetadata(props.workspaceId, cid, { title })
    if (!data.ok) throw new Error(data.message || '修改章节标题失败')
    await loadChapterList()
    if (selectedId.value === cid) {
      await loadChapterDetail({ force: true })
    }
    actionMessage.value = `章节标题已更新`
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

function cancelRename() {
  editingChapterId.value = ''
  editingTitle.value = ''
}

async function handleArchiveChapter(item, e) {
  if (e) e.stopPropagation()
  const name = item.title || item.chapter_id
  if (!confirm(`确定要归档/删除章节 "${name}" 吗？`)) return
  busy.value = true
  try {
    const { data } = await archiveChapter(props.workspaceId, item.chapter_id)
    if (!data.ok) throw new Error(data.message || '归档章节失败')
    await loadChapterList()
    if (selectedId.value === item.chapter_id) {
      const remaining = items.value.find(i => i.status !== 'archived')
      if (remaining) selectChapter(remaining.chapter_id)
    }
    actionMessage.value = `章节 ${name} 已归档`
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    busy.value = false
  }
}

async function handleMoveChapter(item, direction, e) {
  if (e) e.stopPropagation()
  const currentIndex = items.value.findIndex(i => i.chapter_id === item.chapter_id)
  if (currentIndex === -1) return
  const targetIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
  if (targetIndex < 0 || targetIndex >= items.value.length) return

  const currentOrder = Number(item.order || currentIndex + 1)
  const targetItem = items.value[targetIndex]
  const targetOrder = Number(targetItem.order || targetIndex + 1)

  busy.value = true
  try {
    await saveChapterMetadata(props.workspaceId, item.chapter_id, { order: targetOrder })
    await saveChapterMetadata(props.workspaceId, targetItem.chapter_id, { order: currentOrder })
    await loadChapterList()
  } catch (e) {
    actionError.value = '调整排序失败: ' + (e.message || String(e))
  } finally {
    busy.value = false
  }
}

function exportMarkdownOutline() {
  if (!treeItems.value || !treeItems.value.length) return
  let mdText = '# 章节目录\n\n'
  for (const item of treeItems.value) {
    const depth = item.depth || 0
    const indent = '  '.repeat(depth)
    const title = item.title || item.chapter_id
    mdText += `${indent}- ${title}\n`
  }
  const blob = new Blob([mdText], { type: 'text/markdown;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `章节目录_${props.workspaceId || 'outline'}.md`
  a.click()
  URL.revokeObjectURL(url)
}

const rightTab = ref('chat')
const chatInput = ref('')
const chatTurns = ref([])
const chatLoading = ref(false)
const asking = ref(false)
const chatHistoryEl = ref(null)
const editorRef = ref(null)
/** In-session cache of chapter dialogue; server history remains source of truth. */
const chatByChapter = new Map()
let chatLoadToken = 0

let pollTimer = null

const selectedChapter = computed(() =>
  items.value.find(item => item.chapter_id === selectedId.value) || null,
)
const selectedIsLeaf = computed(() => {
  if (!selectedId.value) return false
  if (typeof chapterDetail.value?.is_leaf === 'boolean') return chapterDetail.value.is_leaf
  if (typeof selectedChapter.value?.is_leaf === 'boolean') return selectedChapter.value.is_leaf
  return !items.value.some(item => item.parent_chapter_id === selectedId.value)
})
const editorBlocks = computed(() => chapterDetail.value?.content?.blocks || [])
const contextItems = computed(() => chapterDetail.value?.context?.items || [])
const chapterRequirements = computed(() => chapterDetail.value?.chapter_requirements || [])
const chapterScoringRequirements = computed(() => chapterDetail.value?.chapter_scoring_requirements || [])
const chapterContextRef = computed(() => chapterDetail.value?.chapter_context_ref || {})
const documentOutlineContext = computed(() => chapterDetail.value?.document_outline_context || null)
const writingOrientation = computed(() => chapterDetail.value?.writing_orientation || null)
const orientationPathLabel = computed(() => String(
  writingOrientation.value?.document_position?.path_label || outlinePathLabel.value || '',
).trim())
const orientationRelations = computed(() => {
  const rows = writingOrientation.value?.chapter_relations?.items
  return Array.isArray(rows) ? rows : []
})
const orientationMaterials = computed(() => {
  const notes = writingOrientation.value?.existing_materials?.notes
  return Array.isArray(notes) && notes.length ? notes.join('；') : ''
})
const outlineFlat = computed(() => {
  const rows = documentOutlineContext.value?.outline
  return Array.isArray(rows) ? rows : []
})
const outlinePathLabel = computed(() => String(documentOutlineContext.value?.position?.path_label || '').trim())
const outlineGuidance = computed(() => {
  const policy = documentOutlineContext.value?.writing_policy
  return String(policy?.guidance || '').trim()
})
const outlineRoleLabel = computed(() => {
  const role = String(documentOutlineContext.value?.current_role || '')
  if (role === 'visual') return '图示/路线图'
  if (role === 'method') return '方法细则'
  if (role === 'overview') return '总体骨架'
  return role || '目录'
})
const readonlyView = ref(null)
const readonlyLoading = ref(false)
const readonlyTargetId = ref('')
const globalContextReady = computed(() => Boolean(
  globalProjectContext.value?.global_context_id
  && Number(globalProjectContext.value?.global_context_revision || 0) > 0
  && globalProjectContext.value?.global_context_hash
  && Object.keys(globalProjectContext.value?.identity || {}).length,
))
const globalIdentityRows = computed(() => {
  const identity = globalProjectContext.value?.identity || {}
  const aliases = [
    ['项目名称', ['project_name', '项目名称', 'project', '项目']],
    ['项目编号', ['project_no', 'project_number', '项目编号', '采购编号', '招标编号']],
    ['采购人', ['purchaser', 'procurer', 'buyer', '采购人', '招标人', '采购单位']],
    ['标包', ['package', 'lot', '标包', '包号', '包件', '标段']],
    ['项目地点', ['location', 'project_location', '项目地点', '服务地点']],
  ]
  return aliases
    .map(([label, keys]) => ({ label, value: keys.map(key => identity[key]).find(Boolean) || '' }))
    .filter(item => item.value)
})
const globalFactGroups = computed(() => [
  ['background', '项目背景'],
  ['goals', '建设目标'],
  ['scope', '采购范围'],
  ['work_packages', '核心工作任务'],
  ['inputs', '输入数据'],
  ['processing', '处理任务'],
  ['outputs', '输出成果'],
  ['deliverables', '交付物'],
  ['acceptance_conditions', '验收条件'],
  ['milestones', '服务期限与进度'],
  ['constraints', '共同约束'],
].map(([key, label]) => ({
  key,
  label,
  items: Array.isArray(globalProjectContext.value?.[key]) ? globalProjectContext.value[key] : [],
})).filter(group => group.items.length))
const materializedCount = computed(() =>
  items.value.filter(item => item.materialized || item.status === 'active').length,
)
const formalCount = computed(() =>
  items.value.filter(item => Number(item.formal_content_revision || 0) > 0).length,
)
const canApprove = computed(() => {
  const head = Number(chapterDetail.value?.head_content_revision || 0)
  const formal = Number(chapterDetail.value?.formal_content_revision || 0)
  return selectedIsLeaf.value
    && Boolean(chapterDetail.value?.materialized)
    && head > 0
    && head !== formal
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

function relevanceTierLabel(tier) {
  return {
    project_direct: '本项目资料',
    similar_project: '同类项目资料',
    industry_standard: '行业标准',
  }[String(tier || '')] || '公开线索'
}

function openProjectContextSource() {
  router.push(`/business/${encodeURIComponent(props.workspaceId)}/pipeline`).catch(() => {})
}

async function refreshSnapshotRevision() {
  const snap = await fetchSnapshot(props.workspaceId)
  if (snap.data?.ok) {
    workspaceRevision.value = Number(snap.data.snapshot?.workspace_revision || 0)
    globalProjectContext.value = snap.data.snapshot?.global_project_context || {}
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
  const { force = true, background = false } = options
  if (background && streamingDraft.value) return
  if (!selectedId.value) {
    chapterDetail.value = null
    detailError.value = ''
    return
  }
  const dirty = editorRef.value?.dirty
  if (dirty && !force) {
    remoteHint.value = '远端已更新；本地有未保存编辑，未覆盖草稿'
    return
  }
  const requestedChapterId = selectedId.value
  if (!background || !chapterDetail.value) detailLoading.value = true
  if (!background) {
    actionError.value = ''
    detailError.value = ''
  }
  try {
    const { data } = await fetchChapter(props.workspaceId, requestedChapterId)
    if (!data.ok) throw new Error(data.message || '加载章节失败')
    if (requestedChapterId !== selectedId.value) return
    const currentContent = chapterDetail.value?.content
    const nextContent = data.chapter?.content
    const currentSignature = `${currentContent?.content_revision || 0}:${currentContent?.content_hash || ''}`
    const nextSignature = `${nextContent?.content_revision || 0}:${nextContent?.content_hash || ''}`
    chapterDetail.value = currentContent && currentSignature === nextSignature
      ? { ...data.chapter, content: currentContent }
      : data.chapter
    detailError.value = ''
    remoteHint.value = ''
    await refreshSnapshotRevision()
  } catch (e) {
    if (!background) detailError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    if (requestedChapterId === selectedId.value) detailLoading.value = false
  }
}

async function reloadAll() {
  busy.value = true
  try {
    await loadChapterList()
    await Promise.all([
      loadChapterDetail({ force: true }),
      selectedId.value ? loadChapterChat(selectedId.value, { force: true }) : Promise.resolve(),
    ])
  } finally {
    busy.value = false
  }
}

function outlineContentLabel(item) {
  const status = String(item?.content_status || '')
  if (status === 'formal') return '正式'
  if (status === 'draft') return `草稿 r${item?.content_revision || 0}`
  if (item?.is_leaf === false) return '结构'
  return '无正文'
}

async function inspectOutlineChapter(item) {
  const targetId = String(item?.chapter_id || '').trim()
  const viewerId = String(selectedId.value || '').trim()
  if (!targetId || !viewerId) return
  if (item?.is_current || targetId === viewerId) {
    readonlyView.value = null
    readonlyTargetId.value = ''
    return
  }
  readonlyLoading.value = true
  readonlyTargetId.value = targetId
  actionError.value = ''
  try {
    const { data } = await fetchChapterReadonlyView(props.workspaceId, viewerId, targetId)
    if (readonlyTargetId.value !== targetId) return
    if (!data?.ok) throw new Error(data?.message || '加载他章只读信息失败')
    readonlyView.value = data.chapter_view || null
    rightTab.value = 'context'
  } catch (e) {
    if (readonlyTargetId.value !== targetId) return
    actionError.value = e?.response?.data?.message || e.message || String(e)
    readonlyView.value = null
  } finally {
    if (readonlyTargetId.value === targetId) readonlyLoading.value = false
  }
}

function closeReadonlyView() {
  readonlyView.value = null
  readonlyTargetId.value = ''
  readonlyLoading.value = false
}

function selectChapter(chapterId) {
  if (selectedId.value === chapterId) return
  if (editorRef.value?.dirty) {
    actionError.value = '当前章节有未保存修改，请先保存正文再切换章节。'
    return
  }
  if (draftAbortController) draftAbortController.abort()
  draftAbortController = null
  streamingDraft.value = false
  busy.value = false
  busyAction.value = ''
  streamText.value = ''
  streamOperationId.value = ''
  researchStatus.value = ''
  researchSources.value = []
  detailError.value = ''
  chapterDetail.value = null
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

function streamDeltaText(event) {
  const payload = event?.data && typeof event.data === 'object' ? event.data : event
  return String(payload?.text ?? payload?.delta ?? payload?.content ?? '')
}

function shouldFollowDraft() {
  const el = docBodyEl.value
  if (!el) return false
  return el.scrollHeight - el.scrollTop - el.clientHeight <= 80
}

async function appendDraftDelta(text) {
  if (!text) return
  const follow = shouldFollowDraft()
  streamText.value += text
  if (follow) {
    await nextTick()
    const el = docBodyEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
}

async function generateDraft() {
  if (!selectedId.value) return
  if (!selectedIsLeaf.value) {
    actionError.value = '目录父节点只保留标题，不生成正文；请选择下级叶子章节。'
    return
  }
  if (editorRef.value?.dirty) {
    actionError.value = '当前章节有未保存修改，请先保存后再生成草稿。'
    return
  }
  const chapterId = selectedId.value
  draftAbortController?.abort()
  draftAbortController = new AbortController()
  const controller = draftAbortController
  const operationId = `draft-${chapterId}-${Date.now()}`
  streamOperationId.value = operationId
  streamText.value = ''
  researchStatus.value = ''
  researchSources.value = []
  streamingDraft.value = true
  busy.value = true
  busyAction.value = 'draft'
  actionError.value = ''
  actionMessage.value = ''
  rightTab.value = 'chat'
  const draftTurnId = `draft-${operationId}`
  const draftTurn = {
    id: draftTurnId,
    turn_id: '',
    role: 'assistant',
    content: '',
    thinking: '',
    thinkingOpen: true,
    streaming: true,
    editing: false,
  }
  chatTurns.value = [...chatTurns.value, draftTurn]
  rememberChapterChat(chapterId, chatTurns.value)
  await scrollChatToBottom()
  const patchDraftTurn = (mutator) => {
    if (selectedId.value !== chapterId) return
    chatTurns.value = chatTurns.value.map((turn) => {
      if (turn.id !== draftTurnId) return turn
      const copy = { ...turn }
      mutator(copy)
      return copy
    })
    rememberChapterChat(chapterId, chatTurns.value)
  }
  let streamCompleted = false
  let completedChapter = null
  let completedContent = null
  try {
    await refreshSnapshotRevision()
    const globalRef = globalProjectContext.value || {}
    const chapterRef = chapterDetail.value?.chapter_context_ref || {}
    if (!globalContextReady.value) {
      throw new Error('公共项目事实尚未就绪，已阻止生成。请先完成项目理解并晋级。')
    }
    if (!chapterRef.chapter_context_id || !chapterRef.chapter_context_hash) {
      throw new Error('本章上下文版本缺失，已阻止生成。请刷新章节后重试。')
    }
    await streamChapterDraft(props.workspaceId, chapterId, {
      expected_revision: workspaceRevision.value,
      expected_chapter_revision: Number(chapterDetail.value?.chapter_revision || 0),
      idempotency_key: operationId,
      overwrite_locked: false,
      global_context_id: globalRef.global_context_id,
      global_context_revision: Number(globalRef.global_context_revision),
      global_context_hash: globalRef.global_context_hash,
      chapter_context_id: chapterRef.chapter_context_id,
      chapter_context_revision: Number(chapterRef.chapter_context_revision || 0),
      chapter_context_hash: chapterRef.chapter_context_hash,
    }, {
      signal: controller.signal,
      onEvent: (event) => {
        if (controller.signal.aborted || chapterId !== selectedId.value) return
        const type = String(event?.type || event?.event || '').toLowerCase()
        if (type === 'meta') {
          streamOperationId.value = String(event.operation_id || event?.data?.operation_id || operationId)
        } else if (type === 'research') {
          const payload = event?.data && typeof event.data === 'object' ? event.data : event
          const note = String(payload.message || '正在检索公开资料…').trim()
          researchStatus.value = note
          researchSources.value = Array.isArray(payload.sources) ? payload.sources : []
          if (note) {
            patchDraftTurn((turn) => {
              turn.thinking = turn.thinking ? `${turn.thinking}\n${note}` : note
              turn.thinkingOpen = true
              turn.streaming = true
            })
            scrollChatToBottom()
          }
        } else if (type === 'thinking_delta') {
          const delta = String(event.delta || event?.data?.delta || '')
          if (!delta) return
          patchDraftTurn((turn) => {
            turn.thinking = `${turn.thinking || ''}${delta}`
            turn.thinkingOpen = true
            turn.streaming = true
          })
          scrollChatToBottom()
        } else if (['delta', 'content_delta', 'token'].includes(type)) {
          appendDraftDelta(streamDeltaText(event))
        } else if (type === 'done') {
          const payload = event?.data && typeof event.data === 'object' ? event.data : event
          streamCompleted = true
          completedChapter = payload?.chapter && typeof payload.chapter === 'object'
            ? payload.chapter
            : null
          completedContent = payload?.content && typeof payload.content === 'object'
            ? payload.content
            : null
        } else if (type === 'error') {
          const payload = event?.data && typeof event.data === 'object' ? event.data : event
          const reason = String(payload?.details?.error || payload?.details?.reason || '').trim()
          const message = String(payload?.message || '流式生成失败')
          if (String(payload?.code || '') === 'CHAPTER_RESEARCH_UNAVAILABLE') {
            researchStatus.value = reason ? `公开资料检索失败：${reason}` : message
          }
          throw new Error(reason ? `${message}（${reason}）` : message)
        }
      },
    })
    if (controller.signal.aborted || chapterId !== selectedId.value) return
    if (!streamCompleted) throw new Error('流式连接提前结束，未收到完成事件')
    if (completedChapter || completedContent) {
      const current = chapterDetail.value || {}
      chapterDetail.value = {
        ...current,
        ...(completedChapter || {}),
        content: completedContent || completedChapter?.content || current.content,
      }
    }
    streamingDraft.value = false
    streamText.value = ''
    researchStatus.value = ''
    researchSources.value = []
    remoteHint.value = ''
    actionMessage.value = '草稿已生成'
    patchDraftTurn((turn) => {
      turn.streaming = false
      turn.thinkingOpen = true
      if (!turn.content) turn.content = '已生成本章草稿。思考过程见上方，正文已写入中间文档。'
    })
    await loadChapterList()
    await loadChapterDetail({ force: true, background: true })
  } catch (e) {
    if (e?.name !== 'AbortError') {
      actionError.value = e?.message || String(e)
      remoteHint.value = '流式连接已中断，已保留当前预览；可刷新检查后端是否已完成。'
    }
  } finally {
    if (draftAbortController === controller) draftAbortController = null
    if (chapterId === selectedId.value) {
      streamingDraft.value = false
      busy.value = false
      busyAction.value = ''
      patchDraftTurn((turn) => {
        turn.streaming = false
        if (turn.content) return
        if (controller.signal.aborted) {
          turn.content = '草稿生成已中断。思考过程保留在本条对话中。'
        } else if (actionError.value) {
          turn.content = `草稿未完成：${actionError.value}`
        } else if (turn.thinking) {
          turn.content = '已生成本章草稿。思考过程见上方，正文已写入中间文档。'
        }
      })
    }
  }
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

function mapChatTurns(turns) {
  return (Array.isArray(turns) ? turns : []).map((turn, index) => ({
    id: String(turn.turn_id || `${turn.role || 'turn'}-${turn.created_at || index}-${index}`),
    turn_id: String(turn.turn_id || ''),
    role: turn.role === 'user' ? 'user' : 'assistant',
    content: String(turn.content || ''),
    thinking: String(turn.thinking || ''),
    thinkingOpen: true,
    streaming: false,
    editing: false,
    created_at: turn.created_at || '',
  }))
}

function canEditChatTurn(turn) {
  return Boolean(selectedId.value && turn && !turn.streaming && !asking.value)
}

function onChatComposeKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) {
    return
  }
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  sendChat()
}

function onChatTurnFocus(turn) {
  if (!turn || turn.streaming) return
  turn.editing = true
}

async function onChatTurnBlur(turn, field, event) {
  if (!turn) return
  turn.editing = false
  const chapterId = String(selectedId.value || '').trim()
  if (!chapterId || turn.streaming || asking.value) return
  const next = String(event?.target?.innerText || '').replace(/\u00a0/g, ' ')
  const current = field === 'thinking' ? String(turn.thinking || '') : String(turn.content || '')
  if (next === current) return
  const previous = { content: turn.content, thinking: turn.thinking }
  if (field === 'thinking') turn.thinking = next
  else turn.content = next
  if (!String(turn.content || '').trim() && !String(turn.thinking || '').trim()) {
    turn.content = previous.content
    turn.thinking = previous.thinking
    if (event?.target) event.target.innerText = current
    actionError.value = '正文和思考过程不能同时为空。'
    return
  }
  rememberChapterChat(chapterId, chatTurns.value)
  try {
    const { data } = await saveChapterChatTurn(props.workspaceId, chapterId, {
      turn_id: turn.turn_id || turn.id,
      created_at: turn.created_at || '',
      role: turn.role,
      content: turn.content,
      thinking: turn.thinking,
    })
    if (!data?.ok) throw new Error(data?.message || '保存对话失败')
    if (data.turn?.turn_id) turn.turn_id = String(data.turn.turn_id)
    rememberChapterChat(chapterId, chatTurns.value)
  } catch (e) {
    turn.content = previous.content
    turn.thinking = previous.thinking
    if (event?.target) event.target.innerText = current
    actionError.value = e?.response?.data?.message || e.message || String(e)
  }
}

async function scrollChatToBottom() {
  await nextTick()
  if (chatHistoryEl.value) chatHistoryEl.value.scrollTop = chatHistoryEl.value.scrollHeight
}

function rememberChapterChat(chapterId, turns) {
  if (!chapterId) return
  chatByChapter.set(chapterId, turns)
}

async function loadChapterChat(chapterId, { force = false } = {}) {
  const id = String(chapterId || '').trim()
  if (!id) {
    chatTurns.value = []
    chatLoading.value = false
    return
  }
  if (!force && chatByChapter.has(id)) {
    chatTurns.value = chatByChapter.get(id)
    await nextTick()
    if (chatHistoryEl.value) chatHistoryEl.value.scrollTop = chatHistoryEl.value.scrollHeight
    return
  }
  const token = ++chatLoadToken
  chatLoading.value = true
  try {
    const { data } = await fetchChapterChatHistory(props.workspaceId, id)
    if (token !== chatLoadToken || selectedId.value !== id) return
    if (!data?.ok) throw new Error(data?.message || '加载本章对话失败')
    const turns = mapChatTurns(data.turns)
    rememberChapterChat(id, turns)
    chatTurns.value = turns
    await nextTick()
    if (chatHistoryEl.value) chatHistoryEl.value.scrollTop = chatHistoryEl.value.scrollHeight
  } catch (e) {
    if (token !== chatLoadToken || selectedId.value !== id) return
    // Soft-fail: keep empty local thread so user can still type.
    if (!chatByChapter.has(id)) {
      chatTurns.value = []
    }
    actionError.value = e?.response?.data?.message || e.message || String(e)
  } finally {
    if (token === chatLoadToken) chatLoading.value = false
  }
}

async function sendChat() {
  const text = chatInput.value.trim()
  const chapterId = String(selectedId.value || '').trim()
  if (!text || asking.value || !chapterId) return
  asking.value = true
  actionError.value = ''
  const userTurn = {
    id: `u-${Date.now()}`,
    turn_id: '',
    role: 'user',
    content: text,
    thinking: '',
    thinkingOpen: true,
    streaming: false,
    editing: false,
  }
  const assistantId = `a-${Date.now()}`
  const assistantTurn = {
    id: assistantId,
    turn_id: '',
    role: 'assistant',
    content: '',
    thinking: '',
    thinkingOpen: true,
    streaming: true,
    editing: false,
  }
  const seedTurns = [...chatTurns.value, userTurn, assistantTurn]
  chatTurns.value = seedTurns
  rememberChapterChat(chapterId, seedTurns)
  chatInput.value = ''
  await scrollChatToBottom()

  const patchAssistant = (mutator) => {
    const source = selectedId.value === chapterId
      ? chatTurns.value
      : (chatByChapter.get(chapterId) || [])
    const next = source.map((turn) => {
      if (turn.id !== assistantId) return turn
      const copy = { ...turn }
      mutator(copy)
      return copy
    })
    rememberChapterChat(chapterId, next)
    if (selectedId.value === chapterId) chatTurns.value = next
  }

  try {
    let completedTurns = null
    await streamChapterChat(props.workspaceId, chapterId, text, {
      onEvent: async (event) => {
        const type = String(event?.type || '').toLowerCase()
        if (type === 'inspect_planning' || type === 'inspecting' || type === 'inspect_skipped') {
          const note = String(event.message || event.reason || '').trim()
          if (!note) return
          patchAssistant((turn) => {
            turn.thinking = turn.thinking
              ? `${turn.thinking}\n${note}`
              : note
            turn.thinkingOpen = true
            turn.streaming = true
          })
          if (selectedId.value === chapterId) await scrollChatToBottom()
        } else if (type === 'thinking_delta') {
          const delta = String(event.delta || '')
          if (!delta) return
          patchAssistant((turn) => {
            turn.thinking = `${turn.thinking || ''}${delta}`
            turn.thinkingOpen = true
            turn.streaming = true
          })
          if (selectedId.value === chapterId) await scrollChatToBottom()
        } else if (type === 'content_delta') {
          const delta = String(event.delta || '')
          if (!delta) return
          patchAssistant((turn) => {
            turn.content = `${turn.content || ''}${delta}`
            turn.streaming = true
          })
          if (selectedId.value === chapterId) await scrollChatToBottom()
        } else if (type === 'done') {
          if (Array.isArray(event.turns) && event.turns.length) {
            completedTurns = mapChatTurns(event.turns)
          } else {
            patchAssistant((turn) => {
              turn.content = String(event.reply || turn.content || '（无回复）')
              turn.thinking = String(event.thinking || turn.thinking || '')
              turn.streaming = false
              turn.thinkingOpen = true
            })
          }
          if (event.workspace_revision != null && selectedId.value === chapterId) {
            workspaceRevision.value = Number(event.workspace_revision)
          }
        } else if (type === 'error') {
          throw new Error(event.message || '章节对话失败')
        }
      },
    })

    if (completedTurns) {
      rememberChapterChat(chapterId, completedTurns)
      if (selectedId.value === chapterId) {
        chatTurns.value = completedTurns
        await scrollChatToBottom()
      }
    } else {
      patchAssistant((turn) => {
        turn.streaming = false
        if (!turn.content) turn.content = '（无回复）'
      })
    }
  } catch (e) {
    actionError.value = e?.response?.data?.message || e.message || String(e)
    if (selectedId.value !== chapterId) {
      const cached = chatByChapter.get(chapterId) || []
      rememberChapterChat(
        chapterId,
        cached.map((turn) => (
          turn.id === assistantId
            ? {
                ...turn,
                streaming: false,
                content: turn.content || `请求失败：${actionError.value}`,
              }
            : turn
        )),
      )
      return
    }
    patchAssistant((turn) => {
      turn.streaming = false
      turn.content = turn.content
        ? `${turn.content}\n\n请求失败：${actionError.value}`
        : `请求失败：${actionError.value}`
    })
  } finally {
    asking.value = false
  }
}

watch(
  () => selectedId.value,
  async (id, prev) => {
    if (!id || id === prev) return
    chatInput.value = ''
    closeReadonlyView()
    await Promise.all([
      loadChapterDetail({ force: true }),
      loadChapterChat(id),
    ])
  },
)

watch(
  () => props.initialChapterId,
  (id) => {
    if (id && id !== selectedId.value) selectChapter(id)
  },
)

onMounted(async () => {
  if (props.initialChapterId) selectedId.value = props.initialChapterId
  await reloadAll()
  pollTimer = setInterval(async () => {
    try {
      await loadChapterList()
      if (!streamingDraft.value) await loadChapterDetail({ force: false, background: true })
    } catch (_) {
      /* ignore */
    }
  }, 6000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  draftAbortController?.abort()
})
</script>

<style scoped>
.workbench {
  display: grid;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #f1f5f9;
}

.workbench.is-dragging {
  user-select: none !important;
  cursor: col-resize !important;
}

.resizer {
  width: 6px;
  background: #f8fafc;
  border-left: 1px solid #e2e8f0;
  border-right: 1px solid #e2e8f0;
  cursor: col-resize;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, border-color 0.15s;
  z-index: 10;
}

.resizer:hover,
.workbench.is-dragging .resizer {
  background: #2563eb;
  border-color: #2563eb;
}

.resizer-handle {
  width: 2px;
  height: 20px;
  background: #cbd5e1;
  border-radius: 1px;
  transition: background 0.15s;
}

.resizer:hover .resizer-handle,
.workbench.is-dragging .resizer-handle {
  background: #ffffff;
}

.pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  background: #fff;
}
.pane-chat {
  border-left: none;
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
.chat-chapter-label {
  margin: 4px 0 0;
  font-size: 12px;
  color: #1d4ed8;
  line-height: 1.4;
  word-break: break-word;
}
.chat-chapter-label.muted {
  color: #94a3b8;
}
.tree-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}
.tree-toolbar > .btn {
  flex: 1 1 calc(50% - 3px);
  min-width: 0;
  white-space: nowrap;
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
.chapter-doc-body {
  --word-page-width: 850px;
  --word-page-min-height: 1120px;
  flex: 1;
  display: block;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
  overscroll-behavior: contain;
  padding: 28px 32px 56px;
  background: #e7ebf0;
}
.document-stage {
  width: var(--word-page-width);
  min-height: var(--word-page-min-height);
  margin: 0 auto;
}
.document-paper {
  box-sizing: border-box;
  width: 100%;
  min-height: var(--word-page-min-height);
  padding: 72px 78px;
  border: 1px solid #d5dbe5;
  background: #fff;
  box-shadow: 0 16px 42px rgb(15 23 42 / 10%);
  color: #111827;
  font-family: "SimSun", "Songti SC", "Noto Serif CJK SC", serif;
}
.paper-heading { margin-bottom: 30px; }
.paper-heading h1 {
  margin: 0;
  color: #111827;
  font-family: "SimHei", "Microsoft YaHei", sans-serif;
  font-size: 28px;
  line-height: 1.45;
  font-weight: 700;
  text-align: center;
}
.research-status {
  margin: 0 0 24px;
  padding: 12px 14px;
  border-left: 3px solid #0f766e;
  background: #f0fdfa;
  color: #115e59;
  font-size: 13px;
  line-height: 1.6;
}
.research-status ul { margin: 8px 0 0; padding-left: 18px; }
.orientation-relations {
  margin: 8px 0 0;
  padding-left: 18px;
  color: #334155;
  font-size: 12px;
  line-height: 1.55;
}
.orientation-relations em {
  font-style: normal;
  color: #0f766e;
  font-weight: 700;
}
.research-status a { color: #0f766e; text-decoration: underline; }
.source-tier {
  display: inline-flex;
  margin-right: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  background: #e2e8f0;
  color: #475569;
  font-size: 10px;
  font-weight: 700;
}
.source-tier.project_direct { background: #dcfce7; color: #166534; }
.source-tier.similar_project { background: #ffedd5; color: #9a3412; }
.source-tier.industry_standard { background: #dbeafe; color: #1d4ed8; }
.document-state {
  min-height: 820px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  text-align: center;
  color: #64748b;
  padding: 24px;
}
.document-state h4 { margin: 0; color: #0f172a; font-family: "Microsoft YaHei", sans-serif; }
.document-state p { margin: 0; max-width: 360px; font-family: "Microsoft YaHei", sans-serif; font-size: 13px; }
.document-error h4 { color: #b91c1c; }
.document-error p { color: #7f1d1d; }
.document-loading-mark {
  width: 28px;
  height: 28px;
  border: 3px solid #dbeafe;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: document-loading-spin 800ms linear infinite;
}
@keyframes document-loading-spin {
  to { transform: rotate(360deg); }
}
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
.context-section {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px;
  background: #fff;
}
.shared-context { border-color: #bfdbfe; background: #f8fbff; }
.chapter-only-context { margin-top: 2px; }
.context-section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 9px;
}
.context-section-header strong { display: block; font-size: 13px; color: #0f172a; }
.context-section-header small { display: block; margin-top: 2px; color: #64748b; font-size: 10px; }
.context-version {
  flex-shrink: 0;
  border-radius: 999px;
  padding: 2px 7px;
  background: #e0e7ff;
  color: #3730a3;
  font-size: 10px;
  font-weight: 700;
}
.context-warning {
  padding: 8px;
  border-radius: 7px;
  background: #fef2f2;
  color: #b91c1c;
  font-size: 11px;
  line-height: 1.5;
}
.identity-grid {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  gap: 5px 8px;
  margin: 0 0 9px;
  font-size: 11px;
}
.identity-grid dt { color: #64748b; }
.identity-grid dd { margin: 0; color: #0f172a; overflow-wrap: anywhere; }
.fact-group { border-top: 1px solid #dbeafe; padding: 6px 0; }
.fact-group summary { cursor: pointer; color: #1e3a8a; font-size: 11px; font-weight: 700; }
.fact-group summary span { color: #94a3b8; font-weight: 500; }
.fact-group ul { margin: 6px 0 0; padding-left: 17px; color: #334155; font-size: 11px; line-height: 1.55; }
.context-note { margin: 8px 0 0; color: #64748b; font-size: 10px; line-height: 1.5; }
.context-project-link {
  width: 100%;
  margin-top: 7px;
  border: 1px solid #bfdbfe;
  border-radius: 7px;
  padding: 6px 8px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 11px;
  cursor: pointer;
}
.requirement-card { border-left: 3px solid #2563eb; }
.scoring-card { border-left: 3px solid #f59e0b; }
.outline-path { color: #0f766e; font-weight: 600; }
.outline-readonly-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 280px;
  overflow: auto;
  margin-top: 8px;
  padding-right: 2px;
}
.outline-readonly-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  color: #0f172a;
  padding: 7px 10px;
  text-align: left;
  cursor: pointer;
}
.outline-readonly-item:hover { border-color: #93c5fd; background: #eff6ff; }
.outline-readonly-item.current {
  border-color: #2563eb;
  background: #dbeafe;
  cursor: default;
}
.outline-readonly-item.empty { opacity: 0.85; }
.outline-readonly-title {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  word-break: break-word;
}
.outline-readonly-meta em {
  font-style: normal;
  font-size: 10px;
  color: #64748b;
  white-space: nowrap;
}
.outline-readonly-item.current .outline-readonly-meta em { color: #1d4ed8; font-weight: 700; }
.readonly-chapter-panel {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #cbd5e1;
}
.btn-tiny {
  padding: 2px 8px;
  font-size: 11px;
}
.sibling-summary {
  max-height: 7.5em;
  overflow: auto;
  white-space: pre-wrap;
  line-height: 1.5;
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
.chat-bubble p,
.chat-content {
  margin: 0;
  white-space: pre-wrap;
  color: #0f172a;
  outline: none;
}
.chat-content[contenteditable="true"],
.thinking-body[contenteditable="true"] {
  cursor: text;
  border-radius: 6px;
}
.chat-content[contenteditable="true"]:focus,
.thinking-body[contenteditable="true"]:focus {
  box-shadow: inset 0 0 0 1px #93c5fd;
  background: #fff;
}
.chat-bubble.streaming {
  border-color: #93c5fd;
  box-shadow: 0 0 0 1px rgb(147 197 253 / 35%);
}
.chat-thinking {
  margin: 6px 0 8px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #eef2ff;
  padding: 6px 8px;
}
.thinking-label {
  color: #4338ca;
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}
.thinking-live {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 10px;
  padding: 1px 6px;
  font-weight: 700;
}
.thinking-body {
  margin: 6px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: #334155;
  font-size: 12px;
  line-height: 1.55;
  max-height: 280px;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  outline: none;
}
.chat-edit-hint {
  display: block;
  margin-top: 6px;
  color: #94a3b8;
  font-size: 11px;
}
.chat-streaming-hint {
  color: #64748b !important;
  font-style: italic;
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

/* 侧边栏菜单手动修改样式扩展 */
.tree-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  color: #334155;
  transition: all 0.15s ease;
}
.tree-item:hover {
  background: #f1f5f9;
}
.tree-item.active {
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
  font-weight: 600;
}
.tree-item-input {
  flex: 1;
  min-width: 0;
  padding: 2px 6px;
  font-size: 12px;
  border: 1px solid #2563eb;
  border-radius: 4px;
  outline: none;
  background: #fff;
}
.tree-item-actions {
  display: none;
  align-items: center;
  gap: 2px;
  margin-left: auto;
}
.tree-item:hover .tree-item-actions {
  display: flex;
}
.tree-item:hover .tree-meta {
  display: none;
}
.icon-action-btn {
  background: transparent;
  border: none;
  padding: 2px 4px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  opacity: 0.7;
  transition: opacity 0.15s, background 0.15s;
}
.icon-action-btn:hover:not(:disabled) {
  opacity: 1;
  background: #e2e8f0;
}
.icon-action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.icon-action-btn.danger:hover:not(:disabled) {
  background: #fee2e2;
}

/* Modal 弹窗样式 */
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}
.modal-card {
  background: #fff;
  border-radius: 12px;
  width: 420px;
  max-width: 90vw;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid #e2e8f0;
}
.modal-header h3 { margin: 0; font-size: 16px; color: #0f172a; }
.close-btn { background: none; border: none; font-size: 20px; color: #64748b; cursor: pointer; }
.modal-body { padding: 18px; display: flex; flex-direction: column; gap: 14px; }
.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-group label { font-size: 13px; font-weight: 600; color: #334155; }
.form-control { padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 14px; outline: none; }
.form-control:focus { border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15); }
.form-hint { font-size: 11px; color: #94a3b8; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 18px; background: #f8fafc; border-top: 1px solid #e2e8f0; }

@media (max-width: 1100px) {
  .workbench {
    /* inline style still drives columns; keep media hook for future tweaks */
  }
}
@media (max-width: 720px) {
  .chapter-doc-body { padding: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .document-loading-mark { animation: none; }
}
</style>
