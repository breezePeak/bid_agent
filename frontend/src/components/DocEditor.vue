<template>
  <div class="doc-editor">
    <div v-if="loading" class="doc-empty">加载文档中...</div>
    <div v-else-if="!blocks.length" class="doc-empty">尚未生成 final.md，请先执行到 build-md 阶段。</div>
    <div v-else class="doc-body">
      <!-- TOC sidebar -->
      <aside class="doc-toc" v-if="tocItems.length">
        <div class="doc-toc-title">目录</div>
        <nav class="doc-toc-list">
          <a
            v-for="item in tocItems"
            :key="item.blockId"
            class="doc-toc-item"
            :class="{ 'toc-l2': item.level === 2, 'toc-l3': item.level >= 3 }"
            :title="item.text"
            @click.prevent="scrollToBlock(item.blockId)"
          >{{ item.text }}</a>
        </nav>
      </aside>

      <!-- page content -->
      <div class="doc-page" ref="docPageRef">
        <div class="doc-page-content" ref="docContentRef" @contextmenu.prevent="onContextMenu">
          <template v-for="(page, pi) in pagedBlocks" :key="'page-' + pi">
            <div v-if="pi > 0" class="doc-page-break">
              <span class="doc-page-break-line"></span>
              <span class="doc-page-break-num">— 第 {{ pi + 1 }} 页 —</span>
              <span class="doc-page-break-line"></span>
            </div>
            <div class="doc-page-sheet">
              <template v-for="block in page" :key="block.block_id">
              <!-- pending rewrite diff view -->
              <div v-if="pendingDocEdit && pendingDocEdit.block_id === block.block_id" class="doc-diff-block" :data-block-id="block.block_id">
                <div class="doc-diff-old">{{ pendingDocEdit.old_text }}</div>
                <div class="doc-diff-arrow">↓</div>
                <div class="doc-diff-new">{{ pendingDocEdit.new_text }}</div>
                <div class="doc-diff-actions">
                  <button class="btn btn-sm btn-primary" @click="confirmPending">确认改写</button>
                  <button class="btn btn-sm" @click="discardPending">放弃</button>
                </div>
              </div>
              <!-- normal block -->
              <table v-else-if="block.type === 'table'" class="doc-table" :data-block-id="block.block_id">
                <thead v-if="block.header"><tr><th v-for="cell in block.header" :key="cell">{{ cell }}</th></tr></thead>
                <tbody><tr v-for="(row, ri) in block.rows" :key="ri"><td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td></tr></tbody>
              </table>
              <component
                v-else-if="block.type === 'heading'"
                :is="'h' + Math.min(block.level || 1, 6)"
                class="doc-heading"
                :id="'block-' + block.block_id"
                :data-block-id="block.block_id"
                contenteditable="true"
                @blur="saveBlock($event, block)"
                v-text="block.text"
              ></component>
              <p
                v-else
                class="doc-paragraph"
                :data-block-id="block.block_id"
                contenteditable="true"
                @blur="saveBlock($event, block)"
                v-text="block.text"
              ></p>
            </template>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- context menu -->
    <div v-if="ctxMenu.visible" class="ctx-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
      <div class="ctx-menu-item" @click="ctxAddToChat">添加到聊天框</div>
      <div class="ctx-menu-item" @click="ctxAddAnnotation">添加标注</div>
      <div class="ctx-menu-item ctx-menu-cancel" @click="ctxClose">取消</div>
    </div>

    <!-- selection bar -->
    <div v-if="selectionVisible" class="selection-bar">
      <div class="selection-bar-head">
        <span>选区改写</span>
        <button class="btn btn-sm" @click="clearSelection">关闭</button>
      </div>
      <div class="selection-bar-quoted">{{ selectedText }}</div>
      <textarea v-model="rewriteInstruction" class="selection-input" placeholder="例如：把这段改得更正式；补充评分点响应..."></textarea>
      <button class="btn btn-sm btn-primary" @click="submitRewrite" :disabled="!rewriteInstruction.trim() || selSubmitting">
        {{ selSubmitting ? '改写中...' : 'AI 按批注改写' }}
      </button>
    </div>

    <!-- full rewrite dialog -->
    <div v-if="fullRewriteVisible" class="dialog-overlay" @click.self="fullRewriteVisible = false">
      <div class="dialog" style="width:560px">
        <div class="dialog-header"><h2>AI 全文改写</h2><button class="btn btn-icon" @click="fullRewriteVisible = false">&times;</button></div>
        <div class="dialog-body">
          <div class="form-group">
            <label>改写指令</label>
            <textarea v-model="fullRewriteInstruction" rows="4" style="width:100%;padding:8px;border:1px solid var(--color-border);border-radius:6px;font-size:13px;font-family:inherit;resize:vertical;" placeholder="例如：让全文语气更正式；补充评分点响应..."></textarea>
          </div>
          <p v-if="fullRewriteError" class="form-error">{{ fullRewriteError }}</p>
          <div class="dialog-footer">
            <button class="btn" @click="fullRewriteVisible = false">取消</button>
            <button class="btn btn-primary" @click="submitFullRewrite" :disabled="fullRewriteSubmitting">{{ fullRewriteSubmitting ? '生成中...' : '开始改写' }}</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount, watch } from 'vue'

const props = defineProps({ runId: { type: String, required: true } })
const emit = defineEmits(['add-to-chat', 'add-annotation', 'update-page-count', 'rewrite-applied', 'rewrite-discarded'])

const blocks = ref([])
const loading = ref(false)
const pendingDocEdit = ref(null)
const selectionVisible = ref(false)
const selectedText = ref('')
const selectedBlockId = ref('')
const rewriteInstruction = ref('')
const selSubmitting = ref(false)
const fullRewriteVisible = ref(false)
const fullRewriteInstruction = ref('')
const fullRewriteSubmitting = ref(false)
const fullRewriteError = ref('')
const docPageRef = ref(null)
const docContentRef = ref(null)

const ctxMenu = reactive({ visible: false, x: 0, y: 0 })
let ctxSelectedText = ''
let ctxBlockId = ''

const tocItems = computed(() =>
  blocks.value
    .filter(b => b.type === 'heading')
    .map(b => ({ blockId: b.block_id, level: b.level || 1, text: b.text }))
)

const pageCount = computed(() => pagedBlocks.value.length)

const pagedBlocks = computed(() => {
  const CHARS_PER_LINE = 45
  const LINE_HEIGHT = 25
  const PAGE_HEIGHT = 900
  const HEADING_HEIGHT = 36
  const TABLE_ROW_HEIGHT = 30

  const pages = []
  let curPage = []
  let curH = 0

  for (const b of blocks.value) {
    let h = 0
    if (b.type === 'heading') {
      h = HEADING_HEIGHT
    } else if (b.type === 'table') {
      const rows = b.rows || []
      h = rows.length * TABLE_ROW_HEIGHT + TABLE_ROW_HEIGHT
    } else {
      const chars = (b.text || '').length
      const lines = Math.ceil(chars / CHARS_PER_LINE) || 1
      h = lines * LINE_HEIGHT + 6
    }

    if (curH + h > PAGE_HEIGHT && curPage.length > 0 && b.type !== 'table') {
      pages.push(curPage)
      curPage = []
      curH = 0
    }
    curPage.push(b)
    curH += h
  }
  if (curPage.length) pages.push(curPage)
  return pages.length ? pages : [[]]
})

function scrollToBlock(blockId) {
  const el = document.getElementById('block-' + blockId)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const pendingOld = computed(() => pendingDocEdit.value?.old_text || pendingDocEdit.value?.selected_text || '')
const pendingNew = computed(() => pendingDocEdit.value?.new_text || '')

async function loadDoc() {
  loading.value = true
  try {
    const [renderRes, pendingRes] = await Promise.all([
      fetch('/api/final-doc/render').then(r => r.json()),
      fetch('/api/final-doc/pending').then(r => r.json()),
    ])
    blocks.value = renderRes.blocks || []
    if (pendingRes.pending) pendingDocEdit.value = pendingRes.pending
  } catch (e) { /* ignore */ }
  loading.value = false
}

async function saveBlock(event, block) {
  const el = event.target
  const newText = el.innerText || el.textContent || ''
  if (newText === block.text) return
  try {
    await fetch('/api/final-doc/block-edit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ block_id: block.block_id, new_text: newText, instruction: '' }),
    })
    block.text = newText
  } catch (e) { /* */ }
}

function onMouseUp() {
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed) return
  const text = sel.toString().trim()
  if (!text) return
  const el = sel.anchorNode?.parentElement?.closest?.('[data-block-id]')
  if (!el) return
  selectedText.value = text; selectedBlockId.value = el.dataset.blockId; rewriteInstruction.value = ''; selectionVisible.value = true
}

async function submitRewrite() {
  selSubmitting.value = true
  try {
    const r = await fetch('/api/final-doc/selection-rewrite', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ block_id: selectedBlockId.value, selected_text: selectedText.value, instruction: rewriteInstruction.value }),
    }).then(r => r.json())
    if (r.ok) { pendingDocEdit.value = { kind: 'selection_rewrite', block_id: r.block_id, selected_text: r.selected_text, old_text: r.old_text, new_text: r.new_text, instruction: r.instruction }; selectionVisible.value = false }
  } catch (e) { /* */ }
  selSubmitting.value = false
}

function clearSelection() { selectionVisible.value = false; selectedText.value = ''; window.getSelection().removeAllRanges() }

async function confirmPending() {
  if (!pendingDocEdit.value) return
  const ep = pendingDocEdit.value.kind === 'chat_edit' ? '/api/final-doc/chat-apply' : '/api/final-doc/selection-apply'
  try {
    await fetch(ep, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ new_text: pendingDocEdit.value.new_text, instruction: pendingDocEdit.value.instruction || '' }) })
    pendingDocEdit.value = null; await loadDoc()
    emit('rewrite-applied')
  } catch (e) { /* */ }
}

async function discardPending() {
  if (!pendingDocEdit.value) return
  const ep = pendingDocEdit.value.kind === 'chat_edit' ? '/api/final-doc/chat-discard' : '/api/final-doc/selection-discard'
  try { await fetch(ep, { method: 'POST' }); pendingDocEdit.value = null; emit('rewrite-discarded') } catch (e) { /* */ }
}

function openFullRewrite() { fullRewriteInstruction.value = ''; fullRewriteError.value = ''; fullRewriteVisible.value = true }

// ---- context menu ----
function onContextMenu(e) {
  const sel = window.getSelection()
  const text = (sel && !sel.isCollapsed) ? sel.toString().trim() : ''
  if (text) {
    ctxSelectedText = text
    const el = sel.anchorNode?.parentElement?.closest?.('[data-block-id]')
    ctxBlockId = el?.dataset?.blockId || ''
  } else {
    const range = document.caretRangeFromPoint(e.clientX, e.clientY)
    if (range && docContentRef.value?.contains(range.startContainer)) {
      ctxSelectedText = ''
      ctxBlockId = ''
    } else {
      ctxClose()
      return
    }
  }
  ctxMenu.x = e.clientX
  ctxMenu.y = e.clientY
  ctxMenu.visible = true
}

function ctxAddToChat() {
  if (ctxSelectedText) {
    const tag = ctxBlockId ? `@L${blocks.value.find(b => b.block_id === ctxBlockId)?.start_line || ''} ` : ''
    const preview = truncateText(ctxSelectedText, 8, 8)
    emit('add-to-chat', `${tag}${preview}`)
  }
  ctxClose()
}

function truncateText(text, head, tail) {
  const t = text.replace(/\s+/g, '')
  if (t.length <= head + tail + 3) return t
  return t.slice(0, head) + '...' + t.slice(-tail)
}

function ctxAddAnnotation() {
  if (ctxSelectedText && ctxBlockId) {
    const block = blocks.value.find(b => b.block_id === ctxBlockId)
    const line = block?.start_line || ''
    emit('add-annotation', { text: ctxSelectedText, blockId: ctxBlockId, line })
  }
  ctxClose()
}

function ctxClose() { ctxMenu.visible = false; ctxSelectedText = ''; ctxBlockId = '' }

function onDocClickOutside(e) { if (ctxMenu.visible) ctxClose() }

async function submitFullRewrite() {
  fullRewriteError.value = ''
  if (!fullRewriteInstruction.value.trim()) { fullRewriteError.value = '请输入改写指令'; return }
  fullRewriteSubmitting.value = true
  try {
    const r = await fetch('/api/final-doc/chat-edit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instruction: fullRewriteInstruction.value }) }).then(r => r.json())
    if (r.ok && r.new_md) { pendingDocEdit.value = { kind: 'chat_edit', new_text: r.new_md }; fullRewriteVisible.value = false; await loadDoc() }
    else fullRewriteError.value = r.message || '改写失败'
  } catch (e) { fullRewriteError.value = '请求失败' }
  fullRewriteSubmitting.value = false
}

onMounted(() => { loadDoc(); if (docPageRef.value) docPageRef.value.addEventListener('mouseup', onMouseUp); document.addEventListener('click', onDocClickOutside) })
onBeforeUnmount(() => { if (docPageRef.value) docPageRef.value.removeEventListener('mouseup', onMouseUp); document.removeEventListener('click', onDocClickOutside) })
watch(pageCount, (val) => emit('update-page-count', val))
watch(() => props.runId, loadDoc)

defineExpose({ loadDoc })
</script>
