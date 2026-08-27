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
  display: flex !important;
  flex-direction: column !important;
  min-height: 100% !important;
  position: relative !important;
}

/* 顶部 Word 风格工具栏 */
.word-toolbar {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  padding: 6px 10px !important;
  background: #f3f3f3 !important;
  border: 1px solid #d2d0ce !important;
  border-radius: 2px !important;
  margin-bottom: 24px !important;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif !important;
}
.toolbar-left, .toolbar-right {
  display: flex !important;
  align-items: center !important;
  gap: 6px !important;
}
.word-btn {
  display: inline-flex !important;
  align-items: center !important;
  gap: 5px !important;
  font-size: 12px !important;
  font-weight: 500 !important;
  padding: 4px 10px !important;
  border-radius: 2px !important;
  cursor: pointer !important;
  transition: all 0.1s ease !important;
  border: 1px solid #d2d0ce !important;
  box-shadow: none !important;
  background: #ffffff !important;
  color: #323130 !important;
}
.word-btn .icon-svg {
  width: 13px !important;
  height: 13px !important;
}
.word-btn-primary {
  background: #106ebe !important;
  border-color: #106ebe !important;
  color: #ffffff !important;
}
.word-btn-primary:hover:not(:disabled) {
  background: #005a9e !important;
  border-color: #005a9e !important;
  color: #ffffff !important;
}
.word-btn-secondary {
  background: #ffffff !important;
  color: #323130 !important;
  border-color: #d2d0ce !important;
}
.word-btn-secondary:hover:not(:disabled) {
  background: #edebe9 !important;
  border-color: #c8c6c4 !important;
}
.word-btn:disabled {
  opacity: 0.45 !important;
  cursor: not-allowed !important;
}
.status-tag {
  font-size: 11px !important;
  font-weight: 500 !important;
  padding: 2px 6px !important;
  border-radius: 2px !important;
  display: inline-flex !important;
  align-items: center !important;
}
.dirty-tag {
  color: #8f4d00 !important;
  background: #fff4ce !important;
  border: 1px solid #ffe699 !important;
}
.saved-tag {
  color: #0e700e !important;
  background: #dff6dd !important;
  border: 1px solid #c3eec0 !important;
}
.remote-tag {
  color: #5c2d91 !important;
  background: #f4ecfb !important;
  border: 1px solid #d9bbf2 !important;
}
.word-count-badge {
  font-size: 11.5px !important;
  color: #605e5c !important;
  font-weight: 500 !important;
  margin-left: 4px !important;
}

.streaming-document {
  margin-bottom: 18px !important;
  padding: 14px 16px !important;
  border: 1px solid #c7e0f4 !important;
  border-radius: 2px !important;
  background: #f3f9fe !important;
  color: #201f1e !important;
}
.streaming-document-header {
  display: flex !important;
  align-items: center !important;
  gap: 6px !important;
  margin-bottom: 10px !important;
  color: #004e8c !important;
  font-family: "Microsoft YaHei", sans-serif !important;
  font-size: 12.5px !important;
  font-weight: 600 !important;
}
.streaming-document-header span:last-child { margin-left: auto !important; color: #605e5c !important; font-weight: 400 !important; font-size: 11.5px !important; }
.streaming-document-mark {
  width: 7px !important;
  height: 7px !important;
  flex: 0 0 auto !important;
  border-radius: 50% !important;
  background: #106ebe !important;
  animation: streaming-document-pulse 1.2s ease-in-out infinite !important;
}
.streaming-document-placeholder { margin: 0 !important; color: #605e5c !important; font-size: 13.5px !important; }
.streaming-document-body p {
  margin: 0 0 12px !important;
  font-family: "FangSong", "SimSun", "Songti SC", "Noto Serif CJK SC", "STSong", serif !important;
  font-size: 15.5px !important;
  line-height: 1.85 !important;
  text-indent: 2em !important;
  text-align: justify !important;
  letter-spacing: 0.02em !important;
  color: #111827 !important;
}
.streaming-document-body p:last-child { margin-bottom: 0 !important; }
@keyframes streaming-document-pulse {
  50% { opacity: 0.35; transform: scale(0.8); }
}

/* 正文空状态 */
.word-empty-state {
  text-align: center !important;
  padding: 56px 16px !important;
  color: #605e5c !important;
  font-size: 13.5px !important;
  background: #faf9f8 !important;
  border: 1px dashed #d2d0ce !important;
  border-radius: 2px !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  gap: 12px !important;
}

/* Word 文档正文排版流 */
.word-document-flow {
  display: flex !important;
  flex-direction: column !important;
  position: relative !important;
  width: 100% !important;
}

.word-paragraph-item {
  position: relative !important;
  margin-bottom: 12px !important;
  border-radius: 1px !important;
  transition: background-color 0.1s ease !important;
}

.word-paragraph-item:hover,
.word-paragraph-item.is-editing {
  background-color: rgba(243, 242, 241, 0.6) !important;
}

.word-paragraph-item.is-locked {
  border-left: 2px solid #ffaa44 !important;
  padding-left: 6px !important;
}

/* 悬浮微操作胶囊 */
.floating-actions {
  position: absolute !important;
  top: -13px !important;
  right: 2px !important;
  display: inline-flex !important;
  align-items: center !important;
  gap: 2px !important;
  background: #ffffff !important;
  border: 1px solid #d2d0ce !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12) !important;
  border-radius: 2px !important;
  padding: 1px 5px !important;
  z-index: 10 !important;
  opacity: 0 !important;
  pointer-events: none !important;
  transition: opacity 0.12s ease, transform 0.12s ease !important;
  transform: translateY(2px) !important;
}

.floating-actions.is-visible {
  opacity: 1 !important;
  pointer-events: auto !important;
  transform: translateY(0) !important;
}

.para-idx {
  font-size: 10.5px !important;
  font-weight: 600 !important;
  color: #605e5c !important;
  margin-right: 2px !important;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace !important;
}

.para-badge.lock {
  font-size: 9.5px !important;
  background: #fff4ce !important;
  color: #8f4d00 !important;
  padding: 0 3px !important;
  border-radius: 2px !important;
  margin-right: 2px !important;
}

.action-btn {
  background: transparent !important;
  border: none !important;
  color: #605e5c !important;
  font-size: 11.5px !important;
  line-height: 1 !important;
  width: 16px !important;
  height: 16px !important;
  border-radius: 2px !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  cursor: pointer !important;
  box-shadow: none !important;
}

.action-btn:hover:not(:disabled) {
  background: #edebe9 !important;
  color: #201f1e !important;
  transform: none !important;
}

.action-btn.danger:hover:not(:disabled) {
  background: #fde7e9 !important;
  color: #a80000 !important;
}

.action-btn:disabled {
  opacity: 0.3 !important;
  cursor: not-allowed !important;
}

/* 无边框自适应排版输入框（仿 Word 所见即所得） */
.para-wrapper {
  position: relative !important;
  width: 100% !important;
}

.word-textarea {
  width: 100% !important;
  box-sizing: border-box !important;
  background: transparent !important;
  border: 1px solid transparent !important;
  outline: none !important;
  resize: none !important;
  overflow: hidden !important;
  padding: 1px 2px !important;
  border-radius: 1px !important;
  color: #111827 !important;
  font-family: "FangSong", "SimSun", "Songti SC", "Noto Serif CJK SC", "STSong", serif !important;
  transition: border-color 0.1s ease, background-color 0.1s ease !important;
}

.word-textarea:focus {
  border-color: #c8c6c4 !important;
  background: #ffffff !important;
}

/* 正文段落排版：首行缩进 2em，两端对齐，标准行高 1.85，字号 15.5px */
.paragraph-textarea {
  font-size: 15.5px !important;
  line-height: 1.85 !important;
  text-indent: 2em !important;
  text-align: justify !important;
  letter-spacing: 0.02em !important;
}

/* 标题排版：字号加大加粗，无缩进 */
.heading-textarea {
  font-family: "SimHei", "Microsoft YaHei", "PingFang SC", sans-serif !important;
  font-size: 16.5px !important;
  font-weight: 700 !important;
  line-height: 1.5 !important;
  text-indent: 0 !important;
  color: #111827 !important;
}

/* 底部追加段落提示区 */
.add-para-footer {
  margin-top: 16px !important;
  padding: 8px !important;
  border: 1px dashed #d2d0ce !important;
  border-radius: 2px !important;
  text-align: center !important;
  color: #605e5c !important;
  font-size: 12px !important;
  cursor: pointer !important;
  transition: all 0.1s ease !important;
}

.add-para-footer:hover {
  border-color: #106ebe !important;
  color: #106ebe !important;
  background: #f3f9fe !important;
}
</style>
