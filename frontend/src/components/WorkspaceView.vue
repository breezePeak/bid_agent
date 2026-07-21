<template>
  <div class="workspace-layout" :class="{ 'doc-mode': mode === 'doc' || mode === 'detail' }">
    <!-- chat panel -->
    <div
      class="wl-chat"
      :class="{ narrow: mode === 'doc' || mode === 'detail' }"
      :style="(mode === 'doc' || mode === 'detail') ? { flex: '0 0 ' + chatWidth + 'px', maxWidth: chatWidth + 'px' } : {}"
    >
      <ChatPanel
        ref="chatPanelRef"
        :run-id="runId"
        :narrow="mode === 'doc' || mode === 'detail'"
        @preview="openPreview"
        @open-doc-editor="openDoc"
        @rewrite-done="onRewriteDone"
        @pipeline-log="onPipelineLog"
        @focus-rail="onFocusRail"
        @materials-alert="onMaterialsAlert"
      />
    </div>

    <!-- splitter -->
    <div v-if="mode === 'doc' || mode === 'detail'" class="splitter" @mousedown="onSplitterDown"></div>

    <!-- doc editor -->
    <div v-if="mode === 'doc'" class="wl-doc">
      <div class="wl-doc-header">
        <h3>文档编辑 <span v-if="docPageCount > 0" class="wl-doc-pagecount">— 约 {{ docPageCount }} 页 (A4)</span></h3>
        <div class="wl-doc-actions">
          <button class="btn btn-sm" @click="mode = 'chat'">&larr; 返回聊天</button>
          <button class="btn btn-sm" @click="downloadDocx">下载 Word</button>
          <button class="btn btn-sm" @click="downloadMd">下载 MD</button>
        </div>
      </div>
      <DocEditor
        ref="docEditorRef"
        :run-id="runId"
        @add-to-chat="onAddToChat"
        @add-annotation="onAddAnnotation"
        @update-page-count="docPageCount = $event"
        @rewrite-applied="onDocRewriteApplied"
        @rewrite-discarded="onDocRewriteDiscarded"
      />
    </div>

    <!-- step / compliance detail (same layout as Word preview) -->
    <div v-else-if="mode === 'detail'" class="wl-doc">
      <StepDetailView
        :run-id="runId"
        :command="detailCommand"
        @close="mode = 'chat'"
        @open-chapter="openChapter"
        @rerun-stage="rerunStage"
      />
    </div>

    <!-- right: office / issues / materials / logs / files -->
    <div v-if="mode === 'chat'" class="wl-files">
      <IssuesPanel
        ref="issuesPanelRef"
        :run-id="runId"
        :focus="railFocus"
        :pipeline-logs="pipelineLogs"
        @preview-file="previewFile"
        @open-chapter="openChapter"
        @materials-status="onMaterialsStatus"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import ChatPanel from './ChatPanel.vue'
import DocEditor from './DocEditor.vue'
import StepDetailView from './StepDetailView.vue'
import IssuesPanel from './IssuesPanel.vue'
import { downloadFinalDocx, downloadFinalMd } from '../api'

const props = defineProps({
  runId: { type: String, required: true },
  run: { type: Object, default: null },
})

const mode = ref('chat') // chat | doc | detail
const detailCommand = ref('')
const previewFileName = ref('')
const chatPanelRef = ref(null)
const docEditorRef = ref(null)
const issuesPanelRef = ref(null)
const chatWidth = ref(Math.round((window.innerWidth - 260) * 0.4))
const docPageCount = ref(0)
const railFocus = ref('')
const pipelineLogs = ref([])

function openDoc() {
  mode.value = 'doc'
}

function openPreview(cmd) {
  const c = String(cmd || '').trim()
  if (!c) {
    mode.value = 'chat'
    return
  }
  if (c === 'build-docx' || c === 'final-docx' || c === 'doc-editor') {
    mode.value = 'doc'
    return
  }
  if (
    c === 'build-materials-checklist' ||
    c === 'materials-checklist' ||
    c === 'materials'
  ) {
    mode.value = 'chat'
    railFocus.value = 'materials'
    issuesPanelRef.value?.showMaterials?.()
    return
  }
  if (c === 'compliance-check' || c === 'compliance' || c === 'issues') {
    mode.value = 'chat'
    railFocus.value = 'issues'
    issuesPanelRef.value?.showIssues?.()
    return
  }
  if (c === 'logs' || c === 'pipeline-logs') {
    mode.value = 'chat'
    railFocus.value = 'logs'
    issuesPanelRef.value?.showLogs?.()
    return
  }
  detailCommand.value = c
  mode.value = 'detail'
}

function onFocusRail(key) {
  mode.value = 'chat'
  railFocus.value = key || ''
  if (key === 'materials') issuesPanelRef.value?.showMaterials?.()
  else if (key === 'logs') issuesPanelRef.value?.showLogs?.()
  else if (key === 'issues') issuesPanelRef.value?.showIssues?.()
  else if (key === 'goal' || key === 'office' || key === 'agent') issuesPanelRef.value?.showOffice?.()
}

function onPipelineLog(payload) {
  const line = typeof payload === 'string' ? payload : (payload?.line || '')
  const stage = typeof payload === 'object' ? (payload.stage || '') : ''
  if (!line) return
  pipelineLogs.value = [
    ...pipelineLogs.value.slice(-400),
    { line, stage, at: new Date().toISOString() },
  ]
}

function onMaterialsAlert() {
  // ChatPanel already notified; ensure rail shows materials badge
  issuesPanelRef.value?.refreshMaterialsBadge?.()
}

function onMaterialsStatus(payload) {
  // Keep chat badge/notify in sync when IssuesPanel polls materials
  chatPanelRef.value?.notifyMaterialsStatus?.(payload)
}

async function openChapter(chapterId) {
  const cid = String(chapterId || '').trim()
  if (!cid) return
  mode.value = 'doc'
  await new Promise((r) => setTimeout(r, 80))
  if (docEditorRef.value?.scrollToChapter) {
    docEditorRef.value.scrollToChapter(cid)
  } else if (docEditorRef.value?.loadDoc) {
    await docEditorRef.value.loadDoc()
    docEditorRef.value.scrollToChapter?.(cid)
  }
}

function rerunStage(command) {
  const cmd = String(command || '').trim()
  if (!cmd) return
  mode.value = 'chat'
  if (chatPanelRef.value?.startAutoRun) {
    chatPanelRef.value.startAutoRun(cmd)
  }
}

function previewFile(path) {
  previewFileName.value = path
}
async function downloadDocx() {
  try {
    await downloadFinalDocx(props.runId)
  } catch (error) {
    window.alert(error?.response?.data?.message || error?.message || '正式稿门禁未通过，无法下载 Word。')
  }
}
function downloadMd() { downloadFinalMd(props.runId) }

function onRewriteDone() {
  setTimeout(() => { if (docEditorRef.value?.loadDoc) docEditorRef.value.loadDoc() }, 800)
  issuesPanelRef.value?.refreshGoal?.()
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

watch(() => props.runId, () => {
  mode.value = 'chat'
  detailCommand.value = ''
  railFocus.value = ''
  pipelineLogs.value = []
})
</script>
