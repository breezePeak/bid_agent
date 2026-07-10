<template>
  <div class="workspace-layout" :class="{ 'doc-mode': mode === 'doc' }">
    <!-- chat panel -->
    <div class="wl-chat" :class="{ narrow: mode === 'doc' }" :style="mode === 'doc' ? { flex: '0 0 ' + chatWidth + 'px', maxWidth: chatWidth + 'px' } : {}">
      <ChatPanel
        ref="chatPanelRef"
        :run-id="runId"
        :narrow="mode === 'doc'"
        @preview="openPreview"
        @open-doc-editor="mode = 'doc'"
        @rewrite-done="onRewriteDone"
      />
    </div>

    <!-- splitter -->
    <div v-if="mode === 'doc'" class="splitter" @mousedown="onSplitterDown"></div>

    <!-- doc editor (visible in doc mode) -->
    <div v-if="mode === 'doc'" class="wl-doc">
      <div class="wl-doc-header">
        <h3>文档编辑 <span v-if="docPageCount > 0" class="wl-doc-pagecount">— 约 {{ docPageCount }} 页 (A4)</span></h3>
        <div class="wl-doc-actions">
          <button class="btn btn-sm" @click="mode = 'chat'">&larr; 返回聊天</button>
          <button class="btn btn-sm" @click="downloadDocx">下载 Word</button>
          <button class="btn btn-sm" @click="downloadMd">下载 MD</button>
        </div>
      </div>
      <DocEditor ref="docEditorRef" :run-id="runId" @add-to-chat="onAddToChat" @add-annotation="onAddAnnotation" @update-page-count="docPageCount = $event" @rewrite-applied="onDocRewriteApplied" @rewrite-discarded="onDocRewriteDiscarded" />
    </div>

    <!-- right file explorer (visible in chat mode) -->
    <div v-if="mode === 'chat'" class="wl-files">
      <FileExplorer :run-id="runId" @preview-file="previewFile" />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import ChatPanel from './ChatPanel.vue'
import DocEditor from './DocEditor.vue'
import FileExplorer from './FileExplorer.vue'

const props = defineProps({
  runId: { type: String, required: true },
  run: { type: Object, default: null },
})

const mode = ref('chat')
const previewFileName = ref('')
const previewFileContent = ref('')
const chatPanelRef = ref(null)
const docEditorRef = ref(null)
const chatWidth = ref(Math.round((window.innerWidth - 260) * 0.4))
const docPageCount = ref(0)

function openPreview(cmd) { mode.value = 'chat' }
function previewFile(path) { previewFileName.value = path }
function downloadDocx() { window.open('/api/download/final-docx', '_blank') }
function downloadMd() { window.open('/api/download/final-md', '_blank') }

function onRewriteDone() {
  setTimeout(() => { if (docEditorRef.value?.loadDoc) docEditorRef.value.loadDoc() }, 800)
}

function onDocRewriteApplied() {
  if (chatPanelRef.value?.notifyRewriteApplied) chatPanelRef.value.notifyRewriteApplied()
}

function onDocRewriteDiscarded() {
  if (chatPanelRef.value?.notifyRewriteDiscarded) chatPanelRef.value.notifyRewriteDiscarded()
}

function onAddToChat(text) {
  if (chatPanelRef.value?.addInputText) chatPanelRef.value.addInputText(text)
}

function onAddAnnotation(payload) {
  if (chatPanelRef.value?.addInputText) {
    const tagPart = payload.line ? `@L${payload.line} ` : ''
    chatPanelRef.value.addInputText(`${tagPart}${payload.text}`, { line: payload.line, fullText: payload.text })
  }
}

// ---- splitter drag ----
let dragging = false
let dragStartX = 0
let dragStartW = 0

function onSplitterDown(e) {
  dragging = true
  dragStartX = e.clientX
  dragStartW = chatWidth.value
  document.addEventListener('mousemove', onSplitterMove)
  document.addEventListener('mouseup', onSplitterUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function onSplitterMove(e) {
  if (!dragging) return
  const dx = e.clientX - dragStartX
  chatWidth.value = Math.max(280, Math.min(700, dragStartW + dx))
}

function onSplitterUp() {
  dragging = false
  document.removeEventListener('mousemove', onSplitterMove)
  document.removeEventListener('mouseup', onSplitterUp)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

watch(() => props.runId, () => { mode.value = 'chat' })
</script>
