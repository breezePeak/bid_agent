<template>
  <div class="block-editor word-style-editor">
    <!-- 顶部 Word 风格工具栏 -->
    <div class="word-toolbar">
      <div class="toolbar-left">
        <button
          type="button"
          class="word-btn word-btn-primary"
          :disabled="readonly || busy || !dirty"
          @click="save"
        >
          <svg class="icon-svg" viewBox="0 0 20 20" fill="currentColor">
            <path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/>
          </svg>
          保存正文
        </button>
        <button
          type="button"
          class="word-btn word-btn-secondary"
          :disabled="readonly || busy"
          @click="addParagraph"
        >
          <svg class="icon-svg" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clip-rule="evenodd" />
          </svg>
          插入段落
        </button>
      </div>

      <div class="toolbar-right">
        <span v-if="dirty" class="status-tag dirty-tag">● 未保存修改</span>
        <span v-else class="status-tag saved-tag">✓ 已同步</span>
        <span v-if="remoteHint" class="status-tag remote-tag">{{ remoteHint }}</span>
        <span class="word-count-badge">共 {{ totalChars }} 字</span>
      </div>
    </div>

    <section v-if="streaming" class="streaming-document" role="status" aria-live="polite">
      <div class="streaming-document-header">
        <span class="streaming-document-mark" aria-hidden="true" />
        <strong>正在写入正文</strong>
        <span>已生成 {{ streamText.length }} 字</span>
      </div>
      <p v-if="!streamParagraphs.length" class="streaming-document-placeholder">
        正在根据已确认提纲组织正文，内容生成后会实时显示在这里。
      </p>
      <div v-else class="streaming-document-body">
        <p v-for="(paragraph, index) in streamParagraphs" :key="index">{{ paragraph }}</p>
      </div>
    </section>

    <!-- 正文空状态 -->
    <div v-if="!localBlocks.length && !streaming" class="word-empty-state">
      <p>当前章节暂无正文内容</p>
      <button type="button" class="word-btn word-btn-secondary" :disabled="readonly || busy" @click="addParagraph">
        + 手动新增第一段
      </button>
    </div>

    <!-- Word 规范排版正文流 -->
    <div v-else class="word-document-flow">
      <div
        v-for="(block, index) in localBlocks"
        :key="block.block_id"
        class="word-paragraph-item"
        :class="[
          `block-type-${block.type || 'paragraph'}`,
          {
            'is-locked': block.lock_state === 'USER_LOCKED' || block.human_locked,
            'is-editing': activeBlockId === block.block_id,
          }
        ]"
        @mouseenter="hoveredBlockId = block.block_id"
        @mouseleave="hoveredBlockId = null"
      >
        <!-- 悬浮轻量操作胶囊（平时不打扰排版） -->
        <div
          v-if="!readonly"
          class="floating-actions"
          :class="{ 'is-visible': hoveredBlockId === block.block_id || activeBlockId === block.block_id }"
        >
          <span class="para-idx">P{{ index + 1 }}</span>
          <span v-if="block.lock_state === 'USER_LOCKED' || block.human_locked" class="para-badge lock">已锁定</span>
          <button
            type="button"
            class="action-btn"
            title="上移段落"
            :disabled="busy || index === 0"
            @click.stop="move(block.block_id, index - 1)"
          >↑</button>
          <button
            type="button"
            class="action-btn"
            title="下移段落"
            :disabled="busy || index >= localBlocks.length - 1"
            @click.stop="move(block.block_id, index + 1)"
          >↓</button>
          <button
            type="button"
            class="action-btn danger"
            title="删除段落"
            :disabled="busy"
            @click.stop="remove(block.block_id)"
          >×</button>
        </div>

        <!-- 针对标题类型 -->
        <div v-if="block.type === 'heading' || block.type === 'h2' || block.type === 'h3'" class="para-wrapper heading-wrapper">
          <textarea
            :ref="el => setBlockRef(block.block_id, el)"
            :value="block.content"
            :disabled="readonly || busy"
            class="word-textarea heading-textarea"
            rows="1"
            placeholder="输入小节标题…"
            @focus="activeBlockId = block.block_id"
            @blur="activeBlockId = null"
            @input="onTextareaInput(block.block_id, $event)"
          />
        </div>

        <!-- 标准正文段落（中文排版：首行缩进2字符，自然行距） -->
        <div v-else class="para-wrapper paragraph-wrapper">
          <textarea
            :ref="el => setBlockRef(block.block_id, el)"
            :value="block.content"
            :disabled="readonly || busy"
            class="word-textarea paragraph-textarea"
            rows="1"
            placeholder="输入段落正文…"
            @focus="activeBlockId = block.block_id"
            @blur="activeBlockId = null"
            @input="onTextareaInput(block.block_id, $event)"
          />
        </div>
      </div>

      <!-- 底部追加段落提示区 -->
      <div v-if="!readonly && !busy" class="add-para-footer" @click="addParagraph">
        <span>+ 点击在此处添加新段落</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'

const props = defineProps({
  blocks: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  streaming: { type: Boolean, default: false },
  streamText: { type: String, default: '' },
  remoteHint: { type: String, default: '' },
})
const emit = defineEmits(['save'])

const localBlocks = ref([])
const dirty = ref(false)
const baselineJson = ref('[]')
const hoveredBlockId = ref(null)
const activeBlockId = ref(null)
const textareaRefs = new Map()

const totalChars = computed(() => {
  return localBlocks.value.reduce((acc, block) => acc + (block.content ? block.content.length : 0), 0)
})
const streamParagraphs = computed(() => String(props.streamText || '')
  .split(/\n{2,}/)
  .map(item => item.trim())
  .filter(Boolean))

function setBlockRef(id, el) {
  if (el) {
    textareaRefs.set(id, el)
    resizeTextarea(el)
  } else {
    textareaRefs.delete(id)
  }
}

function resizeTextarea(el) {
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.max(el.scrollHeight, 32)}px`
}

function resizeAll() {
  nextTick(() => {
    textareaRefs.forEach((el) => resizeTextarea(el))
  })
}

watch(
  () => props.blocks,
  (value) => {
    if (dirty.value) return
    const next = Array.isArray(value) ? value.map(item => ({ ...item })) : []
    localBlocks.value = next
    baselineJson.value = JSON.stringify(next)
    resizeAll()
  },
  { immediate: true, deep: true },
)

function markDirty() {
  dirty.value = JSON.stringify(localBlocks.value) !== baselineJson.value
}

function onTextareaInput(blockId, event) {
  const content = event.target.value
  resizeTextarea(event.target)
  localBlocks.value = localBlocks.value.map(item => (
    item.block_id === blockId ? { ...item, content } : item
  ))
  markDirty()
}

function addParagraph() {
  const blockId = `local-${Date.now()}`
  localBlocks.value = [
    ...localBlocks.value,
    {
      block_id: blockId,
      type: 'paragraph',
      content: '',
      source: 'USER_CREATED',
      lock_state: 'USER_LOCKED',
      human_locked: true,
      order: localBlocks.value.length,
    },
  ]
  markDirty()
  nextTick(() => {
    const el = textareaRefs.get(blockId)
    if (el) {
      resizeTextarea(el)
      el.focus()
    }
  })
}

function remove(blockId) {
  localBlocks.value = localBlocks.value.filter(item => item.block_id !== blockId)
  markDirty()
  resizeAll()
}

function move(blockId, toIndex) {
  const list = [...localBlocks.value]
  const from = list.findIndex(item => item.block_id === blockId)
  if (from < 0) return
  const [item] = list.splice(from, 1)
  list.splice(toIndex, 0, item)
  localBlocks.value = list.map((block, order) => ({ ...block, order }))
  markDirty()
  resizeAll()
}

function save() {
  const baseline = JSON.parse(baselineJson.value || '[]')
  const baselineIds = new Set(baseline.map(item => item.block_id))
  const currentIds = new Set(localBlocks.value.map(item => item.block_id))
  const operations = []

  for (const block of baseline) {
    if (!currentIds.has(block.block_id)) {
      operations.push({ op: 'delete', block_id: block.block_id })
    }
  }
  localBlocks.value.forEach((block, index) => {
    if (!baselineIds.has(block.block_id)) {
      operations.push({
        op: 'insert',
        index,
        block: {
          block_id: block.block_id,
          type: block.type || 'paragraph',
          content: block.content,
          target_node_id: block.target_node_id,
        },
      })
      return
    }
    const prev = baseline.find(item => item.block_id === block.block_id)
    if (prev && prev.content !== block.content) {
      operations.push({
        op: 'update',
        block_id: block.block_id,
        content: block.content,
        type: block.type,
      })
    }
  })
  operations.push({
    op: 'replace_all',
    blocks: localBlocks.value.map((block, order) => ({
      ...block,
      order,
      source: block.source === 'AI_GENERATED' ? 'USER_EDITED' : (block.source || 'USER_CREATED'),
      lock_state: 'USER_LOCKED',
      human_locked: true,
    })),
  })
  emit('save', operations)
  dirty.value = false
  baselineJson.value = JSON.stringify(localBlocks.value)
}

onMounted(() => {
  resizeAll()
})

defineExpose({
  dirty,
  markClean() {
    dirty.value = false
    baselineJson.value = JSON.stringify(localBlocks.value)
  },
})
</script>

<style scoped>
.word-style-editor {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  position: relative;
}

/* 顶部 Word 风格工具栏 */
.word-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 24px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.word-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
  border: 1px solid transparent;
}
.word-btn .icon-svg {
  width: 14px;
  height: 14px;
}
.word-btn-primary {
  background: #2563eb;
  color: #fff;
}
.word-btn-primary:hover:not(:disabled) {
  background: #1d4ed8;
}
.word-btn-secondary {
  background: #ffffff;
  color: #334155;
  border-color: #cbd5e1;
}
.word-btn-secondary:hover:not(:disabled) {
  background: #f1f5f9;
  border-color: #94a3b8;
}
.word-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.status-tag {
  font-size: 12px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 4px;
}
.dirty-tag {
  color: #d97706;
  background: #fef3c7;
}
.saved-tag {
  color: #059669;
  background: #ecfdf5;
}
.remote-tag {
  color: #7c3aed;
  background: #f3e8ff;
}
.word-count-badge {
  font-size: 12px;
  color: #64748b;
  margin-left: 4px;
}

.streaming-document {
  margin-bottom: 18px;
  padding: 16px 18px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #f8fbff;
  color: #1e293b;
}
.streaming-document-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  color: #1d4ed8;
  font-family: "Microsoft YaHei", sans-serif;
  font-size: 13px;
}
.streaming-document-header span:last-child { margin-left: auto; color: #64748b; font-weight: 400; }
.streaming-document-mark {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: #2563eb;
  animation: streaming-document-pulse 1.2s ease-in-out infinite;
}
.streaming-document-placeholder { margin: 0; color: #64748b; font-size: 14px; }
.streaming-document-body p {
  margin: 0 0 10px;
  font-family: "SimSun", "Songti SC", "Noto Serif CJK SC", "STSong", serif;
  font-size: 16px;
  line-height: 1.85;
  text-indent: 2em;
  text-align: justify;
}
.streaming-document-body p:last-child { margin-bottom: 0; }
@keyframes streaming-document-pulse {
  50% { opacity: 0.35; transform: scale(0.8); }
}

/* 正文空状态 */
.word-empty-state {
  text-align: center;
  padding: 48px 16px;
  color: #94a3b8;
  font-size: 14px;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

/* Word 文档正文排版流 */
.word-document-flow {
  display: flex;
  flex-direction: column;
  position: relative;
  width: 100%;
}

.word-paragraph-item {
  position: relative;
  margin-bottom: 12px;
  border-radius: 4px;
  transition: background-color 0.15s ease;
}

.word-paragraph-item:hover,
.word-paragraph-item.is-editing {
  background-color: rgba(241, 245, 249, 0.45);
}

.word-paragraph-item.is-locked {
  border-left: 2px solid #f59e0b;
  padding-left: 6px;
}

/* 悬浮微操作胶囊 */
.floating-actions {
  position: absolute;
  top: -14px;
  right: 6px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  border-radius: 999px;
  padding: 2px 8px;
  z-index: 10;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease, transform 0.15s ease;
  transform: translateY(2px);
}

.floating-actions.is-visible {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0);
}

.para-idx {
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  margin-right: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.para-badge.lock {
  font-size: 10px;
  background: #fef3c7;
  color: #d97706;
  padding: 0 4px;
  border-radius: 3px;
  margin-right: 2px;
}

.action-btn {
  background: transparent;
  border: none;
  color: #64748b;
  font-size: 13px;
  line-height: 1;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.1s ease;
}

.action-btn:hover:not(:disabled) {
  background: #f1f5f9;
  color: #1e293b;
}

.action-btn.danger:hover:not(:disabled) {
  background: #fee2e2;
  color: #dc2626;
}

.action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* 无边框自适应排版输入框（仿 Word 所见即所得） */
.para-wrapper {
  position: relative;
  width: 100%;
}

.word-textarea {
  width: 100%;
  box-sizing: border-box;
  background: transparent;
  border: 1px solid transparent;
  outline: none;
  resize: none;
  overflow: hidden;
  padding: 4px 6px;
  border-radius: 4px;
  color: #111827;
  font-family: "SimSun", "Songti SC", "Noto Serif CJK SC", "STSong", serif;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.word-textarea:focus {
  border-color: #cbd5e1;
  background: #ffffff;
}

/* 正文段落排版：首行缩进 2em，两端对齐，标准行高 1.85，字号 16px */
.paragraph-textarea {
  font-size: 16px;
  line-height: 1.85;
  text-indent: 2em;
  text-align: justify;
  letter-spacing: 0.02em;
}

/* 标题排版：字号加大加粗，无缩进 */
.heading-textarea {
  font-family: "SimHei", "Microsoft YaHei", sans-serif;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.5;
  text-indent: 0;
  color: #0f172a;
}

/* 底部追加段落提示区 */
.add-para-footer {
  margin-top: 14px;
  padding: 10px;
  border: 1px dashed #e2e8f0;
  border-radius: 6px;
  text-align: center;
  color: #94a3b8;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.add-para-footer:hover {
  border-color: #3b82f6;
  color: #2563eb;
  background: #eff6ff;
}
</style>
